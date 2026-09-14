"""Тесты контракта кодовой подписи Windows-установщика (Phase 14).

infra/release/installer_signing.py — fail-closed контракт двух независимых
подписей: Ed25519 канала (Phase 13, обязательна) + Authenticode installer
(Phase 14, опциональна, но в production-режиме обязательна). Проверяется:
production-политика отказывает без подписи/timestamp/publisher, ephemeral
test certificate НЕ проходит production-политику, тестовый контур CI не
требует production secrets, и CLI verify-manifest возвращает non-zero при
нарушениях.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
RELEASE = REPO / "infra" / "release"
sys.path.insert(0, str(RELEASE))

from installer_signing import (  # type: ignore[import-not-found]  # noqa: E402
    SigningContractError,
    evaluate_signing_contract,
    verify_release_manifest,
)

PUBLISHER = "CN=HR Manager Pilot, O=HR Manager Pilot"


def _signed_block(
    *,
    mode: str = "production",
    certificate_kind: str = "production",
    status: str = "signed",
    timestamp_valid: bool = True,
    publisher: str = PUBLISHER,
) -> dict:
    return {
        "status": status,
        "mode": mode,
        "publisher": publisher,
        "timestamp_url": "http://timestamp.digicert.com",
        "timestamp_valid": timestamp_valid,
        "digest": "SHA256",
        "certificate_kind": certificate_kind,
        "signtool_verify": "passed",
    }


# --- Production policy: fail closed --------------------------------------------


def test_production_requires_signed_installer() -> None:
    problems = evaluate_signing_contract(
        _signed_block(status="unsigned", mode="disabled"), production=True
    )
    assert problems, "unsigned installer прошёл production-политику"


def test_production_requires_timestamp() -> None:
    problems = evaluate_signing_contract(_signed_block(timestamp_valid=False), production=True)
    assert any("timestamp" in problem for problem in problems)


def test_production_requires_timestamp_url() -> None:
    block = _signed_block()
    block["timestamp_url"] = ""
    problems = evaluate_signing_contract(block, production=True)
    assert any("timestamp" in problem for problem in problems)


def test_production_requires_matching_publisher() -> None:
    problems = evaluate_signing_contract(
        _signed_block(publisher="CN=Someone Else"), production=True, expected_publisher=PUBLISHER
    )
    assert any("publisher" in problem for problem in problems)


def test_production_accepts_valid_contract() -> None:
    problems = evaluate_signing_contract(
        _signed_block(), production=True, expected_publisher=PUBLISHER
    )
    assert problems == []


def test_production_requires_signtool_verify_passed() -> None:
    block = _signed_block()
    block["signtool_verify"] = "failed"
    problems = evaluate_signing_contract(block, production=True)
    assert any("signtool" in problem for problem in problems)


# --- Ephemeral test certificate never passes production --------------------------


def test_test_self_signed_certificate_fails_production_policy() -> None:
    """Ephemeral тестовый сертификат CI не проходит production-политику."""
    problems = evaluate_signing_contract(
        _signed_block(mode="test", certificate_kind="test-self-signed"),
        production=True,
    )
    assert any("тестовый" in problem or "certificate_kind" in problem for problem in problems)
    assert any("mode" in problem for problem in problems)


def test_test_mode_allowed_in_test_contour_only() -> None:
    block = _signed_block(mode="test", certificate_kind="test-self-signed")
    assert evaluate_signing_contract(block, production=False) == []


def test_disabled_mode_is_honest_in_test_contour() -> None:
    block = _signed_block(mode="disabled", status="unsigned")
    assert evaluate_signing_contract(block, production=False) == []


def test_test_contour_must_not_claim_production_certificate() -> None:
    block = _signed_block(mode="test", certificate_kind="production")
    problems = evaluate_signing_contract(block, production=False)
    assert any("production-сертификат" in problem for problem in problems)


def test_production_mode_forbidden_in_test_contour() -> None:
    problems = evaluate_signing_contract(_signed_block(), production=False)
    assert any("production-режим" in problem for problem in problems)


# --- Release manifest verification (CLI + hashes) --------------------------------


def _manifest_file(tmp_path: Path, signing: dict, exe_sha256: str) -> Path:
    manifest = {
        "product": "hr-manager-pilot-windows",
        "version": "0.14.0",
        "release_sha": "2" * 40,
        "installer_exe": {"file": "HR-Manager-Setup-0.14.0.exe", "sha256": exe_sha256},
        "signing": signing,
    }
    path = tmp_path / "release-manifest.json"
    path.write_text(json.dumps(manifest), encoding="utf-8")
    return path


def test_verify_manifest_production_ok(tmp_path: Path) -> None:
    exe = tmp_path / "HR-Manager-Setup-0.14.0.exe"
    exe.write_bytes(b"signed-installer-bytes")
    sha = hashlib.sha256(exe.read_bytes()).hexdigest()
    path = _manifest_file(tmp_path, _signed_block(), sha)
    assert verify_release_manifest(path, production=True, expected_publisher=PUBLISHER) == []


def test_verify_manifest_production_rejects_unsigned(tmp_path: Path) -> None:
    sha = hashlib.sha256(b"whatever").hexdigest()
    path = _manifest_file(tmp_path, _signed_block(status="unsigned", mode="disabled"), sha)
    problems = verify_release_manifest(path, production=True)
    assert problems


def test_verify_manifest_recomputes_exe_hash_after_signing(tmp_path: Path) -> None:
    """SHA256 в манифесте обязан соответствовать ПОДПИСАННОМУ файлу (пересчёт)."""
    exe = tmp_path / "HR-Manager-Setup-0.14.0.exe"
    exe.write_bytes(b"signed-installer-bytes")
    stale_sha = hashlib.sha256(b"old-unsigned-bytes").hexdigest()
    path = _manifest_file(tmp_path, _signed_block(), stale_sha)
    problems = verify_release_manifest(path, production=True, expected_publisher=PUBLISHER)
    assert any("SHA256" in problem for problem in problems)


def test_verify_manifest_cli_returns_nonzero_on_failure(tmp_path: Path) -> None:
    sha = hashlib.sha256(b"x").hexdigest()
    path = _manifest_file(tmp_path, _signed_block(status="unsigned"), sha)
    result = subprocess.run(
        [
            sys.executable,
            str(RELEASE / "installer_signing.py"),
            "verify-manifest",
            "--manifest",
            str(path),
            "--mode",
            "production",
        ],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode != 0
    assert "ОШИБКА" in result.stderr


def test_verify_manifest_cli_ok_in_test_mode(tmp_path: Path) -> None:
    sha = hashlib.sha256(b"x").hexdigest()
    path = _manifest_file(
        tmp_path, _signed_block(mode="test", certificate_kind="test-self-signed"), sha
    )
    result = subprocess.run(
        [
            sys.executable,
            str(RELEASE / "installer_signing.py"),
            "verify-manifest",
            "--manifest",
            str(path),
            "--mode",
            "test",
        ],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, result.stderr


def test_signing_contract_error_is_value_error() -> None:
    # Контракт использует безопасные исключения (не падает с traceback наружу).
    assert issubclass(SigningContractError, ValueError)
