# -*- coding: utf-8 -*-
"""ЗЕРКАЛО infra/release/channel_contract.py (Phase 13).

Контейнер backend собирается из каталога backend/ и не видит infra/, поэтому
контракт канала продублирован сюда. ИСТОЧНИК — infra/release/channel_contract.py;
тест tests/test_update_channel_contract.py требует побайтового совпадения
двух файлов (после этого заголовка). Не править вручную — править источник
и перегенерировать.
"""

from __future__ import annotations

import datetime as _dt
import hashlib
import json
import re

SCHEMA_VERSION = 1
SIGNATURE_SCHEME = "ed25519"

# Порядок полей канонического payload ЗАКРЕПЛЁН: менять только вместе с
# повышением schema_version.
FIELDS: list[tuple[str, str]] = [
    ("schema_version", "int"),
    ("channel", "str"),
    ("version", "str"),
    ("release_sha", "str"),
    ("package_url", "str"),
    ("package_size", "int"),
    ("package_sha256", "str"),
    ("minimum_supported_version", "str"),
    ("published_at", "str"),
    ("notes_ru", "str"),
]

SEMVER_RE = re.compile(
    r"^[0-9]+\.[0-9]+\.[0-9]+(-[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?"
    r"(\+[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?$"
)
RELEASE_SHA_RE = re.compile(r"^[0-9a-f]{40}$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
ISO_UTC_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")

MAX_NOTES_LENGTH = 2000
# Разумная верхняя граница пакета по умолчанию (переопределяется политикой
# скачивания; сюда — только контрактная проверка целочисленности).
MAX_PACKAGE_SIZE = 2**31 - 1


class ChannelError(ValueError):
    """Ошибка контракта канала: безопасный код + человекочитаемое сообщение."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def _no_duplicates(pairs: list[tuple[str, object]]) -> dict[str, object]:
    data: dict[str, object] = {}
    for key, value in pairs:
        if key in data:
            raise ChannelError("duplicate_key", f"повторяющийся ключ JSON: {key!r}")
        data[key] = value
    return data


def parse_manifest_json(text: str | bytes) -> dict:
    """Разбор JSON-транспорта manifest с fail-closed проверками структуры."""
    try:
        raw = json.loads(text, object_pairs_hook=_no_duplicates)
    except ChannelError:
        raise
    except Exception as exc:  # любой сбой разбора = отказ
        raise ChannelError("malformed_json", f"некорректный JSON manifest: {exc}") from exc
    if not isinstance(raw, dict):
        raise ChannelError("malformed_json", "manifest должен быть JSON-объектом")
    return raw


def validate_manifest_fields(manifest: dict) -> None:
    """Проверка типов/форматов 10 известных полей; неизвестные поля — отказ."""
    unknown = sorted(set(manifest) - {name for name, _ in FIELDS} - {"signature"})
    if unknown:
        raise ChannelError("unknown_field", f"неизвестные поля manifest: {', '.join(unknown)}")
    for name, kind in FIELDS:
        if name not in manifest:
            raise ChannelError("missing_field", f"отсутствует обязательное поле: {name}")
        value = manifest[name]
        if kind == "int":
            if isinstance(value, bool) or not isinstance(value, int):
                raise ChannelError("bad_type", f"поле {name} должно быть целым числом")
            if name == "schema_version" and value != SCHEMA_VERSION:
                raise ChannelError(
                    "unknown_schema_version",
                    f"неизвестная версия схемы канала: {value} (ожидается {SCHEMA_VERSION})",
                )
            if name == "package_size" and not 1 <= value <= MAX_PACKAGE_SIZE:
                raise ChannelError("bad_type", f"поле {name} вне допустимых границ")
        else:
            if not isinstance(value, str):
                raise ChannelError("bad_type", f"поле {name} должно быть строкой")
            if "\n" in value or "\r" in value or any(ord(c) < 0x20 for c in value):
                raise ChannelError("bad_value", f"поле {name} содержит управляющие символы")
    if manifest["channel"] != "stable":
        raise ChannelError(
            "unknown_channel",
            f"неизвестный канал: {manifest['channel']!r} (поддерживается только stable)",
        )
    version = manifest["version"]
    if not SEMVER_RE.match(version):
        raise ChannelError("bad_semver", f"некорректный SemVer: {version!r}")
    if not SEMVER_RE.match(manifest["minimum_supported_version"]):
        raise ChannelError(
            "bad_semver",
            f"некорректный SemVer minimum_supported_version: "
            f"{manifest['minimum_supported_version']!r}",
        )
    if not RELEASE_SHA_RE.match(manifest["release_sha"]):
        raise ChannelError("bad_release_sha", "release_sha должен быть 40 hex-символами")
    if not SHA256_RE.match(manifest["package_sha256"]):
        raise ChannelError("bad_sha256", "package_sha256 должен быть 64 hex-символами")
    _validate_package_url(manifest["package_url"])
    if not ISO_UTC_RE.match(manifest["published_at"]):
        raise ChannelError("bad_published_at", "published_at должен быть ISO-8601 UTC")
    try:
        _dt.datetime.strptime(manifest["published_at"], "%Y-%m-%dT%H:%M:%SZ")
    except ValueError as exc:
        raise ChannelError("bad_published_at", "некорректная дата published_at") from exc
    notes = manifest["notes_ru"]
    if len(notes) > MAX_NOTES_LENGTH:
        raise ChannelError("bad_value", "notes_ru слишком длинный")


def _validate_package_url(url: str) -> None:
    if not url.startswith("https://"):
        raise ChannelError("bad_url", "package_url обязан использовать https")
    if "?" in url or "#" in url:
        raise ChannelError(
            "bad_url", "package_url не должен содержать query/fragment (immutable artifact)"
        )
    if "@" in url:
        raise ChannelError("bad_url", "package_url не должен содержать userinfo")
    if not re.match(r"^https://[A-Za-z0-9._-]+(:\d+)?/", url):
        raise ChannelError("bad_url", "package_url имеет недопустимый формат")


def canonical_bytes(manifest: dict) -> bytes:
    """Канонический подписываемый payload: фиксированный порядок полей,
    ``имя:значение`` через LF, UTF-8, завершающий перевод строки.

    manifest передаётся БЕЗ поля signature (оно не входит в payload).
    """
    validate_manifest_fields(manifest)
    lines = [f"{name}:{manifest[name]}" for name, _ in FIELDS]
    return ("\n".join(lines) + "\n").encode("utf-8")


def verify_signature(manifest: dict, public_key_b64: str) -> bytes:
    """Проверка подписи manifest публичным Ed25519-ключом (base64, 32 байта).

    Возвращает канонические байты, подпись которых проверена.
    """
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

    signature = manifest.get("signature")
    if not isinstance(signature, dict):
        raise ChannelError("bad_signature", "отсутствует объект signature")
    if signature.get("scheme") != SIGNATURE_SCHEME:
        raise ChannelError(
            "bad_signature", f"неподдерживаемая схема подписи: {signature.get('scheme')!r}"
        )
    key_id = signature.get("key_id")
    sig_hex = signature.get("sig")
    if not isinstance(key_id, str) or not key_id:
        raise ChannelError("bad_signature", "signature.key_id обязателен")
    if not isinstance(sig_hex, str) or not re.match(r"^[0-9a-f]{128}$", sig_hex):
        raise ChannelError("bad_signature", "signature.sig должен быть 128 hex-символами")
    try:
        import base64

        raw = base64.b64decode(public_key_b64, validate=True)
        key = Ed25519PublicKey.from_public_bytes(raw)
    except Exception as exc:
        raise ChannelError("bad_key", f"некорректный публичный ключ {key_id!r}") from exc
    payload = canonical_bytes(manifest)
    try:
        key.verify(bytes.fromhex(sig_hex), payload)
    except InvalidSignature as exc:
        raise ChannelError("bad_signature", f"подпись недействительна (key_id={key_id!r})") from exc
    return payload


def sign_payload(private_key_hex: str, payload: bytes) -> str:
    """Подпись payload закрытым Ed25519-ключом (hex, 64 символа)."""
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    key = Ed25519PrivateKey.from_private_bytes(bytes.fromhex(private_key_hex))
    return key.sign(payload).hex()


def sign_manifest(manifest: dict, key_id: str, private_key_hex: str) -> dict:
    """Возвращает manifest с полем signature (поле не входит в payload)."""
    payload = canonical_bytes(manifest)
    return {
        **manifest,
        "signature": {
            "key_id": key_id,
            "scheme": SIGNATURE_SCHEME,
            "sig": sign_payload(private_key_hex, payload),
        },
    }


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()
