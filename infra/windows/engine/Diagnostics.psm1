# Агрегированная диагностика. Различает обязательные состояния контракта:
#   docker: missing | daemon_down | ok
#   app:    stopped | starting | ready | degraded
#   db:     unknown | ok | down
#   migration: ok | drift | unknown
#   worker: ok | stale | unknown
#   backup: ok | stale | failed | missing | unknown
#   version: match | mismatch | unknown
#   channels: smtp/telegram — configured | not_configured (пилот: off по умолчанию)
# Вывод всегда проходит редакцию секретов (Redact-HrmText/Protect-HrmOutput).

Set-StrictMode -Version 2.0

function Get-HrmDockerState {
    $cli = Invoke-HrmDocker -Arguments @("--version") -IgnoreExitCode
    if ($cli.ExitCode -ne 0) { return "missing" }
    $info = Invoke-HrmDocker -Arguments @("info", "--format", "{{.ServerVersion}}") -IgnoreExitCode
    if ($info.ExitCode -ne 0) { return "daemon_down" }
    return "ok"
}

function Get-HrmBackupState {
    # Честная оценка бэкапов из /api/ops/backup-health (503 при
    # отсутствии/протухании/провале) и сигнала /api/ops/status.
    param([string]$BaseUrl)
    try {
        $health = Invoke-HrmHttp -Uri "$BaseUrl/api/ops/backup-health"
        if ($health.StatusCode -eq 200 -and $null -ne $health.Body -and $health.Body.fresh -eq $true) {
            return "ok"
        }
        if ($health.StatusCode -eq 503) {
            # Не «ok»: отличаем failed/missing/stale по сигналу статуса.
            $ops = Get-HrmOpsStatus $BaseUrl
            if ($null -ne $ops -and $ops.backup) {
                $b = $ops.backup
                if ($b.ok -eq $false) { return "failed" }
                if (-not $b.available) { return "missing" }
                return "stale"
            }
            return "unknown"
        }
        return "unknown"
    }
    catch { return "unknown" }
}

function Get-HrmDiagnostics {
    param(
        [string]$InstallDir = "",
        [string]$StateDir = "",
        [switch]$AsJson
    )
    if (-not $InstallDir) { $InstallDir = Get-HrmDefaultInstallDir }
    if (-not $StateDir) { $StateDir = Get-HrmStateDir }
    $record = Get-HrmInstallRecord $StateDir
    $port = [int](Get-HrmInstallRecordField -Record $record -Field "port" -Default 0)
    if ($port -le 0) { $port = Get-HrmPort }
    $baseUrl = Get-HrmBaseUrl $port

    $docker = Get-HrmDockerState
    $running = $false
    $frontend = $false
    $backend = $false
    $ops = $null
    if ($docker -eq "ok") {
        $running = Test-HrmComposeRunning $InstallDir $StateDir
        if ($running) {
            $frontend = Test-HrmFrontendReady $baseUrl
            $backend = Test-HrmBackendReady $baseUrl
            # /ops/status deliberately returns 503 with a structured body when
            # the database is down; query it whenever the frontend proxy is up.
            if ($frontend) { $ops = Get-HrmOpsStatus $baseUrl }
        }
    }

    # Состояние приложения.
    if (-not $running) { $app = "stopped" }
    elseif ($null -ne $ops -and $ops.database -and $ops.database.status -ne "ok") { $app = "degraded" }
    elseif ($frontend -and $backend) { $app = "ready" }
    elseif ($frontend -and -not $backend -and $null -ne $ops) { $app = "degraded" }
    elseif ($frontend -or $backend) { $app = "starting" }
    else { $app = "degraded" }

    # БД.
    $db = "unknown"
    if ($null -ne $ops -and $ops.database) {
        $db = if ($ops.database.status -eq "ok") { "ok" } else { "down" }
    }
    elseif ($running -and $frontend -and -not $backend) {
        # The frontend is reachable and containers are running, but the API
        # health gate is failing. In this topology that is the observable
        # database-down/degraded state even when /ops/status is unavailable.
        $db = "down"
    }

    # Миграции: head и отсутствие дрейфа.
    $migration = "unknown"
    $currentRev = ""
    $expectedRev = ""
    if ($null -ne $ops -and $ops.migrations) {
        $currentRev = [string]$ops.migrations.current_revision
        $expectedRev = [string]$ops.migrations.expected_revision
        if ($ops.migrations.ok -eq $true) { $migration = "ok" }
        elseif ($ops.migrations.ok -eq $false) { $migration = "drift" }
    }

    # Worker: notifications.worker.alive из /ops/status.
    $worker = "unknown"
    if ($null -ne $ops -and $ops.notifications -and $ops.notifications.worker) {
        if ($ops.notifications.worker.alive -eq $true) { $worker = "ok" }
        elseif ($ops.notifications.worker.alive -eq $false) { $worker = "stale" }
    }

    # Бэкапы.
    $backup = if ($backend) { Get-HrmBackupState $baseUrl } else { "unknown" }

    # Установленная и работающая версии.
    $version = "unknown"
    $installedSha = [string](Get-HrmInstallRecordField -Record $record -Field "release_sha")
    $runningSha = if ($ops) { [string]$ops.release_sha } else { "" }
    if ($installedSha -and $runningSha) {
        $version = if ($installedSha -eq $runningSha) { "match" } else { "mismatch" }
    }
    elseif ($runningSha) { $version = "match" }

    # Каналы: пилотный compose выключает SMTP/Telegram по умолчанию;
    # честное значение «not_configured» без захода в приватные настройки.
    $channels = @{ smtp = "not_configured"; telegram = "not_configured" }

    # Факты CLI контейнеров и плагина compose (регрессии D5/D6): какой CLI выбран
    # и ПОЧЕМУ, какие кандидаты найдены на машине, какие каталоги плагинов
    # проверены и что в них лежит, чем ответил compose. Без этих фактов отказ
    # «compose не найден» на чужой машине не воспроизвести: непонятно, то ли CLI
    # не запустился, то ли плагина нет, то ли он старый.
    # Проба версии (--version) выполняется только здесь и только для ВЫБРАННОГО
    # CLI: диагностика вызывается по запросу, а не на каждом обращении к Docker.
    $cliPath = ""
    $cliVersion = ""
    $cliProbeVersion = ""
    $cliReason = ""
    $cliCandidates = @()
    try {
        $cliFacts = Get-HrmDockerCliFacts -Probe
        $cliPath = [string]$cliFacts.cli
        $cliVersion = [string]$cliFacts.cli_version
        $cliProbeVersion = [string]$cliFacts.cli_version_probed
        $cliReason = [string]$cliFacts.chosen_reason
        foreach ($candidate in $cliFacts.candidates) {
            $cliCandidates += [ordered]@{
                path = [string]$candidate.path
                source = [string]$candidate.source
                file_version = [string]$candidate.file_version
                install_root = [string]$candidate.install_root
                desktop_root_match = [bool]$candidate.desktop_root_match
            }
        }
    }
    catch {
        # Факты не сняты — это само по себе диагноз: причина пишется в журнал
        # (редактируется от секретов), а не теряется молча.
        $null = Write-HrmLog "warn" ("Факты CLI контейнеров не сняты: " + (Redact-HrmText $_.Exception.Message))
    }
    if (-not $cliPath) {
        # Резервный путь: резолвер CLI не запускает процессов, поэтому работает и
        # тогда, когда проба версии невозможна.
        try { $cliPath = [string](Get-HrmDockerCliPath) } catch { $cliPath = "" }
    }
    $composeProbe = Test-HrmComposeVersion
    $composeReason = ""
    $composePluginFound = $false
    $composePluginDirs = @()
    if ($composeProbe.PSObject.Properties["Reason"]) { $composeReason = [string]$composeProbe.Reason }
    if ($composeProbe.PSObject.Properties["PluginFound"]) { $composePluginFound = [bool]$composeProbe.PluginFound }
    if ($composeProbe.PSObject.Properties["PluginDirs"]) {
        foreach ($dir in $composeProbe.PluginDirs) {
            $composePluginDirs += [ordered]@{
                path = [string]$dir.path
                exists = [bool]$dir.exists
                files = @($dir.files | ForEach-Object { [string]$_ })
                searched_by_cli = [bool]$dir.searched_by_cli
                note = [string]$dir.note
            }
        }
    }
    # Память WSL2 (регрессия D9): факт, а не «исправление». Ожидаемый диапазон и
    # почему движок ничего не удаляет — см. Get-HrmWslMemoryFacts и README.
    $wsl = Get-HrmWslMemoryFacts

    $result = [ordered]@{
        generated_at = (Get-Date).ToString("o")
        install_dir = $InstallDir
        state_dir = $StateDir
        url = $baseUrl
        installed = ($null -ne $record)
        docker = $docker
        app = $app
        database = $db
        migration = $migration
        migration_current = $currentRev
        migration_expected = $expectedRev
        worker = $worker
        backup = $backup
        version = $version
        installed_release_sha = $installedSha
        running_release_sha = $runningSha
        smtp = $channels["smtp"]
        telegram = $channels["telegram"]
        # --- факты ревизии стабильности (12.10.2026) ---
        docker_cli = $cliPath
        docker_cli_version = $cliVersion
        docker_cli_version_probed = $cliProbeVersion
        docker_cli_chosen_reason = $cliReason
        docker_cli_candidates = $cliCandidates
        compose_reason = $composeReason
        compose_plugin_found = $composePluginFound
        compose_plugin_dirs = $composePluginDirs
        wsl_memory_mb = [int]$wsl["total_mb"]
        wsl_memory_observed = [bool]$wsl["observed"]
        wsl_memory_processes = @($wsl["processes"])
        host_memory_total_mb = [int]$wsl["host_total_mb"]
        host_memory_free_mb = [int]$wsl["host_free_mb"]
    }

    # Текст про compose для человекочитаемого вывода: причина целиком уходит в
    # JSON и в журнал, здесь — короткая строка (иначе вывод диагностики
    # превращается в простыню).
    $composeText = (Redact-HrmText [string]$composeProbe.Message)
    if ($composeText.Length -gt 200) { $composeText = $composeText.Substring(0, 197) + "..." }
    # B4 hint for support-bundle shortcut
    $hint = 'Чтобы отправить отчёт разработчику, используйте ярлык "HR Manager — отчёт для разработчика"'
    $result["hint"] = $hint
    if ($AsJson) {
        Protect-HrmOutput (($result | ConvertTo-Json -Depth 6))
    }
    else {
        $rows = @(
            ("docker      : {0}" -f $result.docker),
            ("app         : {0}" -f $result.app),
            ("database    : {0}" -f $result.database),
            ("migration   : {0} (в базе {1}, ожидается {2})" -f $result.migration, $result.migration_current, $result.migration_expected),
            ("worker      : {0}" -f $result.worker),
            ("backup      : {0}" -f $result.backup),
            ("version     : {0} (установлено {1}, в работе {2})" -f $result.version, $result.installed_release_sha, $result.running_release_sha),
            ("smtp        : {0}" -f $result.smtp),
            ("telegram    : {0}" -f $result.telegram),
            ("docker_cli  : {0} (версия {1})" -f $result.docker_cli, $result.docker_cli_version),
            ("docker_cli  : правило выбора — {0}; кандидатов найдено {1}" -f $result.docker_cli_chosen_reason, @($result.docker_cli_candidates).Count),
            ("compose     : {0}" -f $composeText),
            ("wsl_memory  : {0} МБ (на хосте всего {1} МБ, свободно {2} МБ)" -f $result.wsl_memory_mb, $result.host_memory_total_mb, $result.host_memory_free_mb),
            ("url         : {0}" -f $result.url),
            $hint
        )
        $rows | ForEach-Object { Protect-HrmOutput $_ }
    }
}
# --- Phase 14: отчёт о хосте для readiness ---------------------------------------
#
# Движок отправляет на loopback только redacted-факты о хосте: версия Windows,
# состояние Docker/Compose, опубликованные порты, свободное место, флаги
# каталогов и установленная версия. Пути, имена пользователей, токены и любые
# секреты в отчёт не попадают: backend валидирует строгую схему, а всё
# неизвестное отбрасывается (extra="ignore"). Отправка best-effort: офлайн не
# ломает диагностику и не мешает основной работе.

function Get-HrmWindowsFacts {
    # Версия/сборка/название продукта. Ни серийных номеров, ни имён пользователей.
    $facts = @{ version = ""; build = $null; product_name = "" }
    try {
        $info = Get-ItemProperty -Path "HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion" -ErrorAction Stop
        if ($info.ProductName) { $facts["product_name"] = [string]$info.ProductName }
        if ($info.DisplayVersion) { $facts["version"] = [string]$info.DisplayVersion }
        if ($info.CurrentBuildNumber) { $facts["build"] = [int]$info.CurrentBuildNumber }
    }
    catch { }
    return $facts
}

function Get-HrmDockerFacts {
    $facts = @{ cli_ok = $false; daemon_ok = $false; server_version = "" }
    $cli = Invoke-HrmDocker -Arguments @("--version") -IgnoreExitCode
    if ($cli.ExitCode -eq 0) { $facts["cli_ok"] = $true }
    $info = Invoke-HrmDocker -Arguments @("info", "--format", "{{.ServerVersion}}") -IgnoreExitCode
    if ($info.ExitCode -eq 0) {
        $facts["daemon_ok"] = $true
        $facts["server_version"] = $info.Stdout.Trim()
    }
    return $facts
}

function Get-HrmWslMemoryFacts {
    # Память WSL2-виртуальной машины Docker Desktop (процессы vmmemWSL/vmmem).
    #
    # Зачем факт нужен (регрессия D9): на пилотной машине наблюдали vmmemWSL
    # ≈ 1.8 ГБ и подозревали утечку HR Manager. Это НЕ аномалия и не утечка
    # приложения: WSL2 по умолчанию разрешено занимать до 50% ОЗУ хоста, внутри
    # живут демон Docker, контейнеры пилота (Postgres, backend, frontend,
    # worker) и кэш сборки образов; 1–2 ГБ при запущенном стеке — ожидаемый
    # диапазон (см. infra\windows\README.md, раздел «Память WSL2 (vmmemWSL)»).
    # Движок НЕ «экономит» память удалением чего-либо у пользователя: он только
    # сообщает факт и проверяет, что параллельных compose-операций нет
    # (единственность цикла движка и блокировка действия — см. Supervisor.psm1).
    # Ограничение WSL2 по памяти задаёт сам владелец машины файлом .wslconfig;
    # движок его не создаёт и не меняет.
    $facts = @{ observed = $false; total_mb = 0; processes = @(); host_total_mb = 0; host_free_mb = 0 }
    try {
        $os = Get-CimInstance Win32_OperatingSystem -ErrorAction SilentlyContinue
        if ($null -ne $os) {
            if ($os.TotalVisibleMemorySize) { $facts["host_total_mb"] = [int][Math]::Round(([double]$os.TotalVisibleMemorySize / 1024), 0) }
            if ($os.FreePhysicalMemory) { $facts["host_free_mb"] = [int][Math]::Round(([double]$os.FreePhysicalMemory / 1024), 0) }
        }
    }
    catch { }
    try {
        $items = @(Get-Process -ErrorAction Stop | Where-Object { ([string]$_.ProcessName) -match "(?i)^vmmem" })
        foreach ($item in $items) {
            $mb = 0
            try { $mb = [int][Math]::Round(([double]$item.WorkingSet64 / 1MB), 0) } catch { $mb = 0 }
            $facts["processes"] += [pscustomobject]@{
                name = [string]$item.ProcessName
                id = [int]$item.Id
                working_set_mb = $mb
            }
            $facts["total_mb"] = [int]$facts["total_mb"] + $mb
            $facts["observed"] = $true
        }
    }
    catch { }
    return $facts
}

function Get-HrmComposeFacts {
    $probe = Test-HrmComposeVersion
    $version = ""
    $messageProperty = $probe.PSObject.Properties["Message"]
    if ($null -ne $messageProperty -and $messageProperty.Value) {
        $match = [regex]::Match([string]$messageProperty.Value, "(\d+\.\d+\.\d+)")
        if ($match.Success) { $version = $match.Groups[1].Value }
    }
    return @{ ok = [bool]$probe.Passed; version = $version }
}

function Get-HrmPublishedPortFacts {
    # Публикации из `docker compose ps`: сервис + адрес привязки + порт.
    # observed=$false означает «получить не удалось» — backend честно покажет
    # предупреждение вместо ложного «все порты на loopback».
    param([string]$InstallDir, [string]$StateDir)
    $result = @{ observed = $false; ports = @() }
    $ps = Invoke-HrmCompose $InstallDir $StateDir @("ps", "--format", "json") -IgnoreExitCode
    if ($ps.ExitCode -ne 0) { return $result }
    $items = @()
    try { $items = @($ps.Stdout | ConvertFrom-Json -ErrorAction Stop) }
    catch {
        foreach ($line in ($ps.Stdout -split "`r?`n")) {
            if (-not [string]::IsNullOrWhiteSpace($line)) {
                $items += ($line | ConvertFrom-Json -ErrorAction Stop)
            }
        }
    }
    $ports = @()
    foreach ($item in $items) {
        if ($null -eq $item) { continue }
        # StrictMode 2.0: свойства читаем только через PSObject.Properties —
        # Compose может не отдать Publishers, и обращение к ним обязано быть
        # безопасным, а не исключением.
        $publishersProperty = $item.PSObject.Properties["Publishers"]
        if ($null -eq $publishersProperty) { continue }
        $serviceProperty = $item.PSObject.Properties["Service"]
        $service = if ($null -ne $serviceProperty) { [string]$serviceProperty.Value } else { "" }
        foreach ($publisher in @($publishersProperty.Value)) {
            if ($null -eq $publisher) { continue }
            $urlProperty = $publisher.PSObject.Properties["URL"]
            $hostIp = if ($null -ne $urlProperty -and $urlProperty.Value) { [string]$urlProperty.Value } else { "" }
            $portProperty = $publisher.PSObject.Properties["PublishedPort"]
            $port = $null
            if ($null -ne $portProperty -and $portProperty.Value) { $port = [int]$portProperty.Value }
            if (-not $hostIp -and $null -eq $port) { continue }
            $ports += @{ service = $service; host_ip = $hostIp; port = $port }
        }
    }
    $result["observed"] = $true
    $result["ports"] = $ports
    return $result
}

function Get-HrmDirFacts {
    # Только флаги: сами пути в отчёт не уходят.
    param([string]$Path)
    $facts = @{ configured = $false; acl_restricted = $null; inside_state_dir = $null; inside_program_files = $null }
    if ([string]::IsNullOrWhiteSpace($Path)) { return $facts }
    $facts["configured"] = [bool](Test-Path $Path)
    $stateDir = Get-HrmStateDir
    if (-not [string]::IsNullOrWhiteSpace($stateDir)) {
        $facts["inside_state_dir"] = $Path.TrimEnd("\") -like ($stateDir.TrimEnd("\") + "\*")
    }
    $programFiles = $env:ProgramFiles
    if (-not [string]::IsNullOrWhiteSpace($programFiles)) {
        $facts["inside_program_files"] = $Path.TrimEnd("\") -like ($programFiles.TrimEnd("\") + "\*")
    }
    return $facts
}

function Get-HrmHostFreeSpaceMb {
    # Свободное место на диске установки для отчёта о хосте. Имя отличается от
    # Docker.psm1\Get-HrmFreeSpaceMb намеренно: одинаковые имена в двух модулях
    # движка перекрываются в порядке импорта (Docker импортируется после
    # Diagnostics) и делают проверку места непредсказуемой.
    param([string]$Path)
    try {
        $qualifier = Split-Path -Qualifier $Path
        $drive = Get-PSDrive -Name ($qualifier.TrimEnd(":")) -ErrorAction Stop
        return [int]($drive.Free / 1MB)
    }
    catch { return $null }
}

function Get-HrmPreviousImagePresent {
    # Есть ли образ предыдущей версии (возможность безопасного rollback).
    $images = Invoke-HrmDocker -Arguments @("images", "--format", "{{.Repository}}:{{.Tag}}") -IgnoreExitCode
    if ($images.ExitCode -ne 0) { return $null }
    foreach ($line in ($images.Stdout -split "`r?`n")) {
        if ($line.Trim() -like "*:previous") { return $true }
    }
    return $false
}

function Get-HrmHostReportPayload {
    # Полный redacted-отчёт по схеме backend (UpdateEngineHostReportRequest).
    param([string]$InstallDir = "", [string]$StateDir = "", [string]$AppState = "")
    if (-not $InstallDir) { $InstallDir = Get-HrmDefaultInstallDir }
    if (-not $StateDir) { $StateDir = Get-HrmStateDir }
    $record = Get-HrmInstallRecord $StateDir
    $release = Get-HrmJsonFile (Join-Path $InstallDir "release.json")
    # StrictMode 2.0: у JSON-объектов поля читаем через PSObject.Properties.
    $releaseVersion = ""
    if ($null -ne $release) {
        $releaseProperty = $release.PSObject.Properties["version"]
        if ($null -ne $releaseProperty -and $releaseProperty.Value) { $releaseVersion = [string]$releaseProperty.Value }
    }
    $recordSha = ""
    if ($null -ne $record) {
        $shaProperty = $record.PSObject.Properties["release_sha"]
        if ($null -ne $shaProperty -and $shaProperty.Value) { $recordSha = [string]$shaProperty.Value }
    }
    $published = Get-HrmPublishedPortFacts -InstallDir $InstallDir -StateDir $StateDir
    $staging = Get-HrmStagingHostDir
    return [ordered]@{
        schema_version = 1
        generated_at = (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ")
        engine_version = "phase-14"
        app_state = $AppState
        windows = (Get-HrmWindowsFacts)
        docker = (Get-HrmDockerFacts)
        compose = (Get-HrmComposeFacts)
        published_ports = @($published["ports"])
        ports_observed = [bool]$published["observed"]
        free_space_mb = (Get-HrmHostFreeSpaceMb -Path $InstallDir)
        state_dir = (Get-HrmDirFacts -Path $StateDir)
        staging = (Get-HrmDirFacts -Path $staging)
        installed_version = $releaseVersion
        installed_release_sha = $recordSha
        previous_images_present = (Get-HrmPreviousImagePresent)
    }
}

function Send-HrmHostReport {
    # POST на loopback с машинным токеном. Best-effort: без install record или
    # токена ничего не отправляется, сетевые ошибки только логируются.
    param([string]$InstallDir = "", [string]$StateDir = "", [string]$AppState = "")
    if (-not $StateDir) { $StateDir = Get-HrmStateDir }
    $record = Get-HrmInstallRecord $StateDir
    if ($null -eq $record) { return $false }
    $token = Get-HrmSecret $StateDir "HRM_UPDATE_ENGINE_TOKEN"
    if ([string]::IsNullOrEmpty($token)) { return $false }
    $portProperty = $record.PSObject.Properties["port"]
    $port = if ($null -ne $portProperty -and $portProperty.Value) { [int]$portProperty.Value } else { Get-HrmPort }
    $payload = Get-HrmHostReportPayload -InstallDir $InstallDir -StateDir $StateDir -AppState $AppState
    try {
        $response = Invoke-HrmHttp -Uri ("{0}/api/updates/engine-host-report" -f (Get-HrmBaseUrl $port)) `
            -Method "POST" -Body $payload -Headers @{ "X-Engine-Token" = $token }
        return ($response.StatusCode -eq 200)
    }
    catch {
        # Write-HrmLog пишет в pipeline: без $null = функция вернула бы массив.
        $null = Write-HrmLog "info" ("Хост-отчёт не отправлен (сервер недоступен): {0}" -f (Redact-HrmText $_.Exception.Message))
        return $false
    }
}
