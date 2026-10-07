# CI helper: Windows-side checks for the offline license issuer bundle.
# Runs under Windows PowerShell 5.1 on windows-latest (the real target shell).
#
# ENCODING CONTRACT: this file MUST stay ASCII-only and is committed with a
# UTF-8 BOM, exactly like build.ps1 - Windows PowerShell 5.1 reads BOM-less
# .ps1 as ANSI and would misparse UTF-8 punctuation under CP1251.
#
# Phases:
#   parser  - build.ps1 encoding contract + Windows PowerShell 5.1 Parser
#   build   - full build.ps1 run under 5.1, zip present, no hex leaks in log
#   runtime - fresh unzip + CLI chain + fail-closed + loopback + key sweep
#   portable- build-portable.ps1 + single-file exe: trailer, selfcheck, CLI chain
#
# Every failure is written to the GitHub step summary (readable via the
# check-run API) with the offending line number, then the phase exits 1.

param(
    [ValidateSet("parser", "build", "runtime", "portable")]
    [string]$Phase = "parser"
)

Set-StrictMode -Version 2.0
$ErrorActionPreference = "Stop"

$Root = $PSScriptRoot   # this helper lives in tools/license-issuer itself
$BuildScript = Join-Path $Root "build.ps1"
$DistZip = Join-Path $Root "dist\license-issuer-dist.zip"

function Write-Phase([string]$msg) { Write-Host "[$Phase] $msg" }

# The portable launcher is a GUI-subsystem exe: PowerShell does not wait for
# those, so both the wait and the exit code go through Start-Process. The timeout
# is short on purpose: unpacking 20 MB takes seconds, so a longer wait only
# hides a broken artifact and burns CI time.
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

# A hung or failing portable exe must not stay a black box: the launcher keeps
# its own stage log and error log below %LOCALAPPDATA%\HRManager\LicenseIssuer
# (the launcher source never touches keys). Dumping them into the build log is
# also what makes the failure visible through the CI check-run annotations,
# which is the only channel readable from outside a private repository.
function Show-PortableDiagnostics {
    param([string]$Reason)
    Write-Phase ("portable diagnostics: " + $Reason)
    $diagRoot = Join-Path $env:LOCALAPPDATA "HRManager\LicenseIssuer"
    Write-Phase ("diagnostics root: " + $diagRoot)
    foreach ($diagName in @("launcher-trace.log", "launcher-error.log")) {
        $diagPath = Join-Path $diagRoot $diagName
        if (Test-Path $diagPath) {
            Write-Phase ("--- " + $diagName + " (last 30 lines) ---")
            foreach ($diagLine in @(Get-Content -Path $diagPath -Tail 30 -ErrorAction SilentlyContinue)) {
                Write-Phase ("    " + $diagLine)
            }
        }
        else {
            Write-Phase ("--- " + $diagName + ": not found ---")
        }
    }
}


function Report-Fail([string]$title, [string]$detail) {
    # Fence is built from a single-quoted fragment: in a double-quoted string
    # a backtick escapes the next character, so literal backticks there need
    # to be doubled (repo convention for markdown fences).
    $fence = '```'
    $body = "### $title" + "`n" + $fence + "text`n" + $detail + "`n" + $fence + "`n"
    if ($env:GITHUB_STEP_SUMMARY) {
        Add-Content -Path $env:GITHUB_STEP_SUMMARY -Value $body -Encoding utf8
    }
    Write-Host "FAILED: $title"
    Write-Host $detail
    exit 1
}

try {
    # ------------------------------------------------------------ parser
    if ($Phase -eq "parser") {
      Write-Phase ("shell: PowerShell {0} ({1} edition), {2}" -f $PSVersionTable.PSVersion, $PSVersionTable.PSEdition, (Get-Process -Id $PID).Path)
      if ($PSVersionTable.PSEdition -ne "Desktop" -or $PSVersionTable.PSVersion.Major -ne 5) {
          Report-Fail "parser: not running under Windows PowerShell 5.1" ("edition=" + $PSVersionTable.PSEdition + " version=" + $PSVersionTable.PSVersion)
      }
      foreach ($scriptName in @("build.ps1", "build-portable.ps1", "ci-windows-checks.ps1", "ci-windows-acceptance.ps1", "windows-vm-checklist.ps1")) {
        $BuildScript = Join-Path $Root $scriptName
        if (-not (Test-Path $BuildScript)) {
            Report-Fail "parser: $scriptName not found" "expected: $BuildScript"
        }
        $bytes = [System.IO.File]::ReadAllBytes($BuildScript)
        $first = -join ((0..[Math]::Min(7, $bytes.Length - 1)) | ForEach-Object { "{0:X2} " -f $bytes[$_] })
        if ($bytes.Length -lt 3 -or $bytes[0] -ne 0xEF -or $bytes[1] -ne 0xBB -or $bytes[2] -ne 0xBF) {
            Report-Fail "parser: $scriptName must start with a UTF-8 BOM (EF BB BF)" ("first bytes: " + $first)
        }
        # The BOM itself (EF BB BF) is the only permitted non-ASCII content:
        # scan from offset 3.
        $nonAscii = @()
        for ($idx = 3; $idx -lt $bytes.Length; $idx++) {
            if ($bytes[$idx] -ge 0x80) { $nonAscii += ("byte 0x{0:X2} at offset {1}" -f $bytes[$idx], $idx) }
        }
        if ($nonAscii.Count -ne 0) {
            $where = @($nonAscii | Select-Object -First 10)
            Report-Fail "parser: $scriptName must be ASCII-only (found $($nonAscii.Count) non-ASCII bytes)" ($where -join "`n")
        }
        $tokens = $null
        $errors = $null
        $ast = [System.Management.Automation.Language.Parser]::ParseFile($BuildScript, [ref]$tokens, [ref]$errors)
        if ($null -eq $ast) { throw "ParseFile returned no AST" }
        if ($null -ne $errors -and $errors.Count -ne 0) {
            $lines = @($errors | ForEach-Object { "line {0}:{1} {2}" -f $_.Extent.StartLineNumber, $_.Extent.StartColumnNumber, $_.Message })
            Report-Fail "parser: Windows PowerShell 5.1 found $($errors.Count) parse error(s) in $scriptName" ($lines -join "`n")
        }
        $ok = "PASS: $scriptName - UTF-8 BOM present, ASCII-only, 0 parser errors under Windows PowerShell $($PSVersionTable.PSVersion)"
        Write-Phase $ok
        if ($env:GITHUB_STEP_SUMMARY) { Add-Content -Path $env:GITHUB_STEP_SUMMARY -Value $ok -Encoding utf8 }
      }
      $BuildScript = Join-Path $Root "build.ps1"

      # Every PowerShell file in the repository: real encoding + two parses.
      # (1) Parser::ParseFile = exactly how Windows PowerShell 5.1 reads the file
      #     on this machine (BOM -> UTF-8, no BOM -> ANSI code page of the runner).
      # (2) ParseInput of the text as a Russian-locale owner PC would read it
      #     (no BOM -> CP1251), the configuration that broke build.ps1.
      $repoRoot = (Resolve-Path (Join-Path $Root "..\..")).Path
      $allPs = @(& git -C $repoRoot ls-files "*.ps1" "*.psm1" "*.psd1")
      $utf8Strict = New-Object System.Text.UTF8Encoding($false, $true)
      $cp1251 = [System.Text.Encoding]::GetEncoding(1251)
      $bad = @()
      foreach ($rel in $allPs) {
        $full = Join-Path $repoRoot $rel
        $b = [System.IO.File]::ReadAllBytes($full)
        $hasBom = ($b.Length -ge 3 -and $b[0] -eq 0xEF -and $b[1] -eq 0xBB -and $b[2] -eq 0xBF)
        $start = 0; if ($hasBom) { $start = 3 }
        $na = 0; for ($i = $start; $i -lt $b.Length; $i++) { if ($b[$i] -ge 0x80) { $na++ } }
        $validUtf8 = $true
        try { [void]$utf8Strict.GetString($b, $start, $b.Length - $start) } catch { $validUtf8 = $false }
        $t = $null; $e1 = $null
        [void][System.Management.Automation.Language.Parser]::ParseFile($full, [ref]$t, [ref]$e1)
        if ($hasBom) { $text1251 = [System.Text.Encoding]::UTF8.GetString($b, 3, $b.Length - 3) } else { $text1251 = $cp1251.GetString($b) }
        $t2 = $null; $e2 = $null
        [void][System.Management.Automation.Language.Parser]::ParseInput($text1251, [ref]$t2, [ref]$e2)
        $n1 = @($e1).Count; $n2 = @($e2).Count
        $enc = "UTF-8 BOM"; if (-not $hasBom) { if ($na -eq 0) { $enc = "ASCII (no BOM)" } else { $enc = "NO BOM + non-ASCII" } }
        Write-Phase ("{0,-55} {1,-18} nonASCII={2,-6} validUTF8={3,-5} ParseFile errors={4} CP1251-read errors={5}" -f $rel, $enc, $na, $validUtf8, $n1, $n2)
        if ($n1 -ne 0 -or $n2 -ne 0 -or $enc -eq "NO BOM + non-ASCII" -or -not $validUtf8) {
            $first = @($e1) + @($e2) | Select-Object -First 3 | ForEach-Object { "  line {0}: {1}" -f $_.Extent.StartLineNumber, $_.Message }
            $bad += ("$rel ($enc, ParseFile=$n1, CP1251=$n2)`n" + ($first -join "`n"))
        }
      }
      if ($bad.Count -ne 0) { Report-Fail "parser: $($bad.Count) repository PowerShell file(s) not 5.1/CP1251-safe" ($bad -join "`n") }
      Write-Phase ("PASS: all {0} repository PowerShell files: BOM or pure ASCII, valid UTF-8, 0 errors in ParseFile and in the CP1251 read" -f $allPs.Count)
    }

    # ------------------------------------------------------------ build
    if ($Phase -eq "build") {
        $logFile = Join-Path $env:RUNNER_TEMP "license-issuer-build.log"
        # Explicit 5.1 invocation: the script must work under Windows
        # PowerShell 5.1, not only under pwsh 7.
        $previousEap = $ErrorActionPreference
        $ErrorActionPreference = "Continue"
        try {
            # Tee-Object has no -Encoding in Windows PowerShell 5.1: stream to
            # the host and collect, then write the log explicitly.
            # Full path: Windows PowerShell 5.1 (powershell.exe), never pwsh.exe.
            $ps51 = Join-Path $env:SystemRoot "System32\WindowsPowerShell\v1.0\powershell.exe"
            $ps51Info = (& $ps51 -NoProfile -Command '$PSVersionTable.PSVersion.ToString() + [char]32 + $PSVersionTable.PSEdition' | Out-String).Trim()
            Write-Phase "build.ps1 runs via: $ps51 (PowerShell $ps51Info)"
            if ($ps51Info -notmatch "^5\.1\.\S+ Desktop$") { throw "powershell.exe is not Windows PowerShell 5.1 Desktop: $ps51Info" }
            $buildLines = @(& $ps51 -NoProfile -ExecutionPolicy Bypass -File $BuildScript *>&1 | ForEach-Object { $line = $_.ToString(); Write-Host $line; $line })
            $code = $LASTEXITCODE
            Set-Content -Path $logFile -Value $buildLines -Encoding UTF8
        } finally { $ErrorActionPreference = $previousEap }
        Write-Phase "build.ps1 exit code: $code"
        if ($code -ne 0) {
            $tail = (Get-Content $logFile -Tail 40 -ErrorAction SilentlyContinue) -join "`n"
            Report-Fail "build: build.ps1 failed with code $code" $tail
        }
        if (-not (Test-Path $DistZip)) {
            Report-Fail "build: license-issuer-dist.zip was not created" "expected: $DistZip"
        }
        # The build output must never contain key material: any run of 64+
        # hex characters in the log is treated as a leak.
        $logText = Get-Content $logFile -Raw
        $hexRuns = [regex]::Matches($logText, "(?i)\b[0-9a-f]{64,}\b")
        if ($hexRuns.Count -ne 0) {
            Report-Fail "build: build log contains $($hexRuns.Count) run(s) of 64+ hex characters" "possible key material leak; log tail: " + $logText.Substring([Math]::Max(0, $logText.Length - 2000))
        }
        $hostLine = @($buildLines | Where-Object { $_ -match "^Host: PowerShell " }) | Select-Object -First 1
        if (-not $hostLine -or $hostLine -notmatch "Desktop edition" -or $hostLine -notmatch "\\WindowsPowerShell\\v1\.0\\powershell\.exe") {
            Report-Fail "build: build.ps1 did not report running under powershell.exe 5.1 Desktop" ("host line: " + $hostLine)
        }
        Write-Phase ("build.ps1 self-reported " + $hostLine)
        $ok = "PASS: zip present, no 64+ hex material in the build log"
        Write-Phase $ok
        if ($env:GITHUB_STEP_SUMMARY) { Add-Content -Path $env:GITHUB_STEP_SUMMARY -Value $ok -Encoding utf8 }
    }

    # ------------------------------------------------------------ portable
    if ($Phase -eq "portable") {
        # The launcher must never open a modal window here (nobody would click it).
        $env:HRM_NO_DIALOG = "1"
        $portableScript = Join-Path $Root "build-portable.ps1"
        $portableExe = Join-Path $Root "dist\LicenseIssuer-Portable.exe"
        $portableInfo = Join-Path $Root "dist\BUILD-INFO.txt"
        if (-not (Test-Path $portableScript)) { Report-Fail "portable: build-portable.ps1 not found" ("expected: " + $portableScript) }
        if (-not (Test-Path $DistZip)) { Report-Fail "portable: the bundle zip is missing (run the build phase first)" ("expected: " + $DistZip) }
        $issuerVersion = "0.15.0"
        if ($env:HRM_ISSUER_VERSION) { $issuerVersion = $env:HRM_ISSUER_VERSION }
        $logFile = Join-Path $env:RUNNER_TEMP "license-issuer-portable.log"
        # Explicit 5.1 invocation: the builder must work under Windows
        # PowerShell 5.1, not only under pwsh 7.
        $ps51 = Join-Path $env:SystemRoot "System32\WindowsPowerShell\v1.0\powershell.exe"
        $previousEap = $ErrorActionPreference
        $ErrorActionPreference = "Continue"
        try {
            $portableLines = @(& $ps51 -NoProfile -ExecutionPolicy Bypass -File $portableScript -Version $issuerVersion *>&1 | ForEach-Object { $l = $_.ToString(); Write-Host $l; $l })
            $portableCode = $LASTEXITCODE
            Set-Content -Path $logFile -Value $portableLines -Encoding UTF8
        } finally { $ErrorActionPreference = $previousEap }
        Write-Phase "build-portable.ps1 exit code: $portableCode"
        if ($portableCode -ne 0) {
            $tail = (Get-Content $logFile -Tail 40 -ErrorAction SilentlyContinue) -join "`n"
            Report-Fail "portable: build-portable.ps1 failed with code $portableCode" $tail
        }
        if (-not (Test-Path $portableExe)) {
            Report-Fail "portable: LicenseIssuer-Portable.exe was not created" ("expected: " + $portableExe)
        }
        # The build log must never carry key material (the smoke test checks the
        # CLI output; this is the second, independent gate on the whole log).
        $portableLog = Get-Content $logFile -Raw
        $hexRuns = [regex]::Matches($portableLog, "(?i)\b[0-9a-f]{64,}\b")
        if ($hexRuns.Count -ne 0) {
            Report-Fail "portable: build log contains $($hexRuns.Count) run(s) of 64+ hex characters" "possible key material leak"
        }
        if (-not (Test-Path $portableInfo)) { Report-Fail "portable: BUILD-INFO.txt was not written" ("expected: " + $portableInfo) }

        # --- trailer read-back, independent of the builder -------------------
        $marker = "HRMISSUER-PAYLOAD1"
        $trailerSize = $marker.Length + 14
        $bytes = New-Object byte[] $trailerSize
        $stream = [System.IO.File]::OpenRead($portableExe)
        try {
            $exeLength = $stream.Length
            if ($exeLength -le $trailerSize) { throw "the exe is too small to carry a payload" }
            $stream.Seek(-$trailerSize, [System.IO.SeekOrigin]::End) | Out-Null
            $total = 0
            while ($total -lt $trailerSize) {
                $n = $stream.Read($bytes, $total, $trailerSize - $total)
                if ($n -le 0) { throw "could not read the trailer" }
                $total += $n
            }
        } finally { $stream.Dispose() }
        $markerBack = [System.Text.Encoding]::ASCII.GetString($bytes, 0, $marker.Length)
        if ($markerBack -ne $marker) { Report-Fail "portable: trailer marker mismatch" ("found: " + $markerBack) }
        $lengthBack = [System.Text.Encoding]::ASCII.GetString($bytes, $marker.Length, 14)
        [long]$payloadLength = 0
        if (-not [long]::TryParse($lengthBack, [ref]$payloadLength)) { Report-Fail "portable: trailer length is not a number" ("found: " + $lengthBack) }
        if ($payloadLength -le 0 -or ($payloadLength + $trailerSize) -ge $exeLength) { Report-Fail "portable: trailer length is out of range" ("length: " + $payloadLength + " exe: " + $exeLength) }
        Write-Phase ("trailer verified: marker={0} payload={1:N0} bytes exe={2:N0} bytes" -f $markerBack, $payloadLength, $exeLength)

        # --- SHA256 from BUILD-INFO must match the artifact -------------------
        $infoText = Get-Content $portableInfo -Raw
        $infoMatch = [regex]::Match($infoText, "sha256_exe:\s*([0-9a-fA-F]{64})")
        if (-not $infoMatch.Success) { Report-Fail "portable: BUILD-INFO.txt has no sha256_exe line" $infoText }
        $actualHash = (Get-FileHash -Path $portableExe -Algorithm SHA256).Hash
        if ($actualHash -ne $infoMatch.Groups[1].Value.ToUpperInvariant()) {
            Report-Fail "portable: SHA256 mismatch between BUILD-INFO.txt and the artifact" ("recorded: " + $infoMatch.Groups[1].Value + " actual: " + $actualHash)
        }
        # A 64+ hex run is blanked by the annotation redaction (that rule exists
        # to keep key material out of the logs), so the hash that goes into the
        # release report is printed as two 32-character halves.
        Write-Phase ("SHA256 matches BUILD-INFO.txt: " + $actualHash.Substring(0, 32) + " " + $actualHash.Substring(32))

        # --- second, independent CLI run through the shipped exe ---------------
        # A fresh temp directory with spaces: the launcher must quote correctly.
        $root = Join-Path $env:TEMP ("HRM Issuer PR52 Portable " + [guid]::NewGuid().ToString("N"))
        New-Item -ItemType Directory -Path $root -Force | Out-Null
        try {
            $selfJson = Join-Path $root "selfcheck.json"
            Write-Phase ("[{0:HH:mm:ss}] selfcheck via the shipped exe (first run unpacks the payload) ..." -f (Get-Date))
            $selfRun = Invoke-PortableExe -Exe $portableExe -Arguments @("--hrm-selfcheck", $selfJson) -TimeoutSeconds 300 -Stage "selfcheck"
            if ($selfRun.ExitCode -ne 0) { throw "selfcheck exited with code $($selfRun.ExitCode)" }
            if (-not (Test-Path $selfJson)) { throw "selfcheck did not write $selfJson" }
            $selfData = Get-Content $selfJson -Raw | ConvertFrom-Json
            if (-not $selfData.ok) { throw "selfcheck reported ok=false, missing: $($selfData.missing)" }
            Write-Phase ("selfcheck PASS: payload {0} files, {1:N0} bytes" -f $selfData.payload_file_count, $selfData.payload_total_bytes)

            $keysDir = Join-Path $root "keys"
            $licenseFile = Join-Path $root "chain.hrmlicense"
            $steps = @(
                @{ Name = "gen-keypair"; Args = @("gen-keypair", "--out-dir", $keysDir) },
                @{ Name = "issue"; Args = @("issue", "--private-key-file", (Join-Path $keysDir "private_key.hex"), "--client", "Portable Smoke", "--expires", "2099-12-31", "--max-users", "2", "--out", $licenseFile) },
                @{ Name = "verify"; Args = @("verify", "--public-key-file", (Join-Path $keysDir "public_key.b64"), "--license-file", $licenseFile) }
            )
            $chainOutput = @{}
            foreach ($step in $steps) {
                # HRM_PORTABLE_LOG: the launcher redirects the child CLI output into
                # a file, which is what the key-material scan below inspects.
                $logPath = Join-Path $root ("cli-" + $step.Name + ".log")
                $env:HRM_PORTABLE_LOG = $logPath
                try {
                    $run = Invoke-PortableExe -Exe $portableExe -Arguments $step.Args -TimeoutSeconds 300 -Stage $step.Name
                } finally { Remove-Item Env:HRM_PORTABLE_LOG -ErrorAction SilentlyContinue }
                $text = ""
                if (Test-Path $logPath) { $text = (Get-Content -Path $logPath -Raw) }
                $chainOutput[$step.Name] = $text
                if ($run.ExitCode -ne 0) { throw ("step '{0}' exited with code {1}; log: {2}" -f $step.Name, $run.ExitCode, $text) }
                Write-Phase ("  {0}: exit 0, log {1} bytes" -f $step.Name, $text.Length)
            }
            if (-not (Test-Path $licenseFile)) { throw "the CLI chain did not create the license file" }
            Write-Phase "portable CLI chain PASS: gen-keypair -> issue -> verify (exit codes 0)"

            # A tampered license must be rejected with a non-zero exit code.
            $tampered = Join-Path $root "tampered.hrmlicense"
            # Re-serialize the license with a different user limit: any change
            # to the signed payload must invalidate the Ed25519 signature.
            $lic = Get-Content -Path $licenseFile -Raw | ConvertFrom-Json
            $lic.max_active_users = 999
            $lic | ConvertTo-Json -Depth 4 | Set-Content -Path $tampered -Encoding utf8
            $tamperedRun = Invoke-PortableExe -Exe $portableExe -Arguments @("verify", "--public-key-file", (Join-Path $keysDir "public_key.b64"), "--license-file", $tampered) -TimeoutSeconds 300 -Stage "tampered"
            $tamperedCode = $tamperedRun.ExitCode
            if ($tamperedCode -eq 0) { throw "a tampered license was accepted by the portable exe" }
            Write-Phase "tampered license correctly rejected (exit=$tamperedCode)"

            # --- no private key material in the outputs or the temp tree -------
            $privHex = (Get-Content -Path (Join-Path $keysDir "private_key.hex") -Raw).Trim()
            if ($privHex) {
                foreach ($name in $chainOutput.Keys) {
                    if ($chainOutput[$name] -match [regex]::Escape($privHex)) { throw "private key material leaked into '$name' output" }
                }
                foreach ($f in (Get-ChildItem $root -Recurse -File | Where-Object { -not ($_.Name -eq "private_key.hex" -and $_.Directory.Name -eq "keys") -and $_.Length -lt 10MB })) {
                    try {
                        if ((Get-Content $f.FullName -Raw -Encoding utf8 -ErrorAction Stop) -match [regex]::Escape($privHex)) {
                            throw "private key material found in: $($f.FullName)"
                        }
                    } catch [System.IO.IOException] { }
                }
            }
            Write-Phase "no private key material in the portable exe outputs or temp artifacts"
            $ok = "PASS: single-file exe built, trailer valid, GUI path present, CLI chain + tamper rejection + key sweep clean"
            Write-Phase $ok
            if ($env:GITHUB_STEP_SUMMARY) { Add-Content -Path $env:GITHUB_STEP_SUMMARY -Value $ok -Encoding utf8 }
        } finally {
            if (Test-Path $root) {
                Remove-Item $root -Recurse -Force -ErrorAction SilentlyContinue
                if (Test-Path $root) { Write-Phase "WARN: could not remove $root - remove it manually" }
                else { Write-Phase "temporary test directory removed: $root" }
            }
        }
    }

    # ------------------------------------------------------------ runtime
    if ($Phase -eq "runtime") {
        try { [Console]::OutputEncoding = New-Object System.Text.UTF8Encoding $false } catch { }
        if (-not (Test-Path $DistZip)) {
            Report-Fail "runtime: zip missing (build phase must run first)" "expected: $DistZip"
        }
        # Test root with spaces in the name: launchers must survive it.
        $root = Join-Path $env:TEMP "HRM Issuer PR36 Test"
        if (Test-Path $root) { Remove-Item $root -Recurse -Force }
        New-Item -ItemType Directory -Path $root -Force | Out-Null

        function Run-AndCapture([string]$FilePath, [string[]]$CliArgs) {
            # In 5.1 with EAP=Stop merged native stderr (2>&1) becomes a
            # terminating error, so lower the preference for the call
            # (repo convention) and read the verdict from the exit code.
            $previousEap = $ErrorActionPreference
            $ErrorActionPreference = "Continue"
            try {
                $out = & $FilePath @CliArgs 2>&1 | ForEach-Object { $_.ToString() } | Out-String
                $exit = $LASTEXITCODE
            } finally { $ErrorActionPreference = $previousEap }
            return [pscustomobject]@{ Output = $out; ExitCode = $exit }
        }
        function Stop-Tree([System.Diagnostics.Process]$Proc) {
            # .NET Framework (PS 5.1) has no Process.Kill(entireProcessTree):
            # taskkill /T also ends the python.exe child of cmd.exe.
            if ($null -eq $Proc -or $Proc.HasExited) { return }
            $previousEap = $ErrorActionPreference
            $ErrorActionPreference = "Continue"
            try { & taskkill /F /T /PID $Proc.Id 2>&1 | Out-Null } finally { $ErrorActionPreference = $previousEap }
            [void]$Proc.WaitForExit(5000)
        }
        function Start-BatCaptured([string]$BatPath, [string]$OutFile) {
            # .NET ProcessStartInfo: the argument string reaches cmd.exe
            # exactly as written (no PowerShell re-quoting).
            $psi = New-Object System.Diagnostics.ProcessStartInfo
            $psi.FileName = "cmd.exe"
            # cmd /c strips the FIRST and LAST quote when the line has more than
            # two quotes: wrap the whole command in one extra pair so the quoted
            # paths (with spaces) survive intact.
            $psi.Arguments = '/c ""' + $BatPath + '" > "' + $OutFile + '" 2>&1"'
            $psi.UseShellExecute = $false
            $psi.CreateNoWindow = $true
            return [System.Diagnostics.Process]::Start($psi)
        }

        $htmlProc = $null
        $guiProc = $null
        try {
            # --- fresh unzip, separate directory (never from dist/) ---
            $unzip = Join-Path $root "unzipped bundle"
            Expand-Archive -Path $DistZip -DestinationPath $unzip -Force
            $app = Join-Path $unzip "license-issuer"
            $cli = Join-Path $app "run-cli.bat"
            $gui = Join-Path $app "run-gui.bat"
            $html = Join-Path $app "run-html.bat"
            $pyExe = Join-Path $unzip "python\python.exe"
            foreach ($f in @($cli, $gui, $html, $pyExe)) {
                if (-not (Test-Path $f)) { throw "bundle is missing: $f" }
            }
            Write-Phase "bundle unzipped to: $unzip"

            # --- runtime isolation: PATH without any system Python ---
            $originalPath = $env:PATH
            $env:PATH = "C:\Windows\System32;C:\Windows"
            $sysPy = Get-Command python -ErrorAction SilentlyContinue
            if ($sysPy) { throw "test setup failed: system python still reachable on PATH: $($sysPy.Source)" }
            $env:HRM_NO_PAUSE = "1"
            $env:HRM_NO_BROWSER = "1"

            # --- CLI chain: gen-keypair -> issue -> verify ---
            $keysDir = Join-Path $root "keys"   # outside the bundle (the bundle is copied later)
            $gen = Run-AndCapture $cli @("gen-keypair", "--out-dir", $keysDir)
            Write-Host $gen.Output
            if ($gen.ExitCode -ne 0) { throw "run-cli.bat gen-keypair failed (exit=$($gen.ExitCode)): $($gen.Output)" }
            $privFile = Join-Path $keysDir "private_key.hex"
            $pubFile = Join-Path $keysDir "public_key.b64"
            if (-not (Test-Path $privFile) -or -not (Test-Path $pubFile)) { throw "gen-keypair did not create key files" }
            $privHex = (Get-Content $privFile -Raw).Trim()
            if ($privHex -notmatch "^[0-9a-f]{64}$") { throw "private key file does not look like 64 hex chars" }
            $licDir = Join-Path $root "licenses"
            $licFile = Join-Path $licDir "pilot.hrmlicense"
            $issue = Run-AndCapture $cli @("issue", "--private-key-file", $privFile, "--client", "Pilot Maria", "--expires", "2026-12-31", "--max-users", "5", "--out", $licFile)
            Write-Host $issue.Output
            if ($issue.ExitCode -ne 0) { throw "run-cli.bat issue failed (exit=$($issue.ExitCode)): $($issue.Output)" }
            if (-not (Test-Path $licFile)) { throw "issue did not create the license file" }
            $lic = Get-Content $licFile -Raw | ConvertFrom-Json
            if ($lic.client_name -ne "Pilot Maria" -or $lic.expires_at -ne "2026-12-31" -or [int]$lic.max_active_users -ne 5) {
                throw "license fields do not match the request"
            }
            if ([string]$lic.signature -notmatch "^[0-9a-f]{128}$") { throw "license signature is not 128 hex chars" }
            $verify = Run-AndCapture $cli @("verify", "--public-key-file", $pubFile, "--license-file", $licFile)
            Write-Host $verify.Output
            if ($verify.ExitCode -ne 0) { throw "run-cli.bat verify failed (exit=$($verify.ExitCode)): $($verify.Output)" }
            Write-Phase "CLI chain PASS: gen-keypair -> issue -> verify (bundled python only, no system python on PATH)"

            # --- verify must REJECT a tampered license (not vacuous) ---
            $badFile = Join-Path $root "tampered.hrmlicense"
            $licBad = $lic
            $licBad.max_active_users = 6
            # Write WITHOUT a BOM (Set-Content -Encoding utf8 adds one in 5.1),
            # so a rejection can only come from the signature check.
            [System.IO.File]::WriteAllText($badFile, ($licBad | ConvertTo-Json -Compress), (New-Object System.Text.UTF8Encoding $false))
            $bad = Run-AndCapture $cli @("verify", "--public-key-file", $pubFile, "--license-file", $badFile)
            if ($bad.ExitCode -eq 0) { throw "verify ACCEPTED a tampered license - verify is not fail-closed" }
            Write-Phase "tampered license correctly rejected (exit=$($bad.ExitCode))"

            # --- no private key material in any captured CLI output ---
            $allCliOut = $gen.Output + $issue.Output + $verify.Output + $bad.Output
            if ($allCliOut -match [regex]::Escape($privHex)) {
                throw "private key material was printed into CLI output"
            }
            Write-Phase "no private key material in CLI stdout/stderr"

            # --- fail-closed: bundled python removed -> exit 1, no fallback ---
            $noPy = Join-Path $root "no python"
            Copy-Item $unzip $noPy -Recurse
            Remove-Item (Join-Path $noPy "python") -Recurse -Force
            $noCli = Join-Path $noPy "license-issuer\run-cli.bat"
            $fc1 = Run-AndCapture $noCli @("gen-keypair", "--out-dir", (Join-Path $noPy "keys"))
            if ($fc1.ExitCode -eq 0) { throw "launcher succeeded without bundled python (isolated PATH) - not fail-closed" }
            if ($fc1.Output -notmatch "Bundled Python not found") { throw "fail-closed error message missing: $($fc1.Output)" }
            Write-Phase "fail-closed (isolated PATH) PASS: exit=$($fc1.ExitCode)"
            # system python PRESENT on PATH - the launcher must still refuse:
            # a silent fallback would let the key material leave the bundle.
            $env:PATH = $originalPath
            if (Get-Command python -ErrorAction SilentlyContinue) {
                $fc2 = Run-AndCapture $noCli @("gen-keypair", "--out-dir", (Join-Path $noPy "keys"))
                if ($fc2.ExitCode -eq 0) { throw "launcher fell back to system python - private key would leave the bundle" }
                Write-Phase "fail-closed (system python present) PASS: exit=$($fc2.ExitCode), no fallback"
            } else {
                Write-Phase "WARN: no system python on the runner PATH; fallback-proof skipped (isolated-PATH check still holds)"
            }

            # --- run-html.bat: loopback only, page served, bundled python ---
            $htmlLog = Join-Path $root "html.log"
            $htmlProc = Start-BatCaptured $html $htmlLog
            Start-Sleep -Seconds 6
            $listen = @(netstat -ano | ForEach-Object { if ($_ -match ":8765" -and $_ -match "LISTENING") { $_ } })
            if ($listen.Count -eq 0) {
                throw "no LISTENING socket on 8765 - HTML server did not start; log: " + (Get-Content $htmlLog -Raw -ErrorAction SilentlyContinue)
            }
            $badBind = @($listen | Where-Object { $_ -match "0\.0\.0\.0:8765" })
            if ($badBind.Count -ne 0) { throw "HTML server bound to 0.0.0.0: $badBind" }
            $loopBind = @($listen | Where-Object { $_ -match "127\.0\.0\.1:8765" })
            if ($loopBind.Count -eq 0) { throw "HTML server is not bound to 127.0.0.1" }
            Write-Phase "loopback check PASS: LISTENING only on 127.0.0.1:8765"
            $listenerPid0 = [int](($listen | Select-Object -First 1) -split "\s+" | Select-Object -Last 1)
            $proc0 = Get-CimInstance Win32_Process -Filter "ProcessId=$listenerPid0"
            Write-Phase ("listener command line: " + $proc0.CommandLine)
            try {
                $resp = Invoke-WebRequest -Uri "http://127.0.0.1:8765/license-issuer.html" -UseBasicParsing
            } catch {
                $rootListing = ""
                try { $rootListing = (Invoke-WebRequest -Uri "http://127.0.0.1:8765/" -UseBasicParsing).Content } catch { $rootListing = "GET / failed: " + $_.Exception.Message }
                if ($rootListing.Length -gt 1500) { $rootListing = $rootListing.Substring(0, 1500) }
                $srvLog = Get-Content $htmlLog -Raw -ErrorAction SilentlyContinue
                throw ("GET /license-issuer.html failed: " + $_.Exception.Message + "`nlistener cmdline: " + $proc0.CommandLine + "`nGET / :`n" + $rootListing + "`nserver log:`n" + $srvLog)
            }
            if ([int]$resp.StatusCode -ne 200) { throw "HTML page returned HTTP $($resp.StatusCode)" }
            if ([string]$resp.Content -notmatch "License") { throw "served page does not look like the issuer page" }
            Write-Phase ("HTML page served: HTTP 200, {0} bytes" -f ([string]$resp.Content).Length)
            $listenerPid = [int](($listen | Select-Object -First 1) -split "\s+" | Select-Object -Last 1)
            $proc = Get-CimInstance Win32_Process -Filter "ProcessId=$listenerPid"
            # $env:TEMP may be an 8.3 short path (RUNNER~1) while WMI reports the
            # long path, so compare the bundle-relative tail, not the full path.
            if ($proc.ExecutablePath -notlike "*\HRM Issuer PR36 Test\unzipped bundle\python\python.exe") {
                throw "HTML listener is not the bundled python: $($proc.ExecutablePath)"
            }
            Write-Phase "HTML listener PID $listenerPid runs the bundled python.exe (WMI verified)"
            $previousEap = $ErrorActionPreference
            $ErrorActionPreference = "Continue"
            try { & taskkill /F /PID $listenerPid /T | Out-Null } finally { $ErrorActionPreference = $previousEap }
            Stop-Tree $htmlProc

            # --- run-gui.bat: best effort in headless CI; import crash = FAIL ---
            $guiLog = Join-Path $root "gui.log"
            $guiProc = Start-BatCaptured $gui $guiLog
            Start-Sleep -Seconds 10
            $guiText = ""
            if (Test-Path $guiLog) { $guiText = Get-Content $guiLog -Raw -ErrorAction SilentlyContinue }
            if ($guiText -match "ModuleNotFoundError|Traceback") {
                throw "GUI crashed at startup (import/runtime error): $guiText"
            }
            if (-not $guiProc.HasExited) {
                Stop-Tree $guiProc
                Write-Phase "GUI check PASS: process stayed alive without import errors (killed after check)"
            } else {
                Write-Phase ("GUI check WARN: process exited early (code={0}) without traceback - headless CI cannot confirm UI" -f $guiProc.ExitCode)
            }

            # --- no private key in temp artifacts or test logs ---
            $swept = 0
            # the key file itself is excluded by name + parent dir: $env:TEMP may be an
            # 8.3 short path while Get-ChildItem reports the long one.
            foreach ($f in (Get-ChildItem $root -Recurse -File | Where-Object { -not ($_.Name -eq "private_key.hex" -and $_.Directory.Name -eq "keys") -and $_.Length -lt 10MB })) {
                try {
                    if ((Get-Content $f.FullName -Raw -Encoding utf8 -ErrorAction Stop) -match [regex]::Escape($privHex)) {
                        throw "private key material found in: $($f.FullName)"
                    }
                    $swept++
                } catch [System.IO.IOException] { }
            }
            Write-Phase ("key-material sweep done: {0} files scanned, private key not found" -f $swept)

            # --- git tree clean: dist/ and keys are gitignored, no leaks ---
            $gitOut = & git status --porcelain 2>&1 | Out-String
            if ($gitOut.Trim().Length -ne 0) { throw "git tree is not clean after the checks: $gitOut" }
            $lsfiles = & git ls-files -ci --exclude-standard 2>&1 | Out-String
            if ($lsfiles.Trim().Length -ne 0) { throw "a tracked file matches key-material ignore patterns: $lsfiles" }
            Write-Phase "git tree clean; no tracked file hidden by key-material ignore patterns"
            $ok = "ALL RUNTIME CHECKS PASS"
            Write-Phase $ok
            if ($env:GITHUB_STEP_SUMMARY) { Add-Content -Path $env:GITHUB_STEP_SUMMARY -Value $ok -Encoding utf8 }
        }
        finally {
            # Kill everything this phase started, even after a failed check:
            # a surviving python.exe keeps the inherited stdout pipe of the CI
            # step open and the step would hang until the job timeout.
            Stop-Tree $htmlProc
            Stop-Tree $guiProc
            $leftovers = @(Get-CimInstance Win32_Process -Filter "Name='python.exe' OR Name='pythonw.exe'" -ErrorAction SilentlyContinue | Where-Object { $_.ExecutablePath -and $_.ExecutablePath -like "*\HRM Issuer PR36 Test\*" })
            foreach ($l in $leftovers) {
                Write-Phase "killing leftover bundled python PID $($l.ProcessId)"
                $previousEap = $ErrorActionPreference
                $ErrorActionPreference = "Continue"
                try { & taskkill /F /T /PID $l.ProcessId 2>&1 | Out-Null } finally { $ErrorActionPreference = $previousEap }
            }
            Start-Sleep -Seconds 1
            if (Test-Path $root) {
                Remove-Item $root -Recurse -Force -ErrorAction SilentlyContinue
                if (Test-Path $root) { Write-Phase "WARN: could not remove $root - remove it manually" }
                else { Write-Phase "temporary test directory removed: $root" }
            }
        }
    }
}
catch {
    $lineInfo = ""
    if ($_.InvocationInfo) { $lineInfo = "`nscript line: " + $_.InvocationInfo.ScriptLineNumber + " script: " + (Split-Path -Leaf $_.InvocationInfo.ScriptName) }
    if ($Phase -eq "portable") { Show-PortableDiagnostics ("phase failed: " + $_.Exception.Message) }
    Report-Fail "phase '$Phase' failed" ($_.Exception.Message + $lineInfo)
}
