# Build offline license issuer for Windows owner PC — embeddable Python 3.12.3
# No internet required for Maria; owner needs this utility once to issue license.
# Private key NEVER leaves owner PC, never in git/installer/Docker/logs.

param(
    [string]$PythonVersion = "3.12.3",
    [string]$OutDir = "$PSScriptRoot\dist"
)

Set-StrictMode -Version 2.0
$ErrorActionPreference = "Stop"

$embedUrl = "https://www.python.org/ftp/python/$PythonVersion/python-$PythonVersion-embed-amd64.zip"
$embedZip = Join-Path $OutDir "python-$PythonVersion-embed-amd64.zip"
$pythonDir = Join-Path $OutDir "python"
$appDir = Join-Path $OutDir "license-issuer"

Write-Host "Building offline license issuer to $OutDir" -ForegroundColor Cyan

if (-not (Test-Path $OutDir)) { New-Item -ItemType Directory -Path $OutDir -Force | Out-Null }

# Download embeddable Python if not present
if (-not (Test-Path $embedZip)) {
    Write-Host "Downloading $embedUrl ..." -ForegroundColor Yellow
    try {
        # Use TLS 1.2
        [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
        Invoke-WebRequest -Uri $embedUrl -OutFile $embedZip -UseBasicParsing
    } catch {
        Write-Warning "Download failed: $_"
        Write-Host "Please manually download $embedUrl to $embedZip and rerun." -ForegroundColor Red
        Write-Host "Alternatively, use system Python: python cli.py gen-keypair" -ForegroundColor Yellow
        # Continue without embed — create portable folder with scripts only
    }
}

# Extract if zip exists and python dir not exists
if ((Test-Path $embedZip) -and (-not (Test-Path $pythonDir))) {
    Write-Host "Extracting embeddable Python..." -ForegroundColor Yellow
    Expand-Archive -Path $embedZip -DestinationPath $pythonDir -Force
    # Enable pip and site-packages for embeddable (remove _pth restriction)
    $pth = Get-ChildItem -Path $pythonDir -Filter "python*._pth" | Select-Object -First 1
    if ($pth) {
        $content = Get-Content $pth.FullName -Raw
        # Uncomment import site
        $content = $content -replace "#import site", "import site"
        Set-Content -Path $pth.FullName -Value $content -Encoding ASCII
    }
    # Install pip via get-pip.py if needed for cryptography
    # For offline, we bundle cryptography wheel? Simplest: use pip to install cryptography
    Write-Host "Installing cryptography into embeddable Python (requires internet once)..." -ForegroundColor Yellow
    $getPipUrl = "https://bootstrap.pypa.io/get-pip.py"
    $getPip = Join-Path $OutDir "get-pip.py"
    if (-not (Test-Path $getPip)) {
        try { Invoke-WebRequest -Uri $getPipUrl -OutFile $getPip -UseBasicParsing } catch { Write-Warning "get-pip download failed: $_" }
    }
    if (Test-Path $getPip) {
        & (Join-Path $pythonDir "python.exe") $getPip --no-warn-script-location
        & (Join-Path $pythonDir "python.exe") -m pip install --no-warn-script-location cryptography --target (Join-Path $pythonDir "Lib\site-packages")
    }
}

# Create app dir
if (Test-Path $appDir) { Remove-Item $appDir -Recurse -Force }
New-Item -ItemType Directory -Path $appDir -Force | Out-Null

Copy-Item -Path "$PSScriptRoot\license_issuer.py" -Destination $appDir
Copy-Item -Path "$PSScriptRoot\cli.py" -Destination $appDir
Copy-Item -Path "$PSScriptRoot\gui.py" -Destination $appDir
Copy-Item -Path "$PSScriptRoot\license-issuer.html" -Destination $appDir
Copy-Item -Path "$PSScriptRoot\nacl-fast.js" -Destination $appDir -ErrorAction SilentlyContinue
Copy-Item -Path "$PSScriptRoot\README.md" -Destination $appDir -ErrorAction SilentlyContinue

# Create launchers
@"
@echo off
setlocal
REM Offline License Issuer — GUI (owner only)
REM Private key stays ONLY on owner PC!
if exist "%~dp0..\python\python.exe" (
  "%~dp0..\python\python.exe" "%~dp0gui.py" %*
) else (
  python "%~dp0gui.py" %*
)
"@ | Set-Content -Path (Join-Path $appDir "run-gui.bat") -Encoding ASCII

@"
@echo off
setlocal
REM Offline License Issuer — CLI
if exist "%~dp0..\python\python.exe" (
  "%~dp0..\python\python.exe" "%~dp0cli.py" %*
) else (
  python "%~dp0cli.py" %*
)
"@ | Set-Content -Path (Join-Path $appDir "run-cli.bat") -Encoding ASCII

@"
# Offline License Issuer — owner utility
# 1. Generate keypair (once, backup private key securely):
#    run-cli.bat gen-keypair
#    or
#    run-gui.bat -> Generate
# 2. Issue license:
#    run-cli.bat issue --client-name "Pilot Maria" --expires-at 2026-12-31 --max-users 5 --private-key <64hex> --out license.hrmlicense
#    or via GUI
# 3. Public key (base64) -> infra/license/public_key.b64 before building pilot image
# 4. Send license.hrmlicense to Maria, she uploads via UI: Settings -> License
# Private key NEVER in git/installer/Docker/logs!
"@ | Set-Content -Path (Join-Path $appDir "HOWTO.txt") -Encoding UTF8

Write-Host "Build complete: $appDir" -ForegroundColor Green
Write-Host "Contents:" -ForegroundColor Cyan
Get-ChildItem $appDir | Format-Table Name, Length
if (Test-Path $pythonDir) {
    Write-Host "Embeddable Python at $pythonDir" -ForegroundColor Green
} else {
    Write-Host "Embeddable Python not present — using system Python. For fully offline package, download $embedUrl" -ForegroundColor Yellow
}
Write-Host "Next: run $appDir\run-gui.bat or $appDir\run-cli.bat gen-keypair" -ForegroundColor Cyan
