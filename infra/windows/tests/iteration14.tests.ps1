# Регрессии итерации 14: четыре дефекта, найденные независимой проверкой.
#   1) P1: первичная установка удаляла собственный источник, когда Setup.exe
#          передавал SourceDir = {app} = InstallDir (и ошибки копирования не
#          останавливали установку);
#   2) P1: откат обновления восстанавливал только теги образов и писал
#          «предыдущая версия восстановлена» без проверки готовности;
#   3) P2: значок в трее повторно включал автозапуск, который человек выключил;
#   4) P2: значок в трее выходил с ошибкой «HR Manager не установлен», потому
#          что мастер установки запускает его РАНЬШЕ движка.
# Тесты используют те же мок-миры, что и остальные наборы: реальная машина,
# Docker и сеть не затрагиваются. Локальные пути (замок файла) — настоящие:
# проверяем границы, а не только моки.

param([string]$HarnessPath = $PSScriptRoot)

. (Join-Path $HarnessPath "test-harness.ps1")

$script:RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..\..")).Path

Write-Host "== Итерация 14: регрессии ревью (установка, откат, автозапуск, трей) =="

function New-HrmIteration14Dir {
    param([string]$Name = "case")
    $dir = Join-Path ([System.IO.Path]::GetTempPath()) ("HRM i14 " + $Name + " " + [guid]::NewGuid().ToString("N").Substring(0, 8))
    New-Item -ItemType Directory -Path $dir -Force | Out-Null
    return $dir
}

# --- P1-1: Copy-HrmSnapshot ---------------------------------------------------

Test-Case "P1-1: одинаковые пути (Setup передаёт SourceDir={app}) — снимок не удаляется" {
    Initialize-HrmTestEngine
    $world = New-HrmMockWorld
    Set-HrmPreflightOverride @{ windows = $true; powershell = $true; docker = $true; daemon = $true; compose = "v2.29.7 (mock)"; port = $true; state_dir = $true; space = $true; config = $true }
    $state = Get-HrmTestStateDir
    $install = Get-HrmTestInstallDir
    # Мастер установки уже разложил файлы в {app} и запускает движок с -SourceDir {app}.
    New-HrmFakeSnapshot -Root $install -ReleaseSha ("1" * 40)
    Set-Content -Path (Join-Path $install "backend\marker.txt") -Value "v1" -Encoding ASCII
    Install-HrmApp -SourceDir $install -InstallDir $install -StateDir $state -Port 8080
    Assert-HrmTrue (Test-Path (Join-Path $install "infra\compose.pilot.yml")) "снимок приложения уничтожен на первичной установке"
    Assert-HrmTrue (Test-Path (Join-Path $install "release.json")) "release.json уничтожен на первичной установке"
    Assert-HrmEqual "v1" ((Get-Content (Join-Path $install "backend\marker.txt") -Raw).Trim()) "файлы каталога установки повреждены"
    Assert-HrmEqual ("1" * 40) ([string](Get-HrmInstallRecord $state).release_sha) "установка не зафиксирована"
    Clear-HrmExternalMock
    Clear-HrmHttpMock
}

Test-Case "P1-1: Copy-HrmSnapshot с хвостовым разделителем в пути не трогает источник" {
    Initialize-HrmTestEngine
    New-HrmMockWorld | Out-Null
    $root = New-HrmIteration14Dir "same-path"
    New-HrmFakeSnapshot -Root $root -ReleaseSha ("2" * 40)
    Set-Content -Path (Join-Path $root "backend\marker.txt") -Value "keep" -Encoding ASCII
    Copy-HrmSnapshot -SourceDir $root -InstallDir ($root + "\")
    Assert-HrmEqual "keep" ((Get-Content (Join-Path $root "backend\marker.txt") -Raw).Trim()) "источник изменён при совпадении путей"
    Assert-HrmTrue (Test-Path (Join-Path $root "infra\compose.pilot.yml")) "снимок исчез"
}

Test-Case "P1-1: обычное копирование обновляет назначение и не трогает источник" {
    Initialize-HrmTestEngine
    New-HrmMockWorld | Out-Null
    $src = New-HrmIteration14Dir "copy-src"
    $dst = New-HrmIteration14Dir "copy-dst"
    New-HrmFakeSnapshot -Root $src -ReleaseSha ("b" * 40)
    Set-Content -Path (Join-Path $src "backend\marker.txt") -Value "NEW" -Encoding ASCII
    New-HrmFakeSnapshot -Root $dst -ReleaseSha ("a" * 40)
    Set-Content -Path (Join-Path $dst "backend\marker.txt") -Value "OLD" -Encoding ASCII
    Copy-HrmSnapshot -SourceDir $src -InstallDir $dst
    Assert-HrmEqual "NEW" ((Get-Content (Join-Path $dst "backend\marker.txt") -Raw).Trim()) "назначение не обновлено"
    Assert-HrmEqual "NEW" ((Get-Content (Join-Path $src "backend\marker.txt") -Raw).Trim()) "источник изменён при копировании"
    $leaked = @(Get-ChildItem -Path $dst -Filter ".hrm-snapshot-*" -Force -ErrorAction SilentlyContinue)
    Assert-HrmEqual 0 $leaked.Count "остался временный каталог копирования"
}

Test-Case "P1-1: вложенные пути отклоняются ДО любых изменений файлов" {
    Initialize-HrmTestEngine
    New-HrmMockWorld | Out-Null
    # Источник внутри назначения.
    $dest = New-HrmIteration14Dir "nested-dest"
    $src = Join-Path $dest "release"
    New-HrmFakeSnapshot -Root $src -ReleaseSha ("c" * 40)
    $failed = $false
    try { Copy-HrmSnapshot -SourceDir $src -InstallDir $dest } catch { $failed = $true }
    Assert-HrmTrue $failed "вложенные пути не отклонены"
    Assert-HrmTrue (Test-Path (Join-Path $src "release.json")) "источник изменён при отказе"
    Assert-HrmFalse (Test-Path (Join-Path $dest "release.json")) "назначение изменено при отказе"
    # Назначение внутри источника.
    $outer = New-HrmIteration14Dir "nested-src"
    New-HrmFakeSnapshot -Root $outer -ReleaseSha ("d" * 40)
    $inner = Join-Path $outer "install"
    $failed2 = $false
    try { Copy-HrmSnapshot -SourceDir $outer -InstallDir $inner } catch { $failed2 = $true }
    Assert-HrmTrue $failed2 "назначение внутри источника не отклонено"
    Assert-HrmFalse (Test-Path (Join-Path $inner "release.json")) "создан каталог установки внутри источника"
    Assert-HrmTrue (Test-Path (Join-Path $outer "release.json")) "источник изменён при отказе"
}

Test-Case "P1-1: ошибка копирования останавливает установку и не ломает установленную версию" {
    Initialize-HrmTestEngine
    New-HrmMockWorld | Out-Null
    $src = New-HrmIteration14Dir "locked-src"
    $dst = New-HrmIteration14Dir "locked-dst"
    New-HrmFakeSnapshot -Root $src -ReleaseSha ("e" * 40)
    New-HrmFakeSnapshot -Root $dst -ReleaseSha ("a" * 40)
    Set-Content -Path (Join-Path $dst "backend\marker.txt") -Value "OLD" -Encoding ASCII
    # Настоящий замок файла: копирование обязано упасть, а не «пройти молча».
    $handle = [System.IO.File]::Open((Join-Path $src "backend\marker.txt"), [System.IO.FileMode]::Open, [System.IO.FileAccess]::Read, [System.IO.FileShare]::None)
    $failed = $false
    try {
        Copy-HrmSnapshot -SourceDir $src -InstallDir $dst
    }
    catch { $failed = $true }
    finally { $handle.Dispose() }
    Assert-HrmTrue $failed "ошибка копирования не остановила установку"
    Assert-HrmEqual "OLD" ((Get-Content (Join-Path $dst "backend\marker.txt") -Raw).Trim()) "установленная версия повреждена при ошибке копирования"
    Assert-HrmEqual ("a" * 40) ([string]((Get-HrmJsonFile (Join-Path $dst "release.json")).release_sha)) "назначение изменено до успешной копии"
}

Test-Case "P1-1: неполный снимок релиза не копируется (проверка полноты)" {
    Initialize-HrmTestEngine
    New-HrmMockWorld | Out-Null
    $src = New-HrmIteration14Dir "incomplete"
    $dst = New-HrmIteration14Dir "incomplete-dst"
    New-Item -ItemType Directory -Path (Join-Path $src "infra") -Force | Out-Null
    Set-Content -Path (Join-Path $src "infra\compose.pilot.yml") -Value "services: {}" -Encoding ASCII
    # Нет каталогов backend/frontend — снимок неполный.
    $failed = $false
    try { Copy-HrmSnapshot -SourceDir $src -InstallDir $dst } catch { $failed = $true }
    Assert-HrmTrue $failed "неполный снимок принят как успешный"
    Assert-HrmFalse (Test-Path (Join-Path $dst "infra")) "неполный снимок разложен в каталог установки"
}

# --- P1-2: откат обновления --------------------------------------------------

Test-Case "P1-2: откат возвращает прежние файлы, образы и release_sha (снимок от мастера установки)" {
    Initialize-HrmTestEngine
    $world = New-HrmMockWorld
    Set-HrmPreflightOverride @{ windows = $true; powershell = $true; docker = $true; daemon = $true; compose = "v2.29.7 (mock)"; port = $true; state_dir = $true; space = $true; config = $true }
    $state = Get-HrmTestStateDir
    $install = Get-HrmTestInstallDir
    $shaA = ("a" * 40)
    $shaB = ("b" * 40)
    # Установлена версия A.
    New-HrmFakeSnapshot -Root $install -ReleaseSha $shaA
    Set-Content -Path (Join-Path $install "backend\marker.txt") -Value "OLD" -Encoding ASCII
    Install-HrmApp -SourceDir $install -InstallDir $install -StateDir $state -Port 8080 | Out-Null
    Assert-HrmEqual $shaA ([string](Get-HrmInstallRecord $state).release_sha) "версия A не зафиксирована"
    # Мастер установки (ssInstall) сохраняет прежнюю версию и перезаписывает файлы версией B.
    $saved = Save-HrmInstalledSnapshotForSetup -InstallDir $install -StateDir $state
    Assert-HrmTrue ([bool]$saved.saved) "снимок прежней версии не сохранён перед установкой"
    New-HrmFakeSnapshot -Root $install -ReleaseSha $shaB
    Set-Content -Path (Join-Path $install "backend\marker.txt") -Value "NEW" -Encoding ASCII
    # Обновление терпит неудачу на проверке готовности: работающая версия «не сменилась».
    $world.SimulateStaleRelease = $true
    $world.OpsBody.release_sha = $shaA
    $world.TagCount = 0
    $caught = $false
    try { Install-HrmApp -SourceDir $install -InstallDir $install -StateDir $state -Port 8080 | Out-Null } catch { $caught = $true }
    Assert-HrmTrue $caught "провал обновления не дошёл до вызывающего"
    Assert-HrmEqual $shaA ([string](Get-HrmInstallRecord $state).release_sha) "запись установки не вернулась к прежней версии"
    Assert-HrmEqual "OLD" ((Get-Content (Join-Path $install "backend\marker.txt") -Raw).Trim()) "файлы прежней версии не восстановлены"
    Assert-HrmTrue ($world.TagCount -ge 6) "прежние образы не восстановлены по тегам"
    $result = Get-HrmUpdateResult $state
    Assert-HrmEqual "rolled_back" ([string]$result.status) "статус отката не rolled_back"
    Assert-HrmTrue ([bool]$result.recovery_confirmed) "готовность прежней версии не подтверждена, а статус заявлен как успешный откат"
    Assert-HrmFalse ([bool]$result.needs_backup_restore) "лишнее требование восстановления из бэкапа"
    Assert-HrmContains ([string]$result.message) "прежняя версия" "сообщение не объясняет откат"
    Assert-HrmContains ([string]$result.message) "Схема базы данных остаётся на новой ревизии" "после применённой миграции не сказано про схему БД"
    # Снимок мастера установки не затёрт новыми файлами.
    $snapshotSha = [string]((Get-HrmJsonFile (Join-Path (Get-HrmPreviousSnapshotDir $state) "release.json")).release_sha)
    Assert-HrmEqual $shaA $snapshotSha "снимок прежней версии перезаписан файлами новой версии"
    Clear-HrmExternalMock
    Clear-HrmHttpMock
}

Test-Case "P1-2: снимок для мастера не берётся из уже заменённых файлов" {
    Initialize-HrmTestEngine
    $world = New-HrmMockWorld
    Set-HrmPreflightOverride @{ windows = $true; powershell = $true; docker = $true; daemon = $true; compose = "v2.29.7 (mock)"; port = $true; state_dir = $true; space = $true; config = $true }
    $state = Get-HrmTestStateDir
    $install = Get-HrmTestInstallDir
    $shaA = ("a" * 40)
    $shaB = ("b" * 40)
    New-HrmFakeSnapshot -Root $install -ReleaseSha $shaA
    Install-HrmApp -SourceDir $install -InstallDir $install -StateDir $state -Port 8080 | Out-Null
    # Мастер установки УЖЕ перезаписал файлы новой версией: сверка с записью не проходит.
    New-HrmFakeSnapshot -Root $install -ReleaseSha $shaB
    $skipped = Save-HrmInstalledSnapshotForSetup -InstallDir $install -StateDir $state
    Assert-HrmFalse ([bool]$skipped.saved) "недостоверный снимок сохранён как прежняя версия"
    Assert-HrmTrue ([bool]$skipped.skipped) "пропуск недостоверного снимка не отмечен"
    Assert-HrmContains ([string]$skipped.message) "не совпадают" "сообщение не объясняет, почему снимок не сохранён"
    Assert-HrmFalse (Test-Path (Join-Path (Get-HrmPreviousSnapshotDir $state) "release.json")) "недостоверный снимок всё же записан на диск"
    # Если release.json в {app} нет, сверять нечего: файлы не противоречат записи,
    # снимок сохраняется как есть (иначе откат был бы невозможен вовсе).
    Remove-Item (Join-Path $install "release.json") -Force
    $saved = Save-HrmInstalledSnapshotForSetup -InstallDir $install -StateDir $state
    Assert-HrmTrue ([bool]$saved.saved) "без release.json снимок не сохранён"
    Assert-HrmTrue (Test-Path (Join-Path (Get-HrmPreviousSnapshotDir $state) "backend\marker.txt")) "снимок без release.json не содержит файлов"
    Clear-HrmExternalMock
    Clear-HrmHttpMock
}

Test-Case "P1-2: откат без подтверждённой готовности не объявляется успешным" {
    Initialize-HrmTestEngine
    $world = New-HrmMockWorld
    Set-HrmPreflightOverride @{ windows = $true; powershell = $true; docker = $true; daemon = $true; compose = "v2.29.7 (mock)"; port = $true; state_dir = $true; space = $true; config = $true }
    $state = Get-HrmTestStateDir
    $install = Get-HrmTestInstallDir
    $shaA = ("a" * 40)
    $shaB = ("b" * 40)
    New-HrmFakeSnapshot -Root $install -ReleaseSha $shaA
    Set-Content -Path (Join-Path $install "backend\marker.txt") -Value "OLD" -Encoding ASCII
    Install-HrmApp -SourceDir $install -InstallDir $install -StateDir $state -Port 8080 | Out-Null
    $null = Save-HrmInstalledSnapshotForSetup -InstallDir $install -StateDir $state
    $releaseB = New-HrmIteration14Dir "release-b"
    New-HrmFakeSnapshot -Root $releaseB -ReleaseSha $shaB
    # Приложение отвечает, но в работе по-прежнему сбойная версия: обновление
    # падает уже после замены файлов (проверка worker), а мок после отката
    # продолжает сообщать сбойный release_sha — готовность не подтверждена,
    # статус обязан быть честным.
    $world.SimulateStaleRelease = $true
    $world.OpsBody.release_sha = $shaB
    $world.WorkerCheckOk = $false
    $caught = $false
    try { Update-HrmApp -ReleaseDir $releaseB -InstallDir $install -StateDir $state } catch { $caught = $true }
    Assert-HrmTrue $caught "провал обновления не дошёл до вызывающего"
    $result = Get-HrmUpdateResult $state
    Assert-HrmEqual "rollback_failed" ([string]$result.status) "неподтверждённое восстановление объявлено успешным"
    Assert-HrmFalse ([bool]$result.rolled_back) "флаг rolled_back выставлен без подтверждения"
    Assert-HrmFalse ([bool]$result.recovery_confirmed) "recovery_confirmed без подтверждённой готовности"
    Assert-HrmTrue ([bool]$result.needs_backup_restore) "не сказано про восстановление из проверенного бэкапа"
    Assert-HrmContains ([string]$result.message) "не подтверждено" "сообщение не сообщает о неподтверждённом восстановлении"
    Assert-HrmNotContains ([string]$result.message) "восстановлена и отвечает" "ложное заявление о восстановлении"
    # Журнал и данные для разбора сохранены.
    $journal = Get-HrmJsonFile (Get-HrmUpdateJournal $state)
    Assert-HrmEqual "rollback" ([string]$journal.phase) "журнал обновления не сохранён для разбора"
    Assert-HrmFalse (Test-Path (Get-HrmUpdateLock $state)) "блокировка обновления осталась"
    Clear-HrmExternalMock
    Clear-HrmHttpMock
}

Test-Case "P1-2: сбой до замены файлов — честный failed, установленная версия не трогается" {
    Initialize-HrmTestEngine
    $world = New-HrmMockWorld
    Set-HrmPreflightOverride @{ windows = $true; powershell = $true; docker = $true; daemon = $true; compose = "v2.29.7 (mock)"; port = $true; state_dir = $true; space = $true; config = $true }
    $state = Get-HrmTestStateDir
    $install = Get-HrmTestInstallDir
    $shaA = ("a" * 40)
    New-HrmFakeSnapshot -Root $install -ReleaseSha $shaA
    Set-Content -Path (Join-Path $install "backend\marker.txt") -Value "OLD" -Encoding ASCII
    Install-HrmApp -SourceDir $install -InstallDir $install -StateDir $state -Port 8080 | Out-Null
    $releaseB = New-HrmIteration14Dir "release-b-gate"
    New-HrmFakeSnapshot -Root $releaseB -ReleaseSha ("b" * 40)
    $upBefore = $world.UpCount
    # Бэкап не прошёл проверку целостности — обновление обязано остановиться.
    $world.BackupCheckOk = $false
    $caught = $false
    try { Update-HrmApp -ReleaseDir $releaseB -InstallDir $install -StateDir $state } catch { $caught = $true }
    Assert-HrmTrue $caught "провал бэкап-ворот не остановил обновление"
    Assert-HrmEqual "OLD" ((Get-Content (Join-Path $install "backend\marker.txt") -Raw).Trim()) "файлы изменены до бэкап-ворот"
    Assert-HrmEqual $shaA ([string](Get-HrmInstallRecord $state).release_sha) "запись установки изменена до бэкап-ворот"
    $result = Get-HrmUpdateResult $state
    Assert-HrmEqual "failed" ([string]$result.status) "статус при сбое до замены файлов не failed"
    Assert-HrmEqual $upBefore ([int]$world.UpCount) "перезапуск стека при сбое до замены файлов (приложение не должно останавливаться)"
    Assert-HrmContains ([string]$result.message) "не изменялась" "сообщение не объясняет, что установленная версия не менялась"
    Assert-HrmNotContains ([string]$result.message) "восстановлена и отвечает" "ложное заявление о восстановлении"
    Clear-HrmExternalMock
    Clear-HrmHttpMock
}

Test-Case "P1-2: resume продолжает обновление из копии движка, когда каталог релиза исчез" {
    Initialize-HrmTestEngine
    $world = New-HrmMockWorld
    Set-HrmPreflightOverride @{ windows = $true; powershell = $true; docker = $true; daemon = $true; compose = "v2.29.7 (mock)"; port = $true; state_dir = $true; space = $true; config = $true }
    $state = Get-HrmTestStateDir
    $install = Get-HrmTestInstallDir
    $shaA = ("a" * 40)
    $shaB = ("b" * 40)
    New-HrmFakeSnapshot -Root $install -ReleaseSha $shaA
    Install-HrmApp -SourceDir $install -InstallDir $install -StateDir $state -Port 8080 | Out-Null
    $releaseB = New-HrmIteration14Dir "release-b-resume"
    New-HrmFakeSnapshot -Root $releaseB -ReleaseSha $shaB
    Set-Content -Path (Join-Path $releaseB "backend\marker.txt") -Value "NEW" -Encoding ASCII
    # Копия движка: так делает сама фаза prepare (Setup удалил бы свой временный каталог).
    $staging = Copy-HrmReleaseToStaging -ReleaseDir $releaseB -StateDir $state
    # Прерванное обновление: фаза smoke, файлы новой версии уже разложены.
    Set-HrmUpdateJournal $state "smoke" @{
        release_dir = $staging
        previous_ids = @{ }
        release_sha = $shaB
        previous_release_sha = $shaA
        db_revision_before = "0013"
        migration_done = $true
        previous_snapshot_saved = $true
    }
    Copy-HrmSnapshot -SourceDir $staging -InstallDir $install
    # В работе уже новая версия (фаза smoke журнала соответствует этому).
    $world.OpsBody.release_sha = $shaB
    $upgradesBefore = $world.AlembicUpgradeCount
    # Каталог релиза, из которого начиналось обновление, больше не существует.
    Remove-Item -Path $releaseB -Recurse -Force
    $missing = Join-Path ([System.IO.Path]::GetTempPath()) ("HRM missing " + [guid]::NewGuid().ToString("N").Substring(0, 8))
    Update-HrmApp -ReleaseDir $missing -InstallDir $install -StateDir $state
    Assert-HrmEqual $shaB ([string](Get-HrmInstallRecord $state).release_sha) "возобновление не довело обновление до конца"
    Assert-HrmFalse (Test-Path (Get-HrmUpdateJournal $state)) "журнал обновления не очищен после успеха"
    $result = Get-HrmUpdateResult $state
    Assert-HrmEqual "done" ([string]$result.status) "результат возобновления не done"
    Assert-HrmTrue ($world.AlembicUpgradeCount -ge $upgradesBefore) "фаза миграции не выполнялась"
    Clear-HrmExternalMock
    Clear-HrmHttpMock
}

Test-Case "P1-2: файлы {app} уже заменены установщиком — обновление продолжается без ложного снимка" {
    Initialize-HrmTestEngine
    $world = New-HrmMockWorld
    Set-HrmPreflightOverride @{ windows = $true; powershell = $true; docker = $true; daemon = $true; compose = "v2.29.7 (mock)"; port = $true; state_dir = $true; space = $true; config = $true }
    $state = Get-HrmTestStateDir
    $install = Get-HrmTestInstallDir
    $shaA = ("a" * 40)
    $shaB = ("b" * 40)
    New-HrmFakeSnapshot -Root $install -ReleaseSha $shaA
    Install-HrmApp -SourceDir $install -InstallDir $install -StateDir $state -Port 8080 | Out-Null
    # Setup.exe перезаписал {app} версией B, но снимок прежней версии НЕ сохранил.
    New-HrmFakeSnapshot -Root $install -ReleaseSha $shaB
    Set-Content -Path (Join-Path $install "backend\marker.txt") -Value "NEW" -Encoding ASCII
    $releaseB = New-HrmIteration14Dir "release-b-replaced"
    New-HrmFakeSnapshot -Root $releaseB -ReleaseSha $shaB
    Update-HrmApp -ReleaseDir $releaseB -InstallDir $install -StateDir $state
    $result = Get-HrmUpdateResult $state
    Assert-HrmEqual "done" ([string]$result.status) "обновление с уже заменёнными файлами не доведено до конца"
    Assert-HrmEqual $shaB ([string](Get-HrmInstallRecord $state).release_sha) "новая версия не зафиксирована"
    Assert-HrmFalse (Test-Path (Get-HrmPreviousSnapshotDir $state)) "файлы новой версии сохранены как «прежняя версия»"
    Clear-HrmExternalMock
    Clear-HrmHttpMock
}

Test-Case "P1-2: без снимка прежней версии откат не подтверждается (unknown_schema)" {
    Initialize-HrmTestEngine
    $world = New-HrmMockWorld
    Set-HrmPreflightOverride @{ windows = $true; powershell = $true; docker = $true; daemon = $true; compose = "v2.29.7 (mock)"; port = $true; state_dir = $true; space = $true; config = $true }
    $state = Get-HrmTestStateDir
    $install = Get-HrmTestInstallDir
    $shaA = ("a" * 40)
    $shaB = ("b" * 40)
    $shaC = ("c" * 40)
    New-HrmFakeSnapshot -Root $install -ReleaseSha $shaA
    Install-HrmApp -SourceDir $install -InstallDir $install -StateDir $state -Port 8080 | Out-Null
    # Файлы прежней версии утрачены: Setup перезаписал {app} версией B без снимка.
    New-HrmFakeSnapshot -Root $install -ReleaseSha $shaB
    $releaseC = New-HrmIteration14Dir "release-c-no-snapshot"
    New-HrmFakeSnapshot -Root $releaseC -ReleaseSha $shaC
    # Новая версия не начинает отвечать: обновление обязано признать сбой.
    $world.SimulateStaleRelease = $true
    $world.OpsBody.release_sha = $shaB
    $caught = $false
    try { Update-HrmApp -ReleaseDir $releaseC -InstallDir $install -StateDir $state } catch { $caught = $true }
    Assert-HrmTrue $caught "сбой обновления без снимка прежней версии не дошёл до вызывающего"
    $result = Get-HrmUpdateResult $state
    Assert-HrmEqual "rollback_failed" ([string]$result.status) "неподтверждённое восстановление объявлено успешным"
    Assert-HrmFalse ([bool]$result.rolled_back) "флаг rolled_back выставлен без снимка прежней версии"
    Assert-HrmFalse ([bool]$result.recovery_confirmed) "recovery_confirmed без снимка прежней версии"
    Assert-HrmTrue ([bool]$result.needs_backup_restore) "не сказано про восстановление из проверенной копии"
    Assert-HrmContains ([string]$result.message) "не подтверждено" "сообщение не сообщает о неподтверждённом восстановлении"
    Assert-HrmContains ([string]$result.message) "снимок" "не объяснено, что снимка прежней версии нет"
    Assert-HrmNotContains ([string]$result.message) "восстановлена и отвечает" "ложное заявление о восстановлении"
    $journal = Get-HrmJsonFile (Get-HrmUpdateJournal $state)
    Assert-HrmEqual "rollback" ([string]$journal.phase) "журнал не сохранён для разбора"
    Assert-HrmEqual "unknown_schema" ([string]$journal.plan) "политика отката не распознана как unknown_schema"
    Assert-HrmFalse (Test-Path (Get-HrmUpdateLock $state)) "блокировка обновления осталась"
    Clear-HrmExternalMock
    Clear-HrmHttpMock
}

Test-Case "P1-2: после отката новая попытка начинается заново, а не повторяет откат" {
    Initialize-HrmTestEngine
    $world = New-HrmMockWorld
    Set-HrmPreflightOverride @{ windows = $true; powershell = $true; docker = $true; daemon = $true; compose = "v2.29.7 (mock)"; port = $true; state_dir = $true; space = $true; config = $true }
    $state = Get-HrmTestStateDir
    $install = Get-HrmTestInstallDir
    $shaA = ("a" * 40)
    $shaB = ("b" * 40)
    New-HrmFakeSnapshot -Root $install -ReleaseSha $shaA
    Install-HrmApp -SourceDir $install -InstallDir $install -StateDir $state -Port 8080 | Out-Null
    # Остаток прерванного обновления: журнал с фазой отката (блокировка снята).
    Set-HrmUpdateJournal $state "rollback" @{ release_dir = $install; release_sha = $shaB; previous_release_sha = $shaA }
    $releaseB = New-HrmIteration14Dir "release-b-after-rollback"
    New-HrmFakeSnapshot -Root $releaseB -ReleaseSha $shaB
    Update-HrmApp -ReleaseDir $releaseB -InstallDir $install -StateDir $state
    Assert-HrmEqual $shaB ([string](Get-HrmInstallRecord $state).release_sha) "новая попытка не установила новую версию"
    $result = Get-HrmUpdateResult $state
    Assert-HrmEqual "done" ([string]$result.status) "новая попытка завершилась не done"
    Assert-HrmFalse (Test-Path (Get-HrmUpdateJournal $state)) "журнал обновления не очищен"
    Clear-HrmExternalMock
    Clear-HrmHttpMock
}

# --- P2-3: автозапуск --------------------------------------------------------

Test-Case "P2-3: выключенный автозапуск не включается повторно (в т.ч. повторной установкой)" {
    Initialize-HrmTestEngine
    New-HrmMockWorld | Out-Null
    $state = Get-HrmTestStateDir
    $install = Get-HrmTestInstallDir
    $startupDir = Join-Path $state "startup"
    New-Item -ItemType Directory -Path $startupDir -Force | Out-Null
    $env:HRM_AUTOSTART_DIR = $startupDir
    $env:HRM_AUTOSTART_MOCK = "1"
    $trayDir = Join-Path $install "infra\windows"
    New-Item -ItemType Directory -Path $trayDir -Force | Out-Null
    Set-Content -Path (Join-Path $trayDir "hrm-tray.ps1") -Value "# stub" -Encoding ASCII
    $shortcut = Join-Path $startupDir "HR Manager (трей).lnk"
    # Пользователь явно выключил автозапуск в меню значка.
    $null = Disable-HrmAutostart -StateDir $state
    $after = Sync-HrmAutostart -InstallDir $install -StateDir $state
    Assert-HrmFalse (Test-Path $shortcut) "значок трея снова создал ярлык выключенного автозапуска"
    Assert-HrmFalse ([bool]$after.enabled) "выбор пользователя (выключено) перезаписан"
    Assert-HrmTrue ([bool]$after.configured) "состояние автозапуска потеряло признак выбора пользователя"
    # Повторная установка/обновление: мастер снова положил ярлык в автозагрузку.
    Set-Content -Path $shortcut -Value "mock-shortcut: setup" -Encoding UTF8
    $afterSetup = Sync-HrmAutostart -InstallDir $install -StateDir $state
    Assert-HrmFalse (Test-Path $shortcut) "ярлык от мастера установки остался при выключенном автозапуске"
    Assert-HrmFalse ([bool]$afterSetup.enabled) "повторная установка включила выключенный автозапуск"
}

Test-Case "P2-3: первичная настройка и восстановление пропавшего ярлыка" {
    Initialize-HrmTestEngine
    New-HrmMockWorld | Out-Null
    $state = Get-HrmTestStateDir
    $install = Get-HrmTestInstallDir
    $startupDir = Join-Path $state "startup"
    New-Item -ItemType Directory -Path $startupDir -Force | Out-Null
    $env:HRM_AUTOSTART_DIR = $startupDir
    $env:HRM_AUTOSTART_MOCK = "1"
    $trayDir = Join-Path $install "infra\windows"
    New-Item -ItemType Directory -Path $trayDir -Force | Out-Null
    Set-Content -Path (Join-Path $trayDir "hrm-tray.ps1") -Value "# stub" -Encoding ASCII
    $shortcut = Join-Path $startupDir "HR Manager (трей).lnk"
    # Выбора ещё не было: автозапуск включён по умолчанию.
    $fresh = Get-HrmAutostartState -StateDir $state
    Assert-HrmFalse ([bool]$fresh.configured) "до первого выбора состояние уже «настроено»"
    $after = Sync-HrmAutostart -InstallDir $install -StateDir $state
    Assert-HrmTrue (Test-Path $shortcut) "первичная настройка не включила автозапуск"
    Assert-HrmTrue ([bool]$after.enabled) "автозапуск не включён по умолчанию"
    Assert-HrmTrue ([bool]$after.configured) "выбор по умолчанию не зафиксирован"
    # Ярлык пропал (обновление Windows, чистка) — но выбор «включён» сохраняется.
    Remove-Item -Path $shortcut -Force
    $restored = Sync-HrmAutostart -InstallDir $install -StateDir $state
    Assert-HrmTrue (Test-Path $shortcut) "ярлык включённого автозапуска не восстановлен"
    Assert-HrmTrue ([bool]$restored.enabled) "автозапуск выключился сам"
    # Повреждённый файл состояния: выбор не считается записанным, автозапуск не ломается.
    Set-Content -Path (Join-Path $state "autostart.json") -Value "{ это не json" -Encoding UTF8
    $broken = Get-HrmAutostartState -StateDir $state
    Assert-HrmFalse ([bool]$broken.configured) "повреждённый файл состояния считан как выбор пользователя"
    Remove-Item -Path $startupDir -Recurse -Force -ErrorAction SilentlyContinue
}

Test-Case "P2-3: ярлык автозапуска запускает значок в трее и удаляется при выключении" {
    Initialize-HrmTestEngine
    New-HrmMockWorld | Out-Null
    $state = Get-HrmTestStateDir
    $install = Get-HrmTestInstallDir
    $startupDir = Join-Path $state "startup"
    New-Item -ItemType Directory -Path $startupDir -Force | Out-Null
    $env:HRM_AUTOSTART_DIR = $startupDir
    $env:HRM_AUTOSTART_MOCK = "1"
    $trayDir = Join-Path $install "infra\windows"
    New-Item -ItemType Directory -Path $trayDir -Force | Out-Null
    Set-Content -Path (Join-Path $trayDir "hrm-tray.ps1") -Value "# stub" -Encoding ASCII
    Set-Content -Path (Join-Path $startupDir "HR Manager.lnk") -Value "legacy" -Encoding ASCII
    $enabled = Enable-HrmAutostart -InstallDir $install -StateDir $state
    Assert-HrmTrue ([bool]$enabled.enabled) "автозапуск не включился"
    Assert-HrmTrue ([bool]$enabled.configured) "включение не зафиксировано как выбор"
    Assert-HrmFalse (Test-Path (Join-Path $startupDir "HR Manager.lnk")) "устаревший ярлык не удалён"
    $shortcut = Join-Path $startupDir "HR Manager (трей).lnk"
    Assert-HrmTrue (Test-Path $shortcut) "нет ярлыка автозапуска supervisor'а"
    $content = Get-Content -Path $shortcut -Raw -Encoding UTF8
    Assert-HrmContains $content "hrm-tray.ps1" "ярлык запускает не supervisor"
    Assert-HrmNotContains $content "-Action start" "ярлык запускает консольный start"
    $disabled = Disable-HrmAutostart -StateDir $state
    Assert-HrmFalse ([bool]$disabled.enabled) "автозапуск не выключился"
    Assert-HrmTrue ([bool]$disabled.configured) "выключение не зафиксировано"
    Assert-HrmFalse (Test-Path $shortcut) "ярлык не удалён при выключении"
    Remove-Item -Path $startupDir -Recurse -Force -ErrorAction SilentlyContinue
}

# --- P2-4: значок в трее во время установки ---------------------------------

Test-Case "P2-4: план значка — установка идёт, значок не выходит и не мешает мастеру" {
    Initialize-HrmTestEngine
    New-HrmMockWorld | Out-Null
    $plan = Get-HrmTrayStartupPlan -HasRecord $false -MarkerStatus "running" -MarkerFresh $true -MarkerMessage "Устанавливаем HR Manager…"
    Assert-HrmEqual "installing" ([string]$plan.mode) "идущая установка не распознана"
    Assert-HrmEqual "starting" ([string]$plan.state) "нет состояния «запускается» во время установки"
    Assert-HrmTrue ([bool]$plan.busy) "действия не заблокированы во время установки"
    Assert-HrmFalse ([bool]$plan.start_engine) "второй движок запущен во время установки (дубли)"
    Assert-HrmContains ([string]$plan.message) "Устанавливаем HR Manager" "пользователю не сказано, что идёт установка"
    # Обновление поверх установки — тоже «идёт установка».
    $update = Get-HrmTrayStartupPlan -HasRecord $true -MarkerStatus "running" -MarkerFresh $true -MarkerMessage "Обновляем HR Manager…"
    Assert-HrmEqual "installing" ([string]$update.mode) "обновление не распознано"
    Assert-HrmTrue ([bool]$update.busy) "во время обновления действия не заблокированы"
}

Test-Case "P2-4: план значка — установка упала, прервалась или всё готово" {
    Initialize-HrmTestEngine
    New-HrmMockWorld | Out-Null
    # Установка завершилась ошибкой: значок остаётся и показывает причину.
    $failed = Get-HrmTrayStartupPlan -HasRecord $false -MarkerStatus "failed" -MarkerFresh $true -MarkerMessage "Не удалось установить рабочую среду."
    Assert-HrmEqual "install_failed" ([string]$failed.mode) "ошибка установки не распознана"
    Assert-HrmEqual "error" ([string]$failed.state) "нет честного состояния ошибки"
    Assert-HrmFalse ([bool]$failed.busy) "действия заблокированы после ошибки установки"
    Assert-HrmContains ([string]$failed.message) "Не удалось установить" "причина ошибки потеряна"
    # Отметка осталась, установка не идёт и приложения нет.
    $stale = Get-HrmTrayStartupPlan -HasRecord $false -MarkerStatus "running" -MarkerFresh $false -MarkerMessage "Устанавливаем HR Manager…"
    Assert-HrmEqual "install_stale" ([string]$stale.mode) "прерванная установка не распознана"
    Assert-HrmContains ([string]$stale.message) "Setup.exe" "нет понятного действия для пользователя"
    # Готовые файлы установки: продолжает движок, а не человек с Setup.exe.
    $resumeReady = Get-HrmTrayStartupPlan -HasRecord $false -MarkerStatus "running" -MarkerFresh $false -MarkerMessage "Устанавливаем HR Manager…" -InstallDirReady $true
    Assert-HrmEqual "resuming" ([string]$resumeReady.mode) "готовая установка не продолжается автоматически"
    Assert-HrmEqual "resume" ([string]$resumeReady.engine_action) "продолжение установки запускает не resume"
    # Отметка осталась, но приложение установлено: устаревшая отметка не мешает.
    $installed = Get-HrmTrayStartupPlan -HasRecord $true -MarkerStatus "running" -MarkerFresh $false -MarkerMessage "Устанавливаем HR Manager…"
    Assert-HrmEqual "normal" ([string]$installed.mode) "устаревшая отметка мешает обычной работе"
    Assert-HrmTrue ([bool]$installed.start_engine) "движок не запускается в обычном режиме"
    Assert-HrmEqual "supervise" ([string]$installed.engine_action) "обычный режим запускает не supervise"
    # Приложения нет вовсе.
    $missing = Get-HrmTrayStartupPlan -HasRecord $false
    Assert-HrmEqual "not_installed" ([string]$missing.mode) "отсутствие установки не распознано"
    # Незавершённое обновление: значок продолжает операцию (resume).
    $resume = Get-HrmTrayStartupPlan -HasRecord $true -ResumePending $true
    Assert-HrmEqual "resuming" ([string]$resume.mode) "незавершённое обновление не распознано"
    Assert-HrmTrue ([bool]$resume.start_engine) "продолжение обновления не запускается"
    Assert-HrmEqual "resume" ([string]$resume.engine_action) "продолжение запускает не resume"
}

Test-Case "P2-4: план значка по реальным файлам состояния (отметка, запись, журнал обновления)" {
    Initialize-HrmTestEngine
    New-HrmMockWorld | Out-Null
    Set-HrmPreflightOverride @{ windows = $true; powershell = $true; docker = $true; daemon = $true; compose = "v2.29.7 (mock)"; port = $true; state_dir = $true; space = $true; config = $true }
    $state = Get-HrmTestStateDir
    $install = Get-HrmTestInstallDir
    # Мастер установки только что записал отметку, приложения ещё нет.
    Set-HrmSetupMarker -StateDir $state -Status "running" -Message "Устанавливаем HR Manager…" | Out-Null
    $plan = Get-HrmTrayCurrentPlan -StateDir $state -InstallDir $install
    Assert-HrmEqual "installing" ([string]$plan.mode) "свежая отметка установки не распознана"
    $marker = Get-HrmSetupMarker -StateDir $state
    Assert-HrmEqual "running" ([string]$marker.status) "отметка установки не читается"
    Assert-HrmTrue ([bool]$marker.fresh) "свежая отметка считается устаревшей"
    # Готовая установка + устаревшая отметка: движок продолжает первичную установку.
    (Get-Item (Get-HrmSetupMarkerFile $state)).LastWriteTime = (Get-Date).AddHours(-2)
    New-HrmFakeSnapshot -Root $install -ReleaseSha ("7" * 40)
    $resumePlan = Get-HrmTrayCurrentPlan -StateDir $state -InstallDir $install
    Assert-HrmEqual "resuming" ([string]$resumePlan.mode) "прерванная установка не продолжается сама"
    Assert-HrmEqual "resume" ([string]$resumePlan.engine_action) "продолжение запускает не resume"
    # Продолжение действительно доводит первичную установку до записи.
    Resume-HrmOperation -InstallDir $install -StateDir $state
    Assert-HrmEqual ("7" * 40) ([string](Get-HrmInstallRecord $state).release_sha) "первичная установка не доведена до записи"
    Assert-HrmFalse (Test-Path (Get-HrmSetupMarkerFile $state)) "отметка установки не снята после успеха"
    # Установка упала: отметка failed переживает запуск значка.
    Set-HrmSetupMarker -StateDir $state -Status "failed" -Message "Не удалось подготовить рабочую среду." | Out-Null
    $failedPlan = Get-HrmTrayCurrentPlan -StateDir $state -InstallDir $install
    Assert-HrmEqual "install_failed" ([string]$failedPlan.mode) "ошибка установки не видна значку"
    Assert-HrmContains ([string]$failedPlan.message) "Не удалось подготовить" "причина ошибки не передана значку"
    Clear-HrmSetupMarker -StateDir $state
    # Прерванное обновление (журнал с фазой migrate) — значок продолжает работу.
    Set-HrmUpdateJournal $state "migrate" @{ release_dir = $install; release_sha = ("e" * 40) }
    $updatePlan = Get-HrmTrayCurrentPlan -StateDir $state -InstallDir $install
    Assert-HrmEqual "resuming" ([string]$updatePlan.mode) "прерванное обновление не распознано"
    Assert-HrmEqual "resume" ([string]$updatePlan.engine_action) "прерванное обновление не продолжается"
    # Завершённый откат автоповторов не делает: решает владелец.
    Set-HrmUpdateJournal $state "rollback" @{ release_dir = $install; release_sha = ("e" * 40) }
    $afterRollback = Get-HrmTrayCurrentPlan -StateDir $state -InstallDir $install
    Assert-HrmEqual "normal" ([string]$afterRollback.mode) "значок повторяет обновление после отката"
    Assert-HrmFalse ([bool]$afterRollback.busy) "значок занят после отката"
    Clear-HrmExternalMock
    Clear-HrmHttpMock
}

Test-Case "P2-4: статические контракты входных точек и установщика" {
    # Значок: состояние установки, автозапуск и отсутствие старого выхода.
    $trayEntry = Get-Content -Path (Join-Path $script:RepoRoot "infra\windows\hrm-tray.ps1") -Raw -Encoding UTF8
    Assert-HrmContains $trayEntry "Get-HrmTrayCurrentPlan" "значок не учитывает состояние установки"
    Assert-HrmContains $trayEntry "Sync-HrmAutostart" "значок не приводит автозапуск в соответствие с выбором пользователя"
    Assert-HrmNotContains $trayEntry "HR Manager не установлен." "старый выход «HR Manager не установлен.» остался"
    # Движок установки отмечает ход установки и умеет сохранять снимок прежней версии.
    $entry = Get-Content -Path (Join-Path $script:RepoRoot "infra\windows\hr-manager.ps1") -Raw -Encoding UTF8
    Assert-HrmContains $entry "Set-HrmSetupMarker" "движок не отмечает ход установки"
    Assert-HrmContains $entry "Clear-HrmSetupMarker" "движок не снимает отметку после успешной установки"
    Assert-HrmContains $entry "snapshot-previous" "нет действия сохранения прежней версии перед обновлением"
    Assert-HrmContains $entry "Save-HrmInstalledSnapshotForSetup" "снимок прежней версии не сохраняется движком"
    # Установщик: отметка установки и снимок прежней версии ДО перезаписи файлов.
    $iss = Get-Content -Path (Join-Path $script:RepoRoot "installer\installer.iss") -Raw -Encoding UTF8
    Assert-HrmContains $iss "PreservePreviousSnapshot" "мастер не сохраняет прежнюю версию перед обновлением"
    Assert-HrmContains $iss "ssInstall" "снимок прежней версии делается не до копирования файлов"
    Assert-HrmContains $iss "-Action snapshot-previous" "мастер не вызывает сохранение снимка"
    Assert-HrmContains $iss "WriteSetupMarker" "мастер не отмечает начало установки"
    Assert-HrmContains $iss "setup-run.json" "нет файла отметки установки"
    # Откат не объявляет успех без проверки готовности.
    $update = Get-Content -Path (Join-Path $script:RepoRoot "infra\windows\engine\Update.psm1") -Raw -Encoding UTF8
    Assert-HrmContains $update "Restore-HrmPreviousVersion" "нет согласованного восстановления прежней версии"
    Assert-HrmContains $update "recovery_confirmed" "результат обновления не различает подтверждённое восстановление"
    Assert-HrmContains $update "needs_backup_restore" "нет признака «нужна проверенная резервная копия»"
    Assert-HrmContains $update "Get-HrmRollbackPlan" "нет документированной политики отката"
    Assert-HrmNotContains $update 'Write-HrmLog "info" "Предыдущая версия восстановлена."' "осталось безусловное «прежняя версия восстановлена»"
    # Копирование снимка: границы путей и обязательная проверка полноты.
    $installModule = Get-Content -Path (Join-Path $script:RepoRoot "infra\windows\engine\Install.psm1") -Raw -Encoding UTF8
    Assert-HrmContains $installModule "Test-HrmSamePath" "нет защиты от совпадения источника и назначения"
    Assert-HrmContains $installModule "Test-HrmPathInside" "нет защиты от вложенных путей"
    Assert-HrmContains $installModule "Assert-HrmSnapshotComplete" "нет проверки полноты снимка"
    Assert-HrmContains $installModule "Resume-HrmOperation" "нет продолжения прерванной установки"
    # Канал не отправляет серверу «откат выполнен» без подтверждения движка.
    $channel = Get-Content -Path (Join-Path $script:RepoRoot "infra\windows\engine\Channel.psm1") -Raw -Encoding UTF8
    Assert-HrmContains $channel "Get-HrmUpdateResult" "отчёт канала не учитывает итог движка"
    Assert-HrmContains $channel 'if ($engineStatus -and $engineStatus -ne "rolled_back")' "отчёт канала не различает неподтверждённый откат"
    Assert-HrmContains $channel '$resultState = "failed"' "канал не сообщает failed вместо ложного rolled_back"
}

# --- Документация политики отката -------------------------------------------

Test-Case "P1-2: политика отката описана в руководстве по обновлению" {
    $guide = Get-Content -Path (Join-Path $script:RepoRoot "docs\UPDATE_GUIDE.md") -Raw -Encoding UTF8
    Assert-HrmContains $guide "Если обновление не удалось" "нет раздела о неудачном обновлении"
    Assert-HrmContains $guide "проверенн" "не сказано про проверенную резервную копию"
    Assert-HrmContains $guide "Схема базы данных" "не сказано, что схема БД не откатывается"
    Assert-HrmContains $guide "previous-snapshot" "не описано, где хранится снимок прежней версии"
    Assert-HrmContains $guide "rollback_failed" "не описан статус неподтверждённого восстановления"
    Assert-HrmContains $guide "release-staging" "не описана копия релиза для продолжения после перезагрузки"
}

Write-Host "== Итерация 14: проверки завершены =="
