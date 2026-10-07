# Работа с Docker Compose: единый проект hr-manager-pilot, запуск/остановка/
# статус/готовность. Всё идёт через docker CLI (Invoke-HrmExternal) и
# env-файл из защищённого каталога состояния.

Set-StrictMode -Version 2.0

$script:ProjectName = "hr-manager-pilot"

function Get-HrmProjectName {
    # Стабильное имя проекта пилота. Задаётся функцией, чтобы другие модули не
    # зависели от переменной чужой области видимости.
    return "hr-manager-pilot"
}

function Get-HrmComposeFiles {
    param([string]$InstallDir)
    return @(
        (Join-Path $InstallDir "infra\docker-compose.yml"),
        (Join-Path $InstallDir "infra\compose.pilot.yml")
    )
}

function Get-HrmComposeArgs {
    # Общие аргументы compose: стабильное имя проекта + env-файл состояния.
    param([string]$InstallDir, [string]$StateDir)
    $composeArgs = @("compose")
    $composeArgs += ("--project-name", $script:ProjectName)
    $envFile = Get-HrmEnvFile $StateDir
    if (Test-Path $envFile) { $composeArgs += ("--env-file", $envFile) }
    foreach ($composeFile in (Get-HrmComposeFiles $InstallDir)) {
        $composeArgs += ("-f", $composeFile)
    }
    return , $composeArgs
}

function Invoke-HrmCompose {
    param([string]$InstallDir, [string]$StateDir, [string[]]$Command = @(), [string]$Stdin = "", [switch]$IgnoreExitCode)
    $composeArgs = Get-HrmComposeArgs $InstallDir $StateDir
    $composeArgs += $Command
    return Invoke-HrmDocker -Arguments $composeArgs -Stdin $Stdin -IgnoreExitCode:$IgnoreExitCode
}

function Test-HrmComposeRunning {
    # Есть ли запущенные контейнеры проекта (совместимая обёртка над Get-HrmStackState).
    param([string]$InstallDir, [string]$StateDir)
    try {
        $state = Get-HrmStackState -InstallDir $InstallDir -StateDir $StateDir
    }
    catch { return $false }
    return ($state.state -eq "running" -or $state.state -eq "partial" -or $state.state -eq "degraded")
}

function Get-HrmComposeContainers {
    # Список контейнеров проекта (включая остановленные) в виде объектов.
    param([string]$InstallDir, [string]$StateDir)
    $ps = Invoke-HrmCompose $InstallDir $StateDir @("ps", "-a", "--format", "json") -IgnoreExitCode
    if ($ps.ExitCode -ne 0) { return $null }
    $items = @()
    try {
        try { $items = @($ps.Stdout | ConvertFrom-Json -ErrorAction Stop) }
        catch {
            foreach ($line in ($ps.Stdout -split "`r?`n")) {
                if (-not [string]::IsNullOrWhiteSpace($line)) {
                    $items += ($line | ConvertFrom-Json -ErrorAction Stop)
                }
            }
        }
    }
    catch { return $null }
    return @($items | Where-Object { $null -ne $_ })
}

function Test-HrmContainerRunning {
    param($Container)
    $state = [string]$Container.State
    if ($state -eq "running" -or $state -like "Up*") { return $true }
    return $false
}

function Test-HrmContainerHealthy {
    # Неизвестное/отсутствующее здоровье не считается ошибкой: не все сервисы
    # пилота имеют healthcheck.
    param($Container)
    $health = [string]$Container.Health
    if (-not $health -or $health -eq "<none>" -or $health -eq "unknown") { return $true }
    return ($health -eq "healthy")
}

function Get-HrmStackState {
    # Состояния стека: absent | stopped | partial | running | degraded | unknown.
    # Различает «уже запущен», «частично запущен», «зависший/unhealthy» и
    # «сломан» — от этого зависит, что делать дальше (никогда не удаляем тома).
    param([string]$InstallDir, [string]$StateDir)
    $containers = Get-HrmComposeContainers -InstallDir $InstallDir -StateDir $StateDir
    if ($null -eq $containers) {
        # Compose недоступен (нет Docker/движок не отвечает).
        return [pscustomobject]@{ state = "unknown"; total = 0; running = 0; unhealthy = 0; message = "Не удалось получить состояние контейнеров." }
    }
    $items = @($containers)
    if ($items.Count -eq 0) {
        return [pscustomobject]@{ state = "absent"; total = 0; running = 0; unhealthy = 0; message = "Контейнеры ещё не созданы." }
    }
    $running = @($items | Where-Object { Test-HrmContainerRunning $_ })
    $unhealthy = @($running | Where-Object { -not (Test-HrmContainerHealthy $_) })
    if ($running.Count -eq 0) {
        return [pscustomobject]@{ state = "stopped"; total = $items.Count; running = 0; unhealthy = 0; message = "Контейнеры остановлены." }
    }
    if ($running.Count -lt $items.Count) {
        return [pscustomobject]@{
            state = "partial"; total = $items.Count; running = $running.Count; unhealthy = $unhealthy.Count
            message = ("Запущена только часть сервисов ({0} из {1})." -f $running.Count, $items.Count)
        }
    }
    if ($unhealthy.Count -gt 0) {
        return [pscustomobject]@{
            state = "degraded"; total = $items.Count; running = $running.Count; unhealthy = $unhealthy.Count
            message = ("Часть сервисов работает нестабильно ({0})." -f $unhealthy.Count)
        }
    }
    return [pscustomobject]@{ state = "running"; total = $items.Count; running = $running.Count; unhealthy = 0; message = "Все сервисы запущены." }
}

function Repair-HrmStack {
    # Ремонт частично запущенного/нездорового стека БЕЗ удаления томов:
    # пересоздаём только то, что не работает. `down -v` и удаление volume здесь
    # невозможны по построению.
    param([string]$InstallDir, [string]$StateDir)
    $before = Get-HrmStackState -InstallDir $InstallDir -StateDir $StateDir
    if ($before.state -ne "partial" -and $before.state -ne "degraded") {
        return $before
    }
    Write-HrmLog "info" ("Стек запущен не полностью ({0}) — перезапускаем только незапущенные сервисы…" -f $before.state)
    $recreate = Invoke-HrmCompose $InstallDir $StateDir @("up", "-d", "--remove-orphans", "--force-recreate") -IgnoreExitCode
    if ($recreate.ExitCode -ne 0) {
        Write-HrmLog "warn" "Пересоздание не прошло — пробуем обычный запуск."
        Invoke-HrmCompose $InstallDir $StateDir @("up", "-d", "--remove-orphans") | Out-Null
    }
    $after = Get-HrmStackState -InstallDir $InstallDir -StateDir $StateDir
    return $after
}

function Start-HrmStack {
    # Сборка (если нужно) и запуск. Идемпотентно и безопасно для данных:
    # уже запущенный стек не пересобирается повторно, томa не удаляются.
    # Возвращает @{ ok; state; message }.
    param([string]$InstallDir, [string]$StateDir)
    $current = Get-HrmStackState -InstallDir $InstallDir -StateDir $StateDir
    if ($current.state -eq "running") {
        Write-HrmLog "info" "Контейнеры HR Manager уже запущены — пересборка не требуется."
        return [pscustomobject]@{ ok = $true; state = "running"; message = "Контейнеры уже запущены." }
    }
    if ($current.state -eq "partial" -or $current.state -eq "degraded") {
        $repaired = Repair-HrmStack -InstallDir $InstallDir -StateDir $StateDir
        if ($repaired.state -eq "running") {
            return [pscustomobject]@{ ok = $true; state = "running"; message = "Стек восстановлен без потери данных." }
        }
        # Полный up может собрать отсутствующие сервисы, не удаляя тома.
    }
    if ($current.state -eq "absent" -or $current.state -eq "stopped") {
        Invoke-HrmCompose $InstallDir $StateDir @("build", "--pull=false") | Out-Null
    }
    $up = Invoke-HrmCompose $InstallDir $StateDir @("up", "-d", "--remove-orphans") -IgnoreExitCode
    if ($up.ExitCode -ne 0) {
        $message = "Не удалось запустить контейнеры. Нажмите «Создать отчёт для поддержки»."
        Write-HrmLog "error" (Redact-HrmText ("Запуск контейнеров не удался: " + $up.Stderr.Trim()))
        return [pscustomobject]@{ ok = $false; state = "failed"; message = $message }
    }
    $after = Get-HrmStackState -InstallDir $InstallDir -StateDir $StateDir
    if ($after.state -eq "partial" -or $after.state -eq "degraded") {
        $after = Repair-HrmStack -InstallDir $InstallDir -StateDir $StateDir
    }
    Write-HrmLog "info" "Контейнеры пилота запущены (проект $script:ProjectName)."
    return [pscustomobject]@{ ok = $true; state = $after.state; message = "Контейнеры запущены." }
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

function Get-HrmMigrationsHead {
    # Ожидаемая голова миграций из РАЗВЁРНУТОГО образа backend (alembic heads).
    # Зашитая константа головы запрещена: она расходится с релизом.
    param([string]$InstallDir, [string]$StateDir)
    $out = Invoke-HrmCompose $InstallDir $StateDir @("exec", "-T", "backend", "alembic", "heads") -IgnoreExitCode
    if ($out.ExitCode -ne 0) { return $null }
    $match = [regex]::Match($out.Stdout, "(?m)^\s*([0-9a-zA-Z_]+)\s*(\(head\))?")
    if ($match.Success) { return $match.Groups[1].Value }
    return $null
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

function Remove-HrmPilotDataVolume {
    # Explicit allowlist, derived from the fixed Compose project and the volume
    # key in compose.pilot.yml. Never enumerate all project volumes or use -f:
    # pilot_backups and volumes of other projects must survive ordinary purge.
    $name = $script:ProjectName + "_pilot_pgdata"
    $result = Invoke-HrmDocker -Arguments @("volume", "rm", $name) -IgnoreExitCode
    if ($result.ExitCode -ne 0) { throw "Том данных не удалён (занят или недоступен). Бэкапы сохранены." }
}
