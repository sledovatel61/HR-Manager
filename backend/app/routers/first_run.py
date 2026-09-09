"""First-run pairing for the Windows local pilot (phase 12).

The installer (automation engine, never the user) issues a short-lived
pairing code through ``python -m app.cli pilot-pairing issue`` — the code
travels in via the container's stdin and only its SHA-256 digest is stored.
The user types the code in the browser once; on a successful claim this
router, atomically and only while the user table is still empty:

* creates the single pilot owner (role ``admin`` + explicit
  ``pilot_full_access``, exactly like the admin setup wizard endpoint),
* stamps the chosen working mode (``work_role`` — a UI label, NOT RBAC),
* issues a server-side session directly (the generated password is random,
  never returned anywhere, and the account is flagged ``password_is_bootstrap``
  until the owner sets their own password in this same UI).

Safety properties proven by tests: the code is one-shot and race-safe
(FOR UPDATE on the single pending row), rate-limited per IP and per attempt
count, expires after 15 minutes, only works over a loopback Host with a
same-origin browser request, and can never create a second owner once any
user exists. There is no unauthenticated admin mutation anywhere here.
"""

from __future__ import annotations

import hashlib
import re
import secrets
from datetime import timedelta
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import JSONResponse
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.audit import record_event
from app.config import Settings
from app.db import get_db
from app.deps import get_current_user, get_settings_from_request, set_session_cookies
from app.host_guard import is_loopback_host
from app.models import (
    AccessGrant,
    AccessGrantScope,
    AuditAction,
    PilotPairing,
    PilotPairingStatus,
    User,
    UserRole,
    UserSession,
    WorkRole,
)
from app.rate_limiting import SlidingWindowRateLimiter
from app.schemas import (
    FirstRunClaimOut,
    FirstRunClaimRequest,
    FirstRunPasswordSetRequest,
    FirstRunStateOut,
    UserOut,
)
from app.security import generate_csrf_token, hash_password, validate_password_policy
from app.utils import client_ip, ensure_aware, romanize_full_name, utc_now

router = APIRouter(prefix="/setup/first-run", tags=["setup"])

# Per-pairing attempt budget and the fixed window for the public claim
# endpoint (mirrors the login limiter's shape; values documented in README).
MAX_CLAIM_ATTEMPTS = 8
CLAIM_RATE_LIMIT = 10
CLAIM_RATE_WINDOW_S = 900

_CODE_PATTERN = re.compile(r"^[A-Z0-9]{6,8}$")

_claim_limiter: SlidingWindowRateLimiter | None = None


def _get_claim_limiter() -> SlidingWindowRateLimiter:
    global _claim_limiter
    if _claim_limiter is None:
        _claim_limiter = SlidingWindowRateLimiter(
            limit=CLAIM_RATE_LIMIT, window_seconds=CLAIM_RATE_WINDOW_S
        )
    return _claim_limiter


def reset_first_run_limiters() -> None:
    """Reset the process claim limiter (used between tests)."""
    global _claim_limiter
    if _claim_limiter is not None:
        _claim_limiter.reset()


def hash_pairing_code(code: str) -> str:
    """Normalising hash of a pairing code (upper-case, SHA-256 hex)."""
    return hashlib.sha256(code.strip().upper().encode("utf-8")).hexdigest()


def _same_origin_or_reject(request: Request) -> None:
    """Reject cross-site claims (drive-by CSRF) and non-loopback Hosts.

    The browser always sends ``Origin`` for POSTs; it must equal this
    request's host, and that host must be loopback. curl-style clients
    without Origin can only reach the endpoint from the machine itself
    (loopback Host), which is the installer's documented test path.
    """
    if not is_loopback_host(request.headers.get("host")):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                "Первый вход доступен только с этого компьютера (адрес 127.0.0.1). "
                "Откройте http://127.0.0.1 из установщика."
            ),
        )
    origin = request.headers.get("origin")
    if origin:
        origin_parts = urlsplit(origin)
        origin_host = (origin_parts.netloc or "").lower()
        request_host = (request.headers.get("host") or "").lower()
        if origin_parts.scheme not in ("http", "https") or origin_host != request_host:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Запрос первого входа должен поступать из того же источника.",
            )


@router.get(
    "/state",
    response_model=FirstRunStateOut,
    summary="Public first-run state (no PII, loopback pilot)",
)
def first_run_state(
    db: Session = Depends(get_db),
) -> FirstRunStateOut:
    pending = _pending_pairing(db)
    if pending is not None and ensure_aware(pending.expires_at) <= utc_now():
        _expire_pending(db, pending)
        pending = None
    pilot_exists = (
        db.scalar(
            select(func.count())
            .select_from(AccessGrant)
            .where(
                AccessGrant.scope == AccessGrantScope.PILOT_FULL_ACCESS,
                AccessGrant.revoked_at.is_(None),
            )
        )
        or 0
    )
    users = db.scalar(select(func.count()).select_from(User)) or 0
    expires_in: int | None = None
    if pending is not None:
        expires_in = max(0, int((ensure_aware(pending.expires_at) - utc_now()).total_seconds()))
    return FirstRunStateOut(
        pending=pending is not None,
        pending_work_role=pending.work_role.value if pending is not None else None,
        pending_expires_in_seconds=expires_in,
        fresh_install=users == 0,
        pilot_owner_exists=pilot_exists > 0,
    )


@router.post(
    "/claim",
    response_model=FirstRunClaimOut,
    summary="Claim the freshly installed pilot with the pairing code",
)
def claim_first_run(
    payload: FirstRunClaimRequest,
    request: Request,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings_from_request),
) -> JSONResponse:
    _same_origin_or_reject(request)

    ip = client_ip(request) or "unknown"
    limiter = _get_claim_limiter()
    limit_result = limiter.check(ip)
    if not limit_result.allowed:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Слишком много попыток подтверждения. Повторите позже.",
            headers={"Retry-After": str(limit_result.retry_after_seconds)},
        )

    # Lock the single pending row so concurrent claims serialize (PostgreSQL
    # FOR UPDATE; SQLite unit tests are single-threaded by construction).
    pending = db.execute(
        select(PilotPairing)
        .where(PilotPairing.status == PilotPairingStatus.PENDING)
        .with_for_update()
    ).scalar_one_or_none()
    if pending is None:
        raise HTTPException(
            status_code=status.HTTP_410_GONE,
            detail="Активной установки не найдено. Запустите «HR Manager Setup.exe» ещё раз.",
        )
    if ensure_aware(pending.expires_at) <= utc_now():
        _expire_pending(db, pending)
        raise HTTPException(
            status_code=status.HTTP_410_GONE,
            detail="Код подтверждения истёк. Запустите «HR Manager Setup.exe» ещё раз.",
        )
    users = db.scalar(select(func.count()).select_from(User)) or 0
    if users > 0:
        # A second owner must never appear through this flow — full stop.
        record_event(
            db,
            AuditAction.PILOT_PAIRING_REJECTED,
            details="claim refused: installation already has users",
            ip_address=ip,
        )
        db.commit()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Установка уже имеет владельца; захват невозможен.",
        )
    if pending.attempts >= MAX_CLAIM_ATTEMPTS:
        pending.status = PilotPairingStatus.CANCELLED
        record_event(
            db,
            AuditAction.PILOT_PAIRING_REJECTED,
            details="pairing cancelled after too many wrong codes",
            ip_address=ip,
        )
        db.commit()
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Слишком много неверных попыток. Запустите установщик повторно для нового кода.",
        )

    code = payload.code.strip().upper()
    if not _CODE_PATTERN.fullmatch(code) or not secrets.compare_digest(
        hash_pairing_code(code), pending.code_hash
    ):
        pending.attempts += 1
        db.commit()
        # Uniform message: never reveal whether a pairing exists or matched.
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Неверный код подтверждения.",
        )

    # --- exactly-once claim: create the single pilot owner --------------------
    now = utc_now()
    suffix = secrets.token_hex(2)
    username = f"{romanize_full_name(pending.surname)}-pilot-{suffix}"[:64]
    # The generated password is random and never leaves this process: the
    # browser gets a session directly; the owner sets a real password in the
    # protected first-run UI (PUT /setup/first-run/password).
    generated_password = secrets.token_urlsafe(32)
    pilot = User(
        username=username,
        full_name=pending.surname.strip(),
        role=UserRole.ADMIN,
        work_role=WorkRole(pending.work_role),
        password_hash=hash_password(generated_password),
        password_is_bootstrap=True,
        is_active=True,
        created_at=now,
        updated_at=now,
    )
    db.add(pilot)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Не удалось создать владельца установки. Повторите попытку.",
        ) from None
    db.add(
        AccessGrant(
            user_id=pilot.id,
            scope=AccessGrantScope.PILOT_FULL_ACCESS,
            granted_at=now,
        )
    )
    session = UserSession(
        user_id=pilot.id,
        csrf_token=generate_csrf_token(),
        ip_address=ip[:64],
        user_agent=None,
        created_at=now,
        last_seen_at=now,
        expires_at=now + timedelta(minutes=settings.session_ttl_minutes),
    )
    db.add(session)
    pending.status = PilotPairingStatus.CLAIMED
    pending.claimed_at = now
    pending.claimed_user_id = pilot.id
    # Erase the pairing's only personal value: the surname now lives solely
    # on the user profile (full_name), as everywhere else in the product.
    pending.surname = ""
    record_event(
        db,
        AuditAction.PILOT_PAIRING_CLAIMED,
        actor=pilot,
        subject=pilot,
        details=f"first-run claimed for {pilot.username}",
        ip_address=ip,
        commit=False,
    )
    record_event(
        db,
        AuditAction.PILOT_USER_CREATED,
        actor=pilot,
        subject=pilot,
        details=f"pilot account {pilot.username}",
        commit=False,
    )
    record_event(
        db,
        AuditAction.PILOT_ACCESS_GRANTED,
        actor=pilot,
        subject=pilot,
        details="scope=pilot_full_access",
        commit=False,
    )
    db.commit()
    db.refresh(pilot)

    body = FirstRunClaimOut(
        user=UserOut.model_validate(pilot),
        csrf_token=session.csrf_token,
        must_set_password=True,
    )
    response = JSONResponse(status_code=status.HTTP_200_OK, content=body.model_dump(mode="json"))
    set_session_cookies(response, session, settings)
    return response


def require_bootstrap_pilot(current_user: User = Depends(get_current_user)) -> User:
    """Only the just-claimed owner may use this endpoint (fail closed)."""
    if not current_user.password_is_bootstrap:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Смена пароля доступна только на шаге завершения установки.",
        )
    return current_user


@router.put(
    "/password",
    response_model=UserOut,
    summary="Owner sets a permanent password (first-run only)",
)
def set_pilot_password(
    payload: FirstRunPasswordSetRequest,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_bootstrap_pilot),
) -> UserOut:
    """Replace the never-shown generated password with the owner's own.

    Available ONLY while ``password_is_bootstrap`` is set — afterwards this
    endpoint is closed and the ordinary admin password flow applies. CSRF is
    enforced by the session dependency; the password is hashed (Argon2id) and
    never logged; audit carries no password data.
    """
    try:
        validate_password_policy(payload.password, username=current_user.username)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc
    current_user.password_hash = hash_password(payload.password)
    current_user.password_is_bootstrap = False
    current_user.updated_at = utc_now()
    record_event(
        db,
        AuditAction.PILOT_PASSWORD_SET,
        actor=current_user,
        subject=current_user,
        details="owner replaced the bootstrap password",
        ip_address=client_ip(request),
    )
    db.commit()
    db.refresh(current_user)
    return UserOut.model_validate(current_user)


def _pending_pairing(db: Session) -> PilotPairing | None:
    return db.execute(
        select(PilotPairing)
        .where(PilotPairing.status == PilotPairingStatus.PENDING)
        .order_by(PilotPairing.created_at.desc())
    ).scalar_one_or_none()


def _expire_pending(db: Session, pending: PilotPairing) -> None:
    pending.status = PilotPairingStatus.CANCELLED
    record_event(db, AuditAction.PILOT_PAIRING_CANCELLED, details="pairing expired")
    db.commit()
