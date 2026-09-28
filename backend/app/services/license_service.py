"""License service — DB interactions, verification, expiry, rollback protection."""

from __future__ import annotations

import contextlib
import logging
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import Settings
from app.license import (
    LicenseError,
    days_left,
    fingerprint_public_key,
    parse_license_json,
    redacted_license_info,
    validate_license_fields,
    validate_time_consistency,
    verify_signature,
)
from app.models import License, User

logger = logging.getLogger(__name__)


def _now() -> datetime:
    return datetime.now(UTC)


def get_active_license(db: Session) -> License | None:
    """Return active license row or None."""
    return db.scalar(select(License).where(License.is_active.is_(True)).limit(1))


def get_active_license_for_update(db: Session) -> License | None:
    return db.scalar(select(License).where(License.is_active.is_(True)).with_for_update().limit(1))


def count_active_users(db: Session, for_update: bool = False) -> int:
    q = select(func.count()).select_from(User).where(User.is_active.is_(True))
    return db.scalar(q) or 0


def count_active_users_locked(db: Session) -> int:
    ids = db.scalars(select(User.id).where(User.is_active.is_(True)).with_for_update()).all()
    return len(ids)


def parse_and_verify_license_text(text: str | bytes, public_b64: str) -> dict[str, Any]:
    """Parse JSON, validate fields, verify Ed25519 signature."""
    if isinstance(text, bytes):
        try:
            text = text.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise LicenseError("encoding", "файл лицензии должен быть UTF-8") from exc

    data = parse_license_json(text)
    validate_license_fields(data)
    verify_signature(data, public_b64)
    return data


def build_license_row(data: dict, uploaded_by_user_id: object) -> License:
    from datetime import date, time
    from datetime import datetime as dt

    issued_at = dt.strptime(data["issued_at"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)
    exp_date_str = data["expires_at"]
    exp_date = date.fromisoformat(exp_date_str)
    exp_end = datetime.combine(exp_date, time.max).replace(tzinfo=UTC)

    return License(
        license_id=data["license_id"],
        client_name=data["client_name"].strip(),
        issued_at=issued_at,
        expires_at=exp_date_str,
        expires_at_end=exp_end,
        max_active_users=int(data["max_active_users"]),
        signature=data["signature"].lower(),
        is_active=True,
        last_seen_at=_now(),
        uploaded_by_user_id=uploaded_by_user_id,
    )


def check_replacement_allowed(db: Session, new_max: int) -> tuple[bool, int]:
    """Return (allowed, active_count)."""
    active = count_active_users(db)
    return (active <= new_max, active)


def validate_current_license(db: Session, settings: Settings) -> dict[str, Any]:
    """Validate active license, update last_seen_at, raise LicenseError if invalid."""
    public_b64 = (settings.license_public_key or "").strip()
    if not public_b64:
        return {
            "enforcement": "disabled",
            "has_license": False,
            "is_valid": True,
            "reason": ("LICENSE_PUBLIC_KEY не задан — проверка отключена (dev/test)"),
        }

    lic = get_active_license(db)
    if lic is None:
        raise LicenseError(
            "no_license",
            "Лицензия не установлена. Загрузите файл лицензии в разделе Лицензия.",
        )

    data: dict[str, Any] = {
        "license_id": lic.license_id,
        "client_name": lic.client_name,
        "issued_at": lic.issued_at.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "expires_at": lic.expires_at,
        "max_active_users": lic.max_active_users,
        "signature": lic.signature,
    }
    try:
        verify_signature(data, public_b64)
    except LicenseError as exc:
        logger.warning(
            "license signature invalid: %s fp=%s",
            redacted_license_info(data),
            fingerprint_public_key(public_b64),
        )
        raise LicenseError(
            "bad_signature",
            "Подпись лицензии недействительна. Загрузите корректный файл.",
        ) from exc

    now = _now()
    try:
        validate_time_consistency(
            issued_at_str=str(data["issued_at"]),
            expires_at_str=str(data["expires_at"]),
            last_seen_at=lic.last_seen_at,
            now=now,
        )
    except LicenseError as exc:
        if exc.code == "expired":
            raise LicenseError(
                "expired",
                f"Срок лицензии истёк {data['expires_at']} "
                "(действовала до конца дня по UTC). "
                "Администратор может войти и загрузить новую лицензию.",
            ) from exc
        elif exc.code == "clock_rollback":
            raise LicenseError(
                "clock_rollback",
                "Обнаружен перевод системного времени назад. "
                "Проверьте часы сервера. Лицензия временно заблокирована.",
            ) from exc
        else:
            raise

    if lic.last_seen_at is None or now > lic.last_seen_at:
        lic.last_seen_at = now
        db.commit()

    active_count = count_active_users(db)
    return {
        "enforcement": "enabled",
        "has_license": True,
        "is_valid": True,
        "license": {
            "license_id": lic.license_id,
            "client_name": lic.client_name,
            "issued_at": data["issued_at"],
            "expires_at": lic.expires_at,
            "max_active_users": lic.max_active_users,
            "days_left": days_left(lic.expires_at, now),
            "active_users": active_count,
            "last_seen_at": (lic.last_seen_at.isoformat() if lic.last_seen_at else None),
        },
    }


def get_license_status(db: Session, settings: Settings) -> dict[str, Any]:
    public_b64 = (settings.license_public_key or "").strip()
    if not public_b64:
        active = count_active_users(db)
        lic = get_active_license(db)
        if lic:
            return {
                "enforcement": "disabled",
                "has_license": True,
                "is_valid": True,
                "license": {
                    "license_id": lic.license_id,
                    "client_name": lic.client_name,
                    "issued_at": lic.issued_at.strftime("%Y-%m-%dT%H:%M:%SZ"),
                    "expires_at": lic.expires_at,
                    "max_active_users": lic.max_active_users,
                    "active_users": active,
                },
                "note": "LICENSE_PUBLIC_KEY не задан — проверка отключена",
            }
        return {
            "enforcement": "disabled",
            "has_license": False,
            "is_valid": True,
            "active_users": active,
            "note": "LICENSE_PUBLIC_KEY не задан — проверка отключена",
        }

    lic = get_active_license(db)
    active_count = count_active_users(db)
    if lic is None:
        return {
            "enforcement": "enabled",
            "has_license": False,
            "is_valid": False,
            "code": "no_license",
            "message": "Лицензия не установлена. Загрузите файл лицензии.",
            "active_users": active_count,
            "max_active_users": None,
        }

    data2: dict[str, Any] = {
        "license_id": lic.license_id,
        "client_name": lic.client_name,
        "issued_at": lic.issued_at.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "expires_at": lic.expires_at,
        "max_active_users": lic.max_active_users,
        "signature": lic.signature,
    }
    now = _now()
    data = data2

    try:
        verify_signature(data, public_b64)
        validate_time_consistency(
            issued_at_str=str(data["issued_at"]),
            expires_at_str=str(data["expires_at"]),
            last_seen_at=lic.last_seen_at,
            now=now,
        )
        return {
            "enforcement": "enabled",
            "has_license": True,
            "is_valid": True,
            "license": {
                "license_id": lic.license_id,
                "client_name": lic.client_name,
                "issued_at": data["issued_at"],
                "expires_at": lic.expires_at,
                "max_active_users": lic.max_active_users,
                "active_users": active_count,
                "days_left": days_left(lic.expires_at, now),
                "last_seen_at": (lic.last_seen_at.isoformat() if lic.last_seen_at else None),
            },
        }
    except LicenseError as exc:
        dl = None
        with contextlib.suppress(Exception):
            dl = days_left(lic.expires_at, now)
        return {
            "enforcement": "enabled",
            "has_license": True,
            "is_valid": False,
            "code": exc.code,
            "message": str(exc),
            "license": {
                "license_id": lic.license_id,
                "client_name": lic.client_name,
                "issued_at": data["issued_at"],
                "expires_at": lic.expires_at,
                "max_active_users": lic.max_active_users,
                "active_users": active_count,
                "days_left": dl,
                "last_seen_at": (lic.last_seen_at.isoformat() if lic.last_seen_at else None),
            },
        }
