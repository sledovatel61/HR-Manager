# Executable production PEM preflight tests, Windows PowerShell 5.1 and pwsh.
# Import ONLY function ASTs: never run sign.ps1, signtool or import a PFX.
param([string]$HarnessPath = $PSScriptRoot)
. (Join-Path $HarnessPath "test-harness.ps1")
$repo = (Resolve-Path (Join-Path $PSScriptRoot "../../..")).Path
$signPath = Join-Path $repo "installer/sign.ps1"
$tokens = $null
$errors = $null
$ast = [System.Management.Automation.Language.Parser]::ParseInput(
    [System.IO.File]::ReadAllText($signPath), [ref]$tokens, [ref]$errors)
if ($errors.Count -gt 0) { throw "sign.ps1 parse failed" }
foreach ($name in @("Get-HrmSha256Hex", "Assert-HrmPemCertificateFile", "Assert-HrmPinnedRootsPem")) {
    $function = $ast.Find({ param($node)
        $node -is [System.Management.Automation.Language.FunctionDefinitionAst] -and $node.Name -eq $name
    }, $true)
    if ($null -eq $function) { throw "Missing preflight function: $name" }
    . ([scriptblock]::Create($function.Extent.Text))
}
$work = Join-Path ([System.IO.Path]::GetTempPath()) ("hrm-roots-" + [guid]::NewGuid().ToString("N"))
New-Item -ItemType Directory $work | Out-Null
try {
    # Only ephemeral public certificates are written. No production inputs.
    $code = @'
import sys
from pathlib import Path
sys.path.insert(0, str(Path(sys.argv[1]) / "infra/release"))
from sign_authenticode import create_test_authority
work = Path(sys.argv[2])
for name in ("signer", "tsa"):
    (work / (name + ".pem")).write_bytes(create_test_authority(name).ca_pem())
'@
    & python -c $code $repo $work
    if ($LASTEXITCODE -ne 0) { throw "Ephemeral certificate setup failed" }
    $signer = Join-Path $work "signer.pem"
    $tsa = Join-Path $work "tsa.pem"
    $bad = Join-Path $work "bad.pem"
    $utf8 = New-Object System.Text.UTF8Encoding($false)
    $pem = [System.IO.File]::ReadAllText($signer)

    Test-Case "signing preflight accepts disjoint DER certificates" {
        Assert-HrmPinnedRootsPem -Mode production -SignerRootsPath $signer -TimestampRootsPath $tsa
    }
    Test-Case "signing preflight rejects identical DER roots" {
        Assert-HrmThrows "overlap accepted" {
            Assert-HrmPinnedRootsPem -Mode production -SignerRootsPath $signer -TimestampRootsPath $signer
        }
    }
    Test-Case "signing preflight rejects reformatted copy of same certificate" {
        $body = $pem.Replace("-----BEGIN CERTIFICATE-----", "").Replace("-----END CERTIFICATE-----", "") -replace '\s', ''
        $wrapped = ([regex]::Matches($body, '.{1,40}') | ForEach-Object { $_.Value }) -join "`r`n"
        [System.IO.File]::WriteAllText($bad, (" `r`n-----BEGIN CERTIFICATE-----`r`n" + $wrapped + "`r`n-----END CERTIFICATE-----`r`n"), $utf8)
        Assert-HrmThrows "reformatted overlap accepted" {
            Assert-HrmPinnedRootsPem -Mode production -SignerRootsPath $signer -TimestampRootsPath $bad
        }
    }
    Test-Case "signing preflight checks all certificates in a bundle" {
        [System.IO.File]::WriteAllText($bad, ([System.IO.File]::ReadAllText($tsa) + $pem), $utf8)
        Assert-HrmThrows "bundle overlap accepted" {
            Assert-HrmPinnedRootsPem -Mode production -SignerRootsPath $signer -TimestampRootsPath $bad
        }
    }
    foreach ($damage in @("empty", "private", "bom", "broken", "invalid-utf8", "missing")) {
        Test-Case ("signing preflight rejects " + $damage + " PEM in either role") {
            switch ($damage) {
                "empty" { [System.IO.File]::WriteAllText($bad, " ", $utf8) }
                "private" { [System.IO.File]::WriteAllText($bad, ($pem + "-----BEGIN PRIVATE KEY-----"), $utf8) }
                "bom" { [System.IO.File]::WriteAllBytes($bad, ([byte[]]@(0xEF, 0xBB, 0xBF) + $utf8.GetBytes($pem))) }
                "broken" { [System.IO.File]::WriteAllText($bad, $pem.Replace("-----END CERTIFICATE-----", ""), $utf8) }
                "invalid-utf8" { [System.IO.File]::WriteAllBytes($bad, ([byte[]]@(0xFF) + $utf8.GetBytes($pem))) }
                "missing" { Remove-Item $bad -Force -ErrorAction SilentlyContinue }
            }
            Assert-HrmThrows "invalid signer accepted" {
                Assert-HrmPinnedRootsPem -Mode production -SignerRootsPath $bad -TimestampRootsPath $tsa
            }
            Assert-HrmThrows "invalid TSA accepted" {
                Assert-HrmPinnedRootsPem -Mode production -SignerRootsPath $signer -TimestampRootsPath $bad
            }
        }
    }
    Test-Case "test signing mode still requires no operator roots" {
        Assert-HrmPinnedRootsPem -Mode test -SignerRootsPath "" -TimestampRootsPath ""
        Assert-HrmPinnedRootsPem -Mode test -SignerRootsPath $signer -TimestampRootsPath $signer
    }
}
finally { Remove-Item $work -Recurse -Force }
