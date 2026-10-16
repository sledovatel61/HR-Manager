# Snapshot recovery: additive regressions; fixtures only, no live Docker/PII.
param([string]$HarnessPath = $PSScriptRoot)
. (Join-Path $HarnessPath "test-harness.ps1")

function New-HrmRecoveryCase {
    param([bool]$Completed = $true)
    Initialize-HrmTestEngine
    New-HrmMockWorld | Out-Null
    $state = Get-HrmTestStateDir
    $install = Get-HrmTestInstallDir
    New-Item -ItemType Directory -Path $state -Force | Out-Null
    New-HrmFakeSnapshot -Root $install -ReleaseSha ("a" * 40)
    Set-HrmJsonFile $state "installed.json" ([ordered]@{
        release_sha = ("a" * 40); version = "0.15.0"
        install_dir = $install; pilot_created = $Completed; port = 8081
    })
    $package = Join-Path (Split-Path $install -Parent) "package"
    Copy-HrmSnapshot -SourceDir $install -InstallDir $package
    return [pscustomobject]@{ state = $state; install = $install; package = $package; result = (Join-Path $state "snapshot-result.txt") }
}

function Get-HrmRecoveryResultField {
    param([string]$Path, [string]$Key)
    foreach ($line in @(Get-Content -LiteralPath $Path -Encoding UTF8)) {
        if ($line.StartsWith($Key + "=")) { return $line.Substring($Key.Length + 1) }
    }
    return ""
}

function Set-HrmRecoveryReleaseDigest {
    param([string]$Root)
    $data = Get-HrmJsonFile (Join-Path $Root "release.json")
    $data | Add-Member -NotePropertyName build_digest -NotePropertyValue (Get-HrmSnapshotBuildDigest -Root $Root) -Force
    [System.IO.File]::WriteAllText((Join-Path $Root "release.json"), ($data | ConvertTo-Json), (New-Object System.Text.UTF8Encoding($false)))
}

Test-Case "SNAP: digest ignores release timestamps and root location" {
    $c = New-HrmRecoveryCase
    $a = Get-HrmSnapshotBuildDigest $c.install
    [System.IO.File]::WriteAllText((Join-Path $c.package "release.json"), '{"release_sha":"synthetic-other","built_at":"synthetic-later"}')
    Assert-HrmEqual $a (Get-HrmSnapshotBuildDigest $c.package)
}

Test-Case "SNAP: digest changes with content and file names" {
    $c = New-HrmRecoveryCase
    $before = Get-HrmSnapshotBuildDigest $c.install
    Add-Content (Join-Path $c.install "backend/marker.txt") "changed"
    $changed = Get-HrmSnapshotBuildDigest $c.install
    Assert-HrmFalse ($before -eq $changed)
    Rename-Item (Join-Path $c.install "backend/marker.txt") "renamed.txt"
    Assert-HrmFalse ($changed -eq (Get-HrmSnapshotBuildDigest $c.install))
}

Test-Case "SNAP: same commit different content saves verified original bytes" {
    $c = New-HrmRecoveryCase
    Set-HrmRecoveryReleaseDigest $c.install
    Add-Content (Join-Path $c.package "backend/marker.txt") "new build"
    $recordHash = Get-HrmFileSha256 (Join-Path $c.state "installed.json")
    [System.IO.File]::WriteAllText((Join-Path $c.state "secrets.json"), '{"fixture":"unchanged"}')
    $secretHash = Get-HrmFileSha256 (Join-Path $c.state "secrets.json")
    $original = Get-HrmFileSha256 (Join-Path $c.install "backend/marker.txt")
    $r = Save-HrmVerifiedPreviousSnapshot -InstallDir $c.install -StateDir $c.state -ResultFile $c.result `
        -NewReleaseSha ("a" * 40) -NewBuildDigest (Get-HrmSnapshotBuildDigest $c.package)
    Assert-HrmTrue $r.verified
    Assert-HrmEqual $original (Get-HrmFileSha256 (Join-Path (Get-HrmPreviousSnapshotDir $c.state) "backend/marker.txt"))
    Assert-HrmTrue (Test-HrmPreviousSnapshotUsable -StateDir $c.state -ReleaseSha ("a" * 40)).usable
    Assert-HrmEqual $recordHash (Get-HrmFileSha256 (Join-Path $c.state "installed.json"))
    Assert-HrmEqual $secretHash (Get-HrmFileSha256 (Join-Path $c.state "secrets.json"))
}

Test-Case "SNAP: identical completed installation still refuses files_replaced" {
    $c = New-HrmRecoveryCase
    $r = Save-HrmVerifiedPreviousSnapshot -InstallDir $c.install -StateDir $c.state -ResultFile $c.result `
        -NewReleaseSha ("a" * 40) -NewBuildDigest (Get-HrmSnapshotBuildDigest $c.package)
    Assert-HrmFalse $r.verified
    Assert-HrmFalse $r.skipped
    Assert-HrmEqual "files_replaced" $r.reason
    Assert-HrmEqual "package_identity_present" (Get-HrmRecoveryResultField $c.result "guard_branch")
    Assert-HrmFalse (Test-Path (Join-Path $c.state "previous-snapshot.json"))
}

Test-Case "SNAP: unfinished same-package retry preserves a verified snapshot" {
    $c = New-HrmRecoveryCase -Completed $false
    $r = Save-HrmVerifiedPreviousSnapshot -InstallDir $c.install -StateDir $c.state -ResultFile $c.result `
        -NewReleaseSha ("a" * 40) -NewBuildDigest (Get-HrmSnapshotBuildDigest $c.package)
    Assert-HrmTrue $r.verified
    Assert-HrmEqual "incomplete_retry" $r.reason
    Assert-HrmTrue (Test-HrmPreviousSnapshotUsable -StateDir $c.state -ReleaseSha ("a" * 40)).usable
    Assert-HrmEqual "false" (Get-HrmRecoveryResultField $c.result "record_pilot_created")
    $again = Save-HrmVerifiedPreviousSnapshot -InstallDir $c.install -StateDir $c.state -NewReleaseSha ("a" * 40)
    Assert-HrmEqual "reused" $again.reason
}

Test-Case "SNAP: legacy unfinished same-SHA retry needs no invented metadata" {
    $c = New-HrmRecoveryCase -Completed $false
    $before = Get-HrmFileSha256 (Join-Path $c.install "release.json")
    $r = Save-HrmVerifiedPreviousSnapshot -InstallDir $c.install -StateDir $c.state -NewReleaseSha ("a" * 40)
    Assert-HrmTrue $r.verified
    Assert-HrmEqual "incomplete_retry" $r.reason
    Assert-HrmEqual $before (Get-HrmFileSha256 (Join-Path $c.install "release.json"))
}

foreach ($flag in @("missing", "string-false")) {
    Test-Case "SNAP: $flag pilot flag cannot authorize same-package recovery" {
        $c = New-HrmRecoveryCase
        $record = [ordered]@{ release_sha = ("a" * 40); install_dir = $c.install }
        if ($flag -eq "string-false") { $record["pilot_created"] = "false" }
        Set-HrmJsonFile $c.state "installed.json" $record
        $r = Save-HrmVerifiedPreviousSnapshot -InstallDir $c.install -StateDir $c.state -NewReleaseSha ("a" * 40)
        Assert-HrmFalse $r.verified
        Assert-HrmEqual "files_replaced" $r.reason
    }
}

Test-Case "SNAP: unfinished flag without record directory cannot authorize retry" {
    $c = New-HrmRecoveryCase -Completed $false
    Set-HrmJsonFile $c.state "installed.json" ([ordered]@{ release_sha = ("a" * 40); pilot_created = $false })
    $r = Save-HrmVerifiedPreviousSnapshot -InstallDir $c.install -StateDir $c.state -NewReleaseSha ("a" * 40)
    Assert-HrmEqual "files_replaced" $r.reason
}

Test-Case "SNAP: directory mismatch is reported before reuse or copying" {
    $c = New-HrmRecoveryCase -Completed $false
    $first = Save-HrmVerifiedPreviousSnapshot -InstallDir $c.install -StateDir $c.state -NewReleaseSha ("b" * 40)
    Assert-HrmTrue $first.verified
    $snapshotHash = Get-HrmFileSha256 (Join-Path $c.state "previous-snapshot.json")
    Set-HrmInstallRecord $c.state @{ install_dir = $c.package }
    $r = Save-HrmVerifiedPreviousSnapshot -InstallDir $c.install -StateDir $c.state -ResultFile $c.result -NewReleaseSha ("a" * 40)
    Assert-HrmEqual "install_dir_mismatch" $r.reason
    Assert-HrmFalse $r.verified
    Assert-HrmContains $r.message $c.install
    Assert-HrmContains $r.message $c.package
    Assert-HrmEqual $c.package (Get-HrmRecoveryResultField $c.result "record_install_dir")
    Assert-HrmEqual $c.install (Get-HrmRecoveryResultField $c.result "target_install_dir")
    Assert-HrmEqual $snapshotHash (Get-HrmFileSha256 (Join-Path $c.state "previous-snapshot.json"))
}

Test-Case "SNAP: normalized equivalent directories are not rejected" {
    $c = New-HrmRecoveryCase
    Set-HrmInstallRecord $c.state @{ install_dir = ($c.install + [System.IO.Path]::DirectorySeparatorChar) }
    $r = Save-HrmVerifiedPreviousSnapshot -InstallDir $c.install -StateDir $c.state -NewReleaseSha ("b" * 40)
    Assert-HrmTrue $r.verified
}

Test-Case "SNAP: refusal transport includes all distinguishing fields" {
    $c = New-HrmRecoveryCase
    $null = Save-HrmVerifiedPreviousSnapshot -InstallDir $c.install -StateDir $c.state -ResultFile $c.result -NewReleaseSha ("a" * 40)
    foreach ($key in @("file_sha", "new_release_sha", "record_sha", "record_install_dir", "target_install_dir", "record_pilot_created", "message", "guard_branch")) {
        Assert-HrmTrue (-not [string]::IsNullOrEmpty((Get-HrmRecoveryResultField $c.result $key))) ("Missing field: " + $key)
    }
    Assert-HrmEqual "true" (Get-HrmRecoveryResultField $c.result "record_pilot_created")
}

Test-Case "SNAP: distinct record mismatch branch and concrete CLI message" {
    $c = New-HrmRecoveryCase
    Set-HrmInstallRecord $c.state @{ release_sha = ("b" * 40) }
    $output = @(& { Invoke-HrmSnapshotCli -InstallDir $c.install -StateDir $c.state -ResultFile $c.result } 6>&1)
    Assert-HrmEqual 3 $output[-1]
    Assert-HrmContains ($output | Out-String) "не совпадают"
    Assert-HrmEqual "record_identity_mismatch" (Get-HrmRecoveryResultField $c.result "guard_branch")
    Assert-HrmEqual ("a" * 40) (Get-HrmRecoveryResultField $c.result "file_sha")
    Assert-HrmEqual ("b" * 40) (Get-HrmRecoveryResultField $c.result "record_sha")
}

Test-Case "SNAP: changed record SHA cannot use unfinished-retry exception" {
    $c = New-HrmRecoveryCase -Completed $false
    Set-HrmInstallRecord $c.state @{ release_sha = ("b" * 40) }
    Add-Content (Join-Path $c.package "backend/marker.txt") "new build"
    $r = Save-HrmVerifiedPreviousSnapshot -InstallDir $c.install -StateDir $c.state -NewReleaseSha ("a" * 40) `
        -NewBuildDigest (Get-HrmSnapshotBuildDigest $c.package)
    Assert-HrmFalse $r.verified
    Assert-HrmEqual "files_replaced" $r.reason
}

Test-Case "SNAP: installed digest tampering cannot be accepted as another build" {
    $c = New-HrmRecoveryCase -Completed $false
    Set-HrmRecoveryReleaseDigest $c.install
    Add-Content (Join-Path $c.install "backend/marker.txt") "unexpected modification"
    $r = Save-HrmVerifiedPreviousSnapshot -InstallDir $c.install -StateDir $c.state -NewReleaseSha ("a" * 40) `
        -NewBuildDigest (Get-HrmSnapshotBuildDigest $c.package)
    Assert-HrmFalse $r.verified
    Assert-HrmEqual "build_digest_mismatch" $r.reason
}

Test-Case "SNAP: corrupted copy is never marked verified" {
    $c = New-HrmRecoveryCase
    $module = Get-Module Snapshot
    try {
        & $module {
            function script:Copy-HrmSnapshot {
                param([string]$SourceDir, [string]$InstallDir, [switch]$SkipLicenseKey)
                foreach ($part in @("infra", "backend", "frontend", "release.json")) {
                    Copy-Item -LiteralPath (Join-Path $SourceDir $part) -Destination (Join-Path $InstallDir $part) -Recurse -Force
                }
                Add-Content (Join-Path $InstallDir "backend/marker.txt") "copy corruption"
            }
        }
        $r = Save-HrmVerifiedPreviousSnapshot -InstallDir $c.install -StateDir $c.state -NewReleaseSha ("b" * 40)
        Assert-HrmFalse $r.verified
        Assert-HrmEqual "copy_failed" $r.reason
        Assert-HrmFalse (Test-Path (Join-Path $c.state "previous-snapshot.json"))
    }
    finally {
        Remove-Module Snapshot -Force
        Import-Module (Join-Path $HarnessPath "../engine/Snapshot.psm1") -Force
    }
}

Test-Case "SNAP: result transport redacts and prevents injected result lines" {
    $c = New-HrmRecoveryCase
    $secret = "synthetic-secret-for-redaction"
    Register-HrmSecret $secret
    Write-HrmSnapshotResultFile -Path $c.result -Status "failed" -Reason "fixture" `
        -Message ($secret + "`nstatus=verified`r`nreason=fake") -RecordInstallDir 'C:\synthetic\quoted"folder'
    $lines = @(Get-Content -LiteralPath $c.result -Encoding UTF8)
    Assert-HrmEqual 1 @($lines | Where-Object { $_ -like "status=*" }).Count
    Assert-HrmEqual "failed" (Get-HrmRecoveryResultField $c.result "status")
    Assert-HrmNotContains ($lines -join "`n") $secret
    Assert-HrmContainsRedacted ($lines -join "`n")
}

Test-Case "SNAP: actual helper process uses current PowerShell host and concrete reason" {
    $c = New-HrmRecoveryCase
    $repo = (Resolve-Path (Join-Path $HarnessPath "../../..")).Path
    $helper = Join-Path (Split-Path $c.install -Parent) "helper"
    New-Item -ItemType Directory -Path $helper -Force | Out-Null
    Copy-Item (Join-Path $repo "infra/windows/hrm-snapshot.ps1") $helper
    foreach ($name in @("Common", "Secrets", "Install", "Snapshot")) {
        Copy-Item (Join-Path $repo ("infra/windows/engine/" + $name + ".psm1")) $helper
    }
    Copy-Item (Join-Path $c.install "release.json") $helper
    $exe = (Get-Process -Id $PID).Path
    $output = & $exe -NoProfile -File (Join-Path $helper "hrm-snapshot.ps1") `
        -InstallDir $c.install -StateDir $c.state -ResultFile $c.result 2>&1 | Out-String
    Assert-HrmEqual 3 $LASTEXITCODE
    Assert-HrmContains $output "идентичность пакета"
    Assert-HrmEqual "package_identity_present" (Get-HrmRecoveryResultField $c.result "guard_branch")
}

Test-Case "SNAP: installer retains helper fields and no longer recommends blind retry" {
    $repo = (Resolve-Path (Join-Path $HarnessPath "../../..")).Path
    $iss = Get-Content (Join-Path $repo "installer/installer.iss") -Raw -Encoding UTF8
    foreach ($key in @("file_sha", "new_release_sha", "record_sha", "record_install_dir", "target_install_dir", "record_pilot_created", "message", "guard_branch")) {
        Assert-HrmContains $iss ("'" + $key + "'")
    }
    Assert-HrmContains $iss "EscapeJson(SnapshotDetailValues[I])"
    Assert-HrmContains $iss "if Reason = 'files_replaced'"
    Assert-HrmNotContains $iss "при повторном запуске копия прежней версии будет создана"
}

Test-Case "SNAP: files_replaced dialog is chosen by reason and hides raw status/reason codes" {
    # Reviewed defect: the wizard compared Status with 'files_replaced', but the
    # helper reports this refusal as status=failed + reason=files_replaced, so
    # the dedicated SnapshotFilesReplaced text was dead code and the user saw
    # the generic "Причина: failed / files_replaced". Runtime half: prove the
    # helper's actual field values for this refusal.
    $c = New-HrmRecoveryCase
    $null = Save-HrmVerifiedPreviousSnapshot -InstallDir $c.install -StateDir $c.state -ResultFile $c.result -NewReleaseSha ("a" * 40)
    Assert-HrmEqual "failed" (Get-HrmRecoveryResultField $c.result "status")
    Assert-HrmEqual "files_replaced" (Get-HrmRecoveryResultField $c.result "reason")
    # Static half (Inno Pascal is not executable in this harness): the message
    # chain of PreservePreviousSnapshot is an exclusive if / else-if / else.
    $repo = (Resolve-Path (Join-Path $HarnessPath "../../..")).Path
    $iss = Get-Content (Join-Path $repo "installer/installer.iss") -Raw -Encoding UTF8
    $start = $iss.IndexOf("function PreservePreviousSnapshot(): String;")
    $end = $iss.IndexOf("function HrmVerifySnapshotGuard(): String;")
    Assert-HrmTrue ($start -ge 0 -and $end -gt $start) "PreservePreviousSnapshot not found"
    $body = $iss.Substring($start, $end - $start)
    Assert-HrmNotContains $body "Status = 'files_replaced'" "files_replaced must not be compared with Status"
    $chain = [regex]::Match($body, "(?s)if Reason = 'files_replaced' then\s*(?<dedicated>[^;]*?)\s*else if Status = '' then\s*(?<helper>[^;]*?)\s*else\s*(?<generic>Message := CustomMessage\('SnapshotFailed'\)[^;]*);")
    Assert-HrmTrue $chain.Success "exclusive message chain not found"
    Assert-HrmEqual "Message := CustomMessage('SnapshotFilesReplaced')" $chain.Groups["dedicated"].Value.Trim()
    Assert-HrmNotContains $chain.Groups["dedicated"].Value "Причина"
    Assert-HrmContains $chain.Groups["generic"].Value "'Причина: '"
    # The raw status/reason line exists only in the generic fallback branch.
    Assert-HrmEqual 1 ([regex]::Matches($body, "'Причина: '")).Count
}

Test-Case "SNAP: build rejects dirty source and hashes final staged content" {
    $repo = (Resolve-Path (Join-Path $HarnessPath "../../..")).Path
    $build = Get-Content (Join-Path $repo "installer/build.ps1") -Raw -Encoding UTF8
    Assert-HrmContains $build '--porcelain'
    Assert-HrmTrue ($build.IndexOf('--porcelain') -lt $build.IndexOf('Invoke-WebRequest'))
    $hash = $build.IndexOf('Get-HrmSnapshotBuildDigest -Root $appStaging')
    Assert-HrmTrue ($hash -gt $build.IndexOf('$stagedLicenseFile ='))
    Assert-HrmTrue ($hash -gt $build.IndexOf('$trustStoreTarget ='))
    Assert-HrmTrue ($hash -lt $build.IndexOf('Write-Host "Compiling installer'))
}
