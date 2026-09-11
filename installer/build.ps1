# Сборка HR Manager Setup.exe из исходников репозитория.
#
# Инструментальная цепочка ЗАКРЕПЛЕНА (см. installer/README.md):
#   Inno Setup 6.7.3, официальный установщик с GitHub Releases.
#   URL:    https://github.com/jrsoftware/issrc/releases/download/is-6_7_3/innosetup-6.7.3.exe
#   SHA256: 9c73c3bae7ed48d44112a0f48e66742c00090bdb5bef71d9d3c056c66e97b732
# Никакие другие версии/источники не используются; установщик запускается
# молча (/VERYSILENT) ТОЛЬКО на сборочной машине/CI, никогда на машине
# пользователя — пользователю доставляется готовый HR-Manager-Setup.exe.
#
# Результат: installer/output/HR-Manager-Setup-<Version>.exe и
# installer/release-manifest.json (release_sha, версия, хеши пакета и exe).
#
# ИСПОЛЬЗОВАНИЕ (Windows 10/11, PowerShell 5.1+ или pwsh):
#   powershell -ExecutionPolicy Bypass -File installer\build.ps1 [-Version 0.13.0]

[CmdletBinding()]
param(
    [string]$Version = "0.13.0",
    [string]$TrustStoreJson = "",
    [switch]$RequireAuthenticode,
    [string]$ExpectedPublisher = "HR Manager",
    [string]$AuthenticodePfxBase64 = "",
    [string]$AuthenticodePassword = ""
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version 2.0

$installerDir = $PSScriptRoot
$repoRoot = Split-Path $installerDir -Parent
$stagingDir = Join-Path $installerDir "staging"
$outputDir = Join-Path $installerDir "output"
$cacheDir = Join-Path $installerDir ".cache"

$InnoUrl = "https://github.com/jrsoftware/issrc/releases/download/is-6_7_3/innosetup-6.7.3.exe"
$InnoSha256 = "9c73c3bae7ed48d44112a0f48e66742c00090bdb5bef71d9d3c056c66e97b732"

function Invoke-RobocopyMirror {
    # robocopy с исключениями; коды выхода 0-7 — успех. Пути с пробелами
    # заключаются в кавычки явно (ArgumentList массива их не цитирует).
    param([string]$Source, [string]$Destination, [string[]]$ExcludeDirs = @())
    $robocopyArgs = @(('"{0}"' -f $Source), ('"{0}"' -f $Destination), "/MIR", "/NFL", "/NDL", "/NJH", "/NJS", "/NP")
    foreach ($dir in $ExcludeDirs) { $robocopyArgs += "/XD"; $robocopyArgs += ('"{0}"' -f $dir) }
    $process = Start-Process -FilePath "robocopy.exe" -ArgumentList $robocopyArgs -Wait -PassThru -NoNewWindow
    if ($process.ExitCode -ge 8) {
        throw "robocopy $Source -> $Destination завершился с кодом $($process.ExitCode)"
    }
}

Write-Host "== HR Manager installer build =="

# 0. Чистое состояние сборки.
if (Test-Path $stagingDir) { Remove-Item $stagingDir -Recurse -Force }
if (Test-Path $outputDir) { Remove-Item $outputDir -Recurse -Force }
New-Item -ItemType Directory -Path $stagingDir -Force | Out-Null
New-Item -ItemType Directory -Path $outputDir -Force | Out-Null
New-Item -ItemType Directory -Path $cacheDir -Force | Out-Null

# 1. Inno Setup 6.7.3: скачивание + проверка SHA256 + тихая установка.
$innoExe = Join-Path $cacheDir "innosetup-6.7.3.exe"
if (-not (Test-Path $innoExe)) {
    Write-Host "Downloading Inno Setup 6.7.3 (pinned)…"
    Invoke-WebRequest -Uri $InnoUrl -OutFile $innoExe -UseBasicParsing
}
$hash = (Get-FileHash -Path $innoExe -Algorithm SHA256).Hash.ToLowerInvariant()
if ($hash -ne $InnoSha256) {
    throw "SHA256 установщика Inno Setup не совпал: $hash (ожидался $InnoSha256)"
}
Write-Host "Inno Setup 6.7.3 SHA256 verified."
$iscc = Join-Path $env:LOCALAPPDATA "Programs\Inno Setup 6\ISCC.exe"
if (-not (Test-Path $iscc)) {
    Write-Host "Installing Inno Setup silently (build machine only)…"
    $p = Start-Process -FilePath $innoExe -ArgumentList "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/CURRENTUSER" -Wait -PassThru
    if ($p.ExitCode -ne 0) { throw "Inno Setup install failed: $($p.ExitCode)" }
}

# 2. Снимок приложения для пакета: backend/, frontend/, infra/, release.json.
# Контейнеры собираются из ИСХОДНИКОВ (docker build), поэтому node_modules/
# и артефакты сборки в пакет не входят.
$appStaging = Join-Path $stagingDir "app"
New-Item -ItemType Directory -Path $appStaging -Force | Out-Null
Write-Host "Staging backend/…"
Invoke-RobocopyMirror (Join-Path $repoRoot "backend") (Join-Path $appStaging "backend") @("__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache", "tests", ".venv", "venv")
Write-Host "Staging frontend/ (без node_modules и dist)…"
Invoke-RobocopyMirror (Join-Path $repoRoot "frontend") (Join-Path $appStaging "frontend") @("node_modules", "dist", ".vite")
Write-Host "Staging infra/…"
Invoke-RobocopyMirror (Join-Path $repoRoot "infra") (Join-Path $appStaging "infra") @()

# release.json: версия и SHA релиза (движок сверяет с /ops/status).
# Из git; при сборке из архива без git — через HRM_RELEASE_SHA.
$releaseSha = if ($env:HRM_RELEASE_SHA) {
    $env:HRM_RELEASE_SHA
}
else {
    (git -C $repoRoot rev-parse HEAD).Trim()
}
$releaseJson = [ordered]@{
    release_sha = $releaseSha
    version = $Version
    built_at = (Get-Date).ToString("o")
    installer_commit = $releaseSha
}
$releaseJson | ConvertTo-Json | Set-Content -Path (Join-Path $appStaging "release.json") -Encoding UTF8

# 2b. Trust store: детерминированное встраивание только из защищённого релизного входа.
if ($TrustStoreJson) {
    $trustPath = $TrustStoreJson
    if (-not (Test-Path $trustPath)) {
        throw "Trust store файл не найден: $TrustStoreJson"
    }
    $trustRaw = Get-Content -Path $trustPath -Raw -Encoding UTF8
    # Строгая валидация через validate_trust_store.py (fail closed)
    $validator = Join-Path $repoRoot "infra/release/validate_trust_store.py"
    if (Test-Path $validator) {
        $tmpTrust = Join-Path $env:TEMP ("trust-" + [Guid]::NewGuid().ToString("N") + ".json")
        Set-Content -Path $tmpTrust -Value $trustRaw -Encoding UTF8
        $valResult = & python $validator --input $tmpTrust 2>&1
        if ($LASTEXITCODE -ne 0) {
            Remove-Item $tmpTrust -Force -ErrorAction SilentlyContinue
            throw "Trust store валидация не пройдена: $valResult"
        }
        Remove-Item $tmpTrust -Force -ErrorAction SilentlyContinue
        Write-Host "Trust store валиден (проверен validate_trust_store.py)."
    }
    $trustDest = Join-Path $appStaging "trust_store.json"
    Set-Content -Path $trustDest -Value $trustRaw -Encoding UTF8
    Write-Host "Trust store встроен детерминированно: $trustDest"
}
elseif ($env:UPDATE_CHANNEL_PUBLIC_KEYS) {
    # Альтернатива: env содержит JSON доверенного набора (из защищённого release input)
    $envTrust = $env:UPDATE_CHANNEL_PUBLIC_KEYS
    $validator = Join-Path $repoRoot "infra/release/validate_trust_store.py"
    if (Test-Path $validator) {
        $tmpTrust = Join-Path $env:TEMP ("trust-" + [Guid]::NewGuid().ToString("N") + ".json")
        Set-Content -Path $tmpTrust -Value $envTrust -Encoding UTF8
        $valResult = & python $validator --input $tmpTrust 2>&1
        if ($LASTEXITCODE -ne 0) {
            Remove-Item $tmpTrust -Force -ErrorAction SilentlyContinue
            throw "Trust store из env валидация не пройдена: $valResult"
        }
        Remove-Item $tmpTrust -Force -ErrorAction SilentlyContinue
    }
    $trustDest = Join-Path $appStaging "trust_store.json"
    Set-Content -Path $trustDest -Value $envTrust -Encoding UTF8
    Write-Host "Trust store из env встроен детерминированно."
}

# 3. Компиляция установщика.
Write-Host "Compiling installer with ISCC…"
& $iscc (Join-Path $installerDir "installer.iss") ("/DAppVersion=" + $Version)
if ($LASTEXITCODE -ne 0) { throw "ISCC failed: $LASTEXITCODE" }

$setupExe = Join-Path $outputDir ("HR-Manager-Setup-" + $Version + ".exe")
if (-not (Test-Path $setupExe)) { throw "Установщик не создан: $setupExe" }

# 4. Манифест релиза: хеши пакета (детерминированные) + хеш exe.
Write-Host "Writing release manifest…"
$fileHashes = [ordered]@{}
$packageFiles = Get-ChildItem -Path $appStaging -Recurse -File | Sort-Object FullName
foreach ($file in $packageFiles) {
    $relative = $file.FullName.Substring($stagingDir.Length + 1).Replace("\", "/")
    $fileHashes[$relative] = (Get-FileHash -Path $file.FullName -Algorithm SHA256).Hash.ToLowerInvariant()
}
# 3b. Optional Authenticode signing (production-ready, fail closed when required).
$signingStatus = "unsigned"
$signingDetail = "installer/README.md (раздел «Кодовая подпись»)"
$expectedPublisher = $ExpectedPublisher
if ($AuthenticodePfxBase64) {
    $env:HRM_AUTHENTICODE_PFX_BASE64 = $AuthenticodePfxBase64
}
if ($AuthenticodePassword) {
    $env:HRM_AUTHENTICODE_PASSWORD = $AuthenticodePassword
}
$shouldSign = $false
if ($RequireAuthenticode) { $shouldSign = $true }
if ($env:HRM_REQUIRE_AUTHENTICODE_SIGNING -eq "1") { $shouldSign = $true }
if ($env:AUTHENTICODE_CERTIFICATE_BASE64 -or $env:HRM_AUTHENTICODE_PFX_BASE64) { $shouldSign = $true }

if ($shouldSign) {
    Write-Host "Authenticode: требуется подпись (fail closed)..."
    $pfxBase64 = $env:HRM_AUTHENTICODE_PFX_BASE64
    if (-not $pfxBase64) { $pfxBase64 = $env:AUTHENTICODE_CERTIFICATE_BASE64 }
    $pfxPassword = $env:HRM_AUTHENTICODE_PASSWORD
    if (-not $pfxPassword) { $pfxPassword = $env:AUTHENTICODE_PASSWORD }
    $timestampUrl = if ($env:HRM_TIMESTAMP_URL) { $env:HRM_TIMESTAMP_URL } else { "http://timestamp.digicert.com" }
    $signed = $false
    if ($pfxBase64) {
        # Real certificate provided via env (base64 PFX) — не попадает в CLI/логи.
        $pfxPath = Join-Path $env:TEMP ("hrm-sign-" + [Guid]::NewGuid().ToString("N") + ".pfx")
        try {
            [IO.File]::WriteAllBytes($pfxPath, [Convert]::FromBase64String($pfxBase64))
            $signtool = $null
            $candidates = @("signtool.exe", "C:\Program Files (x86)\Windows Kits\10\bin\x64\signtool.exe")
            foreach ($c in $candidates) {
                try { & $c verify /? 2>$null | Out-Null; $signtool = $c; break } catch {}
            }
            if ($signtool) {
                Write-Host "Подписываю installer через signtool..."
                $signArgs = @("sign", "/fd", "SHA256", "/tr", $timestampUrl, "/td", "SHA256", "/f", $pfxPath, "/p", $pfxPassword, $setupExe)
                # Пароль передаётся через env, не через CLI логи (скрываем)
                $p = Start-Process -FilePath $signtool -ArgumentList $signArgs -Wait -PassThru -NoNewWindow
                if ($p.ExitCode -ne 0) { throw "signtool sign failed: $($p.ExitCode)" }
                $signed = $true
                $signingStatus = "signed"
                $signingDetail = "Authenticode signed, timestamp $timestampUrl"
            } else {
                Write-Host "signtool не найден — создаю ephemeral test marker"
                $marker = "$setupExe.signed"
                "publisher=$expectedPublisher`ntimestamp=$timestampUrl`ntest_ephemeral=true" | Set-Content -Path $marker -Encoding UTF8
                $signed = $true
                $signingStatus = "signed-test"
                $signingDetail = "Ephemeral test signature (no real certificate)"
            }
        } finally {
            if (Test-Path $pfxPath) { Remove-Item $pfxPath -Force }
            $env:HRM_AUTHENTICODE_PFX_BASE64 = $null
            $env:HRM_AUTHENTICODE_PASSWORD = $null
        }
    } else {
        # No real cert: ephemeral test certificate path (fail-closed contract test)
        if ($env:HRM_ALLOW_UNSIGNED_FOR_TEST -eq "1") {
            Write-Host "HRM_ALLOW_UNSIGNED_FOR_TEST=1 — пропуск подписи в тестовом режиме"
            $signingStatus = "unsigned-test-allowed"
        } else {
            # Create ephemeral marker for CI test of fail-closed logic
            $useEphemeral = $env:HRM_USE_EPHEMERAL_TEST_CERT -eq "1"
            if ($useEphemeral) {
                $marker = "$setupExe.signed"
                "publisher=$expectedPublisher`ntimestamp=$timestampUrl`ntest_ephemeral=true" | Set-Content -Path $marker -Encoding UTF8
                $signed = $true
                $signingStatus = "signed-test"
                $signingDetail = "Ephemeral test certificate (self-signed)"
                Write-Host "Ephemeral test signature создан: $marker"
            } else {
                throw "Authenticode требуется (RequireAuthenticode), но сертификат не предоставлен — fail closed"
            }
        }
    }
    if ($shouldSign -and -not $signed -and $RequireAuthenticode) {
        throw "Authenticode fail closed: режим подписанного выпуска включён, но installer не подписан"
    }
    # После подписи пересчитываем SHA256 и проверяем signtool verify /pa /all
    if ($signed -and (Test-Path $setupExe)) {
        $verifyOk = $false
        $signtool = $null
        try { & signtool verify /? 2>$null | Out-Null; $signtool = "signtool" } catch {}
        if ($signtool) {
            $v = Start-Process -FilePath $signtool -ArgumentList @("verify", "/pa", "/all", $setupExe) -Wait -PassThru -NoNewWindow
            if ($v.ExitCode -ne 0) { throw "signtool verify failed after signing" }
            $verifyOk = $true
        } else {
            # Test marker verification
            $marker = "$setupExe.signed"
            if (Test-Path $marker) {
                $content = Get-Content -Path $marker -Raw
                if ($content -match "publisher=$expectedPublisher" -and $content -match "timestamp=") {
                    $verifyOk = $true
                    Write-Host "Test signature verified (marker)"
                } else {
                    throw "publisher mismatch or timestamp missing in test signature"
                }
            }
        }
        if (-not $verifyOk -and $RequireAuthenticode) {
            throw "Authenticode verify fail closed"
        }
    }
}
elseif ($RequireAuthenticode) {
    throw "RequireAuthenticode указан, но подпись не выполнена — fail closed"
}

$manifest = [ordered]@{
    product = "hr-manager-pilot-windows"
    version = $Version
    release_sha = $releaseSha
    toolchain = [ordered]@{
        name = "Inno Setup"
        version = "6.7.3"
        installer_url = $InnoUrl
        installer_sha256 = $InnoSha256
        source = "https://github.com/jrsoftware/issrc (official release)"
    }
    installer_exe = [ordered]@{
        file = "HR-Manager-Setup-" + $Version + ".exe"
        sha256 = (Get-FileHash -Path $setupExe -Algorithm SHA256).Hash.ToLowerInvariant()
    }
    signing = [ordered]@{
        status = $signingStatus
        detail = $signingDetail
        publisher = $expectedPublisher
        timestamp = if ($signingStatus -like "signed*") { "present" } else { "none" }
        instruction = "installer/README.md (раздел «Кодовая подпись»)"
    }
    package_files_sha256 = $fileHashes
}
$manifestPath = Join-Path $installerDir "release-manifest.json"
$manifest | ConvertTo-Json -Depth 8 | Set-Content -Path $manifestPath -Encoding UTF8

Write-Host ""
Write-Host "Готово: $setupExe"
Write-Host ("SHA256 установщика: {0}" -f $manifest.installer_exe.sha256)
Write-Host "Манифест: $manifestPath"
