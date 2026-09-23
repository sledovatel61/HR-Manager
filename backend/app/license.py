"""Offline license for closed pilot — installation/server license.

Format (JSON file *.hrmlicense):

{
  "license_id": "uuid v4",
  "client_name": "Пилот Марии",
  "issued_at": "2026-09-23T06:00:00Z",
  "expires_at": "2026-12-31",
  "max_active_users": 5,
  "signature": "<128 hex Ed25519>"
}

Canonical payload for signing (fixed order, LF, UTF-8, trailing LF):
  license_id:<value>
  client_name:<value>
  issued_at:<value>
  expires_at:<value>
  max_active_users:<value>

Date semantics:
- issued_at: ISO8601 UTC datetime, must be <= now
- expires_at: YYYY-MM-DD inclusive until 23:59:59 UTC

Clock rollback protection (best-effort):
- Store last_seen_at, updated to max(last_seen_at, now)
- If now < last_seen_at - 1h => invalid (rollback)

Signature:
- Ed25519 detached, hex 128 chars, over canonical bytes
- Public key: base64 32 bytes, from Settings or file

Private key NEVER in git/installer/frontend/Docker/logs.
"""

from __future__ import annotations

import base64
import hashlib
import json
import re
import uuid
from datetime import UTC, date, datetime, timedelta
from typing import Any

LICENSE_FIELDS: list[tuple[str, str]] = [
    ("license_id", "str"),
    ("client_name", "str"),
    ("issued_at", "str"),
    ("expires_at", "str"),
    ("max_active_users", "int"),
]

LICENSE_ID_RE = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{12}$"
)
CLIENT_NAME_MAX = 200
EXPIRES_AT_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
ISO_UTC_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
MAX_USERS_MIN = 1
MAX_USERS_MAX = 1000
SIGNATURE_RE = re.compile(r"^[0-9a-fA-F]{128}$")


class LicenseError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def _no_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    data: dict[str, Any] = {}
    for k, v in pairs:
        if k in data:
            raise LicenseError(
                "duplicate_key", f"повторяющийся ключ JSON: {k!r}"
            )
        data[k] = v
    return data


def parse_license_json(text: str | bytes) -> dict:
    try:
        raw = json.loads(text, object_pairs_hook=_no_duplicates)
    except LicenseError:
        raise
    except Exception as exc:
        raise LicenseError(
            "malformed_json", f"некорректный JSON лицензии: {exc}"
        ) from exc
    if not isinstance(raw, dict):
        raise LicenseError(
            "malformed_json", "лицензия должна быть JSON-объектом"
        )
    return raw


def _validate_iso_utc(value: str, field: str) -> datetime:
    if not ISO_UTC_RE.match(value):
        raise LicenseError(
            "bad_date",
            f"{field} должен быть ISO-8601 UTC YYYY-MM-DDTHH:MM:SSZ",
        )
    try:
        dt = datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(
            tzinfo=UTC
        )
    except ValueError as exc:
        raise LicenseError("bad_date", f"некорректная дата {field}") from exc
    return dt


def _validate_expires_date(value: str) -> date:
    if not EXPIRES_AT_RE.match(value):
        raise LicenseError("bad_date", "expires_at должен быть YYYY-MM-DD")
    try:
        d = date.fromisoformat(value)
    except ValueError as exc:
        raise LicenseError("bad_date", "некорректная дата expires_at") from exc
    return d


def _validate_payload_fields(data: dict) -> None:
    unknown = sorted(
        set(data) - {name for name, _ in LICENSE_FIELDS} - {"signature"}
    )
    if unknown:
        raise LicenseError(
            "unknown_field",
            f"неизвестные поля лицензии: {', '.join(unknown)}",
        )
    for name, kind in LICENSE_FIELDS:
        if name not in data:
            raise LicenseError("missing_field", f"отсутствует поле: {name}")
        v = data[name]
        if kind == "int":
            if isinstance(v, bool) or not isinstance(v, int):
                raise LicenseError(
                    "bad_type", f"поле {name} должно быть целым числом"
                )
            if name == "max_active_users" and not (
                MAX_USERS_MIN <= v <= MAX_USERS_MAX
            ):
                raise LicenseError(
                    "bad_value",
                    f"max_active_users должен быть {MAX_USERS_MIN}..{MAX_USERS_MAX}",
                )
        else:
            if not isinstance(v, str):
                raise LicenseError(
                    "bad_type", f"поле {name} должно быть строкой"
                )
            if "\n" in v or "\r" in v or any(ord(c) < 0x20 for c in v):
                raise LicenseError(
                    "bad_value",
                    f"поле {name} содержит управляющие символы",
                )

    if not LICENSE_ID_RE.match(data["license_id"]):
        raise LicenseError("bad_license_id", "license_id должен быть UUID")
    try:
        uuid.UUID(data["license_id"])
    except ValueError as exc:
        raise LicenseError(
            "bad_license_id", "license_id не является UUID"
        ) from exc

    client = data["client_name"].strip()
    if not client:
        raise LicenseError(
            "bad_client_name", "client_name не должен быть пустым"
        )
    if len(client) > CLIENT_NAME_MAX:
        raise LicenseError(
            "bad_client_name",
            f"client_name слишком длинный (>{CLIENT_NAME_MAX})",
        )

    _validate_iso_utc(data["issued_at"], "issued_at")
    exp_date = _validate_expires_date(data["expires_at"])
    iss_dt = _validate_iso_utc(data["issued_at"], "issued_at")
    if iss_dt.date() > exp_date:
        raise LicenseError(
            "bad_date", "issued_at не может быть позже expires_at"
        )


def validate_license_fields(data: dict) -> None:
    _validate_payload_fields(data)
    sig = data.get("signature")
    if not isinstance(sig, str) or not SIGNATURE_RE.match(sig):
        raise LicenseError(
            "bad_signature",
            "signature должен быть 128 hex символов Ed25519",
        )


def canonical_bytes(data: dict) -> bytes:
    _validate_payload_fields(data)
    lines = [f"{name}:{data[name]}" for name, _ in LICENSE_FIELDS]
    return ("\n".join(lines) + "\n").encode("utf-8")


def _load_public_key(public_b64: str):
    from cryptography.hazmat.primitives.asymmetric.ed25519 import (
        Ed25519PublicKey,
    )

    try:
        raw = base64.b64decode(public_b64, validate=True)
    except Exception as exc:
        raise LicenseError(
            "bad_public_key", "публичный ключ должен быть base64"
        ) from exc
    if len(raw) != 32:
        raise LicenseError(
            "bad_public_key", "публичный ключ должен быть 32 байта"
        )
    try:
        return Ed25519PublicKey.from_public_bytes(raw)
    except Exception as exc:
        raise LicenseError(
            "bad_public_key", "некорректный публичный ключ Ed25519"
        ) from exc


def verify_signature(data: dict, public_b64: str) -> bytes:
    from cryptography.exceptions import InvalidSignature

    sig_hex = data.get("signature")
    if not isinstance(sig_hex, str):
        raise LicenseError("bad_signature", "отсутствует signature")
    public_key = _load_public_key(public_b64)
    payload = canonical_bytes(
        {k: v for k, v in data.items() if k != "signature"}
    )
    try:
        public_key.verify(bytes.fromhex(sig_hex), payload)
    except InvalidSignature as exc:
        raise LicenseError(
            "bad_signature", "подпись лицензии недействительна"
        ) from exc
    except ValueError as exc:
        raise LicenseError(
            "bad_signature", f"ошибка проверки подписи: {exc}"
        ) from exc
    return payload


def sign_license(data: dict, private_hex: str) -> str:
    from cryptography.hazmat.primitives.asymmetric.ed25519 import (
        Ed25519PrivateKey,
    )

    try:
        priv_bytes = bytes.fromhex(private_hex)
    except Exception as exc:
        raise LicenseError(
            "bad_private_key", "приватный ключ должен быть 64 hex"
        ) from exc
    if len(priv_bytes) != 32:
        raise LicenseError(
            "bad_private_key", "приватный ключ должен быть 32 байта"
        )
    key = Ed25519PrivateKey.from_private_bytes(priv_bytes)
    payload = canonical_bytes(
        {k: v for k, v in data.items() if k != "signature"}
    )
    return key.sign(payload).hex()


def sign_license_dict(data: dict, private_hex: str) -> dict:
    sig = sign_license(data, private_hex)
    return {**data, "signature": sig}


def is_expired(expires_at_str: str, now: datetime | None = None) -> bool:
    now = now or datetime.now(UTC)
    exp_date = _validate_expires_date(expires_at_str)
    exp_end = datetime.combine(exp_date, datetime.max.time()).replace(
        tzinfo=UTC
    )
    return now > exp_end


def days_left(expires_at_str: str, now: datetime | None = None) -> int:
    now = now or datetime.now(UTC)
    exp_date = _validate_expires_date(expires_at_str)
    exp_end = datetime.combine(exp_date, datetime.max.time()).replace(
        tzinfo=UTC
    )
    delta = exp_end - now
    return max(-1, delta.days)


def validate_time_consistency(
    issued_at_str: str,
    expires_at_str: str,
    last_seen_at: datetime | None,
    now: datetime | None,
) -> None:
    now = now or datetime.now(UTC)
    issued_at = _validate_iso_utc(issued_at_str, "issued_at")

    if now < issued_at - timedelta(minutes=5):
        raise LicenseError(
            "clock_rollback",
            "системное время раньше даты выпуска лицензии",
        )

    if last_seen_at is not None and now < last_seen_at - timedelta(hours=1):
        raise LicenseError(
            "clock_rollback",
            "системное время переведено назад (защита срока)",
        )

    if is_expired(expires_at_str, now):
        raise LicenseError(
            "expired",
            f"срок лицензии истёк {expires_at_str} "
            "(действовала до конца дня по UTC)",
        )


def fingerprint_public_key(public_b64: str) -> str:
    try:
        raw = base64.b64decode(public_b64, validate=True)
        sha = hashlib.sha256(raw).hexdigest()
        return f"SHA256:{sha[:16]}... (redacted)"
    except Exception:
        return "SHA256:invalid"


def redacted_license_info(data: dict) -> dict:
    return {
        "license_id": data.get("license_id"),
        "client_name": data.get("client_name"),
        "expires_at": data.get("expires_at"),
        "max_active_users": data.get("max_active_users"),
        "issued_at": data.get("issued_at"),
    }
