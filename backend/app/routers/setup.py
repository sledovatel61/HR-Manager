"""Setup wizard and pilot access administration (phase 8).

The wizard is intentionally minimal and idempotent:

* an admin creates the single pilot account (full combined HR/manager/admin
  access = role ``admin`` + an explicit ``pilot_full_access`` grant);
* a repeated run never resets an existing user's password and never
  silently extends anyone's rights (409 with a clear explanation);
* the setup state endpoint honestly reports what works and what is not
  configured (Telegram/email are reserved for phase 9 and are always
  ``not_configured`` here — nothing is faked).

Passwords are hashed and never logged or returned.
"""

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.audit import record_event
from app.db import get_db
from app.deps import get_current_user, require_roles
from app.models import (
    AccessGrant,
    AccessGrantScope,
    AuditAction,
    NotificationPreference,
    User,
    UserRole,
)
from app.schemas import (
    AccessGrantList,
    AccessGrantOut,
    AccessGrantRequest,
    PilotCreateOut,
    PilotCreateRequest,
    SetupStateOut,
)
from app.security import hash_password
from app.utils import utc_now
from app.worker import worker_is_healthy

router = APIRouter(tags=["setup"])

_admin_only = require_roles(UserRole.ADMIN)


def _active_pilot_grant(db: Session) -> AccessGrant | None:
    return db.execute(
        select(AccessGrant).where(
            AccessGrant.scope == AccessGrantScope.PILOT_FULL_ACCESS,
            AccessGrant.revoked_at.is_(None),
        )
    ).scalar_one_or_none()


@router.get("/setup/state", response_model=SetupStateOut, summary="Setup wizard state")
def setup_state(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> SetupStateOut:
    """Honest status: pilot presence, own preferences, worker, channels."""
    settings = request.app.state.settings
    pilot_grant = _active_pilot_grant(db)
    preferences = db.get(NotificationPreference, user.id)

    tg_status = "not_configured"
    if settings.telegram_bot_token.strip():
        if preferences and preferences.telegram_chat_id is not None and preferences.telegram_opt_in:
            tg_status = "working"
        elif preferences and preferences.telegram_chat_id is not None:
            tg_status = "revoked"
        else:
            tg_status = "configured_in_system"

    email_status = "not_configured"
    if settings.smtp_host.strip():
        if preferences and preferences.email_address and preferences.email_opt_in:
            email_status = "working"
        elif preferences and preferences.email_address:
            email_status = "revoked"
        else:
            email_status = "configured_in_system"

    return SetupStateOut(
        pilot_exists=pilot_grant is not None,
        pilot_grant_active=pilot_grant is not None,
        preferences_initialized=preferences is not None,
        worker_alive=worker_is_healthy(db, settings=settings, now=utc_now()),
        channels={"telegram": tg_status, "email": email_status},
    )


@router.post(
    "/setup/pilot",
    response_model=PilotCreateOut,
    status_code=status.HTTP_201_CREATED,
    summary="Create the pilot account (admin only)",
)
def create_pilot(
    payload: PilotCreateRequest,
    request: Request,
    db: Session = Depends(get_db),
    admin: User = Depends(_admin_only),
) -> PilotCreateOut:
    """Idempotent, safe pilot creation: a fresh admin user plus an explicit
    pilot grant. Existing usernames are never touched (no password reset,
    no silent rights extension)."""
    existing = db.execute(
        select(User).where(User.username == payload.username.strip())
    ).scalar_one_or_none()
    if existing is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "Пользователь с таким именем уже существует. Пароль не изменён "
                "и права не расширены; используйте другого пользователя или "
                "endpoint грантов для явной выдачи прав."
            ),
        )
    if _active_pilot_grant(db) is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Пилотный пользователь уже назначен (повторный запуск мастера не требуется).",
        )
    pilot = User(
        username=payload.username.strip(),
        full_name=payload.full_name.strip(),
        role=UserRole.ADMIN,
        password_hash=hash_password(payload.password),
        is_active=True,
        created_at=utc_now(),
        updated_at=utc_now(),
    )
    db.add(pilot)
    db.flush()
    grant = AccessGrant(
        user_id=pilot.id,
        scope=AccessGrantScope.PILOT_FULL_ACCESS,
        granted_by_user_id=admin.id,
        granted_at=utc_now(),
    )
    db.add(grant)
    record_event(
        db,
        AuditAction.PILOT_USER_CREATED,
        actor=admin,
        subject=pilot,
        details=f"pilot account {pilot.username}",
        commit=False,
    )
    record_event(
        db,
        AuditAction.PILOT_ACCESS_GRANTED,
        actor=admin,
        subject=pilot,
        details="scope=pilot_full_access",
        commit=False,
    )
    db.commit()
    return PilotCreateOut(user_id=pilot.id, username=pilot.username)


@router.get("/admin/access-grants", response_model=AccessGrantList, summary="List grants")
def list_grants(
    db: Session = Depends(get_db),
    admin: User = Depends(_admin_only),
) -> AccessGrantList:
    rows = db.execute(select(AccessGrant).order_by(AccessGrant.granted_at.desc())).scalars().all()
    return AccessGrantList(items=[AccessGrantOut.model_validate(row) for row in rows])


@router.post("/admin/access-grants", response_model=AccessGrantOut, summary="Grant/revoke")
def change_grant(
    payload: AccessGrantRequest,
    db: Session = Depends(get_db),
    admin: User = Depends(_admin_only),
) -> AccessGrantOut:
    """Grant or revoke pilot full access explicitly, with audit."""
    user = db.get(User, payload.user_id)
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Пользователь не найден.")
    if payload.revoke:
        active = db.execute(
            select(AccessGrant).where(
                AccessGrant.user_id == user.id,
                AccessGrant.scope == AccessGrantScope.PILOT_FULL_ACCESS,
                AccessGrant.revoked_at.is_(None),
            )
        ).scalar_one_or_none()
        if active is None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT, detail="Активный грант не найден."
            )
        active.revoked_at = utc_now()
        active.revoke_reason = payload.revoke_reason
        record_event(
            db,
            AuditAction.PILOT_ACCESS_REVOKED,
            actor=admin,
            subject=user,
            details="scope=pilot_full_access",
            commit=False,
        )
        db.commit()
        db.refresh(active)
        return AccessGrantOut.model_validate(active)
    existing = db.execute(
        select(AccessGrant).where(
            AccessGrant.user_id == user.id,
            AccessGrant.scope == AccessGrantScope.PILOT_FULL_ACCESS,
            AccessGrant.revoked_at.is_(None),
        )
    ).scalar_one_or_none()
    if existing is not None:
        return AccessGrantOut.model_validate(existing)
    grant = AccessGrant(
        user_id=user.id,
        scope=AccessGrantScope.PILOT_FULL_ACCESS,
        granted_by_user_id=admin.id,
        granted_at=utc_now(),
    )
    db.add(grant)
    record_event(
        db,
        AuditAction.PILOT_ACCESS_GRANTED,
        actor=admin,
        subject=user,
        details="scope=pilot_full_access",
        commit=False,
    )
    db.commit()
    db.refresh(grant)
    return AccessGrantOut.model_validate(grant)
