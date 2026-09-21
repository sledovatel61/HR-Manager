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
#     Подпись обязана быть полностью подтверждена: `signtool verify /pa`
#     возвращает Valid, издатель совпадает с ожидаемым, метка времени есть,
#     независимый верификатор подтверждает цепочку до production-корня.
#   -Mode test — ephemeral самоподписанный сертификат ТОЛЬКО для CI-проверки
#     контракта (PR/CI без production-секретов). Attestation получает
#     mode="test" и честный signtool_verify_ok, поэтому production policy
#     такой релиз не пропускает.
#
# ПОЧЕМУ TEST-РЕЖИМ НЕ ТРОГАЕТ СИСТЕМНЫЕ ХРАНИЛИЩА:
#   добавление самоподписанного корня в Root/TrustedPublisher (certutil или
#   X509Store) на GitHub-hosted runner показывает диалог подтверждения
#   безопасности и висит до таймаута job'а. Test-режим держит сертификат
#   только в Cert:\CurrentUser\My, ничего не добавляет в доверенные корни и
#   не выдаёт себя за доверенную Windows-подпись.
#
# ЧЕСТНОСТЬ ПРОВЕРКИ В TEST-РЕЖИМЕ:
#   Windows не может подтвердить цепочку ephemeral-корня — это единственный
#   допустимый «недостаток», и он разрешён ТОЛЬКО когда статус подписи ровно
#   «корень не доверен». Любая другая причина (HashMismatch, NotSigned и т.п.)
#   — ошибка. Дополнительно подпись проверяется независимо:
#   infra/release/authenticode.py пересчитывает Authenticode-хеш PE, проверяет
#   CMS-подпись и доводит цепочку до экспортированного корня. То есть
#   test-режим проверяет криптографию, а не «файл чем-то подписан».
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
# Код возврата: 0 — подпись выполнена и проверена, 1 — любой отказ (fail closed).
# Скрипт всегда завершается явным exit: без этого $LASTEXITCODE вызывающей
# стороны остаётся от последней внешней команды (signtool verify), и CI
# ошибочно считал успешную подпись провалом.
#
# Результат: installer/authenticode-attestation.json,
# installer/authenticode-roots.pem (публичные сертификаты для независимой
# проверки вне Windows), installer/authenticode-verification.json (отчёт
# независимого верификатора) и обновлённый installer/release-manifest.json.

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
    [string]$RootsPath = "",
    [string]$VerificationPath = "",
    # Доверенные якоря, заданные ВНЕ проверяемого файла. Без них проверка
    # цепочки вырождается в тавтологию: если корни взять из самой подписи,
    # её примет любой самоподписанный сертификат.
    [string]$SignerRootsPath = "",
    [string]$TimestampRootsPath = ""
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version 2.0

$installerDir = $PSScriptRoot
$repoRoot = Split-Path $installerDir -Parent
if (-not $Version) { throw "нужен -Version" }
if (-not $SetupExe) {
    $SetupExe = Join-Path (Join-Path $installerDir "output") ("HR-Manager-Setup-" + $Version + ".exe")
}
if (-not (Test-Path $SetupExe)) { throw "Setup.exe не найден: $SetupExe" }
if (-not $AttestationPath) { $AttestationPath = Join-Path $installerDir "authenticode-attestation.json" }
if (-not $RootsPath) { $RootsPath = Join-Path $installerDir "authenticode-roots.pem" }
if (-not $VerificationPath) { $VerificationPath = Join-Path $installerDir "authenticode-verification.json" }
if ($Mode -eq "production") {
    # Production обязан проверять цепочку до заранее закреплённого корня.
    # Корни из самой подписи здесь запрещены: это не проверка, а тавтология.
    if (-not $SignerRootsPath) { throw "production: нужен -SignerRootsPath (закреплённый корень издателя)" }
    if (-not (Test-Path $SignerRootsPath)) { throw "production: -SignerRootsPath не найден: $SignerRootsPath" }
    if (-not $TimestampRootsPath) { throw "production: нужен -TimestampRootsPath (закреплённый корень TSA)" }
    if (-not (Test-Path $TimestampRootsPath)) { throw "production: -TimestampRootsPath не найден: $TimestampRootsPath" }
}

function Write-HrmUtf8NoBom {
    # Python-часть release-пайплайна читает JSON через json.loads без BOM-фильтра:
    # Set-Content -Encoding UTF8 в Windows PowerShell 5.1 пишет BOM и ломает разбор.
    param([string]$Path, [string]$Text)
    [System.IO.File]::WriteAllText($Path, $Text, (New-Object System.Text.UTF8Encoding($false)))
}

function Invoke-HrmNative {
    # Внешняя команда с полным выводом в лог. В Windows PowerShell 5.1 связка
    # "$ErrorActionPreference='Stop'" + "2>&1" превращает stderr внешней
    # программы в terminating error, поэтому на время вызова предпочтение
    # снижается, а код возврата читается явно из $LASTEXITCODE.
    param(
        [string]$FilePath,
        [string[]]$Arguments,
        [string]$Label
    )
    Write-Host ("Running: {0} {1}" -f $FilePath, ($Arguments -join " "))
    $previous = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try {
        $output = & $FilePath @Arguments 2>&1 | Out-String
    }
    finally {
        $ErrorActionPreference = $previous
    }
    $code = $LASTEXITCODE
    if ($output) {
        foreach ($line in ($output -split "`r?`n")) {
            if ($line.Trim().Length -gt 0) { Write-Host ("  [{0}] {1}" -f $Label, $line) }
        }
    }
    return [pscustomobject]@{ ExitCode = $code; Output = $output }
}

function Get-HrmSigntool {
    # Только закреплённый Windows SDK; никаких загрузок из сети.
    Write-Host "Searching for signtool.exe..."
    $roots = @()
    if ($env:ProgramFiles) { $roots += (Join-Path $env:ProgramFiles "Windows Kits\10\bin") }
    $x86 = [System.Environment]::GetEnvironmentVariable("ProgramFiles(x86)")
    if ($x86) { $roots += (Join-Path $x86 "Windows Kits\10\bin") }
    foreach ($root in $roots) {
        if (-not (Test-Path $root)) { Write-Host "Root not found: $root"; continue }
        Write-Host "Scanning $root for signtool.exe (x64)..."
        # Быстрый путь: where.exe; запасной — обход каталога SDK.
        $candidates = @()
        try {
            $where = (where.exe signtool.exe 2>$null) | Where-Object { $_ -match "\\x64\\signtool\.exe$" }
            if ($where) { $candidates = @($where | ForEach-Object { Get-Item $_ }) }
        }
        catch {}
        if ($candidates.Count -eq 0) {
            $candidates = @(Get-ChildItem -Path $root -Filter "signtool.exe" -Recurse -ErrorAction SilentlyContinue |
                Where-Object { $_.FullName -match "\\x64\\signtool\.exe$" } |
                Sort-Object FullName -Descending)
        }
        Write-Host "Found $($candidates.Count) candidates in $root"
        if ($candidates.Count -gt 0) {
            Write-Host "Using signtool: $($candidates[0].FullName)"
            return $candidates[0].FullName
        }
    }
    throw "signtool.exe не найден: Windows SDK обязателен для подписи релиза"
}

function Get-HrmPython {
    # Независимая проверка Authenticode выполняется Python-верификатором
    # репозитория; без него подпись не считается подтверждённой.
    foreach ($name in @("python", "python3")) {
        if (Get-Command $name -ErrorAction SilentlyContinue) { return @($name) }
    }
    if (Get-Command "py" -ErrorAction SilentlyContinue) { return @("py", "-3") }
    throw "python не найден: независимая проверка Authenticode обязательна"
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

function Test-HrmUntrustedRootOnly {
    # Единственная причина неподтверждённой цепочки, которую test-режим имеет
    # право пережить: корень не входит в доверенные корни Windows. Всё
    # остальное (битый хеш, отсутствующая подпись, несовместимый формат) — отказ.
    param([string]$Status, [string]$Message)
    if ($Status -eq "NotTrusted") { return $true }
    if ($Status -ne "UnknownError") { return $false }
    return [bool]($Message -match "not trusted by the trust provider|untrusted root|CERT_E_UNTRUSTEDROOT")
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
    Write-HrmUtf8NoBom -Path $Path -Text $builder.ToString()
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
$scriptExitCode = 0

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
        # Ephemeral тестовый сертификат: только CurrentUser\My, только CI-контракт.
        # Ни Root, ни TrustedPublisher не трогаем (см. заголовок скрипта).
        $testCertificate = New-SelfSignedCertificate `
            -Subject "O=HR Manager CI (test only), CN=HR Manager CI Test Publisher" `
            -Type Custom `
            -KeyUsage DigitalSignature `
            -KeyLength 2048 `
            -KeyAlgorithm RSA `
            -HashAlgorithm SHA256 `
            -CertStoreLocation "Cert:\CurrentUser\My" `
            -NotBefore (Get-Date).AddMinutes(-5) `
            -NotAfter (Get-Date).AddDays(2) `
            -TextExtension @("2.5.29.37={text}1.3.6.1.5.5.7.3.3")
        Write-Host ("Ephemeral test certificate created: thumbprint={0}, CurrentUser\My only" -f $testCertificate.Thumbprint)
        $signingThumbprint = $testCertificate.Thumbprint
    }

    # 1. Подпись. /fd SHA256 и /sha1 — без приватного материала в командной
    #    строке; /tr — RFC3161 TSA владельца (только production).
    Write-Host "Signing $SetupExe with thumbprint $signingThumbprint (mode=$Mode)..."
    $signArgs = @("sign", "/fd", "SHA256", "/sha1", $signingThumbprint, "/d", "HR Manager")
    if ($Mode -eq "production") { $signArgs += @("/tr", $TimestampUrl, "/td", "SHA256") }
    $signArgs += $SetupExe
    $signResult = Invoke-HrmNative -FilePath $signtool -Arguments $signArgs -Label "signtool sign"
    if ($signResult.ExitCode -ne 0) {
        throw ("signtool sign завершился с кодом {0}" -f $signResult.ExitCode)
    }
    Write-Host "signtool sign succeeded"

    # 2. Проверка подписи средствами Windows.
    Write-Host "Verifying signature (signtool verify /pa)..."
    $verifyResult = Invoke-HrmNative -FilePath $signtool -Arguments @("verify", "/pa", $SetupExe) -Label "signtool verify"
    $verifyExit = [int]$verifyResult.ExitCode
    Write-Host "signtool verify /pa exit code: $verifyExit"

    $signature = Get-AuthenticodeSignature -FilePath $SetupExe
    $windowsStatus = [string]$signature.Status
    $windowsStatusMessage = [string]$signature.StatusMessage
    Write-Host "Get-AuthenticodeSignature status: $windowsStatus - $windowsStatusMessage"

    if ($null -eq $signature.SignerCertificate) { throw "после подписи у файла нет SignerCertificate" }
    if ($windowsStatus -eq "NotSigned") { throw ("файл не подписан: статус {0}" -f $windowsStatus) }
    if ($windowsStatus -eq "HashMismatch") {
        throw "Authenticode-хеш файла не совпал с подписью (HashMismatch): файл изменён после подписи"
    }

    $windowsChainTrusted = ($windowsStatus -eq "Valid")
    $signtoolVerifyOk = ($verifyExit -eq 0) -and $windowsChainTrusted
    if (-not $signtoolVerifyOk) {
        if ($windowsChainTrusted) {
            throw ("Windows считает подпись действительной, но signtool verify /pa вернул код {0}" -f $verifyExit)
        }
        if (-not (Test-HrmUntrustedRootOnly -Status $windowsStatus -Message $windowsStatusMessage)) {
            throw ("Windows не подтвердил подпись: статус {0} ({1}); это не «недоверенный корень»" -f `
                $windowsStatus, $windowsStatusMessage)
        }
        if ($Mode -eq "production") {
            throw ("production: подпись не подтверждена Windows: статус {0} ({1}), код signtool {2}" -f `
                $windowsStatus, $windowsStatusMessage, $verifyExit)
        }
        if ($signature.SignerCertificate.Thumbprint -ne $testCertificate.Thumbprint) {
            throw ("test: подпись выполнена сертификатом {0}, а не созданным ephemeral-сертификатом {1}" -f `
                $signature.SignerCertificate.Thumbprint, $testCertificate.Thumbprint)
        }
        Write-Host ("TEST MODE ONLY: цепочка не доводится до доверенного корня Windows ({0})." -f $windowsStatusMessage)
        Write-Host "test-сертификат намеренно НЕ добавляется в Root/TrustedPublisher, поэтому"
        Write-Host "подпись подтверждается независимым верификатором ниже, а attestation"
        Write-Host "получает mode=test и signtool_verify_ok=false (production policy такой релиз не примет)."
    }

    $publisher = Get-HrmPublisherName -Certificate $signature.SignerCertificate
    $timestampPresent = $null -ne $signature.TimeStamperCertificate
    if ($Mode -eq "production" -and -not $timestampPresent) {
        throw "production: после подписи нет метки времени (TSA недоступна?)"
    }
    if ($Mode -eq "production" -and $publisher -ne $ExpectedPublisher) {
        throw ("production: издатель сертификата '{0}' не совпал с ожидаемым" -f $publisher)
    }

    # 3. Доверенные якоря для независимой проверки вне Windows.
    #    ЯКОРЬ НЕ БЕРЁТСЯ ИЗ ПРОВЕРЯЕМОГО ФАЙЛА. $signature.SignerCertificate —
    #    это сертификат из самой подписи: положив его в --trust-roots, мы
    #    разрешаем любому самоподписанному сертификату «доводить цепочку до
    #    доверенного корня». Проверено PoC: посторонний издатель проходил
    #    chain_verified=true и timestamp_chain_verified=true при --require-timestamp.
    #    Поэтому:
    #      test       — корень, созданный ЭТИМ запуском ($testCertificate), он
    #                   известен независимо от файла и доказывает, что подписано
    #                   именно нашим ephemeral-сертификатом;
    #      production — закреплённые владельцем PEM'ы из -SignerRootsPath /
    #                   -TimestampRootsPath.
    if ($Mode -eq "production") {
        $trustRootsFile = $SignerRootsPath
        $timestampRootsFile = $TimestampRootsPath
        # Артефакт фиксирует, КАКОЙ якорь использован (аудит), и не содержит
        # сертификата подписанта.
        Write-HrmUtf8NoBom -Path $RootsPath -Text ([System.IO.File]::ReadAllText(
            $SignerRootsPath, [System.Text.Encoding]::UTF8))
        Write-Host "Trust roots: pinned by the operator ($SignerRootsPath)"
    }
    else {
        if ($null -eq $testCertificate) { throw "test: ephemeral-сертификат не создан" }
        Export-HrmCertificatePem -Certificates @($testCertificate) -Path $RootsPath
        $trustRootsFile = $RootsPath
        $timestampRootsFile = ""
        Write-Host ("Trust roots: ephemeral test certificate {0} (создан этим запуском)" -f `
            $testCertificate.Thumbprint)
    }

    # 4. Независимая проверка: Authenticode-хеш PE + CMS + цепочка до корня.
    #    Не зависит от системного Root trust, поэтому одинаково строга в обоих
    #    режимах и не позволяет «подписи существовать» вместо «подпись верна».
    Write-Host "Independent Authenticode verification (infra/release/authenticode.py)..."
    $verifier = Join-Path (Join-Path $repoRoot "infra") (Join-Path "release" "authenticode.py")
    if (-not (Test-Path $verifier)) { throw "независимый верификатор не найден: $verifier" }
    $pythonCommand = @(Get-HrmPython)
    $verifyArgs = @($verifier, "verify", "--file", $SetupExe, "--trust-roots", $trustRootsFile,
        "--expected-publisher", $publisher, "--json-out", $VerificationPath)
    if ($Mode -eq "production") {
        # Явно: корни TSA не наследуются от корней издателя.
        $verifyArgs += @("--require-timestamp", "--timestamp-roots", $timestampRootsFile)
    }
    $pythonExe = [string]$pythonCommand[0]
    $pythonPrefix = @()
    if ($pythonCommand.Count -gt 1) { $pythonPrefix = @($pythonCommand[1..($pythonCommand.Count - 1)]) }
    $verifyPy = Invoke-HrmNative -FilePath $pythonExe -Arguments (@($pythonPrefix) + $verifyArgs) -Label "verify"
    if ($verifyPy.ExitCode -ne 0) {
        # Причина обязана доехать до check-runs: полные логи джоба доступны не
        # всегда, а ::error с кодом и detail читается из аннотаций.
        $reason = "отчёт верификатора отсутствует"
        if (Test-Path $VerificationPath) {
            try {
                $failure = [System.IO.File]::ReadAllText($VerificationPath, [System.Text.Encoding]::UTF8) |
                    ConvertFrom-Json
                $reason = ("{0}: {1}" -f $failure.error_code, $failure.error_detail)
            }
            catch { Write-Host "отчёт верификатора не разобран: $($_.Exception.Message)" }
        }
        $tail = (($verifyPy.Output -split "`r?`n") |
            Where-Object { $_.Trim().Length -gt 0 } | Select-Object -Last 5) -join " | "
        throw ("независимая проверка Authenticode не прошла (код {0}); {1}; вывод верификатора: {2}" -f `
            $verifyPy.ExitCode, $reason, $tail)
    }
    if (-not (Test-Path $VerificationPath)) {
        throw "независимый верификатор не создал отчёт: $VerificationPath"
    }
    $independent = [System.IO.File]::ReadAllText($VerificationPath, [System.Text.Encoding]::UTF8) | ConvertFrom-Json
    if ([bool]$independent.signed -ne $true) { throw "независимая проверка: файл не подписан" }
    if ([bool]$independent.chain_verified -ne $true) {
        throw "независимая проверка: цепочка сертификатов не доведена до доверенного корня"
    }
    if ([string]$independent.signer_thumbprint_sha256 -ne (Get-HrmSha256Hex $signature.SignerCertificate.RawData)) {
        throw "независимая проверка: отпечаток подписанта не совпал с сертификатом Windows"
    }
    Write-Host ("Independent verification passed: digest={0}, chain_verified=True" -f $independent.digest_algorithm)

    # 5. Attestation: только публичные факты (ни ключей, ни паролей) и только
    #    фактические результаты проверок — ничего не «дорисовывается».
    $trustStore = Read-HrmTrustStoreFacts -Path $TrustStoreFile
    $trustStoreSha = ""
    if ($trustStore) { $trustStoreSha = $trustStore.sha256 }
    $attestation = [ordered]@{
        schema = 1
        mode = $Mode
        authenticode_present = ($windowsStatus -ne "NotSigned")
        signtool_verify_ok = $signtoolVerifyOk
        signtool_verify_exit_code = $verifyExit
        windows_signature_status = $windowsStatus
        windows_chain_trusted = $windowsChainTrusted
        independent_verification = [ordered]@{
            tool = "infra/release/authenticode.py"
            passed = $true
            chain_verified = [bool]$independent.chain_verified
            digest_algorithm = [string]$independent.digest_algorithm
            signer_thumbprint_sha256 = [string]$independent.signer_thumbprint_sha256
        }
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
    Write-HrmUtf8NoBom -Path $AttestationPath -Text ($attestation | ConvertTo-Json -Depth 6)

    # 6. Манифест релиза: честный статус подписи.
    $manifestPath = Join-Path $installerDir "release-manifest.json"
    if (Test-Path $manifestPath) {
        $manifestText = [System.IO.File]::ReadAllText($manifestPath, [System.Text.Encoding]::UTF8)
        $manifest = $manifestText | ConvertFrom-Json
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
        signtool_verify_ok = $signtoolVerifyOk
        windows_chain_trusted = $windowsChainTrusted
        timestamp_present = $timestampPresent
        trust_store_sha256 = $trustStoreSha
    }
    $manifest | Add-Member -NotePropertyName "signing" -NotePropertyValue $signingInfo -Force
    Write-HrmUtf8NoBom -Path $manifestPath -Text ($manifest | ConvertTo-Json -Depth 8)

    Write-Host ("Подпись выполнена: {0}" -f $SetupExe)
    Write-Host ("Издатель: {0}; метка времени: {1}; Windows chain trusted: {2}; signtool verify ok: {3}" -f `
        $publisher, $timestampPresent, $windowsChainTrusted, $signtoolVerifyOk)
    Write-Host ("Attestation: {0}" -f $AttestationPath)
}
catch {
    $scriptExitCode = 1
    $message = ($_.Exception.Message -replace "[`r`n]+", " | ")
    Write-Host ("::error title=HRM-AUTHENTICODE::sign.ps1 ({0}) отказал: {1}" -f $Mode, $message)
    if ($_.ScriptStackTrace) { Write-Host $_.ScriptStackTrace }
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
        # Test-сертификат создавался только в CurrentUser\My — там же и удаляется.
        foreach ($storeName in @("My")) {
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

exit $scriptExitCode
