# -*- coding: utf-8 -*-
"""Строгая схема публичного trust store канала обновлений (Phase 14).

Trust store — единственный способ доставки доверия production-клиенту БЕЗ
`trust all`. Формат (JSON, UTF-8):

    {
      "schema_version": 1,
      "environment": "production",          # или "test" (только для fixture)
      "keys": {
        "pilot-release-2026": {
          "key": "<base64 32-байтовый Ed25519 публичный ключ>",
          "revoked": false
        }
      }
    }

Контракт fail closed:

* ровно три поля верхнего уровня и ровно два поля в записи ключа — любые
  неизвестные/лишние поля отклоняются (в том числе любые попытки протащить
  закрытый материал: ``private``, ``seed``, ``secret``, PEM, hex-64);
* schema_version == 1, environment in {production, test};
* production-хранилище нельзя собрать из test-окружения без явного
  ``allow_test`` (dev/test default никогда молча не становится production
  trust root);
* ключ — валидный base64 ровно 32 байта (Ed25519 public key);
* key_id — непустая строка 1..64 из [A-Za-z0-9._-]; уникальность ключей
  гарантируется самим JSON-объектом, дубликаты fingerprint отклоняются;
* production-хранилище обязано содержать хотя бы один неотозванный ключ.

Модуль используется и release-pipeline (publish_channel.py), и сборщиком
установщика, и backend-тестами; PowerShell-сторона движка дублирует те же
проверки при импорте хранилища в channel.json (см. Channel.psm1).
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import re
from pathlib import Path

from channel_contract import ChannelError

SCHEMA_VERSION = 1
KEY_ID_RE = re.compile(r"^[A-Za-z0-9._-]{1,64}$")
# Поля, намекающие на закрытый материал: любое их наличие — отказ.
_PRIVATE_FIELD_NAMES = {
    "private",
    "priv",
    "private_key",
    "secret",
    "secret_key",
    "seed",
    "signing_key",
    "password",
    "token",
    "d",
    "k",
}
_HEX64_RE = re.compile(r"^[0-9a-fA-F]{64}$")
_PEM_MARKERS = ("-----BEGIN", "-----END")


def key_fingerprint(key_b64: str) -> str:
    """SHA256 отпечаток публичного ключа (по декодированным байтам)."""
    try:
        raw = base64.b64decode(key_b64, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ChannelError("bad_trust_store", f"ключ не является валидным base64: {exc}") from exc
    if len(raw) != 32:
        raise ChannelError(
            "bad_trust_store", f"Ed25519 публичный ключ должен быть 32 байта, получено {len(raw)}"
        )
    return hashlib.sha256(raw).hexdigest()


def _reject_private_material(where: str, obj: object) -> None:
    """Глубокий структурный скан: имена полей и значения, похожие на секреты."""
    if isinstance(obj, dict):
        for name, value in obj.items():
            lowered = str(name).strip().lower()
            if lowered in _PRIVATE_FIELD_NAMES:
                raise ChannelError(
                    "private_material",
                    f"{where}: поле {name!r} выглядит как закрытый материал",
                )
            _reject_private_material(f"{where}.{name}", value)
    elif isinstance(obj, list):
        for index, value in enumerate(obj):
            _reject_private_material(f"{where}[{index}]", value)
    elif isinstance(obj, str):
        if any(marker in obj for marker in _PEM_MARKERS):
            raise ChannelError("private_material", f"{where}: обнаружен PEM-блок")
        if _HEX64_RE.fullmatch(obj):
            raise ChannelError(
                "private_material",
                f"{where}: значение выглядит как hex-закрытый ключ (64 hex)",
            )


def validate_trust_store(data: object, *, allow_test: bool = False) -> dict:
    """Валидация и нормализация trust store. Возвращает канонический dict.

    ``allow_test`` разрешает environment=test (только для fixture/тестов);
    production-потребители никогда его не передают.
    """
    if not isinstance(data, dict):
        raise ChannelError("bad_trust_store", "trust store должен быть JSON-объектом")
    expected_fields = {"schema_version", "environment", "keys"}
    actual_fields = set(data.keys())
    if actual_fields != expected_fields:
        missing = sorted(expected_fields - actual_fields)
        extra = sorted(actual_fields - expected_fields)
        raise ChannelError(
            "bad_trust_store",
            f"поля trust store не совпадают со схемой (нет {missing}, лишние {extra})",
        )
    _reject_private_material("trust_store", data)
    if data["schema_version"] != SCHEMA_VERSION:
        raise ChannelError(
            "bad_trust_store", f"schema_version должен быть {SCHEMA_VERSION}"
        )
    environment = data["environment"]
    if environment not in ("production", "test"):
        raise ChannelError("bad_trust_store", f"неизвестное environment: {environment!r}")
    if environment == "test" and not allow_test:
        raise ChannelError(
            "test_trust_store",
            "test/fixture trust store не может использоваться как production",
        )
    keys = data["keys"]
    if not isinstance(keys, dict) or not keys:
        raise ChannelError("bad_trust_store", "keys должен быть непустым объектом")
    normalized: dict[str, dict] = {}
    seen_fingerprints: set[str] = set()
    for key_id, entry in keys.items():
        if not isinstance(key_id, str) or not KEY_ID_RE.fullmatch(key_id):
            raise ChannelError("bad_trust_store", f"некорректный key_id: {key_id!r}")
        if not isinstance(entry, dict) or set(entry.keys()) != {"key", "revoked"}:
            raise ChannelError(
                "bad_trust_store", f"запись ключа {key_id!r} должна содержать ровно key и revoked"
            )
        key_value = entry["key"]
        if not isinstance(key_value, str) or not key_value:
            raise ChannelError("bad_trust_store", f"у ключа {key_id!r} нет значения key")
        fingerprint = key_fingerprint(key_value)
        if fingerprint in seen_fingerprints:
            raise ChannelError(
                "bad_trust_store", f"дубликат ключа (тот же fingerprint): {key_id!r}"
            )
        seen_fingerprints.add(fingerprint)
        revoked = entry["revoked"]
        if not isinstance(revoked, bool):
            raise ChannelError("bad_trust_store", f"revoked у {key_id!r} должен быть bool")
        normalized[key_id] = {"key": key_value, "revoked": revoked}
    if environment == "production" and not any(
        entry["revoked"] is False for entry in normalized.values()
    ):
        raise ChannelError(
            "bad_trust_store", "production trust store обязан иметь неотозванный ключ"
        )
    return {
        "schema_version": SCHEMA_VERSION,
        "environment": environment,
        "keys": normalized,
    }


def load_trust_store(path: str | Path, *, allow_test: bool = False) -> dict:
    """Чтение и валидация trust store из файла."""
    text = Path(path).read_text(encoding="utf-8-sig")
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ChannelError("bad_trust_store", f"некорректный JSON trust store: {exc}") from exc
    return validate_trust_store(data, allow_test=allow_test)


def stores_match(left: dict, right: dict) -> bool:
    """Каноническое сравнение двух уже валидированных хранилищ."""
    return json.dumps(left, sort_keys=True) == json.dumps(right, sort_keys=True)


def key_status(store: dict, key_id: str) -> str:
    """Статус ключа: active | revoked | unknown (для diagnostics/readiness)."""
    entry = store.get("keys", {}).get(key_id)
    if entry is None:
        return "unknown"
    return "revoked" if entry.get("revoked") else "active"


def redacted_summary(store: dict) -> list[dict]:
    """Безопасная сводка для диагностики: key_id, fingerprint, статус."""
    summary: list[dict] = []
    for key_id, entry in store.get("keys", {}).items():
        summary.append(
            {
                "key_id": key_id,
                "fingerprint": key_fingerprint(entry["key"])[:16],
                "status": "revoked" if entry.get("revoked") else "active",
            }
        )
    return summary
