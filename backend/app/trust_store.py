"""Phase 14: строгая проверка trust store канала (клиентский контур backend).

Trust store — единственный источник доверия к подписи канала:
``{"<key_id>": {"key": "<base64 32 байта Ed25519>", "revoked": false}}``.

Правила совпадают с `infra/release/trust_store.py` (тот же документ
поставляет release-пайплайн) и с host-движком:

* плоский объект, каждый ключ верхнего уровня — ``key_id``;
* ``key`` — канонический base64 ровно 32 байт (Ed25519 public key);
* ``revoked`` — обязательный булев флаг (отзыв немедленный, fail closed);
* уникальные ``key_id`` (повтор ключа JSON — отказ);
* отсутствие приватного материала (PEM/JWK/hex-приватный ключ);
* пустой набор означает «канал не настроен», а не «доверять всем»;
* в диагностику попадают только ``key_id``, отпечаток и статус отзыва.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import re

from app.update_channel_contract import ChannelError

TRUST_STORE_MAX_BYTES = 64 * 1024
ED25519_PUBLIC_KEY_BYTES = 32
KEY_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
PRIVATE_MATERIAL_MARKERS = ("-----BEGIN", "PRIVATE KEY", "OPENSSH PRIVATE", "PuTTY-User-Key-File")
PRIVATE_HEX_KEY_LENGTH = 64


def _no_duplicates(pairs: list[tuple[str, object]]) -> dict[str, object]:
    data: dict[str, object] = {}
    for key, value in pairs:
        if key in data:
            raise ChannelError("bad_key_set", f"повторяющийся key_id в trust store: {key!r}")
        data[key] = value
    return data


def assert_no_private_material(text: str) -> None:
    upper = text.upper()
    for marker in PRIVATE_MATERIAL_MARKERS:
        if marker.upper() in upper:
            raise ChannelError(
                "bad_key_set",
                "trust store содержит приватный материал: допустимы только публичные ключи",
            )


def _decode_public_key(value: object, key_id: str) -> bytes:
    if not isinstance(value, str) or not value.strip():
        raise ChannelError("bad_key_set", f"у ключа {key_id!r} нет значения key")
    candidate = value.strip()
    if len(candidate) == PRIVATE_HEX_KEY_LENGTH and all(
        c in "0123456789abcdefABCDEF" for c in candidate
    ):
        raise ChannelError(
            "bad_key_set",
            f"значение key у {key_id!r} похоже на приватный hex-ключ, а не на публичный",
        )
    try:
        raw = base64.b64decode(candidate, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ChannelError("bad_key_set", f"у ключа {key_id!r} некорректный base64") from exc
    if len(raw) != ED25519_PUBLIC_KEY_BYTES:
        raise ChannelError(
            "bad_key_set",
            f"публичный ключ {key_id!r} должен быть ровно "
            f"{ED25519_PUBLIC_KEY_BYTES} байтами Ed25519",
        )
    return raw


def validate_trust_store(data: object) -> dict[str, dict]:
    """Проверка разобранного набора ключей (возвращает его же)."""
    if not isinstance(data, dict) or not data:
        raise ChannelError("bad_key_set", "набор доверенных ключей пуст или не объект")
    for key_id, entry in data.items():
        if not isinstance(key_id, str) or not KEY_ID_RE.match(key_id):
            raise ChannelError(
                "bad_key_set",
                f"некорректный key_id {key_id!r}: допустимы буквы, цифры, "
                "'.', '_', '-' (до 64 символов)",
            )
        if not isinstance(entry, dict):
            raise ChannelError("bad_key_set", f"запись ключа {key_id!r} должна быть объектом")
        unknown = sorted(set(entry) - {"key", "revoked"})
        if unknown:
            raise ChannelError(
                "bad_key_set", f"у ключа {key_id!r} неизвестные поля: {', '.join(unknown)}"
            )
        _decode_public_key(entry.get("key"), key_id)
        if not isinstance(entry.get("revoked"), bool):
            raise ChannelError("bad_key_set", f"у ключа {key_id!r} нет булева флага revoked")
    return data


def parse_trust_store_text(raw: str) -> dict[str, dict]:
    """Строгий разбор текста trust store (пустая строка = канал не настроен)."""
    if not raw.strip():
        return {}
    if len(raw.encode("utf-8")) > TRUST_STORE_MAX_BYTES:
        raise ChannelError("bad_key_set", "trust store превышает допустимый размер")
    assert_no_private_material(raw)
    try:
        data = json.loads(raw, object_pairs_hook=_no_duplicates)
    except ChannelError:
        raise
    except json.JSONDecodeError as exc:
        raise ChannelError("bad_key_set", f"некорректный JSON набора ключей: {exc}") from exc
    return validate_trust_store(data)


def key_fingerprint(key_b64: str) -> str:
    raw = base64.b64decode(key_b64.strip(), validate=True)
    return "SHA256:" + hashlib.sha256(raw).hexdigest()[:16]


def describe_trust_store(data: dict[str, dict]) -> list[dict[str, object]]:
    """Безопасное описание для диагностики (только key_id, отпечаток, отзыв)."""
    return [
        {
            "key_id": key_id,
            "fingerprint": key_fingerprint(entry["key"]),
            "revoked": bool(entry["revoked"]),
        }
        for key_id, entry in sorted(data.items())
    ]
