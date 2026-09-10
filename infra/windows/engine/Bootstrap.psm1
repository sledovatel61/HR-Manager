# Первый запуск: одноразовый loopback-обмен между движком и бэкендом.
# Движок сам НЕ создаёт администратора — он лишь передаёт собранные
# мастером установки данные через /setup/owner/claim и открывает браузер
# с одноразовым тикетом в URL-фрагменте. Создание единственного владельца
# (роль admin + грант pilot_full_access) и выбор пароля происходят на
# сервере через защищённый UI первого запуска.

Set-StrictMode -Version 2.0

function Get-HrmFirstRunInput {
    # Фамилия/роль/часовой пояс из защищённого файла ввода (пишет мастер
    # установки). Если файла нет — интерактивный запрос; неинтерактивно
    # возвращаются безопасные значения по умолчанию (тесты задают файл).
    param([string]$StateDir)
    $file = Get-HrmInputFile $StateDir
    if (Test-Path $file) {
        $data = Get-HrmJsonFile $file
        if ($null -ne $data -and $data.surname) {
            return @{
                Surname = [string]$data.surname
                WorkingMode = [string]$data.working_mode
                Timezone = [string]$data.timezone
            }
        }
    }
    $surname = (Invoke-HrmPrompt -Prompt "Фамилия владельца" -Default "")
    if (-not $surname) { $surname = "Owner" }
    $mode = (Invoke-HrmPrompt -Prompt "Роль (HR | Руководитель | Администратор)" -Default "HR")
    $modeMap = @{ "hr" = "hr"; "руководитель" = "manager"; "администратор" = "admin"; "manager" = "manager"; "admin" = "admin" }
    $workingMode = if ($modeMap.ContainsKey($mode.ToLower())) { $modeMap[$mode.ToLower()] } else { "hr" }
    $timezone = (Invoke-HrmPrompt -Prompt "Часовой пояс" -Default "Europe/Moscow")
    return @{ Surname = $surname; WorkingMode = $workingMode; Timezone = $timezone }
}

function Invoke-HrmFirstRunClaim {
    # POST /setup/owner/claim на loopback с X-Real-IP: 127.0.0.1.
    # Возвращает @{ Ticket = ... } или @{ AlreadyCreated = $true }.
    param([string]$StateDir, [string]$BaseUrl, [string]$Surname, [string]$WorkingMode, [string]$Timezone)
    $token = Get-HrmSecret $StateDir "HRM_EXCHANGE_TOKEN"
    if ([string]::IsNullOrEmpty($token)) { return @{ AlreadyCreated = $true } }
    $body = @{
        exchange_token = $token
        surname = $Surname
        working_mode = $WorkingMode
        timezone = $Timezone
    }
    $headers = @{ "X-Real-IP" = "127.0.0.1" }
    $lastStatus = 0
    # Интервал повтора (тесты ускоряют через HRM_CLAIM_RETRY_SECONDS=0).
    $interval = 5
    if ($env:HRM_CLAIM_RETRY_SECONDS) { $interval = [int]$env:HRM_CLAIM_RETRY_SECONDS }
    # Несколько попыток: бэкенд регистрирует хеш токена при старте; между
    # `up` и готовностью claim может вернуть 403.
    for ($attempt = 1; $attempt -le 12; $attempt++) {
        $response = Invoke-HrmHttp -Uri "$BaseUrl/api/setup/owner/claim" -Method "POST" -Body $body -Headers $headers
        $lastStatus = $response.StatusCode
        if ($response.StatusCode -eq 201 -and $null -ne $response.Body -and $response.Body.ticket) {
            return @{ Ticket = [string]$response.Body.ticket }
        }
        if ($response.StatusCode -eq 409) {
            # Владелец уже создан — обмен закрыт.
            return @{ AlreadyCreated = $true }
        }
        if ($response.StatusCode -eq 404) {
            # Обмен отключён (нет токена в конфигурации бэкенда): повторим
            # после перезапуска, если бэкенд ещё не перечитал env.
            Start-Sleep -Seconds $interval
            continue
        }
        Start-Sleep -Seconds $interval
    }
    throw ("Claim первого запуска не удался (последний статус: {0})." -f $lastStatus)
}

function Start-HrmFirstRun {
    # Полный сценарий первого запуска после установки.
    param([string]$InstallDir, [string]$StateDir, [int]$Port = 0)
    $baseUrl = Get-HrmBaseUrl $Port
    $record = Get-HrmInstallRecord $StateDir
    if ($null -ne $record -and $record.pilot_created) {
        # Repair stale artifacts left by an interrupted/older first-run flow.
        # The database is authoritative: once the owner exists, no raw
        # exchange token or setup URL/input should remain on disk.
        Clear-HrmExchangeToken $StateDir
        foreach ($path in @((Get-HrmInputFile $StateDir), (Get-HrmSetupUrlFile $StateDir))) {
            if (Test-Path $path) { Remove-Item $path -Force }
        }
        $null = Write-HrmPilotEnv $StateDir (Get-HrmReleaseSha $InstallDir $StateDir) $Port
        Invoke-HrmCompose $InstallDir $StateDir @("up", "-d") | Out-Null
        Write-HrmLog "info" "Владелец уже создан — первый запуск не требуется."
        return
    }
    if ($null -eq $record -or -not $record.release_sha) {
        throw "Запись установки отсутствует: сначала выполните -Action install."
    }
    $input = Get-HrmFirstRunInput $StateDir
    $result = Invoke-HrmFirstRunClaim -StateDir $StateDir -BaseUrl $baseUrl `
        -Surname $input.Surname -WorkingMode $input.WorkingMode -Timezone $input.Timezone
    $alreadyCreated = $result.ContainsKey("AlreadyCreated") -and [bool]$result["AlreadyCreated"]
    if ($alreadyCreated) {
        # Обмен закрыт сервером: владелец есть. Фиксируем и убираем токен
        # из env (bootstrap больше не нужен; пользователи уже существуют).
        Set-HrmInstallRecord $StateDir @{ pilot_created = $true }
        Clear-HrmExchangeToken $StateDir
        foreach ($path in @((Get-HrmInputFile $StateDir), (Get-HrmSetupUrlFile $StateDir))) {
            if (Test-Path $path) { Remove-Item $path -Force }
        }
        $null = Write-HrmPilotEnv $StateDir (Get-HrmReleaseSha $InstallDir $StateDir) $Port
        Invoke-HrmCompose $InstallDir $StateDir @("up", "-d") | Out-Null
        Write-HrmLog "info" "Владелец уже существует; первый запуск пропущен."
        return
    }
    $setupUrl = "$baseUrl/#setup=$($result.Ticket)"
    Write-HrmLog "info" "Тикет первого запуска получен; открываю защищённую страницу настройки."
    Invoke-HrmOpenBrowser $setupUrl -StateDir $StateDir
    Set-HrmInstallRecord $StateDir @{ first_run_started_at = (Get-Date).ToString("o") }
    Write-HrmLog "info" "Завершите настройку в открывшемся окне браузера (пароль, часовой пояс, рабочие дни, тихие часы)."
}
