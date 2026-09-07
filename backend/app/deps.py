"""FastAPI dependencies for authentication, CSRF protection and authorization.

Authentication model
--------------------
* The browser holds two same-site cookies:
    - ``hrm_session`` — HttpOnly, SameSite=Lax, Secure (in production); its
      value is the server-side session UUID (122 bits of entropy);
    - ``hrm_csrf``    — readable by JavaScript; double-submit CSRF token
      bound to the session.
* Every state-changing request on an authenticated endpoint must send the
  header ``X-CSRF-Token`` equal to both the CSRF cookie and the token stored
  server-side on the session.
* Sessions live in the database, so logout and expiry revoke them instantly.
"""

import secrets
import uuid
from collections.abc import Callable
from datetime import timedelta

from fastapi import Depends, HTTPException, Request, Response, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings
from app.db import get_db
from app.models import User, UserRole, UserSession
from app.utils import ensure_aware, utc_now

SESSION_COOKIE_NAME = "hrm_session"
CSRF_COOKIE_NAME = "hrm_csrf"
CSRF_HEADER_NAME = "x-csrf-token"

# Methods that mutate state and therefore require a valid CSRF token.
_CSRF_PROTECTED_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})

# How often the sliding session expiry is persisted (avoids a write per
# request while keeping idle timeouts accurate).
_SESSION_REFRESH_INTERVAL = timedelta(minutes=1)


def get_settings_from_request(request: Request) -> Settings:
    """Return the application settings stored on the app state."""
    settings: Settings = request.app.state.settings
    return settings


def _client_error(detail: str, code: int = status.HTTP_401_UNAUTHORIZED) -> HTTPException:
    error = HTTPException(status_code=code, detail=detail)
    if code == status.HTTP_401_UNAUTHORIZED:
        # Browsers handle our cookie-based auth automatically; this header is
        # still set so non-browser API clients get a standard challenge hint.
        error.headers = {"WWW-Authenticate": "Cookie"}
    return error


def get_current_session(
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings_from_request),
) -> UserSession:
    """Resolve and validate the current session cookie.

    Raises 401 when the cookie is missing/malformed, the session does not
    exist, is revoked/expired, or the owning user is inactive. Enforces CSRF
    on state-changing methods.
    """
    cookie_value = request.cookies.get(SESSION_COOKIE_NAME)
    if not cookie_value:
        raise _client_error("Требуется вход в систему.")
    try:
        session_id = uuid.UUID(cookie_value)
    except ValueError:
        raise _client_error("Недействительная сессия.") from None

    user_session = db.get(UserSession, session_id)
    if user_session is None:
        raise _client_error("Сессия не найдена или завершена.")
    if user_session.revoked_at is not None:
        raise _client_error("Сессия завершена.")
    if ensure_aware(user_session.expires_at) <= utc_now():
        raise _client_error("Сессия истекла. Войдите снова.")

    user = db.get(User, user_session.user_id)
    if user is None or not user.is_active:
        raise _client_error("Учётная запись недоступна.")

    if request.method in _CSRF_PROTECTED_METHODS:
        _enforce_csrf(request, user_session)

    _slide_session(db, user_session, response, settings)
    return user_session


def _enforce_csrf(request: Request, user_session: UserSession) -> None:
    """Double-submit CSRF check: header must match cookie and stored token."""
    header_token = request.headers.get(CSRF_HEADER_NAME, "")
    cookie_token = request.cookies.get(CSRF_COOKIE_NAME, "")
    expected = user_session.csrf_token
    if not header_token or not cookie_token:
        raise _client_error("Отсутствует CSRF-токен.", status.HTTP_403_FORBIDDEN)
    # Constant-time comparison of equal-length secrets.
    if not (
        len(header_token) == len(expected)
        and secrets.compare_digest(header_token, expected)
        and len(cookie_token) == len(expected)
        and secrets.compare_digest(cookie_token, expected)
    ):
        raise _client_error("Недействительный CSRF-токен.", status.HTTP_403_FORBIDDEN)


def _slide_session(
    db: Session, user_session: UserSession, response: Response, settings: Settings
) -> None:
    """Extend the sliding session expiry and refresh the cookie."""
    now = utc_now()
    new_expiry = now + timedelta(minutes=settings.session_ttl_minutes)
    last_seen = ensure_aware(user_session.last_seen_at)
    if now - last_seen >= _SESSION_REFRESH_INTERVAL:
        user_session.last_seen_at = now
        user_session.expires_at = new_expiry
        db.commit()
    _set_session_cookies(
        response,
        session_id=str(user_session.id),
        csrf_token=user_session.csrf_token,
        settings=settings,
    )


def get_current_user(
    session: UserSession = Depends(get_current_session),
    db: Session = Depends(get_db),
) -> User:
    """Return the authenticated user for the current session."""
    user = db.scalar(select(User).where(User.id == session.user_id))
    if user is None or not user.is_active:
        raise _client_error("Учётная запись недоступна.")
    return user


def require_roles(*roles: UserRole) -> Callable[..., User]:
    """Dependency factory: allow only the given roles (server-side check)."""

    allowed = set(roles)

    def _checker(current_user: User = Depends(get_current_user)) -> User:
        if current_user.role not in allowed:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Недостаточно прав для этого действия.",
            )
        return current_user

    return _checker


def set_session_cookies(response: Response, user_session: UserSession, settings: Settings) -> None:
    """Attach session and CSRF cookies to a login response."""
    _set_session_cookies(
        response,
        session_id=str(user_session.id),
        csrf_token=user_session.csrf_token,
        settings=settings,
    )


def _set_session_cookies(
    response: Response, *, session_id: str, csrf_token: str, settings: Settings
) -> None:
    secure = settings.session_cookie_is_secure
    response.set_cookie(
        key=SESSION_COOKIE_NAME,
        value=session_id,
        max_age=settings.session_ttl_minutes * 60,
        httponly=True,
        secure=secure,
        samesite="lax",
        path="/",
    )
    # The CSRF cookie is intentionally readable by JavaScript (double-submit
    # pattern); it is not a secret on its own and is useless without the
    # HttpOnly session cookie.
    response.set_cookie(
        key=CSRF_COOKIE_NAME,
        value=csrf_token,
        max_age=settings.session_ttl_minutes * 60,
        httponly=False,
        secure=secure,
        samesite="lax",
        path="/",
    )


def clear_session_cookies(response: Response) -> None:
    """Remove session/CSRF cookies on logout."""
    response.delete_cookie(SESSION_COOKIE_NAME, path="/")
    response.delete_cookie(CSRF_COOKIE_NAME, path="/")
