# Local integrity gate for a separately supplied Docker Desktop sidecar.
# This script never downloads, installs, or launches the installer.
[CmdletBinding()]
param(
    [string]$Path,
    [string]$ExpectedSha256
)

$ErrorActionPreference = "Stop"
$expectedSubject = "CN=Docker Inc, O=Docker Inc, L=Palo Alto, S=California, C=US, SERIALNUMBER=4817464, OID.2.5.4.15=Private Organization, OID.1.3.6.1.4.1.311.60.2.1.2=Delaware, OID.1.3.6.1.4.1.311.60.2.1.3=US"
$expectedThumbprint = "b6bd29272b07ad4d0f1322a739499d67ca3bac3f"

# Exit codes are stable for manual/release automation:
#   0 = SHA256 and valid Docker Authenticode publisher both match
#   1 = integrity/signature/publisher alert
#   2 = the check could not be performed (bad input, missing file, access/API error)
$expected = ([string]$ExpectedSha256).Trim().ToLowerInvariant()
if ($expected -notmatch '^[0-9a-f]{64}$') {
    Write-Host "UNABLE_TO_CHECK: ExpectedSha256 must contain exactly 64 hexadecimal characters."
    exit 2
}
if ([string]::IsNullOrWhiteSpace($Path) -or -not (Test-Path -LiteralPath $Path -PathType Leaf)) {
    Write-Host "UNABLE_TO_CHECK: the local Docker Desktop installer file does not exist."
    exit 2
}

try {
    $resolvedPath = (Resolve-Path -LiteralPath $Path -ErrorAction Stop).Path
    $actualHash = (Get-FileHash -LiteralPath $resolvedPath -Algorithm SHA256 -ErrorAction Stop).Hash.ToLowerInvariant()
}
catch {
    Write-Host ("UNABLE_TO_CHECK: could not read/hash the local installer: {0}" -f $_.Exception.Message)
    exit 2
}
if ($actualHash -ne $expected) {
    Write-Host ("ALERT: local installer SHA256 {0} does not match the supplied pin." -f $actualHash)
    exit 1
}

try {
    $signature = Get-AuthenticodeSignature -FilePath $resolvedPath -ErrorAction Stop
}
catch {
    Write-Host ("UNABLE_TO_CHECK: Get-AuthenticodeSignature failed: {0}" -f $_.Exception.Message)
    exit 2
}
if ($null -eq $signature) {
    Write-Host "UNABLE_TO_CHECK: Get-AuthenticodeSignature returned no result."
    exit 2
}
if ([string]$signature.Status -cne "Valid") {
    Write-Host ("ALERT: Authenticode status is '{0}', not 'Valid'." -f [string]$signature.Status)
    exit 1
}

$certificate = $signature.SignerCertificate
if ($null -eq $certificate) {
    Write-Host "ALERT: the valid Authenticode result has no signer certificate."
    exit 1
}
$actualSubject = [string]$certificate.Subject
$actualThumbprint = ([string]$certificate.Thumbprint).Replace(" ", "").ToLowerInvariant()
if ($actualSubject -cne $expectedSubject) {
    Write-Host "ALERT: Authenticode Subject does not exactly match the pinned Docker Inc subject."
    exit 1
}
if ($actualThumbprint -ne $expectedThumbprint) {
    Write-Host "ALERT: Authenticode thumbprint does not match the pinned Docker Inc certificate."
    exit 1
}

Write-Host "VERIFIED: pinned SHA256, valid Authenticode signature, exact Docker Inc Subject, and certificate thumbprint all match."
exit 0
