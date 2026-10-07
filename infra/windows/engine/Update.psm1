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

function Get-HrmImageIds {
    # Закрепляем текущие образы отдельными тегами ДО сборки. BuildKit может
    # удалить прежний нетегированный image ID, когда частично перезаписывает
    # :pilot во время неудачной multi-service сборки. Стабильный :previous
    # остаётся доступен для rollback даже в этом случае.
    param()
    $ids = @{}
    foreach ($image in @("hr-manager-pilot-backend:pilot", "hr-manager-pilot-frontend:pilot", "hr-manager-pilot-backup:pilot")) {
        $inspect = Invoke-HrmDocker @("image", "inspect", "-f", "{{.Id}}", $image) -IgnoreExitCode
        if ($inspect.ExitCode -eq 0 -and $inspect.Stdout) {
            $previous = $image -replace ':pilot$', ':previous'
            Invoke-HrmDocker @("tag", $inspect.Stdout.Trim(), $previous) | Out-Null
            $ids[$image] = $previous
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
    # The backup image has `backup-scheduler` as ENTRYPOINT. Use its public
    # operations instead of appending a second executable to that entrypoint.
    Invoke-HrmCompose $InstallDir $StateDir @("run", "--rm", "-e", "BACKUP_REASON=pre-update backup", "backup", "oneshot") | Out-Null
    Write-HrmLog "info" "Проверка целостности бэкапа (--deep)…"
    $check = Invoke-HrmCompose $InstallDir $StateDir @("run", "--rm", "backup", "check") -IgnoreExitCode
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

function Get-HrmInstalledVersionInfo {
    # Версия и sha УСТАНОВЛЕННОГО снимка (release.json и запись установки).
    param([string]$InstallDir, [string]$StateDir)
    $version = ""
    $sha = ""
    $releaseFile = Join-Path $InstallDir "release.json"
    if (Test-Path $releaseFile) {
        $data = Get-HrmJsonFile $releaseFile
        if ($null -ne $data) {
            if ($data.PSObject.Properties["version"]) { $version = [string]$data.version }
            if ($data.PSObject.Properties["release_sha"]) { $sha = [string]$data.release_sha }
        }
    }
    $record = Get-HrmInstallRecord $StateDir
    if ($null -ne $record -and $record.PSObject.Properties["release_sha"] -and $record.release_sha) { $sha = [string]$record.release_sha }
    if ($null -ne $record -and $record.PSObject.Properties["version"] -and $record.version) { $version = [string]$record.version }
    return [pscustomobject]@{ version = $version; release_sha = $sha }
}

function Get-HrmReleaseChangelog {
    # Список изменений релиза: release.json (changelog[]) или CHANGELOG.md рядом.
    param([string]$ReleaseDir)
    $entries = @()
    $releaseFile = Join-Path $ReleaseDir "release.json"
    if (Test-Path $releaseFile) {
        $data = Get-HrmJsonFile $releaseFile
        if ($null -ne $data -and $data.PSObject.Properties["changelog"] -and $data.changelog) {
            foreach ($item in @($data.changelog)) { if ($item) { $entries += [string]$item } }
        }
    }
    if ($entries.Count -eq 0) {
        $md = Join-Path $ReleaseDir "CHANGELOG.md"
        if (Test-Path $md) {
            foreach ($line in (Get-Content -Path $md -Encoding UTF8)) {
                $trimmed = $line.Trim()
                if ($trimmed -match "^[-*]\s+(.+)$") { $entries += $Matches[1] }
                if ($entries.Count -ge 10) { break }
            }
        }
    }
    if ($entries.Count -eq 0) { $entries += "Улучшения установки, запуска и диагностики." }
    return $entries
}

function Get-HrmUpdatePreview {
    # Что показывается пользователю ДО обновления: текущая и новая версия,
    # список изменений, предупреждение о сохранении данных и проверки
    # (место, лицензия, настройки, порт, доступ по сети, готовность Docker).
    param([string]$ReleaseDir, [string]$InstallDir = "", [string]$StateDir = "")
    if (-not $InstallDir) { $InstallDir = Get-HrmInstallDefaultDirSafe }
    if (-not $StateDir) { $StateDir = Get-HrmStateDir }
    $current = Get-HrmInstalledVersionInfo -InstallDir $InstallDir -StateDir $StateDir
    $newVersion = ""
    $newSha = ""
    $releaseFile = Join-Path $ReleaseDir "release.json"
    if (Test-Path $releaseFile) {
        $data = Get-HrmJsonFile $releaseFile
        if ($null -ne $data) {
            if ($data.PSObject.Properties["version"]) { $newVersion = [string]$data.version }
            if ($data.PSObject.Properties["release_sha"]) { $newSha = [string]$data.release_sha }
        }
    }
    $record = Get-HrmInstallRecord $StateDir
    $port = Get-HrmPort
    if ($null -ne $record -and $record.PSObject.Properties["port"] -and $record.port) { $port = [int]$record.port }
    $lan = Get-HrmLanConfig $StateDir
    $licenseKey = Get-HrmLicensePublicKey $StateDir

    $checks = @()
    $freeMb = Get-HrmFreeSpaceMb $StateDir
    $freeOk = ($freeMb -lt 0) -or ($freeMb -ge 5120)
    $checks += [pscustomobject]@{
        key = "space"; label = "Свободное место"
        status = if ($freeOk) { "ok" } else { "fail" }
        detail = if ($freeMb -lt 0) { "Не удалось определить свободное место." } else { ("Свободно {0} МБ." -f $freeMb) }
    }
    $checks += [pscustomobject]@{
        key = "license"; label = "Лицензия и ключ проверки"
        status = if ($licenseKey) { "ok" } else { "warn" }
        detail = if ($licenseKey) { "Ключ проверки лицензии на месте; сама лицензия хранится в базе и сохраняется." } else { "Ключ проверки лицензии не найден — приложение не сможет проверить лицензию." }
    }
    $checks += [pscustomobject]@{
        key = "settings"; label = "Настройки и пользователи"
        status = "ok"
        detail = "Пользователи, настройки, вложения и лицензия хранятся в базе данных (том pilot_pgdata) и сохраняются."
    }
    $checks += [pscustomobject]@{
        key = "port"; label = "Порт и доступ по сети"
        status = "ok"
        detail = ("Порт {0} и доступ по сети ({1}) сохраняются без изменений." -f $port, $(if ($lan.enabled) { "включён" } else { "только этот компьютер" }))
    }
    $checks += [pscustomobject]@{
        key = "docker"; label = "Готовность рабочей среды"
        status = if (Test-HrmDockerEngineReady) { "ok" } else { "fail" }
        detail = if (Test-HrmDockerEngineReady) { "Docker Engine работает." } else { "Docker Engine не отвечает — обновление начнётся после его запуска." }
    }
    $backupVolume = Invoke-HrmDocker -Arguments @("volume", "inspect", ((Get-HrmProjectName) + "_pilot_backups")) -IgnoreExitCode
    $checks += [pscustomobject]@{
        key = "backup_volume"; label = "Хранилище резервных копий"
        status = if ($backupVolume.ExitCode -eq 0) { "ok" } else { "warn" }
        detail = if ($backupVolume.ExitCode -eq 0) { "Том резервных копий на месте — перед обновлением будет создан свежий бэкап." } else { "Том резервных копий не найден до первой установки — бэкап будет создан при обновлении." }
    }
    $canUpdate = (@($checks | Where-Object { $_.status -eq "fail" }).Count -eq 0)
    return [pscustomobject]@{
        current_version = $current.version
        current_sha = $current.release_sha
        new_version = $newVersion
        new_sha = $newSha
        same_version = ($current.release_sha -ne "" -and $current.release_sha -eq $newSha)
        changelog = (Get-HrmReleaseChangelog $ReleaseDir)
        data_notice = "Перед обновлением автоматически создаётся резервная копия. Пользователи, настройки, вложения и лицензия сохраняются."
        checks = $checks
        can_update = $canUpdate
        port = $port
        lan_enabled = [bool]$lan.enabled
    }
}

function Format-HrmUpdatePreview {
    # Человеческие строки для мастера/консоли.
    param($Preview)
    $lines = @()
    $from = if ($Preview.current_version) { $Preview.current_version } else { "неизвестна" }
    $to = if ($Preview.new_version) { $Preview.new_version } else { "новая сборка" }
    $lines += ("Обновление HR Manager: {0} → {1}" -f $from, $to)
    $lines += $Preview.data_notice
    $lines += "Что нового:"
    foreach ($item in $Preview.changelog) { $lines += ("  • " + $item) }
    foreach ($check in $Preview.checks) {
        $mark = if ($check.status -eq "ok") { "[OK]" } elseif ($check.status -eq "warn") { "[ВНИМАНИЕ]" } else { "[НУЖНО ДЕЙСТВИЕ]" }
        $lines += ("{0} {1}: {2}" -f $mark, $check.label, $check.detail)
    }
    return $lines
}

function Write-HrmUpdateResult {
    # Результат обновления для трея/мастера/диагностики: «Обновление завершено»
    # или понятное сообщение об ошибке и откате. Секретов здесь нет.
    param(
        [string]$StateDir,
        [string]$Status,
        [string]$Message,
        [string]$FromVersion = "",
        [string]$ToVersion = "",
        [string]$ReleaseSha = "",
        [switch]$RolledBack
    )
    $data = [ordered]@{
        status = $Status
        message = (Redact-HrmText $Message)
        from_version = $FromVersion
        to_version = $ToVersion
        release_sha = $ReleaseSha
        rolled_back = [bool]$RolledBack
        finished_at = (Get-Date).ToString("o")
    }
    Set-HrmJsonFile $StateDir "update-result.json" $data
    return $data
}

function Get-HrmUpdateResult {
    param([string]$StateDir)
    if (-not $StateDir) { $StateDir = Get-HrmStateDir }
    $file = Join-Path $StateDir "update-result.json"
    if (-not (Test-Path $file)) { return $null }
    try { return (Get-HrmJsonFile $file) } catch { return $null }
}

function Assert-HrmUpdatePreservedState {
    # После замены файлов проверяем, что пользовательское состояние НЕ потеряно:
    # ключ лицензии, секреты, порт, LAN-настройка и тома данных.
    param([string]$InstallDir, [string]$StateDir)
    $problems = @()
    $licenseKey = Get-HrmLicensePublicKey $StateDir
    if (-not $licenseKey) { $problems += "ключ проверки лицензии" }
    if (-not (Test-Path (Get-HrmSecretsFile $StateDir))) { $problems += "секреты установки" }
    $record = Get-HrmInstallRecord $StateDir
    if ($null -eq $record) { $problems += "запись установки" }
    if (Test-Path (Join-Path $StateDir "lan.json")) {
        $lan = Get-HrmLanConfig $StateDir
        if ($lan.enabled -and $lan.bind -ne "0.0.0.0") { $problems += "настройка доступа по сети" }
    }
    $volume = Invoke-HrmDocker -Arguments @("volume", "inspect", ((Get-HrmProjectName) + "_pilot_pgdata")) -IgnoreExitCode
    if ($volume.ExitCode -ne 0) { $problems += "том данных PostgreSQL" }
    if ($problems.Count -gt 0) {
        throw ("После обновления не найдено: {0}. Обновление остановлено." -f ($problems -join ", "))
    }
    return $true
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
    # Версия «до обновления» читается ДО подмены файлов снимка: в записи
    # установки её может не быть (старые релизы), тогда берём release.json
    # установленного снимка. Нужна для отчёта «текущая → новая» и трея.
    $previousVersion = ""
    if ($record.PSObject.Properties["version"] -and $record.version) { $previousVersion = [string]$record.version }
    if (-not $previousVersion) { $previousVersion = [string](Get-HrmInstalledVersionInfo -InstallDir $InstallDir -StateDir $StateDir).version }

    # Re-render the protected env before the backup gate. Besides keeping the
    # current release/port authoritative, this upgrades legacy phase-12
    # backup-key encoding before the backup container reads it.
    $null = Write-HrmPilotEnv $StateDir ([string]$record.release_sha) $port

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
            # Сборка идёт по базовому compose-файлу и пилотному overlay из
            # обновляемого снимка. Overlay намеренно не является автономным.
            $newBaseCompose = Join-Path $ReleaseDir "infra\docker-compose.yml"
            $newPilotCompose = Join-Path $ReleaseDir "infra\compose.pilot.yml"
            $envFile = Get-HrmEnvFile $StateDir
            Invoke-HrmDocker @("compose", "--project-name", "hr-manager-pilot", "--env-file", $envFile, "-f", $newBaseCompose, "-f", $newPilotCompose, "build", "--pull=false") | Out-Null
            Set-HrmUpdateJournal $StateDir "switch" @{ release_dir = $ReleaseDir; previous_ids = $previousIds; release_sha = $releaseData.release_sha }
            $phase = "switch"
        }

        if ($phase -eq "switch") {
            Write-HrmLog "info" "Замена файлов снимка и пересоздание контейнеров…"
            $samePath = $false
            try {
                $fullRelease = [System.IO.Path]::GetFullPath($ReleaseDir).TrimEnd('\','/')
                $fullInstall = [System.IO.Path]::GetFullPath($InstallDir).TrimEnd('\','/')
                $samePath = ($fullRelease -eq $fullInstall)
            } catch {}
            if (-not $samePath) {
                Copy-HrmSnapshot $ReleaseDir $InstallDir
            } else {
                Write-HrmLog "info" "Каталог релиза совпадает с установкой — копирование пропущено."
            }
            # Публичный ключ проверки лицензии уже установленного пилота лежит в
            # StateDir и здесь не перезаписывается. Если файла нет (старая
            # установка или ручная чистка), берём ключ из обновляемого снимка:
            # без него приложение не проверит лицензию, а pilot.env с
            # обязательной переменной (:?) не даст стеку подняться.
            $null = Install-HrmLicensePublicKey -SourceDir $ReleaseDir -InstallDir $InstallDir -StateDir $StateDir
            $null = Write-HrmPilotEnv $StateDir $releaseData.release_sha $port
            Invoke-HrmCompose $InstallDir $StateDir @("up", "-d", "--remove-orphans") | Out-Null
            Set-HrmUpdateJournal $StateDir "migrate" @{ release_dir = $ReleaseDir; previous_ids = $previousIds; release_sha = $releaseData.release_sha }
            $phase = "migrate"
        }

        if ($phase -eq "migrate") {
            Write-HrmLog "info" "Однократная миграция схемы (alembic upgrade head)…"
            # A resumed operation may enter directly at migrate after the
            # switch was persisted. Reassert the release env and running
            # containers before migration so smoke observes the target SHA.
            $null = Write-HrmPilotEnv $StateDir $releaseData.release_sha $port
            Invoke-HrmCompose $InstallDir $StateDir @("up", "-d", "--remove-orphans") | Out-Null
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
            # Дрейф миграций: сначала серверный вердикт (/api/ops/status уже
            # знает ожидаемую голову релиза), затем сверка с головой РАЗВЁРНУТОГО
            # образа backend. Зашитой константы головы нет: она расходилась с
            # релизом и давала ложный откат.
            $migrationOk = $true
            $current = Get-HrmMigrationsState $InstallDir $StateDir
            if ($ops.PSObject.Properties["migrations"] -and $null -ne $ops.migrations) {
                if ($ops.migrations.ok -eq $false) {
                    $migrationOk = $false
                    throw ("Smoke: дрейф миграций — в базе {0}, ожидается {1}." -f $ops.migrations.current_revision, $ops.migrations.expected_revision)
                }
            }
            if ($migrationOk -and $null -ne $current) {
                $expected = Get-HrmMigrationsHead $InstallDir $StateDir
                if ($null -ne $expected -and $current -ne $expected) {
                    throw ("Smoke: дрейф миграций — в базе {0}, ожидается {1}." -f $current, $expected)
                }
            }
            if ($migrationOk -and $null -eq $current -and $null -eq $ops.migrations) {
                throw "Smoke: не удалось прочитать состояние миграций."
            }
            # Пользовательское состояние должно остаться на месте (лицензия,
            # секреты, порт, LAN, том данных).
            Assert-HrmUpdatePreservedState -InstallDir $InstallDir -StateDir $StateDir | Out-Null
            $worker = Invoke-HrmCompose $InstallDir $StateDir @("exec", "-T", "worker", "python", "-m", "app.cli", "worker-check") -IgnoreExitCode
            if ($worker.ExitCode -ne 0) { throw "Smoke: worker-check не прошёл." }
            $installedVersion = ""
            if ($releaseData.PSObject.Properties["version"] -and $releaseData.version) { $installedVersion = [string]$releaseData.version }
            Set-HrmInstallRecord $StateDir @{ release_sha = $releaseData.release_sha; updated_at = (Get-Date).ToString("o"); version = $installedVersion }
            Set-HrmUpdateJournal $StateDir "done" @{ release_dir = $ReleaseDir; previous_ids = $previousIds; release_sha = $releaseData.release_sha }
            Clear-HrmUpdateJournal $StateDir
            $null = Write-HrmUpdateResult -StateDir $StateDir -Status "done" -Message "Обновление завершено." -FromVersion $previousVersion -ToVersion $installedVersion -ReleaseSha $releaseData.release_sha
            Write-HrmLog "info" "Обновление завершено."
            return
        }

        if ($phase -eq "done") {
            Clear-HrmUpdateJournal $StateDir
            Write-HrmLog "info" "Обновление уже завершено."
            return
        }
    }
    catch {
        $failureMessage = Redact-HrmText $_.Exception.Message
        Write-HrmLog "error" ("Обновление не удалось: {0}" -f $failureMessage)
        if ($previousIds.Count -gt 0) {
            Write-HrmLog "info" "Возврат к предыдущей рабочей версии (без даунгрейда БД)…"
            Invoke-HrmRollbackImages $previousIds
            Invoke-HrmCompose $InstallDir $StateDir @("up", "-d", "--remove-orphans") | Out-Null
            # Keep the journal for diagnostics/resume, but release the lock:
            # rollback has completed and a corrected release may be retried.
            $lock = Get-HrmUpdateLock $StateDir
            if (Test-Path $lock) { Remove-Item $lock -Force }
            Write-HrmLog "info" "Предыдущая версия восстановлена."
            $null = Write-HrmUpdateResult -StateDir $StateDir -Status "rolled_back" -Message ("Обновление не удалось, восстановлена прежняя версия. Причина: {0}" -f $failureMessage) -FromVersion $previousVersion -ToVersion $previousVersion -RolledBack
        }
        else {
            Set-HrmUpdateJournal $StateDir "rollback" @{ release_dir = $ReleaseDir; release_sha = $releaseData.release_sha }
            $null = Write-HrmUpdateResult -StateDir $StateDir -Status "failed" -Message ("Обновление не удалось: {0}" -f $failureMessage) -FromVersion $previousVersion -ToVersion ([string]$releaseData.version)
        }
        throw
    }
}
