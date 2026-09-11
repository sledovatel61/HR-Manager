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
    $cli = Invoke-HrmExternal -Name "docker.exe" -Arguments @("--version") -IgnoreExitCode
    if ($cli.ExitCode -ne 0) { return "missing" }
    $info = Invoke-HrmExternal -Name "docker.exe" -Arguments @("info", "--format", "{{.ServerVersion}}") -IgnoreExitCode
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
    $port = Get-HrmPort
    if ($null -ne $record -and $record.port) { $port = [int]$record.port }
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
    $installedSha = if ($record) { [string]$record.release_sha } else { "" }
    $runningSha = if ($ops) { [string]$ops.release_sha } else { "" }
    if ($installedSha -and $runningSha) {
        $version = if ($installedSha -eq $runningSha) { "match" } else { "mismatch" }
    }
    elseif ($runningSha) { $version = "match" }

    # Каналы: пилотный compose выключает SMTP/Telegram по умолчанию;
    # честное значение «not_configured» без захода в приватные настройки.
    $channels = @{ smtp = "not_configured"; telegram = "not_configured" }

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
    }

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
            ("url         : {0}" -f $result.url)
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
    $cli = Invoke-HrmExternal -Name "docker.exe" -Arguments @("--version") -IgnoreExitCode
    if ($cli.ExitCode -eq 0) { $facts["cli_ok"] = $true }
    $info = Invoke-HrmExternal -Name "docker.exe" -Arguments @("info", "--format", "{{.ServerVersion}}") -IgnoreExitCode
    if ($info.ExitCode -eq 0) {
        $facts["daemon_ok"] = $true
        $facts["server_version"] = $info.Stdout.Trim()
    }
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

function Get-HrmFreeSpaceMb {
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
    $images = Invoke-HrmExternal -Name "docker.exe" -Arguments @("images", "--format", "{{.Repository}}:{{.Tag}}") -IgnoreExitCode
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
        free_space_mb = (Get-HrmFreeSpaceMb -Path $InstallDir)
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
        Write-HrmLog "info" ("Хост-отчёт не отправлен (сервер недоступен): {0}" -f (Redact-HrmText $_.Exception.Message))
        return $false
    }
}
