# Работа с Docker Compose: единый проект hr-manager-pilot, запуск/остановка/
# статус/готовность. Всё идёт через docker CLI (Invoke-HrmExternal) и
# env-файл из защищённого каталога состояния.

Set-StrictMode -Version 2.0

$script:ProjectName = "hr-manager-pilot"
$script:ExpectedHeadRevision = "0013"

function Get-HrmComposeFile {
    param([string]$InstallDir)
    return (Join-Path $InstallDir "infra\compose.pilot.yml")
}

function Get-HrmComposeArgs {
    # Общие аргументы compose: стабильное имя проекта + env-файл состояния.
    param([string]$InstallDir, [string]$StateDir)
    $composeArgs = @("compose")
    $composeArgs += ("--project-name", $script:ProjectName)
    $envFile = Get-HrmEnvFile $StateDir
    if (Test-Path $envFile) { $composeArgs += ("--env-file", $envFile) }
    $composeArgs += ("-f", (Get-HrmComposeFile $InstallDir))
    return , $composeArgs
}

function Invoke-HrmCompose {
    param([string]$InstallDir, [string]$StateDir, [string[]]$Command = @(), [string]$Stdin = "", [switch]$IgnoreExitCode)
    $composeArgs = Get-HrmComposeArgs $InstallDir $StateDir
    $composeArgs += $Command
    return Invoke-HrmDocker -Arguments $composeArgs -Stdin $Stdin -IgnoreExitCode:$IgnoreExitCode
}

function Test-HrmComposeRunning {
    param([string]$InstallDir, [string]$StateDir)
    $ps = Invoke-HrmCompose $InstallDir $StateDir @("ps", "--format", "json") -IgnoreExitCode
    if ($ps.ExitCode -ne 0) { return $false }
    try {
        $items = $ps.Stdout | ConvertFrom-Json
        if ($null -eq $items) { return $false }
        $running = @($items | Where-Object { $_.State -eq "running" -or $_.State -like "Up*" })
        return ($running.Count -gt 0)
    }
    catch { return $false }
}

function Start-HrmStack {
    # Сборка (если образов ещё нет) и запуск. Идемпотентно.
    param([string]$InstallDir, [string]$StateDir)
    $build = Invoke-HrmCompose $InstallDir $StateDir @("build", "--pull=false")
    Invoke-HrmCompose $InstallDir $StateDir @("up", "-d", "--remove-orphans") | Out-Null
    Write-HrmLog "info" "Контейнеры пилота запущены (проект $script:ProjectName)."
}

function Stop-HrmStack {
    param([string]$InstallDir, [string]$StateDir)
    Invoke-HrmCompose $InstallDir $StateDir @("down", "--remove-orphans") | Out-Null
    Write-HrmLog "info" "Контейнеры остановлены. Данные (pilot_pgdata, pilot_backups) сохранены."
}

function Test-HrmBackendReady {
    # Бэкенд отвечает на /api/health через фронтенд-прокси на 127.0.0.1.
    param([string]$BaseUrl)
    try {
        $health = Invoke-HrmHttp -Uri "$BaseUrl/api/health"
        return ($health.StatusCode -eq 200)
    }
    catch { return $false }
}

function Test-HrmFrontendReady {
    param([string]$BaseUrl)
    try {
        $page = Invoke-HrmHttp -Uri $BaseUrl
        return ($page.StatusCode -eq 200)
    }
    catch { return $false }
}

function Wait-HrmReady {
    # Ограниченное ожидание готовности: фронтенд + /api/health.
    param([string]$BaseUrl, [int]$TimeoutSeconds = 180)
    $frontendReady = Wait-HrmCondition -Condition { Test-HrmFrontendReady $BaseUrl } -TimeoutSeconds $TimeoutSeconds
    $backendReady = Wait-HrmCondition -Condition { Test-HrmBackendReady $BaseUrl } -TimeoutSeconds $TimeoutSeconds
    if (-not ($frontendReady -and $backendReady)) {
        throw "Приложение не стало готовым за $TimeoutSeconds с (фронтенд: $frontendReady, бэкенд: $backendReady). Запустите diagnostics."
    }
    Write-HrmLog "info" "Приложение готово: $BaseUrl"
}

function Get-HrmMigrationsState {
    # Текущая ревизия схемы из работающего бэкенда (только чтение).
    param([string]$InstallDir, [string]$StateDir)
    $out = Invoke-HrmCompose $InstallDir $StateDir @("exec", "-T", "backend", "alembic", "current") -IgnoreExitCode
    if ($out.ExitCode -ne 0) { return $null }
    $match = [regex]::Match($out.Stdout, "(?m)^([0-9a-f]+)\s+\(head\)")
    if ($match.Success) { return $match.Groups[1].Value }
    return $null
}

function Get-HrmOpsStatus {
    # /ops/status через loopback-прокси (без секретов, без PII).
    param([string]$BaseUrl)
    try {
        $result = Invoke-HrmHttp -Uri "$BaseUrl/api/ops/status"
        # 503 — честный сигнал «БД недоступна»; тело ответа при этом валидно.
        if ($result.StatusCode -eq 200 -or $result.StatusCode -eq 503) { return $result.Body }
        return $null
    }
    catch { return $null }
}
