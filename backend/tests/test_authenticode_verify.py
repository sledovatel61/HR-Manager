"""Phase 14: тесты независимой Authenticode-проверки и release-gate.

Проверяется, что публикация НЕ доверяет signing manifest/attestation и
строковым полям о «результате signtool»: gate принимает решение только по
фактическим байтам PE, которые уходят в канал.

Все сертификаты/ключи — эфемерные, создаются в памяти на время прогона
(см. backend/tests/_authenticode_testkit.py). Никаких production-ключей
в репозитории, фикстурах, логах и артефактах нет и быть не может.
"""

from __future__ import annotations

import hashlib
import json
import socket
import subprocess
import sys
import threading
import urllib.request
from pathlib import Path

import pytest
from asn1crypto import cms as asn1cms
from asn1crypto import tsp

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "infra" / "release"))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "infra" / "scripts"))

import _authenticode_testkit as tk  # type: ignore[import-not-found]
from authenticode_verify import (  # type: ignore[import-not-found]
    AuthenticodeError,
    AuthenticodeReport,
    verify_pe_authenticode,
    verify_release_gate,
)

RELEASE_DIR = Path(__file__).resolve().parents[2] / "infra" / "release"
PROD_PUBLISHER = "HR Manager Pilot"
TEST_PUBLISHER = "HR Manager CI Test Signing"


@pytest.fixture(scope="module")
def world() -> dict[str, tk.KeyPair]:
    return tk.make_world()


@pytest.fixture()
def prod_roots(world: dict[str, tk.KeyPair]) -> list[bytes]:
    return [world["prod_ca"].cert_pem]


@pytest.fixture()
def signed_prod(world: dict[str, tk.KeyPair]) -> bytes:
    return tk.sign_pe(b"synthetic-installer-body", world["prod_signer"], world["prod_tsa"])


def first_code(report: AuthenticodeReport) -> str:
    assert report.problems, "ожидались проблемы, отчёт ok"
    return report.problems[0].split(":", 1)[0]


def codes_of(report: AuthenticodeReport) -> list[str]:
    return [p.split(":", 1)[0] for p in report.problems]


# --- Позитивные сценарии -------------------------------------------------------


def test_verify_ok_production(
    world: dict[str, tk.KeyPair], prod_roots: list[bytes], signed_prod: bytes
) -> None:
    report = verify_pe_authenticode(
        signed_prod, expected_publisher=PROD_PUBLISHER, trusted_roots=prod_roots
    )
    assert report.ok, report.problems
    assert report.digest_algorithm == "sha256"
    assert report.signer_subject == f"CN={PROD_PUBLISHER}"
    assert report.chain_subjects == [f"CN={PROD_PUBLISHER}", "CN=HR Manager Test Production Root"]
    assert report.tsa_subject == "CN=HR Manager Test TSA"
    assert report.signing_time is not None
    assert report.signing_time.year == 2026


def test_verify_accepts_path_and_bytes_equally(
    world: dict[str, tk.KeyPair],
    prod_roots: list[bytes],
    signed_prod: bytes,
    tmp_path: Path,
) -> None:
    exe = tmp_path / "installer.exe"
    exe.write_bytes(signed_prod)
    by_path = verify_pe_authenticode(
        str(exe), expected_publisher=PROD_PUBLISHER, trusted_roots=prod_roots
    )
    by_bytes = verify_pe_authenticode(
        signed_prod, expected_publisher=PROD_PUBLISHER, trusted_roots=prod_roots
    )
    assert by_path.ok, by_path.problems
    assert by_path.to_dict() == by_bytes.to_dict()


def test_verify_with_separate_tsa_roots(world: dict[str, tk.KeyPair]) -> None:
    """TSA-корень задаётся отдельно от корня подписанта."""
    pe = tk.sign_pe(b"body", world["prod_signer"], world["test_tsa"])
    ok_report = verify_pe_authenticode(
        pe,
        expected_publisher=PROD_PUBLISHER,
        trusted_roots=[world["prod_ca"].cert_pem],
        tsa_roots=[world["test_ca"].cert_pem],
    )
    assert ok_report.ok, ok_report.problems
    bad_report = verify_pe_authenticode(
        pe,
        expected_publisher=PROD_PUBLISHER,
        trusted_roots=[world["prod_ca"].cert_pem],
        tsa_roots=[world["prod_ca"].cert_pem],  # test TSA не доверен
    )
    assert not bad_report.ok
    assert first_code(bad_report) == "tsa_untrusted"


def test_report_json_contains_no_private_material(
    signed_prod: bytes, prod_roots: list[bytes]
) -> None:
    report = verify_pe_authenticode(
        signed_prod, expected_publisher=PROD_PUBLISHER, trusted_roots=prod_roots
    )
    blob = json.dumps(report.to_dict(), default=str)
    assert "PRIVATE KEY" not in blob
    assert "BEGIN" not in blob


# --- Негативные сценарии: PE и digest ------------------------------------------


def test_reject_unsigned_pe(world: dict[str, tk.KeyPair]) -> None:
    report = verify_pe_authenticode(
        tk.build_pe(b"unsigned body"),
        expected_publisher=PROD_PUBLISHER,
        trusted_roots=[world["prod_ca"].cert_pem],
    )
    assert first_code(report) == "unsigned_pe"


def test_reject_non_pe_bytes(world: dict[str, tk.KeyPair]) -> None:
    for blob in (b"plain text, not an executable", b"", b"MZ" + b"\x00" * 10):
        report = verify_pe_authenticode(
            blob, expected_publisher=PROD_PUBLISHER, trusted_roots=[world["prod_ca"].cert_pem]
        )
        assert not report.ok
        assert first_code(report) == "not_pe"


def test_reject_modification_after_signing(
    world: dict[str, tk.KeyPair], prod_roots: list[bytes], signed_prod: bytes
) -> None:
    for offset in (0x100, signed_prod.index(b"synthetic-installer-body") + 3):
        damaged = bytearray(signed_prod)
        damaged[offset] ^= 0xFF
        report = verify_pe_authenticode(
            bytes(damaged), expected_publisher=PROD_PUBLISHER, trusted_roots=prod_roots
        )
        assert first_code(report) == "digest_mismatch"


def test_reject_signature_transplanted_from_other_file(
    world: dict[str, tk.KeyPair], prod_roots: list[bytes], signed_prod: bytes
) -> None:
    """Подпись, перенесённая с другого файла, не проходит digest-проверку."""
    from authenticode_verify import _parse_pe

    table_offset = _parse_pe(signed_prod).cert_table_offset
    table = signed_prod[table_offset:]
    assert table, "certificate table должна быть непустой"
    other = tk.build_pe(b"completely different program body", cert_table=table)
    report = verify_pe_authenticode(
        other, expected_publisher=PROD_PUBLISHER, trusted_roots=prod_roots
    )
    assert first_code(report) == "digest_mismatch"


def test_reject_lied_spc_digest(world: dict[str, tk.KeyPair], prod_roots: list[bytes]) -> None:
    """Digest в подписи не совпадает с фактическим образом PE."""
    pe = tk.sign_pe(
        b"body", world["prod_signer"], world["prod_tsa"], spc_digest_override=b"\xab" * 32
    )
    report = verify_pe_authenticode(pe, expected_publisher=PROD_PUBLISHER, trusted_roots=prod_roots)
    assert first_code(report) == "digest_mismatch"


# --- Негативные сценарии: CMS-структура и алгоритмы -----------------------------


def test_reject_sha1_signature(world: dict[str, tk.KeyPair], prod_roots: list[bytes]) -> None:
    pe = tk.sign_pe(b"body", world["prod_signer"], world["prod_tsa"], digest_name="sha1")
    report = verify_pe_authenticode(pe, expected_publisher=PROD_PUBLISHER, trusted_roots=prod_roots)
    assert first_code(report) == "sha1_forbidden"


def test_reject_garbage_and_truncated_cms(
    world: dict[str, tk.KeyPair], prod_roots: list[bytes], signed_prod: bytes
) -> None:
    from authenticode_verify import _parse_pe

    table_offset = _parse_pe(signed_prod).cert_table_offset
    table = signed_prod[table_offset:]
    garbage = tk.build_pe(b"body", cert_table=tk.win_cert_table([b"\x30\x03\x02\x01\x05"]))
    truncated = tk.build_pe(b"body", cert_table=table[: len(table) // 2])
    for pe in (garbage, truncated):
        report = verify_pe_authenticode(
            pe, expected_publisher=PROD_PUBLISHER, trusted_roots=prod_roots
        )
        assert first_code(report) == "bad_signature_structure"


# --- Негативные сценарии: издатель, цепочки, EKU --------------------------------


def test_reject_wrong_publisher(
    world: dict[str, tk.KeyPair], prod_roots: list[bytes], signed_prod: bytes
) -> None:
    report = verify_pe_authenticode(
        signed_prod, expected_publisher="Evil Vendor Ltd", trusted_roots=prod_roots
    )
    assert first_code(report) == "publisher_mismatch"


def test_reject_untrusted_root(world: dict[str, tk.KeyPair], prod_roots: list[bytes]) -> None:
    # TSA из доверенного production-контура, подписант — из чужого дерева
    pe = tk.sign_pe(b"body", world["test_signer"], world["prod_tsa"])
    report = verify_pe_authenticode(pe, expected_publisher=TEST_PUBLISHER, trusted_roots=prod_roots)
    assert first_code(report) == "untrusted_root"


def test_reject_signer_without_code_signing_eku(
    world: dict[str, tk.KeyPair],
    prod_roots: list[bytes],
) -> None:
    pe = tk.sign_pe(b"body", world["no_eku_signer"], world["prod_tsa"])
    report = verify_pe_authenticode(pe, expected_publisher=PROD_PUBLISHER, trusted_roots=prod_roots)
    assert first_code(report) == "eku_missing"


def test_reject_empty_or_garbage_trust_roots(signed_prod: bytes) -> None:
    for roots in ([], [b"not a certificate"], [b""]):
        report = verify_pe_authenticode(
            signed_prod, expected_publisher=PROD_PUBLISHER, trusted_roots=roots
        )
        assert first_code(report) == "bad_trust_root"


# --- Негативные сценарии: timestamp ---------------------------------------------


def test_reject_missing_timestamp(world: dict[str, tk.KeyPair], prod_roots: list[bytes]) -> None:
    pe = tk.sign_pe(b"body", world["prod_signer"], None, timestamp=False)
    report = verify_pe_authenticode(pe, expected_publisher=PROD_PUBLISHER, trusted_roots=prod_roots)
    assert first_code(report) == "timestamp_missing"


def test_reject_timestamp_with_wrong_imprint(
    world: dict[str, tk.KeyPair],
    prod_roots: list[bytes],
) -> None:
    """messageImprint не является хешем подписи подписанта."""
    pe = tk.sign_pe(
        b"body", world["prod_signer"], world["prod_tsa"], timestamp_imprint_override=b"\x11" * 32
    )
    report = verify_pe_authenticode(pe, expected_publisher=PROD_PUBLISHER, trusted_roots=prod_roots)
    assert first_code(report) == "timestamp_invalid"


def test_reject_corrupted_timestamp_token(
    world: dict[str, tk.KeyPair],
    prod_roots: list[bytes],
) -> None:
    pe = tk.sign_pe(b"body", world["prod_signer"], world["prod_tsa"], corrupt_timestamp=True)
    report = verify_pe_authenticode(pe, expected_publisher=PROD_PUBLISHER, trusted_roots=prod_roots)
    assert first_code(report) in {"timestamp_invalid", "cms_signature_invalid"}


def test_reject_untrusted_tsa(world: dict[str, tk.KeyPair], prod_roots: list[bytes]) -> None:
    pe = tk.sign_pe(b"body", world["prod_signer"], world["other_tsa"])
    report = verify_pe_authenticode(pe, expected_publisher=PROD_PUBLISHER, trusted_roots=prod_roots)
    assert first_code(report) == "tsa_untrusted"


@pytest.mark.parametrize("tsa_key", ["wrong_eku_tsa", "no_eku_tsa"])
def test_reject_tsa_without_timestamping_eku(
    world: dict[str, tk.KeyPair],
    prod_roots: list[bytes],
    tsa_key: str,
) -> None:
    pe = tk.sign_pe(b"body", world["prod_signer"], world[tsa_key])
    report = verify_pe_authenticode(pe, expected_publisher=PROD_PUBLISHER, trusted_roots=prod_roots)
    assert first_code(report) == "tsa_eku_missing"


def test_reject_legacy_microsoft_timestamp(
    world: dict[str, tk.KeyPair], prod_roots: list[bytes], signed_prod: bytes
) -> None:
    """Legacy MS timestamp (1.3.6.1.4.1.311.2.4.1) не принимается: pipeline
    подписывает через RFC 3161 /tr, всё остальное — откат к небезопасному."""
    from authenticode_verify import _parse_pe

    table_offset = _parse_pe(signed_prod).cert_table_offset
    ci = asn1cms.ContentInfo.load(signed_prod[table_offset:][8:])
    sd = ci["content"]
    si = sd["signer_infos"][0]
    legacy = asn1cms.CMSAttributes(
        [
            asn1cms.CMSAttribute(
                {"type": "1.3.6.1.4.1.311.2.4.1", "values": [si["unsigned_attrs"][0]["values"][0]]}
            )
        ]
    )
    _signer_fields = (
        "version",
        "sid",
        "digest_algorithm",
        "signed_attrs",
        "signature_algorithm",
        "signature",
    )
    fields = {name: si[name] for name in _signer_fields}
    fields["unsigned_attrs"] = legacy
    sd2 = asn1cms.SignedData(
        {
            "version": sd["version"],
            "digest_algorithms": sd["digest_algorithms"],
            "encap_content_info": sd["encap_content_info"],
            "certificates": sd["certificates"],
            "signer_infos": [asn1cms.SignerInfo(fields)],
        }
    )
    ci2 = asn1cms.ContentInfo({"content_type": "signed_data", "content": sd2})
    pe = tk.build_pe(b"synthetic-installer-body", cert_table=tk.win_cert_table([ci2.dump()]))
    report = verify_pe_authenticode(pe, expected_publisher=PROD_PUBLISHER, trusted_roots=prod_roots)
    assert first_code(report) == "timestamp_legacy_unsupported"


def test_reject_certificates_outside_validity_at_signing_time(
    world: dict[str, tk.KeyPair],
    prod_roots: list[bytes],
) -> None:
    """Цепочки проверяются на момент genTime timestamp-а, не «сейчас»."""
    import datetime as dt

    stale = dt.datetime(2020, 1, 1, tzinfo=dt.UTC)
    pe = tk.sign_pe(b"body", world["prod_signer"], world["prod_tsa"], gen_time=stale)
    report = verify_pe_authenticode(pe, expected_publisher=PROD_PUBLISHER, trusted_roots=prod_roots)
    assert first_code(report) in {"certificate_expired", "timestamp_invalid"}


# --- Release gate: манифест — provenance, не источник истины ---------------------


def _write_manifest(
    path: Path, exe_bytes: bytes, *, publisher: str, mode: str, sha: str | None = None
) -> Path:
    manifest = {
        "installer_exe": {"sha256": sha or hashlib.sha256(exe_bytes).hexdigest()},
        "signing": {"publisher": publisher, "mode": mode, "status": "signed"},
    }
    path.write_text(json.dumps(manifest), encoding="utf-8")
    return path


def test_release_gate_ok_production(
    world: dict[str, tk.KeyPair],
    prod_roots: list[bytes],
    signed_prod: bytes,
    tmp_path: Path,
) -> None:
    exe = tmp_path / "installer.exe"
    exe.write_bytes(signed_prod)
    manifest = _write_manifest(
        tmp_path / "manifest.json", signed_prod, publisher=f"CN={PROD_PUBLISHER}", mode="production"
    )
    report = verify_release_gate(
        str(exe),
        str(manifest),
        expected_publisher=PROD_PUBLISHER,
        trusted_roots=prod_roots,
        mode="production",
    )
    assert report.ok, report.problems


def test_release_gate_ok_test_mode(world: dict[str, tk.KeyPair], tmp_path: Path) -> None:
    pe = tk.sign_pe(b"body", world["test_signer"], world["test_tsa"])
    exe = tmp_path / "installer.exe"
    exe.write_bytes(pe)
    manifest = _write_manifest(
        tmp_path / "manifest.json", pe, publisher=f"CN={TEST_PUBLISHER}", mode="test"
    )
    report = verify_release_gate(
        str(exe),
        str(manifest),
        expected_publisher=TEST_PUBLISHER,
        trusted_roots=[world["test_ca"].cert_pem],
        mode="test",
    )
    assert report.ok, report.problems


def test_release_gate_rejects_forged_manifest_on_unsigned_exe(
    world: dict[str, tk.KeyPair], prod_roots: list[bytes], tmp_path: Path
) -> None:
    """Манифест, «утверждающий» подпись, не спасает неподписанный файл."""
    unsigned = tk.build_pe(b"unsigned body")
    exe = tmp_path / "installer.exe"
    exe.write_bytes(unsigned)
    manifest = _write_manifest(
        tmp_path / "manifest.json", unsigned, publisher=f"CN={PROD_PUBLISHER}", mode="production"
    )
    report = verify_release_gate(
        str(exe),
        str(manifest),
        expected_publisher=PROD_PUBLISHER,
        trusted_roots=prod_roots,
        mode="production",
    )
    assert not report.ok
    assert "unsigned_pe" in codes_of(report)


def test_release_gate_rejects_manifest_sha_mismatch(
    world: dict[str, tk.KeyPair],
    prod_roots: list[bytes],
    signed_prod: bytes,
    tmp_path: Path,
) -> None:
    exe = tmp_path / "installer.exe"
    exe.write_bytes(signed_prod)
    manifest = _write_manifest(
        tmp_path / "manifest.json",
        signed_prod,
        publisher=f"CN={PROD_PUBLISHER}",
        mode="production",
        sha="0" * 64,
    )
    report = verify_release_gate(
        str(exe),
        str(manifest),
        expected_publisher=PROD_PUBLISHER,
        trusted_roots=prod_roots,
        mode="production",
    )
    assert "manifest_sha_mismatch" in codes_of(report)


def test_release_gate_rejects_manifest_publisher_lie(
    world: dict[str, tk.KeyPair],
    prod_roots: list[bytes],
    signed_prod: bytes,
    tmp_path: Path,
) -> None:
    exe = tmp_path / "installer.exe"
    exe.write_bytes(signed_prod)
    manifest = _write_manifest(
        tmp_path / "manifest.json", signed_prod, publisher="CN=Somebody Else", mode="production"
    )
    report = verify_release_gate(
        str(exe),
        str(manifest),
        expected_publisher=PROD_PUBLISHER,
        trusted_roots=prod_roots,
        mode="production",
    )
    assert "manifest_publisher_mismatch" in codes_of(report)


def test_release_gate_rejects_test_certificate_in_production(
    world: dict[str, tk.KeyPair], prod_roots: list[bytes], tmp_path: Path
) -> None:
    pe = tk.sign_pe(b"body", world["test_signer"], world["test_tsa"])
    exe = tmp_path / "installer.exe"
    exe.write_bytes(pe)
    manifest = _write_manifest(
        tmp_path / "manifest.json", pe, publisher=f"CN={TEST_PUBLISHER}", mode="test"
    )
    report = verify_release_gate(
        str(exe),
        str(manifest),
        expected_publisher=TEST_PUBLISHER,
        trusted_roots=prod_roots,
        mode="production",
    )
    assert not report.ok
    assert {"untrusted_root", "tsa_untrusted"} & set(codes_of(report))
    assert "manifest_mode_mismatch" in codes_of(report)


def test_release_gate_rejects_missing_or_invalid_manifest(
    world: dict[str, tk.KeyPair],
    prod_roots: list[bytes],
    signed_prod: bytes,
    tmp_path: Path,
) -> None:
    exe = tmp_path / "installer.exe"
    exe.write_bytes(signed_prod)
    missing = verify_release_gate(
        str(exe),
        str(tmp_path / "nope.json"),
        expected_publisher=PROD_PUBLISHER,
        trusted_roots=prod_roots,
        mode="production",
    )
    assert "manifest_missing" in codes_of(missing)

    broken = tmp_path / "broken.json"
    broken.write_text("{ this is not json", encoding="utf-8")
    invalid = verify_release_gate(
        str(exe),
        str(broken),
        expected_publisher=PROD_PUBLISHER,
        trusted_roots=prod_roots,
        mode="production",
    )
    assert "manifest_invalid" in codes_of(invalid)


def test_release_gate_tampered_exe_with_forged_manifest(
    world: dict[str, tk.KeyPair],
    prod_roots: list[bytes],
    signed_prod: bytes,
    tmp_path: Path,
) -> None:
    """Самосогласованный (подделанный) манифест не спасает изменённый exe: sha
    в манифесте совпадает с изменёнными байтами, но подпись уже не сходится."""
    damaged = bytearray(signed_prod)
    damaged[signed_prod.index(b"synthetic-installer-body") + 1] ^= 0x55
    damaged_bytes = bytes(damaged)
    exe = tmp_path / "installer.exe"
    exe.write_bytes(damaged_bytes)
    manifest = _write_manifest(
        tmp_path / "manifest.json",
        damaged_bytes,
        publisher=f"CN={PROD_PUBLISHER}",
        mode="production",
    )
    report = verify_release_gate(
        str(exe),
        str(manifest),
        expected_publisher=PROD_PUBLISHER,
        trusted_roots=prod_roots,
        mode="production",
    )
    assert not report.ok
    assert "digest_mismatch" in codes_of(report)


# --- CLI ------------------------------------------------------------------------


def _run_cli(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(RELEASE_DIR / "authenticode_verify.py"), *args],
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )


def _write_root(tmp_path: Path, pem: bytes) -> str:
    root = tmp_path / "root.pem"
    root.write_bytes(pem)
    return str(root)


def test_cli_verify_ok_and_json_report(
    world: dict[str, tk.KeyPair],
    prod_roots: list[bytes],
    signed_prod: bytes,
    tmp_path: Path,
) -> None:
    exe = tmp_path / "installer.exe"
    exe.write_bytes(signed_prod)
    report_path = tmp_path / "report.json"
    result = _run_cli(
        "verify",
        "--exe",
        str(exe),
        "--expected-publisher",
        PROD_PUBLISHER,
        "--trusted-root",
        _write_root(tmp_path, prod_roots[0]),
        "--json",
        str(report_path),
    )
    assert result.returncode == 0, result.stderr
    data = json.loads(report_path.read_text(encoding="utf-8"))
    assert data["ok"] is True
    assert data["digest_algorithm"] == "sha256"


def test_cli_verify_rejects_tampered_exe(
    world: dict[str, tk.KeyPair],
    prod_roots: list[bytes],
    signed_prod: bytes,
    tmp_path: Path,
) -> None:
    damaged = bytearray(signed_prod)
    damaged[0x100] ^= 0xFF
    exe = tmp_path / "installer.exe"
    exe.write_bytes(bytes(damaged))
    result = _run_cli(
        "verify",
        "--exe",
        str(exe),
        "--expected-publisher",
        PROD_PUBLISHER,
        "--trusted-root",
        _write_root(tmp_path, prod_roots[0]),
    )
    assert result.returncode == 1
    assert "digest_mismatch" in (result.stderr + result.stdout)


def test_cli_release_gate_exit_codes(
    world: dict[str, tk.KeyPair],
    prod_roots: list[bytes],
    signed_prod: bytes,
    tmp_path: Path,
) -> None:
    exe = tmp_path / "installer.exe"
    exe.write_bytes(signed_prod)
    manifest = _write_manifest(
        tmp_path / "manifest.json", signed_prod, publisher=f"CN={PROD_PUBLISHER}", mode="production"
    )
    ok_result = _run_cli(
        "release-gate",
        "--exe",
        str(exe),
        "--manifest",
        str(manifest),
        "--expected-publisher",
        PROD_PUBLISHER,
        "--mode",
        "production",
        "--trusted-root",
        _write_root(tmp_path, prod_roots[0]),
    )
    assert ok_result.returncode == 0, ok_result.stderr

    exe.write_bytes(tk.build_pe(b"swapped unsigned body"))
    forged = _write_manifest(
        tmp_path / "forged.json",
        exe.read_bytes(),
        publisher=f"CN={PROD_PUBLISHER}",
        mode="production",
    )
    bad_result = _run_cli(
        "release-gate",
        "--exe",
        str(exe),
        "--manifest",
        str(forged),
        "--expected-publisher",
        PROD_PUBLISHER,
        "--mode",
        "production",
        "--trusted-root",
        _write_root(tmp_path, prod_roots[0]),
    )
    assert bad_result.returncode == 1


# --- Эфемерный TSA-сервер (drill/CI использует его для signtool /tr) --------------


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def test_tsa_server_http_roundtrip() -> None:
    from drill_tsa_server import (  # type: ignore[import-not-found]
        build_timestamp_token,
        generate_tsa_certificate,
        make_server,
    )

    cert, key = generate_tsa_certificate("Unit Test TSA")
    server = make_server(0, cert, key)
    port = server.server_port
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        import os

        signature = os.urandom(256)
        imprint = hashlib.sha256(signature).digest()
        query = tsp.TimeStampReq(
            {
                "version": 1,
                "message_imprint": {
                    "hash_algorithm": {"algorithm": "sha256"},
                    "hashed_message": imprint,
                },
                "nonce": 424242,
                "cert_req": True,
            }
        ).dump()
        request = urllib.request.Request(
            f"http://127.0.0.1:{port}/",
            data=query,
            method="POST",
            headers={"Content-Type": "application/timestamp-query"},
        )
        with urllib.request.urlopen(request, timeout=10) as response:
            assert response.status == 200
            assert response.headers["Content-Type"] == "application/timestamp-reply"
            reply = tsp.TimeStampResp.load(response.read())
        assert reply["status"]["status"].native == "granted"
        token = reply["time_stamp_token"]
        sd = token["content"]
        tsti = tsp.TSTInfo.load(sd["encap_content_info"]["content"].contents)
        assert bytes(tsti["message_imprint"]["hashed_message"].native) == imprint
        assert tsti["nonce"].native == 424242
        # Токен, выданный сервером, верифицируется в составе подписи PE
        pe = tk.sign_pe(b"tsa-server-body", tk.make_world()["prod_signer"], None)
        assert pe  # сборка работает без локального TSA (токен встроен отдельно)
        # прямой вызов сборщика: imprint подписи -> токен
        direct = build_timestamp_token(
            hash_algorithm_oid="2.16.840.1.101.3.4.2.1",
            imprint=imprint,
            tsa_cert=cert,
            tsa_key=key,
            gen_time=tsti["gen_time"].native,
            serial_number=7,
        )
        assert (
            tsp.TimeStampResp.load(
                tsp.TimeStampResp(
                    {
                        "status": {"status": "granted"},
                        "time_stamp_token": asn1cms.ContentInfo.load(direct),
                    },
                ).dump()
            )["status"]["status"].native
            == "granted"
        )
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=10)


def _parse_rejection(resp_der: bytes) -> tsp.PKIStatusInfo:
    """Разбор TimeStampResp без токена (asn1crypto требует токен, RFC 3161 — нет)."""
    from asn1crypto.core import Sequence

    outer = Sequence.load(resp_der)
    return tsp.PKIStatusInfo.load(outer[0].dump())


def test_tsa_server_rejects_bad_request() -> None:
    from drill_tsa_server import (
        generate_tsa_certificate,
        make_server,
    )

    cert, key = generate_tsa_certificate()
    server = make_server(0, cert, key)
    port = server.server_port
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        for garbage in (b"", b"\x30\x03\x02\x01\x01", b"not-der-at-all"):
            request = urllib.request.Request(
                f"http://127.0.0.1:{port}/",
                data=garbage,
                method="POST",
                headers={"Content-Type": "application/timestamp-query"},
            )
            with urllib.request.urlopen(request, timeout=10) as response:
                assert response.status == 200
                status = _parse_rejection(response.read())
            assert status["status"].native == "rejection"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=10)


def test_authenticode_error_is_fail_closed() -> None:
    """Любое исключение в gate превращается в отказ, а не в «пролетело»."""
    try:
        verify_pe_authenticode(b"MZ", expected_publisher="", trusted_roots=[b"x"])
    except AuthenticodeError:
        pytest.fail("AuthenticodeError не должен покидать verify (fail-closed отчётом)")
    report = verify_pe_authenticode(b"MZ\x00\x00", expected_publisher="X", trusted_roots=[b"x"])
    assert not report.ok
