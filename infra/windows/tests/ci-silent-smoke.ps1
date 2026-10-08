#requires -Version 5.1
<#
    CI-смоук «чистая установка → обновление поверх → удаление» для HR Manager.

    Почему это файл репозитория, а не текст шага workflow. Раннер пишет
    временный скрипт шага в UTF-8 БЕЗ BOM, а Windows PowerShell 5.1 читает такие
    файлы в ANSI-кодировке: кириллица в тексте шага превращается в мусор, и
    шаг падал, не оставив ни одной аннотации (логи шагов из среды сопровождения
    не читаются). Здесь файл лежит в репозитории с BOM, поэтому кириллица
    читается корректно, а сам скрипт проверяется и локально
    (infra/windows/tests/static.tests.ps1: BOM + разбор PowerShell 5.1), и в
    движковом прогоне CI.

    Диагностика идёт АННОТАЦИЯМИ ::notice/::error (их видно через check-run
    API). При сбое в аннотацию попадают: причина, код возврата мастера, решение
    мастера о снимке прежней версии и хвост журнала Inno (/LOG=) — журнал
    мастера единственный рассказывает, почему установка остановилась ДО
    перезаписи файлов.

    Секретов здесь нет: пути, коды возврата, SHA-256 и число файлов.
#>
[CmdletBinding()]
param(
    # Каталог репозитория; по умолчанию — три уровня вверх от файла.
    [string]$RepoRoot = "",
    # Каталоги пилотной установки (совпадают с defaults installer.iss).
    [string]$InstallDir = "",
    [string]$StateDir = ""
)

$ErrorActionPreference = "Stop"

function Format-HrmCommandText {
    # GitHub-команды: сообщение с переводом строки обрывается на первой строке,
    # а знак % ломает разбор команды. Длинный текст обрезаем с конца: важное —
    # в хвосте.
    param([string]$Text, [int]$Limit = 3500)
    if (-not $Text) { return "" }
    $flat = $Text.Replace("%", "%25").Replace("`r", "%0D").Replace("`n", "%0A")
    if ($flat.Length -gt $Limit) { $flat = $flat.Substring($flat.Length - $Limit) }
    return $flat
}

function Write-HrmNotice {
    param([string]$Title, [string]$Message)
    Write-Host ("::notice title=" + $Title + "::" + (Format-HrmCommandText -Text $Message -Limit 2000))
}

function Write-HrmError {
    param([string]$Title, [string]$Message)
    Write-Host ("::error title=" + $Title + "::" + (Format-HrmCommandText -Text $Message))
}

function Read-HrmJsonFile {
    param([string]$Path)
    if (-not (Test-Path -LiteralPath $Path)) { return $null }
    return (Get-Content -LiteralPath $Path -Raw -Encoding UTF8 | ConvertFrom-Json)
}

function Get-HrmJsonProperty {
    # Отсутствующее свойство — это провал проверки, а не «значение по
    # умолчанию»: именно verified/origin/release_sha доказывают, что снимок
    # прежней версии сделан ДО перезаписи файлов.
    param($Object, [string]$Name)
    if ($null -eq $Object) { throw ("нет объекта для чтения свойства " + $Name) }
    if (-not $Object.PSObject.Properties[$Name]) { throw ("в файле нет свойства " + $Name) }
    return $Object.$Name
}

function Write-HrmJsonFile {
    # Как installer/build.ps1: UTF-8 без BOM (файл читает движок).
    param([string]$Path, [string]$Json)
    [System.IO.File]::WriteAllText($Path, $Json, (New-Object System.Text.UTF8Encoding($false)))
}

function Invoke-HrmSetupProcess {
    # /LOG=<файл>: журнал Inno нужен, чтобы сбой был виден причиной, а не
    # «exit code 1». Код возврата обязателен к проверке: остановка мастера до
    # перезаписи файлов (гейт снимка) обязана быть видна автоматике.
    param([string]$Exe, [string]$LogPath)
    Remove-Item -LiteralPath $LogPath -Force -ErrorAction SilentlyContinue
    $arguments = @("/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART", ('/LOG="{0}"' -f $LogPath))
    $process = Start-Process -FilePath $Exe -ArgumentList $arguments -Wait -PassThru
    return [int]$process.ExitCode
}

function Write-HrmSetupLogTail {
    # Хвост журнала мастера — в аннотацию: логи шага читать из среды
    # сопровождения нельзя, а причина остановки мастера есть только там.
    param([string]$Path, [string]$Title, [int]$Lines = 25)
    if (-not (Test-Path -LiteralPath $Path)) {
        Write-HrmError $Title ("журнал мастера не найден: " + $Path)
        return
    }
    $tail = @(Get-Content -LiteralPath $Path -Encoding UTF8 -Tail $Lines)
    Write-HrmError $Title ("журнал " + $Path + "`n" + ($tail -join "`n"))
}

function Assert-HrmSetupLog {
    param([string]$Path, [string]$Pattern, [string]$Message)
    if (-not (Test-Path -LiteralPath $Path)) { throw ($Message + " (журнала нет: " + $Path + ")") }
    $text = Get-Content -LiteralPath $Path -Raw -Encoding UTF8
    if ($text -notmatch $Pattern) {
        throw ($Message + " (в журнале мастера нет строки по образцу: " + $Pattern + ")")
    }
}

function Show-HrmSnapshotDecision {
    # Файл диагностики пишет мастер (installer.iss). На чистой установке
    # каталога состояния ещё нет, и решение видно только в журнале мастера.
    param([string]$Directory, [string]$Label)
    $file = Join-Path $Directory "setup-snapshot.json"
    $data = Read-HrmJsonFile $file
    if ($null -eq $data) {
        Write-HrmNotice $Label ("файла диагностики снимка нет: " + $file)
        return $null
    }
    $summary = "schema=" + [string]$data.schema + " needed=" + [string]$data.needed +
        " status=" + [string]$data.status + " reason=" + [string]$data.reason +
        " run_ok=" + [string]$data.run_ok + " exit=" + [string]$data.run_exit +
        " stop=" + [string]$data.stop + " at=" + [string]$data.at
    Write-HrmNotice $Label $summary
    return $data
}

try {
    if (-not $RepoRoot) {
        $RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..\..")).Path
    }
    if (-not $InstallDir) { $InstallDir = Join-Path $env:LOCALAPPDATA "Programs\HRManager" }
    if (-not $StateDir) { $StateDir = Join-Path $env:LOCALAPPDATA "HRManager" }

    Write-HrmNotice "Silent smoke" ("старт: скрипт=" + $PSCommandPath + " каталог установки=" + $InstallDir)

    $setup = Get-ChildItem -Path (Join-Path $RepoRoot "installer\output") -Filter "HR-Manager-Setup-*.exe" -ErrorAction SilentlyContinue |
        Select-Object -First 1
    if (-not $setup) { throw "в installer\output нет HR-Manager-Setup-*.exe" }
    Write-HrmNotice "Silent smoke" ("мастер: " + $setup.Name + " (" + $setup.Length + " байт)")

    # --- 1. Чистая установка -------------------------------------------------
    # Снимка не будет (сохранять нечего), и мастер обязан продолжить установку:
    # решение видно в журнале Inno (/LOG) строкой «HRM: snapshot decision».
    $installLog = Join-Path $env:TEMP "hrm-smoke-install.log"
    $installCode = Invoke-HrmSetupProcess -Exe $setup.FullName -LogPath $installLog
    if ($installCode -ne 0) {
        Write-HrmSetupLogTail -Path $installLog -Title "Silent smoke: чистая установка не прошла"
        throw ("чистая установка завершилась с кодом " + $installCode)
    }
    Assert-HrmSetupLog -Path $installLog -Pattern "HRM: snapshot decision needed=0" `
        -Message "мастер не сообщил, что на чистой установке сохранять нечего"
    Write-HrmNotice "Silent smoke: установка" "exit=0"

    $engine = Join-Path $InstallDir "infra\windows\hr-manager.ps1"
    if (-not (Test-Path -LiteralPath $engine)) { throw "в каталоге установки нет движка" }
    $uninstaller = Join-Path $InstallDir "unins000.exe"
    if (-not (Test-Path -LiteralPath $uninstaller)) { throw "нет деинсталлятора" }

    # --- 2. «Прежняя версия» и обновление поверх -----------------------------
    # Идентификаторы обязаны отличаться от пакета: иначе снимок прежней версии
    # делать запрещено (files_replaced) — так мастер защищается от «отката» к
    # сбойной сборке.
    $previousSha = ("a" * 40)
    $releasePath = Join-Path $InstallDir "release.json"
    $release = Read-HrmJsonFile $releasePath
    $newSha = [string](Get-HrmJsonProperty -Object $release -Name "release_sha")
    if (-not $newSha) { throw "в release.json пакета нет release_sha" }
    $release.release_sha = $previousSha
    $release.version = "0.0.1"
    Write-HrmJsonFile -Path $releasePath -Json ($release | ConvertTo-Json -Depth 5)
    # Дата назад: пакетный release.json должен быть новее своего прежнего.
    (Get-Item -LiteralPath $releasePath).LastWriteTime = (Get-Date).AddDays(-1)
    $record = [ordered]@{
        version = "0.0.1"
        release_sha = $previousSha
        port = 8080
        installed_at = (Get-Date).AddDays(-1).ToString("o")
    }
    Write-HrmJsonFile -Path (Join-Path $StateDir "installed.json") -Json ($record | ConvertTo-Json)
    Remove-Item -LiteralPath (Join-Path $StateDir "previous-snapshot.json") -Force -ErrorAction SilentlyContinue

    $upgradeLog = Join-Path $env:TEMP "hrm-smoke-upgrade.log"
    $upgradeCode = Invoke-HrmSetupProcess -Exe $setup.FullName -LogPath $upgradeLog
    $decision = Show-HrmSnapshotDecision -Directory $StateDir -Label "Silent smoke: снимок"
    if ($upgradeCode -ne 0) {
        Write-HrmSetupLogTail -Path $upgradeLog -Title "Silent smoke: обновление не прошло"
        throw ("обновление поверх установки завершилось с кодом " + $upgradeCode)
    }
    Write-HrmNotice "Silent smoke: обновление" "exit=0"

    # Снимок обязан быть сделан ДО перезаписи: подтверждён, сделан мастером и
    # содержит файлы ПРЕЖНЕЙ версии, а {app} уже перезаписан новым релизом.
    $descriptor = Read-HrmJsonFile (Join-Path $StateDir "previous-snapshot.json")
    if ($null -eq $descriptor) { throw "снимок прежней версии не создан (previous-snapshot.json)" }
    if ((Get-HrmJsonProperty -Object $descriptor -Name "verified") -ne $true) {
        throw "снимок прежней версии не подтверждён (verified <> true)"
    }
    $origin = [string](Get-HrmJsonProperty -Object $descriptor -Name "origin")
    if ($origin -ne "installer") { throw ("снимок сделан не мастером установки: origin=" + $origin) }
    $descriptorSha = [string](Get-HrmJsonProperty -Object $descriptor -Name "release_sha")
    if ($descriptorSha -ne $previousSha) { throw ("снимок сделан не из прежней версии: " + $descriptorSha) }
    $files = [int](Get-HrmJsonProperty -Object $descriptor -Name "files")
    if ($files -le 0) { throw "в снимке прежней версии нет ни одного файла" }
    $snapshotRelease = Read-HrmJsonFile (Join-Path $StateDir "previous-snapshot\release.json")
    $snapshotSha = [string](Get-HrmJsonProperty -Object $snapshotRelease -Name "release_sha")
    if ($snapshotSha -ne $previousSha) { throw ("в снимке нет файлов прежней версии: " + $snapshotSha) }
    $nowSha = [string](Get-HrmJsonProperty -Object (Read-HrmJsonFile $releasePath) -Name "release_sha")
    if ($nowSha -ne $newSha) { throw ("мастер не заменил файлы программы: " + $nowSha) }
    if ($null -ne $decision) {
        $status = [string](Get-HrmJsonProperty -Object $decision -Name "status")
        if ($status -ne "verified") { throw ("решение мастера о снимке не подтверждено: status=" + $status) }
        $needed = [string](Get-HrmJsonProperty -Object $decision -Name "needed")
        if ($needed -ne "1" -and $needed -ne "True") { throw ("мастер не считал снимок нужным: needed=" + $needed) }
        $stopped = [string](Get-HrmJsonProperty -Object $decision -Name "stop")
        if ($stopped -eq "1" -or $stopped -eq "True") { throw "мастер сообщил об остановке при успешном обновлении" }
    }
    Write-HrmNotice "Silent smoke: снимок" ("verified=true origin=installer файлов=" + $files +
        " прежний=" + $previousSha.Substring(0, 12) + " новый=" + $newSha.Substring(0, 12))

    # --- 3. Удаление ---------------------------------------------------------
    # Программу удаляем; каталог состояния и тома данных остаются (их хранит
    # движок и человек, а не мастер).
    $uninstallLog = Join-Path $env:TEMP "hrm-smoke-uninstall.log"
    $uninstallCode = Invoke-HrmSetupProcess -Exe $uninstaller -LogPath $uninstallLog
    if ($uninstallCode -ne 0) {
        Write-HrmSetupLogTail -Path $uninstallLog -Title "Silent smoke: удаление не прошло"
        throw ("удаление завершилось с кодом " + $uninstallCode)
    }
    if (Test-Path -LiteralPath $engine) { throw "после удаления файлы программы остались на месте" }
    Write-HrmNotice "Silent smoke: удаление" "exit=0"

    Write-HrmNotice "Silent smoke" "все фазы пройдены (установка, обновление со снимком, удаление)"
    exit 0
}
catch {
    Write-HrmError "Silent smoke" ("сбой: " + $_.Exception.Message)
    exit 1
}
