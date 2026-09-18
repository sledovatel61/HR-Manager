# -*- coding: utf-8 -*-
"""Phase 14: ТЕСТОВый Authenticode-подписант (ephemeral сертификаты).

Модуль существует ровно для двух задач:

1. автотесты release policy (полный fail-closed контракт на ephemeral
   сертификатах — промпт Phase 14 требует именно этого, пока у агента нет
   настоящего сертификата и GitHub environment);
2. воспроизводимые fixture для end-to-end drill.

Он СОЗНАТЕЛЬНО не используется в production-релизе: настоящий installer
подписывается ``signtool`` на Windows (``installer/sign.ps1``) сертификатом
владельца, который в репозиторий и в CI-PR не попадает. CLI требует явный
флаг ``--allow-test-signing`` и всегда пишет в метаданные ``mode=test``.

Структуры формируются «как в жизни»: PKCS#7 SignedData с
``SpcIndirectDataContent`` (Authenticode-хеш PE), подписанные атрибуты и
RFC 3161 timestamp token (``id-aa-timeStampToken``), чтобы проверяющий код
проходил тот же путь, что и на реальном ``signtool``-файле.
"""

from __future__ import annotations

import argparse
import base64
import json
import sys
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

sys.path.insert(0, str(Path(__file__).resolve().parent))

from authenticode import (  # noqa: E402
    OID_CT_TSTINFO,
    OID_PKCS7_SIGNED_DATA,
    OID_PKCS9_CONTENT_TYPE,
    OID_PKCS9_MESSAGE_DIGEST,
    OID_PKCS9_SIGNING_TIME,
    OID_PKCS9_TIMESTAMP_TOKEN,
    OID_SPC_INDIRECT_DATA,
    OID_SPC_PE_IMAGE_DATA,
    OID_SPC_STATEMENT_TYPE,
    pe_authenticode_digest,
    parse_pe,
)
from der import (  # noqa: E402
    TAG_CONTEXT0,
    TAG_CONTEXT1,
    TAG_SET,
    encode_bit_string,
    encode_generalized_time,
    encode_integer,
    encode_null,
    encode_octet_string,
    encode_oid,
    encode_sequence,
    encode_set,
    encode_tlv,
    encode_utc_time,
    parse_all,
)

OID_INDIVIDUAL_CODE_SIGNING = "1.3.6.1.4.1.311.2.1.21"
DEFAULT_TIMESTAMP_POLICY = "1.3.6.1.4.1.311.2.1.21.1"
PE_SECTION_ALIGNMENT = 0x200


@dataclass
class EphemeralAuthority:
    """Ephemeral PKI для тестов: корень, code-signing и TSA сертификаты."""

    ca_certificate: x509.Certificate
    ca_key: rsa.RSAPrivateKey
    leaf_certificate: x509.Certificate
    leaf_key: rsa.RSAPrivateKey
    tsa_certificate: x509.Certificate
    tsa_key: rsa.RSAPrivateKey
    publisher: str

    def ca_pem(self) -> bytes:
        return self.ca_certificate.public_bytes(serialization.Encoding.PEM)

    def chain_pem(self, include_tsa: bool = False) -> bytes:
        data = self.ca_pem() + self.leaf_certificate.public_bytes(serialization.Encoding.PEM)
        if include_tsa:
            data += self.tsa_certificate.public_bytes(serialization.Encoding.PEM)
        return data


def make_test_pe(payload: bytes = b"HRM-TEST-PE-BODY") -> bytes:
    """Минимальный, но структурно корректный PE32+ образ для тестов."""
    dos_stub = bytearray(b"\x00" * 0x40)
    dos_stub[0:2] = b"MZ"
    dos_stub[0x3C:0x40] = (0x40).to_bytes(4, "little")

    optional = bytearray(240)
    optional[0:2] = (0x20B).to_bytes(2, "little")  # PE32+
    optional[2] = 14  # linker major
    size_of_code = ((len(payload) + PE_SECTION_ALIGNMENT - 1) // PE_SECTION_ALIGNMENT) * PE_SECTION_ALIGNMENT
    optional[4:8] = size_of_code.to_bytes(4, "little")
    optional[16:20] = PE_SECTION_ALIGNMENT.to_bytes(4, "little")  # EntryPoint
    optional[24:32] = (0x140000000).to_bytes(8, "little")  # ImageBase
    optional[32:36] = PE_SECTION_ALIGNMENT.to_bytes(4, "little")  # SectionAlignment
    optional[36:40] = PE_SECTION_ALIGNMENT.to_bytes(4, "little")  # FileAlignment
    optional[56:60] = (PE_SECTION_ALIGNMENT + 40).to_bytes(4, "little")  # SizeOfImage
    optional[60:64] = PE_SECTION_ALIGNMENT.to_bytes(4, "little")  # SizeOfHeaders
    optional[68:70] = (3).to_bytes(2, "little")  # Subsystem: console
    optional[108:112] = (16).to_bytes(4, "little")  # NumberOfRvaAndSizes
    # Data directory entry 4 (Certificate Table) остаётся нулевой до подписи.

    coff = bytearray(20)
    coff[0:2] = (0x8664).to_bytes(2, "little")  # x64
    coff[2:4] = (1).to_bytes(2, "little")  # NumberOfSections
    coff[16:18] = len(optional).to_bytes(2, "little")  # SizeOfOptionalHeader

    section = bytearray(40)
    section[0:8] = b".text\x00\x00\x00"
    section[8:12] = size_of_code.to_bytes(4, "little")  # VirtualSize
    section[12:16] = PE_SECTION_ALIGNMENT.to_bytes(4, "little")  # VirtualAddress
    section[16:20] = size_of_code.to_bytes(4, "little")  # SizeOfRawData
    section[20:24] = PE_SECTION_ALIGNMENT.to_bytes(4, "little")  # PointerToRawData
    section[36:40] = (0x60000020).to_bytes(4, "little")  # Characteristics

    headers = (
        bytes(dos_stub) + b"PE\x00\x00" + bytes(coff) + bytes(optional) + bytes(section)
    )
    if len(headers) > PE_SECTION_ALIGNMENT:
        raise ValueError("тестовый PE: заголовки не помещаются в SizeOfHeaders")
    headers = headers + b"\x00" * (PE_SECTION_ALIGNMENT - len(headers))
    body = payload + b"\x00" * (size_of_code - len(payload))
    data = headers + body
    assert len(data) == PE_SECTION_ALIGNMENT + size_of_code
    return data


def _key_usage() -> x509.KeyUsage:
    return x509.KeyUsage(
        digital_signature=True,
        content_commitment=False,
        key_encipherment=False,
        data_encipherment=False,
        key_agreement=False,
        key_cert_sign=False,
        crl_sign=False,
        encipher_only=False,
        decipher_only=False,
    )


def create_test_authority(
    publisher: str = "HR Manager Test Publisher",
    *,
    now: datetime | None = None,
    leaf_valid_days: int = 365,
    leaf_code_signing_eku: bool = True,
    tsa_time_stamping_eku: bool = True,
) -> EphemeralAuthority:
    """Ephemeral корень + code-signing сертификат + TSA сертификат."""
    moment = now or datetime.now(UTC)
    ca_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    ca_name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "HR Manager Test Root CA")])
    ca_certificate = (
        x509.CertificateBuilder()
        .subject_name(ca_name)
        .issuer_name(ca_name)
        .public_key(ca_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(moment - timedelta(days=1))
        .not_valid_after(moment + timedelta(days=3650))
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .add_extension(
            x509.KeyUsage(
                digital_signature=True,
                content_commitment=False,
                key_encipherment=False,
                data_encipherment=False,
                key_agreement=False,
                key_cert_sign=True,
                crl_sign=True,
                encipher_only=False,
                decipher_only=False,
            ),
            critical=True,
        )
        .sign(ca_key, hashes.SHA256())
    )

    leaf_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    leaf_certificate = (
        x509.CertificateBuilder()
        .subject_name(
            x509.Name(
                [
                    x509.NameAttribute(NameOID.ORGANIZATION_NAME, publisher),
                    x509.NameAttribute(NameOID.COMMON_NAME, publisher),
                ]
            )
        )
        .issuer_name(ca_certificate.subject)
        .public_key(leaf_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(moment - timedelta(days=1))
        .not_valid_after(moment + timedelta(days=leaf_valid_days))
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(_key_usage(), critical=True)
        .add_extension(
            x509.ExtendedKeyUsage(
                [ExtendedKeyUsageOID.CODE_SIGNING]
                if leaf_code_signing_eku
                else [ExtendedKeyUsageOID.CLIENT_AUTH]
            ),
            critical=True,
        )
        .sign(ca_key, hashes.SHA256())
    )

    tsa_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    tsa_certificate = (
        x509.CertificateBuilder()
        .subject_name(
            x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "HR Manager Test TSA")])
        )
        .issuer_name(ca_certificate.subject)
        .public_key(tsa_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(moment - timedelta(days=1))
        .not_valid_after(moment + timedelta(days=3650))
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(_key_usage(), critical=True)
        .add_extension(
            x509.ExtendedKeyUsage(
                [ExtendedKeyUsageOID.TIME_STAMPING]
                if tsa_time_stamping_eku
                else [ExtendedKeyUsageOID.CODE_SIGNING]
            ),
            critical=True,
        )
        .sign(ca_key, hashes.SHA256())
    )
    return EphemeralAuthority(
        ca_certificate=ca_certificate,
        ca_key=ca_key,
        leaf_certificate=leaf_certificate,
        leaf_key=leaf_key,
        tsa_certificate=tsa_certificate,
        tsa_key=tsa_key,
        publisher=publisher,
    )


def _issuer_and_serial(certificate: x509.Certificate) -> bytes:
    return encode_sequence(
        certificate.issuer.public_bytes(),
        encode_integer(certificate.serial_number),
    )


def _attribute(oid: str, encoded_values: bytes) -> bytes:
    return encode_sequence(encode_oid(oid), encode_set(encoded_values))


def _sign_attrs(signed_attrs_content: bytes, key: rsa.RSAPrivateKey) -> bytes:
    signed_attrs_der = encode_tlv(TAG_SET, signed_attrs_content)
    return key.sign(signed_attrs_der, padding.PKCS1v15(), hashes.SHA256())


def _spc_indirect_data(pe_digest: bytes) -> bytes:
    pe_image_data = encode_sequence(
        encode_bit_string(b"", unused_bits=0),
        encode_tlv(TAG_CONTEXT0, encode_sequence(encode_tlv(TAG_CONTEXT0, encode_sequence()))),
    )
    attribute = encode_sequence(
        encode_oid(OID_SPC_PE_IMAGE_DATA),
        encode_tlv(TAG_CONTEXT0, pe_image_data),
    )
    digest_info = encode_sequence(
        encode_sequence(encode_oid("2.16.840.1.101.3.4.2.1"), encode_null()),
        encode_octet_string(pe_digest),
    )
    return encode_sequence(attribute, digest_info)


def _timestamp_token(
    signer_signature: bytes,
    authority: EphemeralAuthority,
    *,
    gen_time: datetime,
    serial: int = 1,
) -> bytes:
    imprint = hashes.Hash(hashes.SHA256())
    imprint.update(signer_signature)
    tst_info = encode_sequence(
        encode_integer(1),
        encode_oid(DEFAULT_TIMESTAMP_POLICY),
        encode_sequence(
            encode_sequence(encode_oid("2.16.840.1.101.3.4.2.1"), encode_null()),
            encode_octet_string(imprint.finalize()),
        ),
        encode_integer(serial),
        encode_generalized_time(gen_time.strftime("%Y%m%d%H%M%SZ")),
    )
    tst_digest = hashes.Hash(hashes.SHA256())
    tst_digest.update(tst_info)
    attributes = b"".join(
        [
            _attribute(OID_PKCS9_CONTENT_TYPE, encode_oid(OID_CT_TSTINFO)),
            _attribute(OID_PKCS9_MESSAGE_DIGEST, encode_octet_string(tst_digest.finalize())),
            _attribute(OID_PKCS9_SIGNING_TIME, encode_utc_time(gen_time.strftime("%y%m%d%H%M%SZ"))),
        ]
    )
    signature = authority.tsa_key.sign(
        encode_tlv(TAG_SET, attributes),
        padding.PKCS1v15(),
        hashes.SHA256(),
    )
    signer_info = encode_sequence(
        encode_integer(3),
        _issuer_and_serial(authority.tsa_certificate),
        encode_sequence(encode_oid("2.16.840.1.101.3.4.2.1"), encode_null()),
        encode_tlv(TAG_CONTEXT0, attributes),
        encode_sequence(encode_oid("1.2.840.113549.1.1.1"), encode_null()),
        encode_octet_string(signature),
    )
    certificates = encode_tlv(
        TAG_CONTEXT0,
        authority.tsa_certificate.public_bytes(serialization.Encoding.DER)
        + authority.ca_certificate.public_bytes(serialization.Encoding.DER),
    )
    signed_data = encode_sequence(
        encode_integer(3),
        encode_set(encode_sequence(encode_oid("2.16.840.1.101.3.4.2.1"), encode_null())),
        encode_sequence(
            encode_oid(OID_CT_TSTINFO), encode_tlv(TAG_CONTEXT0, tst_info)
        ),
        certificates,
        encode_set(signer_info),
    )
    return encode_sequence(
        encode_oid(OID_PKCS7_SIGNED_DATA), encode_tlv(TAG_CONTEXT0, signed_data)
    )


def build_signed_pkcs7(
    pe_bytes: bytes,
    authority: EphemeralAuthority,
    *,
    with_timestamp: bool = True,
    signed_at: datetime | None = None,
) -> bytes:
    """PKCS#7 SignedData с Authenticode-содержимым (и меткой времени)."""
    moment = signed_at or datetime.now(UTC)
    pe = parse_pe(pe_bytes)
    pe_digest = pe_authenticode_digest(pe_bytes, pe, hashes.SHA256())
    spc = _spc_indirect_data(pe_digest)
    spc_digest = hashes.Hash(hashes.SHA256())
    spc_digest.update(spc)

    attributes = b"".join(
        [
            _attribute(OID_PKCS9_CONTENT_TYPE, encode_oid(OID_SPC_INDIRECT_DATA)),
            _attribute(OID_PKCS9_MESSAGE_DIGEST, encode_octet_string(spc_digest.finalize())),
            _attribute(OID_PKCS9_SIGNING_TIME, encode_utc_time(moment.strftime("%y%m%d%H%M%SZ"))),
            _attribute(
                OID_SPC_STATEMENT_TYPE, encode_oid(OID_INDIVIDUAL_CODE_SIGNING)
            ),
        ]
    )
    signature = authority.leaf_key.sign(
        encode_tlv(TAG_SET, attributes),
        padding.PKCS1v15(),
        hashes.SHA256(),
    )
    unsigned_attrs = b""
    if with_timestamp:
        token = _timestamp_token(signature, authority, gen_time=moment)
        unsigned_attrs = encode_tlv(
            TAG_CONTEXT1, _attribute(OID_PKCS9_TIMESTAMP_TOKEN, token)
        )
    signer_info = encode_sequence(
        encode_integer(1),
        _issuer_and_serial(authority.leaf_certificate),
        encode_sequence(encode_oid("2.16.840.1.101.3.4.2.1"), encode_null()),
        encode_tlv(TAG_CONTEXT0, attributes),
        encode_sequence(encode_oid("1.2.840.113549.1.1.1"), encode_null()),
        encode_octet_string(signature),
        unsigned_attrs,
    )
    certificates = encode_tlv(
        TAG_CONTEXT0,
        authority.leaf_certificate.public_bytes(serialization.Encoding.DER)
        + authority.ca_certificate.public_bytes(serialization.Encoding.DER),
    )
    signed_data = encode_sequence(
        encode_integer(1),
        encode_set(encode_sequence(encode_oid("2.16.840.1.101.3.4.2.1"), encode_null())),
        # Authenticode (PKCS#7): содержимое лежит в [0] как структура, без
        # промежуточного OCTET STRING.
        encode_sequence(encode_oid(OID_SPC_INDIRECT_DATA), encode_tlv(TAG_CONTEXT0, spc)),
        certificates,
        encode_set(signer_info),
    )
    return encode_sequence(
        encode_oid(OID_PKCS7_SIGNED_DATA), encode_tlv(TAG_CONTEXT0, signed_data)
    )


def embed_signature(pe_bytes: bytes, pkcs7: bytes) -> bytes:
    """Встраивает подпись в таблицу сертификатов PE (WIN_CERTIFICATE)."""
    data = bytearray(pe_bytes)
    pe = parse_pe(bytes(data))
    length = 8 + len(pkcs7)
    padded = (length + 7) & ~7
    record = (
        length.to_bytes(4, "little")
        + (0x0200).to_bytes(2, "little")
        + (0x0002).to_bytes(2, "little")
        + pkcs7
        + b"\x00" * (padded - length)
    )
    offset = len(data)
    data.extend(record)
    data[pe.cert_entry_offset : pe.cert_entry_offset + 4] = offset.to_bytes(4, "little")
    data[pe.cert_entry_offset + 4 : pe.cert_entry_offset + 8] = padded.to_bytes(4, "little")
    return bytes(data)


def sign_test_pe(
    pe_bytes: bytes,
    authority: EphemeralAuthority,
    *,
    with_timestamp: bool = True,
    signed_at: datetime | None = None,
) -> bytes:
    """PE с Authenticode-подписью (ephemeral сертификат): тесты и drill."""
    pkcs7 = build_signed_pkcs7(
        pe_bytes, authority, with_timestamp=with_timestamp, signed_at=signed_at
    )
    return embed_signature(pe_bytes, pkcs7)


def _cmd_sign(args: argparse.Namespace) -> int:
    if not args.allow_test_signing:
        raise SystemExit(
            "ОТКАЗ[test_signing_disabled]: тестовый подписант требует --allow-test-signing; "
            "production installer подписывается signtool (installer/sign.ps1)"
        )
    source = Path(args.file)
    pe_bytes = source.read_bytes() if source.exists() else make_test_pe()
    if args.pe_out:
        Path(args.pe_out).write_bytes(pe_bytes)
    authority = create_test_authority(args.publisher)
    signed = sign_test_pe(pe_bytes, authority, with_timestamp=not args.no_timestamp)
    Path(args.out).write_bytes(signed)
    if args.ca_out:
        Path(args.ca_out).write_bytes(authority.chain_pem(include_tsa=True))
    attestation = {
        "mode": "test",
        "authenticode_present": True,
        "timestamp_present": not args.no_timestamp,
        "publisher": authority.publisher,
        "signer_thumbprint_sha256": authority.leaf_certificate.fingerprint(hashes.SHA256()).hex(),
        "ca_sha256": authority.ca_certificate.fingerprint(hashes.SHA256()).hex(),
        "tool": "infra/release/sign_authenticode.py",
    }
    if args.attestation_out:
        Path(args.attestation_out).write_text(
            json.dumps(attestation, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
    print(f"тестовый PE подписан: {args.out} (mode=test, timestamp={not args.no_timestamp})")
    print(f"CA (PEM): {args.ca_out or '<не сохранён>'}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Phase 14 test Authenticode signer")
    sub = parser.add_subparsers(dest="command", required=True)
    sign = sub.add_parser("sign", help="подписать PE тестовым сертификатом (только тесты)")
    sign.add_argument("--file", default=None, help="существующий PE; иначе создаётся тестовый")
    sign.add_argument("--out", required=True)
    sign.add_argument("--publisher", default="HR Manager Test Publisher")
    sign.add_argument("--ca-out", default=None)
    sign.add_argument("--pe-out", default=None)
    sign.add_argument("--attestation-out", default=None)
    sign.add_argument("--no-timestamp", action="store_true")
    sign.add_argument("--allow-test-signing", action="store_true")
    sign.set_defaults(func=_cmd_sign)
    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    sys.exit(main())
