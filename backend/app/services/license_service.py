# -*- coding: utf-8 -*-
"""License service — DB interactions, verification, expiry, rollback protection."""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import Settings
from app.license import (
    LicenseError,
    canonical_bytes,
    days_left,
    fingerprint_public_key,
    is_expired,
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
    """Return active license row or None. Uses FOR UPDATE if caller needs lock."""
    return db.scalar(select(License).where(License.is_active.is_(True)).limit(1))


def get_active_license_for_update(db: Session) -> License | None:
    return db.scalar(
        select(License).where(License.is_active.is_(True)).with_for_update().limit(1)
    )


def count_active_users(db: Session, for_update: bool = False) -> int:
    q = select(func.count()).select_from(User).where(User.is_active.is_(True))
    if for_update:
        # Lock active users rows to prevent concurrent create race
        # For Postgres, SELECT ... FOR UPDATE with count still locks rows? We select ids for update.
        # Simpler: select User ids for update and count in Python, but we use count for performance
        # and rely on license row lock to serialize.
        pass
    return db.scalar(q) or 0


def count_active_users_locked(db: Session) -> int:
    # Lock active user rows: select ids FOR UPDATE
    ids = db.scalars(select(User.id).where(User.is_active.is_(True)).with_for_update()).all()
    return len(ids)


def parse_and_verify_license_text(text: str | bytes, public_b64: str) -> dict:
    """Parse JSON, validate fields, verify Ed25519 signature. Returns dict."""
    if isinstance(text, bytes):
        try:
            text = text.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise LicenseError("encoding", "файл лицензии должен быть UTF-8") from exc

    data = parse_license_json(text)
    # Validate fields (without signature check first to give clear errors)
    validate_license_fields(data)
    # Verify signature
    verify_signature(data, public_b64)
    return data


def build_license_row(data: dict, uploaded_by_user_id) -> License:
    from datetime import datetime as dt

    issued_at = dt.strptime(data["issued_at"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)
    exp_date_str = data["expires_at"]
    # expires_at_end inclusive 23:59:59.999999 UTC
    from datetime import date, time

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
    """Return (allowed, active_count). If active > new_max, not allowed."""
    active = count_active_users(db)
    return (active <= new_max, active)


def validate_current_license(db: Session, settings: Settings) -> dict[str, Any]:
    """
    Validate active license against public key, expiry, clock rollback.
    Updates last_seen_at if valid (monotonic).
    Returns dict with status info for API.
    Raises LicenseError with RU message if invalid.
    """
    public_b64 = (settings.license_public_key or "").strip()
    if not public_b64:
        # Enforcement disabled (dev/test without key)
        return {
            "enforcement": "disabled",
            "has_license": False,
            "is_valid": True,
            "reason": "LICENSE_PUBLIC_KEY не задан — проверка отключена (dev/test)",
        }

    lic = get_active_license(db)
    if lic is None:
        raise LicenseError("no_license", "Лицензия не установлена. Загрузите файл лицензии в разделе Лицензия.")

    # Reconstruct dict for verification
    data = {
        "license_id": lic.license_id,
        "client_name": lic.client_name,
        "issued_at": lic.issued_at.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "expires_at": lic.expires_at,
        "max_active_users": lic.max_active_users,
        "signature": lic.signature,
    }
    try:
        # Verify signature again (in case public key changed or DB tampered)
        verify_signature(data, public_b64)
    except LicenseError as exc:
        logger.warning(
            "license signature invalid: %s fp=%s",
            redacted_license_info(data),
            fingerprint_public_key(public_b64),
        )
        raise LicenseError("bad_signature", "Подпись лицензии недействительна. Загрузите корректный файл лицензии.") from exc

    now = _now()
    # Check clock rollback and expiry
    try:
        validate_time_consistency(
            issued_at_str=data["issued_at"],
            expires_at_str=data["expires_at"],
            last_seen_at=lic.last_seen_at,
            now=now,
        )
    except LicenseError as exc:
        if exc.code == "expired":
            raise LicenseError("expired", f"Срок лицензии истёк {data['expires_at']} (действовала до конца дня по UTC). Администратор может войти и загрузить новую лицензию.") from exc
        elif exc.code == "clock_rollback":
            raise LicenseError("clock_rollback", "Обнаружен перевод системного времени назад. Проверьте часы сервера. Лицензия временно заблокирована для защиты срока.") from exc
        else:
            raise

    # Update last_seen_at monotonic
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
            "last_seen_at": lic.last_seen_at.isoformat() if lic.last_seen_at else None,
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

    data = {
        "license_id": lic.license_id,
        "client_name": lic.client_name,
        "issued_at": lic.issued_at.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "expires_at": lic.expires_at,
        "max_active_users": lic.max_active_users,
        "signature": lic.signature,
    }
    now = _now()
    try:
        verify_signature(data, public_b64)
        validate_time_consistency(
            issued_at_str=data["issued_at"],
            expires_at_str=data["expires_at"],
            last_seen_at=lic.last_seen_at,
            now=now,
        )
        # Valid
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
                "last_seen_at": lic.last_seen_at.isoformat() if lic.last_seen_at else None,
            },
        }
    except LicenseError as exc:
        # Invalid but we still return info
        dl = None
        try:
            dl = days_left(lic.expires_at, now)
        except Exception:
            pass
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
                "last_seen_at": lic.last_seen_at.isoformat() if lic.last_seen_at else None,
            },
        }
