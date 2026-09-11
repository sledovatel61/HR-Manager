"""Phase 14: fail-closed контракт production-выпуска канала.

Здесь проверяется, что `publish_channel.py --release-mode production`
отказывает ровно в тех случаях, которые требует промпт Phase 14:

* нет строго провалидированного production trust store;
* в trust store попал fixture-ключ репозитория (тестовый ключ никогда не
  становится доверенным production-ключом);
* нет authenticode-attestation / attestation помечена как тестовая;
* у подписи нет валидной метки времени;
* изменился SHA256 installer'а после подписи;
* издатель не совпал с ожидаемым;
* цепочка подписи не доводится до доверенного корня из release input;
* в публикуемый пакет попал приватный материал.

Плюс happy path: ephemeral CA + ephemeral Authenticode-подпись + fixture
Ed25519-ключ в режиме `test` (режим CI-PR) — и полностью зелёный
production-выпуск, где обе подписи проверены.

Настоящий production-сертификат в этих тестах не используется: это
owner-action, описанный в runbook.
"""

from __future__ import annotations

import base64
import hashlib
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
RELEASE = REPO / "infra" / "release"
TESTDATA = RELEASE / "testdata"
sys.path.insert(0, str(RELEASE))

from sign_authenticode import (  # type: ignore[import-not-found]  # noqa: E402
    create_test_authority,
    make_test_pe,
    sign_test_pe,
)
from trust_store import (  # type: ignore[import-not-found]  # noqa: E402
    describe_trust_store,
    trust_store_sha256,
)

PUBLISHER = "ООО Ромашка (pilot)"
PACKAGE_URL = (
    "https://github.com/sledovatel61/HR-Manager/releases/download/"
    "v0.14.0/hr-manager-windows-0.14.0.zip"
)


def _hex_key(seed: int) -> str:
    return base64.b64encode(bytes((seed + index) % 256 for index in range(32))).decode()


def _ephemeral_signing_key(tmp_path: Path) -> tuple[Path, Path, str]:
    """Ephemeral Ed25519-ключ канала: закрытый + trust store клиента."""
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    key = Ed25519PrivateKey.generate()
    private_path = tmp_path / "ephemeral-signing-key.hex"
    private_path.write_text(key.private_bytes_raw().hex() + "\n", encoding="utf-8")
    public_b64 = base64.b64encode(
        key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    ).decode()
    trust_store = tmp_path / "trust-store.json"
    trust_store.write_text(
        json.dumps({"pilot-release-2026": {"key": public_b64, "revoked": False}}),
        encoding="utf-8",
    )
    return private_path, trust_store, public_b64


@pytest.fixture()
def production_inputs(tmp_path: Path) -> dict:
    """Ephemeral «production» вход: CA, подписанный installer, attestation."""
    authority = create_test_authority(PUBLISHER)
    installer = tmp_path / "HR-Manager-Setup-0.14.0.exe"
    installer.write_bytes(sign_test_pe(make_test_pe(), authority))
    installer_sha = hashlib.sha256(installer.read_bytes()).hexdigest()
    attestation = {
        "mode": "production",
        "authenticode_present": True,
        "signtool_verify_ok": True,
        "timestamp_present": True,
        "publisher": PUBLISHER,
        "installer_sha256": installer_sha,
        "signer_thumbprint_sha256": authority.leaf_certificate.fingerprint(
            __import__("cryptography").hazmat.primitives.hashes.SHA256()
        ).hex(),
        "tool": "installer/sign.ps1",
    }
    roots = tmp_path / "authenticode-roots.pem"
    roots.write_bytes(authority.ca_pem())
    private_key, trust_store, public_key = _ephemeral_signing_key(tmp_path)
    store_data = json.loads(trust_store.read_text(encoding="utf-8"))
    attestation["trust_store"] = {
        "file": "trust-store.json",
        "sha256": trust_store_sha256(store_data),
        "keys": describe_trust_store(store_data),
    }
    outside_roots = tmp_path / "outside-roots.pem"
    outside_roots.write_bytes(create_test_authority("Другой корень").ca_pem())
    return {
        "installer": installer,
        "attestation": attestation,
        "roots": roots,
        "outside_roots": outside_roots,
        "trust_store": trust_store,
        "private_key": private_key,
        "public_key": public_key,
        "authority": authority,
        "tmp": tmp_path,
    }


def _run_production(
    tmp_path: Path, inputs: dict, extra: list[str], overwrite: dict | None = None
) -> subprocess.CompletedProcess[str]:
    snapshot = tmp_path / "app"
    if not snapshot.exists():
        shutil.copytree(TESTDATA / "snapshot", snapshot)
    attestation_path = tmp_path / "attestation.json"
    payload = dict(inputs["attestation"])
    if overwrite:
        payload.update(overwrite)
    attestation_path.write_text(json.dumps(payload), encoding="utf-8")
    command = [
        sys.executable,
        str(RELEASE / "publish_channel.py"),
        "--snapshot",
        str(snapshot),
        "--version",
        "0.14.0",
        "--release-sha",
        "2" * 40,
        "--package-url",
        PACKAGE_URL,
        "--minimum-supported-version",
        "0.13.0",
        "--notes-ru",
        "Тестовый production-выпуск Phase 14.",
        "--private-key",
        str(inputs["private_key"]),
        "--key-id",
        "pilot-release-2026",
        "--out-dir",
        str(tmp_path / "dist" / "channel"),
        "--release-mode",
        "production",
        "--expected-publisher",
        PUBLISHER,
        "--installer",
        str(inputs["installer"]),
        "--authenticode-attestation",
        str(attestation_path),
        "--authenticode-roots",
        str(inputs["roots"]),
        "--trust-store",
        str(inputs["trust_store"]),
        *extra,
    ]
    return subprocess.run(command, capture_output=True, text=True, timeout=180)


def test_production_requires_trust_store(production_inputs: dict, tmp_path: Path) -> None:
    command = [
        sys.executable,
        str(RELEASE / "publish_channel.py"),
        "--snapshot",
        str(TESTDATA / "snapshot"),
        "--version",
        "0.14.0",
        "--release-sha",
        "2" * 40,
        "--package-url",
        PACKAGE_URL,
        "--minimum-supported-version",
        "0.13.0",
        "--private-key",
        str(TESTDATA / "test_key.priv"),
        "--key-id",
        "pilot-test-key",
        "--out-dir",
        str(tmp_path / "out"),
        "--release-mode",
        "production",
    ]
    result = subprocess.run(command, capture_output=True, text=True, timeout=180)
    assert result.returncode != 0
    assert "missing_trust_store" in result.stderr
    assert not (tmp_path / "out" / "update-channel.json").exists()


def test_production_rejects_fixture_key_in_trust_store(
    production_inputs: dict, tmp_path: Path
) -> None:
    fixture_key = (TESTDATA / "test_key.pub").read_text().strip()
    store = tmp_path / "fixture-store.json"
    store.write_text(
        json.dumps({"pilot-test-key": {"key": fixture_key, "revoked": False}}), encoding="utf-8"
    )
    result = _run_production(tmp_path, production_inputs, ["--trust-store", str(store)])
    assert result.returncode != 0
    assert "fixture_key_in_production" in result.stderr


def test_production_rejects_missing_attestation(production_inputs: dict, tmp_path: Path) -> None:
    result = _run_production(
        tmp_path,
        production_inputs,
        ["--authenticode-attestation", str(tmp_path / "no-such-attestation.json")],
    )
    assert result.returncode != 0
    assert "missing_authenticode_attestation" in result.stderr


def test_production_rejects_test_certificate_attestation(
    production_inputs: dict, tmp_path: Path
) -> None:
    result = _run_production(tmp_path, production_inputs, [], overwrite={"mode": "test"})
    assert result.returncode != 0
    assert "test_certificate_in_production" in result.stderr


def test_production_rejects_unsigned_and_unverified_installer(
    production_inputs: dict, tmp_path: Path
) -> None:
    unsigned = _run_production(
        tmp_path,
        production_inputs,
        [],
        overwrite={"authenticode_present": False},
    )
    assert unsigned.returncode != 0
    assert "installer_unsigned" in unsigned.stderr

    unverified = _run_production(
        tmp_path / "second",  # отдельный прогон, чтобы пакет не переиспользовался
        production_inputs,
        [],
        overwrite={"signtool_verify_ok": False},
    )
    assert unverified.returncode != 0
    assert "signtool_verification_failed" in unverified.stderr


def test_production_rejects_missing_timestamp(production_inputs: dict, tmp_path: Path) -> None:
    # 1. Attestation без метки времени.
    result = _run_production(
        tmp_path, production_inputs, [], overwrite={"timestamp_present": False}
    )
    assert result.returncode != 0
    assert "missing_timestamp" in result.stderr

    # 2. Реальная подпись без метки времени: независимая проверка тоже отказ.
    authority = production_inputs["authority"]
    installer = production_inputs["tmp"] / "unsigned-timestamp-setup.exe"
    installer.write_bytes(sign_test_pe(make_test_pe(), authority, with_timestamp=False))
    sha = hashlib.sha256(installer.read_bytes()).hexdigest()
    result = _run_production(
        production_inputs["tmp"] / "second",
        {**production_inputs, "installer": installer},
        [],
        overwrite={"installer_sha256": sha},
    )
    assert result.returncode != 0
    assert "missing_timestamp" in result.stderr


def test_production_rejects_installer_changed_after_signing(
    production_inputs: dict, tmp_path: Path
) -> None:
    result = _run_production(
        tmp_path,
        production_inputs,
        [],
        overwrite={"installer_sha256": "0" * 64},
    )
    assert result.returncode != 0
    assert "installer_changed_after_signing" in result.stderr


def test_production_rejects_publisher_mismatch(production_inputs: dict, tmp_path: Path) -> None:
    result = _run_production(
        tmp_path, production_inputs, [], overwrite={"publisher": "Другой издатель"}
    )
    assert result.returncode != 0
    assert "publisher_mismatch" in result.stderr


def test_production_rejects_chain_outside_release_roots(
    production_inputs: dict, tmp_path: Path
) -> None:
    result = _run_production(
        tmp_path,
        production_inputs,
        ["--authenticode-roots", str(production_inputs["outside_roots"])],
    )
    assert result.returncode != 0
    assert "untrusted_root" in result.stderr


def test_production_happy_path_with_two_verified_signatures(
    production_inputs: dict, tmp_path: Path
) -> None:
    result = _run_production(tmp_path, production_inputs, [])
    assert result.returncode == 0, result.stderr
    out_dir = tmp_path / "dist" / "channel"
    manifest = json.loads((out_dir / "update-channel.json").read_text(encoding="utf-8"))
    assert manifest["version"] == "0.14.0"
    metadata = json.loads((out_dir / "release-metadata.json").read_text(encoding="utf-8"))
    assert metadata["release_mode"] == "production"
    assert metadata["authenticode"]["signtool_verify_ok"] is True
    assert metadata["authenticode"]["timestamp_present"] is True
    assert metadata["authenticode"]["publisher"] == PUBLISHER
    assert (
        metadata["authenticode"]["signer_thumbprint_sha256"]
        == production_inputs["attestation"]["signer_thumbprint_sha256"]
    )
    assert metadata["trust_store"]["keys"][0]["key_id"] == "pilot-release-2026"
    # Ни приватного ключа Ed25519, ни приватного ключа Authenticode в отчёте.
    rendered = json.dumps(metadata)
    assert "PRIVATE KEY" not in rendered
    assert (TESTDATA / "test_key.priv").read_text().strip()[:32] not in rendered
    sums = (out_dir / "SHA256SUMS").read_text(encoding="utf-8")
    assert "release-metadata.json" in sums
    assert "hr-manager-windows-0.14.0.zip" in sums
    # Независимая проверка канала публичным ключом клиента.
    verification = subprocess.run(
        [
            sys.executable,
            str(RELEASE / "verify_channel.py"),
            "--manifest",
            str(out_dir / "update-channel.json"),
            "--public-key",
            production_inputs["public_key"],
        ],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert verification.returncode == 0, verification.stderr


def test_test_mode_still_works_without_production_input(
    production_inputs: dict, tmp_path: Path
) -> None:
    """CI-PR режим: fixture-ключи, без Authenticode и production secrets."""
    snapshot = tmp_path / "app"
    shutil.copytree(TESTDATA / "snapshot", snapshot)
    result = subprocess.run(
        [
            sys.executable,
            str(RELEASE / "publish_channel.py"),
            "--snapshot",
            str(snapshot),
            "--version",
            "0.14.0",
            "--release-sha",
            "2" * 40,
            "--package-url",
            PACKAGE_URL,
            "--minimum-supported-version",
            "0.13.0",
            "--public-keys-json",
            str(TESTDATA / "trusted_keys.json"),
            "--private-key",
            str(TESTDATA / "test_key.priv"),
            "--key-id",
            "pilot-test-key",
            "--out-dir",
            str(tmp_path / "out"),
            "--release-mode",
            "test",
        ],
        capture_output=True,
        text=True,
        timeout=180,
    )
    assert result.returncode == 0, result.stderr
    metadata = json.loads((tmp_path / "out" / "release-metadata.json").read_text(encoding="utf-8"))
    assert metadata["release_mode"] == "test"
    assert metadata["authenticode"] is None


def test_package_excludes_fixture_private_keys(production_inputs: dict, tmp_path: Path) -> None:
    """Fixture-приватные ключи Phase 13 не уезжают в публикуемом пакете."""
    snapshot = tmp_path / "app"
    shutil.copytree(TESTDATA / "snapshot", snapshot)
    (snapshot / "infra" / "release" / "testdata").mkdir(parents=True, exist_ok=True)
    (snapshot / "infra" / "release" / "testdata" / "test_key.priv").write_text(
        "a" * 64 + "\n", encoding="utf-8"
    )
    result = subprocess.run(
        [
            sys.executable,
            str(RELEASE / "publish_channel.py"),
            "--snapshot",
            str(snapshot),
            "--version",
            "0.14.0",
            "--release-sha",
            "2" * 40,
            "--package-url",
            PACKAGE_URL,
            "--minimum-supported-version",
            "0.13.0",
            "--public-keys-json",
            str(TESTDATA / "trusted_keys.json"),
            "--private-key",
            str(TESTDATA / "test_key.priv"),
            "--key-id",
            "pilot-test-key",
            "--out-dir",
            str(tmp_path / "out"),
        ],
        capture_output=True,
        text=True,
        timeout=180,
    )
    assert result.returncode == 0, result.stderr
    import zipfile

    with zipfile.ZipFile(tmp_path / "out" / "hr-manager-windows-0.14.0.zip") as archive:
        names = archive.namelist()
    assert not any("testdata" in name for name in names), names


def test_package_refuses_private_material_in_snapshot(
    production_inputs: dict, tmp_path: Path
) -> None:
    snapshot = tmp_path / "app"
    shutil.copytree(TESTDATA / "snapshot", snapshot)
    (snapshot / "backend" / "leaked.key").write_text(
        "-----BEGIN PRIVATE KEY-----\nMIIB\n-----END PRIVATE KEY-----\n", encoding="utf-8"
    )
    result = subprocess.run(
        [
            sys.executable,
            str(RELEASE / "publish_channel.py"),
            "--snapshot",
            str(snapshot),
            "--version",
            "0.14.0",
            "--release-sha",
            "2" * 40,
            "--package-url",
            PACKAGE_URL,
            "--minimum-supported-version",
            "0.13.0",
            "--public-keys-json",
            str(TESTDATA / "trusted_keys.json"),
            "--private-key",
            str(TESTDATA / "test_key.priv"),
            "--key-id",
            "pilot-test-key",
            "--out-dir",
            str(tmp_path / "out"),
        ],
        capture_output=True,
        text=True,
        timeout=180,
    )
    assert result.returncode != 0
    assert "private_material_in_package" in result.stderr


@pytest.mark.parametrize(
    ("overwrite", "code"),
    [
        ({"trust_store": None}, "missing_trust_store_attestation"),
        (
            {"trust_store": {"sha256": "0" * 64, "keys": []}},
            "installer_trust_store_mismatch",
        ),
    ],
)
def test_production_requires_matching_embedded_trust_store(
    production_inputs: dict, tmp_path: Path, overwrite: dict, code: str
) -> None:
    """Attestation обязан подтвердить встроенный trust store; подмена — отказ."""
    result = _run_production(tmp_path, production_inputs, [], overwrite=overwrite)
    assert result.returncode != 0
    assert code in result.stderr


def test_embedded_trust_store_must_match_release_store(
    production_inputs: dict, tmp_path: Path
) -> None:
    snapshot = tmp_path / "app"
    shutil.copytree(TESTDATA / "snapshot", snapshot)
    embedded = snapshot / "infra" / "release" / "trust-store.json"
    embedded.parent.mkdir(parents=True, exist_ok=True)
    embedded.write_text(
        json.dumps({"someone-else": {"key": _hex_key(50), "revoked": False}}), encoding="utf-8"
    )
    result = _run_production(tmp_path, production_inputs, [])
    assert result.returncode != 0
    assert "embedded_trust_store_mismatch" in result.stderr
