# Diagnostic repro for PR #57 (no assertions; nothing is changed outside a
# temporary directory): how does THIS PowerShell host parse the output of
# `docker compose ps -a --format json` for an empty project, and what do the
# engine functions Get-HrmComposeContainers / Get-HrmStackState return?
# Facts go to the console and to GitHub notice annotations (one set per host):
# step logs and artifacts are not readable from the review sandbox, check-run
# annotations are. ASCII only on purpose (run by Windows PowerShell 5.1 too).
param([string]$HarnessPath = $PSScriptRoot)

$ErrorActionPreference = "Continue"
$script:HostLabel = "{0} {1}" -f $PSVersionTable.PSEdition, $PSVersionTable.PSVersion
$script:Facts = New-Object System.Collections.Generic.List[string]

function Add-ReproFact {
    param([string]$Text)
    $script:Facts.Add($Text)
    Write-Host ("[{0}] {1}" -f $script:HostLabel, $Text)
}

function Format-ReproError {
    param($Record)
    if ($null -eq $Record) { return "none" }
    $ex = $null
    if ($Record -is [System.Management.Automation.ErrorRecord]) { $ex = $Record.Exception }
    elseif ($Record -is [System.Exception]) { $ex = $Record }
    if ($null -eq $ex) { return ([string]$Record -replace "[\r\n]+", " ") }
    return ("{0}: {1}" -f $ex.GetType().FullName, ($ex.Message -replace "[\r\n]+", " "))
}

function Get-ReproShape {
    param($Value)
    if ($null -eq $Value) { return "null" }
    if ($Value -is [System.Array]) {
        $inner = @($Value | ForEach-Object { if ($null -eq $_) { "null" } elseif ($_ -is [System.Array]) { "array[" + $_.Count + "]" } else { $_.GetType().Name } })
        return ("array[{0}]({1})" -f $Value.Count, ($inner -join ","))
    }
    return $Value.GetType().FullName
}

function Format-ReproText {
    param([string]$Text)
    if ($null -eq $Text) { return "<null>" }
    return ($Text.Replace("`r", "\r").Replace("`n", "\n"))
}

function Invoke-ReproReturn {
    # Same return statement as Get-HrmComposeContainers: shows the value the
    # CALLER receives after PowerShell unrolls the returned collection.
    param($Items)
    return @($Items | Where-Object { $null -ne $_ })
}

function Invoke-ReproEngineParse {
    # The parsing block of Get-HrmComposeContainers (Compose.psm1), unchanged,
    # instrumented to show which branch ran.
    param([string]$Stdout)
    $primary = $null
    $outer = $null
    $items = @()
    try {
        try { $items = @($Stdout | ConvertFrom-Json -ErrorAction Stop) }
        catch {
            $primary = $_
            foreach ($line in ($Stdout -split "`r?`n")) {
                if (-not [string]::IsNullOrWhiteSpace($line)) {
                    $items += ($line | ConvertFrom-Json -ErrorAction Stop)
                }
            }
        }
    }
    catch { $outer = $_ }
    $returnedShape = "not-reached (outer catch returns null)"
    if ($null -eq $outer) { $returnedShape = Get-ReproShape (Invoke-ReproReturn -Items $items) }
    return ("primary-throw=[{0}] outer-throw=[{1}] items={2} caller-receives={3}" -f (Format-ReproError $primary), (Format-ReproError $outer), (Get-ReproShape $items), $returnedShape)
}

# 1) The literal commands requested by the review prompt.
$Error.Clear()
$raw = $null
$rawOk = $null
$rawError = $null
try {
    $raw = '[]' | ConvertFrom-Json -ErrorAction Stop
    $rawOk = $?
}
catch { $rawOk = $false; $rawError = $_ }
Add-ReproFact ("1 ('[]' | ConvertFrom-Json -ErrorAction Stop): rc={0}; error=[{1}]; assigned={2}" -f $rawOk, (Format-ReproError $rawError), (Get-ReproShape $raw))
$count = "n/a"
$countError = $null
try { $count = @('[]' | ConvertFrom-Json -ErrorAction Stop).Count } catch { $countError = $_ }
Add-ReproFact ("2 @('[]' | ConvertFrom-Json -ErrorAction Stop).Count={0}; error=[{1}]" -f $count, (Format-ReproError $countError))
$split = @('[]' -split "`r?`n" | ForEach-Object { "line=[$_]" })
Add-ReproFact ("3 '[]' -split: {0}" -f ($split -join " "))
$lastError = $null
if ($Error.Count -gt 0) { $lastError = $Error[0] }
Add-ReproFact ("4 Error.Count after 1-3={0}; Error[0]=[{1}]" -f $Error.Count, (Format-ReproError $lastError))

# 2) The engine's parse block for each output shape Compose can print.
$ndjson = '{"Name":"db","State":"running"}' + "`n" + '{"Name":"backend","State":"exited"}'
foreach ($sample in @("[]", "", " `n", "null", "compose: unexpected output (not json)", '[{"Name":"backend","State":"running"}]', $ndjson)) {
    $line = "5 parse stdout=[" + (Format-ReproText $sample) + "]: "
    try { $line += (Invoke-ReproEngineParse -Stdout $sample) } catch { $line += "repro failed: " + (Format-ReproError $_) }
    Add-ReproFact $line
}

# 3) The real engine functions with the test harness mock (same context as
#    stack.tests.ps1 "first run: no containers").
try {
    . (Join-Path $HarnessPath "test-harness.ps1")
    Initialize-HrmTestEngine
    $world = New-HrmMockWorld
    Set-HrmPreflightOverride @{ windows = $true; powershell = $true; docker = $true; daemon = $true; compose = "v2.29.7 (mock)"; port = $true; state_dir = $true; space = $true; config = $true }
    Set-HrmDockerOverride @{ desktop = "engine_ready"; engine = $true; wsl = "ok"; virtualization = "enabled"; free_mb = 20480; port_free = $true; admin = $false }
    $install = Get-HrmTestInstallDir
    $state = Get-HrmTestStateDir
    $cases = @(
        @{ name = "mock-default-[]"; json = ""; missing = $false },
        @{ name = "explicit-[]"; json = "[]"; missing = $false },
        @{ name = "garbage"; json = "compose: unexpected output (not json)"; missing = $false },
        @{ name = "single-array"; json = '[{"Name":"backend","State":"running"}]'; missing = $false },
        @{ name = "ndjson"; json = $ndjson; missing = $false },
        @{ name = "docker-missing"; json = ""; missing = $true }
    )
    foreach ($case in $cases) {
        $world.ContainersJson = $case.json
        $world.DockerMissing = $case.missing
        $text = "6 engine " + $case.name + ": "
        try {
            $containers = Get-HrmComposeContainers -InstallDir $install -StateDir $state
            $stack = Get-HrmStackState -InstallDir $install -StateDir $state
            $text += ("Get-HrmComposeContainers={0}; Get-HrmStackState={1} total={2} running={3}" -f (Get-ReproShape $containers), $stack.state, $stack.total, $stack.running)
        }
        catch { $text += "threw " + (Format-ReproError $_) }
        Add-ReproFact $text
    }
    $world.ContainersJson = ""
    $world.DockerMissing = $false
}
catch { Add-ReproFact ("6 engine section failed: " + (Format-ReproError $_)) }

# The harness sets ErrorActionPreference=Stop; under Windows PowerShell 5.1
# that turns any stderr line of a native command (even with 2>$null) into a
# terminating error, which would hide the probe result below.
$ErrorActionPreference = "Continue"

# 4) What the real Compose CLI prints for an empty project on this runner.
$probeDir = Join-Path ([System.IO.Path]::GetTempPath()) ("hrm-compose-probe-" + [System.Guid]::NewGuid().ToString("N").Substring(0, 8))
try {
    $docker = Get-Command docker -ErrorAction SilentlyContinue
    if (-not $docker) { Add-ReproFact "7 real compose: docker CLI is not on PATH" }
    else {
        $versionLines = @(& docker compose version 2>$null)
        $versionCode = $LASTEXITCODE
        New-Item -ItemType Directory -Path $probeDir -Force | Out-Null
        $composeFile = Join-Path $probeDir "compose.yml"
        [System.IO.File]::WriteAllText($composeFile, "services:`n  probe:`n    image: hrm-probe-never-pulled:0`n")
        $project = "hrm-probe-empty-" + $PID
        $psLines = @(& docker compose -f $composeFile -p $project ps -a --format json 2>$null)
        $psCode = $LASTEXITCODE
        $psText = ($psLines -join "`n")
        Add-ReproFact ("7 real compose: version=[{0}] (exit {1}); empty project ps -a --format json: exit={2}; stdout lines={3}; chars={4}; stdout=[{5}]" -f ($versionLines -join " "), $versionCode, $psCode, $psLines.Count, $psText.Length, (Format-ReproText $psText))
        if ($psCode -eq 0) { Add-ReproFact ("8 engine parse of that real stdout: " + (Invoke-ReproEngineParse -Stdout $psText)) }
    }
}
catch { Add-ReproFact ("7 real compose probe failed: " + (Format-ReproError $_)) }
finally { if (Test-Path -LiteralPath $probeDir) { Remove-Item -LiteralPath $probeDir -Recurse -Force -ErrorAction SilentlyContinue } }

# 5) Annotations (GitHub escaping), at most 3800 characters each.
$chunks = New-Object System.Collections.ArrayList
$current = ""
foreach ($fact in $script:Facts) {
    if ($current.Length -gt 0 -and ($current.Length + $fact.Length + 4) -gt 3800) { [void]$chunks.Add($current); $current = "" }
    if ($current.Length -gt 0) { $current += " || " }
    $current += $fact
}
if ($current.Length -gt 0) { [void]$chunks.Add($current) }
for ($i = 0; $i -lt $chunks.Count; $i++) {
    $message = ([string]$chunks[$i]).Replace("%", "%25").Replace("`r", "%0D").Replace("`n", "%0A")
    Write-Host ("::notice title=compose ps empty repro {0}/{1} ({2})::{3}" -f ($i + 1), $chunks.Count, $script:HostLabel, $message)
}
exit 0
