"""Authenticode-тестовый инструментарий (ТОЛЬКО тесты, Phase 14).

Создаёт на лету, в памяти каждого прогона:
* эфемерные CA/подписант/TSA-сертификаты (RSA 2048, EKU Code Signing /
  Time Stamping); никаких production-ключей — ничего не сохраняется и не
  покидает процесс теста;
* минимальный корректный PE32+ (структурные заголовки + certificate table);
* Authenticode-подпись (PKCS#7 SignedData + SPC_INDIRECT_DATA + RFC 3161
  timestamp через общий сборщик drill_tsa_server.build_timestamp_token).

Сборщик подписей тестового контура не является production-кодом и не
используется конвейером выпуска: он существует, чтобы проверять
infra/release/authenticode_verify.py на контролируемых структурах,
включая все негативные сценарии (подмена, перенос подписи, SHA-1 и т.д.).
"""

from __future__ import annotations

import datetime
import hashlib
import struct
import sys
from dataclasses import dataclass
from pathlib import Path

from asn1crypto import cms, core
from asn1crypto import x509 as asn1x509
from asn1crypto.algos import DigestAlgorithm
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa

RELEASE_DIR = Path(__file__).resolve().parents[2] / "infra" / "release"
SCRIPTS_DIR = Path(__file__).resolve().parents[2] / "infra" / "scripts"
for _path in (str(RELEASE_DIR), str(SCRIPTS_DIR)):
    if _path not in sys.path:
        sys.path.insert(0, _path)

from authenticode_verify import (  # type: ignore[import-not-found]  # noqa: E402
    SpcIndirectDataContent,
)
from drill_tsa_server import (  # type: ignore[import-not-found]  # noqa: E402
    build_timestamp_token,
)

SPC_INDIRECT_DATA_OID = "1.3.6.1.4.1.311.2.1.4"
SPC_PE_IMAGE_DATA_OID = "1.3.6.1.4.1.311.2.1.15"
OID_RFC3161_TIMESTAMP_TOKEN = "1.2.840.113549.1.9.16.2.14"
OID_SHA256 = "2.16.840.1.101.3.4.2.1"
OID_SHA1 = "1.3.14.3.2.26"

_DIGEST_BY_NAME = {"sha256": OID_SHA256, "sha1": OID_SHA1}
_HASH_BY_NAME = {"sha256": hashes.SHA256, "sha1": hashes.SHA1}

NOW = datetime.datetime(2026, 9, 11, 12, 0, 0, tzinfo=datetime.UTC)


@dataclass
class KeyPair:
    cert: x509.Certificate
    key: rsa.RSAPrivateKey

    @property
    def cert_pem(self) -> bytes:
        return self.cert.public_bytes(serialization.Encoding.PEM)


def _self_signed_ca(common_name: str) -> KeyPair:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(x509.NameOID.COMMON_NAME, common_name)])
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(NOW - datetime.timedelta(days=1))
        .not_valid_after(NOW + datetime.timedelta(days=365))
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .add_extension(
            x509.KeyUsage(
                digital_signature=False,
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
        .sign(key, hashes.SHA256())
    )
    return KeyPair(cert, key)


def _issue_cert(
    ca: KeyPair,
    common_name: str,
    *,
    eku_oids: list[str] | None,
    key: rsa.RSAPrivateKey | None = None,
    add_eku: bool = True,
) -> KeyPair:
    key = key or rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(x509.NameOID.COMMON_NAME, common_name)])
    builder = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(ca.cert.subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(NOW - datetime.timedelta(days=1))
        .not_valid_after(NOW + datetime.timedelta(days=365))
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
    )
    if add_eku and eku_oids is not None:
        builder = builder.add_extension(
            x509.ExtendedKeyUsage([x509.ObjectIdentifier(oid) for oid in eku_oids]), critical=False
        )
    cert = builder.sign(ca.key, hashes.SHA256())
    return KeyPair(cert, key)


def make_world() -> dict[str, KeyPair]:
    """Два независимых дерева (production-контур теста и «чужой») + TSA."""
    prod_ca = _self_signed_ca("HR Manager Test Production Root")
    test_ca = _self_signed_ca("HR Manager Test Fixture Root")
    other_ca = _self_signed_ca("Unrelated Root")
    code_eku = ["1.3.6.1.5.5.7.3.3"]
    tsa_eku = ["1.3.6.1.5.5.7.3.8"]
    return {
        "prod_ca": prod_ca,
        "test_ca": test_ca,
        "other_ca": other_ca,
        "prod_signer": _issue_cert(prod_ca, "HR Manager Pilot", eku_oids=code_eku),
        "test_signer": _issue_cert(test_ca, "HR Manager CI Test Signing", eku_oids=code_eku),
        "wrong_cn_signer": _issue_cert(prod_ca, "Someone Else", eku_oids=code_eku),
        "no_eku_signer": _issue_cert(prod_ca, "HR Manager Pilot", eku_oids=None, add_eku=False),
        "prod_tsa": _issue_cert(prod_ca, "HR Manager Test TSA", eku_oids=tsa_eku),
        "test_tsa": _issue_cert(test_ca, "HR Manager Test TSA", eku_oids=tsa_eku),
        "other_tsa": _issue_cert(other_ca, "Unrelated TSA", eku_oids=tsa_eku),
        "wrong_eku_tsa": _issue_cert(prod_ca, "HR Manager Test TSA", eku_oids=code_eku),
        "no_eku_tsa": _issue_cert(prod_ca, "HR Manager Test TSA", eku_oids=None, add_eku=False),
    }


# --- Минимальный PE -----------------------------------------------------------


def build_pe(body: bytes = b"synthetic-code", cert_table: bytes | None = None) -> bytes:
    """Минимальный структурно корректный PE32+ (без реальных секций).

    CheckSum и Security Directory обнулены — как того требует расчёт
    digest при подписи; certificate table добавляется в конец файла.
    """
    e_lfanew = 0x80
    optional_size = 112 + 16 * 8  # фиксированная часть PE32+ + 16 директорий
    header_size = e_lfanew + 4 + 20 + optional_size
    if cert_table is None:
        cert_off = 0
        cert_size = 0
        total = header_size + len(body)
    else:
        cert_off = header_size + len(body)
        cert_size = len(cert_table)
        total = cert_off + cert_size
    buf = bytearray(total)
    buf[0:2] = b"MZ"
    struct.pack_into("<I", buf, 0x3C, e_lfanew)
    buf[e_lfanew : e_lfanew + 4] = b"PE\x00\x00"
    coff = e_lfanew + 4
    struct.pack_into("<H", buf, coff, 0x8664)  # Machine = x64
    struct.pack_into("<H", buf, coff + 2, 0)  # NumberOfSections
    struct.pack_into("<H", buf, coff + 16, optional_size)
    struct.pack_into("<H", buf, coff + 18, 0x0022)  # EXECUTABLE_IMAGE|LARGE_ADDRESS_AWARE
    opt = coff + 20
    struct.pack_into("<H", buf, opt, 0x20B)  # PE32+
    struct.pack_into("<I", buf, opt + 4, len(body))  # SizeOfCode
    struct.pack_into("<Q", buf, opt + 24, 0x140000000)  # ImageBase
    struct.pack_into("<I", buf, opt + 56, 0x1000)  # SizeOfImage
    struct.pack_into("<I", buf, opt + 60, header_size)  # SizeOfHeaders
    struct.pack_into("<I", buf, opt + 108, 16)  # NumberOfRvaAndSizes
    struct.pack_into("<II", buf, opt + 144, cert_off, cert_size)  # Security dir
    buf[header_size : header_size + len(body)] = body
    if cert_table is not None:
        buf[cert_off : cert_off + cert_size] = cert_table
    return bytes(buf)


def win_cert_table(payloads: list[bytes]) -> bytes:
    """Certificate table из WIN_CERTIFICATE-записей (8-байтовое выравнивание)."""
    out = bytearray()
    for payload in payloads:
        entry_len = 8 + len(payload)
        out += struct.pack("<IHH", entry_len, 0x0200, 0x0002) + payload
        out += b"\x00" * ((-entry_len) % 8)
    return bytes(out)


# --- Сборка Authenticode-подписи -----------------------------------------------


def _signer_info(
    signer: KeyPair,
    spc_der: bytes,
    attrs_digest_name: str,
    *,
    unsigned_attrs: cms.CMSAttributes | None = None,
) -> tuple[cms.SignerInfo, bytes]:
    hash_cls = _HASH_BY_NAME[attrs_digest_name]
    digest = hashes.Hash(hash_cls())
    digest.update(spc_der)
    attrs = cms.CMSAttributes(
        [
            cms.CMSAttribute({"type": "content_type", "values": [SPC_INDIRECT_DATA_OID]}),
            cms.CMSAttribute(
                {"type": "message_digest", "values": [core.OctetString(digest.finalize())]}
            ),
        ]
    )
    attrs_der = attrs.dump()
    signed_bytes = b"\xa0" + attrs_der[1:] if attrs_der[0] == 0x31 else attrs_der
    signature = signer.key.sign(signed_bytes, padding.PKCS1v15(), hash_cls())
    fields: dict[str, object] = {
        "version": 1,
        "sid": cms.SignerIdentifier(
            {
                "issuer_and_serial_number": {
                    "issuer": asn1x509.Name.load(
                        signer.cert.issuer.public_bytes(serialization.Encoding.DER)
                    ),
                    "serial_number": signer.cert.serial_number,
                }
            }
        ),
        "digest_algorithm": {"algorithm": attrs_digest_name},
        "signed_attrs": attrs,
        "signature_algorithm": {"algorithm": "rsassa_pkcs1v15", "parameters": core.Null},
        "signature": signature,
    }
    if unsigned_attrs is not None:
        fields["unsigned_attrs"] = unsigned_attrs
    return cms.SignerInfo(fields), signature


def sign_pe(
    body: bytes,
    signer: KeyPair,
    tsa: KeyPair | None = None,
    *,
    digest_name: str = "sha256",
    timestamp: bool = True,
    spc_digest_override: bytes | None = None,
    timestamp_imprint_override: bytes | None = None,
    corrupt_timestamp: bool = False,
    gen_time: datetime.datetime | None = None,
) -> bytes:
    """Подписать минимальный PE тестовым подписантом (с TSA или без)."""
    unsigned_pe = build_pe(body)
    digest_oid = _DIGEST_BY_NAME[digest_name]
    image_digest = hashlib.new(digest_name, unsigned_pe).digest()
    if spc_digest_override is not None:
        image_digest = spc_digest_override

    spc = SpcIndirectDataContent(
        {
            "data": {"type": SPC_PE_IMAGE_DATA_OID},
            "message_digest": {
                "digest_algorithm": DigestAlgorithm({"algorithm": digest_oid}),
                "digest": image_digest,
            },
        }
    )
    spc_der = spc.dump()

    unsigned_attrs = None
    if timestamp:
        _si_probe, _signature_probe = _signer_info(signer, spc_der, digest_name)
        imprint = (
            timestamp_imprint_override
            if timestamp_imprint_override is not None
            else hashlib.sha256(_signature_probe).digest()
        )
        token_der = build_timestamp_token(
            hash_algorithm_oid=OID_SHA256,
            imprint=imprint,
            tsa_cert=tsa.cert if tsa is not None else signer.cert,
            tsa_key=tsa.key if tsa is not None else signer.key,
            gen_time=gen_time or NOW,
            serial_number=1,
        )
        if corrupt_timestamp:
            # «Повреждённый» timestamp: меняем байт в genTime внутри TSTInfo —
            # DER остаётся парсимым, но подпись TSA над TSTInfo перестаёт сходиться.
            marker = NOW.strftime("%Y%m%d").encode("ascii")
            pos = token_der.find(marker)
            assert pos > 0, "genTime не найден в токене"
            damaged = bytearray(token_der)
            damaged[pos + 7] = ord("9") if damaged[pos + 7] != ord("9") else ord("8")
            token_der = bytes(damaged)
        unsigned_attrs = cms.CMSAttributes(
            [
                cms.CMSAttribute(
                    {
                        "type": OID_RFC3161_TIMESTAMP_TOKEN,
                        "values": [cms.ContentInfo.load(token_der)],
                    }
                )
            ]
        )

    signer_info, _ = _signer_info(signer, spc_der, digest_name, unsigned_attrs=unsigned_attrs)
    signed_data = cms.SignedData(
        {
            "version": 1,
            "digest_algorithms": [{"algorithm": digest_name}],
            "encap_content_info": cms.ContentInfo(
                {"content_type": SPC_INDIRECT_DATA_OID, "content": spc.retag("explicit", 0)}
            ),
            "certificates": [
                cms.CertificateChoices(
                    {
                        "certificate": asn1x509.Certificate.load(
                            signer.cert.public_bytes(serialization.Encoding.DER)
                        )
                    }
                )
            ],
            "signer_infos": [signer_info],
        }
    )
    token = cms.ContentInfo({"content_type": "signed_data", "content": signed_data})
    return build_pe(body, cert_table=win_cert_table([token.dump()]))
