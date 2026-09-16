# Phase 14: подпись Setup.exe (Authenticode) + attestation для release-пайплайна.
#
# Две НЕЗАВИСИМЫЕ подписи релиза:
#   1. Ed25519-подпись канала (update-channel.json) — всегда обязательна;
#   2. Authenticode-подпись installer'а — этот скрипт.
# Одна подпись никогда не заменяет другую: production policy в
# infra/release/publish_channel.py требует проверки обеих.
#
# РЕЖИМЫ:
#   -Mode production — сертификат владельца (PFX из environment-секрета) +
#     обязательная RFC3161-метка времени. Приватный ключ не печатается и не
#     попадает в артефакты: PFX импортируется в пользовательское хранилище и
#     удаляется после подписи; пароль приходит только через переменную
#     окружения и никогда не попадает в командную строку signtool.
#   -Mode test — ephemeral самоподписанный сертификат ТОЛЬКО для CI-проверки
#     контракта (PR/CI без production-секретов). Attestation получает
#     mode="test", поэтому production policy такой релиз не пропускает.
#
# Использование:
#   powershell -NoProfile -ExecutionPolicy Bypass -File installer\sign.ps1 `
#     -Mode test -Version 0.14.0
#
#   $env:HRM_AUTHENTICODE_PFX_PASSWORD = "<из секрета>"
#   powershell -NoProfile -ExecutionPolicy Bypass -File installer\sign.ps1 `
#     -Mode production -Version 0.14.0 -PfxPath "$env:RUNNER_TEMP\cert.pfx" `
#     -ExpectedPublisher "ООО «Ромашка»" -TimestampUrl "http://timestamp.digicert.com" `
#     -TrustStoreFile "$env:RUNNER_TEMP\trust-store.json"
#
# Результат: installer/authenticode-attestation.json и
# installer/authenticode-roots.pem (публичные сертификаты для независимой
# проверки вне Windows), обновлённый installer/release-manifest.json.

[CmdletBinding()]
param(
    [ValidateSet("test", "production")]
    [string]$Mode = "test",
    [string]$Version = "",
    [string]$SetupExe = "",
    [string]$PfxPath = "",
    [string]$ExpectedPublisher = "",
    [string]$TimestampUrl = "",
    [string]$TrustStoreFile = "",
    [string]$AttestationPath = "",
    [string]$RootsPath = ""
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version 2.0

$installerDir = $PSScriptRoot
if (-not $Version) { throw "нужен -Version" }
if (-not $SetupExe) {
    $SetupExe = Join-Path (Join-Path $installerDir "output") ("HR-Manager-Setup-" + $Version + ".exe")
}
if (-not (Test-Path $SetupExe)) { throw "Setup.exe не найден: $SetupExe" }
if (-not $AttestationPath) { $AttestationPath = Join-Path $installerDir "authenticode-attestation.json" }
if (-not $RootsPath) { $RootsPath = Join-Path $installerDir "authenticode-roots.pem" }

function Get-HrmSigntool {
    # Только закреплённый Windows SDK; никаких загрузок из сети.
    $roots = @(
        (Join-Path ${env:ProgramFiles(x86)} "Windows Kits\10\bin"),
        (Join-Path $env:ProgramFiles "Windows Kits\10\bin")
    )
    foreach ($root in $roots) {
        if (-not (Test-Path $root)) { continue }
        $candidates = @(Get-ChildItem -Path $root -Recurse -Filter "signtool.exe" -ErrorAction SilentlyContinue |
            Where-Object { $_.FullName -match "\\x64\\signtool\.exe$" } |
            Sort-Object FullName -Descending)
        if ($candidates.Count -gt 0) { return $candidates[0].FullName }
    }
    throw "signtool.exe не найден: Windows SDK обязателен для подписи релиза"
}

function Get-HrmPublisherName {
    # Издатель = Organization (O) сертификата, иначе Common Name (CN) —
    # ровно то значение, которое показывает UAC/signtool и сверяет
    # Python-верификатор Authenticode.
    param([System.Security.Cryptography.X509Certificates.X509Certificate2]$Certificate)
    $subject = $Certificate.Subject
    foreach ($prefix in @("O=", "CN=")) {
        $pattern = [regex]::Escape($prefix) + '((?:[^,]|,(?=[^=]*=))*?)(?=,\s*[A-Za-z]+\s*=|$)'
        $match = [regex]::Match($subject, $pattern)
        if ($match.Success) {
            $value = $match.Groups[1].Value.Trim().Trim('"')
            if ($value) { return $value }
        }
    }
    return $subject
}

function Get-HrmSha256Hex {
    param([byte[]]$Bytes)
    $sha = [System.Security.Cryptography.SHA256]::Create()
    try {
        return ([System.BitConverter]::ToString($sha.ComputeHash($Bytes)) -replace "-", "").ToLowerInvariant()
    }
    finally { $sha.Dispose() }
}

function Export-HrmCertificatePem {
    param(
        [System.Security.Cryptography.X509Certificates.X509Certificate2[]]$Certificates,
        [string]$Path
    )
    $builder = New-Object System.Text.StringBuilder
    foreach ($certificate in $Certificates) {
        $raw = $certificate.Export([System.Security.Cryptography.X509Certificates.X509ContentType]::Cert)
        [void]$builder.AppendLine("-----BEGIN CERTIFICATE-----")
        [void]$builder.AppendLine([Convert]::ToBase64String($raw, [Base64FormattingOptions]::InsertLineBreaks))
        [void]$builder.AppendLine("-----END CERTIFICATE-----")
    }
    [System.IO.File]::WriteAllText($Path, $builder.ToString(), (New-Object System.Text.UTF8Encoding($false)))
}

function Read-HrmTrustStoreFacts {
    # Только публичные факты: sha256 файла + key_id/отпечаток/статус отзыва.
    param([string]$Path)
    if (-not $Path) { return $null }
    if (-not (Test-Path $Path)) { throw "trust store не найден: $Path" }
    $text = [System.IO.File]::ReadAllText($Path, [System.Text.Encoding]::UTF8)
    if ($text -match "PRIVATE KEY|BEGIN .*PRIVATE") {
        throw "trust store содержит приватный материал — подпись релиза запрещена"
    }
    $data = $text | ConvertFrom-Json
    $keys = @()
    foreach ($property in $data.PSObject.Properties) {
        $entry = $property.Value
        $keyBytes = [Convert]::FromBase64String([string]$entry.key)
        if ($keyBytes.Length -ne 32) { throw ("ключ {0}: ожидались 32 байта Ed25519" -f $property.Name) }
        $fingerprint = "SHA256:" + (Get-HrmSha256Hex $keyBytes).Substring(0, 16)
        $keys += [ordered]@{
            key_id = $property.Name
            fingerprint = $fingerprint
            revoked = [bool]$entry.revoked
        }
    }
    return [ordered]@{
        file = (Split-Path $Path -Leaf)
        sha256 = (Get-FileHash -Path $Path -Algorithm SHA256).Hash.ToLowerInvariant()
        keys = $keys
    }
}

$signtool = Get-HrmSigntool
$testCertificate = $null
$importedCertificate = $null
$passwordForSigning = ""

Write-Host ("== HR Manager Authenticode signing ({0}) ==" -f $Mode)

try {
    if ($Mode -eq "production") {
        if (-not $PfxPath -or -not (Test-Path $PfxPath)) { throw "production: нужен -PfxPath (из защищённого секрета)" }
        if (-not $TimestampUrl) { throw "production: нужен -TimestampUrl: метка времени обязательна" }
        if (-not $ExpectedPublisher) { throw "production: нужен -ExpectedPublisher" }
        if (-not $env:HRM_AUTHENTICODE_PFX_PASSWORD) {
            throw "production: пароль PFX обязан приходить через переменную окружения HRM_AUTHENTICODE_PFX_PASSWORD"
        }
        # Импорт в пользовательское хранилище: пароль не попадает в
        # командную строку signtool (подпись идёт по отпечатку).
        $pfxBytes = [System.IO.File]::ReadAllBytes($PfxPath)
        $importedCertificate = New-Object System.Security.Cryptography.X509Certificates.X509Certificate2(
            $pfxBytes,
            $env:HRM_AUTHENTICODE_PFX_PASSWORD,
            [System.Security.Cryptography.X509Certificates.X509KeyStorageFlags]"PersistKeySet"
        )
        $store = New-Object System.Security.Cryptography.X509Certificates.X509Store("My", "CurrentUser")
        $store.Open("ReadWrite")
        try { $store.Add($importedCertificate) } finally { $store.Close() }
        $signingThumbprint = $importedCertificate.Thumbprint
    }
    else {
        # Ephemeral тестовый сертификат: только CurrentUser, только CI-контракт.
        $testCertificate = New-SelfSignedCertificate `
            -Subject "O=HR Manager CI (test only), CN=HR Manager CI Test Publisher" `
            -Type Custom `
            -KeyUsage DigitalSignature `
            -KeyLength 2048 `
            -KeyAlgorithm RSA `
            -HashAlgorithm SHA256 `
            -CertStoreLocation "Cert:\CurrentUser\My" `
            -NotAfter (Get-Date).AddDays(2) `
            -TextExtension @("2.5.29.37={text}1.3.6.1.5.5.7.3.3")
        # signtool verify обязан доверять тестовой цепочке — временно
        # добавляем самоподписанный сертификат в пользовательские Root и
        # TrustedPublisher; в finally он удаляется.
        $cerPath = Join-Path ([System.IO.Path]::GetTempPath()) ("hrm-test-signing-" + [Guid]::NewGuid().ToString("N") + ".cer")
        [System.IO.File]::WriteAllBytes($cerPath, $testCertificate.Export([System.Security.Cryptography.X509Certificates.X509ContentType]::Cert))
        & certutil.exe -user -f -addstore Root $cerPath | Out-Null
        if ($LASTEXITCODE -ne 0) { throw "certutil: не удалось добавить тестовый корень в CurrentUser\Root" }
        & certutil.exe -user -f -addstore TrustedPublisher $cerPath | Out-Null
        if ($LASTEXITCODE -ne 0) { throw "certutil: не удалось добавить тестовый издатель" }
        Remove-Item $cerPath -Force -ErrorAction SilentlyContinue
        $signingThumbprint = $testCertificate.Thumbprint
    }

    # 1. Подпись. /fd SHA256 и /sha1 — без приватного материала в командной
    #    строке; /tr — RFC3161 TSA владельца.
    $signArgs = @("sign", "/fd", "SHA256", "/sha1", $signingThumbprint, "/d", "HR Manager")
    if ($Mode -eq "production") { $signArgs += @("/tr", $TimestampUrl, "/td", "SHA256") }
    $signArgs += $SetupExe
    & $signtool @signArgs
    if ($LASTEXITCODE -ne 0) { throw ("signtool sign завершился с кодом {0}" -f $LASTEXITCODE) }

    # 2. Проверка подписи средствами Windows: /pa /all.
    & $signtool verify /pa /all $SetupExe
    if ($LASTEXITCODE -ne 0) { throw ("signtool verify /pa /all не подтвердил подпись (код {0})" -f $LASTEXITCODE) }

    $signature = Get-AuthenticodeSignature -FilePath $SetupExe
    if ($null -eq $signature.SignerCertificate) { throw "после подписи у файла нет SignerCertificate" }
    if ($signature.Status -ne "Valid") { throw ("статус подписи {0} вместо Valid" -f $signature.Status) }

    $publisher = Get-HrmPublisherName -Certificate $signature.SignerCertificate
    $timestampPresent = $null -ne $signature.TimeStamperCertificate
    if ($Mode -eq "production" -and -not $timestampPresent) {
        throw "production: после подписи нет метки времени (TSA недоступна?)"
    }
    if ($Mode -eq "production" -and $publisher -ne $ExpectedPublisher) {
        throw ("production: издатель сертификата '{0}' не совпал с ожидаемым" -f $publisher)
    }

    # 3. Публичная цепочка для независимой проверки вне Windows.
    $chainCerts = @($signature.SignerCertificate)
    if ($signature.TimeStamperCertificate) { $chainCerts += $signature.TimeStamperCertificate }
    Export-HrmCertificatePem -Certificates $chainCerts -Path $RootsPath

    # 4. Attestation: только публичные факты (ни ключей, ни паролей).
    $trustStore = Read-HrmTrustStoreFacts -Path $TrustStoreFile
    $trustStoreSha = ""
    if ($trustStore) { $trustStoreSha = $trustStore.sha256 }
    $attestation = [ordered]@{
        schema = 1
        mode = $Mode
        authenticode_present = $true
        signtool_verify_ok = $true
        timestamp_present = $timestampPresent
        publisher = $publisher
        installer = (Split-Path $SetupExe -Leaf)
        installer_sha256 = (Get-FileHash -Path $SetupExe -Algorithm SHA256).Hash.ToLowerInvariant()
        signer_subject = $signature.SignerCertificate.Subject
        signer_thumbprint_sha256 = Get-HrmSha256Hex $signature.SignerCertificate.RawData
        signer_not_after = $signature.SignerCertificate.NotAfter.ToUniversalTime().ToString("o")
        timestamp_url = $TimestampUrl
        tool = "installer/sign.ps1"
        trust_store = $trustStore
    }
    $attestation | ConvertTo-Json -Depth 6 | Set-Content -Path $AttestationPath -Encoding UTF8

    # 5. Манифест релиза: честный статус подписи.
    $manifestPath = Join-Path $installerDir "release-manifest.json"
    if (Test-Path $manifestPath) {
        $manifest = Get-Content $manifestPath -Raw | ConvertFrom-Json
    }
    else {
        $manifest = New-Object System.Management.Automation.PSCustomObject
    }
    $signingInfo = [ordered]@{
        status = $Mode
        mode = $Mode
        attestation = (Split-Path $AttestationPath -Leaf)
        installer_sha256 = $attestation.installer_sha256
        publisher = $publisher
        timestamp_present = $timestampPresent
        trust_store_sha256 = $trustStoreSha
    }
    $manifest | Add-Member -NotePropertyName "signing" -NotePropertyValue $signingInfo -Force
    $manifest | ConvertTo-Json -Depth 8 | Set-Content -Path $manifestPath -Encoding UTF8

    Write-Host ("Подпись проверена: {0}" -f $SetupExe)
    Write-Host ("Издатель: {0}; метка времени: {1}" -f $publisher, $timestampPresent)
    Write-Host ("Attestation: {0}" -f $AttestationPath)
}
finally {
    if ($importedCertificate) {
        $store = New-Object System.Security.Cryptography.X509Certificates.X509Store("My", "CurrentUser")
        $store.Open("ReadWrite")
        try {
            foreach ($item in $store.Certificates.Find("FindByThumbprint", $importedCertificate.Thumbprint, $false)) {
                $store.Remove($item)
            }
        }
        finally { $store.Close() }
    }
    if ($testCertificate) {
        foreach ($storeName in @("Root", "TrustedPublisher", "My")) {
            $store = New-Object System.Security.Cryptography.X509Certificates.X509Store($storeName, "CurrentUser")
            $store.Open("ReadWrite")
            try {
                foreach ($item in $store.Certificates.Find("FindByThumbprint", $testCertificate.Thumbprint, $false)) {
                    $store.Remove($item)
                }
            }
            finally { $store.Close() }
        }
    }
    if ($PfxPath -and (Test-Path $PfxPath) -and $env:HRM_SIGN_CLEANUP_PFX -eq "1") {
        Remove-Item $PfxPath -Force -ErrorAction SilentlyContinue
    }
}
