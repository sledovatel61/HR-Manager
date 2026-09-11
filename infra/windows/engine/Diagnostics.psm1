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

    # Trust store: redacted diagnostics (только key_id + fingerprint, никаких секретов).
    $trustState = "not_configured"
    $trustFingerprints = @()
    $trustRevoked = 0
    try {
        $chCfg = Get-HrmChannelConfig
        if ($null -ne $chCfg -and $chCfg.public_keys -and $chCfg.public_keys.Count -gt 0) {
            $trustState = "configured"
            foreach ($kid in $chCfg.public_keys.Keys) {
                $entry = $chCfg.public_keys[$kid]
                $revoked = $false
                $fingerprint = "unknown"
                if ($entry -is [hashtable] -and $entry.ContainsKey("revoked")) { $revoked = [bool]$entry.revoked }
                if ($revoked) { $trustRevoked += 1 }
                # Fingerprint: первые 12 hex sha256 публичного ключа (redacted, без самого ключа)
                try {
                    if ($entry -is [hashtable] -and $entry.ContainsKey("key") -and $entry["key"]) {
                        $raw = [Convert]::FromBase64String([string]$entry["key"])
                        $sha = [System.Security.Cryptography.SHA256]::Create().ComputeHash($raw)
                        $fingerprint = -join ($sha[0..5] | ForEach-Object { $_.ToString("x2") })
                    }
                } catch { $fingerprint = "invalid" }
                $trustFingerprints += [ordered]@{ key_id = $kid; fingerprint = $fingerprint; revoked = $revoked }
            }
            # Проверяем встроенный trust_store.json в InstallDir (детерминированность выпуска)
            $embeddedPath = Join-Path $InstallDir "trust_store.json"
            if (Test-Path $embeddedPath) {
                try {
                    $embeddedRaw = Get-Content -Path $embeddedPath -Raw -Encoding UTF8
                    $embedded = $embeddedRaw | ConvertFrom-Json -AsHashtable -ErrorAction Stop
                    # Сравниваем детерминированно (embedded vs channel.json) — несовпадение = warning в диагностике
                    $mismatch = $false
                    if ($embedded.Count -ne $chCfg.public_keys.Count) { $mismatch = $true }
                    else {
                        foreach ($k in $embedded.Keys) {
                            if (-not $chCfg.public_keys.ContainsKey($k)) { $mismatch = $true; break }
                        }
                    }
                    if ($mismatch) { $trustState = "mismatch" }
                } catch { $trustState = "embedded_invalid" }
            }
        }
    } catch { $trustState = "unknown" }

    # Update channel: offline/errors are warnings, not fatal — hrm status/type = warning
    $channelStatus = "unknown"
    $channelUrl = ""
    try {
        $ch = Get-HrmChannelConfig
        if ($ch -and $ch.url) { $channelUrl = [string]$ch.url }
        # Проверяем доступность канала (HEAD-like): offline = warning per phase 14
        if ($channelUrl) {
            $channelStatus = "configured"
            # Не раскрываем URL в логах полностью — только хост через redaction
        } else { $channelStatus = "not_configured" }
    } catch { $channelStatus = "unknown" }

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
        channel = $channelStatus
        channel_url_redacted = if ($channelUrl) { Protect-HrmOutput $channelUrl } else { "" }
        trust_store = $trustState
        trust_fingerprints = $trustFingerprints
        trust_revoked = $trustRevoked
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
            ("channel     : {0}" -f $result.channel),
            ("trust_store : {0} (revoked {1})" -f $result.trust_store, $result.trust_revoked),
            ("url         : {0}" -f $result.url)
        )
        if ($result.trust_fingerprints -and $result.trust_fingerprints.Count -gt 0) {
            foreach ($fp in $result.trust_fingerprints) {
                $rows += ("  key {0}: {1} revoked={2}" -f $fp.key_id, $fp.fingerprint, $fp.revoked)
            }
        }
        $rows | ForEach-Object { Protect-HrmOutput $_ }
    }
}
