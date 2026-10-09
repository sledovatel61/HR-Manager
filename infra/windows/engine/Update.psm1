# Обновление из доверенного каталога релиза.
#
# ДОВЕРЕННАЯ ГРАНИЦА: -ReleaseDir — это каталог, куда пользователь (или
# официальный автозагрузчик) положил подписанный/проверенный снимок релиза
# (infra/, backend/, frontend/, release.json с release_sha). Всё, что лежит
# в этом каталоге, считается доверенным кодом. См. README.
#
# Порядок (журнал фаз в update-journal.json, возобновляемость после сбоя):
#   prepare → backup → build → switch → migrate → smoke → done | rollback
#   - prepare: ДО любого изменения установленной версии сохраняется снимок
#     ПРЕЖНЕЙ версии (StateDir\previous-snapshot), прежние образы закрепляются
#     тегами :previous, в журнал пишутся идентичность релиза (release_sha,
#     version), ревизия схемы БД и копия НОВОГО релиза под управлением движка
#     (StateDir\release-staging). Копия нужна, чтобы фазы оставались
#     повторяемыми, даже если исходный каталог релиза исчез: Inno Setup удаляет
#     свой временный каталог {tmp} при выходе мастера;
#   - валидный шифрованный бэкап + проверка целостности ДО миграции
#     (backup-now + backup-check --deep; ошибка останавливает обновление);
#   - новые образы собираются, НЕ разрушая работающие контейнеры;
#   - фаза switch заменяет файлы снимка только после успешного backup gate;
#   - однократный `alembic upgrade head`; в журнале migration_done пишется по
#     факту успешного применения;
#   - ограниченная готовность + smoke: фронтенд, /api/health, /api/ops/status
#     (release_sha, миграции), worker-check, отсутствие дрейфа миграций;
#   - при провале решение принимает Get-HrmRollbackPlan:
#       * сбой до фазы switch → установленная версия не менялась, восстанавливать
#         нечего (возвращаются только теги образов), статус — failed;
#       * сбой до применения миграций → согласованный откат (файлы из
#         previous-snapshot + образы + release_sha в окружении) и ПРОВЕРКА
#         готовности прежней версии;
#       * миграция уже применена → тот же согласованный откат, но статус
#         «восстановлено» ставится только если прежняя версия ответила и в работе
#         НЕ сбойный релиз; иначе — rollback_failed, needs_backup_restore=true и
#         отсылка к проверенной резервной копии из фазы backup;
#       * состояние схемы или снимок прежней версии проверить не удалось →
#         безопасного автоматического восстановления нет, статус об этом и говорит.
#     Статус «восстановлено» пишется ТОЛЬКО после подтверждённой готовности:
#     ложное «Предыдущая версия восстановлена» — дефект, который здесь закрыт.
#     Даунгрейд БД не выполняется никогда; журнал и бэкапы при неудаче остаются.
#   - предыдущая рабочая версия остаётся активной, пока smoke не прошёл.
#   - resume: незавершённая попытка продолжается с записанной фазы; попытка,
#     завершённая откатом (блокировка снята), начинается заново, а не повторяет
#     откат.

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
    # Готовность именно Linux-движка: стек HR Manager — Linux-контейнеры.
    # Без него обновление ОСТАНАВЛИВАЕТСЯ на Docker-гейте (см. Update-HrmApp),
    # поэтому текст обязан это отражать, а не обещать «начнётся после запуска».
    $dockerReady = Test-HrmDockerLinuxEngineReady
    $checks += [pscustomobject]@{
        key = "docker"; label = "Готовность рабочей среды"
        status = if ($dockerReady) { "ok" } else { "fail" }
        detail = if ($dockerReady) { "Docker Engine работает." } else { "Docker Engine не готов — обновление остановится на проверке среды: запустите Docker Desktop (Linux-движок) и повторите попытку." }
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
        [switch]$RolledBack,
        [bool]$RecoveryConfirmed = $true,
        [bool]$NeedsBackupRestore = $false
    )
    $data = [ordered]@{
        status = $Status
        message = (Redact-HrmText $Message)
        from_version = $FromVersion
        to_version = $ToVersion
        release_sha = $ReleaseSha
        rolled_back = [bool]$RolledBack
        # Честность статуса: «восстановлено» только при подтверждённой
        # готовности, иначе трей/мастер/диагностика видят, что восстановление
        # не подтверждено и нужна резервная копия или отчёт для поддержки.
        recovery_confirmed = [bool]$RecoveryConfirmed
        needs_backup_restore = [bool]$NeedsBackupRestore
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
    foreach ($volumeName in @(((Get-HrmProjectName) + "_pilot_pgdata"), ((Get-HrmProjectName) + "_pilot_backups"))) {
        $volume = Invoke-HrmDocker -Arguments @("volume", "inspect", $volumeName) -IgnoreExitCode
        if ($volume.ExitCode -ne 0) { $problems += ("том " + $volumeName) }
    }
    if ($problems.Count -gt 0) {
        throw ("После обновления не найдено: {0}. Обновление остановлено." -f ($problems -join ", "))
    }
    return $true
}

function Get-HrmInstallDirReleaseSha {
    # Идентичность файлов В КАТАЛОГЕ УСТАНОВКИ (release.json). Отличается от
    # Get-HrmInstalledVersionInfo тем, что не подменяет значение записью
    # установки: нужно уметь отличить «в {app} лежит установленная версия» от
    # «мастер установки уже перезаписал файлы новой версией» (иначе проверка
    # сравнивала бы запись саму с собой и всегда «совпадала»).
    param([string]$InstallDir)
    # Чтение ровно то же, что и в engine\Snapshot.psm1: одна реализация, чтобы
    # мастер установки (CLI hrm-snapshot.ps1) и движок понимали идентичность
    # одинаково.
    return (Get-HrmSnapshotReleaseSha -Directory $InstallDir)
}

function Save-HrmPreviousSnapshot {
    # Снимок ПРЕДЫДУЩЕЙ (работающей) версии ДО того, как {app} будет перезаписан
    # новой версией. Снимок получает манифест файлов и описание с
    # идентификатором версии (engine\Snapshot.psm1): без идентификатора откат не
    # мог доказать, что вернул именно прежнюю версию, а без снимка файлов откат
    # возвращал только теги образов (дефекты ревью итераций 14 и 15).
    param([string]$InstallDir, [string]$StateDir, [string]$ReleaseSha = "", [string]$Version = "")
    $identity = $ReleaseSha
    if (-not $identity) { $identity = Get-HrmSnapshotIdentity -InstallDir $InstallDir -StateDir $StateDir }
    if (-not $Version) { $Version = Get-HrmSnapshotVersionInDir -Directory $InstallDir }
    # Снимок ПРЕДЫДУЩЕЙ (установленной) версии: у старой установки ключа
    # лицензии в {app} может не быть — снимок не требует его (ключ
    # восстановится из нового релиза; см. Assert-HrmSnapshotComplete).
    $snapshot = New-HrmVerifiedSnapshotFromDir -SourceDir $InstallDir -StateDir $StateDir -ReleaseSha $identity -Version $Version -Origin "engine" -SkipLicenseKey
    return [pscustomobject]@{
        saved = [bool]$snapshot.saved
        verified = [bool]$snapshot.verified
        reason = [string]$snapshot.reason
        dir = (Get-HrmPreviousSnapshotDir $StateDir)
        release_sha = [string]$snapshot.release_sha
        digest = [string]$snapshot.digest
        files = [int]$snapshot.files
        message = [string]$snapshot.message
    }
}

function Test-HrmPreviousSnapshotMatches {
    # Снимок прежней версии уже сохранён и ПРОВЕРЕН — например мастер установки
    # сохранил его до того, как перезаписал {app}. Такой снимок нельзя затирать:
    # в {app} уже лежит НОВЫЙ код, и «предыдущей версией» стала бы не та
    # версия.
    #
    # Годность определяется описанием снимка (verified), манифестом файлов и
    # совпадением release_sha — это engine\Snapshot.psm1. Снимок БЕЗ описания
    # (его оставила предыдущая версия движка) принимается только тогда, когда
    # release.json снимка совпадает с ожидаемым идентификатором: иначе «прежней
    # версией» мог бы оказаться чужой код.
    param([string]$StateDir, [string]$ReleaseSha = "")
    $resolved = Resolve-HrmPreviousSnapshot -StateDir $StateDir -ReleaseSha $ReleaseSha
    if (-not $resolved.usable) {
        $null = Write-HrmLog "info" ("Сохранённый снимок прежней версии не принят: {0}." -f (Format-HrmSnapshotProblem -Reason $resolved.reason))
    }
    return [bool]$resolved.usable
}

function Save-HrmInstalledSnapshotForSetup {
    # Действие «snapshot-previous»: сохранить снимок УСТАНОВЛЕННОЙ версии ДО
    # того, как мастер установки заменит файлы при обновлении.
    #
    # Тонкая обёртка над общей реализацией (engine\Snapshot.psm1): ровно ту же
    # логику выполняет CLI hrm-snapshot.ps1, который мастер установки
    # распаковывает из СВОЕГО пакета и запускает до первой перезаписи {app}.
    # Действие сохранено как совместимый путь: его вызывает старое поведение
    # мастера и диагностика, и оно обязано честно сообщать отказ (saved=false),
    # а не «продолжать как будто всё хорошо».
    param([string]$InstallDir = "", [string]$StateDir = "", [string]$ResultFile = "")
    return (Save-HrmVerifiedPreviousSnapshot -InstallDir $InstallDir -StateDir $StateDir -ResultFile $ResultFile)
}

function Copy-HrmReleaseToStaging {
    # Копия НОВОГО релиза в управляемый движком каталог StateDir\release-staging.
    # Так предыдущая версия в {app} не теряется до backup gate, а фазы
    # build/switch/migrate/smoke повторяемы, даже если исходный каталог релиза
    # исчез (Setup.exe кладёт снимок во временный {tmp} и удаляет его при выходе).
    param([string]$ReleaseDir, [string]$StateDir)
    $staging = Get-HrmReleaseStagingDir $StateDir
    if (Test-HrmSamePath $ReleaseDir $staging) {
        # Источник уже и есть копия движка: удалять его нельзя (idempotent).
        return $staging
    }
    if (Test-Path $staging) { Remove-Item -Path $staging -Recurse -Force -ErrorAction Stop }
    New-Item -ItemType Directory -Path $staging -Force -ErrorAction Stop | Out-Null
    Copy-HrmSnapshot -SourceDir $ReleaseDir -InstallDir $staging
    return $staging
}

function Get-HrmRollbackPlan {
    # Политика отката после сбоя обновления (полное описание и что делать
    # владельцу — docs/UPDATE_GUIDE.md, раздел «Если обновление не удалось»).
    #
    #   no_changes                    — сбой до фазы switch: установленная версия
    #                                   не менялась; откат не нужен, статус failed;
    #   restore_previous              — схема БД не менялась (сбой до миграции либо
    #                                   upgrade не сдвинул ревизию): автооткат
    #                                   файлов/образов/окружения + проверка готовности;
    #   restore_previous_after_migration — миграция применена: откат тоже выполняется
    #                                   (даунгрейда БД нет), но «восстановлено»
    #                                   подтверждается только ответившим приложением,
    #                                   где в работе НЕ сбойный релиз; иначе —
    #                                   восстановление из проверенной резервной копии;
    #   unknown_schema                — схему или снимок прежней версии проверить не
    #                                   удалось: безопасного автоотката нет, статус
    #                                   об этом и говорит (журнал и бэкапы сохранены).
    param(
        [string]$FailedPhase = "",
        [bool]$MigrationDone = $false,
        [string]$DbRevisionBefore = "",
        [string]$DbRevisionNow = "",
        [bool]$PreviousSnapshotSaved = $true,
        [bool]$FilesSwitched = $true
    )
    if (-not $FilesSwitched -and -not $MigrationDone) {
        # Сбой до фазы switch: установленная версия не менялась, она продолжает
        # работать. Возвращать нечего, сообщать «откат» было бы неправдой.
        return [pscustomobject]@{ plan = "no_changes"; reason = ("сбой на фазе {0} до замены файлов" -f $FailedPhase) }
    }
    if (-not $PreviousSnapshotSaved) {
        return [pscustomobject]@{ plan = "unknown_schema"; reason = "снимок прежней версии не сохранён (файлы каталога установки уже заменены новой версией)" }
    }
    if ($MigrationDone) {
        return [pscustomobject]@{ plan = "restore_previous_after_migration"; reason = "миграция схемы уже применена" }
    }
    if ($FailedPhase -ne "migrate" -and $FailedPhase -ne "smoke") {
        return [pscustomobject]@{ plan = "restore_previous"; reason = ("сбой на фазе {0} до миграции схемы" -f $FailedPhase) }
    }
    if (-not $DbRevisionBefore -or -not $DbRevisionNow) {
        return [pscustomobject]@{ plan = "unknown_schema"; reason = "ревизию схемы БД прочитать не удалось" }
    }
    if ($DbRevisionBefore -eq $DbRevisionNow) {
        return [pscustomobject]@{ plan = "restore_previous"; reason = ("схема БД осталась на ревизии {0}" -f $DbRevisionNow) }
    }
    return [pscustomobject]@{ plan = "restore_previous_after_migration"; reason = ("схема БД ушла с {0} на {1}" -f $DbRevisionBefore, $DbRevisionNow) }
}

function Restore-HrmPreviousVersion {
    # Согласованный возврат прежней версии: файлы из проверенного снимка
    # previous-snapshot, прежние образы по тегам, прежний release_sha в pilot.env
    # и записи установки, затем ЗАПУСК и ПРОВЕРКА ИДЕНТИЧНОСТИ.
    #
    # «ВОССТАНОВЛЕНО» — только при совпадении ожидаемого идентификатора с тем,
    # что сообщает приложение В РАБОТЕ: HTTP 200 и наличие JSON доказательством не
    # считаются. Пустой release_sha при известном ожидаемом, чужой sha или sha
    # сбойной сборки, недостоверный/чужой снимок — отказ (confirmed = false).
    #
    # Legacy-версии без release_sha: строгий документированный фолбэк
    # (docs/UPDATE_GUIDE.md, «Правило прежней идентичности») — он требует
    # проверенного описания снимка, совпадения версии в работе с версией снимка,
    # известного идентификатора сбойной сборки и того, что приложение НЕ
    # сообщает чужой release_sha. Плюс файлы до и после восстановления сверяются
    # с манифестом снимка.
    param(
        [string]$InstallDir,
        [string]$StateDir,
        [hashtable]$PreviousIds,
        [string]$PreviousReleaseSha = "",
        [string]$PreviousVersion = "",
        [string]$FailedReleaseSha = "",
        [string]$FailedReleaseVersion = "",
        [int]$Port = 0,
        [int]$ReadyTimeoutSeconds = 240
    )
    $problems = @()
    $snapshotDir = Get-HrmPreviousSnapshotDir $StateDir
    # Ожидаемая идентичность прежней версии. Явно переданный sha приоритетен; при
    # пустом (старая установка без release_sha) идентификатором становится sha
    # самого снимка — и он всё равно проверяется, по версии в работе.
    $expectedSha = $PreviousReleaseSha
    # Снимок ДОЛЖЕН пройти проверку идентичности и целостности: чужой или
    # повреждённый снимок не имеет права перезаписывать работающую версию.
    $resolved = Resolve-HrmPreviousSnapshot -StateDir $StateDir -ReleaseSha $expectedSha
    if (-not $resolved.usable) {
        $problems += (Format-HrmSnapshotProblem -Reason $resolved.reason)
    }
    $snapshotMetadata = $resolved.metadata
    $snapshotVerified = [bool](Get-HrmSnapshotField -Metadata $snapshotMetadata -Field "verified")
    $snapshotSha = [string](Get-HrmSnapshotField -Metadata $snapshotMetadata -Field "release_sha")
    $snapshotVersion = [string](Get-HrmSnapshotField -Metadata $snapshotMetadata -Field "version")
    $snapshotManifest = @(Get-HrmSnapshotField -Metadata $snapshotMetadata -Field "manifest")
    if (-not $expectedSha -and $snapshotVerified) { $expectedSha = $snapshotSha }
    if ($problems.Count -eq 0) {
        if ($snapshotManifest.Count -eq 0) {
            # Без манифеста нельзя доказать, что в {app} легла именно прежняя
            # версия: отсутствие манифеста — отказ, а не «попробуем».
            $problems += "у снимка прежней версии нет манифеста файлов — достоверность недоказуема"
        }
    }
    if ($problems.Count -eq 0) {
        # Целостность снимка проверяется ДО подмены файлов: повреждённый снимок
        # не должен затирать работающую версию.
        $snapshotCheck = Test-HrmSnapshotManifest -Root $snapshotDir -Manifest $snapshotManifest
        if (-not $snapshotCheck.ok) {
            $problems += ("снимок прежней версии повреждён: " + (($snapshotCheck.problems | Select-Object -First 3) -join "; "))
        }
    }
    if ($problems.Count -eq 0) {
        # Снимок прежней версии может быть у старой установки без ключа
        # лицензии — восстановление не должно падать на проверке полноты.
        try { Copy-HrmSnapshot -SourceDir $snapshotDir -InstallDir $InstallDir -SkipLicenseKey }
        catch { $problems += ("файлы прежней версии не восстановлены: " + (Redact-HrmText $_.Exception.Message)) }
    }
    if ($problems.Count -eq 0) {
        # Восстановленные файлы обязаны совпасть со снимком: иначе в каталоге
        # установки не прежняя версия, и «восстановлено» было бы неправдой.
        $restoredCheck = Test-HrmSnapshotManifest -Root $InstallDir -Manifest $snapshotManifest
        if (-not $restoredCheck.ok) {
            $problems += ("восстановленные файлы не совпали со снимком прежней версии: " + (($restoredCheck.problems | Select-Object -First 3) -join "; "))
        }
    }
    if ($PreviousIds -and $PreviousIds.Count -gt 0) {
        try { Invoke-HrmRollbackImages $PreviousIds } catch { $problems += ("образы не восстановлены: " + (Redact-HrmText $_.Exception.Message)) }
    }
    if ($problems.Count -eq 0) {
        # Идентичность релиза в окружении и записи установки: даже пустой sha
        # (legacy) обязан снова стоять в pilot.env — иначе стек поднимется с
        # release_sha сбойной сборки и проверка идентичности снова не сойдётся.
        try {
            $null = Write-HrmPilotEnv $StateDir $expectedSha $Port
            $fields = @{ rolled_back_at = (Get-Date).ToString("o") }
            if ($expectedSha) { $fields["release_sha"] = $expectedSha }
            if ($PreviousVersion) { $fields["version"] = $PreviousVersion }
            Set-HrmInstallRecord $StateDir $fields
        }
        catch {
            $problems += ("окружение прежней версии не восстановлено: " + (Redact-HrmText $_.Exception.Message))
        }
    }
    $started = $false
    if ($problems.Count -eq 0) {
        try {
            $up = Invoke-HrmCompose $InstallDir $StateDir @("up", "-d", "--remove-orphans") -IgnoreExitCode
            if ($up.ExitCode -ne 0) {
                $problems += ("стек прежней версии не запустился (код {0})" -f $up.ExitCode)
            }
            else { $started = $true }
        }
        catch { $problems += ("запуск прежней версии не удался: " + (Redact-HrmText $_.Exception.Message)) }
    }
    $confirmed = $false
    if ($started) {
        $baseUrl = Get-HrmBaseUrl $Port
        try {
            Wait-HrmReady $baseUrl $ReadyTimeoutSeconds
            $ops = Get-HrmOpsStatus $baseUrl
            if ($null -eq $ops) {
                $problems += "готовность прежней версии не подтверждена (нет ответа о состоянии приложения)"
            }
            else {
                $opsSha = ""
                $opsVersion = ""
                if ($ops.PSObject.Properties["release_sha"] -and $null -ne $ops.release_sha) { $opsSha = ([string]$ops.release_sha).Trim() }
                if ($ops.PSObject.Properties["version"] -and $null -ne $ops.version) { $opsVersion = ([string]$ops.version).Trim() }
                if ($FailedReleaseSha -and $opsSha -and ($opsSha -eq $FailedReleaseSha)) {
                    # В работе по-прежнему сбойная новая версия: откат НЕ удался.
                    $problems += "в работе осталась сбойная версия — откат не применился"
                }
                elseif ($PreviousReleaseSha) {
                    # Ожидаемый идентификатор передан явно (журнал/запись
                    # установки): подтверждение — ТОЛЬКО при точном совпадении.
                    if (-not $opsSha) {
                        $problems += ("приложение не сообщило версию в работе (пустой release_sha): прежняя версия {0} не подтверждена" -f $PreviousReleaseSha)
                    }
                    elseif ($opsSha -ne $PreviousReleaseSha) {
                        $problems += ("в работе другая версия ({0}), ожидалась прежняя ({1}) — восстановление не подтверждено" -f $opsSha, $PreviousReleaseSha)
                    }
                    else { $confirmed = $true }
                }
                elseif ($opsSha -and -not $snapshotSha) {
                    $problems += ("приложение сообщило версию в работе ({0}), но у прежней версии release_sha нет — подтвердить восстановление нельзя" -f $opsSha)
                }
                elseif ($opsSha -and ($opsSha -ne $snapshotSha)) {
                    $problems += ("в работе версия {0}, а в проверенном снимке прежней версии {1} — восстановление не подтверждено" -f $opsSha, $snapshotSha)
                }
                else {
                    # Строгий фолбэк для версий БЕЗ release_sha (он документирован
                    # в docs/UPDATE_GUIDE.md, «Правило прежней идентичности»):
                    # требуется проверенное описание снимка с версией, известный
                    # идентификатор сбойной сборки, отсутствие чужого release_sha
                    # в ответе и совпадение версии в работе с версией снимка.
                    # Файлы до и после восстановления уже сверены с манифестом.
                    if ($FailedReleaseVersion -and $opsVersion -and ($opsVersion -eq $FailedReleaseVersion)) {
                        $problems += ("в работе версия сбойной сборки ({0}) — откат не применился" -f $opsVersion)
                    }
                    elseif (-not $snapshotVerified -or -not $snapshotVersion) {
                        $problems += "прежняя версия не имеет release_sha, а у снимка нет проверенного описания с версией — подтвердить восстановление нельзя"
                    }
                    elseif (-not $FailedReleaseSha) {
                        $problems += "идентификатор сбойного релиза неизвестен — подтвердить восстановление нельзя"
                    }
                    elseif (-not $opsVersion -or $opsVersion -ne $snapshotVersion) {
                        $problems += ("в работе версия {0}, в проверенном снимке прежней версии {1} — восстановление не подтверждено" -f $opsVersion, $snapshotVersion)
                    }
                    else {
                        Write-HrmLog "info" "Откат подтверждён по содержимому снимка и версии: прежняя версия не сообщает release_sha."
                        $confirmed = $true
                    }
                }
            }
        }
        catch { $problems += ("готовность прежней версии не подтверждена: " + (Redact-HrmText $_.Exception.Message)) }
    }
    if ($confirmed -and $problems.Count -eq 0) {
        $identityLabel = $expectedSha
        if (-not $identityLabel) { $identityLabel = $snapshotVersion }
        return [pscustomobject]@{ restored = $true; confirmed = $true; message = ("Прежняя версия ({0}) восстановлена и отвечает." -f $identityLabel) }
    }
    if ($problems.Count -eq 0) { $problems += "готовность не подтверждена" }
    return [pscustomobject]@{ restored = $started; confirmed = $false; message = ("Восстановление не подтверждено: {0}." -f ($problems -join "; ")) }
}

function Update-HrmApp {
    param(
        [string]$ReleaseDir = "",
        [string]$InstallDir = "",
        [string]$StateDir = ""
    )
    if (-not $InstallDir) { $InstallDir = Get-HrmDefaultInstallDir }
    if (-not $StateDir) { $StateDir = Get-HrmStateDir }
    $sourceReleaseDir = $ReleaseDir
    # Возобновление прерванного обновления: каталог релиза может быть уже
    # недоступен — Setup.exe кладёт снимок во временный {tmp} и удаляет его при
    # выходе мастера. Тогда (и только тогда) работаем из копии движка
    # StateDir\release-staging, записанной в журнал.
    $resumeJournalData = $null
    $journalFile = Get-HrmUpdateJournal $StateDir
    if (Test-Path $journalFile) {
        $candidate = Get-HrmJsonFile $journalFile
        if ($null -ne $candidate -and $candidate.PSObject.Properties["release_dir"] -and $candidate.release_dir) {
            $resumeJournalData = $candidate
        }
    }
    $usingStagedRelease = $false
    if ($resumeJournalData -and -not (Test-Path (Join-Path $ReleaseDir "infra\compose.pilot.yml"))) {
        $stagedCandidate = [string]$resumeJournalData.release_dir
        if (Test-Path (Join-Path $stagedCandidate "infra\compose.pilot.yml")) {
            $ReleaseDir = $stagedCandidate
            $usingStagedRelease = $true
            Write-HrmLog "info" ("Каталог релиза недоступен — продолжаю из копии движка: {0}" -f $ReleaseDir)
        }
    }
    if (-not $ReleaseDir) { throw "Укажите -ReleaseDir (доверенный каталог релиза)." }
    if (-not (Test-Path (Join-Path $ReleaseDir "infra\compose.pilot.yml"))) {
        throw "Каталог релиза не содержит infra\compose.pilot.yml: $ReleaseDir"
    }
    $sourceReleaseData = Get-HrmJsonFile (Join-Path $ReleaseDir "release.json")
    if ($null -eq $sourceReleaseData -or -not $sourceReleaseData.release_sha) {
        throw "В каталоге релиза нет release.json с release_sha."
    }
    if ($usingStagedRelease) {
        # Копию движка повторно не копируем: она и есть источник этой попытки.
        $sourceReleaseDir = $ReleaseDir
    }
    # Запись установки: отсутствующая — «выполните установку», а НЕЧИТАЕМАЯ или
    # НЕВАЛИДНАЯ (битый JSON) — отдельная диагностика. Одно общее «что-то пошло
    # не так» не принимается: причина обязана быть видна владельцу и поддержке
    # (код HRM-INSTALL-RECORD-INVALID).
    $record = $null
    $recordError = ""
    try { $record = Get-HrmInstallRecord $StateDir } catch { $recordError = [string]$_.Exception.Message }
    if ($null -eq $record) {
        if ($recordError) {
            throw ("HRM-INSTALL-RECORD-INVALID: запись установки нечитаема или невалидна ({0}): {1}. Обновление остановлено до изменений — восстановите каталог состояния из резервной копии." -f (Get-HrmInstalledFile $StateDir), (Redact-HrmText $recordError))
        }
        throw "Установка не найдена. Выполните -Action install."
    }
    # Порт читается через PSObject: запись СТАРОЙ установки может не содержать
    # свойства port, а обращение к отсутствующему свойству под StrictMode 2.0
    # обрывает обновление до первой фазы (тот же риск, что и у release_sha).
    $port = Get-HrmPort
    if ($record.PSObject.Properties["port"] -and $record.port) { $port = [int]$record.port }
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
    # Legacy-запись установки может НЕ содержать release_sha (идентификатора у
    # старой версии нет). Обращение к отсутствующему свойству под StrictMode 2.0
    # обрывает обновление до первой фазы, поэтому значение читается через
    # PSObject и передаётся пустым — это документированный случай
    # (docs/UPDATE_GUIDE.md, «Правило прежней идентичности»).
    $recordShaForEnv = ""
    if ($record.PSObject.Properties["release_sha"] -and $record.release_sha) { $recordShaForEnv = [string]$record.release_sha }
    # В старой установке license_public_key.b64 мог ещё отсутствовать в
    # StateDir. Восстанавливаем публичный ключ из доверенного snapshot до
    # генерации pilot.env, иначе обязательная Compose-переменная будет пустой.
    # Fail-closed: ключа нет ни в одном источнике — отказ ДО Compose.
    # Порядок аргументов: -SourceDir = каталог релиза, -InstallDir = {app}
    # (функция ищет ключ в обоих путях; переставленные аргументы работали
    # «случайно» и маскировали настоящий источник ключа).
    $null = Assert-HrmLicensePublicKey -SourceDir $ReleaseDir -InstallDir $InstallDir -StateDir $StateDir
    $null = Write-HrmPilotEnv $StateDir $recordShaForEnv $port

    if (-not (Test-HrmUpdateLockAvailable $StateDir)) {
        throw "Обновление уже выполняется (блокировка update.lock). Подождите или запустите -Action resume."
    }
    Set-Content -Path (Get-HrmUpdateLock $StateDir) -Value ("locked at " + (Get-Date).ToString("o")) -Encoding UTF8

    $journal = Get-HrmUpdateJournal $StateDir
    $resume = (Test-Path $journal)
    $data = $null
    $phase = "prepare"
    if ($resume) {
        $data = Get-HrmJsonFile $journal
        if ($null -ne $data -and $data.PSObject.Properties["phase"]) { $phase = [string]$data.phase }
        # Предыдущая попытка уже откатилась и сняла блокировку: это НОВАЯ
        # попытка, повторять откат нельзя (иначе повторная установка прежней
        # версии вместо попытки обновления).
        if ($phase -eq "rollback") {
            Write-HrmLog "info" "Предыдущая попытка завершилась откатом — начинаем новую попытку обновления."
            Clear-HrmUpdateJournal $StateDir
            $resume = $false
            $phase = "prepare"
        }
        else {
            Write-HrmLog "info" ("Возобновление обновления с фазы {0}." -f $phase)
        }
    }

    # Возобновление: нужные состояния восстанавливаются из журнала. Каталог
    # релиза берётся из журнала: у Setup.exe исходный каталог лежал во временном
    # {tmp} и после выхода мастера его уже нет.
    $previousIds = @{}
    $previousSnapshotSaved = $true
    $dbRevisionBefore = ""
    $migrationDone = $false
    $dbRevisionNow = ""
    # Файлы снимка в {app} уже заменены: от этого зависит и политика отката, и
    # честность сообщения о состоянии. Фаза switch означает замену силами
    # движка; ниже к этому добавляется замена силами Setup.exe (он копирует
    # файлы ДО запуска движка) — иначе ошибка после такой замены возвращала бы
    # ложное «Установленная версия не изменялась».
    $filesSwitched = ($phase -eq "switch" -or $phase -eq "migrate" -or $phase -eq "smoke")
    # Доказательство ИЗ ФАЙЛОВ, а не из журнала: в каталоге установки лежит не
    # та версия, что записана установленной. Мастер установки раскладывает файлы
    # новой версии ДО запуска движка, поэтому сбой даже на первых фазах (бэкап,
    # сборка) — это НЕ «установленная версия не изменялась»: политика отката
    # обязана вернуть прежнюю версию, а если снимка нет — честно сообщить, что
    # автоматическое восстановление невозможно (журнал мог быть записан прежней
    # версией движка и об этом не знать).
    $recordShaForEvidence = ""
    if ($record.PSObject.Properties["release_sha"] -and $record.release_sha) { $recordShaForEvidence = [string]$record.release_sha }
    $currentFileSha = Get-HrmInstallDirReleaseSha $InstallDir
    # Второе доказательство, работающее и для СТАРЫХ установок без release_sha:
    # версия в каталоге установки отличается от версии в записи установки —
    # значит, в {app} уже не та версия, что записана установленной.
    $recordVersionForEvidence = ""
    if ($record.PSObject.Properties["version"] -and $record.version) { $recordVersionForEvidence = [string]$record.version }
    $currentFileVersion = Get-HrmSnapshotVersionInDir -Directory $InstallDir
    $filesReplacedEvidence = ($currentFileSha -ne $recordShaForEvidence)
    if (-not $filesReplacedEvidence -and $currentFileVersion -and $recordVersionForEvidence -and
        ($currentFileVersion -ne $recordVersionForEvidence)) {
        $filesReplacedEvidence = $true
        Write-HrmLog "info" ("В каталоге установки версия {0}, а записана установленной {1} — файлы заменены установщиком." -f $currentFileVersion, $recordVersionForEvidence)
    }
    if ($filesReplacedEvidence) {
        $filesSwitched = $true
        Write-HrmLog "info" ("Файлы каталога установки не совпадают с установленной версией ({0} вместо {1}) — политика отката учитывает возможную замену файлов." -f $currentFileSha, $recordShaForEvidence)
    }
    $releaseDir = $sourceReleaseDir
    if ($resume -and $null -ne $data) {
        if ($data.previous_ids) {
            foreach ($pair in $data.previous_ids.PSObject.Properties) {
                $previousIds[$pair.Name] = [string]$pair.Value
            }
        }
        if ($data.PSObject.Properties["release_dir"] -and $data.release_dir) { $releaseDir = [string]$data.release_dir }
        if ($data.PSObject.Properties["db_revision_before"]) { $dbRevisionBefore = [string]$data.db_revision_before }
        if ($data.PSObject.Properties["migration_done"]) { $migrationDone = [bool]$data.migration_done }
        if ($data.PSObject.Properties["previous_snapshot_saved"]) { $previousSnapshotSaved = [bool]$data.previous_snapshot_saved }
        if ($data.PSObject.Properties["files_switched"]) { $filesSwitched = $filesSwitched -or [bool]$data.files_switched }
    }
    $releaseData = Get-HrmJsonFile (Join-Path $releaseDir "release.json")
    if ($null -eq $releaseData) { $releaseData = $sourceReleaseData }
    $releaseSha = [string]$releaseData.release_sha
    if (-not $releaseSha) { $releaseSha = [string]$sourceReleaseData.release_sha }

    $newVersion = ""
    if ($releaseData.PSObject.Properties["version"] -and $releaseData.version) { $newVersion = [string]$releaseData.version }

    try {
        # --- Docker-гейт ДО любой фазы (дефект C, P1): обновление не должно
        # доходить до Compose — включая backup-ворота — с неготовым Docker.
        # Раньше compose падал с сырым текстом про именованный канал
        # npipe:////./pipe/dockerDesktopLinuxEngine уже ПОСЛЕ операций, которым
        # нужен Docker. Гейт стоит после захвата update.lock и до фазы
        # prepare (то есть до любой compose-операции); обновление не ставит
        # Docker самостоятельно (-AllowInstall:$false). При отказе гейта
        # управление переходит в catch: блокировка снимается, результат
        # записывается честно, файлы не изменены.
        # Кроме возобновления с фазой done: обновление УЖЕ завершено, гейт не
        # нужен, а отказ гейта не должен приводить к откату завершённого обновления.
        if ($phase -ne "done") {
            $prepare = Invoke-HrmDockerPrepare -InstallDir $InstallDir -StateDir $StateDir -Port $port -AllowInstall:$false -Interactive:$false
            if (-not $prepare.ok) {
                Write-HrmLog "error" ("Docker-гейт: обновление остановлено до фазы prepare (состояние: {0})." -f $prepare.state)
                throw $prepare.message
            }
        }

        # --- prepare: сохранить прежнюю версию и скопировать новый релиз ----
        if (-not $resume -or $phase -eq "prepare") {
            # Требует установленного Docker и работающего движка: и то, и другое
            # обязательно для фаз backup/build/switch, а на фазах после отката
            # (resume) повторный снимок не нужен — он уже в previous-snapshot.
            $previousIds = Get-HrmImageIds
            $saved = $null
            $previousSnapshotSaved = $false
            $installedSha = $recordShaForEvidence
            $currentSha = $currentFileSha
            # Файлы {app} уже заменены новой версией (Setup.exe копирует файлы
            # ДО запуска движка): снимок прежней версии из них делать нельзя, а
            # ошибка после такой замены НЕ «ничего не менялось».
            $filesReplacedBySetup = $filesReplacedEvidence
            # Идентификатор прежней версии: запись установки, проверенный снимок
            # мастера (нужен для старых записей без release_sha) либо release.json
            # каталога — но ТОЛЬКО пока файлы не заменены: после замены release.json
            # в {app} принадлежит НОВОЙ версии, и взять из него «прежнюю»
            # идентичность значило бы подтверждать откат к сбойной сборке. Пустая
            # строка означает «идентификатора нет» — отдельный документированный
            # случай (docs/UPDATE_GUIDE.md, «Правило прежней идентичности»).
            $previousReleaseSha = $installedSha
            if (-not $previousReleaseSha) {
                $snapshotForPrevious = Resolve-HrmPreviousSnapshot -StateDir $StateDir -ReleaseSha ""
                if ($snapshotForPrevious.usable) {
                    $previousReleaseSha = [string](Get-HrmSnapshotField -Metadata $snapshotForPrevious.metadata -Field "release_sha")
                }
            }
            if (-not $previousReleaseSha -and -not $filesReplacedBySetup) { $previousReleaseSha = $currentSha }
            if (Test-HrmPreviousSnapshotMatches -StateDir $StateDir -ReleaseSha $previousReleaseSha) {
                # Снимок прежней версии уже сохранён и проверен (мастер
                # установки сохранил его ДО перезаписи {app}). Повторный снимок
                # испортил бы картину: он был бы сделан уже из файлов новой
                # версии.
                Write-HrmLog "info" "Проверенный снимок прежней версии уже сохранён — используем его."
                $saved = [pscustomobject]@{ saved = $true; reason = "reused"; message = "Снимок прежней версии уже сохранён." }
            }
            elseif ($filesReplacedBySetup) {
                # Файлы в {app} уже заменены новой версией (Setup.exe копирует
                # файлы ДО запуска движка), а снимка прежней версии нет: значит
                # сохранить её невозможно. Сохранять НОВЫЕ файлы как «прежнюю
                # версию» нельзя — откат вернул бы сбойный код и объявил успех.
                # Обновление продолжаем (единственная возможность получить
                # работающую версию), но политика отката честно скажет, что
                # автоматическое восстановление недоступно.
                Write-HrmLog "warn" ("Прежняя версия файлов недоступна: в каталоге установки уже файлы новой версии ({0}), снимок прежней версии ({1}) не сохранён." -f $currentSha, $previousReleaseSha)
                $saved = [pscustomobject]@{ saved = $false; reason = "files_replaced"; message = "Прежняя версия файлов уже перезаписана установщиком." }
            }
            else {
                $saved = Save-HrmPreviousSnapshot -InstallDir $InstallDir -StateDir $StateDir -ReleaseSha $previousReleaseSha -Version $previousVersion
            }
            if ($saved.saved) {
                $previousSnapshotSaved = $true
            }
            elseif ($saved.reason -eq "files_replaced") {
                # Продолжаем: файлы уже заменены, откатывать их нечем и незачем.
                $previousSnapshotSaved = $false
            }
            else {
                throw ("Не удалось сохранить снимок предыдущей версии ({0}) — обновление остановлено, файлы не изменены." -f $saved.message)
            }
            if ($filesReplacedBySetup) {
                # Файлы {app} заменены ДО движка: сбой на любой следующей фазе
                # (в том числе до бэкап-ворот) обязан приводить к восстановлению
                # прежней версии, а не к «установленная версия не изменялась».
                $filesSwitched = $true
                Write-HrmLog "info" "Файлы каталога установки заменены установщиком — политика отката учитывает это до начала фаз."
            }
            if ($usingStagedRelease) {
                # Уже работаем из копии движка (возобновление): копировать нечего.
                $releaseDir = $ReleaseDir
            }
            else {
                $staged = Copy-HrmReleaseToStaging -ReleaseDir $sourceReleaseDir -StateDir $StateDir
                $releaseDir = $staged
            }
            $dbRevisionBefore = [string](Get-HrmMigrationsState -InstallDir $InstallDir -StateDir $StateDir)
            if (-not $dbRevisionBefore) { $dbRevisionBefore = "unknown" }
            Set-HrmUpdateJournal $StateDir "prepare" @{
                release_dir = $releaseDir
                source_release_dir = $sourceReleaseDir
                previous_ids = $previousIds
                release_sha = $releaseSha
                previous_version = $previousVersion
                previous_release_sha = $previousReleaseSha
                db_revision_before = $dbRevisionBefore
                previous_snapshot_saved = $previousSnapshotSaved
                files_switched = $filesSwitched
                migration_done = $false
            }
            $phase = "backup"
        }

        if ($phase -eq "backup") {
            Invoke-HrmBackupGate $InstallDir $StateDir
            Set-HrmUpdateJournal $StateDir "build" @{ release_dir = $releaseDir; previous_ids = $previousIds; release_sha = $releaseSha }
            $phase = "build"
        }

        if ($phase -eq "build") {
            Write-HrmLog "info" "Сборка новых образов (работающее приложение не останавливается)…"
            # Сборка идёт по базовому compose-файлу и пилотному overlay из
            # обновляемого снимка. Overlay намеренно не является автономным.
            $newBaseCompose = Join-Path $releaseDir "infra\docker-compose.yml"
            $newPilotCompose = Join-Path $releaseDir "infra\compose.pilot.yml"
            $envFile = Get-HrmEnvFile $StateDir
            Invoke-HrmDocker @("compose", "--project-name", "hr-manager-pilot", "--env-file", $envFile, "-f", $newBaseCompose, "-f", $newPilotCompose, "build", "--pull=false") | Out-Null
            Set-HrmUpdateJournal $StateDir "switch" @{ release_dir = $releaseDir; previous_ids = $previousIds; release_sha = $releaseSha }
            $phase = "switch"
        }

        if ($phase -eq "switch") {
            Write-HrmLog "info" "Замена файлов снимка и пересоздание контейнеров…"
            # С этого момента прежние файлы могут быть уже частично заменены:
            # любая ошибка ниже обязана пройти через согласованное восстановление.
            $filesSwitched = $true
            # Безопасный копировщик сам разбирает границы путей: одинаковые пути
            # (Setup уже разложил снимок в {app}) не удаляют источник, вложенные
            # пути отклоняются до изменений, ошибки копирования останавливают
            # установку. Благодаря этому файлы прежней версии в {app} не
            # теряются до успешного backup gate (дефект P1 ревью).
            Copy-HrmSnapshot $releaseDir $InstallDir
            # Публичный ключ проверки лицензии уже установленного пилота лежит в
            # StateDir и здесь не перезаписывается. Если файла нет (старая
            # установка или ручная чистка), берём ключ из обновляемого снимка:
            # без него приложение не проверит лицензию, а pilot.env с
            # обязательной переменной (:?) не даст стеку подняться.
            # Fail-closed: ключ не найден — отказ до Compose (а не пустая строка).
            $null = Assert-HrmLicensePublicKey -SourceDir $releaseDir -InstallDir $InstallDir -StateDir $StateDir
            $null = Write-HrmPilotEnv $StateDir $releaseSha $port
            Invoke-HrmCompose $InstallDir $StateDir @("up", "-d", "--remove-orphans") | Out-Null
            Set-HrmUpdateJournal $StateDir "migrate" @{ release_dir = $releaseDir; previous_ids = $previousIds; release_sha = $releaseSha }
            $phase = "migrate"
        }

        if ($phase -eq "migrate") {
            Write-HrmLog "info" "Однократная миграция схемы (alembic upgrade head)…"
            # A resumed operation may enter directly at migrate after the
            # switch was persisted. Reassert the release env and running
            # containers before migration so smoke observes the target SHA.
            $null = Write-HrmPilotEnv $StateDir $releaseSha $port
            Invoke-HrmCompose $InstallDir $StateDir @("up", "-d", "--remove-orphans") | Out-Null
            $migrate = Invoke-HrmCompose $InstallDir $StateDir @("exec", "-T", "backend", "alembic", "upgrade", "head")
            Write-HrmLog "info" (($migrate.Stdout -split "`n" | Select-Object -Last 3) -join " ")
            # Факт успешного применения миграции: от него зависит политика
            # отката (прежний код поверх новой схемы не поднимается).
            $migrationDone = $true
            Set-HrmUpdateJournal $StateDir "smoke" @{ release_dir = $releaseDir; previous_ids = $previousIds; release_sha = $releaseSha; migration_done = $true }
            $phase = "smoke"
        }

        if ($phase -eq "smoke") {
            Wait-HrmReady $baseUrl 240
            $ops = Get-HrmOpsStatus $baseUrl
            if ($null -eq $ops) { throw "Smoke: /api/ops/status недоступен." }
            $sha = [string]$ops.release_sha
            if ($releaseSha -and $sha -and $sha -ne $releaseSha) {
                throw ("Smoke: несовпадение версии в работе ({0}) и релиза ({1})." -f $sha, $releaseSha)
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
            Set-HrmInstallRecord $StateDir @{ release_sha = $releaseSha; updated_at = (Get-Date).ToString("o"); version = $newVersion }
            Set-HrmUpdateJournal $StateDir "done" @{ release_dir = $releaseDir; previous_ids = $previousIds; release_sha = $releaseSha }
            Clear-HrmUpdateJournal $StateDir
            $null = Write-HrmUpdateResult -StateDir $StateDir -Status "done" -Message "Обновление завершено." -FromVersion $previousVersion -ToVersion $newVersion -ReleaseSha $releaseSha
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
        $recoveryConfirmed = $true
        $needsBackupRestore = $false
        $resultStatus = "failed"
        $resultMessage = ("Обновление не удалось: {0}" -f $failureMessage)
        if ($previousIds.Count -eq 0) {
            # Прежних образов нет (первичная установка/чистый каталог): откатывать
            # нечего, но причину и журнал сохраняем для диагностики.
            Set-HrmUpdateJournal $StateDir "rollback" @{ release_dir = $releaseDir; release_sha = $releaseSha }
        }
        else {
            $dbRevisionNow = [string](Get-HrmMigrationsState -InstallDir $InstallDir -StateDir $StateDir)
            # Ожидаемая идентичность прежней версии вычисляется ЯВНО и заранее:
            # журнал (если он был) либо запись установки. Она же передаётся в
            # проверку отката — «успех» без совпадения с ней запрещён.
            $expectedPreviousSha = ""
            $previousVersionSaved = $previousVersion
            if ($resume -and $null -ne $data) {
                if ($data.PSObject.Properties["previous_release_sha"]) { $expectedPreviousSha = [string]$data.previous_release_sha }
                if (-not $previousVersionSaved -and $data.PSObject.Properties["previous_version"]) { $previousVersionSaved = [string]$data.previous_version }
            }
            if (-not $expectedPreviousSha) {
                # Безопасное чтение: у записи старой установки поля может не быть.
                $expectedPreviousSha = [string](Get-HrmInstallRecordField -Record $record -Field "release_sha")
            }
            # Политика отката должна отражать фактическое состояние: пригодный
            # (проверенный, совпадающий по идентификатору) снимок обязан быть
            # учтён, даже если журнал об этом молчит — журнал мог быть записан
            # прежней версией движка.
            $snapshotForPlan = Resolve-HrmPreviousSnapshot -StateDir $StateDir -ReleaseSha $expectedPreviousSha
            if (-not $previousSnapshotSaved -and $snapshotForPlan.usable) {
                Write-HrmLog "info" "Снимок прежней версии проверен и пригоден — политика отката учитывает его."
                $previousSnapshotSaved = $true
            }
            $plan = Get-HrmRollbackPlan -FailedPhase $phase -MigrationDone $migrationDone -DbRevisionBefore $dbRevisionBefore -DbRevisionNow $dbRevisionNow -PreviousSnapshotSaved $previousSnapshotSaved -FilesSwitched $filesSwitched
            Write-HrmLog "info" ("Политика восстановления: {0} ({1})." -f $plan.plan, $plan.reason)
            Set-HrmUpdateJournal $StateDir "rollback" @{ release_dir = $releaseDir; release_sha = $releaseSha; plan = $plan.plan }
            if ($plan.plan -eq "no_changes") {
                # Установленная версия не менялась: возвращаем только теги образов
                # (сборка могла переписать :pilot), контейнеры не перезапускаем.
                try { Invoke-HrmRollbackImages $previousIds } catch { Write-HrmLog "warn" ("Образы не возвращены по тегам: {0}" -f (Redact-HrmText $_.Exception.Message)) }
                $resultMessage = ("Обновление не удалось: {0} Установленная версия не изменялась и продолжает работать, данные в порядке." -f $failureMessage)
                Write-HrmLog "info" "Установленная версия не изменялась — приложение продолжает работу."
            }
            else {
                # Согласованный возврат прежней версии: файлы + образы + release_sha,
                # затем запуск и проверка готовности. Успех — только если прежняя
                # версия отвечает и в работе НЕ сбойный релиз.
                $restore = Restore-HrmPreviousVersion -InstallDir $InstallDir -StateDir $StateDir -PreviousIds $previousIds -PreviousReleaseSha $expectedPreviousSha -PreviousVersion $previousVersionSaved -FailedReleaseSha $releaseSha -FailedReleaseVersion $newVersion -Port $port
                if ($restore.confirmed) {
                    Write-HrmLog "info" $restore.message
                    $resultStatus = "rolled_back"
                    $resultMessage = ("Обновление не удалось, прежняя версия восстановлена и отвечает. Причина: {0}" -f $failureMessage)
                    if ($plan.plan -eq "restore_previous_after_migration" -or $migrationDone) {
                        # Честность для владельца: схема БД не откатывается.
                        $resultMessage += " Схема базы данных остаётся на новой ревизии, данные не удалялись; если приложение прежней версии сообщит об ошибке, восстановите базу из проверенной резервной копии, созданной перед обновлением."
                    }
                }
                else {
                    # Ложное «восстановлено» запрещено: пишем честный статус,
                    # сохраняем журнал и бэкапы.
                    Write-HrmLog "error" $restore.message
                    $resultStatus = "rollback_failed"
                    $recoveryConfirmed = $false
                    $needsBackupRestore = $true
                    $resultMessage = ("Обновление не удалось: {0} {1} Журнал обновления и резервные копии сохранены — создайте отчёт и передайте его в поддержку." -f $failureMessage, $restore.message)
                }
            }
        }
        # Блокировка снимается всегда: после отката исправленный релиз можно
        # повторить, а незавершённые данные остаются в журнале для разбора.
        $lock = Get-HrmUpdateLock $StateDir
        if (Test-Path $lock) { Remove-Item $lock -Force }
        $null = Write-HrmUpdateResult -StateDir $StateDir -Status $resultStatus -Message $resultMessage -FromVersion $previousVersion -ToVersion $newVersion -ReleaseSha $releaseSha -RolledBack:($resultStatus -eq "rolled_back") -RecoveryConfirmed:$recoveryConfirmed -NeedsBackupRestore:$needsBackupRestore
        throw
    }
}
