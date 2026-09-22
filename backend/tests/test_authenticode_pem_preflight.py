"""Pre-flight закреплённых Authenticode PEM: missing/empty/invalid/valid.

Публикация production-канала и разбор PEM не должны зависеть от production
PFX, реального TSA или GitHub secrets. Ephemeral CA создаётся в тесте и
содержит только публичный сертификат в PEM-фикстурах.
"""

from __future__ import annotations

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
SIGN_PS1 = REPO / "installer" / "sign.ps1"
sys.path.insert(0, str(RELEASE))

from authenticode import (  # type: ignore[import-not-found]  # noqa: E402
    AuthentiCodeError,
    load_pem_certificates,
)
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
NOT_A_CERT = "HRM-NOT-A-CERT-MARKER"


def _ephemeral_signing_key(tmp_path: Path) -> tuple[Path, Path]:
    import base64

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
    return private_path, trust_store


def _production_inputs(tmp_path: Path) -> dict[str, Path]:
    from cryptography.hazmat.primitives import hashes

    authority = create_test_authority(PUBLISHER)
    installer = tmp_path / "HR-Manager-Setup-0.14.0.exe"
    installer.write_bytes(sign_test_pe(make_test_pe(), authority))
    installer_sha = hashlib.sha256(installer.read_bytes()).hexdigest()
    private_key, trust_store = _ephemeral_signing_key(tmp_path)
    store_data = json.loads(trust_store.read_text(encoding="utf-8"))
    attestation = {
        "mode": "production",
        "authenticode_present": True,
        "signtool_verify_ok": True,
        "timestamp_present": True,
        "publisher": PUBLISHER,
        "installer_sha256": installer_sha,
        "signer_thumbprint_sha256": authority.leaf_certificate.fingerprint(hashes.SHA256()).hex(),
        "tool": "installer/sign.ps1",
        "trust_store": {
            "file": "trust-store.json",
            "sha256": trust_store_sha256(store_data),
            "keys": describe_trust_store(store_data),
        },
    }
    roots = tmp_path / "authenticode-roots.pem"
    roots.write_bytes(authority.ca_pem())
    timestamp_roots = tmp_path / "authenticode-timestamp-roots.pem"
    timestamp_roots.write_bytes(authority.ca_pem())
    attestation_path = tmp_path / "attestation.json"
    attestation_path.write_text(json.dumps(attestation), encoding="utf-8")
    return {
        "installer": installer,
        "roots": roots,
        "timestamp_roots": timestamp_roots,
        "trust_store": trust_store,
        "private_key": private_key,
        "attestation": attestation_path,
    }


def _run_publish(
    tmp_path: Path, inputs: dict[str, Path], extra: list[str]
) -> subprocess.CompletedProcess[str]:
    snapshot = tmp_path / "app"
    if not snapshot.exists():
        shutil.copytree(TESTDATA / "snapshot", snapshot)
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
        "Проверка pre-flight PEM.",
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
        str(inputs["attestation"]),
        "--authenticode-roots",
        str(inputs["roots"]),
        "--authenticode-timestamp-roots",
        str(inputs["timestamp_roots"]),
        "--trust-store",
        str(inputs["trust_store"]),
        *extra,
    ]
    return subprocess.run(command, capture_output=True, text=True, timeout=180)


def _assert_not_published(
    tmp_path: Path, result: subprocess.CompletedProcess[str], code: str
) -> None:
    assert result.returncode != 0
    assert f"ОШИБКА[{code}]" in result.stderr
    assert "Traceback" not in result.stderr
    assert "Traceback" not in result.stdout
    assert "канал собран" not in result.stdout
    assert "-----BEGIN CERTIFICATE-----" not in result.stderr
    assert NOT_A_CERT not in result.stderr
    assert "PRIVATE KEY" not in result.stderr
    out_dir = tmp_path / "dist" / "channel"
    assert not (out_dir / "update-channel.json").exists()
    assert not list(out_dir.glob("hr-manager-windows-*.zip"))


def test_load_pem_certificates_missing_file_is_fail_closed(tmp_path: Path) -> None:
    missing = tmp_path / "missing-signer.pem"
    with pytest.raises(AuthentiCodeError) as excinfo:
        load_pem_certificates(missing)
    assert excinfo.value.code == "missing_pem"
    assert "Traceback" not in str(excinfo.value)
    assert NOT_A_CERT not in str(excinfo.value)


def test_load_pem_certificates_unreadable_directory_is_fail_closed(tmp_path: Path) -> None:
    directory = tmp_path / "roots-dir"
    directory.mkdir()
    with pytest.raises(AuthentiCodeError) as excinfo:
        load_pem_certificates(directory)
    assert excinfo.value.code == "unreadable_pem"
    assert isinstance(excinfo.value.__cause__, OSError)


def test_load_pem_certificates_permission_error_is_unreadable(tmp_path: Path) -> None:
    """Реальный PermissionError при чтении PEM, не IsADirectoryError."""
    denied = tmp_path / "denied.pem"
    denied.write_bytes(b"not-a-certificate\n")
    denied.chmod(0)
    try:
        with pytest.raises(AuthentiCodeError) as excinfo:
            load_pem_certificates(denied)
    finally:
        denied.chmod(0o644)
    assert excinfo.value.code == "unreadable_pem"
    assert isinstance(excinfo.value.__cause__, PermissionError)
    assert not isinstance(excinfo.value.__cause__, FileNotFoundError)
    assert "Traceback" not in str(excinfo.value)
    assert "not-a-certificate" not in str(excinfo.value)


def test_load_pem_certificates_other_oserror_is_unreadable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Прочие OSError (не PermissionError и не FileNotFoundError) тоже fail closed."""
    target = tmp_path / "io-error.pem"
    target.write_bytes(b"pem")

    def _raise_io_error(self: Path) -> bytes:
        raise OSError(5, "Input/output error")

    monkeypatch.setattr(Path, "read_bytes", _raise_io_error)
    with pytest.raises(AuthentiCodeError) as excinfo:
        load_pem_certificates(target)
    assert excinfo.value.code == "unreadable_pem"
    assert isinstance(excinfo.value.__cause__, OSError)
    assert not isinstance(excinfo.value.__cause__, (FileNotFoundError, PermissionError))
    assert "Traceback" not in str(excinfo.value)


def test_load_pem_certificates_empty_file_is_bad_root(tmp_path: Path) -> None:
    empty = tmp_path / "empty.pem"
    empty.write_bytes(b"")
    with pytest.raises(AuthentiCodeError) as excinfo:
        load_pem_certificates(empty)
    assert excinfo.value.code == "bad_root"
    assert "пуст" in str(excinfo.value)

    whitespace = tmp_path / "whitespace.pem"
    whitespace.write_text("\n\n  \n", encoding="utf-8")
    with pytest.raises(AuthentiCodeError) as blank:
        load_pem_certificates(whitespace)
    assert blank.value.code == "bad_root"
    assert "пуст" in str(blank.value)


def test_load_pem_certificates_invalid_pem_is_bad_root(tmp_path: Path) -> None:
    garbage = tmp_path / "garbage.pem"
    garbage.write_text(f"{NOT_A_CERT}\nnot a certificate\n", encoding="utf-8")
    with pytest.raises(AuthentiCodeError) as excinfo:
        load_pem_certificates(garbage)
    assert excinfo.value.code == "bad_root"
    assert NOT_A_CERT not in str(excinfo.value)

    broken = tmp_path / "broken.pem"
    broken.write_text(
        "-----BEGIN CERTIFICATE-----\n@@@@\n-----END CERTIFICATE-----\n",
        encoding="utf-8",
    )
    with pytest.raises(AuthentiCodeError) as broken_info:
        load_pem_certificates(broken)
    assert broken_info.value.code == "bad_root"
    assert "некорректный PEM" in str(broken_info.value)
    assert "@@@@" not in str(broken_info.value)


def test_load_pem_certificates_rejects_bom_and_private_marker(tmp_path: Path) -> None:
    authority = create_test_authority("HR Manager PEM Preflight")
    bom = tmp_path / "bom.pem"
    bom.write_bytes(b"\xef\xbb\xbf" + authority.ca_pem())
    with pytest.raises(AuthentiCodeError) as bom_info:
        load_pem_certificates(bom)
    assert bom_info.value.code == "bad_root"
    assert "BOM" in str(bom_info.value)

    private_marker = tmp_path / "private-marker.pem"
    private_marker.write_bytes(
        b"-----BEGIN PRIVATE KEY-----\nAAAA\n-----END PRIVATE KEY-----\n" + authority.ca_pem()
    )
    with pytest.raises(AuthentiCodeError) as private_info:
        load_pem_certificates(private_marker)
    assert private_info.value.code == "bad_root"
    assert "приватный материал" in str(private_info.value)
    assert "AAAA" not in str(private_info.value)


def test_load_pem_certificates_accepts_valid_public_pem(tmp_path: Path) -> None:
    authority = create_test_authority("HR Manager PEM Preflight")
    valid = tmp_path / "valid.pem"
    valid.write_bytes(authority.ca_pem())
    loaded = load_pem_certificates(valid)
    assert len(loaded) == 1
    assert loaded[0].subject == authority.ca_certificate.subject

    chain = tmp_path / "chain.pem"
    chain.write_bytes(authority.chain_pem())
    assert len(load_pem_certificates(chain)) == 2


def test_verify_cli_missing_pem_has_stable_code(tmp_path: Path) -> None:
    pe = tmp_path / "unsigned.exe"
    pe.write_bytes(make_test_pe())
    missing = tmp_path / "missing.pem"
    result = subprocess.run(
        [
            sys.executable,
            str(RELEASE / "authenticode.py"),
            "verify",
            "--file",
            str(pe),
            "--trust-roots",
            str(missing),
        ],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 1
    assert "ОШИБКА[missing_pem]" in result.stderr
    assert "Traceback" not in result.stderr
    assert "Traceback" not in result.stdout


def test_production_refuses_missing_signer_pem(tmp_path: Path) -> None:
    inputs = _production_inputs(tmp_path)
    result = _run_publish(
        tmp_path, inputs, ["--authenticode-roots", str(tmp_path / "missing-signer.pem")]
    )
    _assert_not_published(tmp_path, result, "missing_pem")


def test_production_refuses_permission_denied_pem_without_traceback(tmp_path: Path) -> None:
    inputs = _production_inputs(tmp_path)
    denied = tmp_path / "denied-signer.pem"
    denied.write_bytes(b"not-published\n")
    denied.chmod(0)
    try:
        result = _run_publish(tmp_path, inputs, ["--authenticode-roots", str(denied)])
    finally:
        denied.chmod(0o644)
    assert result.returncode == 1
    _assert_not_published(tmp_path, result, "unreadable_pem")


def test_production_refuses_missing_timestamp_pem(tmp_path: Path) -> None:
    inputs = _production_inputs(tmp_path)
    result = _run_publish(
        tmp_path,
        inputs,
        ["--authenticode-timestamp-roots", str(tmp_path / "missing-tsa.pem")],
    )
    _assert_not_published(tmp_path, result, "missing_pem")


def test_production_refuses_empty_pem(tmp_path: Path) -> None:
    inputs = _production_inputs(tmp_path)
    empty = tmp_path / "empty-signer.pem"
    empty.write_bytes(b"")
    result = _run_publish(tmp_path, inputs, ["--authenticode-roots", str(empty)])
    _assert_not_published(tmp_path, result, "bad_root")
    assert "пуст" in result.stderr


def test_production_refuses_invalid_pem(tmp_path: Path) -> None:
    inputs = _production_inputs(tmp_path)
    invalid = tmp_path / "invalid-signer.pem"
    invalid.write_text(f"{NOT_A_CERT}\n", encoding="utf-8")
    result = _run_publish(tmp_path, inputs, ["--authenticode-roots", str(invalid)])
    _assert_not_published(tmp_path, result, "bad_root")


def test_production_publishes_when_pinned_pems_are_valid(tmp_path: Path) -> None:
    inputs = _production_inputs(tmp_path)
    result = _run_publish(tmp_path, inputs, [])
    assert result.returncode == 0, result.stderr
    assert "Traceback" not in result.stderr
    out_dir = tmp_path / "dist" / "channel"
    assert (out_dir / "update-channel.json").is_file()
    assert list(out_dir.glob("hr-manager-windows-*.zip"))


def _sign_code() -> str:
    text = SIGN_PS1.read_text(encoding="utf-8-sig")
    kept: list[str] = []
    for line in text.splitlines():
        stripped = line.lstrip()
        if stripped.startswith("#"):
            continue
        kept.append(line)
    return "\n".join(kept)


def test_sign_ps1_preflight_runs_before_signtool_and_fail_closes() -> None:
    """Порядок и отказ до подписи. pwsh в этом контуре нет — контракт структурный.

    Исполняемый разбор missing/empty/invalid/valid PEM проверяет Python-путь
    (тот же fail-closed контракт, которым publish_channel отказывает в выпуске).
    """
    code = _sign_code()
    call = code.index("Assert-HrmPinnedRootsPem -Mode $Mode")
    sign = code.index('Label "signtool sign"')
    assert call < sign

    def _last_column0(token: str, end: int) -> int:
        pos = -1
        start = 0
        needle = "\n" + token
        while True:
            found = code.find(needle, start, end)
            if found < 0:
                return pos
            pos = found + 1
            start = found + 1

    try_at = _last_column0("try {", call)
    catch_at = code.find("\ncatch {", sign)
    assert try_at >= 0
    assert catch_at > sign
    assert try_at < call < sign < catch_at
    assert "$scriptExitCode = 1" in code[catch_at : catch_at + 400]
    assert 'if ($Mode -ne "production") { return }' in code
    preflight = code.split("function Assert-HrmPemCertificateFile", 1)[1]
    preflight = preflight.split("function Assert-HrmPinnedRootsPem", 1)[0]
    assert "signtool" not in preflight.lower()
    for needle in (
        "production pre-flight: PEM не найден",
        "production pre-flight: PEM пуст",
        "production pre-flight: невалидный PEM",
        "production pre-flight: PEM содержит BOM",
        "production pre-flight: в PEM нет сертификата",
        "X509Certificate2",
        "PRIVATE KEY",
    ):
        assert needle in preflight
    assert "Write-Host" not in preflight
    assert "Thumbprint" not in preflight
    # Сообщения throw не интерполируют тело файла.
    for line in preflight.splitlines():
        if "throw" not in line:
            continue
        assert "$text" not in line
        assert "$compact" not in line
        assert "$bytes" not in line
        assert "$der" not in line
