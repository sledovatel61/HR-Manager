"""Предпусковая диагностика пилота (Phase 14): admin read-only readiness.

Backend — граница безопасности: список проверок server-owned, клиент не
передаёт параметры. Доступ: аутентификация + admin + подтверждённый scope
update_channel_manage или pilot_full_access (HR/manager без scope — 403).

Мутации не выполняются автоматически: опасные исправления требуют явного
подтверждения на отдельных эндпоинтах (не здесь). Этот эндпоинт только
чтение (GET).
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_db
from app.deps import get_current_user
from app.models import AccessGrant, AccessGrantScope, User, UserRole
from app.schemas import ReadinessResponse

router = APIRouter(prefix="/readiness", tags=["readiness"])


def _has_active_grant(db: Session, user: User, scope: AccessGrantScope) -> bool:
    return (
        db.execute(
            select(AccessGrant).where(
                AccessGrant.user_id == user.id,
                AccessGrant.scope == scope,
                AccessGrant.revoked_at.is_(None),
            )
        ).scalar_one_or_none()
        is not None
    )


def _require_readiness_access(
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> User:
    if user.role != UserRole.ADMIN:
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="Только администратор.")
    # Need either pilot_full_access or update_channel_manage
    has_pilot = _has_active_grant(db, user, AccessGrantScope.PILOT_FULL_ACCESS)
    has_update = _has_active_grant(db, user, AccessGrantScope.UPDATE_CHANNEL_MANAGE)
    if not (has_pilot or has_update):
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            detail="Требуется подтверждённый scope: pilot_full_access или update_channel_manage.",
        )
    return user


@router.get(
    "/pilot",
    response_model=ReadinessResponse,
    summary="Предпусковая проверка готовности пилота (admin only, read-only)",
)
def get_pilot_readiness(
    request: Request,
    db: Session = Depends(get_db),
    current: User = Depends(_require_readiness_access),
) -> ReadinessResponse:
    """Вернуть server-owned список проверок с verdict."""
    from app.readiness import collect_readiness

    settings = request.app.state.settings
    engine = request.app.state.engine
    data = collect_readiness(settings, engine, db)
    return ReadinessResponse(**data)
