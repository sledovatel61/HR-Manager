# Канал обновлений Windows-пилота (Phase 13), host-сторона.
#
# Движок НЕ скачивает пакеты сам: бэкенд проверяет подписанный manifest и
# скачивает пакет в bind-mounted staging (контейнерный /updates →
# host-каталог HRM_STAGING_DIR). Движок-наблюдатель опрашивает
# /updates/engine-state по loopback с машинным токеном и, получив команду
# install, ПОВТОРНО проверяет manifest доверенными ключами (Crypto.psm1),
# размер/SHA256 пакета, безопасно распаковывает (Zip Slip/ADS/UNC/symlink)
# и передаёт каталог существующему Phase 12 update engine
# (Update-HrmApp: backup gate → миграция → smoke → rollback). Фоновая
# установка не запускается никогда: watcher только проверяет наличие
# обновлений (сервер троттлит) и выполняет ЯВНО поставленную команду.

Set-StrictMode -Version 2.0

$script:ChannelCheckIntervalDefault = 300
$script:ChannelWatcherInterval = 30
$script:ChannelAllowedTopLevel = @("backend", "frontend", "infra", "release.json")
$script:ChannelBlockedExtensions = @(".exe", ".dll", ".bat", ".cmd", ".com", ".scr", ".msi", ".sys", ".ocx", ".vbs", ".jar", ".psm1.out")

function Get-HrmChannelConfigFile { param([string]$StateDir) return (Join-Path $StateDir "channel.json") }
function Get-HrmStagingHostDir {
    # Staging НЕ внутри каталога состояния с секретами и не в backup volume.
    if ($env:HRM_STAGING_DIR) { return $env:HRM_STAGING_DIR }
    return Join-Path $env:LOCALAPPDATA "HRManagerStaging"
}
function Get-HrmChannelPidFile { param([string]$StateDir) return (Join-Path $StateDir "channel-watch.pid") }

# --- Trust store (Phase 14) ----------------------------------------------------

function Test-HrmTrustStoreObject {
    # Строгая валидация ПУБЛИЧНОГО trust store (зеркало infra/release/trust_store.py).
    # Отказ (throw) на: неизвестные/лишние поля, private material, не-base64
    # или не-32-байтовые ключи, неверные типы, пустой набор, test-окружение
    # (dev/test default никогда молча не становится production trust root).
    # Возвращает нормализованный hashtable @{ environment; keys }.
    param($Data)
    if ($null -eq $Data) { throw "Trust store пуст." }
    $names = @($Data.PSObject.Properties | ForEach-Object { $_.Name })
    $expected = @("schema_version", "environment", "keys")
    foreach ($name in $expected) {
        if ($names -notcontains $name) { throw "Trust store: нет поля $name." }
    }
    foreach ($name in $names) {
        if ($expected -notcontains $name) { throw "Trust store: неизвестное поле $name." }
        $lowered = $name.ToLowerInvariant()
        if (@("private", "priv", "secret", "seed", "signing_key", "password", "token") -contains $lowered) {
            throw "Trust store: поле $name выглядит как закрытый материал."
        }
    }
    if ([int]$Data.schema_version -ne 1) { throw "Trust store: schema_version должен быть 1." }
    $environment = [string]$Data.environment
    if ($environment -ne "production" -and $environment -ne "test") {
        throw "Trust store: неизвестное environment '$environment'."
    }
    if ($environment -eq "test") {
        throw "Trust store: test/fixture хранилище не может стать production trust root."
    }
    $keysData = $Data.keys
    if ($null -eq $keysData) { throw "Trust store: keys отсутствует." }
    $keyIds = @($keysData.PSObject.Properties | ForEach-Object { $_.Name })
    if ($keyIds.Count -eq 0) { throw "Trust store: keys пуст." }
    $keys = @{}
    foreach ($keyId in $keyIds) {
        if ($keyId -notmatch "^[A-Za-z0-9._-]{1,64}$") { throw "Trust store: некорректный key_id '$keyId'." }
        $entry = $keysData.PSObject.Properties[$keyId].Value
        if ($null -eq $entry) { throw "Trust store: запись ключа '$keyId' пуста." }
        $entryNames = @($entry.PSObject.Properties | ForEach-Object { $_.Name })
        if ($entryNames -notcontains "key" -or $entryNames -notcontains "revoked") {
            throw "Trust store: запись '$keyId' должна содержать key и revoked."
        }
        if ($entryNames.Count -ne 2) { throw "Trust store: лишние поля в записи '$keyId'." }
        $keyValue = [string]$entry.key
        if ($keyValue -notmatch "^[A-Za-z0-9+/]{43}=$") {
            throw "Trust store: ключ '$keyId' не является base64 32-байтовым Ed25519 публичным ключом."
        }
        try {
            $bytes = [Convert]::FromBase64String($keyValue)
            if ($bytes.Length -ne 32) { throw "len" }
        }
        catch { throw "Trust store: ключ '$keyId' не декодируется как 32 байта." }
        $revokedValue = $entry.revoked
        if ($null -eq $revokedValue -or $revokedValue.GetType().Name -ne "Boolean") {
            throw "Trust store: revoked у '$keyId' должен быть true/false."
        }
        $keys[$keyId] = @{ key = $keyValue; revoked = [bool]$revokedValue }
    }
    $hasActive = $false
    foreach ($value in $keys.Values) { if (-not $value.revoked) { $hasActive = $true } }
    if (-not $hasActive) { throw "Trust store: нет ни одного неотозванного ключа." }
    return @{ environment = $environment; keys = $keys }
}

function Import-HrmTrustStore {
    # Импорт встроенного релизом trust store при первичной установке:
    # публикуется ТОЛЬКО набор публичных ключей (без private material) в
    # channel.json. Существующая конфигурация НИКОГДА не перезаписывается —
    # ротация/отзыв, выполненные администратором, выигрывают.
    # Возвращает $true, если хранилище импортировано.
    param([string]$SnapshotDir, [string]$StateDir)
    $storeFile = Join-Path $SnapshotDir "release-trust-store.json"
    if (-not (Test-Path $storeFile)) { return $false }
    $data = Get-HrmJsonFile $storeFile
    $validated = Test-HrmTrustStoreObject $data
    $configFile = Get-HrmChannelConfigFile $StateDir
    if (Test-Path $configFile) {
        Write-HrmLog "info" "Конфигурация канала уже существует — встроенный trust store не перезаписывает её."
        return $false
    }
    Set-HrmChannelConfig -StateDir $StateDir -PublicKeys $validated.keys
    Write-HrmLog "info" ("Импортирован встроенный trust store ({0} ключ(ей))." -f $validated.keys.Count)
    return $true
}

# --- Факты host-стороны для предпусковой проверки (Phase 14) -------------------

$script:EngineFactsCache = $null
$script:EngineFactsCacheAt = [datetime]::MinValue
$script:EngineFactsRefreshSeconds = 300

function Get-HrmEngineFacts {
    # Собирает redacted-факты машины для backend readiness-проверки.
    # Только ограниченные значения (enum/bool/int/IP-литералы) — без путей,
    # секретов и свободного текста; схема на сервере закрыта (extra=forbid).
    param([string]$StateDir = "", [bool]$WatcherRunning = $false)
    if (-not $StateDir) { $StateDir = Get-HrmStateDir }

    # Windows 10/11 (Win11 = build 22000+, обе версии 10.0).
    $windowsVersion = "other"
    $windowsSupported = $false
    try {
        $os = Get-CimInstance Win32_OperatingSystem -ErrorAction SilentlyContinue
        if ($null -ne $os) {
            $parts = ([string]$os.Version).Split(".")
            $build = if ($parts.Count -ge 3) { [int]$parts[2] } else { 0 }
            $major = if ($parts.Count -ge 1) { [int]$parts[0] } else { 0 }
            if ($major -eq 10 -and $build -ge 22000) { $windowsVersion = "windows_11"; $windowsSupported = $true }
            elseif ($major -eq 10) { $windowsVersion = "windows_10"; $windowsSupported = $true }
        }
    }
    catch { }

    $dockerState = Get-HrmDockerState

    # Compose: версия и соответствие >= 2.24.
    $composeVersion = ""
    $composeOk = $false
    $composeOut = Invoke-HrmExternal -Name "docker.exe" -Arguments @("compose", "version", "--short") -IgnoreExitCode
    if ($composeOut.ExitCode -eq 0 -and $composeOut.Stdout) {
        $composeVersion = (($composeOut.Stdout -split "`n")[0]).Trim()
        if ($composeVersion.Length -gt 32) { $composeVersion = $composeVersion.Substring(0, 32) }
        $match = [regex]::Match($composeVersion, "v?(\d+)\.(\d+)\.(\d+)")
        if ($match.Success) {
            $composeOk = ([int]$match.Groups[1].Value -ge 2) -and ([int]$match.Groups[2].Value -ge 24)
        }
    }

    # Опубликованные порты проекта: только факты (IP:порт -> контейнер).
    $publishedPorts = @()
    if ($dockerState -eq "ok") {
        $ps = Invoke-HrmExternal -Name "docker.exe" -Arguments @("ps", "--filter", "name=hr-manager-pilot", "--format", "{{.Names}}|{{.Ports}}") -IgnoreExitCode
        if ($ps.ExitCode -eq 0 -and $ps.Stdout) {
            foreach ($line in ($ps.Stdout -split "`n")) {
                $trimmed = $line.Trim()
                if (-not $trimmed) { continue }
                $pieces = $trimmed -split "\|", 2
                if ($pieces.Count -lt 2) { continue }
                $containerName = $pieces[0]
                if ($containerName.Length -gt 64) { $containerName = $containerName.Substring(0, 64) }
                foreach ($mapping in ($pieces[1] -split ",")) {
                    $mapping = $mapping.Trim()
                    if (-not $mapping) { continue }
                    $arrow = $mapping.IndexOf("->")
                    if ($arrow -lt 1) { continue }
                    $hostPart = $mapping.Substring(0, $arrow)
                    $lastColon = $hostPart.LastIndexOf(":")
                    if ($lastColon -lt 1) { continue }
                    $hostIp = $hostPart.Substring(0, $lastColon)
                    $hostPortText = $hostPart.Substring($lastColon + 1)
                    $hostIp = $hostIp.Trim("[", "]")
                    $hostPort = 0
                    if (-not [int]::TryParse($hostPortText, [ref]$hostPort)) { continue }
                    if ($hostPort -lt 1 -or $hostPort -gt 65535) { continue }
                    $publishedPorts += @{ host_ip = $hostIp; host_port = $hostPort; container = $containerName }
                    if ($publishedPorts.Count -ge 16) { break }
                }
                if ($publishedPorts.Count -ge 16) { break }
            }
        }
    }

    # Свободное место на диске каталога состояния.
    $diskFreeMb = 0
    try {
        $qualifier = (Split-Path -Qualifier $StateDir).TrimEnd(":")
        $psDrive = Get-PSDrive -Name $qualifier -ErrorAction Stop
        $diskFreeMb = [long]($psDrive.Free / 1MB)
        if ($diskFreeMb -lt 0) { $diskFreeMb = 0 }
    }
    catch { $diskFreeMb = 0 }

    # ACL каталога состояния: наследование выключено, единственная запись —
    # текущий пользователь (так её оставляет Protect-HrmFile при установке).
    $stateAclOk = $false
    if (Test-Path $StateDir) {
        $acl = Invoke-HrmExternal -Name "icacls.exe" -Arguments @($StateDir) -IgnoreExitCode
        if ($acl.ExitCode -eq 0 -and $acl.Stdout) {
            $identity = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
            $lines = @($acl.Stdout -split "`r?`n" | Where-Object { $_.Trim() })
            if ($lines.Count -eq 1 -and $lines[0].StartsWith($identity)) {
                $stateAclOk = $true
            }
        }
    }

    # Staging: доступен для записи и находится ВНЕ каталога состояния.
    $staging = Get-HrmStagingHostDir
    $stagingWritable = $false
    try {
        if (-not (Test-Path $staging)) { New-Item -ItemType Directory -Path $staging -Force | Out-Null }
        $probe = Join-Path $staging (".facts-probe-" + [System.Guid]::NewGuid().ToString("N").Substring(0, 8))
        Set-Content -Path $probe -Value "ok"
        Remove-Item $probe -Force
        $stagingWritable = $true
    }
    catch { $stagingWritable = $false }
    $stagingOutsideState = $true
    try {
        $fullStaging = [System.IO.Path]::GetFullPath($staging)
        $fullState = [System.IO.Path]::GetFullPath($StateDir)
        if ($fullStaging.StartsWith($fullState, [System.StringComparison]::OrdinalIgnoreCase) -or
            $fullState.StartsWith($fullStaging, [System.StringComparison]::OrdinalIgnoreCase)) {
            $stagingOutsideState = $false
        }
    }
    catch { $stagingOutsideState = $false }

    # Возможность отката: закреплены ли предыдущие образы (:previous).
    $previousImages = $false
    if ($dockerState -eq "ok") {
        $images = Invoke-HrmExternal -Name "docker.exe" -Arguments @("images", "--format", "{{.Repository}}:{{.Tag}}") -IgnoreExitCode
        if ($images.ExitCode -eq 0 -and $images.Stdout) {
            foreach ($line in ($images.Stdout -split "`n")) {
                if ($line.Trim() -like "hr-manager-pilot-*:previous") { $previousImages = $true; break }
            }
        }
    }

    $facts = @{
        windows_version = $windowsVersion
        windows_supported = $windowsSupported
        docker_state = $dockerState
        compose_version = $composeVersion
        compose_ok = $composeOk
        published_ports = $publishedPorts
        disk_free_mb = $diskFreeMb
        state_dir_acl_ok = $stateAclOk
        staging_writable = $stagingWritable
        staging_outside_state = $stagingOutsideState
        previous_images_present = $previousImages
        watcher_running = $WatcherRunning
    }
    return $facts
}

function Reset-HrmEngineFactsCache {
    # Тестовый шов: сброс кеша фактов перед проверкой цикла наблюдателя.
    $script:EngineFactsCache = $null
    $script:EngineFactsCacheAt = [datetime]::MinValue
}

function Send-HrmEngineFacts {
    # Отправка фактов на сервер (loopback + машинный токен), с кешем на
    # $script:EngineFactsRefreshSeconds секунд. Сбой отправки не мешает
    # работе наблюдателя (сервер просто не получит свежие факты).
    param([string]$InstallDir = "", [string]$StateDir, [bool]$WatcherRunning = $false)
    $record = Get-HrmInstallRecord $StateDir
    if ($null -eq $record) { return }
    $now = Get-Date
    if ($null -ne $script:EngineFactsCache -and (($now - $script:EngineFactsCacheAt).TotalSeconds -lt $script:EngineFactsRefreshSeconds)) {
        return
    }
    $facts = Get-HrmEngineFacts -StateDir $StateDir -WatcherRunning $WatcherRunning
    $script:EngineFactsCache = $facts
    $script:EngineFactsCacheAt = $now
    $port = if ($record.port) { [int]$record.port } else { Get-HrmPort }
    $baseUrl = Get-HrmBaseUrl $port
    $token = Get-HrmSecret $StateDir "HRM_UPDATE_ENGINE_TOKEN"
    if ([string]::IsNullOrEmpty($token)) { return }
    $headers = @{ "X-Engine-Token" = $token }
    try {
        $result = Invoke-HrmHttp -Uri "$baseUrl/api/updates/engine-facts" -Method "POST" -Body $facts -Headers $headers
        if ($null -eq $result -or $result.StatusCode -ne 200) {
            Write-HrmLog "info" "Канал: факты для проверки готовности не приняты сервером (не влияет на работу)."
        }
    }
    catch {
        Write-HrmLog "info" ("Канал: отправка фактов готовности не удалась: {0}" -f (Redact-HrmText $_.Exception.Message))
    }
}

# --- Конфигурация канала -------------------------------------------------------

function Get-HrmChannelConfig {
    # {url, preview_url, public_keys:{kid:{key,revoked}}, check_min_interval_seconds}
    param([string]$StateDir)
    $file = Get-HrmChannelConfigFile $StateDir
    if (-not (Test-Path $file)) {
        return @{
            url = "https://github.com/sledovatel61/HR-Manager/releases/latest/download/update-channel.json"
            preview_url = ""
            public_keys = @{}
            check_min_interval_seconds = $script:ChannelCheckIntervalDefault
        }
    }
    $data = Get-HrmJsonFile $file
    $keys = @{}
    if ($null -ne $data -and $data.public_keys) {
        foreach ($prop in $data.public_keys.PSObject.Properties) {
            $keys[$prop.Name] = @{ key = [string]$prop.Value.key; revoked = [bool]$prop.Value.revoked }
        }
    }
    return @{
        url = if ($data.url) { [string]$data.url } else { "" }
        preview_url = if ($data.preview_url) { [string]$data.preview_url } else { "" }
        public_keys = $keys
        check_min_interval_seconds = if ($data.check_min_interval_seconds) { [int]$data.check_min_interval_seconds } else { $script:ChannelCheckIntervalDefault }
    }
}

function Set-HrmChannelConfig {
    # Административная правка канала (ротация/отзыв ключей, смена URL).
    param(
        [string]$StateDir,
        [string]$Url = "",
        [hashtable]$PublicKeys = $null,
        [int]$CheckMinIntervalSeconds = 0
    )
    $config = Get-HrmChannelConfig $StateDir
    if ($Url) {
        if (-not $Url.StartsWith("https://")) { throw "URL канала обязан использовать https." }
        $config["url"] = $Url
    }
    if ($null -ne $PublicKeys) { $config["public_keys"] = $PublicKeys }
    if ($CheckMinIntervalSeconds -gt 0) { $config["check_min_interval_seconds"] = $CheckMinIntervalSeconds }
    Set-HrmJsonFile $StateDir "channel.json" $config
}

function Get-HrmInstalledVersion {
    # Версия из release.json установленного снимка.
    param([string]$InstallDir)
    $releaseFile = Join-Path $InstallDir "release.json"
    $data = Get-HrmJsonFile $releaseFile
    if ($null -ne $data -and $data.version) { return [string]$data.version }
    return "0.0.0"
}

# --- Проверка manifest на стороне движка ---------------------------------------

function Assert-HrmChannelPolicy {
    # Fail closed: downgrade, та же версия с другим SHA, minimum_supported.
    # Параметр без типа: OrderedDictionary не приводится к Hashtable.
    param($Manifest, [string]$InstalledVersion, [string]$InstalledSha)
    $available = [string]$Manifest["version"]
    $comparison = Compare-HrmSemVer $available $InstalledVersion
    if ($comparison -lt 0) {
        throw "Downgrade запрещён: $available < $InstalledVersion (канал не применён)."
    }
    if ($comparison -eq 0 -and $InstalledSha -and [string]$Manifest["release_sha"] -ne $InstalledSha) {
        throw "Та же версия с другим release_sha — конфликт целостности."
    }
    if ((Compare-HrmSemVer $InstalledVersion ([string]$Manifest["minimum_supported_version"])) -lt 0) {
        throw ("Текущая версия ниже minimum_supported_version: необходимо ручное обновление.")
    }
}

function Read-HrmVerifiedManifest {
    # Чтение manifest.signed.json + полная клиентская проверка.
    param([string]$ManifestPath, [hashtable]$TrustedKeys)
    if (-not (Test-Path $ManifestPath)) { throw "Отсутствует manifest рядом с пакетом." }
    $text = Get-Content -Path $ManifestPath -Raw -Encoding UTF8
    $manifest = Read-HrmManifestJson $text
    return Test-HrmChannelManifest $manifest $TrustedKeys
}

# --- Безопасная распаковка -------------------------------------------------------

function Assert-HrmSafeEntry {
    # Zip Slip / absolute / UNC / device / ADS / backslash / symlink / exe.
    param([string]$EntryName, [int]$UnixMode)
    if ([string]::IsNullOrEmpty($EntryName)) { throw "Пустое имя записи архива." }
    if ($EntryName.Contains("\")) { throw "Запись с обратным слэшем (backslash): $EntryName" }
    if ($EntryName.Contains(":")) { throw "Запись с ':' (ADS/device/absolute): $EntryName" }
    if ($EntryName.StartsWith("/") -or $EntryName.StartsWith("\\")) { throw "Абсолютный путь записи: $EntryName" }
    if ($EntryName -match "^[A-Za-z]:" ) { throw "Путь с буквой диска: $EntryName" }
    $segments = @($EntryName -split "/")
    foreach ($segment in $segments) {
        if ($segment -eq ".." -or $segment -eq "." ) { throw "Path traversal: $EntryName" }
    }
    if ($segments.Count -lt 1 -or ($script:ChannelAllowedTopLevel -notcontains $segments[0])) {
        throw "Запись вне ожидаемой структуры пакета: $EntryName"
    }
    if (($UnixMode -band 0xF000) -eq 0xA000 -or ($UnixMode -band 0xF000) -eq 0x6000) {
        throw "Символическая ссылка/reparse-point в пакете: $EntryName"
    }
    if ($EntryName.EndsWith("/")) { return } # каталоги не проверяем на расширения
    $extension = [System.IO.Path]::GetExtension($EntryName).ToLowerInvariant()
    if ($script:ChannelBlockedExtensions -contains $extension) {
        throw "Непредусмотренный исполняемый файл в пакете: $EntryName"
    }
}

function Expand-HrmPackage {
    # Распаковка с проверкой КАЖДОЙ записи до записи на диск; результат —
    # каталог staging/expanded-<sha12>. Возвращает путь каталога.
    param([string]$PackagePath, [string]$StagingDir, [string]$ReleaseSha)
    Add-Type -AssemblyName System.IO.Compression.FileSystem
    $zip = [System.IO.Compression.ZipFile]::OpenRead($PackagePath)
    try {
        $entries = $zip.Entries
        foreach ($entry in $entries) {
            $mode = [int]($entry.ExternalAttributes -shr 16)
            if ($mode -eq 0) { $mode = [int]$entry.ExternalAttributes }
            Assert-HrmSafeEntry $entry.FullName $mode
        }
        $target = Join-Path $StagingDir ("expanded-" + $ReleaseSha.Substring(0, 12))
        if (Test-Path $target) { Remove-Item $target -Recurse -Force }
        New-Item -ItemType Directory -Path $target -Force | Out-Null
        foreach ($entry in $entries) {
            if ($entry.FullName.EndsWith("/")) { continue }
            $destination = Join-Path $target ($entry.FullName -replace "/", "\")
            $parent = Split-Path $destination -Parent
            if (-not (Test-Path $parent)) { New-Item -ItemType Directory -Path $parent -Force | Out-Null }
            $stream = $entry.Open()
            try {
                $outFile = [System.IO.File]::Create($destination)
                try { $stream.CopyTo($outFile) } finally { $outFile.Dispose() }
            }
            finally { $stream.Dispose() }
        }
        return $target
    }
    finally {
        $zip.Dispose()
    }
}

# --- Установка из staging ----------------------------------------------------------

function Invoke-HrmChannelInstall {
    # Команда install от сервера: manifest+пакет уже лежат в staging.
    # Повторная проверка и существующий update engine.
    param([string]$InstallDir, [string]$StateDir, [string]$ManifestPath, [string]$PackagePath, [string]$JobId = "")
    $record = Get-HrmInstallRecord $StateDir
    if ($null -eq $record) { throw "Установка не найдена." }
    $config = Get-HrmChannelConfig $StateDir
    $manifest = Read-HrmVerifiedManifest $ManifestPath $config.public_keys
    $installedVersion = Get-HrmInstalledVersion $InstallDir
    $installedSha = if ($record.release_sha) { [string]$record.release_sha } else { "" }
    Assert-HrmChannelPolicy $manifest $installedVersion $installedSha
    # Размер + SHA256 пакета против manifest.
    $packageInfo = Get-Item $PackagePath
    if ($packageInfo.Length -ne [long]$manifest["package_size"]) {
        throw "Размер пакета не совпал с manifest."
    }
    $packageBytes = [System.IO.File]::ReadAllBytes($PackagePath)
    $packageHash = Get-HrmSha256Hex $packageBytes
    if ($packageHash -ne [string]$manifest["package_sha256"]) {
        throw "SHA256 пакета не совпал с manifest."
    }
    # Безопасная распаковка в staging (не в state dir).
    $staging = Get-HrmStagingHostDir
    if (-not (Test-Path $staging)) { New-Item -ItemType Directory -Path $staging -Force | Out-Null }
    $expanded = Expand-HrmPackage $PackagePath $staging ([string]$manifest["release_sha"])
    # Внутренний release.json обязан совпадать с manifest (без противоречий).
    $inner = Get-HrmJsonFile (Join-Path $expanded "release.json")
    if ($null -eq $inner -or -not $inner.release_sha) {
        throw "В пакете нет release.json с release_sha."
    }
    if ([string]$inner.release_sha -ne [string]$manifest["release_sha"]) {
        throw "Внутренний release_sha не совпадает с manifest."
    }
    # Существующий Phase 12 update engine (backup gate → smoke → rollback).
    $null = Write-HrmLog "info" ("Канал: установка проверенного релиза {0} (sha {1})…" -f $manifest["version"], ([string]$manifest["release_sha"]).Substring(0, 12))
    # $null =: update engine пишет журнал в success stream (Write-Output).
    # Без захвата его строки попали бы в возврат этой функции и упаковали
    # hashtable результата в массив — StrictMode дал бы PropertyNotFoundException
    # на $outcome.version у вызывающего.
    $null = Update-HrmApp -ReleaseDir $expanded -InstallDir $InstallDir -StateDir $StateDir
    return @{
        version = [string]$manifest["version"]
        release_sha = [string]$manifest["release_sha"]
        job_id = $JobId
    }
}

# --- Наблюдатель (loopback → бэкенд) ----------------------------------------------

function Invoke-HrmChannelOnce {
    # Один цикл: факты для readiness (кеш 5 мин) → опрос engine-state,
    # выполнение команды, отчёт; фоновая проверка — только через серверный
    # /updates/engine-check (троттлинг).
    param([string]$InstallDir = "", [string]$StateDir = "", [switch]$SkipFacts)
    if (-not $InstallDir) { $InstallDir = Get-HrmDefaultInstallDir }
    if (-not $StateDir) { $StateDir = Get-HrmStateDir }
    $record = Get-HrmInstallRecord $StateDir
    if ($null -eq $record) { return }
    # Факты host-стороны для предпусковой проверки готовности (Phase 14):
    # redacted-отчёт по закрытой схеме; сбой не влияет на канал обновлений.
    if (-not $SkipFacts) {
        try {
            Send-HrmEngineFacts -InstallDir $InstallDir -StateDir $StateDir -WatcherRunning $true
        }
        catch {
            Write-HrmLog "info" ("Канал: не удалось собрать факты готовности: {0}" -f (Redact-HrmText $_.Exception.Message))
        }
    }
    $port = if ($record.port) { [int]$record.port } else { Get-HrmPort }
    $baseUrl = Get-HrmBaseUrl $port
    $token = Get-HrmSecret $StateDir "HRM_UPDATE_ENGINE_TOKEN"
    if ([string]::IsNullOrEmpty($token)) { return }
    $headers = @{ "X-Engine-Token" = $token }
    $installedVersion = Get-HrmInstalledVersion $InstallDir
    $installedSha = if ($record.release_sha) { [string]$record.release_sha } else { "" }
    $headers["X-Installed-Version"] = $installedVersion
    $headers["X-Installed-Sha"] = $installedSha

    $stateUrl = "$baseUrl/api/updates/engine-state"
    $poll = Invoke-HrmHttp -Uri $stateUrl -Headers $headers
    if ($poll.StatusCode -ne 200 -or $null -eq $poll.Body) {
        Write-HrmLog "info" "Канал: сервер недоступен для опроса (офлайн не мешает работе приложения)."
        return
    }
    $body = $poll.Body
    $actions = @($body.actions)
    if ($actions.Count -gt 0 -and $actions -contains "install") {
        $jobId = if ($body.job_id) { [string]$body.job_id } else { "" }
        $manifestPath = if ($body.manifest_path) { [string]$body.manifest_path } else { "" }
        $packagePath = if ($body.release_dir) { [string]$body.release_dir } else { "" }
        # Контейнерный путь /updates/... → host-каталог staging.
        if ($packagePath.StartsWith("/updates/")) {
            $hostStaging = Get-HrmStagingHostDir
            $packagePath = Join-Path $hostStaging (Split-Path $packagePath -Leaf)
            $manifestPath = Join-Path $hostStaging (Split-Path $manifestPath -Leaf)
        }
        $resultState = "installed"
        $resultVersion = $installedVersion
        $resultSha = $installedSha
        $errorCode = ""
        $errorDetail = ""
        try {
            $outcome = Invoke-HrmChannelInstall -InstallDir $InstallDir -StateDir $StateDir -ManifestPath $manifestPath -PackagePath $packagePath -JobId $jobId
            $resultVersion = $outcome.version
            $resultSha = $outcome.release_sha
            Write-HrmLog "info" ("Канал: установка завершена ({0})." -f $outcome.version)
        }
        catch {
            # Phase 12 update engine выполнил rollback сам; отчёт — честный.
            $resultState = "rolled_back"
            $errorCode = "update_failed"
            $stackDetail = if ($_.ScriptStackTrace) { [string]$_.ScriptStackTrace } else { "" }
            $errorDetail = Redact-HrmText (($_.Exception.Message) + " [stack: " + $stackDetail + "]")
            Write-HrmLog "error" ("Канал: установка не удалась, откат выполнен: {0}" -f $errorDetail)
        }
        $report = @{
            job_id = $jobId
            state = $resultState
            installed_version = $resultVersion
            installed_release_sha = $resultSha
            error_code = $errorCode
            error_detail = $errorDetail
        }
        $reportResult = Invoke-HrmHttp -Uri "$baseUrl/api/updates/engine-report" -Method "POST" -Body $report -Headers $headers
        Write-HrmLog "info" ("Канал: отчёт серверу ({0})." -f $resultState)
        return
    }
    # Команд нет: запрос фоновой проверки (сервер троттлит интервалом).
    $check = Invoke-HrmHttp -Uri "$baseUrl/api/updates/engine-check" -Method "POST" -Headers $headers
    if ($check.StatusCode -eq 200 -and $null -ne $check.Body -and $check.Body.state -eq "available") {
        Write-HrmLog "info" ("Канал: доступна версия {0}." -f $check.Body.available_version)
    }
}

function Start-HrmChannelWatch {
    # Блокирующий цикл наблюдателя (запускается отдельным процессом).
    param([string]$InstallDir = "", [string]$StateDir = "")
    if (-not $InstallDir) { $InstallDir = Get-HrmDefaultInstallDir }
    if (-not $StateDir) { $StateDir = Get-HrmStateDir }
    $pidFile = Get-HrmChannelPidFile $StateDir
    if (Test-Path $pidFile) {
        $existing = Get-Content $pidFile -Raw
        try {
            $process = Get-Process -Id ([int]$existing) -ErrorAction Stop
            if ($process.ProcessName -match "powershell|pwsh") {
                Write-HrmLog "info" "Канал: наблюдатель уже запущен (pid $existing)."
                return
            }
        }
        catch { }
    }
    Set-Content -Path $pidFile -Value $PID -Encoding ASCII
    Protect-HrmFile $StateDir $pidFile
    Write-HrmLog "info" "Канал: наблюдатель запущен (интервал $($script:ChannelWatcherInterval) c)."
    while ($true) {
        try {
            Invoke-HrmChannelOnce -InstallDir $InstallDir -StateDir $StateDir
        }
        catch {
            Write-HrmLog "error" ("Канал: ошибка цикла: {0}" -f (Redact-HrmText $_.Exception.Message))
        }
        Start-Sleep -Seconds $script:ChannelWatcherInterval
    }
}

function Stop-HrmChannelWatch {
    param([string]$StateDir = "")
    if (-not $StateDir) { $StateDir = Get-HrmStateDir }
    $pidFile = Get-HrmChannelPidFile $StateDir
    if (-not (Test-Path $pidFile)) {
        Write-HrmLog "info" "Канал: наблюдатель не запущен."
        return
    }
    try {
        $watcherPid = [int](Get-Content $pidFile -Raw)
        Stop-Process -Id $watcherPid -Force -ErrorAction Stop
        Write-HrmLog "info" "Канал: наблюдатель остановлен."
    }
    catch { }
    Remove-Item $pidFile -Force -ErrorAction SilentlyContinue
}
