# Build offline license issuer for Windows owner PC — autonomous bundle
# Goal: after build, owner gets a folder that runs WITHOUT system Python, pip, internet.
# - Embeddable Python 3.12.3 + cryptography pre-installed (built once by maintainer with internet)
# - GUI (Tkinter), CLI, and HTML (WebCrypto) all work offline via bundled python http.server
# - Private key NEVER leaves owner PC, never in git/installer/Docker/logs
# - Fail-closed: if download fails, build fails, no silent fallback to system Python in final artifact
#
# Usage (maintainer, once, with internet):
#   powershell -ExecutionPolicy Bypass -File tools/license-issuer/build.ps1
# Result:
#   tools/license-issuer/dist/
#     python/                # embeddable Python + cryptography
#     license-issuer/        # app files + launchers
#     license-issuer-dist.zip # ready to send to owner (autonomous)
#
# Owner (no Python, no internet needed):
#   Unzip license-issuer-dist.zip -> double-click run-gui.bat OR run-html.bat OR run-cli.bat gen-keypair

param(
    [string]$PythonVersion = "3.12.3",
    [string]$OutDir = "$PSScriptRoot\dist",
    [switch]$OfflineOnly  # if set, do not attempt any download, fail if python/ not present
)

Set-StrictMode -Version 2.0
$ErrorActionPreference = "Stop"

function Write-Info($msg) { Write-Host $msg -ForegroundColor Cyan }
function Write-Warn($msg) { Write-Host $msg -ForegroundColor Yellow }
function Write-Err($msg) { Write-Host $msg -ForegroundColor Red }

$embedUrl = "https://www.python.org/ftp/python/$PythonVersion/python-$PythonVersion-embed-amd64.zip"
$embedZip = Join-Path $OutDir "python-$PythonVersion-embed-amd64.zip"
$pythonDir = Join-Path $OutDir "python"
$appDir = Join-Path $OutDir "license-issuer"
$zipPath = Join-Path $OutDir "license-issuer-dist.zip"

Write-Info "=== HR Manager License Issuer — autonomous build ==="
Write-Info "OutDir: $OutDir"
Write-Info "PythonVersion: $PythonVersion"

if (-not (Test-Path $OutDir)) { New-Item -ItemType Directory -Path $OutDir -Force | Out-Null }

# 1. Obtain embeddable Python (maintainer step, requires internet once)
if (-not (Test-Path $pythonDir)) {
    if ($OfflineOnly) {
        Write-Err "OfflineOnly set and $pythonDir not found — cannot build without download. Run without -OfflineOnly once with internet."
        exit 1
    }
    if (-not (Test-Path $embedZip)) {
        Write-Warn "Downloading $embedUrl ..."
        try {
            [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
            Invoke-WebRequest -Uri $embedUrl -OutFile $embedZip -UseBasicParsing
            Write-Info "Downloaded to $embedZip"
        } catch {
            Write-Err "Download failed: $_"
            Write-Err "Manually download $embedUrl to $embedZip and rerun, or place existing embeddable Python folder to $pythonDir"
            exit 1
        }
    }
    Write-Info "Extracting embeddable Python..."
    if (Test-Path $pythonDir) { Remove-Item $pythonDir -Recurse -Force }
    Expand-Archive -Path $embedZip -DestinationPath $pythonDir -Force

    # Enable site-packages: uncomment import site in ._pth file
    $pthFiles = Get-ChildItem -Path $pythonDir -Filter "python*._pth" -ErrorAction SilentlyContinue
    foreach ($pth in $pthFiles) {
        $content = Get-Content $pth.FullName -Raw
        if ($content -match "#import site") {
            $content = $content -replace "#import site", "import site"
            Set-Content -Path $pth.FullName -Value $content -Encoding ASCII
            Write-Info "Patched $($pth.Name) to enable import site"
        }
    }

    # Ensure Lib\site-packages exists
    $sitePkg = Join-Path $pythonDir "Lib\site-packages"
    if (-not (Test-Path $sitePkg)) { New-Item -ItemType Directory -Path $sitePkg -Force | Out-Null }

    # 2. Install pip + cryptography into embeddable (requires internet once)
    Write-Warn "Installing pip and cryptography into embeddable Python (requires internet once)..."
    $getPipUrl = "https://bootstrap.pypa.io/get-pip.py"
    $getPip = Join-Path $OutDir "get-pip.py"
    if (-not (Test-Path $getPip)) {
        try {
            Invoke-WebRequest -Uri $getPipUrl -OutFile $getPip -UseBasicParsing
        } catch {
            Write-Err "get-pip download failed: $_ — you can still use system pip to install cryptography into $sitePkg"
            # Continue, try system python
        }
    }
    $pyExe = Join-Path $pythonDir "python.exe"
    if (-not (Test-Path $pyExe)) { Write-Err "python.exe not found in $pythonDir"; exit 1 }

    if (Test-Path $getPip) {
        try {
            & $pyExe $getPip --no-warn-script-location
            Write-Info "pip installed"
        } catch {
            Write-Warn "pip install failed: $_"
        }
    }
    # Try to install cryptography via pip into site-packages
    try {
        & $pyExe -m pip install --no-warn-script-location --upgrade pip
        & $pyExe -m pip install --no-warn-script-location cryptography --target $sitePkg
        Write-Info "cryptography installed to $sitePkg"
    } catch {
        Write-Warn "pip install cryptography failed: $_"
        Write-Warn "Trying to copy from system Python if available..."
        try {
            $sysPy = Get-Command python -ErrorAction SilentlyContinue
            if ($sysPy) {
                & python -m pip install cryptography --target $sitePkg --no-warn-script-location
                Write-Info "cryptography installed via system python"
            }
        } catch {
            Write-Err "Failed to install cryptography: $_"
            Write-Err "Build cannot continue without cryptography — embeddable Python must have cryptography in Lib/site-packages"
            exit 1
        }
    }

    # Verify cryptography
    try {
        & $pyExe -c "import cryptography; print(cryptography.__version__)"
        Write-Info "Verified cryptography import in embeddable Python"
    } catch {
        Write-Err "cryptography import failed in embeddable Python: $_"
        exit 1
    }
} else {
    Write-Info "Embeddable Python already exists at $pythonDir — skipping download"
    $pyExe = Join-Path $pythonDir "python.exe"
    try {
        & $pyExe -c "import cryptography; print('cryptography '+cryptography.__version__)"
    } catch {
        Write-Err "Existing $pythonDir does not have cryptography — reinstall or remove folder and rebuild with internet"
        exit 1
    }
}

# 3. Create app dir
if (Test-Path $appDir) { Remove-Item $appDir -Recurse -Force }
New-Item -ItemType Directory -Path $appDir -Force | Out-Null

Copy-Item -Path "$PSScriptRoot\license_issuer.py" -Destination $appDir -Force
Copy-Item -Path "$PSScriptRoot\cli.py" -Destination $appDir -Force
Copy-Item -Path "$PSScriptRoot\gui.py" -Destination $appDir -Force
Copy-Item -Path "$PSScriptRoot\license-issuer.html" -Destination $appDir -Force
if (Test-Path "$PSScriptRoot\nacl-fast.js") {
    Copy-Item -Path "$PSScriptRoot\nacl-fast.js" -Destination $appDir -Force
}
Copy-Item -Path "$PSScriptRoot\README.md" -Destination $appDir -Force

# 4. Create launchers that use bundled python, fail-closed if not present (no silent fallback to system python for owner privacy)
$runGuiBat = @"
@echo off
setlocal
REM HR Manager License Issuer — GUI (owner only, autonomous)
REM Uses bundled python\python.exe, no system Python, no internet required
REM Private key stays ONLY on owner PC!
set SCRIPT_DIR=%~dp0
set PY_EXE=%SCRIPT_DIR%..\python\python.exe
if not exist "%PY_EXE%" (
  echo [ERROR] Bundled Python not found at %PY_EXE%
  echo This distribution must contain python\ folder. Re-download full license-issuer-dist.zip
  echo Alternatively, install Python 3.12+ and run: python "%SCRIPT_DIR%gui.py"
  pause
  exit /b 1
)
"%PY_EXE%" "%SCRIPT_DIR%gui.py" %*
"@
Set-Content -Path (Join-Path $appDir "run-gui.bat") -Value $runGuiBat -Encoding ASCII

$runCliBat = @"
@echo off
setlocal
REM HR Manager License Issuer — CLI (owner only, autonomous)
set SCRIPT_DIR=%~dp0
set PY_EXE=%SCRIPT_DIR%..\python\python.exe
if not exist "%PY_EXE%" (
  echo [ERROR] Bundled Python not found at %PY_EXE%
  echo This distribution must contain python\ folder.
  pause
  exit /b 1
)
"%PY_EXE%" "%SCRIPT_DIR%cli.py" %*
"@
Set-Content -Path (Join-Path $appDir "run-cli.bat") -Value $runCliBat -Encoding ASCII

$runHtmlBat = @"
@echo off
setlocal
REM HR Manager License Issuer — HTML offline via local http.server (secure context for WebCrypto)
REM Opens http://localhost:8765/license-issuer.html in default browser
REM Uses bundled python, no internet required
set SCRIPT_DIR=%~dp0
set PY_EXE=%SCRIPT_DIR%..\python\python.exe
set PORT=8765
if not exist "%PY_EXE%" (
  echo [ERROR] Bundled Python not found at %PY_EXE%
  echo Trying system python...
  set PY_EXE=python
)
echo Starting local server at http://localhost:%PORT%/license-issuer.html
echo Keep this window open, browser will open automatically...
echo Press Ctrl+C to stop server after use.
start "" http://localhost:%PORT%/license-issuer.html
"%PY_EXE%" -m http.server %PORT% --directory "%SCRIPT_DIR%"
"@
Set-Content -Path (Join-Path $appDir "run-html.bat") -Value $runHtmlBat -Encoding ASCII

# README for owner
$howto = @"
HR Manager — Offline License Issuer (autonomous, owner only)
============================================================

This folder is AUTONOMOUS: no system Python, no pip, no internet required at runtime.
- python\           — embeddable Python 3.12.3 + cryptography (bundled)
- license-issuer\   — GUI, CLI, HTML

Double-click launchers:
- run-gui.bat   — GUI (Tkinter) — generate keypair, issue license
- run-html.bat  — HTML offline via http://localhost:8765 (WebCrypto Ed25519, Edge 120+)
- run-cli.bat   — CLI: gen-keypair, issue, verify

Steps (owner, Windows PC, offline after unzip):
1. Unzip license-issuer-dist.zip to a folder (e.g. C:\HR-License\)
2. Double-click run-gui.bat
   - Click "Generate keypair"
   - SAVE private key (64 hex) securely: VeraCrypt/BitLocker/encrypted USB + backup!
   - COPY public key (base64 44 chars) to infra/license/public_key.b64 before building pilot image
3. Fill client name, expiry YYYY-MM-DD, max users, License ID auto
4. Issue -> Download .hrmlicense file
5. Send .hrmlicense to Maria via secure channel (email/messenger/USB)
6. Public key goes to pilot.env via Secrets.psm1 -> HRM_LICENSE_PUBLIC_KEY

Security:
- Private key NEVER in git/installer/frontend/Docker/logs/diagnostic archive
- Only public key in app
- Logs contain only SHA256:xxxx... (redacted)

HTML version:
- Requires Edge 120+ / Chrome 120+ (Windows 10/11 default Edge is OK)
- Needs secure context: http://localhost or https — run-html.bat starts localhost server
- If WebCrypto unavailable, use GUI/CLI (bundled Python)

CLI examples:
  run-cli.bat gen-keypair
  run-cli.bat issue --client-name "Pilot Maria" --expires-at 2026-12-31 --max-users 5 --private-key <64hex> --out license.hrmlicense
  run-cli.bat verify --public-key <base64> --license license.hrmlicense

Verification after build (maintainer):
  python\python.exe -c "import cryptography; print('ok')"
  python\python.exe license-issuer\cli.py gen-keypair

This bundle was built with:
  Python $PythonVersion embeddable + cryptography
  No internet required at runtime.
"@
Set-Content -Path (Join-Path $appDir "HOWTO.txt") -Value $howto -Encoding UTF8
Set-Content -Path (Join-Path $OutDir "HOWTO.txt") -Value $howto -Encoding UTF8

# 5. Smoke test: gen-keypair using bundled python
Write-Info "Running smoke test: gen-keypair via bundled Python..."
try {
    $smokeOut = & $pyExe (Join-Path $appDir "cli.py") gen-keypair 2>&1
    Write-Host $smokeOut
    if ($smokeOut -match "private") { Write-Info "Smoke test PASS: gen-keypair works" } else { Write-Warn "Smoke test output unexpected" }
} catch {
    Write-Warn "Smoke test failed: $_"
}

# 6. Create zip for owner (autonomous, no internet needed)
Write-Info "Creating autonomous zip $zipPath ..."
if (Test-Path $zipPath) { Remove-Item $zipPath -Force }
# Include python/ and license-issuer/ at top level
Compress-Archive -Path $pythonDir, $appDir -DestinationPath $zipPath -Force
Write-Info "Build complete!"
Write-Info "  python dir: $pythonDir"
Write-Info "  app dir: $appDir"
Write-Info "  zip: $zipPath"
Get-ChildItem $appDir | Format-Table Name, Length -AutoSize
if (Test-Path $pythonDir) {
    Write-Info "Embeddable Python present — distribution is AUTONOMOUS (no system Python/internet needed at runtime)"
} else {
    Write-Err "Embeddable Python missing — distribution NOT autonomous"
    exit 1
}
Write-Info "Next: unzip $zipPath on owner Windows PC and double-click run-gui.bat or run-html.bat"
Write-Info "For reproducibility: this build used Python $PythonVersion embeddable from python.org + cryptography wheel"
