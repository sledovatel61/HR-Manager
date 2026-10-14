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
    # Есть ли ЗАПУЩЕННЫЕ контейнеры проекта (совместимая обёртка над
    # Get-HrmStackState). partial/degraded здесь тоже истина — это осознанно:
    # функция отвечает на вопрос «есть ли что останавливать/опрашивать»
    # (Stop-HrmApp, диагностика), а НЕ на вопрос «приложение готово и
    # принадлежит нам». Для открытия приложения и ярлыка готовность и
    # принадлежность проверяются отдельно (Get-HrmStackState + Wait-HrmReady);
    # считать partial/degraded «запущенным приложением» и открывать браузер
    # запрещено (дефект ярлыка 12.10.2026).
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
    # Свойства объектов docker compose ps читаются через PSObject.Properties:
    # при StrictMode обращение к отсутствующему свойству бросило бы исключение
    # (в JSON разных версий Compose поля могут отличаться).
    param($Container)
    if ($null -eq $Container) { return $false }
    if (-not $Container.PSObject.Properties["State"]) { return $false }
    $state = [string]$Container.State
    if ($state -eq "running" -or $state -like "Up*") { return $true }
    return $false
}

function Test-HrmContainerHealthy {
    # Неизвестное/отсутствующее здоровье не считается ошибкой: не все сервисы
    # пилота имеют healthcheck, и не все версии Compose отдают поле Health.
    param($Container)
    if ($null -eq $Container) { return $true }
    if (-not $Container.PSObject.Properties["Health"]) { return $true }
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
    # СООТВЕТСТВИЕ ОБРАЗОВ — обязанность вызывающего: перед ремонтом
    # Start-HrmStack сверяет метку hrmanager.release_sha со снимком в {app} и
    # пересобирает образы при расхождении. Без этого --force-recreate поднимал
    # бы контейнеры из устаревших образов (дефект 12.10.2026).
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

function Sync-HrmPostgresRolePassword {
    # Только существующий секрет: старые каталоги без файла секретов не меняем.
    # local socket (trust у штатного postgres image), НЕ TCP. Пароль/SQL идут
    # только в stdin; psqlrc, запрос пароля и echo SQL отключены. Ошибки psql
    # не публикуем: они могут содержать SQL с экранированным паролем.
    # database_auth_failed = согласовать роль не удалось, данные не удалялись.
    param([string]$InstallDir, [string]$StateDir)
    try {
        $secrets = Get-HrmSecretsMap $StateDir
        if (-not $secrets.ContainsKey("HRM_POSTGRES_PASSWORD")) {
            return [pscustomobject]@{ ok = $true; state = "skipped"; message = "Секрет БД отсутствует; синхронизация пропущена."; synced = $false }
        }
        $password = Get-HrmSecret $StateDir "HRM_POSTGRES_PASSWORD"
        $literal = $password.Replace("'", "''")
        Register-HrmSecret $literal
        # Не допускаем SQL с паролем в штатные журналы PostgreSQL даже при
        # включённом логировании statement/duration. SET выполняются ДО ALTER.
        $sql = "SET log_statement = 'none'; SET log_min_error_statement = 'panic'; SET log_min_duration_statement = -1; SET log_min_duration_sample = -1; SET standard_conforming_strings = on;`nALTER ROLE hr_manager PASSWORD '" + $literal + "';`n"
        $res = Invoke-HrmCompose $InstallDir $StateDir @("exec", "-T", "db", "psql", "-X", "-w", "-U", "hr_manager", "-d", "hr_manager", "-v", "ON_ERROR_STOP=1") -Stdin $sql -IgnoreExitCode
        if ($res.ExitCode -eq 0) {
            $null = Write-HrmLog "info" "Пароль роли hr_manager согласован с каталогом состояния. Данные в pilot_pgdata сохранены."
            return [pscustomobject]@{ ok = $true; state = "synced"; message = "Пароль роли БД согласован."; synced = $true }
        }
    }
    catch {
        # Не выводим исключение драйвера/psql: оно может включать stdin.
    }
    $message = "database_auth_failed: не удалось согласовать пароль роли hr_manager в томе pilot_pgdata с каталогом состояния (возможны несовпадение пароля или недоступность локального подключения к БД). Данные не удалялись. Создайте отчёт для поддержки; проверьте доступность db и локального сокета PostgreSQL. Не удаляйте том и каталог состояния."
    $null = Write-HrmLog "error" (Redact-HrmText $message)
    return [pscustomobject]@{ ok = $false; state = "database_auth_failed"; message = $message; synced = $false }
}

function Wait-HrmStackAfterPasswordSync {
    # Ограниченное ожидание restart-policy/healthcheck, отдельно от HTTP readiness.
    param([string]$InstallDir, [string]$StateDir, [int]$MaxAttempts = 30)
    $interval = Get-HrmWaitIntervalSeconds
    for ($attempt = 1; $attempt -le $MaxAttempts; $attempt++) {
        $state = Get-HrmStackState -InstallDir $InstallDir -StateDir $StateDir
        if ($state.state -eq "running") {
            return [pscustomobject]@{ ok = $true; state = "running"; message = "Стек восстановлен; данные сохранены." }
        }
        if ($attempt -lt $MaxAttempts) { Start-HrmWaitSleep -Seconds $interval }
    }
    $message = "Пароль БД согласован, но сервисы не стали healthy. Данные не удалялись. Создайте отчёт для поддержки."
    $null = Write-HrmLog "error" $message
    return [pscustomobject]@{ ok = $false; state = "stack_not_running"; message = $message }
}

function Start-HrmStack {
    # Сборка (если нужно) и запуск. Идемпотентно и безопасно для данных:
    # уже запущенный стек с СООТВЕТСТВУЮЩИМИ образами не пересобирается
    # повторно, тома не удаляются никогда.
    # Возвращает @{ ok; state; message }.
    #
    # СООТВЕТСТВИЕ СНИМКУ (дефект 12.10.2026): образы пересобираются не только
    # при absent/stopped. Setup.exe копирует файлы НОВОЙ версии в {app} ДО
    # запуска движка, а откат возвращает старые теги образов, поэтому
    # Repair-HrmStack/`up` могли пересоздать контейнеры из УСТАРЕВШИХ образов:
    # приложение отвечало старой версией либо падало в crash-loop миграций.
    # Теперь перед запуском сверяется метка образа hrmanager.release_sha со
    # снимком в {app} (release.json) и при расхождении образы пересобираются —
    # это и есть приведение к развёрнутому снимку.
    #
    # СХЕМА ↔ ОБРАЗ: до запуска (и повторно после) проверяется, что ревизия базы
    # известна alembic-скриптам образа; положительная несовместимость — понятный
    # отказ state="schema_incompatible" вместо crash-loop backend'а.
    #
    # ОКРУЖЕНИЕ: перед сборкой pilot.env приводится к развёрнутому снимку
    # (release_sha, порт публикации из записи установки) — иначе метка новой
    # сборки осталась бы старой, а публикация вернулась бы на старый порт.
    param([string]$InstallDir, [string]$StateDir)

    $record = Get-HrmInstallRecord $StateDir
    $recordPort = [int](Get-HrmInstallRecordField -Record $record -Field "port" -Default 0)
    if ($recordPort -le 0) { $recordPort = Get-HrmPort }
    $snapshotSha = Get-HrmReleaseSha $InstallDir $StateDir

    # 1) pilot.env ↔ развёрнутый снимок (release_sha и порт публикации).
    if ($snapshotSha) {
        $envSha = Get-HrmEnvFileValue $StateDir "HRM_RELEASE_SHA"
        $envPortText = Get-HrmEnvFileValue $StateDir "HRM_PILOT_PORT"
        $envPort = 0
        [void][int]::TryParse($envPortText, [ref]$envPort)
        if ($envSha -ne $snapshotSha -or $envPort -ne $recordPort) {
            $null = Write-HrmPilotEnv $StateDir $snapshotSha $recordPort
            Write-HrmLog "info" ("Окружение пилота приведено к развёрнутому снимку (версия {0}, порт {1})." -f $snapshotSha, $recordPort)
        }
    }

    # 2) Образ ↔ снимок.
    $imageMatch = Test-HrmImagesMatchSnapshot -InstallDir $InstallDir -StateDir $StateDir
    $imageStale = [bool]($imageMatch.known -and $imageMatch.stale)
    $current = Get-HrmStackState -InstallDir $InstallDir -StateDir $StateDir
    if ($current.state -eq "running" -and -not $imageStale) {
        Write-HrmLog "info" "Контейнеры HR Manager уже запущены — пересборка не требуется."
        return [pscustomobject]@{ ok = $true; state = "running"; message = "Контейнеры уже запущены." }
    }
    if ($imageStale) {
        $imageLabel = if ($imageMatch.image_sha) { $imageMatch.image_sha } else { "метка версии отсутствует" }
        Write-HrmLog "info" ("Образы не соответствуют развёрнутому снимку (в образе: {0}; в снимке: {1}) — пересобираю образы и привожу приложение к установленной версии. Данные (тома) не изменяются." -f $imageLabel, $imageMatch.snapshot_sha)
    }
    if ($current.state -eq "absent" -or $current.state -eq "stopped" -or $imageStale) {
        Invoke-HrmCompose $InstallDir $StateDir @("build", "--pull=false") | Out-Null
    }

    $passwordSynced = $false
    # partial/degraded не означает, что db запущен: после сбоя машины может
    # работать только backend. В этом случае сначала обычный up/repair,
    # а не exec на остановленном контейнере с ложным database_auth_failed.
    if (($current.state -eq "partial" -or $current.state -eq "degraded") -and
        (Test-HrmDbContainerRunning -InstallDir $InstallDir -StateDir $StateDir)) {
        $sync = Sync-HrmPostgresRolePassword -InstallDir $InstallDir -StateDir $StateDir
        if (-not $sync.ok) { return $sync }
        $passwordSynced = $sync.synced
    }

    # 3) Схема ↔ образ ДО запуска контейнеров приложения.
    if ($current.state -eq "stopped" -or $current.state -eq "partial" -or $current.state -eq "degraded") {
        if ($current.state -eq "stopped") { Start-HrmDbForSchemaCheck -InstallDir $InstallDir -StateDir $StateDir | Out-Null }
        $compat = Test-HrmSchemaCompatibleWithImage -InstallDir $InstallDir -StateDir $StateDir
        if ($compat.checked -and -not $compat.compatible) {
            Write-HrmLog "error" $compat.message
            return [pscustomobject]@{ ok = $false; state = "schema_incompatible"; message = $compat.message }
        }
    }

    if ($current.state -eq "partial" -or $current.state -eq "degraded") {
        $repaired = Repair-HrmStack -InstallDir $InstallDir -StateDir $StateDir
        if ($repaired.state -eq "running") {
            return [pscustomobject]@{ ok = $true; state = "running"; message = "Стек восстановлен без потери данных." }
        }
        # Полный up может собрать отсутствующие сервисы, не удаляя тома.
    }
    $up = Invoke-HrmCompose $InstallDir $StateDir @("up", "-d", "--remove-orphans") -IgnoreExitCode
    if ($up.ExitCode -ne 0) {
        # Сохраняем причину ДО любого возврата/repair, включая partial/degraded.
        # Предпусковой sync мог быть пропущен: регистрируем существующие
        # секреты (и SQL-escaped вид) без генерации и без зависимости от sync.
        $upError = "Подробности stderr скрыты: не удалось загрузить секреты для редакции."
        try {
            foreach ($value in (Get-HrmSecretsMap $StateDir).Values) {
                Register-HrmSecret $value
                Register-HrmSecret ($value.Replace("'", "''"))
            }
            $upError = Redact-HrmText ([string]$up.Stderr).Trim()
        } catch { }
        Write-HrmLog "error" ("Запуск контейнеров не удался: " + $upError)
    }
    # Даже неуспешный up мог поднять db и оставить backend unhealthy:
    # dependency failed to start — именно этот путь был в отчёте владельца.
    $after = Get-HrmStackState -InstallDir $InstallDir -StateDir $StateDir
    if ($up.ExitCode -ne 0 -and $after.state -ne "partial" -and $after.state -ne "degraded") {
        $message = "Не удалось запустить контейнеры. Нажмите «Создать отчёт для поддержки»."
        return [pscustomobject]@{ ok = $false; state = "failed"; message = $message }
    }
    if ($after.state -eq "partial" -or $after.state -eq "degraded") {
        # up тоже мог оставить db остановленным/несозданным. Это ошибка
        # запуска контейнера, а не доказательство неверного пароля роли.
        if (Test-HrmDbContainerRunning -InstallDir $InstallDir -StateDir $StateDir) {
            $sync = Sync-HrmPostgresRolePassword -InstallDir $InstallDir -StateDir $StateDir
            if (-not $sync.ok) { return $sync }
            $passwordSynced = $passwordSynced -or $sync.synced
        }
        if ($up.ExitCode -ne 0 -and -not $passwordSynced) {
            return [pscustomobject]@{ ok = $false; state = "failed"; message = "Не удалось запустить контейнеры. Создайте отчёт для поддержки." }
        }
        # Контейнеры уже подняты: база запущена, и рассогласование «схема ↔
        # образ» можно установить точно. Это защита от crash-loop: backend
        # перезапускается бесконечно, а человек видит только «не запускается».
        $compat = Test-HrmSchemaCompatibleWithImage -InstallDir $InstallDir -StateDir $StateDir
        if ($compat.checked -and -not $compat.compatible) {
            # Останавливаем именно перезапускающиеся сервисы (данные и тома
            # целы): понятный отказ вместо бесконечного crash-loop.
            Invoke-HrmCompose $InstallDir $StateDir @("stop", "backend", "worker", "backup") -IgnoreExitCode | Out-Null
            Write-HrmLog "error" $compat.message
            return [pscustomobject]@{ ok = $false; state = "schema_incompatible"; message = $compat.message }
        }
        $after = Repair-HrmStack -InstallDir $InstallDir -StateDir $StateDir
    }
    if ($passwordSynced) {
        return (Wait-HrmStackAfterPasswordSync -InstallDir $InstallDir -StateDir $StateDir)
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
    # ПРИНАДЛЕЖНОСТЬ, а не просто HTTP 200 (дефект 12.10.2026): любой чужой
    # 200 — старый development-стек HR Manager (APP_ENV=development) или
    # сторонняя программа, случайно оказавшаяся на порту, — готовностью пилота
    # НЕ считается. /api/health пилота обязан вернуть JSON с environment=pilot.
    # Запрос идёт ЧЕРЕЗ фронтенд-прокси, поэтому успех подтверждает и то, что
    # отвечает именно стек этой установки, а не отдельное приложение на порту.
    param([string]$BaseUrl, [string]$ExpectedEnvironment = "pilot")
    try {
        $health = Invoke-HrmHttp -Uri "$BaseUrl/api/health"
    }
    catch { return $false }
    if ($health.StatusCode -ne 200) { return $false }
    $body = $health.Body
    if ($null -eq $body) { return $false }
    if ($body -is [string]) {
        try { $body = ($body | ConvertFrom-Json) } catch { return $false }
    }
    if ($null -eq $body -or $body -is [string]) { return $false }
    if (-not $body.PSObject.Properties["environment"]) { return $false }
    return ([string]$body.environment -eq $ExpectedEnvironment)
}

function Test-HrmFrontendReady {
    # Страница фронтенда отдаётся (HTTP 200). Принадлежность и версию
    # подтверждает Test-HrmBackendReady/Get-HrmAppIdentity через тот же
    # loopback-прокси: отдающий страницу веб-сервер и API — один стек.
    param([string]$BaseUrl)
    try {
        $page = Invoke-HrmHttp -Uri $BaseUrl
        return ($page.StatusCode -eq 200)
    }
    catch { return $false }
}

function Get-HrmAppIdentity {
    # Идентичность приложения, отвечающего на BaseUrl (только чтение):
    # окружение, release_sha в работе, версия, ревизии миграций. Используется
    # для проверки принадлежности ответа целевому пилоту и ожидаемому релизу —
    # сверять чужой/старый стек с ожидаемой версией иначе нечем.
    param([string]$BaseUrl)
    $result = [pscustomobject]@{
        available = $false; environment = ""; release_sha = ""; version = ""; service = ""
        migrations_current = ""; migrations_expected = ""; migrations_ok = $null
    }
    $ops = Get-HrmOpsStatus $BaseUrl
    if ($null -eq $ops) { return $result }
    if ($ops -is [string]) {
        # 503 приходит сырой строкой — пробуем разобрать JSON (тело валидно).
        try { $ops = ($ops | ConvertFrom-Json) } catch { return $result }
    }
    if ($null -eq $ops -or $ops -is [string]) { return $result }
    $result.available = $true
    if ($ops.PSObject.Properties["environment"] -and $null -ne $ops.environment) { $result.environment = ([string]$ops.environment).Trim() }
    if ($ops.PSObject.Properties["release_sha"] -and $null -ne $ops.release_sha) { $result.release_sha = ([string]$ops.release_sha).Trim() }
    if ($ops.PSObject.Properties["version"] -and $null -ne $ops.version) { $result.version = ([string]$ops.version).Trim() }
    if ($ops.PSObject.Properties["service"] -and $null -ne $ops.service) { $result.service = ([string]$ops.service).Trim() }
    if ($ops.PSObject.Properties["migrations"] -and $null -ne $ops.migrations) {
        if ($ops.migrations.PSObject.Properties["current_revision"] -and $null -ne $ops.migrations.current_revision) { $result.migrations_current = ([string]$ops.migrations.current_revision).Trim() }
        if ($ops.migrations.PSObject.Properties["expected_revision"] -and $null -ne $ops.migrations.expected_revision) { $result.migrations_expected = ([string]$ops.migrations.expected_revision).Trim() }
        if ($ops.migrations.PSObject.Properties["ok"] -and $null -ne $ops.migrations.ok) { $result.migrations_ok = [bool]$ops.migrations.ok }
    }
    return $result
}

function Format-HrmAppIdentityProblem {
    # Понятное объяснение, ПОЧЕМУ ответ на порту не принят за наш пилот
    # (чужое приложение / другая версия / чужой релиз). Без технических команд.
    param($Identity, [string]$ExpectedReleaseSha = "")
    if (-not $Identity.available) {
        return "на порту не отвечает приложение HR Manager (нет ответа о состоянии приложения)"
    }
    if ($Identity.environment -ne "pilot") {
        return ("на порту отвечает ДРУГОЕ приложение HR Manager (окружение «{0}», не «pilot») — вероятно, старый тестовый стек или другая установка; оно не является целевым пилотом" -f $Identity.environment)
    }
    if ($ExpectedReleaseSha -and $Identity.release_sha -ne $ExpectedReleaseSha) {
        $actual = if ($Identity.release_sha) { $Identity.release_sha } else { "не сообщена" }
        return ("в работе ДРУГАЯ версия приложения (release_sha: {0}), ожидалась {1}" -f $actual, $ExpectedReleaseSha)
    }
    return "приложение не подтвердило принадлежность целевой установке"
}

function Wait-HrmReady {
    # Ограниченное ожидание готовности: фронтенд + /api/health пилота, и — при
    # известном ожидаемом релизе — принадлежность ответа целевому пилоту И
    # ожидаемому release_sha. Чужой HTTP 200 или старый релиз готовностью не
    # считаются: браузер на такое открывать нельзя (дефект 12.10.2026).
    param([string]$BaseUrl, [int]$TimeoutSeconds = 180, [string]$ExpectedReleaseSha = "")
    $interval = Get-HrmWaitIntervalSeconds 3
    $frontendReady = Wait-HrmCondition -Condition { Test-HrmFrontendReady $BaseUrl } -TimeoutSeconds $TimeoutSeconds -IntervalSeconds $interval
    $backendReady = Wait-HrmCondition -Condition { Test-HrmBackendReady $BaseUrl } -TimeoutSeconds $TimeoutSeconds -IntervalSeconds $interval
    if (-not ($frontendReady -and $backendReady)) {
        $identity = Get-HrmAppIdentity $BaseUrl
        $problem = Format-HrmAppIdentityProblem -Identity $identity -ExpectedReleaseSha $ExpectedReleaseSha
        throw ("Приложение не стало готовым за {0} с (фронтенд: {1}, бэкенд: {2}): {3}. Запустите diagnostics или завершите установку заново — браузер не открывается, чтобы не показать чужое приложение." -f $TimeoutSeconds, $frontendReady, $backendReady, $problem)
    }
    if ($ExpectedReleaseSha) {
        # Отдельное ограниченное окно: окружение пилота уже переписано под
        # развёрнутый снимок, совпадение наступает почти сразу; если версия не
        # совпала за окно — это старая/чужая сборка, а не «ещё не успело».
        $ownershipTimeout = [Math]::Min($TimeoutSeconds, 60)
        $owned = Wait-HrmCondition -Condition {
            $identity = Get-HrmAppIdentity $BaseUrl
            return ($identity.available -and $identity.environment -eq "pilot" -and $identity.release_sha -eq $ExpectedReleaseSha)
        } -TimeoutSeconds $ownershipTimeout -IntervalSeconds $interval
        if (-not $owned) {
            $identity = Get-HrmAppIdentity $BaseUrl
            $problem = Format-HrmAppIdentityProblem -Identity $identity -ExpectedReleaseSha $ExpectedReleaseSha
            throw ("Не подтверждена принадлежность приложения на {0} ожидаемому релизу: {1}. Данные не изменялись; завершите установку/обновление (Setup.exe) и повторите — браузер не открывается, чтобы не показать чужое или устаревшее приложение." -f $BaseUrl, $problem)
        }
    }
    Write-HrmLog "info" ("Приложение готово: {0}" -f $BaseUrl)
}

function Get-HrmMigrationsHead {
    # Ожидаемая голова миграций из РАЗВЁРНУТОГО образа backend (alembic heads).
    # Зашитая константа головы запрещена: она расходится с релизом.
    # Работающий backend — источник точный (это код в работе); если backend ещё
    # не запущен (проверка ДО запуска), голова читается НЕПОСРЕДСТВЕННО из
    # образа одноразовым контейнером (alembic heads в образе — см.
    # Get-HrmImageMigrationRevisions). Обе ветки возвращают одно и то же —
    # голову РАЗВЁРНУТОГО образа, а не константу.
    param([string]$InstallDir, [string]$StateDir)
    $out = Invoke-HrmCompose $InstallDir $StateDir @("exec", "-T", "backend", "alembic", "heads") -IgnoreExitCode
    if ($out.ExitCode -eq 0) {
        $match = [regex]::Match($out.Stdout, "(?m)^\s*([0-9a-zA-Z_]+)\s*(\(head\))?")
        if ($match.Success) { return $match.Groups[1].Value }
    }
    $fromImage = Get-HrmImageMigrationRevisions
    if ($null -ne $fromImage -and $fromImage.head) { return $fromImage.head }
    return $null
}

function Get-HrmImageMigrationRevisions {
    # Ревизии миграций, которые знает ОБРАЗ backend, и его голова. Читаются
    # одноразовым контейнером (docker run --rm <image> alembic history): команда
    # не подключается к базе и не запускает приложение — только перечисляет
    # скрипты alembic, вшитые в образ. Нужно, чтобы ДО запуска контейнеров
    # понять, знает ли образ ту ревизию, что стоит в базе (см.
    # Test-HrmSchemaCompatibleWithImage).
    # Возвращает @{ revisions; head } либо $null, если образ недоступен.
    param([string]$Image = "hr-manager-pilot-backend:pilot")
    $out = Invoke-HrmDocker -Arguments @("run", "--rm", $Image, "alembic", "history") -IgnoreExitCode
    if ($out.ExitCode -ne 0 -or -not $out.Stdout) { return $null }
    $revisions = @()
    $head = ""
    foreach ($line in ($out.Stdout -split "`r?`n")) {
        $trimmed = $line.Trim()
        if (-not $trimmed -or $trimmed -notmatch "->") { continue }
        $parts = $trimmed -split "->"
        if ($parts.Count -lt 2) { continue }
        $left = (($parts[0] -split ",")[0]).Trim()
        $rightRaw = $parts[1]
        $isHead = ($rightRaw -match "\(head\)")
        $right = (($rightRaw -replace "\(head\)", "" -replace "\(branchpoint\)", "") -split ",")[0].Trim()
        foreach ($token in @($left, $right)) {
            if (-not $token -or $token -eq "<base>") { continue }
            if ($revisions -notcontains $token) { $revisions += $token }
        }
        if ($isHead -and -not $head -and $right) { $head = $right }
    }
    if ($revisions.Count -eq 0) { return $null }
    return [pscustomobject]@{ revisions = $revisions; head = $head }
}

function Get-HrmImageReleaseLabel {
    # Метка hrmanager.release_sha образа backend: её ставит сборка из
    # HRM_RELEASE_SHA pilot.env (build.labels в infra/compose.pilot.yml). По ней
    # видно, ИЗ КАКОГО снимка собран образ — до запуска контейнеров.
    # "" — образ отсутствует или собран предыдущей версией движка без метки.
    param([string]$Image = "hr-manager-pilot-backend:pilot")
    $format = "{{ index .Config.Labels ""hrmanager.release_sha"" }}"
    $inspect = Invoke-HrmDocker -Arguments @("image", "inspect", "-f", $format, $Image) -IgnoreExitCode
    if ($inspect.ExitCode -ne 0) { return "" }
    $value = ([string]$inspect.Stdout).Trim()
    if ($value -eq "<no value>") { return "" }
    return $value
}

function Test-HrmImagesMatchSnapshot {
    # Соответствие образов РАЗВЁРНУТОМУ снимку (release_sha в каталоге установки).
    # known = $false — сравнить нечем (нет release.json/метки): пересборка по
    # догадке не запускается. stale = $true — образ собран из другого снимка:
    # это и есть рассогласование «снимок ↔ образ», из-за которого Setup.exe
    # (копирует файлы ДО движка) показывал старое приложение, а откат возвращал
    # старый образ поверх новых файлов.
    param([string]$InstallDir, [string]$StateDir)
    $snapshotSha = Get-HrmReleaseSha $InstallDir $StateDir
    $imageSha = Get-HrmImageReleaseLabel
    $result = [pscustomobject]@{ known = $false; stale = $false; snapshot_sha = $snapshotSha; image_sha = $imageSha }
    if (-not $snapshotSha) { return $result }
    $result.known = $true
    $result.stale = ($imageSha -ne $snapshotSha)
    return $result
}

function Test-HrmDbContainerRunning {
    # Контейнер базы данных проекта запущен? (Чтение состояния схемы возможно
    # только с работающей базой; тома при этом не изменяются.)
    param([string]$InstallDir, [string]$StateDir)
    $containers = Get-HrmComposeContainers -InstallDir $InstallDir -StateDir $StateDir
    if ($null -eq $containers) { return $false }
    foreach ($container in @($containers)) {
        $name = ""
        if ($null -ne $container -and $container.PSObject.Properties["Name"] -and $null -ne $container.Name) { $name = [string]$container.Name }
        if ($name -notmatch "(^|[-_])db([-_]|\d|$)") { continue }
        if (Test-HrmContainerRunning $container) { return $true }
    }
    return $false
}

function Get-HrmDbSchemaRevision {
    # Ревизия схемы из базы пилота — ТОЛЬКО ЧТЕНИЕ (контейнер db, psql SELECT).
    # Возвращает: "fresh" — база без alembic_version (чистая, миграции впереди);
    # ""    — прочитать не удалось (база не запущена/не отвечает);
    # иначе — идентификатор ревизии.
    param([string]$InstallDir, [string]$StateDir, [int]$MaxAttempts = 20)
    $interval = Get-HrmWaitIntervalSeconds 3
    for ($attempt = 1; $attempt -le $MaxAttempts; $attempt++) {
        $out = Invoke-HrmCompose $InstallDir $StateDir @("exec", "-T", "db", "psql", "-U", "hr_manager", "-d", "hr_manager", "-tAc", "SELECT version_num FROM alembic_version") -IgnoreExitCode
        if ($out.ExitCode -eq 0) {
            $revision = ([string]$out.Stdout).Trim()
            if (-not $revision) { return "fresh" }
            return (($revision -split "`r?`n")[0]).Trim()
        }
        $errorText = [string]$out.Stderr
        if ($errorText -match "alembic_version" -and ($errorText -match "does not exist" -or $errorText -match "не существует")) {
            # Таблицы ревизий нет: чистая база, миграции создаст backend.
            return "fresh"
        }
        if ($errorText -match "is not running" -or $errorText -match "No such service" -or $errorText -match "no configuration file") {
            # База не запущена — ждать нечего.
            return ""
        }
        if ($attempt -lt $MaxAttempts) { Start-HrmWaitSleep -Seconds $interval }
    }
    return ""
}

function Test-HrmSchemaCompatibleWithImage {
    # СХЕМА ↔ ОБРАЗ до запуска контейнеров: ревизия базы обязана быть известна
    # alembic-скриптам образа. Иначе backend падает на старте с «Can't locate
    # revision identified by ...» и уходит в бесконечный перезапуск — ровно то,
    # что видели 12.10.2026 (база обновлена более новым образом, а развёрнут
    # старый снимок). Здесь это выясняется ДО запуска: понятный отказ вместо
    # crash-loop. Блокирует запуск только ПОЛОЖИТЕЛЬНО установленная
    # несовместимость (checked=true, compatible=false); любая неопределённость
    # (нет тома, база не читается, образ недоступен) — checked=false и не
    # блокирует: запуск продолжается, а рассогласование ловит smoke/диагностика.
    param([string]$InstallDir, [string]$StateDir)
    $result = [pscustomobject]@{ checked = $false; compatible = $true; db_revision = ""; image_head = ""; message = "" }
    $volume = Invoke-HrmDocker -Arguments @("volume", "inspect", ((Get-HrmProjectName) + "_pilot_pgdata")) -IgnoreExitCode
    if ($volume.ExitCode -ne 0) {
        # Тома данных нет: база будет создана заново, рассогласования быть не может.
        $result.checked = $true
        return $result
    }
    if (-not (Test-HrmDbContainerRunning -InstallDir $InstallDir -StateDir $StateDir)) {
        # Базу не читаем, не запуская контейнер: пусть решает вызывающий
        # (Start-HrmStack поднимает только контейнер базы для проверки).
        return $result
    }
    $dbRevision = Get-HrmDbSchemaRevision -InstallDir $InstallDir -StateDir $StateDir
    if (-not $dbRevision) { return $result }
    if ($dbRevision -eq "fresh") {
        $result.checked = $true
        return $result
    }
    $known = Get-HrmImageMigrationRevisions
    if ($null -eq $known) { return $result }
    $result.checked = $true
    $result.db_revision = $dbRevision
    $result.image_head = $known.head
    if ($known.revisions -contains $dbRevision) { return $result }
    $result.compatible = $false
    $result.message = ("База данных HR Manager находится на ревизии схемы {0}, которая неизвестна установленному снимку приложения (голова миграций образа: {1}). Запуск ОСТАНОВЛЕН до старта контейнеров: backend не смог бы начать работу (ошибка «Can't locate revision identified by '{0}'») и попал бы в бесконечный перезапуск. Это означает, что базу обновила более новая версия приложения, а сейчас развёрнут более старый снимок. Данные не изменялись и не удалялись. Действия: установите версию приложения, поддерживающую ревизию {0} (Setup.exe новой версии), либо восстановите базу из проверенной резервной копии (docs/backup-and-restore.md)." -f $dbRevision, $result.image_head)
    return $result
}

function Start-HrmDbForSchemaCheck {
    # Поднять ТОЛЬКО контейнер базы данных, чтобы прочитать ревизию схемы (том
    # данных не изменяется; приложение не запускается). Нужно для проверки
    # «схема ↔ образ» при остановленном стеке: контейнеры были созданы раньше,
    # значит база уже инициализирована, и наполненный том может быть обновлён
    # более новой версией.
    param([string]$InstallDir, [string]$StateDir)
    if (Test-HrmDbContainerRunning -InstallDir $InstallDir -StateDir $StateDir) { return $true }
    $volume = Invoke-HrmDocker -Arguments @("volume", "inspect", ((Get-HrmProjectName) + "_pilot_pgdata")) -IgnoreExitCode
    if ($volume.ExitCode -ne 0) { return $false }
    Write-HrmLog "info" "Проверяем совместимость схемы базы с образами — временно запускаем только контейнер базы данных (данные не изменяются)…"
    $up = Invoke-HrmCompose $InstallDir $StateDir @("up", "-d", "db") -IgnoreExitCode
    if ($up.ExitCode -ne 0) { return $false }
    $interval = Get-HrmWaitIntervalSeconds 3
    $attempts = 0
    while ($attempts -lt 20) {
        $attempts++
        if (Test-HrmDbContainerRunning -InstallDir $InstallDir -StateDir $StateDir) { return $true }
        Start-HrmWaitSleep -Seconds $interval
    }
    return $false
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
    # /ops/status через loopback-прокси (вывод проходит редакцию секретов).
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
