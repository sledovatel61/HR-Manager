# Регрессии итерации 15: три дефекта, найденные проверкой PR #52.
#   1) снимок прежней версии делал УСТАНОВЛЕННЫЙ движок (прежняя версия могла не
#      знать действия snapshot-previous), а его отказ маскировался: установка шла
#      дальше и перезаписывала файлы без возможности отката;
#   2) ошибка после замены файлов мастером возвращала ложное «установленная
#      версия не изменялась» и не восстанавливала прежнюю версию;
#   3) подтверждение отката не было привязано к ожидаемому release_sha: пустой
#      или чужой идентификатор проходил как «прежняя версия восстановлена».
#
# Проверяются ПОВЕДЕНИЕ, код возврата вспомогательного скрипта, фактическое
# содержимое каталога установки и итоговый статус (update-result.json), а не
# строки журнала. Реальная машина, Docker и сеть не затрагиваются.

param([string]$HarnessPath = $PSScriptRoot)

. (Join-Path $HarnessPath "test-harness.ps1")

$script:RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..\..")).Path

Write-Host "== Итерация 15: снимок до перезаписи, честный откат, идентичность =="

function New-HrmIteration15Dir {
    param([string]$Name = "case")
    $dir = Join-Path ([System.IO.Path]::GetTempPath()) ("HRM i15 " + $Name + " " + [guid]::NewGuid().ToString("N").Substring(0, 8))
    New-Item -ItemType Directory -Path $dir -Force | Out-Null
    return $dir
}

function Set-HrmIteration15Preflight {
    Set-HrmPreflightOverride @{ windows = $true; powershell = $true; docker = $true; daemon = $true; compose = "v2.29.7 (mock)"; port = $true; state_dir = $true; space = $true; config = $true }
}

function New-HrmSnapshotPackageDir {
    # «Пакет мастера установки»: ровно то, что Setup.exe распаковывает во
    # временный каталог — плоский каталог с CLI снимка, модулями движка и
    # release.json ЭТОГО пакета (идентификатор нового релиза).
    param([string]$ReleaseSha = "", [string]$Version = "1.0.0")
    $dir = New-HrmIteration15Dir "package"
    Copy-Item (Join-Path $script:RepoRoot "infra\windows\hrm-snapshot.ps1") $dir -Force
    foreach ($module in @("Common", "Secrets", "Install", "Snapshot")) {
        Copy-Item (Join-Path $script:RepoRoot ("infra\windows\engine\" + $module + ".psm1")) $dir -Force
    }
    $json = [ordered]@{ release_sha = $ReleaseSha; version = $Version } | ConvertTo-Json
    [System.IO.File]::WriteAllText((Join-Path $dir "release.json"), $json, (New-Object System.Text.UTF8Encoding($false)))
    return $dir
}

function Invoke-HrmSnapshotCliProcess {
    # CLI снимка запускается ОТДЕЛЬНЫМ процессом: код возврата — это контракт с
    # мастером установки, его нельзя подменять вызовом функции в этом процессе.
    param([string]$PackageDir, [string]$InstallDir, [string]$StateDir, [string]$ResultFile)
    $exe = Join-Path $env:SystemRoot "System32\WindowsPowerShell\v1.0\powershell.exe"
    if (-not (Test-Path $exe)) { throw ("не найден Windows PowerShell: " + $exe) }
    $arguments = @("-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
        (Join-Path $PackageDir "hrm-snapshot.ps1"),
        "-InstallDir", $InstallDir, "-StateDir", $StateDir, "-ResultFile", $ResultFile)
    $text = (& $exe @arguments 2>$null | Out-String)
    return [pscustomobject]@{ exit_code = $LASTEXITCODE; output = $text.Trim() }
}

function Get-HrmSnapshotResultField {
    param([string]$Path, [string]$Key)
    if (-not (Test-Path $Path)) { return "" }
    foreach ($line in @(Get-Content -Path $Path -Encoding UTF8)) {
        if ($line -match ("^" + [regex]::Escape($Key) + "=(.*)$")) { return $Matches[1].Trim() }
    }
    return ""
}

function Get-HrmSnapshotDescriptorState {
    param([string]$StateDir)
    $file = Join-Path $StateDir "previous-snapshot.json"
    if (-not (Test-Path $file)) { return $null }
    return (Get-HrmJsonFile $file)
}

function Assert-HrmRollbackResult {
    # Итог обновления читается так же, как его читает трей/мастер/диагностика.
    param(
        [string]$StateDir,
        [string]$Status,
        [bool]$Confirmed,
        [bool]$NeedsBackup,
        [string]$MessagePart = ""
    )
    $result = Get-HrmUpdateResult $StateDir
    Assert-HrmTrue ($null -ne $result) "нет update-result.json"
    Assert-HrmEqual $Status ([string]$result.status) "неверный статус обновления"
    Assert-HrmEqual $Confirmed ([bool]$result.recovery_confirmed) "неверный recovery_confirmed"
    Assert-HrmEqual $NeedsBackup ([bool]$result.needs_backup_restore) "неверный needs_backup_restore"
    if ($MessagePart) {
        Assert-HrmContains ([string]$result.message) $MessagePart "сообщение не объясняет фактическое состояние"
    }
    return $result
}

function Invoke-HrmUpdateExpectingFailure {
    param([string]$ReleaseDir, [string]$InstallDir, [string]$StateDir)
    $caught = $false
    try { Update-HrmApp -ReleaseDir $ReleaseDir -InstallDir $InstallDir -StateDir $StateDir | Out-Null }
    catch { $caught = $true }
    Assert-HrmTrue $caught "провал обновления не дошёл до вызывающего"
}

function ConvertTo-HrmLegacyInstall {
    # Установка, сделанная движком, который release_sha ещё не записывал: ни в
    # записи установки, ни в release.json идентификатора нет.
    param([string]$StateDir, [string]$InstallDir, [int]$Port = 8080, [string]$Version = "0.13.0")
    Set-HrmJsonFile $StateDir "installed.json" ([ordered]@{
            port = $Port; version = $Version; pilot_created = $true
        })
    Set-HrmJsonFile $InstallDir "release.json" ([ordered]@{ version = $Version })
}

function Initialize-HrmUpdateScenario {
    # Состояние «установлена версия A → мастер установки заменил файлы версией B,
    # обновление продолжает движок». Снимок прежней версии делается ДО замены
    # файлов (как это делает Setup.exe).
    param([bool]$Snapshot = $true, [bool]$Legacy = $false)
    $state = Get-HrmTestStateDir
    $install = Get-HrmTestInstallDir
    $shaA = ("a" * 40)
    $shaB = ("b" * 40)
    New-HrmFakeSnapshot -Root $install -ReleaseSha $shaA
    Set-Content -Path (Join-Path $install "backend\marker.txt") -Value "OLD" -Encoding ASCII
    Install-HrmApp -SourceDir $install -InstallDir $install -StateDir $state -Port 8080 | Out-Null
    if ($Legacy) { ConvertTo-HrmLegacyInstall -StateDir $state -InstallDir $install }
    if ($Snapshot) {
        $saved = Save-HrmInstalledSnapshotForSetup -InstallDir $install -StateDir $state
        Assert-HrmTrue ([bool]($saved.verified -or $saved.skipped)) ("снимок прежней версии не сохранён: " + [string]$saved.message)
    }
    # Мастер установки разложил файлы новой версии ДО запуска движка.
    New-HrmFakeSnapshot -Root $install -ReleaseSha $shaB
    Set-Content -Path (Join-Path $install "backend\marker.txt") -Value "NEW" -Encoding ASCII
    if ($Legacy) {
        # Новая версия тоже без идентификатора: доказательство замены файлов
        # обязано находиться по ВЕРСИИ, а не только по release_sha.
        Set-HrmJsonFile $install "release.json" ([ordered]@{ version = "1.0.0" })
    }
    $releaseDir = New-HrmIteration15Dir "release-b"
    New-HrmFakeSnapshot -Root $releaseDir -ReleaseSha $shaB
    return [pscustomobject]@{
        State = $state; Install = $install; ReleaseDir = $releaseDir
        ShaA = $shaA; ShaB = $shaB
    }
}

# --- 1. Снимок прежней версии делается мастером из СВОЕГО пакета --------------

Test-Case "P1-1: CLI снимка на чистой машине: код 0 и «сохранять нечего»" {
    Initialize-HrmTestEngine
    New-HrmMockWorld | Out-Null
    Set-HrmIteration15Preflight
    $state = Get-HrmTestStateDir
    $install = Get-HrmTestInstallDir
    $package = New-HrmSnapshotPackageDir -ReleaseSha ("c" * 40)
    $resultFile = Join-Path (New-HrmIteration15Dir "result") "hrm-snapshot-result.txt"
    $run = Invoke-HrmSnapshotCliProcess -PackageDir $package -InstallDir $install -StateDir $state -ResultFile $resultFile
    Assert-HrmEqual 0 $run.exit_code ("CLI снимка вернул ненулевой код: " + $run.output)
    Assert-HrmEqual "skipped" (Get-HrmSnapshotResultField -Path $resultFile -Key "status") "первая установка не отмечена как «сохранять нечего»"
    Assert-HrmEqual "first_install" (Get-HrmSnapshotResultField -Path $resultFile -Key "reason") "неверная причина пропуска снимка"
    Assert-HrmFalse (Test-Path (Join-Path $state "previous-snapshot")) "на чистой машине создан снимок прежней версии"
    Clear-HrmExternalMock
    Clear-HrmHttpMock
}

Test-Case "P1-1: CLI снимка сохраняет прежнюю версию и проверяет её описание" {
    Initialize-HrmTestEngine
    New-HrmMockWorld | Out-Null
    Set-HrmIteration15Preflight
    $state = Get-HrmTestStateDir
    $install = Get-HrmTestInstallDir
    $shaA = ("a" * 40)
    New-HrmFakeSnapshot -Root $install -ReleaseSha $shaA
    Set-Content -Path (Join-Path $install "backend\marker.txt") -Value "OLD" -Encoding ASCII
    Install-HrmApp -SourceDir $install -InstallDir $install -StateDir $state -Port 8080 | Out-Null
    $package = New-HrmSnapshotPackageDir -ReleaseSha ("c" * 40)
    $resultFile = Join-Path (New-HrmIteration15Dir "result") "hrm-snapshot-result.txt"
    $run = Invoke-HrmSnapshotCliProcess -PackageDir $package -InstallDir $install -StateDir $state -ResultFile $resultFile
    Assert-HrmEqual 0 $run.exit_code ("CLI снимка вернул ненулевой код: " + $run.output)
    Assert-HrmEqual "verified" (Get-HrmSnapshotResultField -Path $resultFile -Key "status") "снимок прежней версии не подтверждён"
    Assert-HrmEqual "saved" (Get-HrmSnapshotResultField -Path $resultFile -Key "reason") "неверная причина снимка"
    $snapshotRelease = Join-Path (Get-HrmPreviousSnapshotDir $state) "release.json"
    Assert-HrmTrue (Test-Path $snapshotRelease) "файлы прежней версии не сохранены"
    Assert-HrmEqual $shaA ([string](Get-HrmJsonFile $snapshotRelease).release_sha) "в снимке не та версия"
    $descriptor = Get-HrmSnapshotDescriptorState -StateDir $state
    Assert-HrmTrue ($null -ne $descriptor) "нет описания снимка previous-snapshot.json"
    Assert-HrmTrue ([bool]$descriptor.verified) "снимок не помечен проверенным"
    Assert-HrmEqual $shaA ([string]$descriptor.release_sha) "описание снимка не содержит идентификатор прежней версии"
    Assert-HrmTrue (@($descriptor.manifest).Count -gt 0) "в описании снимка нет манифеста файлов"
    Assert-HrmEqual $shaA ([string](Get-HrmJsonFile (Join-Path $install "release.json")).release_sha) "мастер изменил файлы каталога установки до перезаписи"
    Clear-HrmExternalMock
    Clear-HrmHttpMock
}

Test-Case "P1-1: CLI отказывает (код 3), когда файлы {app} уже заменены новой версией" {
    Initialize-HrmTestEngine
    New-HrmMockWorld | Out-Null
    Set-HrmIteration15Preflight
    $state = Get-HrmTestStateDir
    $install = Get-HrmTestInstallDir
    $shaB = ("b" * 40)
    # В {app} уже разложены файлы НОВОГО релиза (release_sha = shaB), а запись
    # установки идентификатора не содержит — «прежней версией» они не являются.
    New-HrmFakeSnapshot -Root $install -ReleaseSha $shaB
    Install-HrmApp -SourceDir $install -InstallDir $install -StateDir $state -Port 8080 | Out-Null
    Set-HrmJsonFile $state "installed.json" ([ordered]@{ port = 8080; version = "0.13.0"; pilot_created = $true })
    $package = New-HrmSnapshotPackageDir -ReleaseSha $shaB
    $resultFile = Join-Path (New-HrmIteration15Dir "result") "hrm-snapshot-result.txt"
    $run = Invoke-HrmSnapshotCliProcess -PackageDir $package -InstallDir $install -StateDir $state -ResultFile $resultFile
    Assert-HrmEqual 3 $run.exit_code ("CLI снимка не отказал: " + $run.output)
    Assert-HrmEqual "failed" (Get-HrmSnapshotResultField -Path $resultFile -Key "status") "отказ не отмечен как failed"
    Assert-HrmEqual "files_replaced" (Get-HrmSnapshotResultField -Path $resultFile -Key "reason") "неверная причина отказа"
    Assert-HrmFalse (Test-Path (Join-Path $state "previous-snapshot.json")) "снимок новых файлов выдан за прежнюю версию"
    Assert-HrmFalse (Test-Path (Join-Path (Get-HrmPreviousSnapshotDir $state) "release.json")) "новые файлы сохранены как снимок прежней версии"
    Clear-HrmExternalMock
    Clear-HrmHttpMock
}

Test-Case "P1-1: CLI принимает снимок движка без описания (обновление со старой версии)" {
    Initialize-HrmTestEngine
    New-HrmMockWorld | Out-Null
    Set-HrmIteration15Preflight
    $state = Get-HrmTestStateDir
    $install = Get-HrmTestInstallDir
    $shaA = ("a" * 40)
    New-HrmFakeSnapshot -Root $install -ReleaseSha $shaA
    Install-HrmApp -SourceDir $install -InstallDir $install -StateDir $state -Port 8080 | Out-Null
    # Прежний движок оставил только файлы снимка — без описания.
    $snapshotDir = Get-HrmPreviousSnapshotDir $state
    New-Item -ItemType Directory -Path $snapshotDir -Force | Out-Null
    New-HrmFakeSnapshot -Root $snapshotDir -ReleaseSha $shaA
    Assert-HrmFalse (Test-Path (Join-Path $state "previous-snapshot.json")) "описание снимка уже существует"
    $package = New-HrmSnapshotPackageDir -ReleaseSha ("c" * 40)
    $resultFile = Join-Path (New-HrmIteration15Dir "result") "hrm-snapshot-result.txt"
    $run = Invoke-HrmSnapshotCliProcess -PackageDir $package -InstallDir $install -StateDir $state -ResultFile $resultFile
    Assert-HrmEqual 0 $run.exit_code ("снимок предыдущего движка не принят: " + $run.output)
    Assert-HrmEqual "verified" (Get-HrmSnapshotResultField -Path $resultFile -Key "status") "принятый снимок не проверен"
    $descriptor = Get-HrmSnapshotDescriptorState -StateDir $state
    Assert-HrmTrue ($null -ne $descriptor) "снимок предыдущего движка остался без описания"
    Assert-HrmEqual "adopted" ([string]$descriptor.origin) "снимок принят не как принятый у прежнего движка"
    Assert-HrmEqual $shaA ([string]$descriptor.release_sha) "описание снимка не содержит идентификатор прежней версии"
    Clear-HrmExternalMock
    Clear-HrmHttpMock
}

# --- 2. Ошибка после замены файлов мастером ----------------------------------

Test-Case "P1-2: сбой после замены файлов мастером — прежняя версия восстановлена" {
    Initialize-HrmTestEngine
    $world = New-HrmMockWorld
    Set-HrmIteration15Preflight
    $scenario = Initialize-HrmUpdateScenario -Snapshot $true
    $world.SimulateStaleRelease = $true
    $world.OpsBody.release_sha = $scenario.ShaA
    $world.BackupCheckOk = $false
    Invoke-HrmUpdateExpectingFailure -ReleaseDir $scenario.ReleaseDir -InstallDir $scenario.Install -StateDir $scenario.State
    Assert-HrmRollbackResult -StateDir $scenario.State -Status "rolled_back" -Confirmed $true -NeedsBackup $false `
        -MessagePart "прежняя версия восстановлена и отвечает" | Out-Null
    Assert-HrmEqual "OLD" ((Get-Content (Join-Path $scenario.Install "backend\marker.txt") -Raw).Trim()) "файлы прежней версии не восстановлены"
    Assert-HrmEqual $scenario.ShaA ([string](Get-HrmJsonFile (Join-Path $scenario.Install "release.json")).release_sha) "в каталоге установки не прежняя версия"
    Assert-HrmEqual $scenario.ShaA ([string](Get-HrmInstallRecord $scenario.State).release_sha) "запись установки не вернулась к прежней версии"
    Clear-HrmExternalMock
    Clear-HrmHttpMock
}

Test-Case "P1-2: сбой после замены файлов БЕЗ снимка — честный rollback_failed" {
    Initialize-HrmTestEngine
    $world = New-HrmMockWorld
    Set-HrmIteration15Preflight
    $scenario = Initialize-HrmUpdateScenario -Snapshot $false
    $world.SimulateStaleRelease = $true
    $world.OpsBody.release_sha = $scenario.ShaA
    $world.BackupCheckOk = $false
    Invoke-HrmUpdateExpectingFailure -ReleaseDir $scenario.ReleaseDir -InstallDir $scenario.Install -StateDir $scenario.State
    $result = Assert-HrmRollbackResult -StateDir $scenario.State -Status "rollback_failed" -Confirmed $false -NeedsBackup $true `
        -MessagePart "не подтверждено"
    Assert-HrmNotContains ([string]$result.message) "Установленная версия не изменялась" "ложное «установленная версия не изменялась» после замены файлов мастером"
    Assert-HrmFalse (Test-Path (Join-Path $scenario.State "previous-snapshot.json")) "снимок новых файлов выдан за прежнюю версию"
    $journal = Get-HrmJsonFile (Join-Path $scenario.State "update-journal.json")
    Assert-HrmEqual "rollback" ([string]$journal.phase) "журнал обновления не сохранён для разбора"
    Clear-HrmExternalMock
    Clear-HrmHttpMock
}

Test-Case "P1-2: продолжение по журналу учитывает замену файлов мастером" {
    Initialize-HrmTestEngine
    $world = New-HrmMockWorld
    Set-HrmIteration15Preflight
    $scenario = Initialize-HrmUpdateScenario -Snapshot $true
    # Журнал прерванной попытки: файлы {app} заменены мастером, снимок прежней
    # версии сохранён. Прежний движок признак files_switched не читал и объявлял
    # «установленная версия не изменялась».
    Set-HrmJsonFile $scenario.State "update-journal.json" ([ordered]@{
            phase = "backup"
            release_dir = $scenario.ReleaseDir
            release_sha = $scenario.ShaB
            previous_ids = [ordered]@{ "hr-manager-pilot-backend:pilot" = "hr-manager-pilot-backend:previous" }
            previous_release_sha = $scenario.ShaA
            previous_snapshot_saved = $true
            files_switched = $true
            migration_done = $false
        })
    $world.SimulateStaleRelease = $true
    $world.OpsBody.release_sha = $scenario.ShaA
    $world.BackupCheckOk = $false
    Invoke-HrmUpdateExpectingFailure -ReleaseDir $scenario.ReleaseDir -InstallDir $scenario.Install -StateDir $scenario.State
    Assert-HrmRollbackResult -StateDir $scenario.State -Status "rolled_back" -Confirmed $true -NeedsBackup $false | Out-Null
    Assert-HrmEqual "OLD" ((Get-Content (Join-Path $scenario.Install "backend\marker.txt") -Raw).Trim()) "файлы прежней версии не восстановлены при продолжении обновления"
    Clear-HrmExternalMock
    Clear-HrmHttpMock
}

# --- 3. Подтверждение отката привязано к ожидаемому release_sha ---------------

Test-Case "P1-3: пустой release_sha в работе — откат НЕ подтверждён" {
    Initialize-HrmTestEngine
    $world = New-HrmMockWorld
    Set-HrmIteration15Preflight
    $scenario = Initialize-HrmUpdateScenario -Snapshot $true
    $world.SimulateStaleRelease = $true
    $world.OpsBody.release_sha = ""
    $world.BackupCheckOk = $false
    Invoke-HrmUpdateExpectingFailure -ReleaseDir $scenario.ReleaseDir -InstallDir $scenario.Install -StateDir $scenario.State
    Assert-HrmRollbackResult -StateDir $scenario.State -Status "rollback_failed" -Confirmed $false -NeedsBackup $true `
        -MessagePart "не сообщило версию" | Out-Null
    Clear-HrmExternalMock
    Clear-HrmHttpMock
}

Test-Case "P1-3: чужой release_sha в работе — откат НЕ подтверждён" {
    Initialize-HrmTestEngine
    $world = New-HrmMockWorld
    Set-HrmIteration15Preflight
    $scenario = Initialize-HrmUpdateScenario -Snapshot $true
    $world.SimulateStaleRelease = $true
    $world.OpsBody.release_sha = ("c" * 40)
    $world.BackupCheckOk = $false
    Invoke-HrmUpdateExpectingFailure -ReleaseDir $scenario.ReleaseDir -InstallDir $scenario.Install -StateDir $scenario.State
    Assert-HrmRollbackResult -StateDir $scenario.State -Status "rollback_failed" -Confirmed $false -NeedsBackup $true `
        -MessagePart "другая версия" | Out-Null
    Clear-HrmExternalMock
    Clear-HrmHttpMock
}

Test-Case "P1-3: совпадающий release_sha и готовая версия — откат подтверждён" {
    Initialize-HrmTestEngine
    $world = New-HrmMockWorld
    Set-HrmIteration15Preflight
    $scenario = Initialize-HrmUpdateScenario -Snapshot $true
    $world.SimulateStaleRelease = $true
    $world.OpsBody.release_sha = $scenario.ShaA
    $world.BackupCheckOk = $false
    Invoke-HrmUpdateExpectingFailure -ReleaseDir $scenario.ReleaseDir -InstallDir $scenario.Install -StateDir $scenario.State
    Assert-HrmRollbackResult -StateDir $scenario.State -Status "rolled_back" -Confirmed $true -NeedsBackup $false | Out-Null
    Clear-HrmExternalMock
    Clear-HrmHttpMock
}

Test-Case "P1-3: в работе осталась сбойная версия — откат НЕ подтверждён" {
    Initialize-HrmTestEngine
    $world = New-HrmMockWorld
    Set-HrmIteration15Preflight
    $scenario = Initialize-HrmUpdateScenario -Snapshot $true
    $world.SimulateStaleRelease = $true
    $world.OpsBody.release_sha = $scenario.ShaB
    $world.BackupCheckOk = $false
    Invoke-HrmUpdateExpectingFailure -ReleaseDir $scenario.ReleaseDir -InstallDir $scenario.Install -StateDir $scenario.State
    Assert-HrmRollbackResult -StateDir $scenario.State -Status "rollback_failed" -Confirmed $false -NeedsBackup $true `
        -MessagePart "сбойная" | Out-Null
    Clear-HrmExternalMock
    Clear-HrmHttpMock
}

Test-Case "P1-3: старая версия без release_sha — откат подтверждается содержимым и версией" {
    Initialize-HrmTestEngine
    $world = New-HrmMockWorld
    Set-HrmIteration15Preflight
    $scenario = Initialize-HrmUpdateScenario -Snapshot $true -Legacy $true
    $world.SimulateStaleRelease = $true
    $world.OpsBody.release_sha = ""
    $world.OpsBody.version = "0.13.0"
    $world.BackupCheckOk = $false
    Invoke-HrmUpdateExpectingFailure -ReleaseDir $scenario.ReleaseDir -InstallDir $scenario.Install -StateDir $scenario.State
    Assert-HrmRollbackResult -StateDir $scenario.State -Status "rolled_back" -Confirmed $true -NeedsBackup $false `
        -MessagePart "прежняя версия" | Out-Null
    Assert-HrmEqual "OLD" ((Get-Content (Join-Path $scenario.Install "backend\marker.txt") -Raw).Trim()) "файлы прежней версии не восстановлены"
    Clear-HrmExternalMock
    Clear-HrmHttpMock
}

Test-Case "P1-3: старая версия без release_sha — версия сбойной сборки в работе не подтверждает откат" {
    Initialize-HrmTestEngine
    $world = New-HrmMockWorld
    Set-HrmIteration15Preflight
    $scenario = Initialize-HrmUpdateScenario -Snapshot $true -Legacy $true
    $world.SimulateStaleRelease = $true
    $world.OpsBody.release_sha = ""
    $world.OpsBody.version = "1.0.0"
    $world.BackupCheckOk = $false
    Invoke-HrmUpdateExpectingFailure -ReleaseDir $scenario.ReleaseDir -InstallDir $scenario.Install -StateDir $scenario.State
    Assert-HrmRollbackResult -StateDir $scenario.State -Status "rollback_failed" -Confirmed $false -NeedsBackup $true `
        -MessagePart "сбойной сборки" | Out-Null
    Clear-HrmExternalMock
    Clear-HrmHttpMock
}

Test-Case "P1-3: старая версия без release_sha — чужой идентификатор в работе не подтверждает откат" {
    Initialize-HrmTestEngine
    $world = New-HrmMockWorld
    Set-HrmIteration15Preflight
    $scenario = Initialize-HrmUpdateScenario -Snapshot $true -Legacy $true
    $world.SimulateStaleRelease = $true
    $world.OpsBody.release_sha = $scenario.ShaB
    $world.OpsBody.version = "0.13.0"
    $world.BackupCheckOk = $false
    Invoke-HrmUpdateExpectingFailure -ReleaseDir $scenario.ReleaseDir -InstallDir $scenario.Install -StateDir $scenario.State
    Assert-HrmRollbackResult -StateDir $scenario.State -Status "rollback_failed" -Confirmed $false -NeedsBackup $true | Out-Null
    Clear-HrmExternalMock
    Clear-HrmHttpMock
}

Test-Case "P1-2: старые файлы без идентификатора не сохраняются как «прежняя версия»" {
    Initialize-HrmTestEngine
    $world = New-HrmMockWorld
    Set-HrmIteration15Preflight
    $scenario = Initialize-HrmUpdateScenario -Snapshot $false -Legacy $true
    $world.SimulateStaleRelease = $true
    $world.OpsBody.release_sha = ""
    $world.OpsBody.version = "1.0.0"
    $world.BackupCheckOk = $false
    Invoke-HrmUpdateExpectingFailure -ReleaseDir $scenario.ReleaseDir -InstallDir $scenario.Install -StateDir $scenario.State
    # Файлы новой версии уже в {app} (доказательство — расхождение ВЕРСИЙ):
    # снимок из них делать запрещено, обновление обязано честно сообщить отказ.
    Assert-HrmFalse (Test-Path (Join-Path $scenario.State "previous-snapshot.json")) "снимок сделан из файлов новой версии"
    Assert-HrmRollbackResult -StateDir $scenario.State -Status "rollback_failed" -Confirmed $false -NeedsBackup $true | Out-Null
    Clear-HrmExternalMock
    Clear-HrmHttpMock
}

# --- 4. Статические контракты мастера установки и входных точек --------------

Test-Case "P2: мастер установки готовит снимок из своего пакета и проверяет результат" {
    $issPath = Join-Path $script:RepoRoot "installer\installer.iss"
    $iss = Get-Content -Path $issPath -Raw -Encoding UTF8
    # Снимок делает вспомогательный скрипт ИЗ ПАКЕТА мастера: установленный
    # движок прежней версии может не знать действия snapshot-previous.
    Assert-HrmContains $iss "hrm-snapshot.ps1" "мастер не использует вспомогательный скрипт снимка"
    Assert-HrmContains $iss "ExtractTemporaryFile" "мастер не распаковывает вспомогательный скрипт из своего пакета"
    Assert-HrmNotContains $iss "-Action snapshot-previous" "мастер снова полагается на действие УСТАНОВЛЕННОГО движка"
    foreach ($name in @("hrm-snapshot.ps1", "Common.psm1", "Install.psm1", "Secrets.psm1", "Snapshot.psm1", "release.json")) {
        Assert-HrmContains $iss ('"' + $name + '"; Flags: dontcopy noencryption') ("нет записи dontcopy для " + $name)
    }
    # Код возврата и файл результата проверяются, отказ виден человеку.
    Assert-HrmContains $iss "(ResultCode = 0)" "мастер не проверяет код возврата вспомогательного скрипта"
    Assert-HrmContains $iss "HrmReadResultKey" "мастер не читает результат подготовки снимка"
    Assert-HrmContains $iss "HrmExtractSnapshotHelper" "нет проверки распаковки вспомогательных файлов"
    Assert-HrmContains $iss "PrepareToInstall" "нет остановки установки до копирования файлов"
    Assert-HrmContains $iss "HrmVerifySnapshotGuard" "нет последней проверки перед копированием файлов"
    Assert-HrmContains $iss "RaiseException" "нет остановки установки, если снимок не подтверждён"
    Assert-HrmContains $iss "russian.SnapshotStop=" "нет понятного сообщения об остановке установки"
    Assert-HrmContains $iss "russian.SnapshotFilesReplaced=" "нет понятного сообщения о заменённых файлах"
    Assert-HrmContains $iss "RunOk := HrmRunSnapshotHelper" "результат запуска вспомогательного скрипта не сохраняется"
    Assert-HrmContains $iss "if RunOk and" "нулевой код возврата вспомогательного скрипта не обязателен"
    # При solid compression вспомогательные файлы обязаны быть ПЕРВЫМИ в [Files]:
    # иначе распаковка тянет весь пакет.
    $helperIndex = -1
    $appIndex = -1
    $lines = @(Get-Content -Path $issPath -Encoding UTF8)
    for ($i = 0; $i -lt $lines.Count; $i++) {
        if ($helperIndex -lt 0 -and $lines[$i] -like '*"hrm-snapshot.ps1"; Flags: dontcopy*') { $helperIndex = $i }
        if ($appIndex -lt 0 -and $lines[$i] -match '^Source: "staging\\app\\\*"') { $appIndex = $i }
    }
    Assert-HrmTrue ($helperIndex -ge 0) "в [Files] нет записи dontcopy для вспомогательного скрипта"
    Assert-HrmTrue ($appIndex -ge 0) "в [Files] нет записи снимка приложения"
    Assert-HrmTrue ($helperIndex -lt $appIndex) "вспомогательные файлы идут после снимка приложения (дорогая распаковка при solid compression)"
}

Test-Case "P2: движок честно сообщает отказ снимка и импортирует модуль снимка" {
    $entryPath = Join-Path $script:RepoRoot "infra\windows\hr-manager.ps1"
    $entry = Get-Content -Path $entryPath -Raw -Encoding UTF8
    Assert-HrmContains $entry "Save-HrmInstalledSnapshotForSetup" "снимок прежней версии не сохраняется движком"
    $start = $entry.IndexOf('"snapshot-previous" {')
    $end = $entry.IndexOf('"channel" {')
    Assert-HrmTrue ($start -gt 0 -and $end -gt $start) "не найден блок действия snapshot-previous"
    $action = $entry.Substring($start, $end - $start)
    Assert-HrmContains $action "throw" "отказ снимка не виден вызывающему (действие завершается с кодом 0)"
    # Все три входные точки импортируют модуль снимка.
    foreach ($rel in @("infra\windows\hr-manager.ps1", "infra\windows\hrm-tray.ps1", "infra\windows\tests\test-harness.ps1")) {
        $text = Get-Content -Path (Join-Path $script:RepoRoot $rel) -Raw -Encoding UTF8
        Assert-HrmContains $text '"Install", "Snapshot"' ("модуль снимка не импортируется: " + $rel)
    }
    # Модули и CLI должны читаться PowerShell 5.1 как UTF-8 (кириллица в коде).
    foreach ($rel in @("infra\windows\engine\Snapshot.psm1", "infra\windows\hrm-snapshot.ps1")) {
        $bytes = [System.IO.File]::ReadAllBytes((Join-Path $script:RepoRoot $rel))
        $bom = ($bytes.Length -gt 3 -and $bytes[0] -eq 0xEF -and $bytes[1] -eq 0xBB -and $bytes[2] -eq 0xBF)
        Assert-HrmTrue $bom ("нет UTF-8 BOM: " + $rel)
    }
    $cli = Get-Content -Path (Join-Path $script:RepoRoot "infra\windows\hrm-snapshot.ps1") -Raw -Encoding UTF8
    Assert-HrmContains $cli "Invoke-HrmSnapshotCli" "CLI снимка не вызывает движок снимка"
    Assert-HrmContains $cli "exit `$exitCode" "CLI снимка не возвращает код мастеру установки"
}

Test-Case "P2: правило прежней идентичности описано в руководстве по обновлению" {
    $guide = Get-Content -Path (Join-Path $script:RepoRoot "docs\UPDATE_GUIDE.md") -Raw -Encoding UTF8
    Assert-HrmContains $guide "hrm-snapshot.ps1" "в руководстве не описан вспомогательный скрипт снимка"
    Assert-HrmContains $guide "PrepareToInstall" "не сказано, на каком шаге мастер делает снимок"
    Assert-HrmContains $guide "files_replaced" "не описано поведение при уже заменённых файлах"
    Assert-HrmContains $guide "Правило прежней идентичности" "не описано правило подтверждения отката"
    Assert-HrmContains $guide 'пустой `release_sha`' "не сказано, что пустой release_sha не подтверждает откат"
}

Write-Host "== Итерация 15: проверки завершены =="
