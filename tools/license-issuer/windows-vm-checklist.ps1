# Owner/CI Windows machine checklist for the license issuer bundle (PR #36).
#
# Runs the full runtime checklist on a REAL Windows machine with Windows
# PowerShell 5.1 (the target shell), in separate temporary directories:
#   C:\Users\User\Documents\HR\issuer-pr36-test          (build work dir)
#   C:\Users\User\Documents\HR\issuer-pr36-test-output   (fresh unzip + runtime)
#
# Steps: 5.1 parser check -> build -> unzip -> CLI chain via run-cli.bat ->
# HTML launcher loopback check -> GUI best effort -> key-material sweep ->
# repo cleanliness. Prints exact commands, exit codes and a final verdict.
# Exits non-zero on any failure.
#
# ENCODING CONTRACT: ASCII-only + UTF-8 BOM (same as build.ps1) so Windows
# PowerShell 5.1 parses it under any code page.
#
# Usage (from the repository root):
#   powershell -ExecutionPolicy Bypass -File tools\license-issuer\windows-vm-checklist.ps1
#   powershell -ExecutionPolicy Bypass -File tools\license-issuer\windows-vm-checklist.ps1 -BaseDir D:\HR-Test

param(
    [string]$BaseDir = "C:\Users\User\Documents\HR",
    [string]$PythonVersion = "3.12.3"
)

Set-StrictMode -Version 2.0
$ErrorActionPreference = "Stop"

$work = Join-Path $BaseDir "issuer-pr36-test"
$out = Join-Path $BaseDir "issuer-pr36-test-output"
$repoRoot = Split-Path -Parent $PSScriptRoot
$buildScript = Join-Path $PSScriptRoot "build.ps1"

$failures = @()
$pass = 0
$cliOut1 = ""
$cliOut2 = ""
$cliOut3 = ""
$privHex = ""

function Step-Log([string]$msg) { Write-Host ">>> $msg" -ForegroundColor Cyan }
function Step-Pass([string]$msg) { $script:pass++; Write-Host "PASS: $msg" -ForegroundColor Green }
function Step-Fail([string]$msg) { $script:failures += $msg; Write-Host "FAIL: $msg" -ForegroundColor Red }

Write-Host "=== License issuer Windows VM checklist (PR #36) ==="
Write-Host "PS: $((Get-Host).Version.ToString())"
Write-Host "work:  $work"
Write-Host "out:   $out"

try {
    # ------------------------------------------------ 1. parser check (5.1)
    $tokens = $null
    $errors = $null
    [void][System.Management.Automation.Language.Parser]::ParseFile($buildScript, [ref]$tokens, [ref]$errors)
    $byteCount = (Get-Item $buildScript).Length
    $head = [System.IO.File]::ReadAllBytes($buildScript)[0..2] -join ","
    if ($null -ne $errors -and $errors.Count -ne 0) {
        Step-Fail "parser: $($errors.Count) parse error(s): $(@($errors | ForEach-Object { '{0}:{1} {2}' -f $_.Extent.StartLineNumber, $_.Extent.StartColumnNumber, $_.Message }) -join '; ')"
    } else {
        Step-Pass "parser: build.ps1 parses with 0 errors under $((Get-Host).Version.ToString()) (first bytes $head)"
    }
    $isBom = ($head -eq "239,187,191")
    Step-Log "build.ps1 BOM present: $isBom, size: $byteCount"

    # ------------------------------------------------ 2. clean tree check
    if (Test-Path $repoRoot) {
        $dirty = & git -C $repoRoot status --porcelain 2>&1 | Out-String
        if ($dirty.Trim().Length -ne 0) {
            Step-Fail "repo tree is not clean before the build: $dirty"
        } else {
            Step-Pass "repo tree clean before build"
        }
    }

    # ------------------------------------------------ 3. build
    if (Test-Path $work) { Remove-Item $work -Recurse -Force }
    New-Item -ItemType Directory -Path $work -Force | Out-Null
    $buildDist = Join-Path $work "dist"
    Step-Log "command: powershell -ExecutionPolicy Bypass -File `"$buildScript`" -OutDir `"$buildDist`" -PythonVersion $PythonVersion"
    $previousEap = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try {
        # Tee-Object has no -Encoding in Windows PowerShell 5.1
        $buildLines = @(& powershell -NoProfile -ExecutionPolicy Bypass -File $buildScript -OutDir $buildDist -PythonVersion $PythonVersion *>&1 | ForEach-Object { $line = $_.ToString(); Write-Host $line; $line })
        $buildCode = $LASTEXITCODE
        Set-Content -Path (Join-Path $work "build.log") -Value $buildLines -Encoding UTF8
    } finally { $ErrorActionPreference = $previousEap }
    Step-Log "build exit code: $buildCode"
    $distZip = Join-Path $buildDist "license-issuer-dist.zip"
    if ($buildCode -ne 0) {
        Step-Fail "build failed with exit code $buildCode (see $work\build.log)"
    } elseif (-not (Test-Path $distZip)) {
        Step-Fail "build exited 0 but $distZip is missing"
    } else {
        Step-Pass "build: license-issuer-dist.zip created ($buildCode)"
        $logText = Get-Content (Join-Path $work "build.log") -Raw
        $hexLeak = [regex]::Matches($logText, "(?i)\b[0-9a-f]{64,}\b")
        if ($hexLeak.Count -ne 0) { Step-Fail "build log contains $($hexLeak.Count) run(s) of 64+ hex chars (key material leak?)" }
        else { Step-Pass "build log has no 64+ hex runs" }
    }

    # ------------------------------------------------ 4. fresh unzip
    if (Test-Path $out) { Remove-Item $out -Recurse -Force }
    New-Item -ItemType Directory -Path $out -Force | Out-Null
    $bundle = Join-Path $out "bundle"
    Expand-Archive -Path $distZip -DestinationPath $bundle -Force
    $app = Join-Path $bundle "license-issuer"
    $pyExe = Join-Path $bundle "python\python.exe"
    foreach ($f in @((Join-Path $app "run-cli.bat"), (Join-Path $app "run-gui.bat"), (Join-Path $app "run-html.bat"), $pyExe)) {
        if (-not (Test-Path $f)) { Step-Fail "bundle is missing $f" }
    }
    Step-Pass "bundle unzipped to $bundle"

    # pth check: the generated _pth must contain ..\license-issuer
    $pthFile = Get-ChildItem $bundle\python -Filter "python*._pth" | Select-Object -First 1
    $pthText = Get-Content $pthFile.FullName -Raw
    if ($pthText -match [regex]::Escape("..\" + "license-issuer") -and $pthText -match "import site") {
        Step-Pass "python*._pth contains ..\license-issuer and 'import site'"
    } else {
        Step-Fail "python*._pth lacks the app-dir entry: $pthText"
    }

    # ------------------------------------------------ 5. CLI chain via run-cli.bat
    $env:HRM_NO_PAUSE = "1"
    $env:HRM_NO_BROWSER = "1"
    $runCli = Join-Path $app "run-cli.bat"
    $keys = Join-Path $out "keys"
    $previousEap = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try {
        Step-Log "command: `"$runCli`" gen-keypair --out-dir `"$keys`""
        $cliOut1 = & $runCli gen-keypair --out-dir $keys 2>&1 | Out-String
        $code1 = $LASTEXITCODE
        Step-Log "gen-keypair exit code: $code1"
        Write-Host $cliOut1
        $privFile = Join-Path $keys "private_key.hex"
        $pubFile = Join-Path $keys "public_key.b64"
        if ($code1 -ne 0 -or -not (Test-Path $privFile) -or -not (Test-Path $pubFile)) {
            Step-Fail "run-cli.bat gen-keypair failed (exit=$code1)"
        } else {
            Step-Pass "run-cli.bat gen-keypair (exit=$code1)"
        }
        $privHex = (Get-Content $privFile -Raw).Trim()
        $licDir = Join-Path $out "licenses"
        $licFile = Join-Path $licDir "pilot.hrmlicense"
        Step-Log "command: `"$runCli`" issue --private-key-file `"$privFile`" --client `"Pilot Maria`" --expires 2026-12-31 --max-users 5 --out `"$licFile`""
        $cliOut2 = & $runCli issue --private-key-file $privFile --client "Pilot Maria" --expires 2026-12-31 --max-users 5 --out $licFile 2>&1 | Out-String
        $code2 = $LASTEXITCODE
        Step-Log "issue exit code: $code2"
        Write-Host $cliOut2
        if ($code2 -ne 0 -or -not (Test-Path $licFile)) {
            Step-Fail "run-cli.bat issue failed (exit=$code2)"
        } else {
            Step-Pass "run-cli.bat issue (exit=$code2)"
        }
        Step-Log "command: `"$runCli`" verify --public-key-file `"$pubFile`" --license-file `"$licFile`""
        $cliOut3 = & $runCli verify --public-key-file $pubFile --license-file $licFile 2>&1 | Out-String
        $code3 = $LASTEXITCODE
        Step-Log "verify exit code: $code3"
        Write-Host $cliOut3
        if ($code3 -ne 0) { Step-Fail "run-cli.bat verify failed (exit=$code3)" }
        else { Step-Pass "run-cli.bat verify (exit=$code3)" }
    } finally { $ErrorActionPreference = $previousEap }

    # ------------------------------------------------ 6. HTML launcher: loopback only
    $htmlLog = Join-Path $out "html.log"
    $psi = New-Object System.Diagnostics.ProcessStartInfo
    $psi.FileName = "cmd.exe"
    $psi.Arguments = '/c ""' + (Join-Path $app "run-html.bat") + '" > "' + $htmlLog + '" 2>&1"'   # extra outer quotes: cmd /c strips first+last
    $psi.UseShellExecute = $false
    $psi.CreateNoWindow = $true
    $htmlProc = [System.Diagnostics.Process]::Start($psi)
    Start-Sleep -Seconds 6
    $listen = @(netstat -ano | ForEach-Object { if ($_ -match ":8765" -and $_ -match "LISTENING") { $_ } })
    if ($listen.Count -eq 0) {
        Step-Fail "HTML server did not start (no LISTENING on 8765); log: " + (Get-Content $htmlLog -Raw -ErrorAction SilentlyContinue)
    } else {
        if (@($listen | Where-Object { $_ -match "0\.0\.0\.0:8765" }).Count -ne 0) { Step-Fail "HTML server bound to 0.0.0.0: $listen" }
        elseif (@($listen | Where-Object { $_ -match "127\.0\.0\.1:8765" }).Count -eq 0) { Step-Fail "HTML server not bound to 127.0.0.1: $listen" }
        else {
            Step-Pass "run-html.bat LISTENING only on 127.0.0.1:8765"
            try {
                $resp = Invoke-WebRequest -Uri "http://127.0.0.1:8765/license-issuer.html" -UseBasicParsing
                if ([int]$resp.StatusCode -eq 200) { Step-Pass "HTML page served (HTTP 200, $(([string]$resp.Content).Length) bytes)" }
                else { Step-Fail "HTML page returned HTTP $($resp.StatusCode)" }
            } catch { Step-Fail "HTML page request failed: $($_.Exception.Message)" }
        }
        $listenerPid = [int](($listen | Select-Object -First 1) -split "\s+" | Select-Object -Last 1)
        $previousEap = $ErrorActionPreference
        $ErrorActionPreference = "Continue"
        try { & taskkill /F /PID $listenerPid /T | Out-Null } finally { $ErrorActionPreference = $previousEap }
    }
    if (-not $htmlProc.HasExited) {
        $previousEap = $ErrorActionPreference
        $ErrorActionPreference = "Continue"
        try { & taskkill /F /T /PID $htmlProc.Id 2>&1 | Out-Null } finally { $ErrorActionPreference = $previousEap }
    }

    # ------------------------------------------------ 7. GUI best effort (needs a display)
    $guiLog = Join-Path $out "gui.log"
    $psi2 = New-Object System.Diagnostics.ProcessStartInfo
    $psi2.FileName = "cmd.exe"
    $psi2.Arguments = '/c ""' + (Join-Path $app "run-gui.bat") + '" > "' + $guiLog + '" 2>&1"'
    $psi2.UseShellExecute = $false
    $guiProc = [System.Diagnostics.Process]::Start($psi2)
    Start-Sleep -Seconds 10
    $guiText = (Get-Content $guiLog -Raw -ErrorAction SilentlyContinue)
    if ($guiText -match "ModuleNotFoundError|Traceback") {
        Step-Fail "GUI crashed at startup: $guiText"
    } elseif ($guiProc.HasExited) {
        Step-Log "GUI process exited early (code=$($guiProc.ExitCode)) without traceback - no display? manual GUI check required"
    } else {
        Step-Pass "GUI process stayed alive without import errors (killed; manual interaction still required)"
        $previousEap = $ErrorActionPreference
        $ErrorActionPreference = "Continue"
        try { & taskkill /F /T /PID $guiProc.Id 2>&1 | Out-Null } finally { $ErrorActionPreference = $previousEap }
    }

    # ------------------------------------------------ 8. key-material sweep
    if (-not $privHex) { Step-Fail "no private key generated - sweep skipped" }
    $swept = 0
    if ($privHex) {
    foreach ($f in (Get-ChildItem $work, $out -Recurse -File | Where-Object { $_.FullName -ne $privFile -and $_.Length -lt 20MB })) {
        try {
            if ((Get-Content $f.FullName -Raw -Encoding utf8 -ErrorAction Stop) -match [regex]::Escape($privHex)) {
                Step-Fail "private key material found in: $($f.FullName)"
            }
            $swept++
        } catch [System.IO.IOException] { }
    }
    }
    if ($privHex) {
        Step-Pass "key sweep: $swept files scanned in work+out, private key not found"
        $allCliOut = $cliOut1 + $cliOut2 + $cliOut3
        if ($allCliOut -match [regex]::Escape($privHex)) { Step-Fail "private key material in CLI output" }
        else { Step-Pass "no private key material in CLI stdout/stderr" }
    }

    # ------------------------------------------------ 9. repo cleanliness after
    if (Test-Path $repoRoot) {
        $dirty = & git -C $repoRoot status --porcelain 2>&1 | Out-String
        if ($dirty.Trim().Length -ne 0) { Step-Fail "repo tree dirty after checks: $dirty" }
        else { Step-Pass "repo tree clean after checks" }
    }
}
finally {
    Write-Host ""
    Write-Host "=== RESULT: $pass PASS, $($failures.Count) FAIL ==="
    foreach ($f in $failures) { Write-Host "  FAIL: $f" -ForegroundColor Red }
    # keep the directories for inspection; delete only when explicitly requested
    Write-Host "work/out directories kept for inspection: $work / $out"
}
if ($failures.Count -ne 0) { exit 1 }
exit 0
