"""Authentication endpoints: login, logout, current user.

Brute-force defence is two-layered:

1. **Per-IP rate limit** (in-memory sliding window, :mod:`app.rate_limiting`)
   caps the number of login attempts a single client can make — floods get
   HTTP 429 before any password check.
2. **Per-account lockout** (persisted on ``users.failed_login_count`` /
   ``locked_until``): after ``LOGIN_MAX_FAILURES`` consecutive failures the
   account is locked for ``LOGIN_LOCK_MINUTES`` — HTTP 423. This survives
   restarts and protects even against distributed attempts.

Successful login resets the failure counter and issues a server-side session
plus a bound CSRF token.
"""

import logging
from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import JSONResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.audit import record_event
from app.config import Settings
from app.db import get_db
from app.deps import (
    clear_session_cookies,
    get_current_session,
    get_current_user,
    get_settings_from_request,
    set_session_cookies,
)
from app.models import AuditAction, User, UserSession
from app.rate_limiting import SlidingWindowRateLimiter
from app.schemas import CurrentUserOut, LoginRequest, UserOut
from app.security import generate_csrf_token, hash_password, needs_rehash, verify_password
from app.utils import client_ip, ensure_aware, user_agent, utc_now

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/auth", tags=["auth"])

# Process-local login limiter; constructed lazily from settings so tests get a
# clean limiter when they build a fresh app.
_login_limiter: SlidingWindowRateLimiter | None = None


def _get_limiter(settings: Settings) -> SlidingWindowRateLimiter:
    global _login_limiter
    if _login_limiter is None:
        _login_limiter = SlidingWindowRateLimiter(
            limit=settings.login_rate_limit,
            window_seconds=settings.login_rate_window_seconds,
        )
    return _login_limiter


def reset_login_limiter() -> None:
    """Reset the process login limiter (used between tests)."""
    global _login_limiter
    if _login_limiter is not None:
        _login_limiter.reset()


def _find_user_by_username(db: Session, username: str) -> User | None:
    """Case-insensitive username lookup."""
    return db.scalar(select(User).where(func.lower(User.username) == username.lower()))


@router.post(
    "/login",
    response_model=CurrentUserOut,
    summary="Log in with username and password",
    status_code=status.HTTP_200_OK,
)
def login(
    payload: LoginRequest,
    request: Request,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings_from_request),
) -> JSONResponse:
    """Authenticate and create a server-side session."""
    ip = client_ip(request)
    ua = user_agent(request.headers)

    limiter = _get_limiter(settings)
    limit_result = limiter.check(ip or "unknown")
    if not limit_result.allowed:
        # Do not reveal whether the credentials were right.
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Слишком много попыток входа. Повторите позже.",
            headers={"Retry-After": str(limit_result.retry_after_seconds)},
        )

    user = _find_user_by_username(db, payload.username)
    now = utc_now()

    if user is None:
        # Uniform error for unknown user / wrong password (no user enumeration).
        record_event(
            db,
            AuditAction.LOGIN_FAILURE,
            username=payload.username,
            ip_address=ip,
            user_agent=ua,
            details="unknown username",
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Неверное имя пользователя или пароль.",
        )

    if not user.is_active:
        record_event(
            db,
            AuditAction.LOGIN_FAILURE,
            actor=user,
            subject=user,
            username=user.username,
            ip_address=ip,
            user_agent=ua,
            details="inactive account login attempt",
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Учётная запись отключена. Обратитесь к администратору.",
        )

    if user.locked_until is not None and ensure_aware(user.locked_until) > now:
        raise HTTPException(
            status_code=status.HTTP_423_LOCKED,
            detail="Учётная запись временно заблокирована после неудачных попыток входа. "
            "Повторите позже или обратитесь к администратору.",
        )

    if not verify_password(user.password_hash, payload.password):
        user.failed_login_count += 1
        locked = False
        if user.failed_login_count >= settings.login_max_failures:
            user.locked_until = now + timedelta(minutes=settings.login_lock_minutes)
            user.failed_login_count = 0
            locked = True
        db.commit()
        record_event(
            db,
            AuditAction.ACCOUNT_LOCKED if locked else AuditAction.LOGIN_FAILURE,
            actor=user,
            subject=user,
            username=user.username,
            ip_address=ip,
            user_agent=ua,
            details=(
                f"account locked for {settings.login_lock_minutes} minutes after repeated "
                "failed logins"
                if locked
                else f"failed login, consecutive failures = {user.failed_login_count}"
            ),
        )
        if locked:
            raise HTTPException(
                status_code=status.HTTP_423_LOCKED,
                detail="Учётная запись временно заблокирована после нескольких неудачных "
                "попыток входа. Повторите позже или обратитесь к администратору.",
            )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Неверное имя пользователя или пароль.",
        )

    # Successful authentication: reset counters, rehash if parameters changed.
    user.failed_login_count = 0
    user.locked_until = None
    user.last_login_at = now
    if needs_rehash(user.password_hash):
        user.password_hash = hash_password(payload.password)

    user_session = UserSession(
        user_id=user.id,
        csrf_token=generate_csrf_token(),
        ip_address=ip,
        user_agent=ua,
        expires_at=now + timedelta(minutes=settings.session_ttl_minutes),
        last_seen_at=now,
    )
    db.add(user_session)
    db.commit()
    db.refresh(user_session)
    record_event(
        db,
        AuditAction.LOGIN_SUCCESS,
        actor=user,
        subject=user,
        username=user.username,
        ip_address=ip,
        user_agent=ua,
    )

    payload_out = CurrentUserOut(
        user=UserOut.model_validate(user), csrf_token=user_session.csrf_token
    )
    # Build the response explicitly so the Set-Cookie headers are attached to
    # the same object that travels back through the middleware stack.
    response = JSONResponse(content=payload_out.model_dump(mode="json"))
    set_session_cookies(response, user_session, settings)
    return response


@router.post(
    "/logout",
    summary="Log out (revoke the current session)",
    status_code=status.HTTP_200_OK,
)
def logout(
    request: Request,
    user_session: UserSession = Depends(get_current_session),
    db: Session = Depends(get_db),
) -> JSONResponse:
    """Revoke the current session server-side and clear cookies."""
    user = db.get(User, user_session.user_id)
    user_session.revoked_at = utc_now()
    db.commit()
    if user is not None:
        record_event(
            db,
            AuditAction.LOGOUT,
            actor=user,
            subject=user,
            username=user.username,
            ip_address=client_ip(request),
            user_agent=user_agent(request.headers),
        )
    # Build the response explicitly so the cookie-deletion headers are set on
    # the same object that travels back through the middleware stack.
    response = JSONResponse(content={"status": "ok"})
    clear_session_cookies(response)
    return response


@router.get(
    "/me",
    response_model=CurrentUserOut,
    summary="Current authenticated user",
)
def me(
    user_session: UserSession = Depends(get_current_session),
    current_user: User = Depends(get_current_user),
) -> CurrentUserOut:
    """Return the current user and the session CSRF token."""
    return CurrentUserOut(
        user=UserOut.model_validate(current_user), csrf_token=user_session.csrf_token
    )


def create_session_for_user(
    db: Session, user: User, *, settings: Settings, ip: str | None, ua: str | None
) -> UserSession:
    """Create a session directly (used by tests and internal flows)."""
    now = utc_now()
    user_session = UserSession(
        user_id=user.id,
        csrf_token=generate_csrf_token(),
        ip_address=ip,
        user_agent=ua,
        expires_at=now + timedelta(minutes=settings.session_ttl_minutes),
        last_seen_at=now,
    )
    db.add(user_session)
    db.commit()
    db.refresh(user_session)
    return user_session


__all__ = ["create_session_for_user", "reset_login_limiter", "router"]
