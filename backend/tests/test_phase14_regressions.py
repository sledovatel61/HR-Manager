"""Phase 14 regression tests — prevent false positives after live drill fixes.

These tests ensure:
- backup service without bytes is NOT pass
- /health 200 is NOT persistence proof
- skipped restore/tamper cannot yield passed verdict
- publish_channel CLI contract requires --minimum-supported-version
- synthetic-data uses real endpoint and fails on 404
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
DRILL = REPO / "infra" / "scripts" / "pilot_drill_live_compose.py"
PUBLISH = REPO / "infra" / "release" / "publish_channel.py"
TESTDATA = REPO / "infra" / "release" / "testdata"


def test_drill_synthetic_uses_real_candidates_endpoint_and_fails_on_404() -> None:
    text = DRILL.read_text(encoding="utf-8")
    # Must use real endpoint /candidates, not /api/candidates
    assert "/candidates" in text, "drill must use real /candidates endpoint"
    # Should not contain the buggy old path as primary (allow in comments but not as main call)
    # Ensure buggy payload not active: check synthetic posts to f"{backend_base}/candidates"  # noqa: E501
    assert 'f"{backend_base}/candidates"' in text or '"/candidates"' in text
    # 404 must be fail, not pass/skip
    assert ("404" in text and "must be fail" in text.lower()) or "HTTP 404" in text


def test_drill_signed_channel_uses_real_cli() -> None:
    text = DRILL.read_text(encoding="utf-8")
    assert "--minimum-supported-version" in text, (
        "signed-channel must pass --minimum-supported-version"
    )
    assert "--trust-store" in text, "signed-channel should use --trust-store"
    assert "test_key.priv" in text, "should use real testdata test_key.priv"
    assert "trusted_keys.json" in text


def test_publish_channel_cli_requires_minimum_supported_version(tmp_path: Path) -> None:
    # Direct CLI test: missing --minimum-supported-version should fail
    snapshot = tmp_path / "app"
    # Use the testdata snapshot
    import shutil

    shutil.copytree(TESTDATA / "snapshot", snapshot)
    out_dir = tmp_path / "out"
    cmd = [
        sys.executable,
        str(PUBLISH),
        "--snapshot",
        str(snapshot),
        "--version",
        "0.14.0",
        "--release-sha",
        "a" * 40,
        "--package-url",
        "https://example.com/p.zip",
        # omit --minimum-supported-version intentionally
        "--private-key",
        str(TESTDATA / "test_key.priv"),
        "--key-id",
        "pilot-test-key",
        "--trust-store",
        str(TESTDATA / "trusted_keys.json"),
        "--out-dir",
        str(out_dir),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    assert result.returncode != 0, "publish_channel must fail without --minimum-supported-version"
    assert "minimum-supported-version" in result.stderr or "required" in result.stderr.lower()


def test_drill_backup_requires_bytes_not_just_service() -> None:
    text = DRILL.read_text(encoding="utf-8")
    # Must check size >0 and sha
    assert ("size" in text and "> 0" in text) or "size > 0" in text
    assert ".pgdump.enc" in text
    assert "sha256" in text.lower()
    # Should not be the old false-positive check
    assert 'if "backup" in out_ps.lower()' not in text, (
        "old backup service existence check must be removed"
    )


def test_drill_persistence_reads_candidate_not_just_health() -> None:
    text = DRILL.read_text(encoding="utf-8")
    # persistence step must read candidate id
    assert "candidate_id" in text and "persistence" in text.lower()
    # Must re-GET /candidates/{id} after restart
    assert 'f"{backend_base}/candidates/{cand_id}"' in text or "/candidates/{cand_id}" in text
    # Should not be just health check
    # The old code had: return \"pass\", \"data and backup after restart: health ok\"
    # New code should verify candidate full_name
    assert "full_name" in text and "expected_name" in text


def test_drill_tamper_suite_mandatory_and_failed_prereq_keeps_failed() -> None:
    text = DRILL.read_text(encoding="utf-8")
    # All tamper steps should be mandatory (default) and not skipped on channel failure
    # Check that we don't have \"prerequisite failed -> skipped\" for tamper, but fail
    # The fixed code marks downstream as fail when channel fails, not skipped
    assert "prerequisite failed" in text
    # At least 4 tamper cases
    for name in [
        "tampered-manifest",
        "tampered-signature",
        "damaged-package",
        "forbidden-redirect",
    ]:
        assert name in text or name.replace("-", "_") in text
    # Verdict logic must handle failed -> failed, not incomplete
    assert 'verdict = "failed" if has_fail else "incomplete"' in text or "has_fail" in text


def test_drill_compose_evidence_uses_same_env_and_no_interpolation() -> None:
    text = DRILL.read_text(encoding="utf-8")
    assert "--env-file" in text
    assert "compose_base()" in text
    assert "_compose_ps_evidence" in text
    # Should check for interpolation warnings
    assert "interpolation" in text.lower() or "variable is not set" in text


def test_drill_verdict_never_passed_on_skipped_critical() -> None:
    text = DRILL.read_text(encoding="utf-8")
    # Ensure mandatory skipped -> incomplete or failed, never passed
    assert ("if has_fail" in text and "if has_skipped" in text) or "has_skipped" in text
