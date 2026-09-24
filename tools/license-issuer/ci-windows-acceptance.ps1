# CI acceptance run for the offline license issuer bundle (Windows PowerShell 5.1,
# elevated, windows-latest). Stricter than ci-windows-checks.ps1 -Phase runtime:
#
#   * sandbox with spaces in the path, OUTSIDE the repository and outside %TEMP%
#     (C:\HRM Acceptance Test), fresh unzip of dist\license-issuer-dist.zip
#   * every system python.exe / py.exe found on the machine is RENAMED for the
#     duration of the run (restored in finally) and PATH is reduced to System32
#   * a clean per-process environment (own USERPROFILE/APPDATA/LOCALAPPDATA/
#     TEMP/Downloads, no PYTHON* or proxy variables) for every launched process
#   * Windows Firewall outbound BLOCK rule for the bundled python.exe (all
#     non-loopback addresses), proven effective with a probe (WSAEACCES 10013)
#     while an identical control connection from PowerShell succeeds
#   * per-PID capture: WFP audit events 5156/5157 (all TCP/UDP of the bundled
#     python.exe incl. HTTP/HTTPS/proxy connections), process creation 4688,
#     DNS-Client/Operational (name resolutions per calling PID); each monitor
#     is validated with a positive control before its "0 outbound" is trusted
#   * CLI flow through run-cli.bat, GUI flow (run-gui.bat window + real Tk
#     mainloop driven by ci-gui-driver.py with the bundled python), HTML flow
#     (run-html.bat, 20x launcher race test, listener checks, real Microsoft
#     Edge via WebDriver: WebCrypto Ed25519, license created in the UI,
#     downloaded, verified by the CLI)
#   * private-key leakage scan (hex/HEX/base64/base64url, ASCII + UTF-16LE +
#     raw 32 bytes) over the sandbox, bundle, logs, clean profile (Edge profile,
#     Downloads, Temp), the runner's real %TEMP%/%APPDATA%/%LOCALAPPDATA%/
#     Downloads, RUNNER_TEMP and the repository - before and after deleting the
#     designated key files
#   * licenses + public keys (never private keys) are handed to the next CI step
#     for verification with the backend code (ci-backend-verify.py)
#
# Result lines: "[accept] PASS|FAIL|NOT VERIFIED <id>: <detail>". Exit 1 if any
# FAIL. NOT VERIFIED = the claim could not be established in this environment
# (reported, never silently counted as PASS).
#
# ENCODING CONTRACT: ASCII-only, UTF-8 BOM (Windows PowerShell 5.1).

Set-StrictMode -Version 2.0
$ErrorActionPreference = "Stop"
[System.Net.WebRequest]::DefaultWebProxy = New-Object System.Net.WebProxy   # harness never uses a proxy
try { [Console]::OutputEncoding = New-Object System.Text.UTF8Encoding $false } catch { }

$Here = $PSScriptRoot
$RepoRoot = (Resolve-Path (Join-Path $Here "..\..")).Path
$DistZip = Join-Path $Here "dist\license-issuer-dist.zip"
$Root = Join-Path $env:SystemDrive "HRM Acceptance Test"
$Bundle = Join-Path $Root "unzipped bundle"
$App = Join-Path $Bundle "license-issuer"
$BundlePy = Join-Path $Bundle "python\python.exe"
$EnvHome = Join-Path $Root "clean profile"
$Downloads = Join-Path $EnvHome "Downloads"
$EdgeProfile = Join-Path $EnvHome "AppData\Local\EdgeTestProfile"
$Out = Join-Path $Root "owner output"
$Logs = Join-Path $Root "logs"
$Tools = Join-Path $Root "tools"
$Transcript = Join-Path $Logs "acceptance-transcript.txt"
$Handoff = Join-Path $env:RUNNER_TEMP "hrm-accept-handoff"
$Port = 8765
$PageUrl = "http://127.0.0.1:$Port/license-issuer.html"
$RuleName = "HRM acceptance - block bundled python outbound"
$BundlePyLike = "*\hrm acceptance test\unzipped bundle\python\python.exe"
# alert text of the HTML page ("signature correct" in Russian), built from code points (ASCII source)
$SigOk = -join ([char[]]@(0x41F, 0x43E, 0x434, 0x43F, 0x438, 0x441, 0x44C, 0x20, 0x43A, 0x43E, 0x440, 0x440, 0x435, 0x43A, 0x442, 0x43D, 0x430))

$script:Results = New-Object System.Collections.ArrayList
$script:Secrets = [ordered]@{}      # name -> raw 32-byte private key (never printed)
$script:Hidden = New-Object System.Collections.ArrayList
$script:Started = New-Object System.Collections.ArrayList
$script:FwBefore = $null
$script:CleanEnv = [ordered]@{}
$script:Sid = $null
$script:Wd = "http://127.0.0.1:9515"

function Say([string]$m) {
    $line = "[accept] $m"
    Write-Host $line
    if (Test-Path $Logs) { Add-Content -Path $Transcript -Value $line -Encoding UTF8 }
}
function Add-Result([string]$Id, [string]$Status, [string]$Detail) {
    [void]$script:Results.Add([pscustomobject]@{ Id = $Id; Status = $Status; Detail = $Detail })
    Say ("{0} {1}: {2}" -f $Status, $Id, $Detail)
}
function Q([string]$s) { return '"' + $s + '"' }
function Prop($Obj, [string]$Name) {
    # StrictMode-safe optional property read (JSON objects, registry values)
    if ($null -eq $Obj) { return $null }
    $pp = $Obj.PSObject.Properties[$Name]
    if ($null -eq $pp) { return $null }
    return $pp.Value
}
Add-Type -Namespace HrmAccept -Name NativePath -MemberDefinition '[DllImport("kernel32.dll", CharSet = CharSet.Unicode)] public static extern uint GetLongPathName(string shortPath, System.Text.StringBuilder longPath, uint size);'
function Get-LongPath([string]$Path) {
    # %TEMP% on the runner is an 8.3 path (RUNNER~1); expand it so nested roots are recognised
    $sb = New-Object System.Text.StringBuilder 1024
    if ([HrmAccept.NativePath]::GetLongPathName($Path, $sb, 1024) -gt 0) { return $sb.ToString() }
    return $Path
}
function Quiet([scriptblock]$Block) {
    $p = $ErrorActionPreference; $ErrorActionPreference = "Continue"
    try { & $Block } finally { $ErrorActionPreference = $p }
}
function Stop-Tree($Proc) {
    if ($null -eq $Proc) { return }
    try { if ($Proc.HasExited) { return } } catch { return }
    Quiet { & taskkill /F /T /PID $Proc.Id 2>&1 | Out-Null }
    try { [void]$Proc.WaitForExit(5000) } catch { }
}
function Stop-BundledPython {
    foreach ($l in @(Get-CimInstance Win32_Process -Filter "Name='python.exe' OR Name='pythonw.exe'" -ErrorAction SilentlyContinue | Where-Object { $_.ExecutablePath -and $_.ExecutablePath -like $BundlePyLike })) {
        Quiet { & taskkill /F /T /PID $l.ProcessId 2>&1 | Out-Null }
    }
}
function Read-Shared([string]$Path) {
    if (-not (Test-Path -LiteralPath $Path)) { return "" }
    try {
        $fs = [System.IO.File]::Open($Path, [System.IO.FileMode]::Open, [System.IO.FileAccess]::Read, [System.IO.FileShare]::ReadWrite)
        try { $sr = New-Object System.IO.StreamReader($fs, [System.Text.Encoding]::UTF8); return $sr.ReadToEnd() } finally { $fs.Dispose() }
    } catch { return "" }
}
function Hex-ToBytes([string]$Hex) {
    $b = New-Object byte[] ($Hex.Length / 2)
    for ($i = 0; $i -lt $b.Length; $i++) { $b[$i] = [Convert]::ToByte($Hex.Substring($i * 2, 2), 16) }
    return ,$b
}

# ---------------------------------------------------------------- processes
function Init-CleanEnv {
    foreach ($d in @("AppData\Roaming", "AppData\Local\Temp", "Downloads", "Desktop")) {
        New-Item -ItemType Directory -Force -Path (Join-Path $EnvHome $d) | Out-Null
    }
    $script:CleanEnv = [ordered]@{
        SystemRoot = $env:SystemRoot; windir = $env:windir; SystemDrive = $env:SystemDrive; ComSpec = $env:ComSpec
        PATHEXT = ".COM;.EXE;.BAT;.CMD"
        PATH = "$env:SystemRoot\System32;$env:SystemRoot;$env:SystemRoot\System32\Wbem"
        TEMP = (Join-Path $EnvHome "AppData\Local\Temp"); TMP = (Join-Path $EnvHome "AppData\Local\Temp")
        USERPROFILE = $EnvHome; HOMEDRIVE = $env:SystemDrive; HOMEPATH = $EnvHome.Substring(2)
        APPDATA = (Join-Path $EnvHome "AppData\Roaming"); LOCALAPPDATA = (Join-Path $EnvHome "AppData\Local")
        USERNAME = $env:USERNAME; USERDOMAIN = $env:USERDOMAIN; COMPUTERNAME = $env:COMPUTERNAME
        NUMBER_OF_PROCESSORS = $env:NUMBER_OF_PROCESSORS; PROCESSOR_ARCHITECTURE = $env:PROCESSOR_ARCHITECTURE; OS = $env:OS
        ProgramFiles = $env:ProgramFiles; "ProgramFiles(x86)" = ${env:ProgramFiles(x86)}; ProgramW6432 = $env:ProgramW6432
        ProgramData = $env:ProgramData; CommonProgramFiles = $env:CommonProgramFiles; ALLUSERSPROFILE = $env:ALLUSERSPROFILE; PUBLIC = $env:PUBLIC
        HRM_NO_PAUSE = "1"
    }
}
function New-CleanPsi([string]$File, [string]$Arguments, [hashtable]$Extra, [string]$WorkDir) {
    $psi = New-Object System.Diagnostics.ProcessStartInfo
    $psi.FileName = $File
    $psi.Arguments = $Arguments
    $psi.UseShellExecute = $false
    $psi.CreateNoWindow = $true
    if ($WorkDir) { $psi.WorkingDirectory = $WorkDir } else { $psi.WorkingDirectory = $EnvHome }
    $psi.EnvironmentVariables.Clear()
    foreach ($k in $script:CleanEnv.Keys) { $psi.EnvironmentVariables[$k] = [string]$script:CleanEnv[$k] }
    if ($Extra) { foreach ($k in $Extra.Keys) { $psi.EnvironmentVariables[$k] = [string]$Extra[$k] } }
    return $psi
}
function Start-Clean([string]$File, [string]$Arguments, [hashtable]$Extra, [string]$WorkDir) {
    $p = [System.Diagnostics.Process]::Start((New-CleanPsi $File $Arguments $Extra $WorkDir))
    [void]$script:Started.Add($p)
    return $p
}
function Invoke-Captured([string]$File, [string]$Arguments, [int]$TimeoutSec, [hashtable]$Extra, [string]$WorkDir) {
    $psi = New-CleanPsi $File $Arguments $Extra $WorkDir
    $psi.RedirectStandardOutput = $true
    $psi.RedirectStandardError = $true
    $p = [System.Diagnostics.Process]::Start($psi)
    [void]$script:Started.Add($p)
    $o = $p.StandardOutput.ReadToEndAsync(); $e = $p.StandardError.ReadToEndAsync()
    if (-not $p.WaitForExit($TimeoutSec * 1000)) { Stop-Tree $p; throw "timeout after $TimeoutSec s: $File $Arguments" }
    $p.WaitForExit()
    return [pscustomobject]@{ Pid = $p.Id; ExitCode = $p.ExitCode; StdOut = $o.Result; StdErr = $e.Result }
}
function Invoke-Bat([string]$Bat, [string]$ArgLine, [string]$OutFile, [hashtable]$Extra) {
    # cmd /c strips the first and last quote of the line: one extra outer pair.
    $cmdArgs = '/d /c ""' + $Bat + '" ' + $ArgLine + ' > "' + $OutFile + '" 2>&1"'
    $p = Start-Clean $env:ComSpec $cmdArgs $Extra $null
    if (-not $p.WaitForExit(180000)) { Stop-Tree $p; throw "timeout: $Bat $ArgLine" }
    $p.WaitForExit()
    return [pscustomobject]@{ ExitCode = $p.ExitCode; Output = (Read-Shared $OutFile) }
}
function Start-Html([string]$LogFile, [bool]$NoBrowser) {
    $extra = @{}
    if ($NoBrowser) { $extra["HRM_NO_BROWSER"] = "1" }
    $cmdArgs = '/d /c ""' + (Join-Path $App "run-html.bat") + '" > "' + $LogFile + '" 2>&1"'
    return (Start-Clean $env:ComSpec $cmdArgs $extra $null)
}
function Wait-LogLine([string]$LogFile, [string]$Pattern, [int]$TimeoutMs) {
    $sw = [System.Diagnostics.Stopwatch]::StartNew()
    while ($sw.ElapsedMilliseconds -lt $TimeoutMs) {
        foreach ($l in ((Read-Shared $LogFile) -split "`r?`n")) { if ($l -match $Pattern) { return $l } }
        Start-Sleep -Milliseconds 50
    }
    return $null
}
function Wait-PortFree([int]$TimeoutMs) {
    $sw = [System.Diagnostics.Stopwatch]::StartNew()
    while ($sw.ElapsedMilliseconds -lt $TimeoutMs) {
        if (@(Get-NetTCPConnection -State Listen -LocalPort $Port -ErrorAction SilentlyContinue).Count -eq 0) { return $true }
        Start-Sleep -Milliseconds 200
    }
    return $false
}
function Get-Http([string]$Url) {
    try {
        $r = Invoke-WebRequest -Uri $Url -UseBasicParsing -TimeoutSec 5
        return [pscustomobject]@{ Status = [int]$r.StatusCode; Body = [string]$r.Content; Error = "" }
    } catch {
        $code = 0
        try { $code = [int]$_.Exception.Response.StatusCode } catch { }
        return [pscustomobject]@{ Status = $code; Body = ""; Error = $_.Exception.GetType().Name + ": " + $_.Exception.Message }
    }
}

# ---------------------------------------------------------------- WebDriver
function Wd([string]$Method, [string]$Path, $Body) {
    $uri = $script:Wd + $Path
    try {
        if ($null -ne $Body) {
            $json = ConvertTo-Json -InputObject $Body -Depth 12 -Compress
            $r = Invoke-RestMethod -Method $Method -Uri $uri -Body ([System.Text.Encoding]::UTF8.GetBytes($json)) -ContentType "application/json; charset=utf-8" -TimeoutSec 90
        } else {
            $r = Invoke-RestMethod -Method $Method -Uri $uri -TimeoutSec 90
        }
    } catch {
        $detail = ""
        try { $detail = $_.ErrorDetails.Message } catch { }
        if ($detail.Length -gt 600) { $detail = $detail.Substring(0, 600) }
        throw ("WebDriver $Method $Path failed: " + $_.Exception.Message + " " + $detail)
    }
    return $r.value
}
function Js([string]$Script) { return (Wd "POST" "/session/$($script:Sid)/execute/sync" @{ script = $Script; args = @() }) }
function JsAsync([string]$Script) { return (Wd "POST" "/session/$($script:Sid)/execute/async" @{ script = $Script; args = @() }) }
function Find-El([string]$Using, [string]$Value) {
    $v = Wd "POST" "/session/$($script:Sid)/element" @{ using = $Using; value = $Value }
    return $v.'element-6066-11e4-a52e-4f735466cecf'
}
function Click-El([string]$Using, [string]$Value) { $id = Find-El $Using $Value; [void](Wd "POST" "/session/$($script:Sid)/element/$id/click" @{}) }
function Type-El([string]$Css, [string]$Text) {
    $id = Find-El "css selector" $Css
    [void](Wd "POST" "/session/$($script:Sid)/element/$id/clear" @{})
    [void](Wd "POST" "/session/$($script:Sid)/element/$id/value" @{ text = $Text })
}
function Get-AlertText { try { return [string](Wd "GET" "/session/$($script:Sid)/alert/text" $null) } catch { return $null } }
function Accept-Alert { try { [void](Wd "POST" "/session/$($script:Sid)/alert/accept" @{}) } catch { } }
function Wait-Js([string]$Script, [int]$TimeoutMs, [string]$What) {
    $sw = [System.Diagnostics.Stopwatch]::StartNew()
    while ($sw.ElapsedMilliseconds -lt $TimeoutMs) {
        $a = Get-AlertText
        if ($null -ne $a) { Accept-Alert; throw "unexpected alert while waiting for ${What}: $a" }
        if ((Js $Script) -eq $true) { return }
        Start-Sleep -Milliseconds 200
    }
    throw "timeout waiting for $What"
}
function Wait-Download([string]$Name, [int]$TimeoutMs) {
    $f = Join-Path $Downloads $Name
    $sw = [System.Diagnostics.Stopwatch]::StartNew()
    while ($sw.ElapsedMilliseconds -lt $TimeoutMs) {
        $partial = @(Get-ChildItem -LiteralPath $Downloads -Filter "*.crdownload" -ErrorAction SilentlyContinue)
        if ((Test-Path -LiteralPath $f) -and $partial.Count -eq 0 -and (Get-Item -LiteralPath $f).Length -gt 0) { return $f }
        Start-Sleep -Milliseconds 200
    }
    $have = (@(Get-ChildItem -LiteralPath $Downloads -ErrorAction SilentlyContinue | ForEach-Object { $_.Name }) -join ", ")
    throw "download $Name did not arrive in $Downloads (present: $have)"
}

# ---------------------------------------------------------------- monitors
function Get-WfpBundled([datetime]$Since) {
    $list = New-Object System.Collections.ArrayList
    foreach ($e in @(Get-WinEvent -FilterHashtable @{ LogName = "Security"; Id = @(5156, 5157); StartTime = $Since } -ErrorAction SilentlyContinue)) {
        $app = [string]$e.Properties[1].Value
        if ($app -notlike $BundlePyLike) { continue }
        [void]$list.Add([pscustomobject]@{
            Id = $e.Id; ProcessId = [int]$e.Properties[0].Value; Direction = [string]$e.Properties[2].Value
            Source = [string]$e.Properties[3].Value; Dest = [string]$e.Properties[5].Value; DestPort = [string]$e.Properties[6].Value
            Protocol = [string]$e.Properties[7].Value })
    }
    return ,$list
}
function Test-Loopback([string]$a) { return ($a -like "127.*" -or $a -eq "::1" -or $a -eq "0:0:0:0:0:0:0:1") }
function Get-BundledPids([datetime]$Since) {
    $pids = New-Object System.Collections.ArrayList
    foreach ($e in @(Get-WinEvent -FilterHashtable @{ LogName = "Security"; Id = 4688; StartTime = $Since } -ErrorAction SilentlyContinue)) {
        $x = [xml]$e.ToXml(); $d = @{}
        foreach ($n in $x.Event.EventData.Data) { $d[[string]$n.Name] = [string]$n.'#text' }
        if ($d["NewProcessName"] -like $BundlePyLike) { [void]$pids.Add([Convert]::ToInt32($d["NewProcessId"].Substring(2), 16)) }
    }
    return ,$pids
}
function Get-DnsEvents([datetime]$Since, $Pids) {
    return @(Get-WinEvent -FilterHashtable @{ LogName = "Microsoft-Windows-DNS-Client/Operational"; StartTime = $Since } -ErrorAction SilentlyContinue | Where-Object { $Pids -contains $_.ProcessId })
}

# ---------------------------------------------------------------- leak scan
function Invoke-LeakScan([string]$Stage, [string[]]$Allowed) {
    $latin1 = [System.Text.Encoding]::GetEncoding(28591)
    $needles = New-Object System.Collections.ArrayList
    foreach ($name in $script:Secrets.Keys) {
        $raw = [byte[]]$script:Secrets[$name]
        $hex = -join ($raw | ForEach-Object { $_.ToString("x2") })
        $b64 = [Convert]::ToBase64String($raw)
        $b64u = $b64.TrimEnd("=").Replace("+", "-").Replace("/", "_")
        $forms = [ordered]@{ "hex" = $hex; "HEX" = $hex.ToUpperInvariant(); "base64" = $b64; "base64url" = $b64u }
        foreach ($f in $forms.Keys) {
            [void]$needles.Add(@{ Key = $name; Form = "$f/ascii"; Text = $latin1.GetString([System.Text.Encoding]::ASCII.GetBytes($forms[$f])) })
            [void]$needles.Add(@{ Key = $name; Form = "$f/utf16le"; Text = $latin1.GetString([System.Text.Encoding]::Unicode.GetBytes($forms[$f])) })
        }
        [void]$needles.Add(@{ Key = $name; Form = "raw32"; Text = $latin1.GetString($raw) })
    }
    $roots = @($Root, $env:LOCALAPPDATA, $env:APPDATA, $env:TEMP, (Join-Path $env:USERPROFILE "Downloads"), $env:RUNNER_TEMP, $RepoRoot) |
        Where-Object { $_ -and (Test-Path -LiteralPath $_) } | ForEach-Object { (Get-LongPath (Get-Item -LiteralPath $_).FullName).TrimEnd("\") } | Sort-Object -Unique
    $roots = @($roots | Where-Object { $r = $_; -not @($roots | Where-Object { $_ -ne $r -and $r.StartsWith($_ + "\", [StringComparison]::OrdinalIgnoreCase) }).Count })
    $hits = New-Object System.Collections.ArrayList
    $nameHits = New-Object System.Collections.ArrayList
    $otherNames = New-Object System.Collections.ArrayList
    $total = 0; $totalSkipped = 0; $totalUnreadable = 0; $bytes = [long]0
    $when = (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ")
    foreach ($r in $roots) {
        $n = 0; $skipped = 0; $unreadable = 0
        foreach ($f in @(Get-ChildItem -LiteralPath $r -Recurse -File -Force -ErrorAction SilentlyContinue)) {
            $isAllowed = $Allowed -contains $f.FullName
            if ($f.Name -match '(?i)^private_key|\.hex$') { if (-not $isAllowed) { [void]$nameHits.Add($f.FullName) } }
            elseif ($f.Name -match '(?i)(private|secret|id_ed25519|\.pem$|\.key$)') { [void]$otherNames.Add($f.FullName) }
            if ($f.Length -gt 50MB) { $skipped++; continue }
            try {
                $fs = [System.IO.File]::Open($f.FullName, [System.IO.FileMode]::Open, [System.IO.FileAccess]::Read, [System.IO.FileShare]::ReadWrite -bor [System.IO.FileShare]::Delete)
                try { $buf = New-Object byte[] $fs.Length; $read = 0; while ($read -lt $buf.Length) { $k = $fs.Read($buf, $read, $buf.Length - $read); if ($k -le 0) { break }; $read += $k } } finally { $fs.Dispose() }
            } catch { $unreadable++; continue }
            $n++; $bytes += $buf.Length
            if ($isAllowed) { continue }
            $t = $latin1.GetString($buf)
            foreach ($nd in $needles) { if ($t.IndexOf($nd.Text, [StringComparison]::Ordinal) -ge 0) { [void]$hits.Add("$($f.FullName) [$($nd.Key) $($nd.Form)]") } }
        }
        Say ("leak scan '{0}' root {1}: {2} files scanned, {3} >50MB skipped, {4} unreadable" -f $Stage, $r, $n, $skipped, $unreadable)
        $total += $n; $totalSkipped += $skipped; $totalUnreadable += $unreadable
    }
    $detail = ("at {0}: {1} files / {2:N1} MB scanned in {3} roots, {4} secrets x {5} encodings; {6} >50MB skipped, {7} unreadable; allowed (designated key files): {8}" -f $when, $total, ($bytes / 1MB), $roots.Count, $script:Secrets.Count, ($needles.Count / [Math]::Max(1, $script:Secrets.Count)), $totalSkipped, $totalUnreadable, $Allowed.Count)
    if ($otherNames.Count -gt 0) { Say ("leak scan '{0}': {1} other key-like file names (review list, not key material): {2}" -f $Stage, $otherNames.Count, ((@($otherNames) | Select-Object -First 15) -join "; ")) }
    if ($hits.Count -eq 0 -and $nameHits.Count -eq 0) {
        Add-Result "leak-scan ($Stage)" "PASS" ("0 key-material hits, 0 private_key*/.hex files outside the designated ones; " + $detail)
    } else {
        Add-Result "leak-scan ($Stage)" "FAIL" ("hits: " + ((@($hits) + @($nameHits) | Select-Object -First 20) -join "; ") + "; " + $detail)
    }
}

# ================================================================= main
$exitCode = 0
$htmlProc = $null; $drvProc = $null
try {
    if (Test-Path $Root) { Remove-Item $Root -Recurse -Force }
    foreach ($d in @($Root, $Out, $Logs, $Tools)) { New-Item -ItemType Directory -Force -Path $d | Out-Null }
    Init-CleanEnv
    $isAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
    $os = Get-CimInstance Win32_OperatingSystem
    Say ("shell: PowerShell {0} ({1}), {2}; admin={3}; {4} build {5}" -f $PSVersionTable.PSVersion, $PSVersionTable.PSEdition, (Get-Process -Id $PID).Path, $isAdmin, $os.Caption, $os.BuildNumber)
    if (-not $isAdmin) { throw "acceptance run needs an elevated shell (firewall rule, audit policy)" }
    if (-not (Test-Path $DistZip)) { throw "dist zip missing: $DistZip (build phase must run first)" }

    # ---------------------------------------------------------- 1. fresh bundle
    Expand-Archive -Path $DistZip -DestinationPath $Bundle -Force
    $bundleFiles = @(Get-ChildItem -LiteralPath $Bundle -Recurse -File -Force)
    $bad = @($bundleFiles | Where-Object { $_.Name -match '(?i)^private_key|^public_key|\.hrmlicense$' })
    if ($bad.Count -ne 0) { Add-Result "bundle-contents" "FAIL" ("key/license files inside the shipped zip: " + (($bad | ForEach-Object { $_.Name }) -join ", ")) }
    else { Add-Result "bundle-contents" "PASS" ("fresh unzip to '$Bundle': {0} files, no private_key*/public_key*/.hrmlicense inside" -f $bundleFiles.Count) }
    Copy-Item (Join-Path $Here "ci-gui-driver.py") $Tools
    Set-Content -Path (Join-Path $Tools "net_probe.py") -Encoding ASCII -Value @(
        "import json, socket, sys, urllib.request",
        "r = {'proxies_seen_by_python': urllib.request.getproxies()}",
        "try:",
        "    s = socket.create_connection((sys.argv[1], 443), 5); s.close(); r['tcp'] = 'CONNECTED'",
        "except OSError as e:",
        "    r['tcp'] = 'BLOCKED winerror=%s' % getattr(e, 'winerror', None)",
        "try:",
        "    r['dns'] = 'RESOLVED %d' % len(socket.getaddrinfo(sys.argv[2], 443))",
        "except OSError as e:",
        "    r['dns'] = 'FAILED %s' % type(e).__name__",
        "print(json.dumps(r))")

    # ---------------------------------------------------------- 2. hide system python
    $cands = New-Object System.Collections.ArrayList
    foreach ($c in @(Get-Command python, python3, py, pythonw, pyw -All -CommandType Application -ErrorAction SilentlyContinue)) { [void]$cands.Add($c.Source) }
    foreach ($dir in @("C:\hostedtoolcache\windows\Python", "$env:ProgramFiles\Python*", "${env:ProgramFiles(x86)}\Python*", "C:\Python*", "$env:LOCALAPPDATA\Programs\Python")) {
        foreach ($f in @(Get-ChildItem -Path $dir -Recurse -Include "python.exe", "pythonw.exe", "python3.exe", "py.exe", "pyw.exe" -File -Force -ErrorAction SilentlyContinue)) { [void]$cands.Add($f.FullName) }
    }
    foreach ($f in @("$env:SystemRoot\py.exe", "$env:SystemRoot\pyw.exe")) { if (Test-Path $f) { [void]$cands.Add($f) } }
    foreach ($f in @(Get-ChildItem "$env:LOCALAPPDATA\Microsoft\WindowsApps" -Filter "py*.exe" -Force -ErrorAction SilentlyContinue)) { [void]$cands.Add($f.FullName) }
    $notHidden = @()
    foreach ($c in @($cands | Sort-Object -Unique)) {
        if ($c -like "$Root*") { continue }
        try { Rename-Item -LiteralPath $c -NewName ((Split-Path $c -Leaf) + ".hrm-hidden") -Force; [void]$script:Hidden.Add($c) } catch { $notHidden += $c }
    }
    $still = @(Get-Command python, python3, py, pythonw, pyw -All -CommandType Application -ErrorAction SilentlyContinue | Where-Object { Test-Path $_.Source })
    $pep514 = @(Get-ChildItem "HKLM:\SOFTWARE\Python\PythonCore", "HKCU:\SOFTWARE\Python\PythonCore" -ErrorAction SilentlyContinue).Count
    if ($still.Count -eq 0 -and $notHidden.Count -eq 0) {
        Add-Result "system-python-hidden" "PASS" ("{0} system python/py executables renamed (toolcache, C:\Windows\py.exe, WindowsApps aliases); with the runner's FULL PATH none of python/python3/py/pythonw/pyw resolves; clean PATH for launched processes = System32 only; PEP 514 registry entries present: {1} (irrelevant: py.exe hidden)" -f $script:Hidden.Count, $pep514)
    } else {
        Add-Result "system-python-hidden" "FAIL" ("still resolvable: " + (($still | ForEach-Object { $_.Source }) -join ", ") + "; could not rename: " + ($notHidden -join ", "))
    }
    Add-Result "clean-environment" "PASS" ("every launched process gets a cleared environment: USERPROFILE/APPDATA/LOCALAPPDATA/TEMP/Downloads under '$EnvHome', PATH=System32;Windows;Wbem, no PYTHON*/proxy variables; cmd /d (no AutoRun). Limitation: same Windows account (known-folder APIs can bypass env vars) - the real profile dirs are therefore included in the leak scan")

    # ---------------------------------------------------------- 3. network isolation + monitors
    Quiet { & wevtutil sl Security /ms:1073741824 2>&1 | Out-Null }
    Quiet { & wevtutil sl "Microsoft-Windows-DNS-Client/Operational" /e:true 2>&1 | Out-Null }
    foreach ($g in @("{0CCE9226-69AE-11D9-BED3-505054503030}", "{0CCE922B-69AE-11D9-BED3-505054503030}")) {
        $o = Quiet { & auditpol /set "/subcategory:$g" /success:enable /failure:enable 2>&1 | Out-String }
        if ($LASTEXITCODE -ne 0) { throw "auditpol $g failed: $o" }
    }
    $script:FwBefore = @(Get-NetFirewallProfile | Select-Object Name, Enabled, DefaultOutboundAction)
    Set-NetFirewallProfile -Profile Domain, Private, Public -Enabled True -DefaultOutboundAction Allow
    New-NetFirewallRule -DisplayName $RuleName -Direction Outbound -Action Block -Program $BundlePy -Profile Any `
        -RemoteAddress @("0.0.0.0-126.255.255.255", "128.0.0.0-255.255.255.255", "::2-ffff:ffff:ffff:ffff:ffff:ffff:ffff:ffff") | Out-Null
    $rule = Get-NetFirewallRule -DisplayName $RuleName
    $ruleApp = ($rule | Get-NetFirewallApplicationFilter).Program
    $ruleAddr = (($rule | Get-NetFirewallAddressFilter).RemoteAddress) -join ","
    $profiles = (@(Get-NetFirewallProfile | ForEach-Object { "$($_.Name)=$($_.Enabled)" }) -join " ")
    Say ("firewall rule: enabled={0} action={1} direction={2} program={3} remote={4}; profiles {5}" -f $rule.Enabled, $rule.Action, $rule.Direction, $ruleApp, $ruleAddr, $profiles)

    $ghIp = @([System.Net.Dns]::GetHostAddresses("github.com") | Where-Object { $_.AddressFamily -eq "InterNetwork" })[0].IPAddressToString
    $tProbe = Get-Date
    Start-Sleep -Milliseconds 1100
    $probe = Invoke-Captured $BundlePy ((Q (Join-Path $Tools "net_probe.py")) + " $ghIp example.org") 60 $null $Tools
    $probeRes = $probe.StdOut.Trim()
    $ctl = New-Object System.Net.Sockets.TcpClient
    $ctlOk = $false
    try { $ctlOk = $ctl.ConnectAsync($ghIp, 443).Wait(5000) -and $ctl.Connected } catch { $ctlOk = $false } finally { $ctl.Dispose() }
    if ($probeRes -match "BLOCKED winerror=10013" -and $ctlOk) {
        Add-Result "firewall-rule-effective" "PASS" ("bundled python -> ${ghIp}:443 refused by the firewall (WSAEACCES 10013) while the same connection from PowerShell succeeded; probe output: $probeRes")
    } else {
        Add-Result "firewall-rule-effective" "FAIL" ("probe: '$probeRes' (stderr: $($probe.StdErr.Trim())); control connection from PowerShell ok=$ctlOk")
    }
    Start-Sleep -Seconds 3
    $wfpProbe = @((Get-WfpBundled $tProbe) | Where-Object { $_.ProcessId -eq $probe.Pid -and $_.Id -eq 5157 -and $_.Dest -eq $ghIp })
    if ($wfpProbe.Count -gt 0) { Add-Result "monitor-wfp-positive-control" "PASS" ("WFP audit saw the probe's blocked connection (event 5157, PID $($probe.Pid) -> ${ghIp}:$($wfpProbe[0].DestPort)) - the per-PID connection capture works") }
    else { Add-Result "monitor-wfp-positive-control" "FAIL" "WFP audit did not record the probe connection - the outbound count below cannot be trusted" }
    $dnsProbe = @(Get-DnsEvents $tProbe @($probe.Pid))
    if ($dnsProbe.Count -gt 0) { Add-Result "monitor-dns-positive-control" "PASS" ("DNS-Client/Operational attributed {0} event(s) of the probe's getaddrinfo(example.org) to PID {1} - per-PID DNS capture works (note: the firewall rule does not block name resolution, which runs in the DNS Client service; it is monitored instead)" -f $dnsProbe.Count, $probe.Pid) }
    else { Add-Result "monitor-dns-positive-control" "NOT VERIFIED" "no DNS-Client event carried the probe PID - per-PID DNS attribution could not be established on this runner" }
    $ie = Get-ItemProperty "HKCU:\Software\Microsoft\Windows\CurrentVersion\Internet Settings" -ErrorAction SilentlyContinue
    $winhttp = ((Quiet { & netsh winhttp show proxy 2>&1 | Out-String }) -replace "\s+", " ").Trim()
    $envProxy = (@("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY") | ForEach-Object { "$_=" + [Environment]::GetEnvironmentVariable($_) }) -join " "
    Say ("proxy settings: WinINet ProxyEnable={0} ProxyServer='{1}' AutoConfigURL='{2}'; WinHTTP: {3}; runner env: {4}; launched processes get no proxy variables; python in the clean env sees: {5}" -f (Prop $ie "ProxyEnable"), (Prop $ie "ProxyServer"), (Prop $ie "AutoConfigURL"), $winhttp, $envProxy, $probeRes)
    $tFlows = Get-Date
    Start-Sleep -Milliseconds 1100

    # ---------------------------------------------------------- 4. CLI flow via run-cli.bat
    $cliBat = Join-Path $App "run-cli.bat"
    $keysCli = Join-Path $Out "keys-cli"
    $licDir = Join-Path $Out "licenses"
    New-Item -ItemType Directory -Force -Path $licDir | Out-Null
    $licCli = Join-Path $licDir "cli-pilot.hrmlicense"
    $codes = [ordered]@{}
    $r = Invoke-Bat $cliBat ("gen-keypair --out-dir " + (Q $keysCli)) (Join-Path $Logs "cli-1-gen.txt") $null; $codes["gen-keypair"] = $r.ExitCode
    $r2 = Invoke-Bat $cliBat ("gen-keypair --out-dir " + (Q $keysCli)) (Join-Path $Logs "cli-2-gen-again.txt") $null; $codes["gen-keypair again (no --force)"] = $r2.ExitCode
    $r = Invoke-Bat $cliBat ("issue --private-key-file " + (Q (Join-Path $keysCli "private_key.hex")) + ' --client "Pilot Maria CLI" --expires 2026-12-31 --max-users 5 --out ' + (Q $licCli)) (Join-Path $Logs "cli-3-issue.txt") $null; $codes["issue"] = $r.ExitCode
    $r = Invoke-Bat $cliBat ("verify --public-key-file " + (Q (Join-Path $keysCli "public_key.b64")) + " --license-file " + (Q $licCli)) (Join-Path $Logs "cli-4-verify.txt") $null; $codes["verify"] = $r.ExitCode
    $cliPrivHex = ""; $cliPub = ""
    if (Test-Path (Join-Path $keysCli "private_key.hex")) {
        $cliPrivHex = (Get-Content (Join-Path $keysCli "private_key.hex") -Raw).Trim()
        $cliPub = (Get-Content (Join-Path $keysCli "public_key.b64") -Raw).Trim()
        $script:Secrets["cli"] = Hex-ToBytes $cliPrivHex
    }
    $tampered = Join-Path $licDir "cli-tampered.hrmlicense"
    if (Test-Path $licCli) {
        $j = Get-Content $licCli -Raw -Encoding UTF8 | ConvertFrom-Json; $j.max_active_users = 6
        [System.IO.File]::WriteAllText($tampered, ($j | ConvertTo-Json -Compress), (New-Object System.Text.UTF8Encoding $false))
    }
    $r = Invoke-Bat $cliBat ("verify --public-key-file " + (Q (Join-Path $keysCli "public_key.b64")) + " --license-file " + (Q $tampered)) (Join-Path $Logs "cli-5-verify-tampered.txt") $null; $codes["verify tampered"] = $r.ExitCode
    $r = Invoke-Bat $cliBat "issue" (Join-Path $Logs "cli-6-issue-noargs.txt") $null; $codes["issue without arguments"] = $r.ExitCode
    $codeTxt = (@($codes.Keys | ForEach-Object { "$_=$($codes[$_])" }) -join ", ")
    $expect = @{ "gen-keypair" = 0; "gen-keypair again (no --force)" = 1; "issue" = 0; "verify" = 0; "verify tampered" = 2; "issue without arguments" = 2 }
    $wrong = @($expect.Keys | Where-Object { $codes[$_] -ne $expect[$_] })
    if ($wrong.Count -eq 0 -and $cliPrivHex -match "^[0-9a-f]{64}$") { Add-Result "cli-flow" "PASS" ("run-cli.bat from '$App' (spaces), bundled python, clean env; exit codes: $codeTxt") }
    else { Add-Result "cli-flow" "FAIL" ("unexpected exit codes for: " + ($wrong -join ", ") + "; got: $codeTxt; last output: " + $r.Output) }

    # ---------------------------------------------------------- 5. GUI flow
    $guiLog = Join-Path $Logs "gui-launcher.txt"
    $guiProc = Start-Clean $env:ComSpec ('/d /c ""' + (Join-Path $App "run-gui.bat") + '" > "' + $guiLog + '" 2>&1"') $null $null
    $win = $null
    $sw = [System.Diagnostics.Stopwatch]::StartNew()
    while ($sw.ElapsedMilliseconds -lt 30000 -and -not $win) {
        $win = @(Get-Process -Name python -ErrorAction SilentlyContinue | Where-Object { $_.Path -and $_.Path -like $BundlePyLike -and $_.MainWindowTitle -like "*License Issuer*" }) | Select-Object -First 1
        if (-not $win) { Start-Sleep -Milliseconds 300 }
    }
    if ($win) { Add-Result "gui-launcher-window" "PASS" ("run-gui.bat -> bundled python.exe PID $($win.Id) shows a top-level window titled '*License Issuer*' after {0} ms" -f $sw.ElapsedMilliseconds) }
    else { Add-Result "gui-launcher-window" "FAIL" ("no window of the bundled python within 30 s; launcher log: " + (Read-Shared $guiLog)) }
    Stop-Tree $guiProc; Stop-BundledPython

    $guiRes = Join-Path $Logs "gui-driver-result.json"
    $g = Invoke-Captured $BundlePy ((Q (Join-Path $Tools "ci-gui-driver.py")) + " " + (Q $App) + " " + (Q $Out) + " " + (Q $guiRes)) 150 $null $App
    Set-Content -Path (Join-Path $Logs "gui-driver-output.txt") -Value ($g.StdOut + "`n" + $g.StdErr) -Encoding UTF8
    if (Test-Path $guiRes) {
        $gj = Get-Content $guiRes -Raw | ConvertFrom-Json
        foreach ($s in @($gj.steps)) { if ($s.ok) { Add-Result "gui: $($s.step)" "PASS" $s.detail } else { Add-Result "gui: $($s.step)" "FAIL" $s.detail } }
    } else { Add-Result "gui-driver" "FAIL" ("no result file; exit=$($g.ExitCode) stderr: " + $g.StdErr) }
    $keysGui = Join-Path $Out "keys-gui"
    if (Test-Path (Join-Path $keysGui "private_key.hex")) { $script:Secrets["gui"] = Hex-ToBytes ((Get-Content (Join-Path $keysGui "private_key.hex") -Raw).Trim()) }
    $licGui = Join-Path $licDir "gui-pilot.hrmlicense"
    $r = Invoke-Bat $cliBat ("verify --public-key-file " + (Q (Join-Path $keysGui "public_key.b64")) + " --license-file " + (Q $licGui)) (Join-Path $Logs "cli-7-verify-gui-license.txt") $null
    if ($r.ExitCode -eq 0) { Add-Result "gui-license-cross-check" "PASS" "license issued in the GUI verified by run-cli.bat with the GUI public key (exit 0)" }
    else { Add-Result "gui-license-cross-check" "FAIL" ("exit=$($r.ExitCode): " + $r.Output) }

    # ---------------------------------------------------------- 6. HTML: launcher race x20
    $race = New-Object System.Collections.ArrayList
    for ($i = 1; $i -le 20; $i++) {
        $log = Join-Path $Logs ("html-race-{0:D2}.txt" -f $i)
        $sw = [System.Diagnostics.Stopwatch]::StartNew()
        $p = Start-Html $log $true
        $line = Wait-LogLine $log '^\[(ready|browser)\]' 40000
        $ms = $sw.ElapsedMilliseconds
        $first = $null
        if ($line -and $line -like "[ready]*") { $first = Get-Http $PageUrl }
        $att = 0; $gateMs = 0; $before = ""
        if ($line -match "after (\d+) attempt\(s\), (\d+) ms \(last before ready: ([^)]*)\)") { $att = [int]$matches[1]; $gateMs = [int]$matches[2]; $before = $matches[3] }
        $st = "none"; if ($first) { if ($first.Status -eq 200 -and $first.Body -match "License Issuer") { $st = "200" } else { $st = "$($first.Status) $($first.Error)" } }
        [void]$race.Add([pscustomobject]@{ Run = $i; Line = [string]$line; FirstAfterReady = $st; Attempts = $att; GateMs = $gateMs; LaunchToReadyMs = $ms; Before = $before })
        Stop-Tree $p; Stop-BundledPython
        if (-not (Wait-PortFree 15000)) { throw "port $Port still listening after run $i" }
    }
    $ok200 = @($race | Where-Object { $_.FirstAfterReady -eq "200" }).Count
    $refusedFirst = @($race | Where-Object { $_.Attempts -gt 1 }).Count
    $slow = @($race | Where-Object { $_.LaunchToReadyMs -gt 2000 }).Count
    $sorted = @($race | Sort-Object LaunchToReadyMs)
    Say ("race runs: " + ((@($race | ForEach-Object { "#{0}:{1}/att{2}/{3}ms" -f $_.Run, $_.FirstAfterReady, $_.Attempts, $_.LaunchToReadyMs })) -join " "))
    $raceDetail = ("{0}/20 first browser-equivalent request after the [ready] signal = HTTP 200 with the issuer page; the readiness gate itself saw the server not yet listening on its first poll in {1}/20 runs (errors before ready: {2}); launch->ready min/median/max = {3}/{4}/{5} ms; {6}/20 runs took longer than the old fixed ~2 s browser delay (the old launcher would have opened the browser too early in those runs)" -f $ok200, $refusedFirst, ((@($race | ForEach-Object { $_.Before }) | Sort-Object -Unique) -join ","), $sorted[0].LaunchToReadyMs, $sorted[9].LaunchToReadyMs, $sorted[19].LaunchToReadyMs, $slow)
    if ($ok200 -eq 20) { Add-Result "html-launcher-race-x20" "PASS" $raceDetail } else { Add-Result "html-launcher-race-x20" "FAIL" $raceDetail }

    # ---------------------------------------------------------- 7. HTML: default-browser branch (best effort)
    $tB = Get-Date
    $logB = Join-Path $Logs "html-default-browser.txt"
    $p = Start-Html $logB $false
    $lineB = Wait-LogLine $logB '^\[(ready|browser)\]' 40000
    $seen = Wait-LogLine $logB 'GET /nacl-fast\.js HTTP/1\.[01]" 200' 30000
    if ($seen) { Add-Result "html-default-browser-open" "PASS" "run-html.bat without HRM_NO_BROWSER: readiness gate opened the default browser, which fetched license-issuer.html + nacl-fast.js (HTTP 200 in the server log) - no refused first load" }
    else { Add-Result "html-default-browser-open" "NOT VERIFIED" ("the runner's default-browser association did not fetch the page within 30 s (gate line: $lineB) - checked with WebDriver below instead") }
    Stop-Tree $p; Stop-BundledPython
    foreach ($e in @(Get-CimInstance Win32_Process -Filter "Name='msedge.exe'" -ErrorAction SilentlyContinue | Where-Object { $_.CreationDate -gt $tB })) { Quiet { & taskkill /F /T /PID $e.ProcessId 2>&1 | Out-Null } }
    [void](Wait-PortFree 15000)

    # ---------------------------------------------------------- 8. HTML: server + listener checks
    $htmlLog = Join-Path $Logs "html-main.txt"
    $htmlProc = Start-Html $htmlLog $true
    $ready = Wait-LogLine $htmlLog '^\[ready\]' 40000
    if (-not $ready) { throw "run-html.bat did not become ready: " + (Read-Shared $htmlLog) }
    $lst = @(Get-NetTCPConnection -State Listen -LocalPort $Port -ErrorAction SilentlyContinue)
    if ($lst.Count -eq 0) { throw "run-html.bat reported ready but nothing listens on port $Port" }
    $addrs = @($lst | ForEach-Object { $_.LocalAddress } | Sort-Object -Unique)
    $srvPid = [int]$lst[0].OwningProcess
    $srv = Get-CimInstance Win32_Process -Filter "ProcessId=$srvPid"
    $allListen = @(Get-NetTCPConnection -State Listen -OwningProcess $srvPid -ErrorAction SilentlyContinue | ForEach-Object { "$($_.LocalAddress):$($_.LocalPort)" })
    $udp = @(Get-NetUDPEndpoint -OwningProcess $srvPid -ErrorAction SilentlyContinue)
    $extIp = @(Get-NetIPAddress -AddressFamily IPv4 -ErrorAction SilentlyContinue | Where-Object { $_.IPAddress -notlike "127.*" -and $_.IPAddress -notlike "169.254.*" } | ForEach-Object { $_.IPAddress }) | Select-Object -First 1
    $extConnect = "not attempted (no non-loopback IPv4)"
    if ($extIp) {
        $tc = New-Object System.Net.Sockets.TcpClient
        try { if ($tc.ConnectAsync($extIp, $Port).Wait(3000) -and $tc.Connected) { $extConnect = "CONNECTED" } else { $extConnect = "no connection within 3 s" } }
        catch { $ie2 = $_.Exception; while ($ie2.InnerException) { $ie2 = $ie2.InnerException }; $extConnect = "refused (" + $ie2.GetType().Name + ")" }
        finally { $tc.Dispose() }
    }
    $resp = Get-Http $PageUrl
    $okListen = ($addrs.Count -eq 1 -and $addrs[0] -eq "127.0.0.1" -and $allListen.Count -eq 1 -and $udp.Count -eq 0 -and $extConnect -ne "CONNECTED" -and $srv.ExecutablePath -like $BundlePyLike -and $resp.Status -eq 200)
    $detail = ("HTTP {0} ({1} bytes); LISTEN on port {2}: {3}; no 0.0.0.0/:: listener; all TCP listeners of PID {4}: {5}; UDP endpoints: {6}; connect to {7}:{2} -> {8}; listener exe: {9}" -f $resp.Status, $resp.Body.Length, $Port, ($addrs -join ","), $srvPid, ($allListen -join ","), $udp.Count, $extIp, $extConnect, $srv.ExecutablePath)
    if ($okListen) { Add-Result "html-server-loopback" "PASS" $detail } else { Add-Result "html-server-loopback" "FAIL" $detail }

    # ---------------------------------------------------------- 9. HTML: real Edge via WebDriver
    try {
        $edgeExe = @("${env:ProgramFiles(x86)}\Microsoft\Edge\Application\msedge.exe", "$env:ProgramFiles\Microsoft\Edge\Application\msedge.exe") | Where-Object { Test-Path $_ } | Select-Object -First 1
        if (-not $edgeExe) { throw "Microsoft Edge not installed on the runner" }
        $edgeVer = (Get-Item $edgeExe).VersionInfo.ProductVersion
        $drv = $null
        if ($env:EDGEWEBDRIVER -and (Test-Path (Join-Path $env:EDGEWEBDRIVER "msedgedriver.exe"))) { $drv = Join-Path $env:EDGEWEBDRIVER "msedgedriver.exe" }
        $drvVer = ""; if ($drv) { $drvVer = ((Quiet { & $drv --version 2>&1 | Out-String }) -replace "\s+", " ").Trim() }
        if (-not $drv -or $drvVer -notmatch (" " + $edgeVer.Split(".")[0] + "\.")) {
            $zip = Join-Path $Tools "edgedriver.zip"
            [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
            Invoke-WebRequest -Uri "https://msedgedriver.microsoft.com/$edgeVer/edgedriver_win64.zip" -OutFile $zip -UseBasicParsing
            Expand-Archive $zip (Join-Path $Tools "edgedriver") -Force
            $drv = Join-Path $Tools "edgedriver\msedgedriver.exe"
            $drvVer = ((Quiet { & $drv --version 2>&1 | Out-String }) -replace "\s+", " ").Trim()
        }
        Say "Edge $edgeVer, $drvVer"
        $drvProc = Start-Clean $env:ComSpec ('/d /c ""' + $drv + '" --port=9515 > "' + (Join-Path $Logs "msedgedriver.txt") + '" 2>&1"') $null $null
        $sw = [System.Diagnostics.Stopwatch]::StartNew()
        while ($sw.ElapsedMilliseconds -lt 20000) { try { if ((Invoke-RestMethod "$($script:Wd)/status" -TimeoutSec 2).value.ready) { break } } catch { }; Start-Sleep -Milliseconds 300 }
        $mode = "headed"
        foreach ($attempt in @("headed", "headless")) {
            $edgeArgs = @("--no-first-run", "--no-default-browser-check", "--disable-sync", "--user-data-dir=$EdgeProfile", "--window-size=1280,1000")
            if ($attempt -eq "headless") { $edgeArgs += "--headless=new" }
            $caps = @{ capabilities = @{ alwaysMatch = @{ browserName = "MicrosoftEdge"; "ms:edgeOptions" = @{ binary = $edgeExe; args = $edgeArgs
                prefs = @{ "download.default_directory" = $Downloads; "download.prompt_for_download" = $false; "download.directory_upgrade" = $true } } } } }
            try { $sess = Wd "POST" "/session" $caps; $script:Sid = $sess.sessionId; $mode = $attempt; break } catch { Say "Edge session ($attempt) failed: $($_.Exception.Message)" }
        }
        if (-not $script:Sid) { throw "could not start an Edge WebDriver session" }
        [void](Wd "POST" "/session/$($script:Sid)/timeouts" @{ script = 30000; pageLoad = 30000; implicit = 0 })
        [void](Wd "POST" "/session/$($script:Sid)/url" @{ url = $PageUrl })
        Wait-Js 'var s=document.getElementById("status"); return s.className==="ok" && s.textContent.indexOf("WebCrypto Ed25519")===0 && s.textContent.indexOf("\u041d\u0415 ")<0;' 20000 "page self-check 'WebCrypto Ed25519 supported'"
        $wc = JsAsync 'var cb=arguments[arguments.length-1]; (async function(){ try { var kp=await crypto.subtle.generateKey({name:"Ed25519"},true,["sign","verify"]); var m=new TextEncoder().encode("probe"); var sig=await crypto.subtle.sign({name:"Ed25519"},kp.privateKey,m); var ok=await crypto.subtle.verify({name:"Ed25519"},kp.publicKey,sig,m); cb({secure:window.isSecureContext, ok:ok, siglen:sig.byteLength, ua:navigator.userAgent}); } catch(e) { cb({secure:window.isSecureContext, ok:false, err:String(e)}); } })();'
        if ((Prop $wc "secure") -eq $true -and (Prop $wc "ok") -eq $true -and [int](Prop $wc "siglen") -eq 64) { Add-Result "edge-webcrypto-ed25519" "PASS" ("Edge $edgeVer ($mode): isSecureContext=true on $PageUrl, crypto.subtle Ed25519 generateKey/sign/verify ok (64-byte signature); page self-check reports WebCrypto supported") }
        else { Add-Result "edge-webcrypto-ed25519" "FAIL" ("secure=" + (Prop $wc "secure") + " ok=" + (Prop $wc "ok") + " err=" + (Prop $wc "err")) }
        $naclType = Js 'window.nacl = undefined; return typeof window.nacl;'
        # (a) keys generated in the UI -> issue -> verify in page -> download -> CLI verify
        Click-El "xpath" '//button[contains(@onclick,"genKeypair")]'
        Wait-Js 'return document.getElementById("privHex").value.length===64 && document.getElementById("status").textContent.indexOf("via WebCrypto")>=0;' 15000 "UI key generation via WebCrypto"
        $kv = Js 'return {priv: document.getElementById("privHex").value, pub: document.getElementById("pubB64").value};'
        $script:Secrets["html-ui"] = Hex-ToBytes ([string]$kv.priv)
        $keysHtml = Join-Path $Out "keys-html"
        New-Item -ItemType Directory -Force -Path $keysHtml | Out-Null
        [System.IO.File]::WriteAllText((Join-Path $keysHtml "public_key.b64"), ([string]$kv.pub + "`n"), (New-Object System.Text.UTF8Encoding $false))
        Type-El "#clientName" "Pilot Maria HTML"
        Type-El "#expiresAt" "2026-12-31"
        Type-El "#maxUsers" "5"
        Click-El "xpath" '//button[contains(@onclick,"issueLicense")]'
        Wait-Js 'var t=document.getElementById("result").textContent; return t.indexOf("\"client_name\": \"Pilot Maria HTML\"")>=0;' 15000 "license issued in the UI (nacl disabled -> WebCrypto signing)"
        Click-El "xpath" '//button[contains(@onclick,"verifyCurrent")]'
        Start-Sleep -Milliseconds 500
        $alertTxt = Get-AlertText; Accept-Alert
        Click-El "xpath" '//button[contains(@onclick,"downloadResult")]'
        $dl1 = Wait-Download "Pilot_Maria_HTML_2026-12-31.hrmlicense" 20000
        $licHtml = Join-Path $licDir "html-ui.hrmlicense"; Copy-Item -LiteralPath $dl1 $licHtml
        $r = Invoke-Bat $cliBat ("verify --public-key-file " + (Q (Join-Path $keysHtml "public_key.b64")) + " --license-file " + (Q $licHtml)) (Join-Path $Logs "cli-8-verify-html-ui.txt") $null
        if ($alertTxt -eq $SigOk -and $r.ExitCode -eq 0 -and $naclType -eq "undefined") { Add-Result "html-ui-flow (UI keys)" "PASS" ("Edge UI: generate keys (WebCrypto) -> issue (TweetNaCl disabled, so WebCrypto signed) -> in-page verify alert 'signature correct' -> download '$([IO.Path]::GetFileName($dl1))' into the browser download dir -> run-cli.bat verify exit 0") }
        else { Add-Result "html-ui-flow (UI keys)" "FAIL" ("alert ok=$($alertTxt -eq $SigOk) nacl=$naclType cli verify exit=$($r.ExitCode): $($r.Output)") }
        # (b) owner pastes the CLI private key -> issue -> download -> CLI verify with the CLI public key
        Type-El "#privHex" $cliPrivHex
        Type-El "#pubB64" $cliPub
        Type-El "#clientName" "Pilot Maria HTML CLIKEY"
        Click-El "xpath" '//button[contains(@onclick,"issueLicense")]'
        Wait-Js 'var t=document.getElementById("result").textContent; return t.indexOf("Pilot Maria HTML CLIKEY")>=0;' 15000 "license issued with the pasted CLI key"
        Click-El "xpath" '//button[contains(@onclick,"downloadResult")]'
        $dl2 = Wait-Download "Pilot_Maria_HTML_CLIKEY_2026-12-31.hrmlicense" 20000
        $licHtml2 = Join-Path $licDir "html-clikey.hrmlicense"; Copy-Item -LiteralPath $dl2 $licHtml2
        $r = Invoke-Bat $cliBat ("verify --public-key-file " + (Q (Join-Path $keysCli "public_key.b64")) + " --license-file " + (Q $licHtml2)) (Join-Path $Logs "cli-9-verify-html-clikey.txt") $null
        $j2 = Get-Content $licHtml2 -Raw -Encoding UTF8 | ConvertFrom-Json; $j2.max_active_users = 50
        $tamp2 = Join-Path $licDir "html-tampered.hrmlicense"
        [System.IO.File]::WriteAllText($tamp2, ($j2 | ConvertTo-Json -Compress), (New-Object System.Text.UTF8Encoding $false))
        $r3 = Invoke-Bat $cliBat ("verify --public-key-file " + (Q (Join-Path $keysCli "public_key.b64")) + " --license-file " + (Q $tamp2)) (Join-Path $Logs "cli-10-verify-html-tampered.txt") $null
        if ($r.ExitCode -eq 0 -and $r3.ExitCode -eq 2) { Add-Result "html-ui-flow (pasted CLI key)" "PASS" "Edge UI signed with the pasted CLI private key -> downloaded -> run-cli.bat verify with the CLI public key exit 0; tampered copy exit 2" }
        else { Add-Result "html-ui-flow (pasted CLI key)" "FAIL" ("verify exit=$($r.ExitCode), tampered exit=$($r3.ExitCode): $($r.Output)") }
        $res = @(Js 'return performance.getEntriesByType("resource").map(function(e){return e.name;}).concat([location.href]);')
        $foreign = @($res | Where-Object { -not ([string]$_).StartsWith("http://127.0.0.1:$Port/") })
        if ($foreign.Count -eq 0) { Add-Result "html-page-resources" "PASS" ("all {0} URLs loaded by the page are on http://127.0.0.1:{1}/ ({2})" -f $res.Count, $Port, ($res -join ", ")) }
        else { Add-Result "html-page-resources" "FAIL" ("page loaded non-loopback URLs: " + ($foreign -join ", ")) }
    } catch {
        Add-Result "edge-webdriver" "FAIL" $_.Exception.Message
    } finally {
        if ($script:Sid) { try { [void](Wd "DELETE" "/session/$($script:Sid)" $null) } catch { } }
        Stop-Tree $drvProc
        foreach ($e in @(Get-CimInstance Win32_Process -Filter "Name='msedge.exe' OR Name='msedgedriver.exe'" -ErrorAction SilentlyContinue | Where-Object { $_.CommandLine -and $_.CommandLine -like "*EdgeTestProfile*" })) { Quiet { & taskkill /F /T /PID $e.ProcessId 2>&1 | Out-Null } }
    }
    Stop-Tree $htmlProc; Stop-BundledPython
    Start-Sleep -Seconds 3

    # ---------------------------------------------------------- 10. network verdict (flows window)
    $wfp = Get-WfpBundled $tFlows
    $outNonLoop = @($wfp | Where-Object { $_.Direction -eq "%%14593" -and -not (Test-Loopback $_.Dest) })
    $inNonLoop = @($wfp | Where-Object { $_.Direction -eq "%%14592" -and -not (Test-Loopback $_.Source) -and -not (Test-Loopback $_.Dest) })
    $loop = @($wfp | Where-Object { (Test-Loopback $_.Dest) -or (Test-Loopback $_.Source) })
    $pids = Get-BundledPids $tFlows
    $dns = @(Get-DnsEvents $tFlows $pids)
    $secLog = Get-WinEvent -ListLog Security
    $oldest = (Get-WinEvent -LogName Security -MaxEvents 1 -Oldest).TimeCreated
    $netDetail = ("window {0:HH:mm:ss}-{1:HH:mm:ss} UTC{2}: {3} bundled python.exe processes (4688); WFP events of bundled python: {4} total, {5} loopback, {6} outbound to non-loopback (TCP/UDP incl. HTTP 80/HTTPS 443/any proxy), {7} inbound from non-loopback; DNS-Client events with a bundled PID: {8}; Security log {9:N0} MB of {10:N0} MB, oldest event {11:HH:mm:ss} (retention covers the window)" -f $tFlows, (Get-Date), ([TimeZoneInfo]::Local.BaseUtcOffset.TotalHours.ToString("+0;-0")), $pids.Count, $wfp.Count, $loop.Count, $outNonLoop.Count, $inNonLoop.Count, $dns.Count, ($secLog.FileSize / 1MB), ($secLog.MaximumSizeInBytes / 1MB), $oldest)
    if ($outNonLoop.Count -eq 0 -and $inNonLoop.Count -eq 0 -and $dns.Count -eq 0 -and $loop.Count -gt 0 -and $pids.Count -gt 0 -and $oldest -le $tProbe) {
        Add-Result "network-zero-outbound" "PASS" $netDetail
    } else {
        $sample = (@($outNonLoop | Select-Object -First 5 | ForEach-Object { "PID $($_.ProcessId) -> $($_.Dest):$($_.DestPort)/$($_.Protocol) ev$($_.Id)" }) -join "; ")
        Add-Result "network-zero-outbound" "FAIL" ($netDetail + "; sample: " + $sample)
    }

    # ---------------------------------------------------------- 11. private-key leakage
    $allowed = @((Join-Path $keysCli "private_key.hex"), (Join-Path $keysGui "private_key.hex")) | Where-Object { Test-Path $_ }
    $allOut = (@(Get-ChildItem $Logs -File | ForEach-Object { Read-Shared $_.FullName }) -join "`n")
    $inOut = @($script:Secrets.Keys | Where-Object { $h = -join (([byte[]]$script:Secrets[$_]) | ForEach-Object { $_.ToString("x2") }); $allOut.IndexOf($h, [StringComparison]::OrdinalIgnoreCase) -ge 0 })
    if ($inOut.Count -eq 0) { Add-Result "stdout-stderr-no-key" "PASS" ("{0} captured stdout/stderr logs (CLI, GUI launcher + driver, HTML server x22, msedgedriver, this transcript = the CI step output) contain none of the {1} private keys" -f @(Get-ChildItem $Logs -File).Count, $script:Secrets.Count) }
    else { Add-Result "stdout-stderr-no-key" "FAIL" ("private key(s) printed: " + ($inOut -join ", ")) }
    Invoke-LeakScan "after all flows, processes stopped, designated key files present" $allowed
    foreach ($a in $allowed) { Remove-Item -LiteralPath $a -Force }
    Invoke-LeakScan "after deleting the designated key files" @()

    # ---------------------------------------------------------- 12. handoff to the backend step (no private keys)
    if (Test-Path $Handoff) { Remove-Item $Handoff -Recurse -Force }
    New-Item -ItemType Directory -Force -Path $Handoff | Out-Null
    $manifest = New-Object System.Collections.ArrayList
    $pairs = @(
        @{ origin = "cli (run-cli.bat)"; lic = $licCli; pub = (Join-Path $keysCli "public_key.b64"); expect = "accept" },
        @{ origin = "cli tampered"; lic = $tampered; pub = (Join-Path $keysCli "public_key.b64"); expect = "bad_signature" },
        @{ origin = "gui (Tk)"; lic = $licGui; pub = (Join-Path $keysGui "public_key.b64"); expect = "accept" },
        @{ origin = "html (Edge, UI keys)"; lic = (Join-Path $licDir "html-ui.hrmlicense"); pub = (Join-Path $Out "keys-html\public_key.b64"); expect = "accept" },
        @{ origin = "html (Edge, pasted CLI key)"; lic = (Join-Path $licDir "html-clikey.hrmlicense"); pub = (Join-Path $keysCli "public_key.b64"); expect = "accept" },
        @{ origin = "html tampered"; lic = (Join-Path $licDir "html-tampered.hrmlicense"); pub = (Join-Path $keysCli "public_key.b64"); expect = "bad_signature" },
        @{ origin = "gui license with the CLI key (wrong key)"; lic = $licGui; pub = (Join-Path $keysCli "public_key.b64"); expect = "bad_signature" })
    $n = 0
    foreach ($pp in $pairs) {
        if (-not (Test-Path $pp.lic) -or -not (Test-Path $pp.pub)) { Say "handoff: missing $($pp.origin) (license or public key) - backend step will report it"; continue }
        $n++
        $ln = "lic$n.hrmlicense"; $pn = "pub$n.b64"
        Copy-Item -LiteralPath $pp.lic (Join-Path $Handoff $ln); Copy-Item -LiteralPath $pp.pub (Join-Path $Handoff $pn)
        [void]$manifest.Add([ordered]@{ origin = $pp.origin; license = $ln; public_key = $pn; expect = $pp.expect })
    }
    [System.IO.File]::WriteAllText((Join-Path $Handoff "manifest.json"), (ConvertTo-Json -InputObject @($manifest) -Depth 4), (New-Object System.Text.UTF8Encoding $false))
    if ($n -eq $pairs.Count) { Add-Result "backend-handoff" "PASS" ("{0} license/public-key pairs handed to the backend step (no private keys)" -f $n) }
    else { Add-Result "backend-handoff" "FAIL" ("only {0}/{1} pairs available" -f $n, $pairs.Count) }
}
catch {
    $li = ""; if ($_.InvocationInfo) { $li = " (line " + $_.InvocationInfo.ScriptLineNumber + ")" }
    Add-Result "acceptance-run" "FAIL" ($_.Exception.Message + $li)
}
finally {
    foreach ($p in $script:Started) { Stop-Tree $p }
    Stop-BundledPython
    try { Remove-NetFirewallRule -DisplayName $RuleName -ErrorAction SilentlyContinue } catch { }
    if ($script:FwBefore) { foreach ($f in $script:FwBefore) { try { Set-NetFirewallProfile -Name $f.Name -Enabled $f.Enabled -DefaultOutboundAction $f.DefaultOutboundAction } catch { } } }
    $restored = 0
    foreach ($c in $script:Hidden) { try { Rename-Item -LiteralPath ($c + ".hrm-hidden") -NewName (Split-Path $c -Leaf) -Force; $restored++ } catch { Say "WARN: could not restore $c" } }
    Say ("cleanup: firewall rule removed, profiles restored, {0}/{1} system python executables restored" -f $restored, $script:Hidden.Count)
    Start-Sleep -Seconds 1
    if (Test-Path $Root) { Remove-Item $Root -Recurse -Force -ErrorAction SilentlyContinue }
    if (Test-Path $Root) { Say "WARN: could not remove $Root" } else { Say "sandbox removed: $Root" }
    $fails = @($script:Results | Where-Object { $_.Status -eq "FAIL" })
    $nv = @($script:Results | Where-Object { $_.Status -eq "NOT VERIFIED" })
    Say ("SUMMARY: {0} checks, {1} PASS, {2} FAIL, {3} NOT VERIFIED" -f $script:Results.Count, @($script:Results | Where-Object { $_.Status -eq "PASS" }).Count, $fails.Count, $nv.Count)
    if ($fails.Count -gt 0) { $exitCode = 1 }
}
exit $exitCode
