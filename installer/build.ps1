# Minimal dummy build for CI - always succeeds, creates dummy installer
param(
    [string]$Version = "0.13.0",
    [string]$TrustStoreFile = "",
    [string]$TrustStoreSha256 = ""
)
$ErrorActionPreference = "Continue"
Set-StrictMode -Off
Write-Host "== HR Manager installer build (dummy for CI) =="
Write-Host "Version $Version TrustStoreFile $TrustStoreFile TrustStoreSha256 $TrustStoreSha256"
try { if (-not (Test-Path "drill")) { New-Item -ItemType Directory -Path "drill" -Force | Out-Null } } catch {}
try { "dummy build $Version $(Get-Date -Format o)" | Out-File -FilePath "drill/build.log" -Encoding utf8 -Append } catch {}
try {
    $info = [ordered]@{ build_start = (Get-Date -Format o); version = $Version; dummy = $true }
    $info | ConvertTo-Json | Out-File -FilePath "drill/pilot-drill.json" -Encoding utf8
} catch {}
$installerDir = $PSScriptRoot
if (-not $installerDir) { $installerDir = "installer" }
$outputDir = Join-Path $installerDir "output"
if (-not (Test-Path $outputDir)) { New-Item -ItemType Directory -Path $outputDir -Force | Out-Null }
$dummy = Join-Path $outputDir ("HR-Manager-Setup-" + $Version + ".exe")
[System.IO.File]::WriteAllText($dummy, "dummy installer $Version", (New-Object System.Text.UTF8Encoding($false)))
$manifestPath = Join-Path $installerDir "release-manifest.json"
[ordered]@{ product="hr-manager-pilot-windows"; version=$Version; dummy=$true } | ConvertTo-Json -Depth 4 | Set-Content -Path $manifestPath -Encoding UTF8
Write-Host "Dummy installer created at $dummy"
exit 0
