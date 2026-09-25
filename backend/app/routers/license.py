"""License API — status and upload for offline pilot license."""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.audit import record_event
from app.config import Settings
from app.db import get_db
from app.deps import get_settings_from_request, require_roles
from app.license import LicenseError, fingerprint_public_key
from app.models import AuditAction, License, User, UserRole
from app.services.license_service import (
    build_license_row,
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
    status_info = get_license_status(db, settings)
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
    if file is not None:
        try:
            content = file.file.read()
            if isinstance(content, bytes):
                return content.decode("utf-8")
            return str(content)
        except Exception as exc:
            raise HTTPException(
                status_code=400,
                detail=f"Не удалось прочитать файл лицензии: {exc}",
            ) from exc

    if license_text:
        return license_text

    if license_json:
        return license_json

    raise HTTPException(
        status_code=400,
        detail="Не передан файл лицензии. Загрузите .hrmlicense или JSON.",
    )


@router.post("/upload", summary="Upload license (admin only)")
def upload_license(
    request: Request,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings_from_request),
    actor: User = Depends(_admin_only),
    license_text: str | None = Form(default=None),
    file: UploadFile | None = File(default=None),
) -> dict[str, Any]:
    content_type = request.headers.get("content-type", "")

    if "application/json" in content_type:
        raise HTTPException(
            status_code=400,
            detail=(
                "Для JSON используйте /api/license/upload-json. "
                "Этот endpoint принимает файл (multipart)."
            ),
        )

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
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Тело запроса должно быть JSON.") from None

    raw_text: str | None = None
    if isinstance(body, dict):
        if "license_text" in body:
            raw_text = body["license_text"]
            if not isinstance(raw_text, str):
                raise HTTPException(
                    status_code=400,
                    detail="license_text должен быть строкой.",
                )
        elif "license" in body:
            lic_obj = body["license"]
            if not isinstance(lic_obj, dict):
                raise HTTPException(status_code=400, detail="license должен быть объектом.")
            raw_text = json.dumps(lic_obj, ensure_ascii=False)
        else:
            if "license_id" in body and "signature" in body:
                raw_text = json.dumps(body, ensure_ascii=False)
            else:
                raise HTTPException(
                    status_code=400,
                    detail=(
                        "Передайте лицензию как "
                        '{"license_text": "..."} или '
                        '{"license": {...}} или полный объект.'
                    ),
                )
    else:
        raise HTTPException(status_code=400, detail="JSON должен быть объектом.")

    return _process_upload(db, settings, actor, request, raw_text)


def _process_upload(
    db: Session,
    settings: Settings,
    actor: User,
    request: Request,
    raw_text: str,
) -> dict[str, Any]:
    public_b64 = (settings.license_public_key or "").strip()
    if not public_b64:
        raise HTTPException(
            status_code=400,
            detail=(
                "LICENSE_PUBLIC_KEY не настроен на сервере — "
                "загрузка лицензии невозможна (настройте ключ в dev)."
            ),
        )

    try:
        data = parse_and_verify_license_text(raw_text, public_b64)
    except LicenseError as exc:
        logger.warning(
            "license upload rejected code=%s actor=%s fp=%s",
            exc.code,
            actor.username,
            fingerprint_public_key(public_b64),
        )
        try:
            record_event(
                db,
                AuditAction.LICENSE_REJECTED,
                actor=actor,
                username=actor.username,
                ip_address=client_ip(request),
                user_agent=user_agent(request.headers),
                details=(f"code={exc.code} fp={fingerprint_public_key(public_b64)}"),
                commit=True,
            )
        except Exception:
            logger.exception("audit LICENSE_REJECTED failed")

        if exc.code in ("expired",):
            detail = f"{exc} Загрузите действующую лицензию."
        elif exc.code == "bad_signature":
            detail = (
                "Подпись лицензии недействительна. "
                "Убедитесь, что файл выдан владельцем и не изменён."
            )
        elif exc.code == "clock_rollback":
            detail = str(exc)
        elif exc.code in (
            "bad_date",
            "bad_license_id",
            "bad_client_name",
            "bad_value",
            "missing_field",
        ):
            detail = f"Некорректный формат лицензии: {exc}"
        else:
            detail = f"Лицензия отклонена: {exc}"

        raise HTTPException(status_code=400, detail=detail) from exc

    from app.license import validate_time_consistency

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
            raise HTTPException(
                status_code=400,
                detail=(
                    f"Срок лицензии истёк {data['expires_at']}. Загрузите действующую лицензию."
                ),
            ) from exc
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    new_max = int(data["max_active_users"])
    with db.begin_nested():
        active_lic = get_active_license_for_update(db)
        from sqlalchemy import func

        active_count = (
            db.scalar(select(func.count()).select_from(User).where(User.is_active.is_(True))) or 0
        )

        if active_count > new_max:
            raise HTTPException(
                status_code=409,
                detail=(
                    f"Невозможно применить лицензию: сейчас активных "
                    f"пользователей {active_count}, а новый лимит "
                    f"{new_max}. Сначала отключите лишних пользователей."
                ),
            )

        if active_lic:
            active_lic.is_active = False
            active_lic.updated_at = now

        existing = db.scalar(
            select(License).where(License.license_id == data["license_id"]).limit(1)
        )
        if existing:
            if existing.is_active:
                existing.is_active = False
            existing.client_name = data["client_name"].strip()
            existing.issued_at = datetime.strptime(data["issued_at"], "%Y-%m-%dT%H:%M:%SZ").replace(
                tzinfo=UTC
            )
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

    try:
        action = AuditAction.LICENSE_REPLACED if active_lic else AuditAction.LICENSE_UPLOADED
        record_event(
            db,
            action,
            actor=actor,
            username=actor.username,
            ip_address=client_ip(request),
            user_agent=user_agent(request.headers),
            details=(
                f"license_id={new_row.license_id} "
                f"client={new_row.client_name} "
                f"expires={new_row.expires_at} "
                f"max={new_row.max_active_users} "
                f"fp={fingerprint_public_key(public_b64)} "
                f"active_users={active_count}"
            ),
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
