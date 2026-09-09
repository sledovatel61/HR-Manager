# Секреты пилота: генерация один раз, хранение только в защищённом каталоге
# состояния, без ротации при повторных запусках, без попадания в вывод,
# git, диагностику или URL. Командная строка процессов секретов не содержит:
# в контейнеры они попадают через `docker compose --env-file pilot.env`.

Set-StrictMode -Version 2.0

$script:SecretNames = @(
    "HRM_POSTGRES_PASSWORD",   # 32 hex — пароль БД
    "HRM_SIGNING_KEY",         # 64 hex — подпись сессий/CSRF
    "HRM_BOOTSTRAP_ADMIN_PASSWORD", # 32 hex — запасной bootstrap-пароль (не используется при токене)
    "HRM_BACKUP_KEY",          # 64 hex — ключ шифрования бэкапов (в контейнер как BACKUP_ENC_KEY)
    "HRM_BACKUP_KEY_ID",       # строка — идентификатор ключа бэкапа
    "HRM_EXCHANGE_TOKEN"       # одноразовый токен первого запуска (гасится после claim)
)

function Initialize-HrmStateDir {
    # Каталог состояния с ACL только для текущего пользователя.
    param([string]$StateDir)
    if (-not (Test-Path $StateDir)) {
        New-Item -ItemType Directory -Path $StateDir -Force | Out-Null
        Protect-HrmFile $StateDir $StateDir
    }
    $secretsFile = Get-HrmSecretsFile $StateDir
    if (-not (Test-Path $secretsFile)) {
        Set-HrmJsonFile $StateDir "secrets.json" ([ordered]@{})
    }
    else {
        Protect-HrmFile $StateDir $secretsFile
    }
    return $StateDir
}

function Get-HrmSecretsMap {
    param([string]$StateDir)
    $file = Get-HrmSecretsFile $StateDir
    $data = Get-HrmJsonFile $file
    if ($null -eq $data) { return @{} }
    $map = @{}
    foreach ($name in $script:SecretNames) {
        $property = $data.PSObject.Properties[$name]
        $value = if ($null -ne $property) { $property.Value } else { $null }
        if ($value) { $map[$name] = [string]$value }
    }
    return $map
}

function Get-HrmSecret {
    # Возвращает секрет по имени; создаёт один раз и больше никогда не меняет.
    param([string]$StateDir, [string]$Name)
    if ($script:SecretNames -notcontains $Name) {
        throw "Неизвестный секрет: $Name"
    }
    $map = Get-HrmSecretsMap $StateDir
    if ($map.ContainsKey($Name)) {
        $value = $map[$Name]
        if (-not [string]::IsNullOrEmpty($value)) {
            Register-HrmSecret $value
            return $value
        }
    }
    $value = switch ($Name) {
        "HRM_POSTGRES_PASSWORD" { New-HrmHex 16 }
        "HRM_SIGNING_KEY" { New-HrmHex 32 }
        "HRM_BOOTSTRAP_ADMIN_PASSWORD" { New-HrmHex 16 }
        "HRM_BACKUP_KEY" { New-HrmHex 32 }
        "HRM_BACKUP_KEY_ID" { "pilot-" + (New-HrmHex 4) }
        "HRM_EXCHANGE_TOKEN" { New-HrmHex 16 }
    }
    $file = Get-HrmSecretsFile $StateDir
    $data = Get-HrmJsonFile $file
    $merged = [ordered]@{}
    if ($null -ne $data) {
        foreach ($prop in $data.PSObject.Properties) { $merged[$prop.Name] = $prop.Value }
    }
    $merged[$Name] = $value
    Set-HrmJsonFile $StateDir "secrets.json" $merged
    Register-HrmSecret $value
    return $value
}

function Clear-HrmExchangeToken {
    # После успешного claim токен больше не нужен (строка обмена потреблена
    # атомарно; после создания владельца bootstrap её не воссоздаст).
    param([string]$StateDir)
    $file = Get-HrmSecretsFile $StateDir
    $data = Get-HrmJsonFile $file
    $exchangeProperty = if ($null -ne $data) { $data.PSObject.Properties["HRM_EXCHANGE_TOKEN"] } else { $null }
    if ($null -ne $exchangeProperty -and $exchangeProperty.Value) {
        $merged = [ordered]@{}
        foreach ($prop in $data.PSObject.Properties) { $merged[$prop.Name] = $prop.Value }
        $merged["HRM_EXCHANGE_TOKEN"] = ""
        Set-HrmJsonFile $StateDir "secrets.json" $merged
    }
}

function Write-HrmPilotEnv {
    # Генерирует pilot.env для docker compose. Файл защищён ACL; содержимое
    # никогда не выводится.
    param([string]$StateDir, [string]$ReleaseSha = "", [int]$Port = 0)
    $secrets = @{}
    foreach ($name in @("HRM_POSTGRES_PASSWORD", "HRM_SIGNING_KEY", "HRM_BACKUP_KEY", "HRM_BACKUP_KEY_ID", "HRM_BOOTSTRAP_ADMIN_PASSWORD")) {
        $secrets[$name] = Get-HrmSecret $StateDir $name
    }
    # Одноразовый токен первого запуска: создаётся только если установка ещё
    # не завершена (владельца нет). После создания владельца compose-файл
    # требует НЕПУСТОЕ значение (${HRM_EXCHANGE_TOKEN:?}) — пишем случайный
    # placeholder, который сервер никогда не применит: пользователи уже
    # существуют, обмен закрыт (store_pilot_exchange не срабатывает).
    $state = Get-HrmInstallRecord $StateDir
    if ($null -eq $state -or -not $state.pilot_created) {
        $exchange = Get-HrmSecret $StateDir "HRM_EXCHANGE_TOKEN"
    }
    else {
        $exchange = "retired-" + (New-HrmHex 16)
    }
    $port = Get-HrmPort $Port
    $lines = @(
        ("HRM_POSTGRES_PASSWORD={0}" -f $secrets["HRM_POSTGRES_PASSWORD"]),
        ("HRM_SIGNING_KEY={0}" -f $secrets["HRM_SIGNING_KEY"]),
        ("HRM_BOOTSTRAP_ADMIN_PASSWORD={0}" -f $secrets["HRM_BOOTSTRAP_ADMIN_PASSWORD"]),
        ("HRM_EXCHANGE_TOKEN={0}" -f $exchange),
        ("HRM_BACKUP_KEY={0}" -f $secrets["HRM_BACKUP_KEY"]),
        ("HRM_BACKUP_KEY_ID={0}" -f $secrets["HRM_BACKUP_KEY_ID"]),
        ("HRM_RELEASE_SHA={0}" -f $ReleaseSha),
        ("HRM_PILOT_PORT={0}" -f $port)
    )
    $envFile = Get-HrmEnvFile $StateDir
    Set-Content -Path $envFile -Value $lines -Encoding UTF8
    Protect-HrmFile $StateDir $envFile
    return $envFile
}

function Get-HrmInstallRecord {
    param([string]$StateDir)
    $file = Get-HrmInstalledFile $StateDir
    if (-not (Test-Path $file)) { return $null }
    return Get-HrmJsonFile $file
}

function Set-HrmInstallRecord {
    # Windows PowerShell 5.1 не умеет Add-Member на Hashtable — пересборка.
    param([string]$StateDir, [hashtable]$Fields)
    $record = Get-HrmInstallRecord $StateDir
    $merged = [ordered]@{}
    if ($null -ne $record) {
        foreach ($prop in $record.PSObject.Properties) { $merged[$prop.Name] = $prop.Value }
    }
    foreach ($key in $Fields.Keys) { $merged[$key] = $Fields[$key] }
    Set-HrmJsonFile $StateDir "installed.json" $merged
}
