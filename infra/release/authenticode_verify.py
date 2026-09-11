# -*- coding: utf-8 -*-
"""Независимая проверка Authenticode-подписи Windows-установщика (Phase 14).

Проверяет ФАКТИЧЕСКИЕ БАЙТЫ PE-файла — signing manifest/attestation и
строковые поля о «результате signtool» НЕ являются источником истины
(manifest остаётся provenance и сверяется отдельным release-gate).

Что подтверждается (fail closed, любое нарушение — AuthenticodeError):

* файл структурно корректен как PE (MZ/PE\\0\\0, optional header, data
  directories, certificate table в пределах файла);
* Authenticode-подпись присутствует (certificate table, WIN_CERT_TYPE
  _PKCS_SIGNED_PKT, ревизия 2.0);
* digest содержимого PE совпадает с подписанным SpcIndirectData
  (любое изменение файла после подписи и перенос подписи на другой
  файл обнаруживаются как ``digest_mismatch``);
* CMS/PKCS#7-подпись криптографически корректна: подпись по DER
  signedAttrs, атрибуты content-type и message-digest сверены;
* сертификат подписанта имеет EKU Code Signing, действителен на момент
  подписи и издатель (publisher/Subject) совпадает с ожидаемым;
* цепочка сертификатов доводится до ЯВНО ЗАДАННОГО доверенного root
  (никаких неявных системных хранилищ; корни передаются вызовом);
* RFC 3161 timestamp присутствует и криптографически валиден:
  messageImprint = hash(подписи подписанта), подпись TSA корректна,
  TSTInfo не изменён, сертификат TSA имеет EKU Time Stamping и цепочку
  до явно заданного TSA-root, genTime внутри срока действия сертификатов;
* политика дайджестов: SHA-1/MD5 запрещены, требуется SHA-256+.

Legacy-формат timestamp Microsoft (OID 1.3.6.1.4.1.311.2.4.1) не
поддерживается: конвейер подписывает через ``signtool /tr`` (RFC 3161),
поэтому legacy-штамп — отказ с явным кодом (fail closed, без молчаливого
ослабления).

Зависимости: cryptography (уже в контракте релиза) + asn1crypto
(чистый Python, MIT; парсер CMS/ASN.1 — самописный DER-парсер в
криптографическом коде был бы источником уязвимостей, поэтому используется
выверенная библиотека). Новых runtime-зависимостей приложения нет.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import struct
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from asn1crypto import cms as asn1cms
from asn1crypto import core, tsp
from asn1crypto.algos import DigestInfo
from asn1crypto import x509 as asn1x509
from cryptography import x509
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec, ed448, ed25519, padding, rsa

# --- OID / константы ----------------------------------------------------------

SPC_INDIRECT_DATA_OID = "1.3.6.1.4.1.311.2.1.4"
SPC_PE_IMAGE_DATA_OID = "1.3.6.1.4.1.311.2.1.15"
OID_CODE_SIGNING_EKU = "1.3.6.1.5.5.7.3.3"
OID_TIME_STAMPING_EKU = "1.3.6.1.5.5.7.3.8"
OID_ATTR_CONTENT_TYPE = "1.2.840.113549.1.9.3"
OID_ATTR_MESSAGE_DIGEST = "1.2.840.113549.1.9.4"
OID_RFC3161_TIMESTAMP_TOKEN = "1.2.840.113549.1.9.16.2.14"
OID_LEGACY_MS_TIMESTAMP = "1.3.6.1.4.1.311.2.4.1"
OID_CT_TSTINFO = "1.2.840.113549.1.9.16.1.4"

OID_RSA_ENCRYPTION = "1.2.840.113549.1.1.1"
OID_RSASSA_PSS = "1.2.840.113549.1.1.10"
_RSA_SHA_SUFFIX_OIDS = {
    "1.2.840.113549.1.1.11",
    "1.2.840.113549.1.1.12",
    "1.2.840.113549.1.1.13",
    "1.2.840.113549.1.1.14",
}
_OID_ECDSA_SHA256 = "1.2.840.10045.4.3.2"
_OID_ECDSA_SHA384 = "1.2.840.10045.4.3.3"
_OID_ECDSA_SHA512 = "1.2.840.10045.4.3.4"
_OID_ED25519 = "1.3.101.112"

WIN_CERT_REVISION_2_0 = 0x0200
WIN_CERT_TYPE_PKCS_SIGNED = 0x0002

# digest OID -> (имя, конструктор хеша, разрешён production-политикой)
_DIGESTS: dict[str, tuple[str, type[hashes.HashAlgorithm], bool]] = {
    "1.3.14.3.2.26": ("sha1", hashes.SHA1, False),
    "1.2.840.113549.2.5": ("md5", hashes.MD5, False),
    "2.16.840.1.101.3.4.2.1": ("sha256", hashes.SHA256, True),
    "2.16.840.1.101.3.4.2.2": ("sha384", hashes.SHA384, True),
    "2.16.840.1.101.3.4.2.3": ("sha512", hashes.SHA512, True),
}

_MAX_CHAIN_DEPTH = 8


class AuthenticodeError(ValueError):
    """Нарушение Authenticode-контракта: безопасный код + сообщение."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


# --- ASN.1-схемы (Authenticode SPC) -------------------------------------------


class SpcAttributeTypeAndOptionalValue(core.Sequence):
    _fields = [
        ("type", core.ObjectIdentifier),
        ("value", core.Any, {"optional": True}),
    ]


class SpcIndirectDataContent(core.Sequence):
    """SpcIndirectDataContent ::= SEQUENCE { data, messageDigest DigestInfo }."""

    _fields = [
        ("data", SpcAttributeTypeAndOptionalValue),
        ("message_digest", DigestInfo),
    ]


# --- Отчёт --------------------------------------------------------------------


@dataclass
class AuthenticodeReport:
    """Результат независимой проверки (только публичные факты)."""

    ok: bool = True
    problems: list[str] = field(default_factory=list)
    signer_subject: str = ""
    digest_algorithm: str = ""
    signing_time: datetime | None = None
    chain_subjects: list[str] = field(default_factory=list)
    tsa_subject: str = ""

    def fail(self, error: AuthenticodeError) -> None:
        self.ok = False
        self.problems.append(f"{error.code}: {error.message}")

    def to_dict(self) -> dict[str, object]:
        return {
            "ok": self.ok,
            "problems": self.problems,
            "signer_subject": self.signer_subject,
            "digest_algorithm": self.digest_algorithm,
            "signing_time": (
                self.signing_time.astimezone(timezone.utc).isoformat()
                if self.signing_time
                else None
            ),
            "chain_subjects": self.chain_subjects,
            "tsa_subject": self.tsa_subject,
        }


# --- PE-парсер ----------------------------------------------------------------


@dataclass
class _PeInfo:
    cert_table_offset: int
    cert_table_size: int
    checksum_pos: int
    security_dir_pos: int


def _parse_pe(data: bytes) -> _PeInfo:
    if len(data) < 0x40 or data[:2] != b"MZ":
        raise AuthenticodeError("not_pe", "файл не является PE (нет MZ-заголовка)")
    e_lfanew = struct.unpack_from("<I", data, 0x3C)[0]
    if e_lfanew <= 0 or e_lfanew + 24 > len(data):
        raise AuthenticodeError("not_pe", "e_lfanew вне файла")
    if data[e_lfanew : e_lfanew + 4] != b"PE\x00\x00":
        raise AuthenticodeError("not_pe", "нет подписи PE\\0\\0")
    coff = e_lfanew + 4
    num_sections = struct.unpack_from("<H", data, coff + 2)[0]
    size_optional = struct.unpack_from("<H", data, coff + 16)[0]
    if size_optional < 128:
        raise AuthenticodeError("not_pe", "optional header слишком мал")
    opt = coff + 20
    magic = struct.unpack_from("<H", data, opt)[0]
    if magic not in (0x10B, 0x20B):
        raise AuthenticodeError("not_pe", "неизвестный magic optional header")
    num_rva_pos = opt + (92 if magic == 0x10B else 108)
    data_dir_pos = opt + (96 if magic == 0x10B else 112)
    if data_dir_pos + 5 * 8 > opt + size_optional:
        raise AuthenticodeError("not_pe", "data directories не помещаются в optional header")
    num_rva = struct.unpack_from("<I", data, num_rva_pos)[0]
    if num_rva < 5:
        raise AuthenticodeError("not_pe", "таблица data directories короче 5 записей")
    # Секционные заголовки обязаны помещаться в файл до certificate table.
    if opt + size_optional + num_sections * 40 > len(data):
        raise AuthenticodeError("not_pe", "секционные заголовки вне файла")
    security_dir_pos = data_dir_pos + 4 * 8
    cert_off = struct.unpack_from("<I", data, security_dir_pos)[0]
    cert_size = struct.unpack_from("<I", data, security_dir_pos + 4)[0]
    if cert_size == 0 or cert_off == 0:
        raise AuthenticodeError("unsigned_pe", "certificate table отсутствует — файл не подписан")
    if cert_off + cert_size > len(data):
        raise AuthenticodeError("bad_pe", "certificate table выходит за пределы файла")
    return _PeInfo(cert_off, cert_size, opt + 64, security_dir_pos)


def _pe_digest(data: bytes, pe: _PeInfo, hash_cls: type[hashes.HashAlgorithm]) -> bytes:
    """Digest образа PE по правилам Authenticode.

    Хешируется всё, кроме certificate table, при обнулённых полях CheckSum и
    Security Directory; байты после certificate table (если есть) тоже входят.
    """
    masks = [(pe.checksum_pos, 4), (pe.security_dir_pos, 8)]
    regions = [(0, pe.cert_table_offset), (pe.cert_table_offset + pe.cert_table_size, len(data))]
    digest = hashes.Hash(hash_cls())
    for lo, hi in regions:
        pos = lo
        cuts = sorted(
            (max(lo, start), min(hi, start + length))
            for start, length in masks
            if start < hi and start + length > lo
        )
        for cut_start, cut_end in cuts:
            if cut_start > pos:
                digest.update(data[pos:cut_start])
            digest.update(b"\x00" * (cut_end - cut_start))
            pos = cut_end
        if pos < hi:
            digest.update(data[pos:hi])
    return digest.finalize()


def _cert_table_payloads(data: bytes, pe: _PeInfo) -> list[bytes]:
    """DER-полезные нагрузки PKCS#7-записей certificate table."""
    payloads: list[bytes] = []
    off = pe.cert_table_offset
    end = off + pe.cert_table_size
    while off < end:
        if all(b == 0 for b in data[off:end]):
            break  # выравнивающий нулевой хвост последней записи
        if off + 8 > end:
            raise AuthenticodeError("bad_signature_structure", "обрезанная запись WIN_CERTIFICATE")
        entry_len, revision, cert_type = struct.unpack_from("<IHH", data, off)
        if entry_len < 8 or off + entry_len > end:
            raise AuthenticodeError("bad_signature_structure", "dwLength WIN_CERTIFICATE вне таблицы")
        if revision != WIN_CERT_REVISION_2_0:
            raise AuthenticodeError("bad_signature_structure", "неподдерживаемая ревизия WIN_CERTIFICATE")
        if cert_type == WIN_CERT_TYPE_PKCS_SIGNED:
            payloads.append(data[off + 8 : off + entry_len])
        off = (off + entry_len + 7) & ~7
    if not payloads:
        raise AuthenticodeError("unsigned_pe", "в certificate table нет PKCS#7-подписи")
    return payloads


# --- Сертификаты и цепочки ----------------------------------------------------


def _load_cms_certs(signed_data: asn1cms.SignedData) -> list[x509.Certificate]:
    certs: list[x509.Certificate] = []
    if signed_data["certificates"] is not None:
        for choice in signed_data["certificates"]:
            if choice.name == "certificate":
                certs.append(x509.load_der_x509_certificate(choice.chosen.dump()))
    return certs


def _verify_cert_signature(cert: x509.Certificate, issuer: x509.Certificate) -> bool:
    public_key = issuer.public_key()
    try:
        params = cert.signature_algorithm_parameters
        if isinstance(public_key, rsa.RSAPublicKey):
            if isinstance(params, padding.PSS):
                public_key.verify(cert.signature, cert.tbs_certificate_bytes, params)
            else:
                public_key.verify(
                    cert.signature,
                    cert.tbs_certificate_bytes,
                    padding.PKCS1v15(),
                    cert.signature_hash_algorithm,
                )
        elif isinstance(public_key, ec.EllipticCurvePublicKey):
            public_key.verify(cert.signature, cert.tbs_certificate_bytes, params)
        elif isinstance(public_key, (ed25519.Ed25519PublicKey, ed448.Ed448PublicKey)):
            public_key.verify(cert.signature, cert.tbs_certificate_bytes)
        else:
            return False
    except (InvalidSignature, Exception):  # noqa: BLE001 - fail closed на любой сбой
        return False
    return True


def _build_chain(
    signer: x509.Certificate,
    pool: list[x509.Certificate],
    trusted_roots: list[x509.Certificate],
    *,
    at_time: datetime,
) -> list[x509.Certificate]:
    """Явная цепочка signer → … → доверенный root.

    Корень сопоставляется побайтово (DER) с одним из переданных корней.
    Никаких системных хранилищ: доверие ТОЛЬКО явному списку.
    """
    root_ders = {root.public_bytes(serialization.Encoding.DER) for root in trusted_roots}
    path = [signer]
    current = signer
    seen = {current.public_bytes(serialization.Encoding.DER)}
    while current.public_bytes(serialization.Encoding.DER) not in root_ders:
        if len(path) > _MAX_CHAIN_DEPTH:
            raise AuthenticodeError("untrusted_root", "цепочка слишком длинная")
        candidates = [
            cand
            for cand in [*pool, *trusted_roots]
            if cand.subject == current.issuer
            and cand.public_bytes(serialization.Encoding.DER) not in seen
        ]
        # Prefer exact AuthorityKeyIdentifier/SubjectKeyIdentifier match.
        try:
            aki = current.extensions.get_extension_for_class(x509.AuthorityKeyIdentifier).value
        except x509.ExtensionNotFound:
            aki = None
        if aki is not None and aki.key_identifier is not None:
            matched = []
            for cand in candidates:
                try:
                    ski = cand.extensions.get_extension_for_class(x509.SubjectKeyIdentifier).value
                except x509.ExtensionNotFound:
                    continue
                if ski.digest == aki.key_identifier:
                    matched.append(cand)
            if matched:
                candidates = matched
        issuer_cert = next((cand for cand in candidates if _verify_cert_signature(current, cand)), None)
        if issuer_cert is None:
            raise AuthenticodeError(
                "untrusted_root",
                "цепочка сертификатов не доводится до переданного доверенного root",
            )
        _check_ca_constraints(issuer_cert, at_time=at_time)
        path.append(issuer_cert)
        current = issuer_cert
        seen.add(current.public_bytes(serialization.Encoding.DER))
    return path


def _check_ca_constraints(cert: x509.Certificate, *, at_time: datetime) -> None:
    if cert.not_valid_before_utc > at_time or cert.not_valid_after_utc < at_time:
        raise AuthenticodeError("certificate_expired", "сертификат вне срока действия")
    try:
        constraints = cert.extensions.get_extension_for_class(x509.BasicConstraints).value
        if not constraints.ca:
            raise AuthenticodeError("untrusted_root", "промежуточный сертификат не CA")
    except x509.ExtensionNotFound:
        raise AuthenticodeError("untrusted_root", "нет BasicConstraints у промежуточного сертификата") from None
    try:
        usage = cert.extensions.get_extension_for_class(x509.KeyUsage).value
        if not usage.key_cert_sign:
            raise AuthenticodeError("untrusted_root", "KeyUsage не разрешает подпись сертификатов")
    except x509.ExtensionNotFound:
        pass  # не fail closed: отсутствие KeyUsage допустимо (RFC 5280)


def _check_validity(cert: x509.Certificate, at_time: datetime, role: str) -> None:
    if cert.not_valid_before_utc > at_time or cert.not_valid_after_utc < at_time:
        raise AuthenticodeError("certificate_expired", f"сертификат {role} вне срока действия")


def _check_eku(cert: x509.Certificate, required_oid: str, code: str, role: str) -> None:
    try:
        eku = cert.extensions.get_extension_for_class(x509.ExtendedKeyUsage).value
    except x509.ExtensionNotFound:
        raise AuthenticodeError(code, f"у сертификата {role} нет Extended Key Usage") from None
    oids = {oid.dotted_string for oid in eku}
    if required_oid not in oids:
        raise AuthenticodeError(code, f"сертификат {role} не имеет требуемого EKU")


def _publisher_matches(cert: x509.Certificate, expected: str) -> bool:
    expected_norm = " ".join(expected.split())
    if cert.subject.rfc4514_string() == expected_norm:
        return True
    common_names = cert.subject.get_attributes_for_oid(x509.NameOID.COMMON_NAME)
    if common_names and " ".join(common_names[0].value.split()) == expected_norm:
        return True
    # "CN=..." без остальных компонентов тоже допустимо для сравнения.
    if expected_norm.startswith("CN=") and common_names:
        cn = " ".join(common_names[0].value.split())
        return cn == expected_norm[3:].strip()
    return False


# --- Подписи CMS --------------------------------------------------------------


def _digest_policy(oid: str) -> tuple[str, type[hashes.HashAlgorithm]]:
    entry = _DIGESTS.get(oid)
    if entry is None:
        raise AuthenticodeError("digest_policy", f"неподдерживаемый алгоритм дайджеста {oid}")
    name, hash_cls, allowed = entry
    if not allowed:
        raise AuthenticodeError(
            "sha1_forbidden" if name == "sha1" else "digest_policy",
            f"production-политика запрещает дайджест {name} (требуется SHA-256+)",
        )
    return name, hash_cls


def _verify_blob_signature(
    public_key: object,
    signature: bytes,
    blob: bytes,
    signature_alg_oid: str,
    hash_cls: type[hashes.HashAlgorithm],
) -> None:
    try:
        if isinstance(public_key, rsa.RSAPublicKey):
            if signature_alg_oid == OID_RSASSA_PSS:
                raise AuthenticodeError(
                    "unsupported_signature_algorithm",
                    "RSASSA-PSS не поддерживается (signtool использует PKCS#1 v1.5)",
                )
            public_key.verify(signature, blob, padding.PKCS1v15(), hash_cls())
        elif isinstance(public_key, ec.EllipticCurvePublicKey):
            public_key.verify(signature, blob, ec.ECDSA(hash_cls()))
        elif isinstance(public_key, ed25519.Ed25519PublicKey):
            public_key.verify(signature, blob)
        elif isinstance(public_key, ed448.Ed448PublicKey):
            public_key.verify(signature, blob)
        else:
            raise AuthenticodeError("unsupported_signature_algorithm", "неизвестный тип ключа подписанта")
    except AuthenticodeError:
        raise
    except InvalidSignature:
        raise AuthenticodeError("cms_signature_invalid", "криптографическая подпись CMS не прошла проверку") from None
    except Exception as exc:  # noqa: BLE001 - fail closed на любой сбой
        raise AuthenticodeError("cms_signature_invalid", f"ошибка проверки подписи: {exc}") from exc


def _find_signer_cert(
    sid: asn1cms.SignerIdentifier, pool: list[x509.Certificate]
) -> x509.Certificate:
    if sid.name == "issuer_and_serial_number":
        isn = sid.chosen
        issuer_der = isn["issuer"].dump()
        serial = isn["serial_number"].native
        for cert in pool:
            if cert.serial_number == serial and cert.issuer.public_bytes() == issuer_der:
                return cert
    else:
        ski = bytes(sid.chosen.native)
        for cert in pool:
            try:
                ext = cert.extensions.get_extension_for_class(x509.SubjectKeyIdentifier).value
            except x509.ExtensionNotFound:
                continue
            if ext.digest == ski:
                return cert
    raise AuthenticodeError("signer_certificate_missing", "сертификат подписанта не найден в CMS")


def _strip_explicit_context0(der: bytes) -> bytes:
    """Снять [0] EXPLICIT-обёртку (DER, определённая длина)."""
    if not der or der[0] != 0xA0:
        raise AuthenticodeError("bad_signature_structure", "ожидалась [0]-обёртка содержимого CMS")
    length_byte = der[1]
    if length_byte < 0x80:
        header = 2
    else:
        num_octets = length_byte & 0x7F
        if num_octets == 0 or num_octets > 4:
            raise AuthenticodeError("bad_signature_structure", "BER indefinite length не поддерживается")
        header = 2 + num_octets
    return der[header:]


def _attrs_by_oid(attrs: asn1cms.CMSAttributes) -> dict[str, asn1cms.CMSAttribute]:
    return {attr["type"].dotted: attr for attr in attrs}


def _verify_signed_attrs(
    attrs: asn1cms.CMSAttributes,
    *,
    content_type_oid: str,
    content_der: bytes,
    hash_cls: type[hashes.HashAlgorithm],
) -> bytes:
    """Проверить content-type/message-digest; вернуть DER для проверки подписи."""
    by_oid = _attrs_by_oid(attrs)
    ct = by_oid.get(OID_ATTR_CONTENT_TYPE)
    if ct is None:
        raise AuthenticodeError("bad_signature_structure", "нет атрибута content-type")
    ct_value = ct["values"][0]
    ct_oid = ct_value.dotted if isinstance(ct_value, core.ObjectIdentifier) else str(ct_value.native)
    if ct_oid != content_type_oid:
        raise AuthenticodeError("bad_signature_structure", "content-type не соответствует подписываемому содержимому")
    md = by_oid.get(OID_ATTR_MESSAGE_DIGEST)
    if md is None:
        raise AuthenticodeError("bad_signature_structure", "нет атрибута message-digest")
    declared = bytes(md["values"][0].native)
    digest = hashes.Hash(hash_cls())
    digest.update(content_der)
    if declared != digest.finalize():
        raise AuthenticodeError("digest_mismatch", "атрибут message-digest не совпал с содержимым CMS")
    # Подпись вычисляется над DER signedAttrs с [0]-тегом (universal SET -> A0).
    dumped = attrs.dump()
    if dumped and dumped[0] == 0x31:
        dumped = b"\xa0" + dumped[1:]
    return dumped


# --- Timestamp ----------------------------------------------------------------


def _verify_timestamp_token(
    token_der: bytes, signer_signature: bytes, trusted_roots: list[x509.Certificate]
) -> tuple[datetime, x509.Certificate]:
    """RFC 3161: структура, подпись TSA, messageImprint, EKU, цепочка, genTime."""
    try:
        token = asn1cms.ContentInfo.load(token_der)
    except Exception as exc:  # noqa: BLE001 - fail closed
        raise AuthenticodeError("timestamp_invalid", f"некорректный timestamp-токен: {exc}") from exc
    if token["content_type"].native != "signed_data":
        raise AuthenticodeError("timestamp_invalid", "timestamp-токен не SignedData")
    sd = token["content"]
    eci = sd["encap_content_info"]
    if eci["content_type"].dotted != OID_CT_TSTINFO:
        raise AuthenticodeError("timestamp_invalid", "eContentType timestamp-токена не TSTInfo")
    content = eci["content"]
    if content is None:
        raise AuthenticodeError("timestamp_invalid", "timestamp-токен без содержимого")
    # asn1crypto знает eContentType TSTInfo и сам парсит содержимое в TSTInfo;
    # для проверки подписи TSA нужны исходные DER-байты содержимого.
    tst_der = content.contents if isinstance(content, core.ParsableOctetString) else content.dump()
    if not isinstance(tst_der, bytes) or not tst_der:
        raise AuthenticodeError("timestamp_invalid", "пустое содержимое timestamp-токена")
    try:
        tsti = tsp.TSTInfo.load(tst_der)
    except Exception as exc:  # noqa: BLE001 - fail closed
        raise AuthenticodeError("timestamp_invalid", f"некорректный TSTInfo: {exc}") from exc

    tsa_certs = _load_cms_certs(sd)
    signer_infos = sd["signer_infos"]
    if not len(signer_infos):
        raise AuthenticodeError("timestamp_invalid", "timestamp-токен без подписанта")
    tsi = signer_infos[0]
    tsa_cert = _find_signer_cert(tsi["sid"], tsa_certs)
    digest_name, hash_cls = _digest_policy(tsi["digest_algorithm"]["algorithm"].dotted)
    del digest_name
    signed_attrs = tsi["signed_attrs"]
    if signed_attrs is None:
        raise AuthenticodeError("timestamp_invalid", "TSA-подпись без signedAttrs")
    try:
        attrs_der = _verify_signed_attrs(
            signed_attrs,
            content_type_oid=OID_CT_TSTINFO,
            content_der=tst_der,
            hash_cls=hash_cls,
        )
        _verify_blob_signature(
            tsa_cert.public_key(),
            bytes(tsi["signature"].native),
            attrs_der,
            tsi["signature_algorithm"]["algorithm"].dotted,
            hash_cls,
        )
    except AuthenticodeError as exc:
        # Любая криптографическая проблема внутри токена => невалидный timestamp
        # (не путать с digest_mismatch самого установщика).
        raise AuthenticodeError("timestamp_invalid", f"подпись TSA не прошла проверку: {exc.message}") from exc

    # messageImprint обязан быть хешем ПОДПИСИ подписанта.
    imprint = tsti["message_imprint"]
    imprint_hash_cls = _digest_policy(imprint["hash_algorithm"]["algorithm"].dotted)[1]
    expected = hashes.Hash(imprint_hash_cls())
    expected.update(signer_signature)
    if bytes(imprint["hashed_message"].native) != expected.finalize():
        raise AuthenticodeError(
            "timestamp_invalid", "messageImprint timestamp не является хешем подписи установщика"
        )

    _check_eku(tsa_cert, OID_TIME_STAMPING_EKU, "tsa_eku_missing", "TSA")
    gen_time = tsti["gen_time"].native
    if gen_time.tzinfo is None:
        gen_time = gen_time.replace(tzinfo=timezone.utc)
    _check_validity(tsa_cert, gen_time, "TSA")
    try:
        chain = _build_chain(tsa_cert, tsa_certs, trusted_roots, at_time=gen_time)
    except AuthenticodeError as exc:
        if exc.code == "untrusted_root":
            raise AuthenticodeError(
                "tsa_untrusted", f"TSA-цепочка не доводится до доверенного TSA-root: {exc.message}"
            ) from exc
        raise
    if len(chain) > 1:
        for intermediate in chain[1:-1]:
            _check_ca_constraints(intermediate, at_time=gen_time)
    return gen_time, tsa_cert


def _verify_signer_info(
    si: asn1cms.SignerInfo,
    *,
    spc_der: bytes,
    certs: list[x509.Certificate],
    expected_publisher: str,
    signer_roots: list[x509.Certificate],
    tsa_roots: list[x509.Certificate],
    report: AuthenticodeReport,
) -> None:
    digest_name, hash_cls = _digest_policy(si["digest_algorithm"]["algorithm"].dotted)
    signed_attrs = si["signed_attrs"]
    if signed_attrs is None:
        raise AuthenticodeError("bad_signature_structure", "Authenticode требует signedAttrs")
    attrs_der = _verify_signed_attrs(
        signed_attrs,
        content_type_oid=SPC_INDIRECT_DATA_OID,
        content_der=spc_der,
        hash_cls=hash_cls,
    )
    signer_cert = _find_signer_cert(si["sid"], certs)
    signature = bytes(si["signature"].native)
    _verify_blob_signature(
        signer_cert.public_key(),
        signature,
        attrs_der,
        si["signature_algorithm"]["algorithm"].dotted,
        hash_cls,
    )

    _check_eku(signer_cert, OID_CODE_SIGNING_EKU, "eku_missing", "подписанта")
    if not _publisher_matches(signer_cert, expected_publisher):
        raise AuthenticodeError(
            "publisher_mismatch",
            f"publisher подписи не совпал с ожидаемым ({signer_cert.subject.rfc4514_string()!r})",
        )

    # Timestamp: отсутствует/legacy — fail closed.
    unsigned = si["unsigned_attrs"]
    if unsigned is None:
        raise AuthenticodeError("timestamp_missing", "подпись без RFC 3161 timestamp")
    token_attr = None
    legacy = False
    for attr in unsigned:
        oid = attr["type"].dotted
        if oid == OID_RFC3161_TIMESTAMP_TOKEN:
            token_attr = attr
            break
        if oid == OID_LEGACY_MS_TIMESTAMP:
            legacy = True
    if token_attr is None:
        if legacy:
            raise AuthenticodeError(
                "timestamp_legacy_unsupported",
                "legacy Microsoft timestamp не поддерживается (требуется RFC 3161)",
            )
        raise AuthenticodeError("timestamp_missing", "подпись без RFC 3161 timestamp")
    gen_time, tsa_cert = _verify_timestamp_token(
        token_attr["values"][0].dump(), signature, tsa_roots
    )

    _check_validity(signer_cert, gen_time, "подписанта")
    chain = _build_chain(signer_cert, certs, signer_roots, at_time=gen_time)
    for intermediate in chain[1:-1]:
        _check_ca_constraints(intermediate, at_time=gen_time)

    report.digest_algorithm = digest_name
    report.signer_subject = signer_cert.subject.rfc4514_string()
    report.signing_time = gen_time
    report.chain_subjects = [cert.subject.rfc4514_string() for cert in chain]
    report.tsa_subject = tsa_cert.subject.rfc4514_string()


# --- Публичное API ------------------------------------------------------------


def load_roots(pem_inputs: list[bytes]) -> list[x509.Certificate]:
    """Загрузить доверенные корни из PEM/DER-байтов (несколько допустимо)."""
    roots: list[x509.Certificate] = []
    for blob in pem_inputs:
        data = blob.strip()
        if not data:
            continue
        try:
            if data.startswith(b"-----"):
                roots.extend(x509.load_pem_x509_certificates(data))
            else:
                roots.append(x509.load_der_x509_certificate(data))
        except Exception as exc:  # noqa: BLE001 - fail closed
            raise AuthenticodeError("bad_trust_root", f"не удалось прочитать доверенный корень: {exc}") from exc
    if not roots:
        raise AuthenticodeError("bad_trust_root", "список доверенных корней пуст")
    return roots


def _load_exe_bytes(exe: str | Path | bytes | bytearray) -> bytes:
    """PE можно передать путём или уже прочитанными байтами (raw-проверка)."""
    if isinstance(exe, (bytes, bytearray)):
        return bytes(exe)
    data = Path(exe).read_bytes()
    if not data:
        raise AuthenticodeError("bad_arguments", "файл установщика пуст")
    return data


def verify_pe_authenticode(
    exe_path: str | Path | bytes,
    *,
    expected_publisher: str,
    trusted_roots: list[bytes],
    tsa_roots: list[bytes] | None = None,
) -> AuthenticodeReport:
    """Независимая проверка Authenticode по байтам PE (fail closed).

    ``exe_path`` — путь к файлу ИЛИ сами байты PE (второе исключает TOCTOU
    между публикацией и проверкой: gate проверяет именно те байты,
    которые уходят в канал).
    """
    report = AuthenticodeReport()
    try:
        if not expected_publisher or not expected_publisher.strip():
            raise AuthenticodeError("bad_arguments", "expected_publisher обязателен")
        roots = load_roots(trusted_roots)
        tsaroots = load_roots(tsa_roots) if tsa_roots else roots
        data = _load_exe_bytes(exe_path)
        pe = _parse_pe(data)
        payloads = _cert_table_payloads(data, pe)
        for payload in payloads:
            try:
                ci = asn1cms.ContentInfo.load(payload)
                if ci["content_type"].native != "signed_data":
                    raise AuthenticodeError("bad_signature_structure", "certificate table содержит не SignedData")
            except AuthenticodeError:
                raise
            except Exception as exc:  # noqa: BLE001 - любой мусор в таблице => структурная ошибка
                raise AuthenticodeError(
                    "bad_signature_structure", f"некорректная CMS-структура в certificate table: {exc}"
                ) from exc
            sd = ci["content"]
            eci = sd["encap_content_info"]
            if eci["content_type"].dotted != SPC_INDIRECT_DATA_OID:
                raise AuthenticodeError(
                    "bad_signature_structure", "eContentType не SPC_INDIRECT_DATA (это не Authenticode)"
                )
            content = eci["content"]
            if content is None:
                raise AuthenticodeError("bad_signature_structure", "SignedData без содержимого")
            spc_der = _strip_explicit_context0(content.dump())
            spc = SpcIndirectDataContent.load(spc_der)
            if spc["data"]["type"].dotted != SPC_PE_IMAGE_DATA_OID:
                raise AuthenticodeError("bad_signature_structure", "SPC data type не PE image")
            digest_name, hash_cls = _digest_policy(
                spc["message_digest"]["digest_algorithm"]["algorithm"].dotted
            )
            del digest_name
            declared = bytes(spc["message_digest"]["digest"].native)
            if declared != _pe_digest(data, pe, hash_cls):
                raise AuthenticodeError(
                    "digest_mismatch",
                    "digest образа PE не совпал с подписанным: файл изменён после подписи "
                    "или подпись перенесена с другого файла",
                )
            certs = _load_cms_certs(sd)
            signer_infos = sd["signer_infos"]
            if not len(signer_infos):
                raise AuthenticodeError("bad_signature_structure", "SignedData без подписантов")
            for si in signer_infos:
                _verify_signer_info(
                    si,
                    spc_der=spc_der,
                    certs=certs,
                    expected_publisher=expected_publisher,
                    signer_roots=roots,
                    tsa_roots=tsaroots,
                    report=report,
                )
    except AuthenticodeError as exc:
        report.fail(exc)
    except Exception as exc:  # noqa: BLE001 - fail closed на любых неожиданных сбоях
        report.fail(AuthenticodeError("unexpected_error", f"непредвиденная ошибка проверки: {exc}"))
    return report


def verify_release_gate(
    exe_path: str | Path,
    manifest_path: str | Path,
    *,
    mode: str,
    expected_publisher: str,
    trusted_roots: list[bytes],
    tsa_roots: list[bytes] | None = None,
) -> AuthenticodeReport:
    """Production-гейт публикации: НЕЗАВИСИМАЯ проверка байтов PE + сверка
    манифеста как provenance (манифест не может «доказать» подпись).

    Проверяет: (1) raw-PE Authenticode по явным корням; (2) SHA256 подписанного
    exe в манифесте совпадает с фактическими байтами; (3) блок signing
    манифеста согласован с фактическим подписантом и режимом.
    """
    report = verify_pe_authenticode(
        exe_path,
        expected_publisher=expected_publisher,
        trusted_roots=trusted_roots,
        tsa_roots=tsa_roots,
    )
    exe_sha = hashlib.sha256(
        exe_path if isinstance(exe_path, (bytes, bytearray)) else Path(exe_path).read_bytes()
    ).hexdigest()
    manifest_file = Path(manifest_path)
    if not manifest_file.exists():
        report.fail(AuthenticodeError("manifest_missing", f"release-manifest не найден: {manifest_file.name}"))
        return report
    try:
        manifest = json.loads(manifest_file.read_text(encoding="utf-8-sig"))
    except (ValueError, OSError) as exc:
        report.fail(AuthenticodeError("manifest_invalid", f"манифест не читается: {exc}"))
        return report
    actual_sha = exe_sha
    installer = manifest.get("installer_exe") or {}
    declared_sha = installer.get("sha256") or ""
    if not isinstance(declared_sha, str) or declared_sha.lower() != actual_sha:
        report.fail(
            AuthenticodeError(
                "manifest_sha_mismatch",
                "SHA256 в манифесте не совпадает с фактическими байтами установщика",
            )
        )
    signing = manifest.get("signing") or {}
    if report.ok:
        declared_publisher = (signing.get("publisher") or "").strip()
        if declared_publisher and declared_publisher != report.signer_subject:
            report.fail(
                AuthenticodeError(
                    "manifest_publisher_mismatch",
                    "publisher в манифесте не совпадает с фактическим подписантом",
                )
            )
    if mode == "production" and signing.get("mode") != "production":
        report.fail(
            AuthenticodeError("manifest_mode_mismatch", "production-публикация требует signing.mode=production")
        )
    return report


# --- CLI ----------------------------------------------------------------------


def _read_root_file(path: str) -> bytes:
    return Path(path).read_bytes()


def _write_json(path: str, report: AuthenticodeReport) -> None:
    Path(path).write_text(
        json.dumps(report.to_dict(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    def add_common(target: argparse.ArgumentParser) -> None:
        target.add_argument("--exe", required=True, help="проверяемый PE-файл (installer)")
        target.add_argument(
            "--expected-publisher", required=True, help="ожидаемый Subject сертификата подписанта"
        )
        target.add_argument(
            "--trusted-root",
            action="append",
            required=True,
            help="PEM/DER доверенного root подписанта (можно повторять; production-доверие явное)",
        )
        target.add_argument(
            "--timestamproot",
            action="append",
            default=None,
            help="PEM/DER доверенного root TSA (по умолчанию — те же корни)",
        )
        target.add_argument("--json", default=None, help="записать машиночитаемый отчёт в файл")

    verify_cmd = sub.add_parser("verify", help="независимая проверка Authenticode по байтам PE")
    add_common(verify_cmd)

    gate_cmd = sub.add_parser("release-gate", help="production-гейт: raw-PE + сверка манифеста")
    add_common(gate_cmd)
    gate_cmd.add_argument("--manifest", required=True, help="installer/release-manifest.json")
    gate_cmd.add_argument("--mode", choices=["production", "test"], default="production")

    args = parser.parse_args(argv)
    roots = [_read_root_file(path) for path in args.trusted_root]
    tsaroots = [_read_root_file(path) for path in args.timestamproot] if args.timestamproot else None

    if args.command == "verify":
        report = verify_pe_authenticode(
            args.exe,
            expected_publisher=args.expected_publisher,
            trusted_roots=roots,
            tsa_roots=tsaroots,
        )
    else:
        report = verify_release_gate(
            args.exe,
            args.manifest,
            mode=args.mode,
            expected_publisher=args.expected_publisher,
            trusted_roots=roots,
            tsa_roots=tsaroots,
        )
    if args.json:
        _write_json(args.json, report)
    if report.ok:
        print("Authenticode: ПОДПИСЬ ПОДТВЕРЖДЕНА (независимая проверка байтов PE)")
        print(f"  подписант: {report.signer_subject}")
        print(f"  digest: {report.digest_algorithm}")
        print(f"  время подписи (RFC 3161): {report.signing_time}")
        print(f"  цепочка: {' -> '.join(report.chain_subjects)}")
        print(f"  TSA: {report.tsa_subject}")
        return 0
    print("Authenticode: ПРОВЕРКА ПРОВАЛЕНА (fail closed)", file=sys.stderr)
    for problem in report.problems:
        print(f"  ОШИБКА[{problem.split(':', 1)[0]}]: {problem.split(':', 1)[1].strip()}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
