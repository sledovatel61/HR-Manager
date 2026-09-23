# -*- coding: utf-8 -*-
"""License API — status and upload for offline pilot license.

- GET /api/license/status — any authenticated user (and also allowed without license for diagnostics)
- POST /api/license/upload — admin only, accepts JSON or file upload
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.audit import record_event
from app.config import Settings
from app.deps import get_settings_from_request
from app.db import get_db
from app.deps import get_current_user, require_roles
from app.license import LicenseError, fingerprint_public_key, redacted_license_info
from app.models import AuditAction, License, User, UserRole
from app.services.license_service import (
    build_license_row,
    check_replacement_allowed,
    count_active_users,
    get_active_license,
    get_active_license_for_update,
    get_license_status,
    parse_and_verify_license_text,
)
from app.utils import client_ip, user_agent

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/license", tags=["license"])

_admin_only = require_roles(UserRole.ADMIN)


@router.get("/status", summary="License status (admin + diagnostics)")
def license_status(
    request: Request,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings_from_request),
) -> dict[str, Any]:
    """Return current license status. Allowed without license (guard allows /api/license/*)."""
    # Even unauthenticated? Require at least auth/me allowed, but status should be available to admin after login.
    # If no current_user, we still return status for diagnostics (ops/pilot-readiness already allows, but license status itself is guarded as allowed)
    # So we allow anonymous for first-run diagnostics? Keep it authenticated if possible, but not mandatory for pilot-readiness.
    # We will return status regardless of auth, but if auth present, audit? No.
    status_info = get_license_status(db, settings)
    # Add public key fingerprint for diagnostics (redacted)
    if settings.license_public_key:
        status_info["public_key_fingerprint"] = fingerprint_public_key(settings.license_public_key)
    else:
        status_info["public_key_fingerprint"] = None
    return status_info


def _extract_license_text_from_request(
    license_text: str | None,
    license_json: str | None,
    file: UploadFile | None,
) -> str:
    """Extract raw license JSON text from various inputs."""
    if file is not None:
        # Read file content
        try:
            content = file.file.read()
            if isinstance(content, bytes):
                return content.decode("utf-8")
            return str(content)
        except Exception as exc:
            raise HTTPException(status_code=400, detail=f"Не удалось прочитать файл лицензии: {exc}") from exc

    if license_text:
        return license_text

    if license_json:
        return license_json

    raise HTTPException(status_code=400, detail="Не передан файл лицензии. Загрузите .hrmlicense файл или вставьте JSON.")


@router.post("/upload", summary="Upload license (admin only)")
def upload_license(
    request: Request,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings_from_request),
    actor: User = Depends(_admin_only),
    # Support both JSON body and multipart
    license_text: str | None = Form(default=None),
    file: UploadFile | None = File(default=None),
) -> dict[str, Any]:
    """
    Upload license file. Accepts multipart/form-data with file field,
    or JSON body with license_text.

    For JSON requests (Content-Type: application/json), the endpoint also
    accepts {"license_text": "..."} or {"license": {...}} or raw license JSON.

    Only admin can replace. Replacement rejected if active_users > new limit.
    """
    # Determine input: if request is JSON, parse body manually because Form/File won't be populated
    raw_text: str | None = None
    content_type = request.headers.get("content-type", "")

    if "application/json" in content_type:
        # We need to read JSON body synchronously? FastAPI would have parsed via dependency, but we used Form.
        # So we try to get raw body from request? This endpoint is sync, but we can use request.json() via async? Instead, we will handle via a second endpoint for JSON.
        # For simplicity, we will not support JSON here; we create a separate JSON endpoint below.
        raise HTTPException(
            status_code=400,
            detail="Для JSON используйте /api/license/upload-json. Этот endpoint принимает файл (multipart).",
        )

    # Multipart path
    try:
        raw_text = _extract_license_text_from_request(license_text, None, file)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Ошибка чтения лицензии: {exc}") from exc

    return _process_upload(db, settings, actor, request, raw_text)


@router.post("/upload-json", summary="Upload license via JSON (admin only)")
async def upload_license_json(
    request: Request,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings_from_request),
    actor: User = Depends(_admin_only),
) -> dict[str, Any]:
    """Accept JSON body: {"license_text": "..."} or {"license": {...}} or raw license object."""
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Тело запроса должно быть JSON.")

    raw_text: str | None = None
    if isinstance(body, dict):
        if "license_text" in body:
            raw_text = body["license_text"]
            if not isinstance(raw_text, str):
                raise HTTPException(status_code=400, detail="license_text должен быть строкой.")
        elif "license" in body:
            lic_obj = body["license"]
            if not isinstance(lic_obj, dict):
                raise HTTPException(status_code=400, detail="license должен быть объектом.")
            raw_text = json.dumps(lic_obj, ensure_ascii=False)
        else:
            # Assume body itself is the license object
            # Check if it looks like license (has license_id)
            if "license_id" in body and "signature" in body:
                raw_text = json.dumps(body, ensure_ascii=False)
            else:
                raise HTTPException(
                    status_code=400,
                    detail="Передайте лицензию как {\"license_text\": \"...\"} или {\"license\": {...}} или полный объект лицензии.",
                )
    else:
        raise HTTPException(status_code=400, detail="JSON должен быть объектом.")

    return _process_upload(db, settings, actor, request, raw_text)


def _process_upload(
    db: Session, settings: Settings, actor: User, request: Request, raw_text: str
) -> dict[str, Any]:
    public_b64 = (settings.license_public_key or "").strip()
    if not public_b64:
        # In dev/test without key, allow upload but skip signature verification? No, require key for verification.
        # For dev/test, we still allow but use provided test key if set, otherwise skip enforcement.
        # If no key, we cannot verify — reject with clear message unless in test mode where we allow any?
        # Spec: in pilot/production key MUST be set. In test/dev enforcement disabled, but upload should still work if key set.
        # If key empty, we treat as disabled and allow upload without verification for testing? Better to require key for verification.
        # We'll check: if enforcement disabled, skip signature check and just store? But that would allow unsigned arbitrary data — forbidden.
        # So we require public key to be set even in dev for upload to be meaningful. If not set, return 400.
        raise HTTPException(
            status_code=400,
            detail="LICENSE_PUBLIC_KEY не настроен на сервере — загрузка лицензии невозможна (настройте ключ в dev).",
        )

    # Parse and verify
    try:
        data = parse_and_verify_license_text(raw_text, public_b64)
    except LicenseError as exc:
        logger.warning(
            "license upload rejected code=%s actor=%s fp=%s",
            exc.code,
            actor.username,
            fingerprint_public_key(public_b64),
        )
        # Audit rejection
        try:
            record_event(
                db,
                AuditAction.LICENSE_REJECTED,
                actor=actor,
                username=actor.username,
                ip_address=client_ip(request),
                user_agent=user_agent(request.headers),
                details=f"code={exc.code} fp={fingerprint_public_key(public_b64)}",
                commit=True,
            )
        except Exception:
            logger.exception("audit LICENSE_REJECTED failed")

        # RU messages
        if exc.code in ("expired",):
            detail = f"{exc} Загрузите действующую лицензию."
        elif exc.code == "bad_signature":
            detail = "Подпись лицензии недействительна. Убедитесь, что файл выдан владельцем и не изменён."
        elif exc.code == "clock_rollback":
            detail = str(exc)
        elif exc.code in ("bad_date", "bad_license_id", "bad_client_name", "bad_value", "missing_field"):
            detail = f"Некорректный формат лицензии: {exc}"
        else:
            detail = f"Лицензия отклонена: {exc}"

        raise HTTPException(status_code=400, detail=detail) from exc

    # Check expiry (is_expired already checked in validate_time_consistency? But we also check explicit)
    from app.license import is_expired as _is_expired, validate_time_consistency

    now = datetime.now(UTC)
    try:
        validate_time_consistency(
            issued_at_str=data["issued_at"],
            expires_at_str=data["expires_at"],
            last_seen_at=None,
            now=now,
        )
    except LicenseError as exc:
        if exc.code == "expired":
            raise HTTPException(status_code=400, detail=f"Срок лицензии истёк {data['expires_at']}. Загрузите действующую лицензию.") from exc
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    # Replacement check: active_users > new_limit?
    new_max = int(data["max_active_users"])
    # Use FOR UPDATE to serialize replacement
    with db.begin_nested():
        active_lic = get_active_license_for_update(db)
        # Count active users with lock
        from sqlalchemy import func

        active_count = db.scalar(select(func.count()).select_from(User).where(User.is_active.is_(True))) or 0

        if active_count > new_max:
            raise HTTPException(
                status_code=400,
                detail=f"Невозможно применить лицензию: сейчас активных пользователей {active_count}, а новый лимит {new_max}. Сначала отключите лишних пользователей, затем повторите загрузку.",
            )

        # Deactivate old license if exists
        if active_lic:
            active_lic.is_active = False
            active_lic.updated_at = now

        # Check if same license_id already exists (history)
        existing = db.scalar(select(License).where(License.license_id == data["license_id"]).limit(1))
        if existing:
            # If same id exists and is active, it's a re-upload? Allow if same content? But we already deactivated old active.
            # If same id exists but different signature/client, treat as conflict? For simplicity, allow re-upload if same id but update.
            if existing.is_active:
                existing.is_active = False
            # Create new row with new id? Actually license_id same, but we want history. So we can reuse existing row? Better create new row with same license_id but different PK? But license_id unique constraint would fail.
            # So if same license_id exists, we update it and reactivate.
            existing.client_name = data["client_name"].strip()
            existing.issued_at = datetime.strptime(data["issued_at"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)
            from datetime import date, time

            exp_date = date.fromisoformat(data["expires_at"])
            existing.expires_at = data["expires_at"]
            existing.expires_at_end = datetime.combine(exp_date, time.max).replace(tzinfo=UTC)
            existing.max_active_users = new_max
            existing.signature = data["signature"].lower()
            existing.is_active = True
            existing.last_seen_at = now
            existing.uploaded_by_user_id = actor.id
            existing.updated_at = now
            new_row = existing
        else:
            new_row = build_license_row(data, actor.id)
            db.add(new_row)

    db.commit()
    db.refresh(new_row)

    # Audit success
    try:
        action = AuditAction.LICENSE_REPLACED if active_lic else AuditAction.LICENSE_UPLOADED
        record_event(
            db,
            action,
            actor=actor,
            username=actor.username,
            ip_address=client_ip(request),
            user_agent=user_agent(request.headers),
            details=f"license_id={new_row.license_id} client={new_row.client_name} expires={new_row.expires_at} max={new_row.max_active_users} fp={fingerprint_public_key(public_b64)} active_users={active_count}",
            commit=True,
        )
    except Exception:
        logger.exception("audit license upload failed")

    logger.info(
        "license uploaded id=%s client=%s expires=%s max=%s actor=%s fp=%s",
        new_row.license_id,
        new_row.client_name,
        new_row.expires_at,
        new_row.max_active_users,
        actor.username,
        fingerprint_public_key(public_b64),
    )

    return {
        "license_id": new_row.license_id,
        "client_name": new_row.client_name,
        "issued_at": new_row.issued_at.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "expires_at": new_row.expires_at,
        "max_active_users": new_row.max_active_users,
        "active_users": active_count,
        "message": "Лицензия успешно загружена и активирована.",
    }
