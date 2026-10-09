# Секреты пилота: генерация один раз, хранение только в защищённом каталоге
# состояния, без ротации при повторных запусках, без попадания в вывод,
# git, диагностику или URL. Командная строка процессов секретов не содержит:
# в контейнеры они попадают через `docker compose --env-file pilot.env`.

Set-StrictMode -Version 2.0

$script:SecretNames = @(
    "HRM_POSTGRES_PASSWORD",   # 32 hex — пароль БД
    "HRM_SIGNING_KEY",         # 64 hex — подпись сессий/CSRF
    "HRM_BOOTSTRAP_ADMIN_PASSWORD", # 32 hex — запасной bootstrap-пароль (не используется при токене)
    "HRM_BACKUP_KEY",          # base64 от 32 байт — AES-256 ключ бэкапов
    "HRM_BACKUP_KEY_ID",       # строка — идентификатор ключа бэкапа
    "HRM_EXCHANGE_TOKEN",      # одноразовый токен первого запуска (гасится после claim)
    "HRM_UPDATE_ENGINE_TOKEN"  # 32 hex — машинный токен движка канала обновлений
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
            # Ранние сборки phase 12 сохраняли backup key как 64 hex, хотя
            # backend-контракт BACKUP_ENC_KEY требует base64 от 32 байт.
            # Конвертируем те же байты один раз (это не ротация ключа).
            if ($Name -eq "HRM_BACKUP_KEY" -and $value -match "^[0-9a-fA-F]{64}$") {
                $bytes = New-Object byte[] 32
                for ($i = 0; $i -lt 32; $i++) {
                    $bytes[$i] = [Convert]::ToByte($value.Substring($i * 2, 2), 16)
                }
                $value = [Convert]::ToBase64String($bytes)
                $file = Get-HrmSecretsFile $StateDir
                $data = Get-HrmJsonFile $file
                $merged = [ordered]@{}
                foreach ($prop in $data.PSObject.Properties) { $merged[$prop.Name] = $prop.Value }
                $merged[$Name] = $value
                Set-HrmJsonFile $StateDir "secrets.json" $merged
            }
            Register-HrmSecret $value
            return $value
        }
    }
    $value = switch ($Name) {
        "HRM_POSTGRES_PASSWORD" { New-HrmHex 16 }
        "HRM_SIGNING_KEY" { New-HrmHex 32 }
        "HRM_BOOTSTRAP_ADMIN_PASSWORD" { New-HrmHex 16 }
        "HRM_BACKUP_KEY" {
            $bytes = New-Object byte[] 32
            [System.Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($bytes)
            [Convert]::ToBase64String($bytes)
        }
        "HRM_BACKUP_KEY_ID" { "pilot-" + (New-HrmHex 4) }
        "HRM_EXCHANGE_TOKEN" { New-HrmHex 16 }
        "HRM_UPDATE_ENGINE_TOKEN" { New-HrmHex 16 }
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

function Get-HrmLicensePublicKey {
    # Публичный ключ лицензии (не секрет) — читается ТОЛЬКО из внешнего
    # локального файла StateDir/license_public_key.b64 (подготовлен
    # установщиком из snapshot) или из env HRM_LICENSE_PUBLIC_KEY (тесты).
    # Fallback из git checkout (infra/license/public_key.b64) запрещён —
    # ключ должен приходить через runtime-конфигурацию.
    param([string]$StateDir)
    if ($StateDir) {
        try {
            $stateFile = Join-Path $StateDir "license_public_key.b64"
            if (Test-Path $stateFile) {
                $content = (Get-Content -Path $stateFile -Raw -Encoding UTF8).Trim()
                if ($content -match "^[A-Za-z0-9+/]{43}=$|^[A-Za-z0-9+/]{44}$|^[A-Za-z0-9_-]{43,44}$") {
                    return $content
                }
            }
        } catch {}
    }
    if ($env:HRM_LICENSE_PUBLIC_KEY) {
        return $env:HRM_LICENSE_PUBLIC_KEY.Trim()
    }
    return ""
}

function Install-HrmLicensePublicKey {
    # Публичный ключ проверки лицензии в StateDir\license_public_key.b64 —
    # внешний локальный файл (в сборку/git он не вшивается). Вызывается и при
    # установке, и при обновлении: у уже установленного пилота файл уже есть
    # (тогда ничего не меняем — состояние пользователя не перезаписывается), а
    # если его нет (старая установка, ручная чистка), ключ берётся из
    # обновляемого снимка. Иначе после обновления приложение не смогло бы
    # проверить лицензию, а pilot.env с обязательной переменной (:?) не дал бы
    # стеку подняться. Ключ публичный: в секреты, бандлы и журнал он не попадает.
    param([string]$SourceDir = "", [string]$InstallDir = "", [string]$StateDir = "")
    if (-not $StateDir) { return "" }
    $stateKeyFile = Join-Path $StateDir "license_public_key.b64"
    if (Test-Path $stateKeyFile) { return (Get-HrmLicensePublicKey $StateDir) }
    $sourceKeyCandidates = @(
        (Join-Path $SourceDir "infra/license/public_key.b64"),
        (Join-Path $InstallDir "infra/license/public_key.b64")
    )
    if ($env:HRM_SOURCE_DIR) {
        $sourceKeyCandidates += Join-Path $env:HRM_SOURCE_DIR "infra/license/public_key.b64"
    }
    $foundKey = $null
    $foundPath = $null
    foreach ($sk in $sourceKeyCandidates) {
        if (-not $sk) { continue }
        try {
            if (Test-Path $sk) {
                $raw = (Get-Content -Path $sk -Raw -Encoding UTF8).Trim()
                if ($raw -match "^[A-Za-z0-9+/]{43}=$|^[A-Za-z0-9+/]{44}$|^[A-Za-z0-9_-]{43,44}$") {
                    try {
                        $norm = $raw -replace "-", "+"
                        $norm = $norm -replace "_", "/"
                        $pad = (4 - ($norm.Length % 4)) % 4
                        if ($pad -gt 0) { $norm += "=" * $pad }
                        $decoded = [Convert]::FromBase64String($norm)
                        if ($decoded.Length -eq 32) {
                            $foundKey = $raw
                            $foundPath = $sk
                            break
                        }
                    } catch {}
                }
            }
        } catch {}
    }
    if ($foundKey) {
        Set-Content -Path $stateKeyFile -Value $foundKey -Encoding UTF8 -NoNewline
        Protect-HrmFile $StateDir $stateKeyFile
        $null = Write-HrmLog "info" "Лицензионный ключ скопирован из $foundPath в $stateKeyFile."
    } elseif ($env:HRM_LICENSE_PUBLIC_KEY) {
        # Ключ из env (тестовый/служебный сценарий).
        $envKey = $env:HRM_LICENSE_PUBLIC_KEY.Trim()
        if ($envKey -match "^[A-Za-z0-9+/]{43}=$|^[A-Za-z0-9+/]{44}$|^[A-Za-z0-9_-]{43,44}$") {
            Set-Content -Path $stateKeyFile -Value $envKey -Encoding UTF8 -NoNewline
            Protect-HrmFile $StateDir $stateKeyFile
            $null = Write-HrmLog "info" "Лицензионный ключ взят из HRM_LICENSE_PUBLIC_KEY (env)."
        }
    }
    if (-not (Test-Path $stateKeyFile)) {
        # Fail-closed: пустой ключ НЕ записывается молча. Вызывающий обязан
        # отказать (Assert-HrmLicensePublicKey / Write-HrmPilotEnv), иначе
        # Compose упадёт на интерполяции ${HRM_LICENSE_PUBLIC_KEY:?}
        # с загадочным текстом вместо понятной причины.
        $null = Write-HrmLog "warn" "LICENSE PUBLIC KEY не найден ни в одном источнике (требуется infra/license/public_key.b64 в снимке): вызывающий обязан остановиться с отказом до Compose (fail-closed)."
        return ""
    }
    return (Get-HrmLicensePublicKey $StateDir)
}

function Assert-HrmLicensePublicKey {
    # Честный отказ по публичному ключу лицензии (fail-closed): сначала ключ
    # восстанавливается из всех источников (StateDir, каталог релиза/snapshot,
    # {app}, HRM_LICENSE_PUBLIC_KEY), а если его так и нет — операция
    # останавливается ДО первого вызова Compose с понятным сообщением.
    # Compose требует НЕПУСТОЕ значение (${HRM_LICENSE_PUBLIC_KEY:?} в
    # compose.pilot.yml для backend/backup/worker): пустой ключ, записанный в
    # pilot.env, убивал бы стек на интерполяции. Код HRM-LICENSE-KEY-MISSING —
    # для диагностики и журналов.
    param([string]$SourceDir = "", [string]$InstallDir = "", [string]$StateDir = "")
    $key = Install-HrmLicensePublicKey -SourceDir $SourceDir -InstallDir $InstallDir -StateDir $StateDir
    if (-not $key) {
        $searched = @()
        if ($StateDir) { $searched += (Join-Path $StateDir "license_public_key.b64") }
        if ($SourceDir) { $searched += (Join-Path $SourceDir "infra/license/public_key.b64") }
        if ($InstallDir) { $searched += (Join-Path $InstallDir "infra/license/public_key.b64") }
        $searched += "HRM_LICENSE_PUBLIC_KEY (env)"
        throw ("HRM-LICENSE-KEY-MISSING: публичный ключ лицензии не найден ни в одном источнике ({0}). Без него Compose не поднимет стек (переменная HRM_LICENSE_PUBLIC_KEY обязательна, compose.pilot.yml). Положите infra/license/public_key.b64 в снимок релиза или задайте ключ — операция остановлена ДО первого вызова Compose." -f ($searched -join "; "))
    }
    return $key
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
    # Запись установки читается безопасно: нечитаемый/невалидный installed.json
    # — отдельная диагностика (код HRM-INSTALL-RECORD-INVALID), а не сырой
    # текст ошибки разбора JSON.
    $state = $null
    try { $state = Get-HrmInstallRecord $StateDir } catch {
        throw ("HRM-INSTALL-RECORD-INVALID: запись установки нечитаема или невалидна ({0}): {1}. Генерация pilot.env остановлена — восстановите каталог состояния из резервной копии." -f (Get-HrmInstalledFile $StateDir), (Redact-HrmText $_.Exception.Message))
    }
    if (-not [bool](Get-HrmInstallRecordField -Record $state -Field "pilot_created" -Default $false)) {
        $exchange = Get-HrmSecret $StateDir "HRM_EXCHANGE_TOKEN"
    }
    else {
        $exchange = "retired-" + (New-HrmHex 16)
    }
    $port = Get-HrmPort $Port
    $engineToken = Get-HrmSecret $StateDir "HRM_UPDATE_ENGINE_TOKEN"
    # Конфигурация канала обновлений (Phase 13) — серверная/host-конфигурация
    # из channel.json; staging-каталог host отделён от каталога секретов.
    $channel = Get-HrmChannelConfig $StateDir
    $keysJson = ($channel.public_keys | ConvertTo-Json -Compress)
    $licensePub = Get-HrmLicensePublicKey $StateDir
    if (-not $licensePub) {
        # Fail-closed (последняя линия обороны): пустой ключ не должен попасть
        # в pilot.env — Compose требует непустое значение
        # (${HRM_LICENSE_PUBLIC_KEY:?} в compose.pilot.yml). Код
        # HRM-LICENSE-KEY-EMPTY — для диагностики; сообщение называет переменную.
        throw "HRM-LICENSE-KEY-EMPTY: публичный ключ лицензии пуст (нет файла license_public_key.b64 в каталоге состояния и не задан HRM_LICENSE_PUBLIC_KEY). Compose не поднимет стек без ключа (переменная HRM_LICENSE_PUBLIC_KEY обязательна) — генерация pilot.env остановлена."
    }
    # LAN bind: 127.0.0.1 by default, 0.0.0.0 only via explicit lan-access flow
    # Arbitrary HRM_PILOT_BIND values are rejected — safe fallback to 127.0.0.1
    $pilotBind = "127.0.0.1"
    try {
        $lanFile = Join-Path $StateDir "lan.json"
        if (Test-Path $lanFile) {
            $lanData = Get-HrmJsonFile $lanFile
            if ($null -ne $lanData -and $lanData.enabled -eq $true) {
                $rawBind = if ($lanData.PSObject.Properties["bind"] -and $lanData.bind) { [string]$lanData.bind } else { "0.0.0.0" }
                if ($rawBind -eq "0.0.0.0") { $pilotBind = "0.0.0.0" } else { $pilotBind = "127.0.0.1" }
            } else {
                $pilotBind = "127.0.0.1"
            }
        }
    } catch {}
    $lines = @(
        ("HRM_POSTGRES_PASSWORD={0}" -f $secrets["HRM_POSTGRES_PASSWORD"]),
        ("HRM_SIGNING_KEY={0}" -f $secrets["HRM_SIGNING_KEY"]),
        ("HRM_BOOTSTRAP_ADMIN_PASSWORD={0}" -f $secrets["HRM_BOOTSTRAP_ADMIN_PASSWORD"]),
        ("HRM_EXCHANGE_TOKEN={0}" -f $exchange),
        ("HRM_BACKUP_KEY={0}" -f $secrets["HRM_BACKUP_KEY"]),
        ("HRM_BACKUP_KEY_ID={0}" -f $secrets["HRM_BACKUP_KEY_ID"]),
        ("HRM_RELEASE_SHA={0}" -f $ReleaseSha),
        ("HRM_PILOT_PORT={0}" -f $port),
        ("HRM_PILOT_BIND={0}" -f $pilotBind),
        ("HRM_UPDATE_ENGINE_TOKEN={0}" -f $engineToken),
        ("HRM_STAGING_DIR={0}" -f (Get-HrmStagingHostDir)),
        ("HRM_UPDATE_CHANNEL_URL={0}" -f $channel.url),
        # Кавычки JSON экранируются literal-заменой: -replace использует
        # regex-синтаксис replacement и удалил бы обратный слэш.
        ("HRM_UPDATE_CHANNEL_PUBLIC_KEYS=`"{0}`"" -f ([Regex]::Replace($keysJson, '"', '\"'))),
        ("HRM_UPDATE_CHECK_MIN_INTERVAL={0}" -f $channel.check_min_interval_seconds),
        ("HRM_LICENSE_PUBLIC_KEY={0}" -f $licensePub)
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

function Get-HrmInstallRecordField {
    # Безопасное чтение поля записи установки. У записи СТАРОЙ установки поля
    # может не быть вовсе, а прямой доступ к отсутствующему свойству под
    # StrictMode 2.0 — исключение PropertyNotFound: установка, обновление,
    # диагностика и трей обрывались бы на ровном месте.
    param($Record, [string]$Field, $Default = "")
    if ($null -eq $Record) { return $Default }
    if (-not $Record.PSObject.Properties[$Field]) { return $Default }
    $value = $Record.PSObject.Properties[$Field].Value
    if ($null -eq $value) { return $Default }
    return $value
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
