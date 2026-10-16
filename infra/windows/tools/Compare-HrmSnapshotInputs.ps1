# Read-only metadata comparison. No Docker, secrets or state mutation.
# Output is PRIVATE: never commit/upload the generated report.
#requires -Version 5.1
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$InstallDir,
    [Parameter(Mandatory = $true)][string]$StateDir,
    [Parameter(Mandatory = $true)][string]$PackageReleaseFile,
    [Parameter(Mandatory = $true)][string]$PrivateReportFile
)
Set-StrictMode -Version 2.0
$ErrorActionPreference = "Stop"

function Read-HrmComparisonJson {
    param([string]$File)
    try { return ([System.IO.File]::ReadAllText($File, [System.Text.Encoding]::UTF8) | ConvertFrom-Json) }
    catch { throw "Required comparison metadata is missing, unreadable or invalid. No source contents printed." }
}
function Read-HrmComparisonField {
    param($Object, [string]$Field)
    $property = $Object.PSObject.Properties[$Field]
    if ($null -eq $property) { return $null }
    return $property.Value
}

$reportDir = Split-Path ([System.IO.Path]::GetFullPath($PrivateReportFile)) -Parent
if (-not (Test-Path -LiteralPath $reportDir -PathType Container)) {
    throw "Create an ACL-restricted private report directory first; this script will not create it."
}
if (Test-Path -LiteralPath $PrivateReportFile) { throw "Refusing to overwrite an existing report or state file." }
$reportFull = [System.IO.Path]::GetFullPath($PrivateReportFile)
$sourceRoot = (Resolve-Path (Join-Path $PSScriptRoot "../../..")).Path
foreach ($root in @($InstallDir, $StateDir, $sourceRoot)) {
    $prefix = [System.IO.Path]::GetFullPath($root).TrimEnd([char[]]@('\', '/')) + [System.IO.Path]::DirectorySeparatorChar
    if ($reportFull.StartsWith($prefix, [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "Private report must be outside installation, state and source trees."
    }
}
$target = Read-HrmComparisonJson (Join-Path $InstallDir "release.json")
$package = Read-HrmComparisonJson $PackageReleaseFile
$record = Read-HrmComparisonJson (Join-Path $StateDir "installed.json")
$a = [string](Read-HrmComparisonField $target "release_sha")
$b = [string](Read-HrmComparisonField $package "release_sha")
$c = [string](Read-HrmComparisonField $record "release_sha")
$d = [string](Read-HrmComparisonField $record "install_dir")
$samePath = $null
if ($d) {
    $left = [System.IO.Path]::GetFullPath($d).TrimEnd([char[]]@('\', '/'))
    $right = [System.IO.Path]::GetFullPath($InstallDir).TrimEnd([char[]]@('\', '/'))
    $samePath = [string]::Equals($left, $right, [System.StringComparison]::OrdinalIgnoreCase)
}
$branch = "neither_files_replaced_predicate"
if ($a -and $b -and $a -eq $b) { $branch = "package_identity_present" }
elseif ($a -and $c -and $a -ne $c) { $branch = "record_identity_mismatch" }
$data = [ordered]@{
    schema = "hrm-private-snapshot-inputs-1"
    file_sha = $a; new_release_sha = $b; record_sha = $c
    file_version = (Read-HrmComparisonField $target "version")
    file_build_digest = (Read-HrmComparisonField $target "build_digest")
    new_build_digest = (Read-HrmComparisonField $package "build_digest")
    record_install_dir = $d; target_install_dir = $InstallDir
    record_pilot_created = (Read-HrmComparisonField $record "pilot_created")
    port = (Read-HrmComparisonField $record "port")
    file_equals_package = [bool]($a -and $b -and $a -eq $b)
    file_equals_record = [bool]($a -and $c -and $a -eq $c)
    record_directory_equals_target = $samePath
    legacy_refusal_predicate = $branch
    previous_descriptor_present = (Test-Path (Join-Path $StateDir "previous-snapshot.json"))
    note = "Read-only predicate diagnosis, not a guard decision: existing verified/adoptable snapshot takes precedence. Correlate with the PRIVATE installer refusal log."
}
$bytes = [System.Text.Encoding]::UTF8.GetBytes(($data | ConvertTo-Json -Depth 4))
$stream = [System.IO.File]::Open($PrivateReportFile, [System.IO.FileMode]::CreateNew, [System.IO.FileAccess]::Write, [System.IO.FileShare]::None)
try { $stream.Write($bytes, 0, $bytes.Length) } finally { $stream.Dispose() }
# Console deliberately excludes SHA/path/token values; owner keeps full report private.
Write-Host ("legacy_refusal_predicate=" + $branch)
Write-Host ("file_equals_package=" + $data.file_equals_package)
Write-Host ("file_equals_record=" + $data.file_equals_record)
Write-Host ("record_directory_equals_target=" + $samePath)
