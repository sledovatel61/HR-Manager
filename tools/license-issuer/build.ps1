# Build offline license issuer for Windows owner PC - autonomous bundle
# Goal: after build, owner gets a folder that runs WITHOUT system Python, pip, internet.
# - Embeddable Python 3.12.3 + cryptography pre-installed (built once by maintainer with internet)
# - GUI (Tkinter), CLI, and HTML (WebCrypto) all work offline via bundled python http.server
#   (run-html.bat serves on 127.0.0.1 only - the bundle is never exposed to the LAN)
# - Private key NEVER leaves owner PC, never in git/installer/Docker/logs; the smoke
#   test runs in a temporary directory outside the repository and is deleted afterwards
# - Fail-closed: if download fails or the build fails, the build fails; the final
#   artifact never silently falls back to system Python
#
# ENCODING CONTRACT (critical for Windows PowerShell 5.1):
#   This file MUST be saved as UTF-8 WITH BOM, and the content MUST stay ASCII-only.
#   Windows PowerShell 5.1 reads BOM-less .ps1 files as ANSI (system code page).
#   Under CP1251 a UTF-8 em dash (E2 80 94) decodes to "R" + "Dzh" + U+201D (a right
#   double quotation mark), which the tokenizer treats as a string terminator, so the
#   script dies with parse errors before any line executes. Keeping the content
#   ASCII-only makes the file parse under ANY code page even if the BOM is ever
#   stripped. CI enforces both properties and parses this file with the real
#   Windows PowerShell 5.1 Parser.
#
# Usage (maintainer, once, with internet):
#   powershell -ExecutionPolicy Bypass -File tools/license-issuer/build.ps1
# Result:
#   tools/license-issuer/dist/
#     python/                  # embeddable Python + cryptography
#     license-issuer/          # app files + launchers
#     license-issuer-dist.zip  # ready to send to owner (autonomous)
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

# Make every python invocation below write UTF-8 regardless of the console
# code page, so build logs (and the owner's console via the launchers) are
# deterministic for the Cyrillic messages.
$env:PYTHONUTF8 = "1"

$embedUrl = "https://www.python.org/ftp/python/$PythonVersion/python-$PythonVersion-embed-amd64.zip"
$embedZip = Join-Path $OutDir "python-$PythonVersion-embed-amd64.zip"
$pythonDir = Join-Path $OutDir "python"
$appDir = Join-Path $OutDir "license-issuer"
$zipPath = Join-Path $OutDir "license-issuer-dist.zip"
$pyExe = Join-Path $pythonDir "python.exe"

Write-Info "=== HR Manager License Issuer - autonomous build ==="
Write-Info "OutDir: $OutDir"
Write-Info "PythonVersion: $PythonVersion"
# Evidence of the shell that actually runs this build (Windows PowerShell 5.1 =
# Desktop edition, powershell.exe; pwsh 7 = Core edition, pwsh.exe).
Write-Info ("Host: PowerShell {0} ({1} edition), {2}" -f $PSVersionTable.PSVersion, $PSVersionTable.PSEdition, (Get-Process -Id $PID).Path)

if (-not (Test-Path $OutDir)) { New-Item -ItemType Directory -Path $OutDir -Force | Out-Null }

# Run a native command with all output captured; Windows PowerShell 5.1 with
# ErrorActionPreference=Stop turns merged stderr into a terminating error, so
# the preference is lowered for the duration of the call (repo convention).
function Invoke-NativeCaptured([string[]]$ArgumentList) {
    $previousEap = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try {
        $output = (& $script:pyExe @ArgumentList 2>&1 | ForEach-Object { $_.ToString() } | Out-String)
        $code = $LASTEXITCODE
    } finally { $ErrorActionPreference = $previousEap }
    return [pscustomobject]@{ Output = $output; ExitCode = $code }
}

# Run a native command, stream its output to the host, return ONLY the exit
# code. Build-time native calls (pip, get-pip) must be judged by exit code:
# under Windows PowerShell 5.1 with a redirected host (CI, logs) native stderr
# becomes NativeCommandError records, and with ErrorActionPreference=Stop a
# harmless pip warning would abort the command mid-install.
function Invoke-NativeLogged([string]$Exe, [string[]]$ArgumentList) {
    $previousEap = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try {
        & $Exe @ArgumentList 2>&1 | ForEach-Object { Write-Host $_.ToString() }
        $code = $LASTEXITCODE
    } finally { $ErrorActionPreference = $previousEap }
    return $code
}

# Configure python*._pth deterministically (see the comment at the call site).
function Set-BundledPth {
    $pthFiles = @(Get-ChildItem -Path $pythonDir -Filter "python*._pth" -ErrorAction SilentlyContinue)
    if ($pthFiles.Count -ne 1) {
        Write-Err "Expected exactly one python*._pth in $pythonDir, found $($pthFiles.Count)"
        exit 1
    }
    $majorMinor = (($PythonVersion -split "\.")[0..1] -join "")
    $pthContent = @(
        "python$majorMinor.zip",
        ".",
        "..\license-issuer",
        "Lib\site-packages",
        "import site"
    )
    Set-Content -Path $pthFiles[0].FullName -Value $pthContent -Encoding ASCII
    Write-Info "Patched $($pthFiles[0].Name): stdlib zip, python dir, ..\license-issuer (app modules), Lib\site-packages, import site"
}

# 1. Obtain embeddable Python (maintainer step, requires internet once)
if (-not (Test-Path $pythonDir)) {
    if ($OfflineOnly) {
        Write-Err "OfflineOnly set and $pythonDir not found - cannot build without download. Run without -OfflineOnly once with internet."
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

    # Ensure Lib\site-packages exists
    $sitePkg = Join-Path $pythonDir "Lib\site-packages"
    if (-not (Test-Path $sitePkg)) { New-Item -ItemType Directory -Path $sitePkg -Force | Out-Null }
    # The stock _pth has "#import site" and no Lib\site-packages, so pip
    # installed by get-pip would be invisible to "python -m pip". Patch now;
    # the same idempotent patch runs again below for cached python/ trees.
    Set-BundledPth

    # 2. Install pip + cryptography into embeddable (requires internet once)
    Write-Warn "Installing pip and cryptography into embeddable Python (requires internet once)..."
    $getPipUrl = "https://bootstrap.pypa.io/get-pip.py"
    $getPip = Join-Path $OutDir "get-pip.py"
    if (-not (Test-Path $getPip)) {
        try {
            Invoke-WebRequest -Uri $getPipUrl -OutFile $getPip -UseBasicParsing
        } catch {
            Write-Err "get-pip download failed: $_ - you can still use system pip to install cryptography into $sitePkg"
        }
    }
    if (-not (Test-Path $pyExe)) { Write-Err "python.exe not found in $pythonDir"; exit 1 }

    if (Test-Path $getPip) {
        $code = Invoke-NativeLogged $pyExe @($getPip, "--no-warn-script-location")
        if ($code -eq 0) { Write-Info "pip installed" } else { Write-Warn "get-pip exited with code $code" }
    }
    # Install cryptography into site-packages with the bundled interpreter.
    $code = Invoke-NativeLogged $pyExe @("-m", "pip", "install", "--no-warn-script-location", "--disable-pip-version-check", "cryptography", "--target", $sitePkg)
    if ($code -ne 0) {
        Write-Warn "bundled pip install cryptography failed (exit=$code)"
        Write-Warn "Trying system pip at BUILD time only (the artifact still uses the bundled interpreter)..."
        $sysPy = Get-Command python -ErrorAction SilentlyContinue
        if (-not $sysPy) {
            Write-Err "No bundled pip and no system python for the build-time install - cannot continue"
            exit 1
        }
        $code = Invoke-NativeLogged $sysPy.Source @("-m", "pip", "install", "--disable-pip-version-check", "cryptography", "--target", $sitePkg, "--no-warn-script-location", "--only-binary", ":all:", "--platform", "win_amd64", "--python-version", $PythonVersion)
        if ($code -ne 0) {
            Write-Err "Failed to install cryptography (exit=$code)"
            Write-Err "Build cannot continue without cryptography - embeddable Python must have cryptography in Lib/site-packages"
            exit 1
        }
        Write-Info "cryptography installed via system pip (build time only, win_amd64 wheels for Python $PythonVersion)"
    } else {
        Write-Info "cryptography installed to $sitePkg"
    }
} else {
    Write-Info "Embeddable Python already exists at $pythonDir - skipping download"
    if (-not (Test-Path $pyExe)) { Write-Err "python.exe not found in $pythonDir"; exit 1 }
}

# Configure python*._pth deterministically (idempotent, both fresh and cached
# python/ trees). The embeddable distribution ignores PYTHONPATH and does NOT
# add the script directory to sys.path: with the stock _pth file, running
# license-issuer\cli.py from the launcher dies with
# "ModuleNotFoundError: No module named 'license_issuer'". The app directory is
# a sibling of python/ in the bundle, so "..\license-issuer" (resolved relative
# to the python/ directory) makes the modules importable no matter what the
# current working directory is.
Set-BundledPth

# Tkinter for the GUI. The Windows EMBEDDABLE distribution ships without
# tkinter/Tcl/Tk (run-gui.bat died with "No module named 'tkinter'"). The
# official per-component tcltk.msi of the same Python version is taken from
# python.org and unpacked with an ADMINISTRATIVE extraction (msiexec /a: no
# registry entries, nothing installed on the build machine). Layout inside the
# embeddable tree: _tkinter.pyd + Tcl/Tk DLLs in python\ (on sys.path via "."),
# the tkinter package in python\tkinter, the Tcl/Tk script libraries in
# python\tcl (_tkinter looks for <base_prefix>\tcl\tcl8.x itself).
$tkReady = (Test-Path (Join-Path $pythonDir "_tkinter.pyd")) -and (Test-Path (Join-Path $pythonDir "tkinter\__init__.py")) -and (Test-Path (Join-Path $pythonDir "tcl"))
if (-not $tkReady) {
    $tcltkUrl = "https://www.python.org/ftp/python/$PythonVersion/amd64/tcltk.msi"
    $tcltkMsi = Join-Path $OutDir "tcltk-$PythonVersion-amd64.msi"
    if (-not (Test-Path $tcltkMsi)) {
        if ($OfflineOnly) {
            Write-Err "OfflineOnly set and $tcltkMsi not found - the GUI needs tkinter. Download $tcltkUrl to $tcltkMsi and rerun."
            exit 1
        }
        Write-Warn "Downloading $tcltkUrl (tkinter for the GUI) ..."
        try {
            [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
            Invoke-WebRequest -Uri $tcltkUrl -OutFile $tcltkMsi -UseBasicParsing
        } catch {
            Write-Err "tcltk.msi download failed: $_"
            exit 1
        }
    }
    $tkExtract = Join-Path $OutDir "tcltk-extract"
    if (Test-Path $tkExtract) { Remove-Item $tkExtract -Recurse -Force }
    New-Item -ItemType Directory -Path $tkExtract -Force | Out-Null
    # msiexec is a GUI-subsystem program: "& msiexec" would not wait.
    $msiArgs = '/a "' + $tcltkMsi + '" /qn TARGETDIR="' + $tkExtract + '"'
    $msi = Start-Process -FilePath "msiexec.exe" -ArgumentList $msiArgs -Wait -PassThru
    if ($msi.ExitCode -ne 0) {
        Write-Err "msiexec /a tcltk.msi failed (exit=$($msi.ExitCode))"
        exit 1
    }
    $srcPyd = Get-ChildItem -Path $tkExtract -Recurse -Filter "_tkinter.pyd" | Select-Object -First 1
    $srcPkg = Get-ChildItem -Path $tkExtract -Recurse -Directory -Filter "tkinter" | Where-Object { Test-Path (Join-Path $_.FullName "__init__.py") } | Select-Object -First 1
    $srcTcl = Get-ChildItem -Path $tkExtract -Recurse -Directory -Filter "tcl" | Where-Object { @(Get-ChildItem -Path $_.FullName -Directory -Filter "tcl8*").Count -gt 0 } | Select-Object -First 1
    if (-not $srcPyd -or -not $srcPkg -or -not $srcTcl) {
        Write-Err "tcltk.msi layout not recognised (pyd=$([bool]$srcPyd) package=$([bool]$srcPkg) tcl=$([bool]$srcTcl))"
        exit 1
    }
    Copy-Item -Path $srcPyd.FullName -Destination $pythonDir -Force
    # every DLL shipped next to _tkinter.pyd in tcltk.msi (tcl86t.dll, tk86t.dll, ...)
    Get-ChildItem -Path $srcPyd.DirectoryName -Filter "*.dll" | ForEach-Object { Copy-Item -Path $_.FullName -Destination $pythonDir -Force }
    $dstPkg = Join-Path $pythonDir "tkinter"
    if (Test-Path $dstPkg) { Remove-Item $dstPkg -Recurse -Force }
    Copy-Item -Path $srcPkg.FullName -Destination $dstPkg -Recurse -Force
    $dstTcl = Join-Path $pythonDir "tcl"
    if (Test-Path $dstTcl) { Remove-Item $dstTcl -Recurse -Force }
    Copy-Item -Path $srcTcl.FullName -Destination $dstTcl -Recurse -Force
    Remove-Item $tkExtract -Recurse -Force
    Write-Info "tkinter + Tcl/Tk added to the bundled Python from tcltk.msi ($PythonVersion)"
}
$tkCheck = Invoke-NativeCaptured @("-c", "import tkinter; print(tkinter.TkVersion, tkinter.Tcl().eval('info patchlevel'))")
if ($tkCheck.ExitCode -ne 0) {
    Write-Err "tkinter/Tcl check failed in the bundled Python (exit=$($tkCheck.ExitCode)) - the GUI would not start:"
    Write-Err $tkCheck.Output
    exit 1
}
if (@(Get-ChildItem -Path (Join-Path $pythonDir "tcl") -Directory -Filter "tk8*").Count -eq 0) {
    Write-Err "Tk script library (tcl\tk8.x) missing in the bundled Python - the GUI would not start"
    exit 1
}
Write-Info "Verified tkinter (Tk/Tcl $($tkCheck.Output.Trim())) in the bundled Python"

# Verify cryptography imports in the bundled interpreter (never in a system one)
$verify = Invoke-NativeCaptured @("-c", "import cryptography; print(cryptography.__version__)")
if ($verify.ExitCode -ne 0) {
    Write-Err "cryptography import failed in the bundled Python (exit=$($verify.ExitCode)):"
    Write-Err $verify.Output
    Write-Err "Reinstall or remove the python/ folder and rebuild with internet"
    exit 1
}
Write-Info "Verified cryptography $($verify.Output.Trim()) in the bundled Python"

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
Copy-Item -Path "$PSScriptRoot\open_when_ready.py" -Destination $appDir -Force
Copy-Item -Path "$PSScriptRoot\README.md" -Destination $appDir -Force

# 4. Create launchers: bundled python only, fail-closed if it is missing (no
#    silent fallback to system python - the private key must stay in the bundle).
#    cd /d "%~dp0" makes relative paths deterministic; all paths are quoted so
#    folders with spaces work; PYTHONUTF8/PYTHONPATH are belt-and-braces on top
#    of the ..\license-issuer entry in the _pth file.
$runGuiBat = @"
@echo off
setlocal
REM HR Manager License Issuer - GUI (owner only, autonomous)
REM Uses the bundled ..\python\python.exe only. NO system Python, NO internet.
REM The private key never leaves this bundle.
set "SCRIPT_DIR=%~dp0"
cd /d "%SCRIPT_DIR%"
set "PY_EXE=%SCRIPT_DIR%..\python\python.exe"
if not exist "%PY_EXE%" goto :py_missing
set "PYTHONUTF8=1"
set "PYTHONPATH=%SCRIPT_DIR%"
"%PY_EXE%" "%SCRIPT_DIR%gui.py" %*
exit /b %ERRORLEVEL%
:py_missing
echo [ERROR] Bundled Python not found at %PY_EXE%
echo This distribution must contain python\ folder. Re-download full license-issuer-dist.zip
echo The issuer never falls back to a system Python: the private key must stay inside this bundle.
if not defined HRM_NO_PAUSE pause
exit /b 1
"@
Set-Content -Path (Join-Path $appDir "run-gui.bat") -Value $runGuiBat -Encoding ASCII

$runCliBat = @"
@echo off
setlocal
REM HR Manager License Issuer - CLI (owner only, autonomous)
REM Uses the bundled ..\python\python.exe only. NO system Python, NO internet.
REM The private key never leaves this bundle.
set "SCRIPT_DIR=%~dp0"
cd /d "%SCRIPT_DIR%"
set "PY_EXE=%SCRIPT_DIR%..\python\python.exe"
if not exist "%PY_EXE%" goto :py_missing
set "PYTHONUTF8=1"
set "PYTHONPATH=%SCRIPT_DIR%"
"%PY_EXE%" "%SCRIPT_DIR%cli.py" %*
exit /b %ERRORLEVEL%
:py_missing
echo [ERROR] Bundled Python not found at %PY_EXE%
echo This distribution must contain python\ folder. Re-download full license-issuer-dist.zip
echo The issuer never falls back to a system Python: the private key must stay inside this bundle.
if not defined HRM_NO_PAUSE pause
exit /b 1
"@
Set-Content -Path (Join-Path $appDir "run-cli.bat") -Value $runCliBat -Encoding ASCII

$runHtmlBat = @"
@echo off
setlocal
REM HR Manager License Issuer - HTML offline via local http.server (secure context for WebCrypto)
REM Opens http://127.0.0.1:8765/license-issuer.html in the default browser.
REM The server binds to 127.0.0.1 ONLY (loopback): never exposed to the LAN.
REM Uses the bundled ..\python\python.exe only. NO system Python, NO internet.
set "SCRIPT_DIR=%~dp0"
cd /d "%SCRIPT_DIR%"
set "PY_EXE=%SCRIPT_DIR%..\python\python.exe"
set "PORT=8765"
if not exist "%PY_EXE%" goto :py_missing
set "PYTHONUTF8=1"
set "PYTHONPATH=%SCRIPT_DIR%"
echo Starting local server at http://127.0.0.1:%PORT%/license-issuer.html (loopback only)
echo Keep this window open. Press Ctrl+C to stop the server after use.
REM No fixed delay (race: the browser could hit the port before the server
REM listens). open_when_ready.py (bundled python) polls the page on loopback and
REM opens the browser only after HTTP 200 with the issuer page marker.
REM HRM_NO_BROWSER=1: it only prints the [ready] line (CI, checklists).
start "" /b "%PY_EXE%" "%SCRIPT_DIR%open_when_ready.py" "http://127.0.0.1:%PORT%/license-issuer.html"
REM "%SCRIPT_DIR%." not "%SCRIPT_DIR%": %~dp0 ends with a backslash and \" is an
REM escaped quote in Windows argv parsing (the directory would end in a quote -> 404).
"%PY_EXE%" -m http.server %PORT% -b 127.0.0.1 --directory "%SCRIPT_DIR%."
exit /b %ERRORLEVEL%
:py_missing
echo [ERROR] Bundled Python not found at %PY_EXE%
echo This distribution must contain python\ folder. Re-download full license-issuer-dist.zip
echo The issuer never falls back to a system Python: the private key must stay inside this bundle.
if not defined HRM_NO_PAUSE pause
exit /b 1
"@
Set-Content -Path (Join-Path $appDir "run-html.bat") -Value $runHtmlBat -Encoding ASCII

# HOWTO for owner
$howto = @"
HR Manager - Offline License Issuer (autonomous, owner only)
============================================================

This folder is AUTONOMOUS: no system Python, no pip, no internet required at runtime.
- python\           - embeddable Python 3.12.3 + cryptography + tkinter (bundled)
- license-issuer\   - GUI, CLI, HTML

Double-click launchers:
- run-gui.bat   - GUI (Tkinter): generate keypair, issue license
- run-html.bat  - HTML offline via http://127.0.0.1:8765 (WebCrypto Ed25519, Edge 120+, loopback only)
- run-cli.bat   - CLI: gen-keypair, issue, verify

Steps (owner, Windows PC, offline after unzip):
1. Unzip license-issuer-dist.zip to a folder (e.g. C:\HR-License\)
2. Double-click run-gui.bat
   - Click "Generate new keypair"
   - SAVE private key (64 hex) securely: VeraCrypt/BitLocker/encrypted USB + backup!
   - COPY public key (base64 44 chars) to infra/license/public_key.b64 before building pilot image
3. Fill client name, expiry YYYY-MM-DD, max users. License ID is auto
4. Issue -> save the .hrmlicense file
5. Send .hrmlicense to Maria via secure channel (email/messenger/USB)
6. Public key goes to pilot.env via Secrets.psm1 -> HRM_LICENSE_PUBLIC_KEY

Security:
- Private key NEVER in git/installer/frontend/Docker/logs/diagnostic archive
- Only public key in app
- Logs contain only SHA256:xxxx... (redacted)

HTML version:
- Requires Edge 120+ / Chrome 120+ (Windows 10/11 default Edge is OK)
- Needs a secure context: http://127.0.0.1 or https - run-html.bat starts a loopback-only server
- If WebCrypto is unavailable, use GUI/CLI (bundled Python)

CLI examples (run from the license-issuer folder):
  run-cli.bat gen-keypair --out-dir keys
  run-cli.bat issue --private-key-file keys\private_key.hex --client "Pilot Maria" --expires 2026-12-31 --max-users 5 --out licenses\pilot.hrmlicense
  run-cli.bat verify --public-key-file keys\public_key.b64 --license-file licenses\pilot.hrmlicense

Verification after build (maintainer):
  python\python.exe -c "import cryptography; print('ok')"
  license-issuer\run-cli.bat gen-keypair

This bundle was built with:
  Python $PythonVersion embeddable + cryptography wheel + tkinter/Tcl/Tk from tcltk.msi
  No internet required at runtime.
"@
Set-Content -Path (Join-Path $appDir "HOWTO.txt") -Value $howto -Encoding ASCII
Set-Content -Path (Join-Path $OutDir "HOWTO.txt") -Value $howto -Encoding ASCII

# 5. Smoke test: full CLI chain gen-keypair -> issue -> verify using the bundled
#    python, in a temporary directory OUTSIDE the repository, then remove
#    everything again. The smoke test must never leave key material in the git
#    work tree and must never echo the private key into the build log; if it
#    does, the build fails.
Write-Info "Running smoke test: gen-keypair -> issue -> verify via bundled Python (temporary directory outside the repo)..."
$smokeDir = Join-Path ([System.IO.Path]::GetTempPath()) ("hrm-issuer-smoke-" + [guid]::NewGuid().ToString("N"))
$smokeKeysCreated = $false
try {
    New-Item -ItemType Directory -Path $smokeDir -Force | Out-Null
    $smokeKeysDir = Join-Path $smokeDir "keys"
    $smokeLicense = Join-Path $smokeDir "smoke.hrmlicense"
    $cli = Join-Path $appDir "cli.py"

    $gen = Invoke-NativeCaptured @($cli, "gen-keypair", "--out-dir", $smokeKeysDir)
    if ($gen.ExitCode -ne 0) { throw "cli.py gen-keypair exited with code $($gen.ExitCode): $($gen.Output)" }
    $smokePriv = Join-Path $smokeKeysDir "private_key.hex"
    $smokePub = Join-Path $smokeKeysDir "public_key.b64"
    if (-not (Test-Path $smokePriv) -or -not (Test-Path $smokePub)) {
        throw "cli.py gen-keypair did not create the expected key files in $smokeKeysDir"
    }
    $smokeKeysCreated = $true

    $issue = Invoke-NativeCaptured @($cli, "issue", "--private-key-file", $smokePriv, "--client", "Smoke Test", "--expires", "2099-12-31", "--max-users", "5", "--out", $smokeLicense)
    if ($issue.ExitCode -ne 0) { throw "cli.py issue exited with code $($issue.ExitCode): $($issue.Output)" }
    if (-not (Test-Path $smokeLicense)) { throw "cli.py issue did not create $smokeLicense" }

    $verify = Invoke-NativeCaptured @($cli, "verify", "--public-key-file", $smokePub, "--license-file", $smokeLicense)
    if ($verify.ExitCode -ne 0) { throw "cli.py verify exited with code $($verify.ExitCode): $($verify.Output)" }

    # Never allow private key material to reach the build log
    $smokePrivHex = (Get-Content -Path $smokePriv -Raw).Trim()
    if ($smokePrivHex) {
        foreach ($step in @(@{ Name = "gen-keypair"; Text = $gen.Output }, @{ Name = "issue"; Text = $issue.Output }, @{ Name = "verify"; Text = $verify.Output })) {
            if ($step.Text -match [regex]::Escape($smokePrivHex)) {
                throw "cli.py $($step.Name) printed private key material - refusing to continue"
            }
        }
    }
    Write-Info "Smoke test PASS: gen-keypair -> issue -> verify works with the bundled Python; no key material in this log, no key material in the repository"
} catch {
    Write-Err "Smoke test failed: $_"
    exit 1
} finally {
    if (Test-Path $smokeDir) { Remove-Item -Path $smokeDir -Recurse -Force -ErrorAction SilentlyContinue }
    if (Test-Path $smokeDir) { Write-Warn "could not remove temporary smoke directory $smokeDir - delete it manually" }
    elseif ($smokeKeysCreated) { Write-Info "Temporary smoke directory removed" }
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
    Write-Info "Embeddable Python present - distribution is AUTONOMOUS (no system Python/internet needed at runtime)"
} else {
    Write-Err "Embeddable Python missing - distribution NOT autonomous"
    exit 1
}
Write-Info "Next: unzip $zipPath on owner Windows PC and double-click run-gui.bat or run-html.bat"
Write-Info "For reproducibility: this build used Python $PythonVersion embeddable from python.org + cryptography wheel"
