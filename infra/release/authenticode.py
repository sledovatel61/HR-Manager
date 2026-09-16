# -*- coding: utf-8 -*-
"""Phase 14: независимая проверка Authenticode-подписи Windows installer'а.

Зачем: канал обновлений защищён detached Ed25519-подписью (Phase 13), но
пользователь запускает ``HR-Manager-Setup.exe`` ДО того, как приложение
получит доступ к каналу. Поэтому production-релиз обязан иметь ещё и
Authenticode-подпись, проверенную в двух независимых местах:

* Windows (``signtool verify /pa /all`` + ``Get-AuthenticodeSignature``) —
  построение цепочки доверия средствами ОС;
* здесь, на Linux (release pipeline) — криптографическая проверка
  содержимого: подпись CMS, Authenticode-хеш PE-образа, привязка хеша
  SpcIndirectDataContent к файлу, издатель и метка времени RFC 3161.

Это НЕ полноценный аналог signtool: доверие к корню сертификата здесь
проверяется только при явно переданных ``--trust-roots`` (production
policy передаёт их из защищённого release input). Fail closed: любое
несоответствие — ошибка с безопасным кодом, без PII/секретов.

CLI:

    python infra/release/authenticode.py verify \
        --file installer/output/HR-Manager-Setup-0.14.0.exe \
        [--expected-publisher "ООО Ромашка"] [--require-timestamp] \
        [--trust-roots roots.pem] [--at 2026-09-11T00:00:00Z] \
        [--json-out auth.json]
"""

from __future__ import annotations

import argparse
import base64
import json
import sys
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from cryptography import x509
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec, padding, rsa
from cryptography.x509.oid import ExtendedKeyUsageOID, ObjectIdentifier

sys.path.insert(0, str(Path(__file__).resolve().parent))

from der import (  # noqa: E402
    TAG_CONTEXT0,
    TAG_CONTEXT1,
    TAG_SEQUENCE,
    TAG_SET,
    DerError,
    Node,
    decode_integer,
    decode_oid,
    decode_time,
    encode_tlv,
    expect,
    parse_all,
    parse_one,
)

# --- OID, встречающиеся в Authenticode -----------------------------------------

OID_SPC_INDIRECT_DATA = "1.3.6.1.4.1.311.2.1.4"
OID_SPC_PE_IMAGE_DATA = "1.3.6.1.4.1.311.2.1.15"
OID_SPC_STATEMENT_TYPE = "1.3.6.1.4.1.311.2.1.11"
OID_PKCS7_SIGNED_DATA = "1.2.840.113549.1.7.2"
OID_PKCS9_CONTENT_TYPE = "1.2.840.113549.1.9.3"
OID_PKCS9_MESSAGE_DIGEST = "1.2.840.113549.1.9.4"
OID_PKCS9_SIGNING_TIME = "1.2.840.113549.1.9.5"
OID_PKCS9_TIMESTAMP_TOKEN = "1.2.840.113549.1.9.16.2.14"
OID_CT_TSTINFO = "1.2.840.113549.1.9.16.1.4"
OID_EKU_CODE_SIGNING = ExtendedKeyUsageOID.CODE_SIGNING.dotted_string
OID_EKU_TIME_STAMPING = ExtendedKeyUsageOID.TIME_STAMPING.dotted_string

_DIGESTS: dict[str, type[hashes.HashAlgorithm]] = {
    "1.3.14.3.2.26": hashes.SHA1,
    "2.16.840.1.101.3.4.2.1": hashes.SHA256,
    "2.16.840.1.101.3.4.2.2": hashes.SHA384,
    "2.16.840.1.101.3.4.2.3": hashes.SHA512,
}
_DIGEST_NAMES = {
    "1.3.14.3.2.26": "sha1",
    "2.16.840.1.101.3.4.2.1": "sha256",
    "2.16.840.1.101.3.4.2.2": "sha384",
    "2.16.840.1.101.3.4.2.3": "sha512",
}
_RSA_SIGNATURE_OIDS = {
    "1.2.840.113549.1.1.1": None,  # rsaEncryption (digest из digestAlgorithm)
    "1.2.840.113549.1.1.5": hashes.SHA1,
    "1.2.840.113549.1.1.11": hashes.SHA256,
    "1.2.840.113549.1.1.12": hashes.SHA384,
    "1.2.840.113549.1.1.13": hashes.SHA512,
}
_ECDSA_SIGNATURE_OIDS = {
    "1.2.840.10045.4.1": hashes.SHA1,
    "1.2.840.10045.4.3.2": hashes.SHA256,
    "1.2.840.10045.4.3.3": hashes.SHA384,
    "1.2.840.10045.4.3.4": hashes.SHA512,
}

# Authenticode-хеш PE считается по документированному алгоритму: пропускаются
# поле контрольной суммы и таблица сертификатов (вместе с её записью в data
# directory), а результат дополняется нулями до границы 8 байт.
PE_PAD_BOUNDARY = 8


class AuthentiCodeError(ValueError):
    """Ошибка проверки Authenticode: безопасный код + сообщение."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass
class PeInfo:
    """Разобранные поля PE, нужные для Authenticode."""

    checksum_offset: int
    cert_entry_offset: int
    cert_table_offset: int
    cert_table_size: int
    size_of_headers: int
    optional_magic: int

    @property
    def has_certificate_table(self) -> bool:
        return self.cert_table_offset > 0 and self.cert_table_size > 0


# --- PE -----------------------------------------------------------------------


def parse_pe(data: bytes) -> PeInfo:
    """Разбор PE-заголовков с fail-closed проверками границ."""
    if len(data) < 0x40 or data[0:2] != b"MZ":
        raise AuthentiCodeError("not_pe", "файл не является PE-образом (нет MZ)")
    e_lfanew = int.from_bytes(data[0x3C:0x40], "little")
    if e_lfanew <= 0 or e_lfanew + 24 > len(data):
        raise AuthentiCodeError("bad_pe", "некорректное смещение PE-заголовка")
    if data[e_lfanew : e_lfanew + 4] != b"PE\0\0":
        raise AuthentiCodeError("bad_pe", "отсутствует подпись PE")
    coff = e_lfanew + 4
    size_of_optional_header = int.from_bytes(data[coff + 16 : coff + 18], "little")
    optional = coff + 20
    if size_of_optional_header < 96 or optional + size_of_optional_header > len(data):
        raise AuthentiCodeError("bad_pe", "некорректный размер optional header")
    magic = int.from_bytes(data[optional : optional + 2], "little")
    if magic == 0x10B:  # PE32
        data_dir_offset = optional + 96
        checksum_offset = optional + 64
    elif magic == 0x20B:  # PE32+
        data_dir_offset = optional + 112
        checksum_offset = optional + 64
    else:
        raise AuthentiCodeError("bad_pe", f"неизвестный optional header magic: 0x{magic:x}")
    size_of_headers = int.from_bytes(data[optional + 60 : optional + 64], "little")
    # Data directory index 4 = Certificate Table.
    cert_entry_offset = data_dir_offset + 4 * 8
    if cert_entry_offset + 8 > optional + size_of_optional_header:
        raise AuthentiCodeError("bad_pe", "таблица данных PE обрывается до Certificate Table")
    cert_table_offset = int.from_bytes(data[cert_entry_offset : cert_entry_offset + 4], "little")
    cert_table_size = int.from_bytes(
        data[cert_entry_offset + 4 : cert_entry_offset + 8], "little"
    )
    if cert_table_offset and cert_table_offset + cert_table_size > len(data):
        raise AuthentiCodeError("bad_pe", "таблица сертификатов выходит за пределы файла")
    return PeInfo(
        checksum_offset=checksum_offset,
        cert_entry_offset=cert_entry_offset,
        cert_table_offset=cert_table_offset,
        cert_table_size=cert_table_size,
        size_of_headers=size_of_headers,
        optional_magic=magic,
    )


def _hash_algorithm(
    algorithm: type[hashes.HashAlgorithm] | hashes.HashAlgorithm | None,
) -> hashes.HashAlgorithm:
    if algorithm is None:
        return hashes.SHA256()
    if isinstance(algorithm, hashes.HashAlgorithm):
        return algorithm
    return algorithm()


def pe_authenticode_digest(
    data: bytes,
    pe: PeInfo,
    algorithm: type[hashes.HashAlgorithm] | hashes.HashAlgorithm | None = None,
) -> bytes:
    """Authenticode-хеш PE-образа (без checksum и таблицы сертификатов)."""
    hasher = hashes.Hash(_hash_algorithm(algorithm))
    regions: list[tuple[int, int]] = [
        (0, pe.checksum_offset),
        (pe.checksum_offset + 4, pe.cert_entry_offset),
        (pe.cert_entry_offset + 8, pe.cert_table_offset if pe.has_certificate_table else len(data)),
    ]
    if pe.has_certificate_table:
        regions.append((pe.cert_table_offset + pe.cert_table_size, len(data)))
    hashed_length = 0
    for start, end in regions:
        if start > end or end > len(data):
            raise AuthentiCodeError("bad_pe", "некорректные границы Authenticode-хеша")
        hasher.update(data[start:end])
        hashed_length += end - start
    remainder = hashed_length % PE_PAD_BOUNDARY
    if remainder:
        hasher.update(b"\x00" * (PE_PAD_BOUNDARY - remainder))
    return hasher.finalize()


def extract_pkcs7_blob(data: bytes, pe: PeInfo) -> bytes:
    """Извлекает PKCS#7 (SignedData) из WIN_CERTIFICATE-таблицы."""
    if not pe.has_certificate_table:
        raise AuthentiCodeError("unsigned", "в файле нет таблицы сертификатов Authenticode")
    offset = pe.cert_table_offset
    end = pe.cert_table_offset + pe.cert_table_size
    records: list[bytes] = []
    while offset + 8 <= end:
        length = int.from_bytes(data[offset : offset + 4], "little")
        revision = int.from_bytes(data[offset + 4 : offset + 6], "little")
        cert_type = int.from_bytes(data[offset + 6 : offset + 8], "little")
        if length < 8 or offset + length > end:
            raise AuthentiCodeError("bad_certificate_table", "некорректная запись WIN_CERTIFICATE")
        if revision != 0x0200:
            raise AuthentiCodeError(
                "bad_certificate_table", f"неподдерживаемая revision: 0x{revision:04x}"
            )
        if cert_type == 0x0002:  # WIN_CERT_TYPE_PKCS_SIGNED_DATA
            records.append(data[offset + 8 : offset + length])
        offset += (length + 7) & ~7  # записи выровнены по 8 байт
    if not records:
        raise AuthentiCodeError("unsigned", "PKCS#7-подпись в таблице сертификатов не найдена")
    return records[0]


# --- CMS/PKCS#7 ----------------------------------------------------------------


@dataclass
class SignedDataInfo:
    econtent_type: str
    econtent: bytes
    certificates: list[x509.Certificate]
    signer_info: Node
    raw: bytes


def _unwrap_content(node: Node) -> bytes:
    """Содержимое [0] EXPLICIT ContentInfo: OCTET STRING или структура."""
    children = node.children
    if len(children) == 1 and children[0].tag == 0x04:  # OCTET STRING
        return children[0].content
    return node.content


def parse_signed_data(data: bytes) -> SignedDataInfo:
    """Разбор PKCS#7 ContentInfo/SignedData."""
    try:
        content_info = parse_one(data)
    except DerError as exc:
        raise AuthentiCodeError("bad_pkcs7", f"некорректный DER подписи: {exc}") from exc
    expect(content_info, TAG_SEQUENCE, "ContentInfo")
    items = content_info.children
    if len(items) != 2:
        raise AuthentiCodeError("bad_pkcs7", "ContentInfo обязан содержать ровно два элемента")
    content_type = decode_oid(items[0])
    if content_type != OID_PKCS7_SIGNED_DATA:
        raise AuthentiCodeError(
            "bad_pkcs7", f"ожидался SignedData, получен contentType {content_type}"
        )
    try:
        content = items[1].children
    except DerError as exc:
        raise AuthentiCodeError("bad_pkcs7", f"некорректный ContentInfo: {exc}") from exc
    if len(content) != 1 or content[0].tag != TAG_SEQUENCE:
        raise AuthentiCodeError("bad_pkcs7", "ContentInfo не содержит SignedData")
    signed_data = content[0]
    fields = signed_data.children
    if len(fields) < 4:
        raise AuthentiCodeError("bad_pkcs7", "SignedData содержит меньше четырёх полей")
    index = 0
    decode_integer(fields[index])  # version
    index += 1
    digest_algorithms = parse_all(expect(fields[index], TAG_SET, "digestAlgorithms").content)
    if not digest_algorithms:
        raise AuthentiCodeError("bad_pkcs7", "digestAlgorithms пуст")
    decode_oid(expect(digest_algorithms[0], TAG_SEQUENCE, "AlgorithmIdentifier").children[0])
    index += 1
    # encapContentInfo
    encap = expect(fields[index], TAG_SEQUENCE, "encapContentInfo")
    index += 1
    encap_items = encap.children
    if len(encap_items) != 2:
        raise AuthentiCodeError("bad_pkcs7", "encapContentInfo обязан содержать тип и контент")
    econtent_type = decode_oid(encap_items[0])
    if encap_items[1].tag != TAG_CONTEXT0:
        raise AuthentiCodeError("bad_pkcs7", "контент SignedData обязан быть в [0] EXPLICIT")
    econtent = _unwrap_content(encap_items[1])

    certificates: list[x509.Certificate] = []
    signer_info: Node | None = None
    while index < len(fields):
        field_node = fields[index]
        index += 1
        if field_node.tag == TAG_CONTEXT0:  # certificates [0] IMPLICIT SET OF
            for candidate in parse_all(field_node.content):
                if candidate.tag != TAG_SEQUENCE:
                    continue
                try:
                    certificates.append(x509.load_der_x509_certificate(candidate.der()))
                except Exception:  # посторонняя структура — не сертификат
                    continue
        elif field_node.tag == TAG_SET:  # signerInfos
            entries = parse_all(field_node.content)
            if len(entries) != 1:
                raise AuthentiCodeError(
                    "bad_pkcs7",
                    f"ожидался ровно один подписант, найдено {len(entries)} (fail closed)",
                )
            signer_info = entries[0]
        # CRLs [1] IMPLICIT и прочие поля игнорируются.
    if signer_info is None:
        raise AuthentiCodeError("bad_pkcs7", "в SignedData нет signerInfos")
    return SignedDataInfo(
        econtent_type=econtent_type,
        econtent=econtent,
        certificates=certificates,
        signer_info=signer_info,
        raw=data,
    )


@dataclass
class SignerOutcome:
    certificate: x509.Certificate
    digest_algorithm_oid: str
    signature: bytes
    signed_attrs: Node | None
    signed_attrs_der: bytes
    attributes: dict[str, list[Node]] = field(default_factory=dict)
    unsigned_attributes: dict[str, list[Node]] = field(default_factory=dict)


def _attributes_from(set_node: Node) -> dict[str, list[Node]]:
    attributes: dict[str, list[Node]] = {}
    for attribute in parse_all(set_node.content):
        if attribute.tag != TAG_SEQUENCE:
            raise AuthentiCodeError("bad_pkcs7", "атрибут подписи обязан быть SEQUENCE")
        parts = attribute.children
        if len(parts) != 2:
            raise AuthentiCodeError("bad_pkcs7", "атрибут подписи обязан содержать OID и значения")
        oid = decode_oid(parts[0])
        values = parse_all(expect(parts[1], TAG_SET, "значения атрибута").content)
        attributes.setdefault(oid, []).extend(values)
    return attributes


def _find_certificate(
    certificates: list[x509.Certificate], signer_info: Node
) -> x509.Certificate:
    """Поиск сертификата подписанта по IssuerAndSerialNumber или SKI."""
    fields = signer_info.children
    if len(fields) < 3:
        raise AuthentiCodeError("bad_pkcs7", "SignerInfo содержит меньше трёх полей")
    sid = fields[1]
    if sid.tag == TAG_SEQUENCE:
        parts = sid.children
        if len(parts) != 2:
            raise AuthentiCodeError("bad_pkcs7", "некорректный IssuerAndSerialNumber")
        serial = decode_integer(parts[1])
        issuer_der = parts[0].der()
        for certificate in certificates:
            if certificate.serial_number == serial and certificate.issuer.public_bytes() == issuer_der:
                return certificate
        # Некоторые реализации кодируют issuer иначе — сверяем только серийный номер.
        matches = [c for c in certificates if c.serial_number == serial]
        if len(matches) == 1:
            return matches[0]
        raise AuthentiCodeError("signer_not_found", "сертификат подписанта не найден")
    if sid.tag == TAG_CONTEXT0:  # SubjectKeyIdentifier
        key_id = sid.content
        matches = [
            c
            for c in certificates
            if c.extensions.get_extension_for_class(x509.SubjectKeyIdentifier).value.digest == key_id
        ]
        if len(matches) == 1:
            return matches[0]
        raise AuthentiCodeError("signer_not_found", "сертификат подписанта не найден по SKI")
    raise AuthentiCodeError("bad_pkcs7", "неподдерживаемый тип идентификатора подписанта")


def _signature_algorithm(oid: str, digest_algorithm: hashes.HashAlgorithm) -> hashes.HashAlgorithm:
    if oid in _ECDSA_SIGNATURE_OIDS:
        return _ECDSA_SIGNATURE_OIDS[oid]()
    if oid in _RSA_SIGNATURE_OIDS:
        fixed = _RSA_SIGNATURE_OIDS[oid]
        return fixed() if fixed is not None else digest_algorithm
    raise AuthentiCodeError("unsupported_algorithm", f"неподдерживаемый алгоритм подписи: {oid}")


def _verify_public_key_signature(
    public_key: object, signature: bytes, data: bytes, algorithm: hashes.HashAlgorithm
) -> None:
    try:
        if isinstance(public_key, rsa.RSAPublicKey):
            public_key.verify(signature, data, padding.PKCS1v15(), algorithm)
        elif isinstance(public_key, ec.EllipticCurvePublicKey):
            public_key.verify(signature, data, ec.ECDSA(algorithm))
        else:
            raise AuthentiCodeError("unsupported_key", "неподдерживаемый тип публичного ключа")
    except InvalidSignature as exc:
        raise AuthentiCodeError("bad_signature", "подпись CMS недействительна") from exc


def verify_signer_info(signed_data: SignedDataInfo) -> SignerOutcome:
    """Проверка CMS-подписи: подписанные атрибуты и подпись подписанта."""
    signer_info = signed_data.signer_info
    fields = signer_info.children
    index = 0
    decode_integer(fields[index])
    index += 1
    certificate = _find_certificate(signed_data.certificates, signer_info)
    index += 1  # sid (IssuerAndSerialNumber | SubjectKeyIdentifier)
    digest_algorithm_oid = decode_oid(
        expect(fields[index], TAG_SEQUENCE, "digestAlgorithm").children[0]
    )
    index += 1
    if digest_algorithm_oid not in _DIGESTS:
        raise AuthentiCodeError(
            "unsupported_algorithm", f"неподдерживаемый digest: {digest_algorithm_oid}"
        )
    digest_algorithm = _DIGESTS[digest_algorithm_oid]()
    signed_attrs: Node | None = None
    if index < len(fields) and fields[index].tag == TAG_CONTEXT0:
        signed_attrs = fields[index]
        index += 1
    signature_algorithm_oid = decode_oid(
        expect(fields[index], TAG_SEQUENCE, "signatureAlgorithm").children[0]
    )
    index += 1
    signature = expect(fields[index], 0x04, "signature").content
    signature_algorithm = _signature_algorithm(signature_algorithm_oid, digest_algorithm)
    # unsignedAttrs [1] IMPLICIT SET OF Attribute — здесь лежит метка времени.
    unsigned_attributes: dict[str, list[Node]] = {}
    if index + 1 < len(fields) and fields[index + 1].tag == TAG_CONTEXT1:
        unsigned_attributes = _attributes_from(fields[index + 1])

    if signed_attrs is None:
        # Authenticode всегда подписывает атрибуты; подпись «сырого» контента
        # здесь не принимается (fail closed).
        raise AuthentiCodeError(
            "missing_signed_attrs", "Authenticode-подпись обязана содержать signed attributes"
        )
    # Подпись вычисляется по DER SET OF signedAttrs (тег SET, не [0]).
    attrs_der = encode_tlv(TAG_SET, signed_attrs.content)
    _verify_public_key_signature(
        certificate.public_key(), signature, attrs_der, signature_algorithm
    )
    attributes = _attributes_from(signed_attrs)
    return SignerOutcome(
        certificate=certificate,
        digest_algorithm_oid=digest_algorithm_oid,
        signature=signature,
        signed_attrs=signed_attrs,
        signed_attrs_der=attrs_der,
        attributes=attributes,
        unsigned_attributes=unsigned_attributes,
    )


def _attribute_bytes(attributes: dict[str, list[Node]], oid: str) -> bytes | None:
    values = attributes.get(oid)
    if not values:
        return None
    return values[0].der()


def _digest_of(data: bytes, algorithm_oid: str) -> bytes:
    hasher = hashes.Hash(_DIGESTS[algorithm_oid]())
    hasher.update(data)
    return hasher.finalize()


def _parse_authenticode_content(econtent: bytes) -> tuple[bytes, str]:
    """SpcIndirectDataContent → (messageDigest, digest_algorithm_oid)."""
    try:
        spc = parse_one(econtent)
    except DerError as exc:
        raise AuthentiCodeError("bad_spc", f"некорректный SpcIndirectDataContent: {exc}") from exc
    expect(spc, TAG_SEQUENCE, "SpcIndirectDataContent")
    fields = spc.children
    if len(fields) != 2:
        raise AuthentiCodeError("bad_spc", "SpcIndirectDataContent обязан содержать два поля")
    data_type = expect(fields[0], TAG_SEQUENCE, "SpcAttributeTypeAndOptionalValue")
    type_items = data_type.children
    if not type_items or decode_oid(type_items[0]) != OID_SPC_PE_IMAGE_DATA:
        raise AuthentiCodeError(
            "bad_spc", "Authenticode-содержимое обязано описывать PE-образ (SpcPeImageData)"
        )
    digest_info = expect(fields[1], TAG_SEQUENCE, "DigestInfo")
    digest_items = digest_info.children
    if len(digest_items) != 2:
        raise AuthentiCodeError("bad_spc", "DigestInfo обязан содержать алгоритм и хеш")
    algorithm = expect(digest_items[0], TAG_SEQUENCE, "AlgorithmIdentifier")
    algorithm_oid = decode_oid(algorithm.children[0])
    if algorithm_oid not in _DIGESTS:
        raise AuthentiCodeError(
            "unsupported_algorithm", f"неподдерживаемый digest Authenticode: {algorithm_oid}"
        )
    digest = expect(digest_items[1], 0x04, "messageDigest").content
    return digest, algorithm_oid


def _parse_timestamp_token(value_der: bytes, signer_signature: bytes) -> dict:
    """Разбор и проверка RFC 3161 timestamp token (id-aa-timeStampToken)."""
    token = parse_signed_data(value_der)
    if token.econtent_type != OID_CT_TSTINFO:
        raise AuthentiCodeError(
            "bad_timestamp", f"timestamp token имеет неожиданный тип {token.econtent_type}"
        )
    try:
        tst = parse_one(token.econtent)
    except DerError as exc:
        raise AuthentiCodeError("bad_timestamp", f"некорректный TSTInfo: {exc}") from exc
    expect(tst, TAG_SEQUENCE, "TSTInfo")
    outcome = verify_signer_info(token)
    tsa_certificate = outcome.certificate
    if OID_EKU_TIME_STAMPING not in _extended_key_usage(tsa_certificate):
        raise AuthentiCodeError(
            "bad_timestamp",
            "сертификат TSA не имеет Extended Key Usage timeStamping",
        )
    fields = tst.children
    if len(fields) < 5:
        raise AuthentiCodeError("bad_timestamp", "TSTInfo содержит меньше пяти полей")
    message_imprint = expect(fields[2], TAG_SEQUENCE, "messageImprint")
    imprint_items = message_imprint.children
    if len(imprint_items) != 2:
        raise AuthentiCodeError("bad_timestamp", "messageImprint обязан содержать алгоритм и хеш")
    imprint_algorithm = decode_oid(expect(imprint_items[0], TAG_SEQUENCE, "AlgorithmIdentifier").children[0])
    if imprint_algorithm not in _DIGESTS:
        raise AuthentiCodeError(
            "unsupported_algorithm", f"неподдерживаемый digest метки времени: {imprint_algorithm}"
        )
    imprint = expect(imprint_items[1], 0x04, "hashedMessage").content
    if imprint != _digest_of(signer_signature, imprint_algorithm):
        raise AuthentiCodeError(
            "bad_timestamp",
            "messageImprint метки времени не соответствует подписи подписанта (подмена метки)",
        )
    gen_time = decode_time(fields[4])
    return {
        "present": True,
        "gen_time": gen_time,
        "digest_algorithm": _DIGEST_NAMES.get(imprint_algorithm, imprint_algorithm),
        "tsa_subject": tsa_certificate.subject.rfc4514_string(),
        "tsa_issuer": tsa_certificate.issuer.rfc4514_string(),
        "tsa_thumbprint_sha256": tsa_certificate.fingerprint(hashes.SHA256()).hex(),
        "certificate": tsa_certificate,
        "certificates": token.certificates,
    }


def _extended_key_usage(certificate: x509.Certificate) -> list[str]:
    try:
        extension = certificate.extensions.get_extension_for_class(x509.ExtendedKeyUsage)
    except x509.ExtensionNotFound:
        return []
    return [usage.dotted_string for usage in extension.value]


def _publisher_names(certificate: x509.Certificate) -> list[str]:
    names: list[str] = []
    for attribute in certificate.subject.get_attributes_for_oid(x509.oid.NameOID.ORGANIZATION_NAME):
        names.append(str(attribute.value))
    for attribute in certificate.subject.get_attributes_for_oid(x509.oid.NameOID.COMMON_NAME):
        names.append(str(attribute.value))
    return names


def _parse_at(value: str | None) -> datetime:
    if not value:
        return datetime.now(UTC)
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    parsed = datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _parse_certificate_time(value: str) -> datetime:
    """UTCTime ``YYMMDDHHMMSSZ`` или GeneralizedTime ``YYYYMMDDHHMMSSZ``."""
    text = value.strip()
    try:
        if len(text) == 13:
            return datetime.strptime(text, "%y%m%d%H%M%SZ").replace(tzinfo=UTC)
        return datetime.strptime(text, "%Y%m%d%H%M%SZ").replace(tzinfo=UTC)
    except ValueError as exc:
        raise AuthentiCodeError("bad_time", f"некорректное время подписи: {value!r}") from exc


def load_pem_certificates(path: Path) -> list[x509.Certificate]:
    data = path.read_bytes()
    certificates: list[x509.Certificate] = []
    for block in data.split(b"-----END CERTIFICATE-----"):
        if b"-----BEGIN CERTIFICATE-----" not in block:
            continue
        body = block.split(b"-----BEGIN CERTIFICATE-----", 1)[1].strip()
        try:
            certificates.append(x509.load_der_x509_certificate(base64.b64decode(body)))
        except Exception as exc:  # pragma: no cover - защита от битого входа
            raise AuthentiCodeError("bad_root", f"некорректный PEM-сертификат: {exc}") from exc
    if not certificates:
        raise AuthentiCodeError("bad_root", f"в {path} нет PEM-сертификатов")
    return certificates


def _verify_chain(
    certificate: x509.Certificate,
    intermediates: list[x509.Certificate],
    roots: list[x509.Certificate],
    at: datetime,
) -> None:
    """Минимальная проверка цепочки: подписи, сроки, доведение до корня."""
    root_ders = {root.public_bytes(serialization.Encoding.DER) for root in roots}
    pool = list(intermediates) + list(roots)
    current = certificate
    seen = {current.serial_number}
    for _ in range(8):
        if current.not_valid_before_utc > at or current.not_valid_after_utc < at:
            raise AuthentiCodeError(
                "certificate_expired",
                f"сертификат {current.subject.rfc4514_string()} недействителен на момент {at.isoformat()}",
            )
        if current.public_bytes(serialization.Encoding.DER) in root_ders:
            return
        candidates = [
            candidate
            for candidate in pool
            if candidate.subject == current.issuer and candidate.serial_number not in seen
        ]
        signature_algorithm = _signature_algorithm(
            current.signature_algorithm_oid.dotted_string, current.signature_hash_algorithm
        )
        issuer = None
        for candidate in candidates:
            try:
                _verify_public_key_signature(
                    candidate.public_key(),
                    current.signature,
                    current.tbs_certificate_bytes,
                    signature_algorithm,
                )
            except AuthentiCodeError:
                continue  # одноимённый, но не тот издатель — пробуем следующий
            issuer = candidate
            break
        if issuer is None:
            raise AuthentiCodeError(
                "untrusted_root",
                f"цепочка сертификатов не доводится до доверенного корня: "
                f"{current.issuer.rfc4514_string()}",
            )
        seen.add(issuer.serial_number)
        current = issuer
    raise AuthentiCodeError("untrusted_root", "слишком длинная цепочка сертификатов")


def verify_authenticode(
    path: Path,
    *,
    expected_publisher: str | None = None,
    require_timestamp: bool = True,
    at: datetime | None = None,
    trust_roots: list[x509.Certificate] | None = None,
    timestamp_roots: list[x509.Certificate] | None = None,
) -> dict:
    """Полная проверка Authenticode-подписи файла (fail closed).

    Любая ошибка разбора DER превращается в ``AuthentiCodeError`` с кодом
    ``bad_pkcs7``: снаружи не бывает «частично проверенной» подписи.
    """
    try:
        return _verify_authenticode_inner(
            path,
            expected_publisher=expected_publisher,
            require_timestamp=require_timestamp,
            at=at,
            trust_roots=trust_roots,
            timestamp_roots=timestamp_roots,
        )
    except DerError as exc:
        raise AuthentiCodeError("bad_pkcs7", f"некорректная структура подписи: {exc}") from exc


def _verify_authenticode_inner(
    path: Path,
    *,
    expected_publisher: str | None = None,
    require_timestamp: bool = True,
    at: datetime | None = None,
    trust_roots: list[x509.Certificate] | None = None,
    timestamp_roots: list[x509.Certificate] | None = None,
) -> dict:
    data = path.read_bytes()
    if not data:
        raise AuthentiCodeError("empty_file", f"файл пуст: {path}")
    pe = parse_pe(data)
    blob = extract_pkcs7_blob(data, pe)
    signed_data = parse_signed_data(blob)
    if signed_data.econtent_type != OID_SPC_INDIRECT_DATA:
        raise AuthentiCodeError(
            "bad_pkcs7",
            f"подпись не является Authenticode (contentType {signed_data.econtent_type})",
        )
    outcome = verify_signer_info(signed_data)
    signer = outcome.certificate

    # 1. Привязка содержимого подписи к файлу: Authenticode-хеш PE.
    content_type_attr = _attribute_bytes(outcome.attributes, OID_PKCS9_CONTENT_TYPE)
    if content_type_attr is None:
        raise AuthentiCodeError("bad_signature", "в подписи нет атрибута contentType")
    if decode_oid(parse_one(content_type_attr)) != OID_SPC_INDIRECT_DATA:
        raise AuthentiCodeError("bad_signature", "contentType подписи не SpcIndirectDataContent")
    message_digest_attr = _attribute_bytes(outcome.attributes, OID_PKCS9_MESSAGE_DIGEST)
    if message_digest_attr is None:
        raise AuthentiCodeError("bad_signature", "в подписи нет атрибута messageDigest")
    declared_digest = parse_one(message_digest_attr).content
    if declared_digest != _digest_of(signed_data.econtent, outcome.digest_algorithm_oid):
        raise AuthentiCodeError(
            "bad_signature", "messageDigest подписанных атрибутов не совпал с содержимым подписи"
        )
    spc_digest, spc_digest_oid = _parse_authenticode_content(signed_data.econtent)
    computed = pe_authenticode_digest(data, pe, _DIGESTS[spc_digest_oid])
    if computed != spc_digest:
        raise AuthentiCodeError(
            "digest_mismatch",
            "Authenticode-хеш файла не совпал с подписанным SpcIndirectDataContent "
            "(файл изменён после подписи)",
        )

    # 2. EKU и издатель.
    eku = _extended_key_usage(signer)
    if OID_EKU_CODE_SIGNING not in eku:
        raise AuthentiCodeError(
            "missing_eku", "сертификат подписанта не имеет Extended Key Usage codeSigning"
        )
    publisher_names = _publisher_names(signer)
    publisher_match: bool | None = None
    if expected_publisher:
        normalized = expected_publisher.strip().casefold()
        publisher_match = any(name.strip().casefold() == normalized for name in publisher_names)
        if not publisher_match:
            raise AuthentiCodeError(
                "publisher_mismatch",
                f"издатель сертификата {publisher_names or ['<нет>']} не совпал с ожидаемым",
            )

    # 3. Метка времени (RFC 3161): наличие, подпись TSA, imprint, время.
    timestamp_attr = None
    for oid, values in outcome.unsigned_attributes.items():
        # id-aa-timeStampToken (RFC 3161) либо устаревшая counterSignature.
        if oid in (OID_PKCS9_TIMESTAMP_TOKEN, "1.2.840.113549.1.9.6"):
            timestamp_attr = values[0].der()
    timestamp_info: dict = {"present": False}
    if timestamp_attr is not None:
        timestamp_info = _parse_timestamp_token(timestamp_attr, outcome.signature)
    if require_timestamp and not timestamp_info["present"]:
        raise AuthentiCodeError(
            "missing_timestamp", "Authenticode-подпись не содержит метки времени (RFC 3161)"
        )

    # 4. Действительность сертификата подписанта на момент подписания.
    if timestamp_info["present"]:
        signed_at = _parse_certificate_time(timestamp_info["gen_time"])
        if signed_at < signer.not_valid_before_utc:
            raise AuthentiCodeError(
                "certificate_expired", "метка времени раньше начала срока действия сертификата"
            )
    else:
        signing_time_attr = _attribute_bytes(outcome.attributes, OID_PKCS9_SIGNING_TIME)
        if signing_time_attr is not None:
            signed_at = _parse_certificate_time(decode_time(parse_one(signing_time_attr)))
        else:
            signed_at = at or datetime.now(UTC)
        if signer.not_valid_before_utc > signed_at or signer.not_valid_after_utc < signed_at:
            raise AuthentiCodeError(
                "certificate_expired", "сертификат подписанта недействителен на момент подписания"
            )

    # 5. Доверие: проверка цепочки только при явно переданных корнях.
    chain_verified: bool | None = None
    if trust_roots:
        _verify_chain(signer, signed_data.certificates, trust_roots, at or datetime.now(UTC))
        chain_verified = True
    timestamp_chain_verified: bool | None = None
    if timestamp_info["present"] and timestamp_roots:
        _verify_chain(
            timestamp_info["certificate"],
            timestamp_info["certificates"],
            timestamp_roots,
            at or datetime.now(UTC),
        )
        timestamp_chain_verified = True

    digests = hashes.SHA256()
    hasher = hashes.Hash(digests)
    hasher.update(data)
    file_sha256 = hasher.finalize().hex()

    return {
        "file": str(path),
        "file_size": len(data),
        "file_sha256": file_sha256,
        "signed": True,
        "chain_verified": chain_verified,
        "timestamp_chain_verified": timestamp_chain_verified,
        "publisher": publisher_names[0] if publisher_names else None,
        "publisher_names": publisher_names,
        "publisher_match": publisher_match,
        "digest_algorithm": _DIGEST_NAMES.get(spc_digest_oid, spc_digest_oid),
        "signer_subject": signer.subject.rfc4514_string(),
        "signer_issuer": signer.issuer.rfc4514_string(),
        "signer_serial": str(signer.serial_number),
        "signer_thumbprint_sha256": signer.fingerprint(hashes.SHA256()).hex(),
        "signer_not_before": signer.not_valid_before_utc.isoformat(),
        "signer_not_after": signer.not_valid_after_utc.isoformat(),
        "signer_eku": eku,
        "timestamp": {
            key: value for key, value in timestamp_info.items() if key not in {"certificate", "certificates"}
        },
        "timestamp_present": bool(timestamp_info["present"]),
    }


def _cmd_verify(args: argparse.Namespace) -> int:
    trust_roots = load_pem_certificates(Path(args.trust_roots)) if args.trust_roots else None
    timestamp_roots = (
        load_pem_certificates(Path(args.timestamp_roots)) if args.timestamp_roots else trust_roots
    )
    result = verify_authenticode(
        Path(args.file),
        expected_publisher=args.expected_publisher,
        require_timestamp=args.require_timestamp,
        at=_parse_at(args.at),
        trust_roots=trust_roots,
        timestamp_roots=timestamp_roots,
    )
    if args.json_out:
        Path(args.json_out).write_text(
            json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(f"Authenticode валиден: {result['signer_subject']}")
        print(f"издатель: {result['publisher']!r}, хеш файла: {result['file_sha256'][:16]}…")
        stamp = result["timestamp"].get("gen_time") if result["timestamp_present"] else "нет"
        print(f"метка времени: {stamp}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Phase 14 Authenticode verification")
    sub = parser.add_subparsers(dest="command", required=True)
    verify = sub.add_parser("verify", help="проверить Authenticode-подпись PE-файла")
    verify.add_argument("--file", required=True)
    verify.add_argument("--expected-publisher", default=None)
    verify.add_argument("--require-timestamp", action="store_true")
    verify.add_argument("--trust-roots", default=None, help="PEM с доверенными корнями")
    verify.add_argument("--timestamp-roots", default=None, help="PEM с корнями TSA")
    verify.add_argument("--at", default=None, help="ISO-время проверки (по умолчанию now UTC)")
    verify.add_argument("--json", action="store_true")
    verify.add_argument("--json-out", default=None)
    verify.set_defaults(func=_cmd_verify)
    args = parser.parse_args(argv)
    try:
        return int(args.func(args))
    except AuthentiCodeError as exc:
        print(f"ОШИБКА[{exc.code}]: {exc}", file=sys.stderr)
        return 1
    except FileNotFoundError as exc:
        print(f"ОШИБКА: файл не найден: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
