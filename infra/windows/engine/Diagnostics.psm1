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
            if ($backend) { $ops = Get-HrmOpsStatus $baseUrl }
        }
    }

    # Состояние приложения.
    if (-not $running) { $app = "stopped" }
    elseif ($null -ne $ops -and $ops.database -and $ops.database.status -ne "ok") { $app = "degraded" }
    elseif ($frontend -and $backend) { $app = "ready" }
    elseif ($frontend -or $backend) { $app = "starting" }
    else { $app = "degraded" }

    # БД.
    $db = "unknown"
    if ($null -ne $ops -and $ops.database) {
        $db = if ($ops.database.status -eq "ok") { "ok" } else { "down" }
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
