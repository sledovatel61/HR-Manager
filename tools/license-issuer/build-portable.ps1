# HR Manager - build the PORTABLE single-file license issuer for the owner PC.
#
# Result: tools/license-issuer/dist/LicenseIssuer-Portable.exe
#   * ONE file the owner double-clicks: no Python, no Node, no Docker, no
#     Visual Studio, no internet, no installation step;
#   * first run extracts the bundled runtime to
#     %LOCALAPPDATA%\HRManager\LicenseIssuer\payload-<size> and opens the GUI;
#   * command line: "LicenseIssuer-Portable.exe verify --public-key-file ... --license-file ..."
#     runs the bundled CLI and returns its exit code (CI uses this).
#
# Build recipe (no third-party binaries, no downloads beyond what build.ps1
# already fetched from python.org / PyPI):
#   1. build.ps1 produces dist\python\ (embeddable Python + cryptography) and
#      dist\license-issuer\ (gui.py, cli.py, license_issuer.py, launchers);
#   2. this script packs both folders into dist\license-issuer-payload.zip;
#   3. the launcher (launcher\Program.cs, ASCII-only) is compiled with the
#      csc.exe that ships with Windows/.NET Framework (Roslyn csc from Visual
#      Studio is used when present - it also makes the PE header deterministic);
#   4. the ZIP is appended to the launcher with a 32-byte trailer
#      ("HRMISSUER-PAYLOAD1" + 14 ASCII digits with the payload length), which
#      is exactly what launcher\Program.cs reads back at run time.
#
# ENCODING CONTRACT (same as build.ps1): this file is UTF-8 WITH BOM and
# ASCII-only, so Windows PowerShell 5.1 can parse it under any code page.
#
# Usage (maintainer, after build.ps1 has run at least once):
#   powershell -ExecutionPolicy Bypass -File tools/license-issuer/build-portable.ps1
# Optional:
#   -Version 0.15.0            version stamped into BUILD-INFO.txt
#   -CryptographyVersion 50.0.2  recorded for reproducibility (pinned by build.ps1)
#   -SkipSelfTest              skip the run-the-exe smoke test (not recommended)

param(
    [string]$Version = "",
    [string]$PythonVersion = "3.12.3",
    [string]$CryptographyVersion = "50.0.2",
    [string]$OutDir = "$PSScriptRoot\dist",
    [switch]$SkipSelfTest
)

Set-StrictMode -Version 2.0
$ErrorActionPreference = "Stop"

function Write-Info($msg) { Write-Host $msg -ForegroundColor Cyan }
function Write-Warn2($msg) { Write-Host $msg -ForegroundColor Yellow }
function Write-Err($msg) { Write-Host $msg -ForegroundColor Red }

# A hung or failing portable exe must not stay a black box: the launcher keeps
# its own stage log and error log below %LOCALAPPDATA%\HRManager\LicenseIssuer
# (the launcher source never touches keys). Dumping them into the build log is
# also what makes the failure visible through the CI check-run annotations,
# which is the only channel readable from outside a private repository.
function Show-PortableDiagnostics {
    param([string]$Reason)
    Write-Warn2 ("portable diagnostics: " + $Reason)
    $diagRoot = Join-Path $env:LOCALAPPDATA "HRManager\LicenseIssuer"
    Write-Warn2 ("diagnostics root: " + $diagRoot)
    foreach ($diagName in @("launcher-trace.log", "launcher-error.log")) {
        $diagPath = Join-Path $diagRoot $diagName
        if (Test-Path $diagPath) {
            Write-Warn2 ("--- " + $diagName + " (last 30 lines) ---")
            foreach ($diagLine in @(Get-Content -Path $diagPath -Tail 30 -ErrorAction SilentlyContinue)) {
                Write-Warn2 ("    " + $diagLine)
            }
        }
        else {
            Write-Warn2 ("--- " + $diagName + ": not found ---")
        }
    }
}

# Launcher processes are GUI-subsystem exes: PowerShell does not wait for those,
# so the exit code has to be read through Start-Process -Wait -PassThru, with an
# explicit timeout so a stuck process fails the build instead of hanging it.
# The timeout is short on purpose: unpacking 20 MB takes seconds, so a longer
# wait only hides a broken artifact and burns CI time.
function Invoke-PortableExe {
    param([string]$Exe, [string[]]$Arguments = @(), [int]$TimeoutSeconds = 300, [string]$Stage = "")
    $quoted = @()
    foreach ($argument in $Arguments) {
        if ($argument -match '[\s"]') { $quoted += ('"' + ($argument -replace '"', '\"') + '"') } else { $quoted += $argument }
    }
    # The process is started through .NET rather than Start-Process: Windows
    # PowerShell 5.1 does not always expose the exit code of a GUI-subsystem
    # process (in CI the -PassThru object returned an empty ExitCode), and the
    # verdict must never depend on that. An exit code that still cannot be read is
    # reported as a hard failure instead of being treated as success.
    $psi = New-Object System.Diagnostics.ProcessStartInfo
    $psi.FileName = $Exe
    $psi.Arguments = ($quoted -join " ")
    $psi.UseShellExecute = $false
    $psi.WorkingDirectory = (Split-Path $Exe -Parent)
    $process = [System.Diagnostics.Process]::Start($psi)
    if (-not $process.WaitForExit($TimeoutSeconds * 1000)) {
        Show-PortableDiagnostics ("timeout after {0}s (stage '{1}'): {2}" -f $TimeoutSeconds, $Stage, ($quoted -join " "))
        try { $process.Kill() } catch { }
        throw ("TIMEOUT: {0} did not finish in {1} seconds" -f $Exe, $TimeoutSeconds)
    }
    $exitCode = $null
    try { $process.Refresh(); $exitCode = $process.ExitCode } catch { $exitCode = $null }
    if ($null -eq $exitCode) {
        Show-PortableDiagnostics ("exit code of {0} is not readable (stage '{1}')" -f $Exe, $Stage)
        throw ("cannot read the exit code of {0}" -f $Exe)
    }
    return [pscustomobject]@{ ExitCode = $exitCode }
}

$pythonDir = Join-Path $OutDir "python"
$appDir = Join-Path $OutDir "license-issuer"
$payloadZip = Join-Path $OutDir "license-issuer-payload.zip"
$exePath = Join-Path $OutDir "LicenseIssuer-Portable.exe"
$launcherSource = Join-Path $PSScriptRoot "launcher\Program.cs"
$infoPath = Join-Path $OutDir "BUILD-INFO.txt"
$payloadMarker = "HRMISSUER-PAYLOAD1"
$lengthDigits = 14

Write-Info "=== HR Manager License Issuer - portable single-file build ==="
Write-Info ("Host: PowerShell {0} ({1} edition), {2}" -f $PSVersionTable.PSVersion, $PSVersionTable.PSEdition, (Get-Process -Id $PID).Path)

if (-not (Test-Path $pythonDir)) { Write-Err "Bundled Python not found at $pythonDir - run build.ps1 first"; exit 1 }
if (-not (Test-Path (Join-Path $pythonDir "python.exe"))) { Write-Err "python.exe not found in $pythonDir - run build.ps1 first"; exit 1 }
if (-not (Test-Path $appDir)) { Write-Err "Issuer app folder not found at $appDir - run build.ps1 first"; exit 1 }
foreach ($required in @("gui.py", "cli.py", "license_issuer.py")) {
    if (-not (Test-Path (Join-Path $appDir $required))) { Write-Err "$required not found in $appDir - run build.ps1 first"; exit 1 }
}
if (-not (Test-Path $launcherSource)) { Write-Err "launcher source not found at $launcherSource"; exit 1 }

# 1. Pack the payload (Python runtime + issuer app) with the native ZIP writer.
#    Windows PowerShell 5.1 Compress-Archive needs tens of minutes for the ~10k
#    small files of the embeddable Python + Tcl/Tk (and copies the tree twice);
#    System.IO.Compression.ZipArchive is fast and writes forward-slash entry
#    names, which is exactly what the launcher reads back.
Write-Info "Packing payload zip $payloadZip (System.IO.Compression.ZipArchive) ..."
if (Test-Path $payloadZip) { Remove-Item $payloadZip -Force }
try { Add-Type -AssemblyName System.IO.Compression | Out-Null } catch { }
$fileTotal = 0
$rawBytes = 0
$packStarted = Get-Date
$zipStream = [System.IO.File]::Open($payloadZip, [System.IO.FileMode]::Create, [System.IO.FileAccess]::Write)
try {
    $archive = New-Object System.IO.Compression.ZipArchive($zipStream, [System.IO.Compression.ZipArchiveMode]::Create)
    try {
        foreach ($source in @($pythonDir, $appDir)) {
            $baseName = Split-Path $source -Leaf
            foreach ($file in (Get-ChildItem -Path $source -Recurse -File -ErrorAction SilentlyContinue)) {
                $relative = $file.FullName.Substring($source.Length).TrimStart('\', '/')
                if (($relative -split '[\\/]') -contains "__pycache__") { continue }
                if ($file.Extension -eq ".pyc") { continue }
                $entryName = ($baseName + "/" + $relative).Replace('\', '/')
                $entry = $archive.CreateEntry($entryName, [System.IO.Compression.CompressionLevel]::Optimal)
                # Fixed entry time (1980-01-01 is the earliest the ZIP format can
                # store): the payload must not depend on when it was built, so the
                # portable exe stays reproducible from the pinned inputs.
                $entry.LastWriteTime = [datetime]::new(1980, 1, 1, 0, 0, 0)
                $target = $entry.Open()
                try {
                    $sourceStream = [System.IO.File]::OpenRead($file.FullName)
                    try { $sourceStream.CopyTo($target) } finally { $sourceStream.Dispose() }
                } finally { $target.Dispose() }
                $fileTotal++
                $rawBytes += $file.Length
                if (($fileTotal % 2000) -eq 0) {
                    Write-Host ("  ... {0} files, {1:N0} MB raw, {2:N1}s" -f $fileTotal, ($rawBytes / 1MB), ((Get-Date) - $packStarted).TotalSeconds)
                }
            }
        }
    } finally { $archive.Dispose() }
} finally { $zipStream.Dispose() }
if (-not (Test-Path $payloadZip)) { Write-Err "payload zip was not created"; exit 1 }
$payloadSize = (Get-Item $payloadZip).Length
Write-Info ("payload zip: {0} files, {1:N0} bytes, packed in {2:N1}s" -f $fileTotal, $payloadSize, ((Get-Date) - $packStarted).TotalSeconds)
if ($fileTotal -eq 0) { Write-Err "payload zip is empty - nothing to pack"; exit 1 }
if ($payloadSize -ge 10000000000) { Write-Err "payload is too large for the 14-digit trailer"; exit 1 }

# 1b. Read the payload archive back with the same API the launcher uses. The
#     first run of the artifact in CI failed inside ZipArchive while opening the
#     payload that was appended to the exe, so the archive itself is now verified
#     here, in the build, before the exe is assembled: a payload that cannot be
#     opened must stop the build, not the owner's double-click.
try {
    $checkStream = [System.IO.File]::OpenRead($payloadZip)
    try {
        $checkArchive = New-Object System.IO.Compression.ZipArchive($checkStream, [System.IO.Compression.ZipArchiveMode]::Read)
        try { $checkEntries = $checkArchive.Entries.Count } finally { $checkArchive.Dispose() }
    } finally { $checkStream.Dispose() }
}
catch {
    Write-Err ("payload zip cannot be read back: " + $_.Exception.Message)
    exit 1
}
if ($checkEntries -ne $fileTotal) {
    Write-Err ("payload zip entry count mismatch: {0} in the archive, {1} packed" -f $checkEntries, $fileTotal)
    exit 1
}
Write-Info ("payload zip verified: {0} entries readable" -f $checkEntries)

# 2. Compile the launcher. Roslyn csc (from Visual Studio / Build Tools) is
#    preferred: it emits a deterministic PE header. The .NET Framework csc that
#    is present on every Windows 10/11 machine is the fallback, so the owner's
#    artifact can be rebuilt even without Visual Studio.
function Find-CSharpCompiler {
    $candidates = New-Object System.Collections.ArrayList
    $vswhere = Join-Path ${env:ProgramFiles(x86)} "Microsoft Visual Studio\Installer\vswhere.exe"
    if (Test-Path $vswhere) {
        try {
            $vsPath = & $vswhere -latest -products * -requires Microsoft.Component.MSBuild -property installationPath 2>$null
            if ($vsPath) {
                foreach ($relative in @("MSBuild\Current\Bin\Roslyn\csc.exe", "MSBuild\Current\Bin\csc.exe")) {
                    $candidate = Join-Path (($vsPath | Select-Object -First 1).Trim()) $relative
                    if (Test-Path $candidate) { [void]$candidates.Add($candidate) }
                }
            }
        } catch { }
    }
    foreach ($framework in @("$env:WINDIR\Microsoft.NET\Framework64\v4.0.30319\Roslyn\csc.exe", "$env:WINDIR\Microsoft.NET\Framework64\v4.0.30319\csc.exe", "$env:WINDIR\Microsoft.NET\Framework\v4.0.30319\csc.exe")) {
        if (Test-Path $framework) { [void]$candidates.Add($framework) }
    }
    return $candidates
}

$compilers = Find-CSharpCompiler
if ($compilers.Count -eq 0) {
    Write-Err "No C# compiler found. Windows 10/11 ships csc.exe in %WINDIR%\Microsoft.NET\Framework64\v4.0.30319 - install .NET Framework 4.x or Visual Studio Build Tools."
    exit 1
}
$csc = $compilers[0]
Write-Info "C# compiler: $csc"
$isRoslyn = ($csc -match "Roslyn")

$frameworkDir = Join-Path $env:WINDIR "Microsoft.NET\Framework64\v4.0.30319"
$references = @(
    (Join-Path $frameworkDir "System.dll"),
    (Join-Path $frameworkDir "System.Core.dll"),
    (Join-Path $frameworkDir "System.IO.Compression.dll"),
    (Join-Path $frameworkDir "System.Windows.Forms.dll"),
    (Join-Path $frameworkDir "System.Drawing.dll")
)
foreach ($reference in $references) {
    if (-not (Test-Path $reference)) { Write-Err "reference assembly not found: $reference"; exit 1 }
}
if (-not (Test-Path (Join-Path $frameworkDir "System.IO.Compression.FileSystem.dll"))) {
    Write-Warn2 "System.IO.Compression.FileSystem.dll not present - not needed (the launcher uses ZipArchive only)"
}

$compiledLauncher = Join-Path $OutDir "LicenseIssuer-Launcher.exe"
if (Test-Path $compiledLauncher) { Remove-Item $compiledLauncher -Force }
$cscArgs = @(
    "/nologo",
    "/target:winexe",
    "/optimize+",
    "/platform:anycpu",
    "/out:$compiledLauncher",
    "/r:$($references -join ',')"
)
if ($isRoslyn) { $cscArgs += "/deterministic" }
$cscArgs += $launcherSource
Write-Info "Compiling launcher ..."
$previousEap = $ErrorActionPreference
$ErrorActionPreference = "Continue"
try {
    & $csc @cscArgs 2>&1 | ForEach-Object { Write-Host $_.ToString() }
    $compileCode = $LASTEXITCODE
} finally { $ErrorActionPreference = $previousEap }
if ($compileCode -ne 0 -or -not (Test-Path $compiledLauncher)) {
    Write-Err "launcher compilation failed (csc exit=$compileCode)"
    exit 1
}
if (-not $isRoslyn) {
    Write-Warn2 "Built with the .NET Framework csc (C# 5): the artifact is content-reproducible, not byte-reproducible."
}
Write-Info ("launcher: {0:N0} bytes" -f (Get-Item $compiledLauncher).Length)

# 3. Assemble: launcher + payload + trailer (marker + 14 digits).
Write-Info "Assembling $exePath ..."
if (Test-Path $exePath) { Remove-Item $exePath -Force }
Copy-Item -Path $compiledLauncher -Destination $exePath -Force
$destination = [System.IO.File]::Open($exePath, [System.IO.FileMode]::Append, [System.IO.FileAccess]::Write, [System.IO.FileShare]::None)
try {
    $payloadStream = [System.IO.File]::OpenRead($payloadZip)
    try { $payloadStream.CopyTo($destination) } finally { $payloadStream.Dispose() }
    $trailer = New-Object System.Collections.Generic.List[byte]
    $trailer.AddRange([System.Text.Encoding]::ASCII.GetBytes($payloadMarker))
    $trailer.AddRange([System.Text.Encoding]::ASCII.GetBytes($payloadSize.ToString("D$lengthDigits")))
    if ($trailer.Count -ne ($payloadMarker.Length + $lengthDigits)) {
        Write-Err ("trailer size mismatch: {0} bytes" -f $trailer.Count)
        exit 1
    }
    $destination.Write($trailer.ToArray(), 0, $trailer.Count)
    $destination.Flush()
} finally { $destination.Dispose() }
$exeSize = (Get-Item $exePath).Length
Write-Info ("portable exe: {0:N0} bytes (launcher {1:N0} + payload {2:N0} + trailer {3})" -f $exeSize, (Get-Item $compiledLauncher).Length, $payloadSize, ($payloadMarker.Length + $lengthDigits))

# 4. Read the trailer back the same way the launcher does. If this check fails
#    the artifact would be born broken - fail closed before the smoke test.
$trailerSize = $payloadMarker.Length + $lengthDigits
$trailerBytes = New-Object byte[] $trailerSize
$readStream = [System.IO.File]::OpenRead($exePath)
try {
    $readStream.Seek(-$trailerSize, [System.IO.SeekOrigin]::End) | Out-Null
    $readTotal = 0
    while ($readTotal -lt $trailerSize) {
        $readNow = $readStream.Read($trailerBytes, $readTotal, $trailerSize - $readTotal)
        if ($readNow -le 0) { Write-Err "could not read the trailer back from $exePath"; exit 1 }
        $readTotal += $readNow
    }
} finally { $readStream.Dispose() }
$markerBack = [System.Text.Encoding]::ASCII.GetString($trailerBytes, 0, $payloadMarker.Length)
if ($markerBack -ne $payloadMarker) { Write-Err "trailer marker mismatch: '$markerBack'"; exit 1 }
$lengthBack = [System.Text.Encoding]::ASCII.GetString($trailerBytes, $payloadMarker.Length, $lengthDigits)
if ([long]$lengthBack -ne $payloadSize) { Write-Err "trailer length mismatch: $lengthBack vs $payloadSize"; exit 1 }
Write-Info "trailer verified: marker '$markerBack', payload length $lengthBack"

# 5. SHA256 evidence for the release notes / acceptance checklist.
$exeHash = (Get-FileHash -Path $exePath -Algorithm SHA256).Hash
$payloadHash = (Get-FileHash -Path $payloadZip -Algorithm SHA256).Hash
Write-Info "SHA256 (portable exe): $exeHash"
Write-Info "SHA256 (payload zip):  $payloadHash"

$info = @()
$info += "HR Manager License Issuer - portable single file"
$info += "version: $Version"
$info += "python: $PythonVersion embeddable (python.org)"
$info += "cryptography: $CryptographyVersion"
$info += "compiler: $csc"
$info += "compiler_deterministic: $isRoslyn"
$info += "sha256_exe: $exeHash"
$info += "sha256_payload_zip: $payloadHash"
$info += "payload_bytes: $payloadSize"
$info += "exe_bytes: $exeSize"
$info += "built_in_ci_or_windows_only: yes (csc.exe comes from Windows/.NET Framework)"
Set-Content -Path $infoPath -Value $info -Encoding ASCII

if ($SkipSelfTest) {
    Write-Warn2 "-SkipSelfTest: the artifact was NOT executed. Run it once on Windows before handing it to the owner."
    Write-Info "Build complete: $exePath"
    exit 0
}

# 6. Smoke test: run the artifact exactly as the owner/CI would, in a temporary
#    directory outside the repository (key material must never land in the work
#    tree), then remove everything. The private key must never appear in the log.
Write-Info ("[{0:HH:mm:ss}] Smoke test: --hrm-selfcheck + CLI chain (gen-keypair -> issue -> verify) through the portable exe ..." -f (Get-Date))
$smokeDir = Join-Path ([System.IO.Path]::GetTempPath()) ("hrm-portable-smoke-" + [guid]::NewGuid().ToString("N"))
try {
    New-Item -ItemType Directory -Path $smokeDir -Force | Out-Null
    $selfCheckJson = Join-Path $smokeDir "selfcheck.json"
    # HRM_NO_DIALOG: a modal window would never be dismissed on a build machine,
    # so the launcher must report through its log files instead of blocking.
    $env:HRM_NO_DIALOG = "1"

    $selfCheckRun = Invoke-PortableExe -Exe $exePath -Arguments @("--hrm-selfcheck", $selfCheckJson) -TimeoutSeconds 300 -Stage "selfcheck"
    if ($selfCheckRun.ExitCode -ne 0) {
        Show-PortableDiagnostics ("selfcheck exit code " + $selfCheckRun.ExitCode)
        Write-Err "selfcheck exited with $($selfCheckRun.ExitCode)"
        exit 1
    }
    if (-not (Test-Path $selfCheckJson)) { Write-Err "selfcheck did not write $selfCheckJson"; exit 1 }
    $selfCheck = Get-Content -Path $selfCheckJson -Raw | ConvertFrom-Json
    if (-not $selfCheck.ok) { Write-Err ("selfcheck reported failure: missing=" + $selfCheck.missing); exit 1 }
    Write-Info ("selfcheck PASS: payload={0} files, {1:N0} bytes" -f $selfCheck.payload_file_count, $selfCheck.payload_total_bytes)

    $keysDir = Join-Path $smokeDir "keys"
    $licenseFile = Join-Path $smokeDir "smoke.hrmlicense"
    $steps = @(
        @{ Name = "gen-keypair"; Args = @("gen-keypair", "--out-dir", $keysDir) },
        @{ Name = "issue"; Args = @("issue", "--private-key-file", (Join-Path $keysDir "private_key.hex"), "--client", "Smoke Test", "--expires", "2099-12-31", "--max-users", "5", "--out", $licenseFile) },
        @{ Name = "verify"; Args = @("verify", "--public-key-file", (Join-Path $keysDir "public_key.b64"), "--license-file", $licenseFile) }
    )
    $outputs = @{}
    foreach ($step in $steps) {
        # HRM_PORTABLE_LOG (launcher feature): the child CLI writes its stdout/stderr
        # into a file, so the key-material scan below sees the real command output.
        $logPath = Join-Path $smokeDir ("cli-" + $step.Name + ".log")
        $env:HRM_PORTABLE_LOG = $logPath
        try {
            $run = Invoke-PortableExe -Exe $exePath -Arguments $step.Args -TimeoutSeconds 300 -Stage $step.Name
        } finally { Remove-Item Env:HRM_PORTABLE_LOG -ErrorAction SilentlyContinue }
        $text = ""
        if (Test-Path $logPath) { $text = (Get-Content -Path $logPath -Raw) }
        $outputs[$step.Name] = $text
        if ($run.ExitCode -ne 0) {
            Show-PortableDiagnostics ("CLI step '{0}' exit code {1}" -f $step.Name, $run.ExitCode)
            Write-Err ("portable CLI step '{0}' exited with code {1}" -f $step.Name, $run.ExitCode)
            Write-Err ("log: " + $text)
            exit 1
        }
        Write-Info ("  {0}: exit 0, log {1:N0} bytes" -f $step.Name, $text.Length)
    }
    if (-not (Test-Path $licenseFile)) { Write-Err "portable CLI did not create the license file"; exit 1 }

    $privateHex = (Get-Content -Path (Join-Path $keysDir "private_key.hex") -Raw).Trim()
    if ($privateHex) {
        foreach ($name in $outputs.Keys) {
            if ($outputs[$name] -match [regex]::Escape($privateHex)) {
                Write-Err "private key material leaked into '$name' output - refusing to ship"
                exit 1
            }
        }
    }
    Write-Info "Smoke test PASS: portable exe opens the GUI path, runs the CLI chain and never prints key material"
} catch {
    Show-PortableDiagnostics ("smoke test failed: " + $_.Exception.Message)
    Write-Err "smoke test failed: $_"
    exit 1
} finally {
    Remove-Item Env:HRM_NO_DIALOG -ErrorAction SilentlyContinue
    if (Test-Path $smokeDir) { Remove-Item -Path $smokeDir -Recurse -Force -ErrorAction SilentlyContinue }
    if (Test-Path $smokeDir) { Write-Warn2 "could not remove $smokeDir - delete it manually" }
}

# 7. Fail closed if any key-like file slipped into the dist/ tree.
$leaks = @(Get-ChildItem -Path $OutDir -Recurse -File -ErrorAction SilentlyContinue | Where-Object { $_.Name -match "private_key|\.hrmlicense$" })
if ($leaks.Count -ne 0) {
    Write-Err ("key material found under dist/: " + (($leaks | ForEach-Object { $_.FullName }) -join ", "))
    exit 1
}

Write-Info "Build complete!"
Write-Info "  portable exe: $exePath"
Write-Info "  sha256:       $exeHash"
Write-Info "  build info:   $infoPath"
Write-Info "Hand this ONE file to the owner. Double-click opens the GUI; the exe needs no Python/Node/Docker/Visual Studio and no internet."
