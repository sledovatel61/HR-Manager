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

import hashlib
import secrets as crypto_secrets
from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.responses import JSONResponse
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.audit import record_event
from app.config import Settings
from app.db import get_db
from app.deps import get_current_user, require_roles, set_session_cookies
from app.models import (
    AccessGrant,
    AccessGrantScope,
    AuditAction,
    NotificationPreference,
    PilotFirstRunClaim,
    User,
    UserRole,
    UserSession,
)
from app.rate_limiting import SlidingWindowRateLimiter
from app.schemas import (
    AccessGrantList,
    AccessGrantOut,
    AccessGrantRequest,
    CurrentUserOut,
    FirstRunClaimRequest,
    FirstRunStatusOut,
    PilotCreateOut,
    PilotCreateRequest,
    PilotPasswordSetRequest,
    SetupStateOut,
    UserOut,
)
from app.security import (
    WeakPasswordError,
    generate_csrf_token,
    hash_password,
    validate_password_policy,
)
from app.utils import client_ip, utc_now
from app.worker import worker_is_healthy

router = APIRouter(tags=["setup"])

_admin_only = require_roles(UserRole.ADMIN)

# Process-local limiter for the one-shot first-run exchange (constructed
# lazily from settings; tests get a clean limiter when they build a fresh app).
_first_run_limiter: SlidingWindowRateLimiter | None = None


def _get_first_run_limiter(settings: Settings) -> SlidingWindowRateLimiter:
    global _first_run_limiter
    if _first_run_limiter is None:
        _first_run_limiter = SlidingWindowRateLimiter(
            limit=settings.first_run_rate_limit,
            window_seconds=settings.first_run_rate_window_s,
        )
    return _first_run_limiter


def reset_first_run_limiter() -> None:
    """Reset the process first-run limiter (used between tests)."""
    global _first_run_limiter
    if _first_run_limiter is not None:
        _first_run_limiter.reset()


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


# --- Phase 12: local pilot first-run exchange ---------------------------------

_TRANSLIT = {
    "а": "a",
    "б": "b",
    "в": "v",
    "г": "g",
    "д": "d",
    "е": "e",
    "ё": "e",
    "ж": "zh",
    "з": "z",
    "и": "i",
    "й": "y",
    "к": "k",
    "л": "l",
    "м": "m",
    "н": "n",
    "о": "o",
    "п": "p",
    "р": "r",
    "с": "s",
    "т": "t",
    "у": "u",
    "ф": "f",
    "х": "kh",
    "ц": "ts",
    "ч": "ch",
    "ш": "sh",
    "щ": "sch",
    "ъ": "",
    "ы": "y",
    "ь": "",
    "э": "e",
    "ю": "yu",
    "я": "ya",
}


def _derive_pilot_username(surname: str) -> str:
    """Deterministic, safe technical username from the owner's surname.

    The user never has to type this value for the first login (the one-shot
    exchange authenticates them); it only needs to be stable and unique.
    """
    lowered = surname.lower()
    transliterated = "".join(_TRANSLIT.get(ch, ch) for ch in lowered)
    cleaned = "".join(ch if ch.isalnum() else "-" for ch in transliterated)
    cleaned = "-".join(part for part in cleaned.split("-") if part)
    base = cleaned.strip("-")[:40] or "pilot"
    return f"pilot-{base}"[:60]


def _is_loopback(ip: str | None) -> bool:
    """True for loopback client addresses (the only callers the first-run
    endpoint accepts). 127.0.0.0/8 and ::1."""
    if not ip:
        return False
    return ip == "::1" or ip.startswith("127.")


@router.get(
    "/setup/first-run/status",
    response_model=FirstRunStatusOut,
    summary="Whether the one-shot first-run flow is still pending (no PII)",
)
def first_run_status(
    request: Request,
    db: Session = Depends(get_db),
) -> FirstRunStatusOut:
    """Unauthenticated signal used by the installer and the SPA. Reveals only
    whether the pilot owner has not been created yet (``pending``) or exists
    but has not set a password (``needs_password``) — no token, surname or
    working mode."""
    settings = request.app.state.settings
    if not settings.first_run_token:
        return FirstRunStatusOut(pending=False)
    user_count = db.scalar(select(func.count()).select_from(User))
    if user_count == 0:
        return FirstRunStatusOut(pending=True)
    needs_password = db.scalar(
        select(func.count())
        .select_from(User)
        .join(AccessGrant, AccessGrant.user_id == User.id)
        .where(
            AccessGrant.scope == AccessGrantScope.PILOT_FULL_ACCESS,
            AccessGrant.revoked_at.is_(None),
            User.password_change_required.is_(True),
        )
    )
    return FirstRunStatusOut(pending=False, needs_password=bool(needs_password))


@router.post(
    "/setup/first-run",
    response_model=CurrentUserOut,
    summary="One-shot first-run claim (loopback + single-use exchange token)",
)
def claim_first_run(
    payload: FirstRunClaimRequest,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
) -> JSONResponse:
    """Create the single pilot owner and log the browser in, exactly once.

    Security boundaries (phase 12 contract):
    * loopback-only — the pilot frontend is published on 127.0.0.1 and this
      endpoint refuses any non-loopback client;
    * the exchange token is a 256-bit one-shot nonce generated by the
      installer and injected only via the container environment; it is
      compared in constant time, must be used before ``FIRST_RUN_EXPIRES_AT``
      and is consumed atomically (SHA-256 recorded in a unique claim row);
    * the surname and working mode come from the installer-injected
      environment, never from this request and never from a URL;
    * the initial password is a random value the user never sees and the
      account is flagged ``password_change_required`` so the UI asks the
      owner to set their own password right after the first login;
    * rate-limited per client IP; fully audited; replay/takeover impossible.
    """
    settings: Settings = request.app.state.settings
    ip = client_ip(request)

    if not settings.first_run_token:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Первый запуск недоступен."
        )
    if not _is_loopback(ip):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Первый запуск доступен только с этого компьютера.",
        )
    limiter = _get_first_run_limiter(settings)
    if not limiter.check(ip or "unknown").allowed:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Слишком много попыток первого запуска. Подождите и повторите.",
            headers={"Retry-After": "60"},
        )

    expected = settings.first_run_token
    supplied = payload.exchange_token
    token_ok = len(supplied) == len(expected) and crypto_secrets.compare_digest(supplied, expected)
    expiry = settings.first_run_expiry_dt()
    if not token_ok:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Неверный код первого запуска."
        )
    if expiry is None or utc_now() >= expiry:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Код первого запуска истёк. Запустите установку повторно.",
        )

    surname = (settings.pilot_surname or "").strip()
    if not surname or len(surname) > 200:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Не задана фамилия владельца установки. Повторите установку.",
        )
    working_mode = settings.pilot_working_mode
    username = _derive_pilot_username(surname)
    initial_password = crypto_secrets.token_urlsafe(24)
    token_hash = hashlib.sha256(supplied.encode("utf-8")).hexdigest()

    existing_users = db.scalar(select(func.count()).select_from(User))
    created = False
    if existing_users == 0:
        user = User(
            username=username,
            full_name=surname,
            role=UserRole.ADMIN,
            working_mode=working_mode,
            password_change_required=True,
            password_hash=hash_password(initial_password),
            is_active=True,
            created_at=utc_now(),
            updated_at=utc_now(),
        )
        db.add(user)
        try:
            db.flush()
        except IntegrityError:
            db.rollback()
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Первый запуск уже выполнен (повторный запуск не требуется).",
            ) from None
        created = True
    else:
        # Recovery path: the owner already exists but lost their session
        # before setting their own password. The same one-shot exchange
        # re-arms their account with a fresh random password and logs them in
        # again — it never creates a second owner and never touches an
        # account whose password the owner already chose (no takeover).
        owner = db.execute(
            select(User)
            .join(AccessGrant, AccessGrant.user_id == User.id)
            .where(
                AccessGrant.scope == AccessGrantScope.PILOT_FULL_ACCESS,
                AccessGrant.revoked_at.is_(None),
                User.password_change_required.is_(True),
            )
        ).scalar_one_or_none()
        if owner is None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    "Пилотная учётная запись уже настроена (повторный первый запуск не требуется)."
                ),
            )
        owner.password_hash = hash_password(initial_password)
        owner.password_change_required = True
        owner.updated_at = utc_now()
        db.flush()
        user = owner

    claim = PilotFirstRunClaim(token_hash=token_hash, user_id=user.id, claimed_at=utc_now())
    db.add(claim)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Код первого запуска уже использован.",
        ) from None

    if created:
        grant = AccessGrant(
            user_id=user.id,
            scope=AccessGrantScope.PILOT_FULL_ACCESS,
            granted_by_user_id=user.id,
            granted_at=utc_now(),
        )
        db.add(grant)

    user_session = UserSession(
        user_id=user.id,
        csrf_token=generate_csrf_token(),
        ip_address=ip,
        expires_at=utc_now() + timedelta(minutes=settings.session_ttl_minutes),
        last_seen_at=utc_now(),
    )
    db.add(user_session)

    record_event(
        db,
        AuditAction.FIRST_RUN_CLAIMED,
        actor=user,
        subject=user,
        details="first-run token claimed",
        commit=False,
    )
    if created:
        record_event(
            db,
            AuditAction.PILOT_USER_CREATED,
            actor=user,
            subject=user,
            details=f"pilot account {user.username} working_mode={working_mode}",
            commit=False,
        )
        record_event(
            db,
            AuditAction.PILOT_ACCESS_GRANTED,
            actor=user,
            subject=user,
            details="scope=pilot_full_access",
            commit=False,
        )
    record_event(
        db,
        AuditAction.FIRST_RUN_COMPLETED,
        actor=user,
        subject=user,
        details=(
            "pilot owner created via one-shot first-run exchange"
            if created
            else "pilot first-run resumed (password re-armed before owner chose one)"
        ),
        commit=False,
    )
    db.commit()
    db.refresh(user_session)

    payload_out = CurrentUserOut(
        user=UserOut.model_validate(user), csrf_token=user_session.csrf_token
    )
    json_response = JSONResponse(content=payload_out.model_dump(mode="json"))
    set_session_cookies(json_response, user_session, settings)
    return json_response


@router.post(
    "/setup/password",
    response_model=UserOut,
    summary="Set the own password (first run and later password changes)",
)
def set_own_password(
    payload: PilotPasswordSetRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> UserOut:
    """The owner sets their own password in the UI. Clears the forced-change
    flag and audits the change without logging any password material."""
    try:
        validate_password_policy(payload.password, username=current_user.username)
    except WeakPasswordError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from None

    current_user.password_hash = hash_password(payload.password)
    current_user.password_change_required = False
    current_user.updated_at = utc_now()
    record_event(
        db,
        AuditAction.PILOT_PASSWORD_SET,
        actor=current_user,
        subject=current_user,
        details="owner set their own password",
        commit=False,
    )
    db.commit()
    db.refresh(current_user)
    return UserOut.model_validate(current_user)
