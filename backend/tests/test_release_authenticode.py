"""Phase 14: тесты независимой Authenticode-проверки release pipeline.

Тестовый подписант (`sign_authenticode.py`) создаёт ephemeral сертификаты и
корректную структуру PKCS#7/Authenticode — ровно ту, которую формирует
`signtool`. Здесь проверяется, что проверяющий код (`authenticode.py`) её
принимает и НЕ принимает ни одну из типовых подмен:

* изменённый файл после подписи (Authenticode-хеш PE);
* чужая подпись, перенесённая в другой файл;
* отсутствующая/битая метка времени RFC 3161;
* чужой издатель;
* цепочка, не доводящаяся до доверенного корня;
* сертификат без codeSigning / TSA без timeStamping EKU.

Никакие production-секреты и реальные сертификаты здесь не используются.
"""

from __future__ import annotations

import base64
import copy
import hashlib
import json
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import hashes, serialization

REPO = Path(__file__).resolve().parents[2]
RELEASE = REPO / "infra" / "release"
# Release-инструментарий не является пакетом приложения: подключаем каталог
# напрямую (та же схема, что у publish_channel.py внутри workflow).
sys.path.insert(0, str(RELEASE))

import authenticode  # type: ignore[import-not-found]  # noqa: E402
from authenticode import (  # noqa: E402
    OID_PKCS9_MESSAGE_DIGEST,
    AuthentiCodeError,
    PeInfo,
    extract_pkcs7_blob,
    load_pem_certificates,
    parse_pe,
    parse_signed_data,
    pe_authenticode_digest,
    verify_authenticode,
    verify_signer_info,
)
from der import parse_one  # type: ignore[import-not-found]  # noqa: E402
from sign_authenticode import (  # type: ignore[import-not-found]  # noqa: E402
    EphemeralAuthority,
    build_signed_pkcs7,
    create_test_authority,
    embed_signature,
    make_test_pe,
    sign_test_pe,
)

PUBLISHER = "ООО Ромашка (тестовый издатель)"


@pytest.fixture(scope="module")
def authority() -> EphemeralAuthority:
    return create_test_authority(PUBLISHER)


@pytest.fixture()
def signed_pe(tmp_path: Path, authority: EphemeralAuthority) -> Path:
    path = tmp_path / "HR-Manager-Setup-test.exe"
    path.write_bytes(sign_test_pe(make_test_pe(), authority))
    return path


def test_verify_accepts_valid_signature_with_timestamp(
    signed_pe: Path, authority: EphemeralAuthority
) -> None:
    result = verify_authenticode(
        signed_pe,
        expected_publisher=PUBLISHER,
        require_timestamp=True,
        trust_roots=[authority.ca_certificate],
        timestamp_roots=[authority.ca_certificate],
    )
    assert result["signed"] is True
    assert result["publisher_match"] is True
    assert result["timestamp_present"] is True
    assert result["chain_verified"] is True
    assert result["timestamp_chain_verified"] is True
    assert result["digest_algorithm"] == "sha256"
    assert result["signer_thumbprint_sha256"] == (
        authority.leaf_certificate.fingerprint(
            __import__("cryptography").hazmat.primitives.hashes.SHA256()
        ).hex()
    )
    # Отчёт не содержит приватного материала.
    text = repr(result)
    assert "PRIVATE" not in text
    assert "BEGIN" not in text


def test_verify_rejects_file_modified_after_signing(
    signed_pe: Path, authority: EphemeralAuthority
) -> None:
    data = bytearray(signed_pe.read_bytes())
    pe = parse_pe(bytes(data))
    # Портим байт в теле образа (вне таблицы сертификатов и checksum).
    data[pe.cert_table_offset - 1] ^= 0xFF
    signed_pe.write_bytes(bytes(data))
    with pytest.raises(AuthentiCodeError) as excinfo:
        verify_authenticode(
            signed_pe, expected_publisher=PUBLISHER, trust_roots=[authority.ca_certificate]
        )
    assert excinfo.value.code == "digest_mismatch"


def test_verify_rejects_signature_transplanted_to_another_file(
    tmp_path: Path, authority: EphemeralAuthority
) -> None:
    original = sign_test_pe(make_test_pe(b"ORIGINAL"), authority)
    other_pe = make_test_pe(b"OTHER-BODY")
    other = bytearray(other_pe)
    # Переносим чужую таблицу сертификатов в другой файл.
    pe_original = parse_pe(original)
    blob = original[
        pe_original.cert_table_offset : pe_original.cert_table_offset + pe_original.cert_table_size
    ]
    pe_other = parse_pe(other_pe)
    other.extend(blob)
    other[pe_other.cert_entry_offset : pe_other.cert_entry_offset + 4] = (
        pe_original.cert_table_offset.to_bytes(4, "little")
    )
    other[pe_other.cert_entry_offset + 4 : pe_other.cert_entry_offset + 8] = (
        pe_original.cert_table_size.to_bytes(4, "little")
    )
    # Таблица должна указывать на реально добавленную область.
    offset = len(other_pe)
    other[pe_other.cert_entry_offset : pe_other.cert_entry_offset + 4] = offset.to_bytes(
        4, "little"
    )
    path = tmp_path / "transplanted.exe"
    path.write_bytes(bytes(other))
    with pytest.raises(AuthentiCodeError) as excinfo:
        verify_authenticode(path, trust_roots=[authority.ca_certificate])
    assert excinfo.value.code == "digest_mismatch"


def test_verify_requires_timestamp_when_policy_demands(
    tmp_path: Path, authority: EphemeralAuthority
) -> None:
    path = tmp_path / "unsigned-timestamp.exe"
    path.write_bytes(sign_test_pe(make_test_pe(), authority, with_timestamp=False))
    with pytest.raises(AuthentiCodeError) as excinfo:
        verify_authenticode(path, require_timestamp=True, trust_roots=[authority.ca_certificate])
    assert excinfo.value.code == "missing_timestamp"
    # Без требования метки времени подпись проверяется (тестовый режим CI).
    result = verify_authenticode(path, require_timestamp=False)
    assert result["timestamp_present"] is False


def test_verify_rejects_wrong_publisher(signed_pe: Path, authority: EphemeralAuthority) -> None:
    with pytest.raises(AuthentiCodeError) as excinfo:
        verify_authenticode(signed_pe, expected_publisher="Другой издатель")
    assert excinfo.value.code == "publisher_mismatch"


def test_verify_rejects_untrusted_root(tmp_path: Path, authority: EphemeralAuthority) -> None:
    foreign = create_test_authority("Другой корень")
    path = tmp_path / "signed-by-foreign.exe"
    path.write_bytes(sign_test_pe(make_test_pe(), foreign))
    with pytest.raises(AuthentiCodeError) as excinfo:
        verify_authenticode(
            path,
            expected_publisher=foreign.publisher,
            trust_roots=[authority.ca_certificate],
            timestamp_roots=[authority.ca_certificate],
        )
    assert excinfo.value.code in {"untrusted_root", "bad_signature"}


def test_verify_rejects_missing_code_signing_eku(
    tmp_path: Path, authority: EphemeralAuthority
) -> None:
    weak = create_test_authority("Издатель без EKU", leaf_code_signing_eku=False)
    path = tmp_path / "no-eku.exe"
    path.write_bytes(sign_test_pe(make_test_pe(), weak))
    with pytest.raises(AuthentiCodeError) as excinfo:
        verify_authenticode(path)
    assert excinfo.value.code == "missing_eku"


def test_verify_rejects_timestamp_without_time_stamping_eku(
    tmp_path: Path, authority: EphemeralAuthority
) -> None:
    weak = create_test_authority("Издатель", tsa_time_stamping_eku=False)
    path = tmp_path / "tsa-wrong-eku.exe"
    path.write_bytes(sign_test_pe(make_test_pe(), weak))
    with pytest.raises(AuthentiCodeError) as excinfo:
        verify_authenticode(path)
    assert excinfo.value.code == "bad_timestamp"


def test_verify_rejects_expired_certificate(tmp_path: Path) -> None:
    past = datetime.now(UTC) - timedelta(days=400)
    expired = create_test_authority("Просроченный издатель", now=past, leaf_valid_days=30)
    path = tmp_path / "expired.exe"
    path.write_bytes(sign_test_pe(make_test_pe(), expired, signed_at=past))
    with pytest.raises(AuthentiCodeError) as excinfo:
        verify_authenticode(path, trust_roots=[expired.ca_certificate])
    assert excinfo.value.code == "certificate_expired"


def test_verify_rejects_unsigned_file(tmp_path: Path) -> None:
    path = tmp_path / "unsigned.exe"
    path.write_bytes(make_test_pe())
    with pytest.raises(AuthentiCodeError) as excinfo:
        verify_authenticode(path)
    assert excinfo.value.code == "unsigned"


def test_verify_rejects_non_pe(tmp_path: Path) -> None:
    path = tmp_path / "not-an-exe.bin"
    path.write_bytes(b"just a text file, not an installer" * 4)
    with pytest.raises(AuthentiCodeError) as excinfo:
        verify_authenticode(path)
    assert excinfo.value.code == "not_pe"


def test_verify_rejects_tampered_timestamp_token(
    tmp_path: Path, authority: EphemeralAuthority
) -> None:
    pe = make_test_pe()
    pkcs7 = bytearray(build_signed_pkcs7(pe, authority))
    # Портим байт внутри timestamp-токена (в конце структуры unsignedAttrs).
    pkcs7[-20] ^= 0xFF
    path = tmp_path / "tampered-token.exe"
    path.write_bytes(embed_signature(pe, bytes(pkcs7)))
    with pytest.raises(AuthentiCodeError) as excinfo:
        verify_authenticode(path)
    assert excinfo.value.code in {"bad_timestamp", "bad_signature", "bad_pkcs7"}


def test_pe_digest_ignores_certificate_table_and_checksum(authority: EphemeralAuthority) -> None:
    pe = make_test_pe()
    info = parse_pe(pe)
    before = pe_authenticode_digest(pe, info)
    signed = sign_test_pe(pe, authority)
    after = pe_authenticode_digest(signed, parse_pe(signed))
    # Подпись добавляет только таблицу сертификатов: Authenticode-хеш не меняется.
    assert before == after
    assert extract_pkcs7_blob(signed, parse_pe(signed))


def _spec_regions(data: bytes, pe: PeInfo) -> list[tuple[int, int]]:
    """Независимая реализация алгоритма Microsoft (без дополнения нулями).

    Регионы повторяют документированные шаги: от начала до CheckSum, пропуск
    4 байт, до записи Certificate Table, пропуск 8 байт, до начала таблицы
    сертификатов и от её конца до EOF. Никаких нулевых хвостов.
    """
    end_of_body = pe.cert_table_offset if pe.has_certificate_table else len(data)
    regions = [
        (0, pe.checksum_offset),
        (pe.checksum_offset + 4, pe.cert_entry_offset),
        (pe.cert_entry_offset + 8, end_of_body),
    ]
    if pe.has_certificate_table:
        regions.append((pe.cert_table_offset + pe.cert_table_size, len(data)))
    return regions


def _spec_digest(data: bytes, pe: PeInfo) -> bytes:
    hasher = hashes.Hash(hashes.SHA256())
    for start, end in _spec_regions(data, pe):
        hasher.update(data[start:end])
    return hasher.finalize()


@pytest.mark.parametrize("trailing", [b"", b"\x00" * 8, b"\x00" * 16])
def test_pe_digest_matches_spec_without_zero_padding(
    authority: EphemeralAuthority, trailing: bytes
) -> None:
    """Authenticode-хеш обязан совпадать с документированным алгоритмом.

    Регрессия: прежняя реализация дополняла хешируемую часть нулями до границы
    8 байт. Round-trip с `sign_test_pe` этого не ловил (подписант считал хеш той
    же функцией), а реальный `signtool.exe` — ловил сразу: на CI-раннере для
    HR-Manager-Setup-0.13.0.exe подписанный digest был 4c64684e…, тогда как
    дополненный нулями — d7b7725b… (см. docs/phase-14-report-arena.md).
    """
    signed = sign_test_pe(make_test_pe(b"HRM-TEST-PE-BODY-FOR-CI"), authority) + trailing
    pe = parse_pe(signed)
    hashed_length = sum(end - start for start, end in _spec_regions(signed, pe))
    # Геометрия обязана быть «неудобной»: иначе тест не отличает implementations.
    assert hashed_length % 8 != 0, "нужна не кратная 8 длина хешируемой части"
    assert pe_authenticode_digest(signed, pe, hashes.SHA256) == _spec_digest(signed, pe)
    # И явно: дополненный нулями вариант даёт ДРУГОЙ хеш (то есть не принимается).
    padded = hashes.Hash(hashes.SHA256())
    for start, end in _spec_regions(signed, pe):
        padded.update(signed[start:end])
    padded.update(b"\x00" * (8 - hashed_length % 8))
    assert padded.finalize() != pe_authenticode_digest(signed, pe, hashes.SHA256)


def test_verify_accepts_signature_over_pe_with_non_aligned_hashed_length(
    tmp_path: Path, authority: EphemeralAuthority
) -> None:
    """End-to-end: подпись принимается, когда длина образа не кратна 8.

    Это сценарий реального установщика (signtool.exe), который прежняя версия
    с нулевым дополнением отвергала как `digest_mismatch`.
    """
    signed = sign_test_pe(make_test_pe(b"HRM-TEST-PE-BODY-FOR-CI"), authority)
    pe = parse_pe(signed)
    hashed_length = sum(end - start for start, end in _spec_regions(signed, pe))
    assert hashed_length % 8 != 0
    target = tmp_path / "HR-Manager-Setup-aligned.exe"
    target.write_bytes(signed)
    roots = tmp_path / "roots.pem"
    roots.write_bytes(authority.ca_pem())
    report = verify_authenticode(
        target,
        expected_publisher=PUBLISHER,
        require_timestamp=True,
        trust_roots=load_pem_certificates(roots),
        timestamp_roots=load_pem_certificates(roots),
    )
    assert report["signed"] is True
    assert report["chain_verified"] is True
    assert report["timestamp_present"] is True


def test_cli_refuses_test_signing_without_explicit_flag(tmp_path: Path) -> None:
    import sign_authenticode

    with pytest.raises(SystemExit) as excinfo:
        sign_authenticode.main(
            [
                "sign",
                "--out",
                str(tmp_path / "out.exe"),
            ]
        )
    assert "test_signing_disabled" in str(excinfo.value)


def test_report_contains_no_private_material(
    signed_pe: Path, authority: EphemeralAuthority
) -> None:
    """Отчёт проверки не содержит ни приватного ключа, ни PEM-материала."""
    from cryptography.hazmat.primitives import serialization

    result = verify_authenticode(signed_pe, expected_publisher=PUBLISHER, require_timestamp=True)
    rendered = repr(copy.deepcopy(result))
    private_der = authority.leaf_key.private_bytes(
        serialization.Encoding.DER,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    private_pem = authority.leaf_key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode()
    assert base64.b64encode(private_der).decode() not in rendered
    assert "PRIVATE KEY" not in rendered
    assert private_pem.splitlines()[0] not in rendered


def test_verify_cli_reports_machine_readable_failure(tmp_path: Path) -> None:
    """Отказ верификатора обязан быть машиночитаемым и не менять код возврата.

    sign.ps1 кладёт error_code/error_detail в ::error, поэтому причина видна в
    check-runs даже когда полные логи джоба недоступны. Fail-closed сохранён.
    """
    authority = create_test_authority("HR Manager Report Publisher")
    signed = sign_test_pe(make_test_pe(), authority, with_timestamp=False)
    target = tmp_path / "signed.exe"
    target.write_bytes(signed)
    roots = tmp_path / "roots.pem"
    roots.write_bytes(authority.chain_pem())
    report = tmp_path / "verification.json"

    # Байт перед таблицей сертификатов входит в Authenticode-хеш, поэтому
    # такая правка обязана давать digest_mismatch (а не «подпись существует»).
    pe = parse_pe(signed)
    tampered = bytearray(signed)
    tampered[pe.cert_table_offset - 1] ^= 0xFF
    bad = tmp_path / "tampered.exe"
    bad.write_bytes(bytes(tampered))

    ok_code = authenticode.main(
        [
            "verify",
            "--file",
            str(target),
            "--trust-roots",
            str(roots),
            "--expected-publisher",
            "HR Manager Report Publisher",
            "--json-out",
            str(report),
        ]
    )
    assert ok_code == 0
    assert json.loads(report.read_text(encoding="utf-8"))["ok"] is True

    bad_code = authenticode.main(
        [
            "verify",
            "--file",
            str(bad),
            "--trust-roots",
            str(roots),
            "--expected-publisher",
            "HR Manager Report Publisher",
            "--json-out",
            str(report),
        ]
    )
    assert bad_code == 1
    payload = json.loads(report.read_text(encoding="utf-8"))
    assert payload["ok"] is False
    assert payload["signed"] is False
    assert payload["error_code"] == "digest_mismatch"
    assert payload["error_detail"]


def _embed_signtool_style(pe_bytes: bytes, pkcs7: bytes) -> tuple[bytes, int]:
    """Таблица сертификатов в том виде, в каком её пишет реальный signtool.

    Отличие от `embed_signature`: `dwLength` записи WIN_CERTIFICATE включает
    выравнивание до границы 8 байт. Возвращает (файл, число байт выравнивания).
    """
    data = bytearray(pe_bytes)
    pe = parse_pe(bytes(data))
    length = 8 + len(pkcs7)
    padded = (length + 7) & ~7
    if padded == length:
        padded += 8  # гарантируем ненулевое выравнивание, чтобы тест был осмысленным
    record = (
        padded.to_bytes(4, "little")
        + (0x0200).to_bytes(2, "little")
        + (0x0002).to_bytes(2, "little")
        + pkcs7
        + b"\x00" * (padded - length)
    )
    offset = len(data)
    data.extend(record)
    data[pe.cert_entry_offset : pe.cert_entry_offset + 4] = offset.to_bytes(4, "little")
    data[pe.cert_entry_offset + 4 : pe.cert_entry_offset + 8] = padded.to_bytes(4, "little")
    return bytes(data), padded - length


def test_verify_accepts_signtool_record_padding(
    authority: EphemeralAuthority, tmp_path: Path
) -> None:
    """signtool включает выравнивание записи в dwLength — подпись обязана читаться.

    Именно на этом падала реальная подпись установщика в CI:
    bad_pkcs7 «после DER-элемента остались лишние байты». Собственный подписант
    репозитория выравнивание в dwLength не включает, поэтому round-trip тестами
    расхождение не ловилось.
    """
    pe = make_test_pe()
    pkcs7 = build_signed_pkcs7(pe, authority, with_timestamp=False)
    signed, padding = _embed_signtool_style(pe, pkcs7)
    assert padding > 0, "тест должен проверять ненулевое выравнивание"

    target = tmp_path / "signtool-layout.exe"
    target.write_bytes(signed)
    verified = verify_authenticode(
        target,
        expected_publisher=PUBLISHER,
        require_timestamp=False,
        trust_roots=[authority.ca_certificate],
    )
    assert verified["signed"] is True
    assert verified["publisher_match"] is True
    assert verified["chain_verified"] is True


def test_verify_rejects_nonzero_record_padding(
    authority: EphemeralAuthority, tmp_path: Path
) -> None:
    """Выравнивание обязано быть нулевым: любой другой хвост — отказ."""
    pe = make_test_pe()
    pkcs7 = build_signed_pkcs7(pe, authority, with_timestamp=False)
    signed, _ = _embed_signtool_style(pe, pkcs7)
    tampered = bytearray(signed)
    tampered[-1] = 0x41  # последний байт выравнивания больше не ноль
    target = tmp_path / "signtool-badpad.exe"
    target.write_bytes(bytes(tampered))
    with pytest.raises(AuthentiCodeError) as excinfo:
        verify_authenticode(target, expected_publisher=PUBLISHER, require_timestamp=False)
    assert excinfo.value.code == "bad_certificate_table"


SIGNTOOL_BLOB = RELEASE / "testdata" / "signtool-message-digest.der"


def _message_digest_of(blob: bytes) -> tuple[bytes, bytes, bytes]:
    """(messageDigest, тело SpcIndirectDataContent, весь TLV eContent)."""
    signed = parse_signed_data(blob)
    outcome = verify_signer_info(signed)
    declared = parse_one(
        authenticode._attribute_bytes(outcome.attributes, OID_PKCS9_MESSAGE_DIGEST)
    ).content
    spc_node = parse_one(signed.econtent)
    return declared, spc_node.content, signed.econtent


@pytest.mark.parametrize("source", ["signtool", "repo-signer"])
def test_message_digest_hashes_spc_body_not_tlv(source: str, authority: EphemeralAuthority) -> None:
    """messageDigest = SHA256(тела SpcIndirectDataContent), без заголовка SEQUENCE.

    Так считает настоящий signtool: golden-блоб реальной подписи установщика
    (CI run 35610245011, ephemeral test-сертификат) даёт
    sha256(spc_body) == messageDigest, тогда как sha256(всего TLV) — нет.
    Тестовый подписант репозитория обязан повторять эталон, иначе независимый
    верификатор невозможно сверять с signtool.
    """
    if source == "signtool":
        blob = SIGNTOOL_BLOB.read_bytes()
    else:
        blob = build_signed_pkcs7(make_test_pe(), authority, with_timestamp=False)

    declared, spc_body, econtent_tlv = _message_digest_of(blob)
    assert declared == hashlib.sha256(spc_body).digest()
    assert declared != hashlib.sha256(econtent_tlv).digest()


def test_trust_roots_must_come_from_outside_the_artifact(tmp_path: Path) -> None:
    """Якорь доверия, взятый из самой подписи, делает проверку тавтологией.

    `_verify_chain` немедленно возвращает успех, когда DER подписанта есть среди
    корней. Поэтому «экспортировать SignerCertificate в --trust-roots» — это не
    проверка цепочки: так проходит любой самоподписанный издатель. Тест фиксирует
    обе половины утверждения, чтобы паттерн нельзя было вернуть молча.
    """
    outsider = create_test_authority("EVIL Corp (attacker)")
    target = tmp_path / "outsider-signed.exe"
    target.write_bytes(sign_test_pe(make_test_pe(), outsider, with_timestamp=False))

    # Тавтология: корень — это сертификат подписанта из того же файла.
    self_roots = tmp_path / "self-roots.pem"
    self_roots.write_bytes(outsider.leaf_certificate.public_bytes(serialization.Encoding.PEM))
    tautology = verify_authenticode(
        target,
        expected_publisher="EVIL Corp (attacker)",
        require_timestamp=False,
        trust_roots=load_pem_certificates(self_roots),
    )
    assert tautology["chain_verified"] is True, (
        "предусловие: тавтологический якорь действительно принимает"
    )

    # Корректная проверка: якорь задан независимо от артефакта.
    pinned = tmp_path / "pinned-roots.pem"
    pinned.write_bytes(create_test_authority("ООО Ромашка (тестовый издатель)").ca_pem())
    with pytest.raises(AuthentiCodeError) as excinfo:
        verify_authenticode(
            target,
            expected_publisher="EVIL Corp (attacker)",
            require_timestamp=False,
            trust_roots=load_pem_certificates(pinned),
        )
    assert excinfo.value.code == "untrusted_root"
