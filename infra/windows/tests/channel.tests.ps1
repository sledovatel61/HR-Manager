# Тесты канала обновлений (Phase 13): Ed25519-проверка, канонизация,
# SemVer, безопасная распаковка, наблюдатель (loopback-контракт с моком).
# Реальная сеть/Docker не затрагиваются: fixture из infra/release/testdata.

param([string]$HarnessPath = $PSScriptRoot)

. (Join-Path $HarnessPath "test-harness.ps1")

$script:Testdata = Join-Path $PSScriptRoot "..\..\..\infra\release\testdata"

function Get-HrmFixturePath { param([string]$Name) return (Join-Path $script:Testdata $Name) }
function Get-HrmFixtureText { param([string]$Name) return ((Get-Content (Get-HrmFixturePath $Name) -Raw -Encoding UTF8) -replace "`r", "") }

function New-HrmChannelWorld {
    # Мок-мир канала: docker-моки из общей обвязки + HTTP-контракт сервера.
    param(
        [string]$TestRoot = "",
        [string]$ReleaseSha = ("3" * 40),
        [string]$QueueInstall = "",
        [string]$EngineCheckState = "up_to_date"
    )
    if (-not $TestRoot) {
        $TestRoot = Join-Path ([System.IO.Path]::GetTempPath()) ("HRM channel " + [System.Guid]::NewGuid().ToString("N").Substring(0, 8))
    }
    $sourceDir = Join-Path $TestRoot "источник"
    New-HrmFakeSnapshot -Root $sourceDir -ReleaseSha $ReleaseSha
    $world = New-HrmMockWorld -ReleaseSha ("2" * 40)
    # Update smoke сверяет работающий release_sha с релизом ("2"*40).
    $world.OpsBody.release_sha = "2" * 40
    Set-HrmPreflightOverride @{
        windows = $true; powershell = $true; docker = $true; daemon = $true;
        compose = "v2.29.7 (mock)"; port = $true; state_dir = $true; space = $true; config = $true
    }
    # Каталог staging host (пакет + manifest кладём сюда заранее).
    $staging = Join-Path $TestRoot "staging"
    New-Item -ItemType Directory -Path $staging -Force | Out-Null
    $env:HRM_STAGING_DIR = $staging
    if ($QueueInstall) {
        Copy-Item (Get-HrmFixturePath "package.valid.zip") (Join-Path $staging "release-222222222222.zip") -Force
        Copy-Item (Get-HrmFixturePath "manifest.valid.json") (Join-Path $staging "release-222222222222.json") -Force
    }
    # HTTP-контракт сервера канала.
    $global:HRM_ChannelWorld = [pscustomobject]@{
        Reports = @()
        EngineCheckCount = 0
        QueueInstall = $QueueInstall
        EngineCheckState = $EngineCheckState
        Facts = @()
        Calls = @()
    }
    Set-HrmHttpMock {
        param($Uri, $Method, $Body, $Headers)
        $w = $global:HRM_ChannelWorld
        $w.Calls += [string]$Uri
        if ($Uri -like "*/api/updates/engine-facts") {
            # Phase 14: факты readiness — захват тела и заголовков.
            $w.Facts += , @{ Body = $Body; Headers = $Headers }
            return @{ StatusCode = 200; Body = "ok" }
        }
        if ($Uri -like "*/api/updates/engine-state") {
            if ($w.QueueInstall) {
                return @{
                    StatusCode = 200
                    Body = [pscustomobject]@{
                        actions = @("install")
                        job_id = "job-test-1"
                        release_dir = "/updates/release-222222222222.zip"
                        manifest_path = "/updates/release-222222222222.json"
                        error_code = $null
                    }
                }
            }
            return @{ StatusCode = 200; Body = [pscustomobject]@{ actions = @(); job_id = $null; release_dir = $null; manifest_path = $null; error_code = $null } }
        }
        if ($Uri -like "*/api/updates/engine-check") {
            $w.EngineCheckCount++
            return @{ StatusCode = 200; Body = [pscustomobject]@{ state = $w.EngineCheckState; available_version = $null } }
        }
        if ($Uri -like "*/api/updates/engine-report") {
            $w.Reports += , @{ Body = $Body; Headers = $Headers }
            return @{ StatusCode = 200; Body = [pscustomobject]@{ state = "up_to_date" } }
        }
        if ($Uri -like "*/api/health") { return @{ StatusCode = 200; Body = [pscustomobject]@{ status = "ok" } } }
        if ($Uri -like "*/api/ops/status") {
            return @{ StatusCode = 200; Body = $global:HRM_MockWorld.OpsBody }
        }
        return @{ StatusCode = 200; Body = "ok" }
    }
    # Канал доверяет тестовым ключам из fixture.
    $keysData = Get-HrmJsonFile (Get-HrmFixturePath "trusted_keys.json")
    $keys = @{}
    foreach ($prop in $keysData.PSObject.Properties) {
        $keys[$prop.Name] = @{ key = [string]$prop.Value.key; revoked = [bool]$prop.Value.revoked }
    }
    Set-HrmChannelConfig -StateDir (Get-HrmTestStateDir) -PublicKeys $keys
    # Установленная запись: версия 0.13.0, sha 33..3.
    $state = Get-HrmTestStateDir
    Initialize-HrmStateDir $state | Out-Null
    Set-HrmInstallRecord $state @{
        release_sha = $ReleaseSha
        install_dir = (Get-HrmTestInstallDir)
        state_dir = $state
        port = 8080
        installed_at = "2026-09-10T00:00:00Z"
        pilot_created = $true
    }
    # Снимок приложения в install dir (установленная версия — 0.13.0).
    Copy-Item (Join-Path $sourceDir "backend") (Get-HrmTestInstallDir) -Recurse -Force
    Copy-Item (Join-Path $sourceDir "frontend") (Get-HrmTestInstallDir) -Recurse -Force
    Copy-Item (Join-Path $sourceDir "infra") (Get-HrmTestInstallDir) -Recurse -Force
    (@{ release_sha = $ReleaseSha; version = "0.13.0" } | ConvertTo-Json) |
        Set-Content -Path (Join-Path (Get-HrmTestInstallDir) "release.json") -Encoding UTF8
    return [pscustomobject]@{ Root = $TestRoot; Staging = $staging; World = $world }
}

Write-Host "== Криптография канала =="

Test-Case "Ed25519: эталонный вектор RFC 8032 §7.1 TEST 1 (пустое сообщение)" {
    Initialize-HrmTestEngine
    New-HrmMockWorld | Out-Null
    # Вектор публичного ключа RFC 8032 дан в hex; конвертируем в base64.
    $pkHex = "d75a980182b10ab7d54bfed3c964073a0ee172f3daa62325af021a68f707511a"
    $pkBytes = ConvertFrom-HrmHex $pkHex
    $pkB64 = ConvertTo-HrmBase64 $pkBytes
    $sigHex = "e5564300c360ac729086e2cc806e828a84877f1eb8e5d974d873e065224901555fb8821590a33bacc61e39701cf9b46bd25bf5f0595bbe24655141438e7a100b"
    # Типизированная переменная: пустой нетипизированный массив PS 5.1
    # не может привязать к параметру [byte[]].
    [byte[]]$message = @()
    $result = Test-HrmEd25519Signature -PublicKeyBase64 $pkB64 -Message $message -SignatureHex $sigHex
    Assert-HrmTrue $result "подпись RFC 8032 не прошла проверку"
}

Test-Case "little-endian: старший байт >= 0x80 даёт положительный BigInteger" {
    Initialize-HrmTestEngine
    New-HrmMockWorld | Out-Null
    # .NET BigInteger.Parse(hex, AllowHexSpecifier) трактует hex со старшим
    # байтом >= 0x80 как двух-дополнительное отрицательное число; конвертация
    # обязана возвращать положительное (иначе биты скаляра читаются неверно).
    $bytes = New-Object byte[] 32
    $bytes[31] = 0x9f
    $bytes[0] = 0x01
    $value = ConvertFrom-HrmLittleEndian $bytes
    Assert-HrmTrue ([System.Numerics.BigInteger]::Compare($value, [System.Numerics.BigInteger]::Zero) -gt 0) "скаляр отрицательный при старшем байте 0x9f"
    Assert-HrmTrue ([System.Numerics.BigInteger]::op_BitwiseAnd($value, [System.Numerics.BigInteger]::One).IsOne) "младший бит не прочитан"
}

Test-Case "manifest: канонические байты совпадают с golden-fixture" {
    Initialize-HrmTestEngine
    New-HrmMockWorld | Out-Null
    $text = Get-HrmFixtureText "manifest.valid.json"
    $manifest = Read-HrmManifestJson $text
    $bytes = Get-HrmCanonicalBytes $manifest
    $actual = [System.Text.Encoding]::UTF8.GetString($bytes)
    $expected = Get-HrmFixtureText "manifest.canonical.txt"
    Assert-HrmEqual $expected $actual "канонизация расходится с Python-реализацией"
}

Test-Case "manifest: валидная подпись принимается доверенным ключом" {
    Initialize-HrmTestEngine
    New-HrmMockWorld | Out-Null
    $manifest = Read-HrmManifestJson (Get-HrmFixtureText "manifest.valid.json")
    $keysData = Get-HrmJsonFile (Get-HrmFixturePath "trusted_keys.json")
    $keys = @{}
    foreach ($prop in $keysData.PSObject.Properties) {
        $keys[$prop.Name] = @{ key = [string]$prop.Value.key; revoked = [bool]$prop.Value.revoked }
    }
    $verified = Test-HrmChannelManifest $manifest $keys
    Assert-HrmEqual "0.14.0" $verified["version"] "версия не прочитана"
    Assert-HrmEqual "pilot-test-key" $verified["key_id"] "key_id не прочитан"
}

Test-Case "manifest: изменённый байт подписи отклоняется" {
    Initialize-HrmTestEngine
    New-HrmMockWorld | Out-Null
    $manifest = Read-HrmManifestJson (Get-HrmFixtureText "manifest.bad_sig.json")
    $keys = @{ "pilot-test-key" = @{ key = (Get-HrmFixtureText "test_key.pub").Trim(); revoked = $false } }
    $code = ""
    try { $null = Test-HrmChannelManifest $manifest $keys }
    catch { $code = $_.Exception.Data["Code"] }
    Assert-HrmEqual "bad_signature" $code "изменённая подпись не отклонена (код $code)"
}

Test-Case "manifest: подмена подписанного поля (tampered) отклоняется" {
    Initialize-HrmTestEngine
    New-HrmMockWorld | Out-Null
    $manifest = Read-HrmManifestJson (Get-HrmFixtureText "manifest.tampered.json")
    $keys = @{ "pilot-test-key" = @{ key = (Get-HrmFixtureText "test_key.pub").Trim(); revoked = $false } }
    $code = ""
    try { $null = Test-HrmChannelManifest $manifest $keys }
    catch { $code = $_.Exception.Data["Code"] }
    Assert-HrmEqual "bad_signature" $code "подменённое поле не отклонено (код $code)"
}

Test-Case "manifest: неизвестный и отозванный ключ отклоняются" {
    Initialize-HrmTestEngine
    New-HrmMockWorld | Out-Null
    $unknown = Read-HrmManifestJson (Get-HrmFixtureText "manifest.unknown_key.json")
    $keys = @{ "pilot-test-key" = @{ key = (Get-HrmFixtureText "test_key.pub").Trim(); revoked = $false } }
    $code = ""
    try { $null = Test-HrmChannelManifest $unknown $keys }
    catch { $code = $_.Exception.Data["Code"] }
    Assert-HrmEqual "unknown_key" $code "неизвестный ключ не отклонён (код $code)"
    $revokedManifest = Read-HrmManifestJson (Get-HrmFixtureText "manifest.revoked_key.json")
    $keysWithRevoked = @{
        "pilot-test-key" = @{ key = (Get-HrmFixtureText "test_key.pub").Trim(); revoked = $false }
        "pilot-revoked-key" = @{ key = (Get-HrmFixtureText "revoked_key.pub").Trim(); revoked = $true }
    }
    $code = ""
    try { $null = Test-HrmChannelManifest $revokedManifest $keysWithRevoked }
    catch { $code = $_.Exception.Data["Code"] }
    Assert-HrmEqual "revoked_key" $code "отозванный ключ не отклонён (код $code)"
}

Test-Case "manifest: неизвестная схема, неверный SemVer и HTTP-URL отклоняются" {
    Initialize-HrmTestEngine
    New-HrmMockWorld | Out-Null
    $keys = @{ "pilot-test-key" = @{ key = (Get-HrmFixtureText "test_key.pub").Trim(); revoked = $false } }
    $schema2 = Read-HrmManifestJson (Get-HrmFixtureText "manifest.schema2.json")
    $code = ""
    try { $null = Test-HrmChannelManifest $schema2 $keys }
    catch { $code = $_.Exception.Data["Code"] }
    Assert-HrmEqual "manifest_invalid" $code "схема v2 не отклонена (код $code)"
    $badSemver = Read-HrmManifestJson (Get-HrmFixtureText "manifest.bad_semver.json")
    $code = ""
    try { $null = Test-HrmChannelManifest $badSemver $keys }
    catch { $code = $_.Exception.Data["Code"] }
    Assert-HrmEqual "manifest_invalid" $code "неверный SemVer не отклонён (код $code)"
}

Test-Case "SemVer: общая таблица случаев (единая с Python-тестами)" {
    Initialize-HrmTestEngine
    New-HrmMockWorld | Out-Null
    $cases = Get-HrmJsonFile (Get-HrmFixturePath "semver_cases.json")
    foreach ($case in $cases) {
        $got = Compare-HrmSemVer ([string]($case[0])) ([string]($case[1]))
        Assert-HrmEqual ([int]($case[2])) $got ("SemVer {0} vs {1}" -f $case[0], $case[1])
    }
}

Test-Case "политика: downgrade/конфликт целостности/minimum — fail closed" {
    Initialize-HrmTestEngine
    New-HrmMockWorld | Out-Null
    $keys = @{ "pilot-test-key" = @{ key = (Get-HrmFixtureText "test_key.pub").Trim(); revoked = $false } }
    $downgrade = Test-HrmChannelManifest (Read-HrmManifestJson (Get-HrmFixtureText "manifest.downgrade.json")) $keys
    Assert-HrmThrows "downgrade не отклонён" { Assert-HrmChannelPolicy $downgrade "0.13.0" ("3" * 40) }
    $conflict = Test-HrmChannelManifest (Read-HrmManifestJson (Get-HrmFixtureText "manifest.same_version_diff_sha.json")) $keys
    Assert-HrmThrows "конфликт sha не отклонён" { Assert-HrmChannelPolicy $conflict "0.13.0" ("3" * 40) }
    $valid = Test-HrmChannelManifest (Read-HrmManifestJson (Get-HrmFixtureText "manifest.valid.json")) $keys
    Assert-HrmThrows "minimum_supported не отклонён" { Assert-HrmChannelPolicy $valid "0.12.0" ("3" * 40) }
    Assert-HrmChannelPolicy $valid "0.13.0" ("3" * 40)
}

Write-Host "== Безопасная распаковка =="

Test-Case "Zip Slip/absolute/UNC/ADS/symlink/backslash/лишний корень/exe отклоняются" {
    Initialize-HrmTestEngine
    New-HrmMockWorld | Out-Null
    $staging = Join-Path ([System.IO.Path]::GetTempPath()) ("hrm-extract-" + [System.Guid]::NewGuid().ToString("N"))
    New-Item -ItemType Directory -Path $staging -Force | Out-Null
    foreach ($attack in @("package.zipslip.zip", "package.abs.zip", "package.unc.zip", "package.ads.zip", "package.symlink.zip", "package.backslash.zip", "package.extra_root.zip", "package.exe.zip")) {
        $threw = $false
        try {
            $null = Expand-HrmPackage (Get-HrmFixturePath $attack) $staging ("2" * 40)
        }
        catch { $threw = $true }
        Assert-HrmTrue $threw ("атакующий пакет не отклонён: " + $attack)
    }
    Remove-Item $staging -Recurse -Force -ErrorAction SilentlyContinue
}

Test-Case "валидный пакет распаковывается; release.json совпадает с manifest" {
    Initialize-HrmTestEngine
    New-HrmMockWorld | Out-Null
    $staging = Join-Path ([System.IO.Path]::GetTempPath()) ("hrm-extract-ok-" + [System.Guid]::NewGuid().ToString("N"))
    New-Item -ItemType Directory -Path $staging -Force | Out-Null
    $expanded = Expand-HrmPackage (Get-HrmFixturePath "package.valid.zip") $staging ("2" * 40)
    Assert-HrmTrue (Test-Path (Join-Path $expanded "release.json")) "нет release.json после распаковки"
    $inner = Get-HrmJsonFile (Join-Path $expanded "release.json")
    Assert-HrmEqual ("2" * 40) ([string]$inner.release_sha) "внутренний sha"
    Remove-Item $staging -Recurse -Force -ErrorAction SilentlyContinue
}

Test-Case "пакет без release.json/с чужим sha отклоняется на уровне установки" {
    Initialize-HrmTestEngine
    New-HrmMockWorld | Out-Null
    $staging = Join-Path ([System.IO.Path]::GetTempPath()) ("hrm-inner-" + [System.Guid]::NewGuid().ToString("N"))
    New-Item -ItemType Directory -Path $staging -Force | Out-Null
    $noRelease = Expand-HrmPackage (Get-HrmFixturePath "package.no_release.zip") $staging ("7" * 40)
    $keys = @{ "pilot-test-key" = @{ key = (Get-HrmFixtureText "test_key.pub").Trim(); revoked = $false } }
    $manifest = Test-HrmChannelManifest (Read-HrmManifestJson (Get-HrmFixtureText "manifest.valid.json")) $keys
    $threw = $false
    try {
        $inner = Get-HrmJsonFile (Join-Path $noRelease "release.json")
        if ($null -eq $inner -or -not $inner.release_sha) { throw "нет release.json" }
        if ([string]$inner.release_sha -ne [string]$manifest["release_sha"]) { throw "sha не совпал" }
    }
    catch { $threw = $true }
    Assert-HrmTrue $threw "пакет без release.json прошёл внутреннюю проверку"
    Remove-Item $staging -Recurse -Force -ErrorAction SilentlyContinue
}

Write-Host "== Наблюдатель канала =="

Test-Case "наблюдатель: команда install → повторная проверка → update engine → отчёт installed" {
    Initialize-HrmTestEngine
    $t = New-HrmChannelWorld -QueueInstall "yes"
    $state = Get-HrmTestStateDir
    $install = Get-HrmTestInstallDir
    Invoke-HrmChannelOnce -InstallDir $install -StateDir $state | Out-Null
    $channelWorld = $global:HRM_ChannelWorld
    Assert-HrmEqual 1 $channelWorld.Reports.Count "отчёт серверу не отправлен"
    $reportDetail = if ($channelWorld.Reports[0].Body.error_detail) { [string]$channelWorld.Reports[0].Body.error_detail } else { "" }
    Assert-HrmEqual "installed" ([string]$channelWorld.Reports[0].Body.state) ("итог не installed (detail: " + $reportDetail + ")")
    Assert-HrmEqual "0.14.0" ([string]$channelWorld.Reports[0].Body.installed_version) "версия в отчёте"
    Assert-HrmEqual ("2" * 40) ([string]$channelWorld.Reports[0].Body.installed_release_sha) "sha в отчёте"
    Assert-HrmEqual "job-test-1" ([string]$channelWorld.Reports[0].Body.job_id) "job_id в отчёте"
    # Переиспользован Phase 12 update engine: бэкап-ворота + миграция + build.
    Assert-HrmEqual 1 $t.World.BackupNowCount "бэкап-ворота не выполнялись"
    Assert-HrmEqual 1 $t.World.AlembicUpgradeCount "миграция не выполнялась"
    Assert-HrmTrue ($t.World.BuildCount -ge 1) "сборка образов не выполнялась"
    # Токен движка передан только в заголовке (не в URL и не в теле).
    $reportHeaders = $channelWorld.Reports[0].Headers
    Assert-HrmEqual (Get-HrmSecret $state "HRM_UPDATE_ENGINE_TOKEN") ([string]$reportHeaders["X-Engine-Token"]) "токен в заголовке"
}

Test-Case "наблюдатель: провал update → отчёт rolled_back (без даунгрейда БД)" {
    Initialize-HrmTestEngine
    $t = New-HrmChannelWorld -QueueInstall "yes"
    $state = Get-HrmTestStateDir
    $install = Get-HrmTestInstallDir
    # Smoke провалится: работающая версия не совпадёт с релизом
    # (SimulateStaleRelease: мок не подменяет release_sha после миграции).
    $t.World.SimulateStaleRelease = $true
    $t.World.OpsBody.release_sha = "3" * 40
    Invoke-HrmChannelOnce -InstallDir $install -StateDir $state | Out-Null
    $channelWorld = $global:HRM_ChannelWorld
    Assert-HrmEqual 1 $channelWorld.Reports.Count "отчёт не отправлен"
    $reportDetail = if ($channelWorld.Reports[0].Body.error_detail) { [string]$channelWorld.Reports[0].Body.error_detail } else { "" }
    Assert-HrmEqual "rolled_back" ([string]$channelWorld.Reports[0].Body.state) "итог не rolled_back"
    Assert-HrmEqual "update_failed" ([string]$channelWorld.Reports[0].Body.error_code) "код ошибки"
    Assert-HrmEqual 6 $t.World.TagCount ("откат к прежним образам не выполнен (detail: " + $reportDetail + ")")
    Assert-HrmEqual ("3" * 40) (Get-HrmInstallRecord $state).release_sha "версия изменилась при провале"
}

Test-Case "наблюдатель: без команд — только фоновая проверка, установки нет" {
    Initialize-HrmTestEngine
    $t = New-HrmChannelWorld -QueueInstall "" -EngineCheckState "available"
    $state = Get-HrmTestStateDir
    $install = Get-HrmTestInstallDir
    Invoke-HrmChannelOnce -InstallDir $install -StateDir $state | Out-Null
    $channelWorld = $global:HRM_ChannelWorld
    Assert-HrmEqual 1 $channelWorld.EngineCheckCount "фоновая проверка не запрошена"
    Assert-HrmEqual 0 $channelWorld.Reports.Count "не должно быть отчёта"
    Assert-HrmEqual 0 $t.World.BackupNowCount "фоновая установка недопустима (бэкап не должен был запуститься)"
}

Test-Case "наблюдатель: сервер недоступен — работа приложения не нарушается" {
    Initialize-HrmTestEngine
    $t = New-HrmChannelWorld -QueueInstall ""
    $state = Get-HrmTestStateDir
    $install = Get-HrmTestInstallDir
    Set-HrmHttpMock {
        param($Uri, $Method, $Body, $Headers)
        return @{ StatusCode = 404; Body = $null }
    }
    Invoke-HrmChannelOnce -InstallDir $install -StateDir $state | Out-Null
    Assert-HrmEqual 0 $t.World.BackupNowCount "офлайн не должен вызывать update"
    Assert-HrmEqual 0 $global:HRM_ChannelWorld.Reports.Count "офлайн не должен вызывать отчёт"
}

Test-Case "канал: хеш/размер пакета проверяются повторно перед установкой" {
    Initialize-HrmTestEngine
    $t = New-HrmChannelWorld -QueueInstall "yes"
    $state = Get-HrmTestStateDir
    $install = Get-HrmTestInstallDir
    # Испорченный пакет в staging (размер/хеш не совпадут с manifest).
    Set-Content -Path (Join-Path $t.Staging "release-222222222222.zip") -Value "tampered" -Encoding ASCII
    Invoke-HrmChannelOnce -InstallDir $install -StateDir $state | Out-Null
    $channelWorld = $global:HRM_ChannelWorld
    Assert-HrmEqual 1 $channelWorld.Reports.Count "отчёт не отправлен"
    Assert-HrmEqual "rolled_back" ([string]$channelWorld.Reports[0].Body.state) "испорченный пакет не остановил установку"
    Assert-HrmEqual 0 $t.World.AlembicUpgradeCount "миграция не должна была выполняться"
}

Write-Host "== Trust store и факты readiness (Phase 14) =="

function New-HrmTestTrustStoreJson {
    # Валидный production trust store как PSCustomObject (как из JSON).
    param([hashtable]$Keys = @{ "pilot-test-key" = @{ key = "RdoOG6nUyIJr4vqrLPQD36UISqCFrLov+HgDcisGKxM="; revoked = $false } })
    return [pscustomobject]@{
        schema_version = 1
        environment = "production"
        keys = [pscustomobject]$Keys
    }
}

Test-Case "trust store: валидный production store принимается и нормализуется" {
    Initialize-HrmTestEngine
    New-HrmChannelWorld | Out-Null
    $store = New-HrmTestTrustStoreJson -Keys @{
        "pilot-test-key" = @{ key = "RdoOG6nUyIJr4vqrLPQD36UISqCFrLov+HgDcisGKxM="; revoked = $false }
        "pilot-old-key" = @{ key = "D83KWJq/Tb9ETFv8x2gNe7DvOfZM0vMY4YKH+aQVvy0="; revoked = $true }
    }
    $validated = Test-HrmTrustStoreObject $store
    Assert-HrmEqual "production" $validated["environment"] "environment"
    Assert-HrmEqual 2 $validated["keys"].Count "число ключей"
    Assert-HrmTrue ($validated["keys"]["pilot-old-key"].revoked) "revoked-флаг не сохранён"
}

Test-Case "trust store: private material отклоняется" {
    Initialize-HrmTestEngine
    New-HrmChannelWorld | Out-Null
    $store = New-HrmTestTrustStoreJson
    $store | Add-Member -NotePropertyName private -NotePropertyValue ("f" * 64)
    Assert-HrmThrows "private material в trust store не отклонён" { Test-HrmTrustStoreObject $store }
    # Приватное поле внутри записи ключа — тоже отказ (лишнее поле записи).
    $store2 = New-HrmTestTrustStoreJson -Keys @{ "pilot-test-key" = @{ key = "RdoOG6nUyIJr4vqrLPQD36UISqCFrLov+HgDcisGKxM="; revoked = $false; private = "f" * 64 } }
    Assert-HrmThrows "private material в записи ключа не отклонён" { Test-HrmTrustStoreObject $store2 }
}

Test-Case "trust store: test-хранилище не становится production trust root" {
    Initialize-HrmTestEngine
    New-HrmChannelWorld | Out-Null
    $store = New-HrmTestTrustStoreJson
    $store.environment = "test"
    Assert-HrmThrows "test-environment store принят как production root" { Test-HrmTrustStoreObject $store }
}

Test-Case "trust store: не-base64 и не-32-байтовые ключи отклоняются" {
    Initialize-HrmTestEngine
    New-HrmChannelWorld | Out-Null
    $bad1 = New-HrmTestTrustStoreJson -Keys @{ "pilot-test-key" = @{ key = "not-base64!!"; revoked = $false } }
    Assert-HrmThrows "некорректный base64 ключ принят" { Test-HrmTrustStoreObject $bad1 }
    # 31 байт -> base64 не соответствует формату 32-байтового ключа
    # (43 символа + '='); 33 байта — тоже. Оба варианта обязаны отклоняться.
    $shortKey = [Convert]::ToBase64String(, (New-Object byte[] 31))
    $bad2 = New-HrmTestTrustStoreJson -Keys @{ "pilot-test-key" = @{ key = $shortKey; revoked = $false } }
    Assert-HrmThrows "31-байтовый ключ принят" { Test-HrmTrustStoreObject $bad2 }
}

Test-Case "trust store: все ключи отозваны / пустой набор / лишние поля / плохой key_id отклоняются" {
    Initialize-HrmTestEngine
    New-HrmChannelWorld | Out-Null
    $allRevoked = New-HrmTestTrustStoreJson -Keys @{ "pilot-test-key" = @{ key = "RdoOG6nUyIJr4vqrLPQD36UISqCFrLov+HgDcisGKxM="; revoked = $true } }
    Assert-HrmThrows "полностью отозванный store принят" { Test-HrmTrustStoreObject $allRevoked }
    $empty = [pscustomobject]@{ schema_version = 1; environment = "production"; keys = [pscustomobject]@{} }
    Assert-HrmThrows "пустой набор ключей принят" { Test-HrmTrustStoreObject $empty }
    $extra = New-HrmTestTrustStoreJson
    $extra | Add-Member -NotePropertyName url -NotePropertyValue "https://evil.example.com"
    Assert-HrmThrows "лишнее поле верхнего уровня принято" { Test-HrmTrustStoreObject $extra }
    $badId = New-HrmTestTrustStoreJson -Keys @{ "ключ с пробелами" = @{ key = "RdoOG6nUyIJr4vqrLPQD36UISqCFrLov+HgDcisGKxM="; revoked = $false } }
    Assert-HrmThrows "некорректный key_id принят" { Test-HrmTrustStoreObject $badId }
    $badSchema = New-HrmTestTrustStoreJson
    $badSchema.schema_version = 2
    Assert-HrmThrows "schema_version=2 принят" { Test-HrmTrustStoreObject $badSchema }
}

Test-Case "Import-HrmTrustStore: публикует только публичные ключи из встроенного store" {
    Initialize-HrmTestEngine
    $t = New-HrmChannelWorld
    $state = Get-HrmTestStateDir
    # Конфигурация канала создана миром — удаляем, имитируя первичную установку.
    $configFile = Get-HrmChannelConfigFile $state
    if (Test-Path $configFile) { Remove-Item $configFile -Force }
    $snapshotDir = Join-Path $t.Root "snapshot-trust"
    New-Item -ItemType Directory -Path $snapshotDir -Force | Out-Null
    $store = @{
        schema_version = 1
        environment = "production"
        keys = @{
            "pilot-test-key" = @{ key = "RdoOG6nUyIJr4vqrLPQD36UISqCFrLov+HgDcisGKxM="; revoked = $false }
            "pilot-old-key" = @{ key = "D83KWJq/Tb9ETFv8x2gNe7DvOfZM0vMY4YKH+aQVvy0="; revoked = $true }
        }
    } | ConvertTo-Json -Depth 5
    Set-Content -Path (Join-Path $snapshotDir "release-trust-store.json") -Value $store -Encoding UTF8
    $imported = Import-HrmTrustStore -SnapshotDir $snapshotDir -StateDir $state
    Assert-HrmTrue $imported "импорт не выполнен"
    $config = Get-HrmChannelConfig $state
    Assert-HrmEqual 2 @($config["public_keys"].PSObject.Properties).Count "ключи не импортированы"
    $importedKey = $config["public_keys"].PSObject.Properties["pilot-test-key"].Value
    Assert-HrmEqual "RdoOG6nUyIJr4vqrLPQD36UISqCFrLov+HgDcisGKxM=" $importedKey.key "материал ключа"
    # В опубликованной конфигурации нет private material и environment-полей.
    $configText = Get-Content $configFile -Raw -Encoding UTF8
    Assert-HrmNotContains $configText "private" "private material в channel.json"
    Assert-HrmNotContains $configText "environment" "environment утёк в публичную конфигурацию"
}

Test-Case "Import-HrmTrustStore: существующая конфигурация не перезаписывается (ротация выигрывает)" {
    Initialize-HrmTestEngine
    $t = New-HrmChannelWorld
    $state = Get-HrmTestStateDir
    # Мир уже создал channel.json с доверенными ключами; администратор отозвал
    # один из них — встроенный store НЕ должен затереть это решение.
    $configFile = Get-HrmChannelConfigFile $state
    Assert-HrmTrue (Test-Path $configFile) "мир не создал channel.json"
    $before = Get-Content $configFile -Raw -Encoding UTF8
    $snapshotDir = Join-Path $t.Root "snapshot-trust"
    New-Item -ItemType Directory -Path $snapshotDir -Force | Out-Null
    @{ schema_version = 1; environment = "production"; keys = @{ "rogue-key" = @{ key = "AdoOG6nUyIJr4vqrLPQD36UISqCFrLov+HgDcisGKxM="; revoked = $false } } } | ConvertTo-Json -Depth 5 |
        Set-Content -Path (Join-Path $snapshotDir "release-trust-store.json") -Encoding UTF8
    $imported = Import-HrmTrustStore -SnapshotDir $snapshotDir -StateDir $state
    Assert-HrmFalse $imported "импорт перезаписал конфигурацию"
    $after = Get-Content $configFile -Raw -Encoding UTF8
    Assert-HrmEqual $before $after "channel.json изменён при импорте"
}

Test-Case "Import-HrmTrustStore: невалидный встроенный store — отказ, конфигурация не создаётся" {
    Initialize-HrmTestEngine
    $t = New-HrmChannelWorld
    $state = Get-HrmTestStateDir
    $configFile = Get-HrmChannelConfigFile $state
    if (Test-Path $configFile) { Remove-Item $configFile -Force }
    $snapshotDir = Join-Path $t.Root "snapshot-trust"
    New-Item -ItemType Directory -Path $snapshotDir -Force | Out-Null
    @{ schema_version = 1; environment = "production"; keys = @{ "pilot-test-key" = @{ key = "not-base64!!"; revoked = $false } } } | ConvertTo-Json -Depth 5 |
        Set-Content -Path (Join-Path $snapshotDir "release-trust-store.json") -Encoding UTF8
    Assert-HrmThrows "невалидный встроенный store принят" { Import-HrmTrustStore -SnapshotDir $snapshotDir -StateDir $state }
    Assert-HrmFalse (Test-Path $configFile) "channel.json создан из невалидного store"
}

Test-Case "Get-HrmEngineFacts: закрытая схема фактов из мок-мира" {
    Initialize-HrmTestEngine
    New-HrmChannelWorld | Out-Null
    $state = Get-HrmTestStateDir
    $facts = Get-HrmEngineFacts -StateDir $state -WatcherRunning $true
    # Ровно поля backend-схемы UpdateEngineFactsRequest (extra=forbid).
    $expected = @("windows_version", "windows_supported", "docker_state", "compose_version", "compose_ok",
        "published_ports", "disk_free_mb", "state_dir_acl_ok", "staging_writable", "staging_outside_state",
        "previous_images_present", "watcher_running")
    Assert-HrmEqual $expected.Count $facts.Keys.Count ("лишние/недостающие поля: " + (($facts.Keys | Sort-Object) -join ","))
    foreach ($name in $expected) {
        Assert-HrmTrue $facts.ContainsKey($name) ("нет поля " + $name)
    }
    # Мок: compose version v2.29.7 -> ok; docker ps/images не замоканы -> пусто.
    Assert-HrmEqual $true $facts["compose_ok"] "compose_ok"
    Assert-HrmEqual "v2.29.7" $facts["compose_version"] "compose_version"
    Assert-HrmEqual 0 @($facts["published_ports"]).Count "published_ports не пуст"
    Assert-HrmEqual $false $facts["previous_images_present"] "previous_images_present"
    Assert-HrmEqual $false $facts["state_dir_acl_ok"] "icacls-мок с пустым выводом должен давать false"
    Assert-HrmEqual $true $facts["staging_writable"] "staging должен быть записываем"
    Assert-HrmEqual $true $facts["watcher_running"] "watcher_running не передан"
    # Типы: published_ports — массив, disk_free_mb — число.
    Assert-HrmTrue ($facts["disk_free_mb"] -is [long] -or $facts["disk_free_mb"] -is [int]) "disk_free_mb не число"
}

Test-Case "Get-HrmEngineFacts: опубликованные порты парсятся из docker ps" {
    Initialize-HrmTestEngine
    New-HrmChannelWorld | Out-Null
    $state = Get-HrmTestStateDir
    $global:HRM_MockWorld.DockerPsPorts = "hr-manager-pilot-backend|0.0.0.0:5432->5432/tcp, 127.0.0.1:8080->80/tcp`nhr-manager-pilot-frontend|127.0.0.1:8081->80/tcp`n"
    $global:HRM_MockWorld.DockerImagesOutput = "hr-manager-pilot-backend:previous`nhr-manager-pilot-frontend:latest`n"
    $facts = Get-HrmEngineFacts -StateDir $state -WatcherRunning $false
    $ports = @($facts["published_ports"])
    Assert-HrmEqual 3 $ports.Count "порты не распарсены"
    $exposed = @($ports | Where-Object { $_["host_ip"] -eq "0.0.0.0" })
    Assert-HrmEqual 1 $exposed.Count "0.0.0.0-порт не найден"
    Assert-HrmEqual 5432 $exposed[0]["host_port"] "host_port"
    Assert-HrmEqual "hr-manager-pilot-backend" $exposed[0]["container"] "container"
    $loopback = @($ports | Where-Object { $_["host_ip"] -eq "127.0.0.1" })
    Assert-HrmEqual 2 $loopback.Count "loopback-порты не найдены"
    Assert-HrmEqual $true $facts["previous_images_present"] "previous-образы не найдены"
}

Test-Case "Send-HrmEngineFacts: POST с машинным токеном, кеш 5 минут, Reset-HrmEngineFactsCache" {
    Initialize-HrmTestEngine
    New-HrmChannelWorld | Out-Null
    $state = Get-HrmTestStateDir
    Reset-HrmEngineFactsCache
    Send-HrmEngineFacts -StateDir $state -WatcherRunning $true
    $world = $global:HRM_ChannelWorld
    Assert-HrmEqual 1 @($world.Facts).Count "факты не отправлены"
    $sent = $world.Facts[0]
    $token = Get-HrmSecret $state "HRM_UPDATE_ENGINE_TOKEN"
    Assert-HrmEqual $token ([string]$sent.Headers["X-Engine-Token"]) "машинный токен не в заголовке"
    # Тело — только закрытая схема фактов (никаких секретов и путей).
    $bodyJson = $sent.Body | ConvertTo-Json -Depth 5
    Assert-HrmNotContains $bodyJson $token "токен утёк в тело фактов"
    Assert-HrmNotContains $bodyJson "password" "секреты в теле фактов"
    Assert-HrmTrue ($sent.Body.ContainsKey("windows_version")) "нет windows_version"
    # Кеш: повторная отправка в течение 5 минут не создаёт второй POST.
    Send-HrmEngineFacts -StateDir $state -WatcherRunning $true
    Assert-HrmEqual 1 @($world.Facts).Count "кеш фактов не работает"
    # Тестовый шов сбрасывает кеш.
    Reset-HrmEngineFactsCache
    Send-HrmEngineFacts -StateDir $state -WatcherRunning $true
    Assert-HrmEqual 2 @($world.Facts).Count "Reset-HrmEngineFactsCache не сбросил кеш"
}

Test-Case "наблюдатель: факты отправляются ДО опроса engine-state; -SkipFacts отключает" {
    Initialize-HrmTestEngine
    New-HrmChannelWorld -QueueInstall "" | Out-Null
    $state = Get-HrmTestStateDir
    $install = Get-HrmTestInstallDir
    Reset-HrmEngineFactsCache
    Invoke-HrmChannelOnce -InstallDir $install -StateDir $state | Out-Null
    $world = $global:HRM_ChannelWorld
    Assert-HrmEqual 1 @($world.Facts).Count "факты не отправлены наблюдателем"
    $factsIndex = [array]::IndexOf(@($world.Calls), (@($world.Calls | Where-Object { $_ -like "*/api/updates/engine-facts" })[0]))
    $stateIndex = [array]::IndexOf(@($world.Calls), (@($world.Calls | Where-Object { $_ -like "*/api/updates/engine-state" })[0]))
    Assert-HrmTrue ($factsIndex -ge 0 -and $stateIndex -ge 0 -and $factsIndex -lt $stateIndex) "факты отправлены не до engine-state"
    # -SkipFacts: без отправки фактов (ручной запуск/диагностика).
    Reset-HrmEngineFactsCache
    $world.Facts = @()
    $world.Calls = @()
    Invoke-HrmChannelOnce -InstallDir $install -StateDir $state -SkipFacts | Out-Null
    Assert-HrmEqual 0 @($world.Facts).Count "-SkipFacts не отключил отправку"
    Assert-HrmEqual 1 $world.EngineCheckCount "фоновая проверка не выполнена"
}

Test-Case "наблюдатель: сбой отправки фактов не мешает циклу обновлений" {
    Initialize-HrmTestEngine
    $t = New-HrmChannelWorld -QueueInstall "yes"
    $state = Get-HrmTestStateDir
    $install = Get-HrmTestInstallDir
    Reset-HrmEngineFactsCache
    # Факты отклоняются сервером (500) — установка всё равно выполняется
    # и отчёт отправляется (сбой facts не влияет на канал обновлений).
    Set-HrmHttpMock {
        param($Uri, $Method, $Body, $Headers)
        $w = $global:HRM_ChannelWorld
        $w.Calls += [string]$Uri
        if ($Uri -like "*/api/updates/engine-facts") { return @{ StatusCode = 500; Body = $null } }
        if ($Uri -like "*/api/updates/engine-state") {
            return @{ StatusCode = 200; Body = [pscustomobject]@{ actions = @("install"); job_id = "job-facts-1"; release_dir = "/updates/release-222222222222.zip"; manifest_path = "/updates/release-222222222222.json"; error_code = $null } }
        }
        if ($Uri -like "*/api/updates/engine-report") {
            $w.Reports += , @{ Body = $Body; Headers = $Headers }
            return @{ StatusCode = 200; Body = [pscustomobject]@{ state = "up_to_date" } }
        }
        return @{ StatusCode = 200; Body = "ok" }
    }
    Invoke-HrmChannelOnce -InstallDir $install -StateDir $state | Out-Null
    $world = $global:HRM_ChannelWorld
    Assert-HrmEqual 1 @($world.Reports).Count "установка не выполнена из-за сбоя facts"
    Assert-HrmEqual "installed" ([string]$world.Reports[0].Body.state) "итог не installed"
    Assert-HrmTrue ($t.World.BackupNowCount -ge 1) "update-цикл не дошёл до бэкап-ворот"
}

Write-Host ("Тесты канала: {0} пройдено, {1} провалено" -f $global:HRM_TestPassed, $global:HRM_TestFailed)
