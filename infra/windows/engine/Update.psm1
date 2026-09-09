# Обновление из доверенного каталога релиза.
#
# ДОВЕРЕННАЯ ГРАНИЦА: -ReleaseDir — это каталог, куда пользователь (или
# официальный автозагрузчик) положил подписанный/проверенный снимок релиза
# (infra/, backend/, frontend/, release.json с release_sha). Всё, что лежит
# в этом каталоге, считается доверенным кодом. См. README.
#
# Порядок (журнал фаз в update-journal.json, возобновляемость после сбоя):
#   prepare → backup → build → switch → migrate → smoke → done | rollback
#   - валидный шифрованный бэкап + проверка целостности ДО миграции
#     (backup-now + backup-check --deep; ошибка останавливает обновление);
#   - новые образы собираются, НЕ разрушая работающие контейнеры;
#   - однократный `alembic upgrade head`;
#   - ограниченная готовность + smoke: фронтенд, /api/health, /api/ops/status
#     (release_sha, миграции), worker-check, отсутствие дрейфа миграций;
#   - при провале — возврат к ПРЕДЫДУЩИМ образам (перетегирование);
#     даунгрейд БД НЕ выполняется никогда;
#   - предыдущая рабочая версия остаётся активной, пока smoke не прошёл.

Set-StrictMode -Version 2.0

$script:UpdatePhases = @("prepare", "backup", "build", "switch", "migrate", "smoke", "done", "rollback")

# Голова миграций Alembic, которую должен показывать работающий бэкенд
# после обновления (проверка отсутствия дрейфа в smoke). Обновляется при
# добавлении новых ревизий схемы.
$script:ExpectedHeadRevision = "0013"

function Get-HrmImageIds {
    # Текущие ID образов пилота (для возврата).
    param()
    $ids = @{}
    foreach ($image in @("hr-manager-pilot-backend:pilot", "hr-manager-pilot-frontend:pilot", "hr-manager-pilot-backup:pilot")) {
        $inspect = Invoke-HrmDocker @("image", "inspect", "-f", "{{.Id}}", $image) -IgnoreExitCode
        if ($inspect.ExitCode -eq 0 -and $inspect.Stdout) {
            $ids[$image] = $inspect.Stdout.Trim()
        }
    }
    return $ids
}

function Set-HrmUpdateJournal {
    # Windows PowerShell 5.1 не умеет Add-Member на Hashtable — пересборка.
    param([string]$StateDir, [string]$Phase, [hashtable]$Extra = @{})
    $journal = Get-HrmUpdateJournal $StateDir
    $data = Get-HrmJsonFile $journal
    $merged = [ordered]@{}
    if ($null -ne $data) {
        foreach ($prop in $data.PSObject.Properties) { $merged[$prop.Name] = $prop.Value }
    }
    $merged["phase"] = $Phase
    $merged["updated_at"] = (Get-Date).ToString("o")
    foreach ($key in $Extra.Keys) { $merged[$key] = $Extra[$key] }
    Set-HrmJsonFile $StateDir "update-journal.json" $merged
}

function Clear-HrmUpdateJournal {
    param([string]$StateDir)
    $journal = Get-HrmUpdateJournal $StateDir
    if (Test-Path $journal) { Remove-Item $journal -Force }
    $lock = Get-HrmUpdateLock $StateDir
    if (Test-Path $lock) { Remove-Item $lock -Force }
}

function Test-HrmUpdateLockAvailable {
    # Блокировка обновления: свежая (< 4 часов) — обновление уже идёт;
    # старая — прерванное обновление, разрешаем перехват.
    param([string]$StateDir)
    $lock = Get-HrmUpdateLock $StateDir
    if (-not (Test-Path $lock)) { return $true }
    $age = (Get-Date) - (Get-Item $lock).LastWriteTime
    return ($age.TotalHours -ge 4)
}

function Invoke-HrmBackupGate {
    # Валидный шифрованный бэкап + проверка целостности перед миграцией.
    param([string]$InstallDir, [string]$StateDir)
    Write-HrmLog "info" "Бэкап перед обновлением…"
    Invoke-HrmCompose $InstallDir $StateDir @("run", "--rm", "backup", "python", "-m", "app.cli", "backup-now", "--as-scheduler", "--reason", "pre-update backup") | Out-Null
    Write-HrmLog "info" "Проверка целостности бэкапа (--deep)…"
    $check = Invoke-HrmCompose $InstallDir $StateDir @("run", "--rm", "backup", "python", "-m", "app.cli", "backup-check", "--deep", "--as-scheduler") -IgnoreExitCode
    if ($check.ExitCode -ne 0) {
        throw "Бэкап не прошёл проверку целостности — обновление остановлено (данные не тронуты)."
    }
    Write-HrmLog "info" "Бэкап валиден."
}

function Invoke-HrmRollbackImages {
    # Возврат к предыдущим образам; БД не даунгрейдится.
    param([hashtable]$PreviousIds)
    foreach ($image in $PreviousIds.Keys) {
        Invoke-HrmDocker @("tag", $PreviousIds[$image], $image) | Out-Null
    }
    Write-HrmLog "info" "Предыдущие образы восстановлены по тегам."
}

function Update-HrmApp {
    param(
        [string]$ReleaseDir = "",
        [string]$InstallDir = "",
        [string]$StateDir = ""
    )
    if (-not $ReleaseDir) { throw "Укажите -ReleaseDir (доверенный каталог релиза)." }
    if (-not (Test-Path (Join-Path $ReleaseDir "infra\compose.pilot.yml"))) {
        throw "Каталог релиза не содержит infra\compose.pilot.yml: $ReleaseDir"
    }
    $releaseData = Get-HrmJsonFile (Join-Path $ReleaseDir "release.json")
    if ($null -eq $releaseData -or -not $releaseData.release_sha) {
        throw "В каталоге релиза нет release.json с release_sha."
    }
    if (-not $InstallDir) { $InstallDir = Get-HrmDefaultInstallDir }
    if (-not $StateDir) { $StateDir = Get-HrmStateDir }
    $record = Get-HrmInstallRecord $StateDir
    if ($null -eq $record) { throw "Установка не найдена. Выполните -Action install." }
    $port = [int]$record.port
    $baseUrl = Get-HrmBaseUrl $port

    if (-not (Test-HrmUpdateLockAvailable $StateDir)) {
        throw "Обновление уже выполняется (блокировка update.lock). Подождите или запустите -Action resume."
    }
    Set-Content -Path (Get-HrmUpdateLock $StateDir) -Value ("locked at " + (Get-Date).ToString("o")) -Encoding UTF8

    $journal = Get-HrmUpdateJournal $StateDir
    $resume = (Test-Path $journal)
    $phase = "prepare"
    if ($resume) {
        $data = Get-HrmJsonFile $journal
        $phase = [string]$data.phase
        Write-HrmLog "info" ("Возобновление обновления с фазы {0}." -f $phase)
    }

    # Возобновление: нужные состояния восстанавливаются из журнала.
    $previousIds = @{}
    if ($resume -and $data.previous_ids) {
        foreach ($pair in $data.previous_ids.PSObject.Properties) {
            $previousIds[$pair.Name] = [string]$pair.Value
        }
    }

    try {
        if (-not $resume -or $phase -eq "prepare") {
            $previousIds = Get-HrmImageIds
            Set-HrmUpdateJournal $StateDir "prepare" @{ release_dir = $ReleaseDir; previous_ids = $previousIds; release_sha = $releaseData.release_sha }
            $phase = "backup"
        }

        if ($phase -eq "backup") {
            Invoke-HrmBackupGate $InstallDir $StateDir
            Set-HrmUpdateJournal $StateDir "build" @{ release_dir = $ReleaseDir; previous_ids = $previousIds; release_sha = $releaseData.release_sha }
            $phase = "build"
        }

        if ($phase -eq "build") {
            Write-HrmLog "info" "Сборка новых образов (работающее приложение не останавливается)…"
            # Сборка идёт по временному compose-файлу обновляемого снимка.
            $newCompose = Join-Path $ReleaseDir "infra\compose.pilot.yml"
            $envFile = Get-HrmEnvFile $StateDir
            Invoke-HrmDocker @("compose", "--project-name", "hr-manager-pilot", "--env-file", $envFile, "-f", $newCompose, "build", "--pull=false") | Out-Null
            Set-HrmUpdateJournal $StateDir "switch" @{ release_dir = $ReleaseDir; previous_ids = $previousIds; release_sha = $releaseData.release_sha }
            $phase = "switch"
        }

        if ($phase -eq "switch") {
            Write-HrmLog "info" "Замена файлов снимка и пересоздание контейнеров…"
            Copy-HrmSnapshot $ReleaseDir $InstallDir
            $null = Write-HrmPilotEnv $StateDir $releaseData.release_sha $port
            Invoke-HrmCompose $InstallDir $StateDir @("up", "-d", "--remove-orphans") | Out-Null
            Set-HrmUpdateJournal $StateDir "migrate" @{ release_dir = $ReleaseDir; previous_ids = $previousIds; release_sha = $releaseData.release_sha }
            $phase = "migrate"
        }

        if ($phase -eq "migrate") {
            Write-HrmLog "info" "Однократная миграция схемы (alembic upgrade head)…"
            $migrate = Invoke-HrmCompose $InstallDir $StateDir @("exec", "-T", "backend", "alembic", "upgrade", "head")
            Write-HrmLog "info" (($migrate.Stdout -split "`n" | Select-Object -Last 3) -join " ")
            Set-HrmUpdateJournal $StateDir "smoke" @{ release_dir = $ReleaseDir; previous_ids = $previousIds; release_sha = $releaseData.release_sha }
            $phase = "smoke"
        }

        if ($phase -eq "smoke") {
            Wait-HrmReady $baseUrl 240
            $ops = Get-HrmOpsStatus $baseUrl
            if ($null -eq $ops) { throw "Smoke: /api/ops/status недоступен." }
            $sha = [string]$ops.release_sha
            if ($releaseData.release_sha -and $sha -and $sha -ne $releaseData.release_sha) {
                throw ("Smoke: несовпадение версии в работе ({0}) и релиза ({1})." -f $sha, $releaseData.release_sha)
            }
            $current = Get-HrmMigrationsState $InstallDir $StateDir
            if ($null -eq $current) { throw "Smoke: не удалось прочитать текущую ревизию миграций." }
            if ($current -ne $script:ExpectedHeadRevision) {
                throw ("Smoke: дрейф миграций — в базе {0}, ожидается {1}." -f $current, $script:ExpectedHeadRevision)
            }
            $worker = Invoke-HrmCompose $InstallDir $StateDir @("exec", "-T", "worker", "python", "-m", "app.cli", "worker-check") -IgnoreExitCode
            if ($worker.ExitCode -ne 0) { throw "Smoke: worker-check не прошёл." }
            Set-HrmInstallRecord $StateDir @{ release_sha = $releaseData.release_sha; updated_at = (Get-Date).ToString("o") }
            Set-HrmUpdateJournal $StateDir "done" @{ release_dir = $ReleaseDir; previous_ids = $previousIds; release_sha = $releaseData.release_sha }
            Clear-HrmUpdateJournal $StateDir
            Write-HrmLog "info" ("Обновление завершено: {0}" -f $releaseData.release_sha)
            return
        }

        if ($phase -eq "done") {
            Clear-HrmUpdateJournal $StateDir
            Write-HrmLog "info" "Обновление уже завершено."
            return
        }
    }
    catch {
        Write-HrmLog "error" ("Обновление не удалось: {0}" -f (Redact-HrmText $_.Exception.Message))
        if ($previousIds.Count -gt 0) {
            Write-HrmLog "info" "Возврат к предыдущей рабочей версии (без даунгрейда БД)…"
            Invoke-HrmRollbackImages $previousIds
            Invoke-HrmCompose $InstallDir $StateDir @("up", "-d", "--remove-orphans") | Out-Null
            Write-HrmLog "info" "Предыдущая версия восстановлена."
        }
        else {
            Set-HrmUpdateJournal $StateDir "rollback" @{ release_dir = $ReleaseDir; release_sha = $releaseData.release_sha }
        }
        throw
    }
}
