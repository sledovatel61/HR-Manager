# Кодовая подпись Windows-установщика (Authenticode, Phase 14).
#
# Второй, НЕЗАВИСИМЫЙ уровень подписи рядом с Ed25519-подписью канала
# обновлений (Phase 13): installer подписывается Authenticode, канал
# по-прежнему проверяет detached Ed25519 manifest. Подпись НЕ заменяет и не
# ослабляет проверки канала.
#
# РЕЖИМЫ (-Mode):
#   production — сертификат владельца из GitHub environment. Секреты приходят
#                ТОЛЬКО через переменные окружения (никогда через аргументы):
#                  HRM_SIGNING_PFX_BASE64   — сертификат PFX в base64;
#                  HRM_SIGNING_PFX_PASSWORD — пароль PFX;
#                  HRM_SIGNING_TIMESTAMP_URL — RFC 3161 timestamp-сервер;
#                  HRM_SIGNING_PUBLISHER    — ожидаемый Subject подписи.
#                Пароль читается в SecureString и НИКОГДА не попадает в
#                командную строку: PFX импортируется в хранилище текущего
#                пользователя (Import-PfxCertificate), подпись выполняется
#                по отпечатку (signtool /sha1). Отсутствие любого секрета в
#                production-режиме — фатальная ошибка (fail closed), без
#                отката к неподписанной сборке.
#   test       — ephemeral самоподписанный сертификат CodeSigningCert для
#                CI/fixture-прогона контракта. Тестовый сертификат помечается
#                certificate_kind=test-self-signed и НИКОГДА не проходит
#                production-политику (infra/release/installer_signing.py).
#                HRM_SIGNING_TIMESTAMP_URL в test-режиме позволяет указать
#                локальный эфемерный RFC 3161 TSA (drill_tsa_server.py).
#   disabled   — подпись не выполняется (локальные сборки; статус честно
#                остаётся unsigned, production-выпуск это отклонит).
#
# После подписи: signtool verify /pa /all, проверка publisher, пересчёт
# SHA256 exe и перезапись блока signing в installer/release-manifest.json.

[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidateSet("production", "test", "disabled")]
    [string]$Mode,

    [string]$Version = "",

    # Каталог манифеста (по умолчанию — каталог этого скрипта).
    [string]$InstallerDir = ""
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version 2.0

if (-not $InstallerDir) { $InstallerDir = $PSScriptRoot }
$outputDir = Join-Path $InstallerDir "output"

function Find-HrmSigntool {
    # signtool из Windows SDK (windows-latest). Поиск по стандартным путям.
    $candidates = @()
    $command = Get-Command signtool.exe -ErrorAction SilentlyContinue
    if ($command) { $candidates += $command.Source }
    $kitsRoot = "${env:ProgramFiles(x86)}\Windows Kits\10\bin"
    if (Test-Path $kitsRoot) {
        foreach ($version in (Get-ChildItem $kitsRoot -Directory | Sort-Object Name -Descending)) {
            $candidates += (Join-Path $version.FullName "x64\signtool.exe")
        }
    }
    foreach ($candidate in $candidates) {
        if ($candidate -and (Test-Path $candidate)) { return $candidate }
    }
    throw "signtool.exe не найден (требуется Windows SDK)."
}

function Write-HrmSigningManifest {
    # Пересчёт SHA256 подписанного exe и перезапись блока signing.
    param(
        [string]$ExePath,
        [string]$Status,
        [string]$SignMode,
        [string]$Publisher,
        [string]$TimestampUrl,
        [bool]$TimestampValid,
        [string]$CertificateKind,
        [string]$SigntoolVerify
    )
    $manifestPath = Join-Path $InstallerDir "release-manifest.json"
    if (-not (Test-Path $manifestPath)) { throw "release-manifest.json не найден: $manifestPath" }
    $manifest = Get-Content $manifestPath -Raw -Encoding UTF8 | ConvertFrom-Json
    $exeHash = (Get-FileHash -Path $ExePath -Algorithm SHA256).Hash.ToLowerInvariant()
    $signing = [ordered]@{
        status = $Status
        mode = $SignMode
        publisher = $Publisher
        timestamp_url = $TimestampUrl
        timestamp_valid = $TimestampValid
        digest = "SHA256"
        certificate_kind = $CertificateKind
        signtool_verify = $SigntoolVerify
        instruction = "infra/release/installer_signing.py verify-manifest --mode production"
    }
    $installerExe = [ordered]@{
        file = [System.IO.Path]::GetFileName($ExePath)
        sha256 = $exeHash
    }
    # PowerShell 5.1: замена вложенных свойств PSCustomObject через Add-Member.
    $manifest.signing = $null
    $manifest.installer_exe = $null
    $manifest | Add-Member NoteProperty signing ([pscustomobject]$signing) -Force
    $manifest | Add-Member NoteProperty installer_exe ([pscustomobject]$installerExe) -Force
    $manifest | ConvertTo-Json -Depth 8 | Set-Content -Path $manifestPath -Encoding UTF8
    return $exeHash
}

function Get-HrmTimestampValid {
    # Timestamp считается валидным, когда подпись несёт сертификат штампа
    # времени (RFC 3161) — signtool verify /pa /all проверяет цепочку целиком.
    param([string]$ExePath)
    $signature = Get-AuthenticodeSignature -FilePath $ExePath
    if ($null -ne $signature.TimeStamperCertificate -and $signature.TimeStamperCertificate.Subject) {
        return $true
    }
    return $false
}

# --- Неподписанный режим (честный, production его отклонит) ---------------------

if ($Mode -eq "disabled") {
    Write-Host "Режим disabled: подпись не выполняется (статус остаётся unsigned)."
    exit 0
}

# --- Определение exe -----------------------------------------------------------

$setupPattern = if ($Version) { "HR-Manager-Setup-" + $Version + ".exe" } else { "HR-Manager-Setup-*.exe" }
$setupExe = Get-ChildItem -Path $outputDir -Filter $setupPattern -ErrorAction SilentlyContinue |
    Sort-Object LastWriteTime -Descending | Select-Object -First 1
if (-not $setupExe) { throw "Установщик не найден: $outputDir\$setupPattern" }
$exePath = $setupExe.FullName
$signtool = Find-HrmSigntool
Write-Host ("Подписываем: {0}" -f $setupExe.Name)

# --- Подготовка сертификата (без секретов в командной строке) --------------------

$thumbprint = ""
$certificateKind = ""
$signerCert = $null
$timestampUrl = ""
$expectedPublisher = ""
$storeLocation = "Cert:\CurrentUser\My"
$tempFiles = @()

try {
    if ($Mode -eq "production") {
        $pfxBase64 = $env:HRM_SIGNING_PFX_BASE64
        $pfxPassword = $env:HRM_SIGNING_PFX_PASSWORD
        $timestampUrl = $env:HRM_SIGNING_TIMESTAMP_URL
        $expectedPublisher = $env:HRM_SIGNING_PUBLISHER
        if (-not $pfxBase64) { throw "production: HRM_SIGNING_PFX_BASE64 не задан (fail closed)." }
        if (-not $pfxPassword) { throw "production: HRM_SIGNING_PFX_PASSWORD не задан (fail closed)." }
        if (-not $timestampUrl) { throw "production: HRM_SIGNING_TIMESTAMP_URL не задан (fail closed)." }
        if (-not $expectedPublisher) { throw "production: HRM_SIGNING_PUBLISHER не задан (fail closed)." }
        # PFX — во временный файл (RUNNER_TEMP, не артефакт), удаление в finally.
        $tempDir = if ($env:RUNNER_TEMP) { $env:RUNNER_TEMP } else { [System.IO.Path]::GetTempPath() }
        $pfxPath = Join-Path $tempDir ("hrm-signing-" + [System.Guid]::NewGuid().ToString("N") + ".pfx")
        [System.IO.File]::WriteAllBytes($pfxPath, [Convert]::FromBase64String($pfxBase64))
        $tempFiles += $pfxPath
        # Пароль — SecureString в процессе, НЕ в аргументах какой-либо команды.
        $securePassword = ConvertTo-SecureString -String $pfxPassword -AsPlainText -Force
        $imported = Import-PfxCertificate -FilePath $pfxPath -CertStoreLocation $storeLocation -Password $securePassword
        $signerCert = $imported
        $thumbprint = $imported.Thumbprint
        $certificateKind = "production"
        Write-Host "Сертификат production импортирован в хранилище пользователя (пароль не покидал процесс)."
    }
    else {
        # test: ephemeral самоподписанный сертификат (только CI/fixture).
        $subject = "CN=HR Manager CI Test Signing"
        $testCert = New-SelfSignedCertificate -Type CodeSigningCert -Subject $subject -CertStoreLocation $storeLocation
        $signerCert = $testCert
        $thumbprint = $testCert.Thumbprint
        $certificateKind = "test-self-signed"
        # CI может подставить локальный эфемерный RFC 3161 TSA
        # (infra/scripts/drill_tsa_server.py) через HRM_SIGNING_TIMESTAMP_URL:
        # контракт timestamp проверяется полностью, без внешней сети.
        $timestampUrl = if ($env:HRM_SIGNING_TIMESTAMP_URL) { $env:HRM_SIGNING_TIMESTAMP_URL } else { "http://timestamp.digicert.com" }
        $expectedPublisher = $subject
        # Чтобы signtool verify /pa прошёл на тестовом раннере, сертификат
        # добавляется в Root/TrustedPublisher ТОЛЬКО этого ephemeral раннера.
        $tempDir = if ($env:RUNNER_TEMP) { $env:RUNNER_TEMP } else { [System.IO.Path]::GetTempPath() }
        $cerPath = Join-Path $tempDir ("hrm-test-signing-" + $thumbprint + ".cer")
        [System.IO.File]::WriteAllBytes($cerPath, $testCert.Export([System.Security.Cryptography.X509Certificates.X509ContentType]::Cert))
        $tempFiles += $cerPath
        $null = Import-Certificate -FilePath $cerPath -CertStoreLocation "Cert:\CurrentUser\Root"
        $null = Import-Certificate -FilePath $cerPath -CertStoreLocation "Cert:\CurrentUser\TrustedPublisher"
        Write-Host "Тестовый сертификат создан и доверен только в рамках ephemeral-раннера."
    }

    # --- Подпись (signtool: без пароля и ключа в командной строке) --------------

    $signArgs = @(
        "sign", "/fd", "SHA256",
        "/tr", $timestampUrl, "/td", "SHA256",
        "/sha1", $thumbprint,
        $exePath
    )
    & $signtool @signArgs
    if ($LASTEXITCODE -ne 0) { throw "signtool sign завершился с кодом $LASTEXITCODE" }

    # --- Независимая проверка: signtool verify /pa /all --------------------------

    $verifyArgs = @("verify", "/pa", "/all", $exePath)
    & $signtool @verifyArgs
    if ($LASTEXITCODE -ne 0) { throw "signtool verify /pa /all НЕ ПРОШЁЛ (код $LASTEXITCODE)" }
    $signtoolVerify = "passed"

    # --- Publisher и timestamp ---------------------------------------------------

    $signature = Get-AuthenticodeSignature -FilePath $exePath
    $publisher = if ($signature.SignerCertificate -and $signature.SignerCertificate.Subject) {
        $signature.SignerCertificate.Subject
    } else { "" }
    $timestampValid = Get-HrmTimestampValid $exePath
    if (-not $timestampValid) {
        throw "timestamp подписи отсутствует или невалиден (fail closed)."
    }
    $normalizedPublisher = ($publisher -replace "\s+", " ").Trim()
    $normalizedExpected = ($expectedPublisher -replace "\s+", " ").Trim()
    if (-not $normalizedPublisher -or ($normalizedPublisher -ne $normalizedExpected -and -not $normalizedPublisher.StartsWith($normalizedExpected, [System.StringComparison]::OrdinalIgnoreCase))) {
        throw "publisher подписи не совпал с ожидаемым: '$normalizedPublisher'"
    }

    # --- Публичный сертификат подписанта (PEM) для независимой проверки --------
    # Сертификат и так встроен в подпись exe (public material, секретов нет):
    # PEM выгружается рядом с манифестом, чтобы независимый верификатор
    # (infra/release/authenticode_verify.py) в test-режиме мог проверить
    # цепочку до фактического эфемерного корня, а не до системного хранилища.
    $pemBody = [Convert]::ToBase64String(`
        $signerCert.Export([System.Security.Cryptography.X509Certificates.X509ContentType]::Cert),`
        [System.Base64FormattingOptions]::InsertLineBreaks)
    $signerPem = "-----BEGIN CERTIFICATE-----`n" + $pemBody + "`n-----END CERTIFICATE-----`n"
    [System.IO.File]::WriteAllText((Join-Path $outputDir "signer-public.pem"), $signerPem, [System.Text.Encoding]::ASCII)
    Write-Host "Публичный сертификат подписанта: installer/output/signer-public.pem"

    # --- Пересчёт SHA256 и перезапись манифеста ----------------------------------

    $exeHash = Write-HrmSigningManifest -ExePath $exePath -Status "signed" -SignMode $Mode `
        -Publisher $normalizedPublisher -TimestampUrl $timestampUrl -TimestampValid $true `
        -CertificateKind $certificateKind -SigntoolVerify $signtoolVerify
    Write-Host ("Подпись применена. SHA256 установщика (пересчитан): {0}" -f $exeHash)
    Write-Host ("publisher: {0}" -f $normalizedPublisher)
    Write-Host ("certificate_kind: {0}" -f $certificateKind)
    exit 0
}
finally {
    # Секретный материал удаляется всегда: PFX-файл и сертификат из хранилища.
    foreach ($tempFile in $tempFiles) {
        if (Test-Path $tempFile) { Remove-Item $tempFile -Force -ErrorAction SilentlyContinue }
    }
    if ($thumbprint) {
        foreach ($store in @("Cert:\CurrentUser\My", "Cert:\CurrentUser\Root", "Cert:\CurrentUser\TrustedPublisher")) {
            $certPath = Join-Path $store $thumbprint
            if (Test-Path $certPath) {
                Remove-Item $certPath -Force -ErrorAction SilentlyContinue
            }
        }
    }
}
