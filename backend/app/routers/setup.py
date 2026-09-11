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

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.audit import record_event
from app.db import get_db
from app.deps import get_current_user, require_roles, set_session_cookies
from app.models import (
    AccessGrant,
    AccessGrantScope,
    AuditAction,
    NotificationPreference,
    User,
    UserRole,
)
from app.rate_limiting import SlidingWindowRateLimiter
from app.schemas import (
    AccessGrantList,
    AccessGrantOut,
    AccessGrantRequest,
    CurrentUserOut,
    PilotCreateOut,
    PilotCreateRequest,
    SetupOwnerClaimRequest,
    SetupOwnerClaimResponse,
    SetupOwnerPreviewRequest,
    SetupOwnerPreviewResponse,
    SetupOwnerRedeemRequest,
    SetupStateOut,
    UserOut,
)
from app.security import hash_password
from app.utils import client_ip, user_agent, utc_now
from app.worker import worker_is_healthy

router = APIRouter(tags=["setup"])

_admin_only = require_roles(UserRole.ADMIN)

# Process-local rate limiter for the first-run endpoints (constructed lazily
# from settings; rebuilt when a test injects a different limit so per-test
# overrides stay deterministic).
_first_run_limiter: SlidingWindowRateLimiter | None = None
_first_run_limiter_key: tuple[int, int] | None = None


def _get_first_run_limiter(settings: object) -> SlidingWindowRateLimiter:
    from app.config import Settings

    assert isinstance(settings, Settings)
    global _first_run_limiter, _first_run_limiter_key
    key = (settings.pilot_setup_rate_limit, settings.pilot_setup_rate_window_seconds)
    if _first_run_limiter is None or _first_run_limiter_key != key:
        _first_run_limiter = SlidingWindowRateLimiter(limit=key[0], window_seconds=key[1])
        _first_run_limiter_key = key
    return _first_run_limiter


def reset_first_run_limiter() -> None:
    """Reset the process-global first-run limiter (used between tests)."""
    global _first_run_limiter
    if _first_run_limiter is not None:
        _first_run_limiter.reset()


def _first_run_rate_check(request: Request) -> None:
    from app.config import Settings

    settings = request.app.state.settings
    assert isinstance(settings, Settings)
    ip = client_ip(request) or "unknown"
    result = _get_first_run_limiter(settings).check(ip)
    if not result.allowed:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Слишком много попыток. Повторите позже.",
            headers={"Retry-After": str(result.retry_after_seconds)},
        )


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
    from app.smtp import config_from_settings as smtp_config_from_settings
    from app.telegram import config_from_settings as telegram_config_from_settings

    settings = request.app.state.settings
    pilot_grant = _active_pilot_grant(db)
    preferences = db.get(NotificationPreference, user.id)
    telegram_ready = telegram_config_from_settings(settings).is_configured
    smtp_ready = smtp_config_from_settings(settings).is_configured
    return SetupStateOut(
        pilot_exists=pilot_grant is not None,
        pilot_grant_active=pilot_grant is not None,
        preferences_initialized=preferences is not None,
        worker_alive=worker_is_healthy(db, settings=settings, now=utc_now()),
        working_mode=user.working_mode,
        channels={
            # Global availability only (per-user bindings live in
            # /integrations/status); «available» never claims the user's
            # own channel works.
            "telegram": "available" if telegram_ready else "not_configured",
            "email": "available" if smtp_ready else "not_configured",
        },
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
                AccessGrant.scope == AccessGrantScope(payload.scope),
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
            details=f"scope={payload.scope}",
            commit=False,
        )
        db.commit()
        db.refresh(active)
        return AccessGrantOut.model_validate(active)
    existing = db.execute(
        select(AccessGrant).where(
            AccessGrant.user_id == user.id,
            AccessGrant.scope == AccessGrantScope(payload.scope),
            AccessGrant.revoked_at.is_(None),
        )
    ).scalar_one_or_none()
    if existing is not None:
        return AccessGrantOut.model_validate(existing)
    grant = AccessGrant(
        user_id=user.id,
        scope=AccessGrantScope(payload.scope),
        granted_by_user_id=admin.id,
        granted_at=utc_now(),
    )
    db.add(grant)
    record_event(
        db,
        AuditAction.PILOT_ACCESS_GRANTED,
        actor=admin,
        subject=user,
        details=f"scope={payload.scope}",
        commit=False,
    )
    db.commit()
    db.refresh(grant)
    return AccessGrantOut.model_validate(grant)


# --- Phase 12: local pilot first-run (one-time exchange) ----------------------


@router.post(
    "/setup/owner/claim",
    response_model=SetupOwnerClaimResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Claim the one-time first-run exchange (loopback installer only)",
)
def claim_pilot_owner(
    payload: SetupOwnerClaimRequest,
    request: Request,
    db: Session = Depends(get_db),
) -> SetupOwnerClaimResponse:
    """The installer engine claims the one-time exchange token over loopback
    and receives a short-lived ticket for the browser. Single-use, TTL-
    bound, loopback-only; the raw token is never persisted (SHA-256 only)."""
    from app.setup_owner import claim_owner, loopback_client_ok

    settings = request.app.state.settings
    if not loopback_client_ok(request, settings):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Первоначальная настройка недоступна."
        )
    _first_run_rate_check(request)
    ticket, expires_at = claim_owner(
        db,
        settings,
        token=payload.exchange_token,
        surname=payload.surname,
        working_mode=payload.working_mode,
        timezone=payload.timezone,
        client_ip=client_ip(request),
    )
    return SetupOwnerClaimResponse(ticket=ticket, expires_at=expires_at)


@router.post(
    "/setup/owner/preview",
    response_model=SetupOwnerPreviewResponse,
    summary="First-run screen data (surname, mode, readiness; loopback only)",
)
def preview_pilot_owner(
    payload: SetupOwnerPreviewRequest,
    request: Request,
    db: Session = Depends(get_db),
) -> SetupOwnerPreviewResponse:
    """What the browser first-run screen shows: the surname and working mode
    collected during installation, plus backend/db/worker/backup readiness.
    The ticket is not consumed here — only redeem consumes it."""
    from app.setup_owner import loopback_client_ok, preview_owner

    settings = request.app.state.settings
    if not loopback_client_ok(request, settings):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Первоначальная настройка недоступна."
        )
    _first_run_rate_check(request)
    data = preview_owner(db, settings, ticket=payload.ticket)
    return SetupOwnerPreviewResponse(**data)


@router.post(
    "/setup/owner/redeem",
    response_model=CurrentUserOut,
    summary="Complete the first run: create the single pilot owner (loopback only)",
)
def redeem_pilot_owner(
    payload: SetupOwnerRedeemRequest,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
) -> CurrentUserOut:
    """Atomically creates the single pilot owner (server role admin +
    explicit pilot_full_access grant), applies the chosen preferences and
    establishes an authenticated session. The password is chosen here, in a
    protected UI. The ticket is single-use; after completion the bootstrap
    flow is permanently closed. CSRF applies to every later mutation through
    the regular session/CSRF cookies."""
    from app.setup_owner import loopback_client_ok, redeem_owner

    settings = request.app.state.settings
    if not loopback_client_ok(request, settings):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Первоначальная настройка недоступна."
        )
    _first_run_rate_check(request)
    user, user_session = redeem_owner(
        db,
        settings,
        ticket=payload.ticket,
        password=payload.password,
        timezone=payload.timezone,
        workdays=payload.workdays,
        quiet_hours_start=payload.quiet_hours_start,
        quiet_hours_end=payload.quiet_hours_end,
        client_ip=client_ip(request),
        user_agent=user_agent(request.headers),
    )
    set_session_cookies(response, user_session, settings)
    return CurrentUserOut(
        user=UserOut.model_validate(user),
        csrf_token=user_session.csrf_token,
        working_mode=user.working_mode,
    )
