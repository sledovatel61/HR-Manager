"""Source wiring contracts only; these do NOT replace either PowerShell runtime.

HRM_SNAPSHOT_CONTRACT_ROOT can point to a read-only baseline export to demonstrate
red/green for diagnostic/build wiring, without changing the checked-out branch.
"""

import os
from pathlib import Path

ROOT = Path(
    os.environ.get("HRM_SNAPSHOT_CONTRACT_ROOT", Path(__file__).resolve().parents[3])
)
SNAPSHOT = (ROOT / "infra/windows/engine/Snapshot.psm1").read_text(encoding="utf-8-sig")
BUILD = (ROOT / "installer/build.ps1").read_text(encoding="utf-8-sig")
INNO = (ROOT / "installer/installer.iss").read_text(encoding="utf-8-sig")


def test_diagnostic_fields_reach_helper_and_inno_json():
    for field in (
        "file_sha",
        "new_release_sha",
        "record_sha",
        "record_install_dir",
        "target_install_dir",
        "record_pilot_created",
        "message",
        "guard_branch",
    ):
        assert f"{field} =" in SNAPSHOT
        assert f"'{field}'" in INNO
    assert "EscapeJson(SnapshotDetailValues[I])" in INNO
    assert "UTF8Encode(Json)" in INNO


def test_cli_prints_concrete_redacted_message():
    cli = SNAPSHOT.split("function Invoke-HrmSnapshotCli {", 1)[1].split(
        "function Format-HrmSnapshotReason", 1
    )[0]
    assert "Redact-HrmText ([string]$result.message)" in cli
    assert "Format-HrmSnapshotReason -Reason $result.reason" not in cli


def test_directory_check_precedes_snapshot_reuse():
    body = SNAPSHOT.split("function Invoke-HrmPreviousSnapshotDecision {", 1)[1]
    assert body.index('reason = "install_dir_mismatch"') < body.index(
        "Resolve-HrmPreviousSnapshot -StateDir"
    )
    assert "Test-HrmSamePath -Left $recordDir -Right $InstallDir" in body


def test_retry_is_bound_to_explicit_false_directory_and_record_identity():
    assert "$pilot -is [bool] -and -not $pilot -and $recordDir" in SNAPSHOT
    assert "$installedSha -and $installedSha -eq $fileSha" in SNAPSHOT
    assert 'reason = "files_replaced"' in SNAPSHOT
    assert '"incomplete_retry"' in SNAPSHOT


def test_digest_is_canonical_and_avoids_metadata_self_reference():
    digest = SNAPSHOT.split("function Get-HrmSnapshotBuildDigest {", 1)[1].split(
        "function Test-HrmSnapshotManifest", 1
    )[0]
    assert 'if ($entry.path -eq "release.json") { continue }' in digest
    assert "StringComparer]::Ordinal" in digest
    assert "CultureInfo]::InvariantCulture" in digest
    assert "Encoding]::UTF8" in digest


def test_build_hashes_final_staged_tree_and_rejects_dirty_source():
    assert BUILD.index("--porcelain") < BUILD.index("Invoke-WebRequest")
    digest = BUILD.index("Get-HrmSnapshotBuildDigest -Root $appStaging")
    assert BUILD.index("$stagedLicenseFile =") < digest
    assert BUILD.index("$trustStoreTarget =") < digest
    assert digest < BUILD.index('Write-Host "Compiling installer')
    assert '$releaseJson["build_digest"]' in BUILD


def test_copy_verification_is_against_original_source():
    assert "$sourceManifest = @(Get-HrmSnapshotManifest -Root $SourceDir)" in SNAPSHOT
    assert "Test-HrmSnapshotManifest -Root $temp -Manifest $sourceManifest" in SNAPSHOT
    assert "Get-HrmSnapshotManifest -Root $SourceDir)) -ne $sourceDigest" in SNAPSHOT


def test_inno_uses_reason_not_status_and_no_blind_retry():
    assert "if Reason = 'files_replaced' then" in INNO
    assert "if Status = 'files_replaced' then" not in INNO
    assert "при повторном запуске копия прежней версии будет создана" not in INNO
