"""Unit tests for authentication: login, logout, sessions, CSRF, lockout.

These run against in-memory SQLite (APP_ENV=test). The same scenarios are
repeated against real PostgreSQL in tests/test_integration_auth.py.
"""

import time
from collections.abc import Iterator
from datetime import timedelta

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import AuditAction, AuditEvent, User, UserRole, UserSession
from app.routers.auth import reset_login_limiter
from app.security import verify_password
from app.utils import utc_now
from tests.conftest import FIXTURE_PASSWORD, make_user


@pytest.fixture(autouse=True)
def _clean_limiter() -> Iterator[None]:
    reset_login_limiter()
    yield
    reset_login_limiter()


def _login(client: TestClient, username: str, password: str) -> httpx.Response:
    return client.post("/auth/login", json={"username": username, "password": password})


def test_login_success_returns_user_and_sets_cookies(
    client: TestClient, db_session: Session
) -> None:
    make_user(db_session, username="hr1", role=UserRole.HR)

    response = _login(client, "hr1", FIXTURE_PASSWORD)

    assert response.status_code == 200
    body = response.json()
    assert body["user"]["username"] == "hr1"
    assert body["user"]["role"] == "hr"
    assert "password" not in body["user"]
    assert "password_hash" not in body["user"]
    assert len(body["csrf_token"]) > 20
    # Session and CSRF cookies are set.
    set_cookie = response.headers.get("set-cookie", "")
    assert "hrm_session=" in set_cookie
    assert "HttpOnly" in set_cookie
    assert "hrm_csrf=" in set_cookie

    # A server-side session row exists.
    session = db_session.scalar(select(UserSession))
    assert session is not None
    assert session.revoked_at is None
    assert session.csrf_token == body["csrf_token"]

    # last_login_at is recorded.
    user = db_session.scalar(select(User).where(User.username == "hr1"))
    assert user is not None and user.last_login_at is not None


def test_login_is_case_insensitive_on_username(client: TestClient, db_session: Session) -> None:
    make_user(db_session, username="HR_Manager", role=UserRole.HR)

    response = _login(client, "hr_manager", FIXTURE_PASSWORD)

    assert response.status_code == 200
    assert response.json()["user"]["username"] == "HR_Manager"


def test_login_with_wrong_password_is_unauthorized(client: TestClient, db_session: Session) -> None:
    make_user(db_session, username="hr1", role=UserRole.HR)

    response = _login(client, "hr1", "Definitely-Wrong-123")

    assert response.status_code == 401
    assert response.json()["detail"] == "Неверное имя пользователя или пароль."


def test_login_unknown_user_is_unauthorized_and_does_not_enumerate(
    client: TestClient, db_session: Session
) -> None:
    make_user(db_session, username="hr1", role=UserRole.HR)

    unknown = _login(client, "ghost", FIXTURE_PASSWORD)
    wrong_password = _login(client, "hr1", "Wrong-Password-999")

    assert unknown.status_code == 401
    assert wrong_password.status_code == 401
    # Identical message for both cases -> no user enumeration.
    assert unknown.json()["detail"] == wrong_password.json()["detail"]


def test_failed_logins_lock_account_after_threshold(
    client: TestClient, db_session: Session
) -> None:
    make_user(db_session, username="hr1", role=UserRole.HR)

    # Five wrong passwords -> the fifth triggers the lock (423).
    for _ in range(4):
        assert _login(client, "hr1", "wrong").status_code == 401
    locked = _login(client, "hr1", "wrong")
    assert locked.status_code == 423

    # Even the correct password is refused while locked.
    correct = _login(client, "hr1", FIXTURE_PASSWORD)
    assert correct.status_code == 423

    # Lockout is persisted on the user (SQLite returns naive datetimes; the
    # app's ensure_aware helper is the normalization used in production code).
    from app.utils import ensure_aware

    user = db_session.scalar(select(User).where(User.username == "hr1"))
    assert user is not None
    assert user.locked_until is not None and ensure_aware(user.locked_until) > utc_now()

    # An account_locked audit event exists.
    locked_events = db_session.scalars(
        select(AuditEvent).where(AuditEvent.action == AuditAction.ACCOUNT_LOCKED)
    ).all()
    assert len(locked_events) == 1


def test_expired_lock_allows_login_again(client: TestClient, db_session: Session) -> None:
    user = make_user(db_session, username="hr1", role=UserRole.HR)
    # Simulate a lock that expired one minute ago.
    user.locked_until = utc_now() - timedelta(minutes=1)
    user.failed_login_count = 5
    db_session.commit()

    response = _login(client, "hr1", FIXTURE_PASSWORD)

    assert response.status_code == 200
    db_session.refresh(user)
    assert user.locked_until is None
    assert user.failed_login_count == 0


def test_inactive_user_cannot_login(client: TestClient, db_session: Session) -> None:
    make_user(db_session, username="fired", role=UserRole.HR, is_active=False)

    response = _login(client, "fired", FIXTURE_PASSWORD)

    assert response.status_code == 403
    assert "отключена" in response.json()["detail"]


def test_logout_revokes_session(client: TestClient, db_session: Session) -> None:
    make_user(db_session, username="hr1", role=UserRole.HR)
    login = _login(client, "hr1", FIXTURE_PASSWORD)
    csrf = login.json()["csrf_token"]

    logout = client.post("/auth/logout", headers={"X-CSRF-Token": csrf})
    assert logout.status_code == 200
    # Cookies are cleared (expires in the past).
    set_cookie = logout.headers.get("set-cookie", "")
    assert "hrm_session=" in set_cookie and "Max-Age=0" in set_cookie

    # The session is revoked server-side and no longer accepted.
    me_after = client.get("/auth/me")
    assert me_after.status_code == 401

    session = db_session.scalar(select(UserSession))
    assert session is not None and session.revoked_at is not None

    # A logout audit event was recorded.
    assert db_session.scalar(select(AuditEvent).where(AuditEvent.action == AuditAction.LOGOUT))


def test_me_requires_authentication(client: TestClient) -> None:
    response = client.get("/auth/me")
    assert response.status_code == 401


def test_revoked_session_cookie_is_rejected(client: TestClient, db_session: Session) -> None:
    make_user(db_session, username="hr1", role=UserRole.HR)
    login = _login(client, "hr1", FIXTURE_PASSWORD)
    csrf = login.json()["csrf_token"]

    # Revoke the session out-of-band (e.g. admin action / cleanup).
    session = db_session.scalar(select(UserSession))
    assert session is not None
    session.revoked_at = utc_now()
    db_session.commit()

    assert client.get("/auth/me").status_code == 401
    # State-changing request also rejected.
    assert client.post("/auth/logout", headers={"X-CSRF-Token": csrf}).status_code == 401


def test_expired_session_is_rejected(client: TestClient, db_session: Session) -> None:
    make_user(db_session, username="hr1", role=UserRole.HR)
    login = _login(client, "hr1", FIXTURE_PASSWORD)
    assert login.status_code == 200

    session = db_session.scalar(select(UserSession))
    assert session is not None
    session.expires_at = utc_now() - timedelta(minutes=1)
    db_session.commit()

    assert client.get("/auth/me").status_code == 401


def test_malformed_session_cookie_is_rejected(client: TestClient) -> None:
    client.cookies.set("hrm_session", "not-a-uuid")
    assert client.get("/auth/me").status_code == 401


def test_csrf_required_for_state_changing_requests(client: TestClient, db_session: Session) -> None:
    make_user(db_session, username="admin1", role=UserRole.ADMIN)
    login = _login(client, "admin1", FIXTURE_PASSWORD)
    csrf = login.json()["csrf_token"]

    # No CSRF header -> rejected even with a valid session.
    no_header = client.post(
        "/admin/users",
        json={"username": "newhr", "role": "hr", "password": FIXTURE_PASSWORD},
    )
    assert no_header.status_code == 403

    # Wrong header -> rejected.
    bad_header = client.post(
        "/admin/users",
        json={"username": "newhr", "role": "hr", "password": FIXTURE_PASSWORD},
        headers={"X-CSRF-Token": "bogus-token"},
    )
    assert bad_header.status_code == 403

    # Correct header -> accepted.
    ok = client.post(
        "/admin/users",
        json={"username": "newhr", "role": "hr", "password": FIXTURE_PASSWORD},
        headers={"X-CSRF-Token": csrf},
    )
    assert ok.status_code == 201


def test_security_headers_present(client: TestClient, db_session: Session) -> None:
    make_user(db_session, username="hr1", role=UserRole.HR)
    response = client.get("/health")
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert response.headers["X-Frame-Options"] == "DENY"
    assert response.headers["Referrer-Policy"] == "no-referrer"
    assert "Permissions-Policy" in response.headers


def test_password_is_stored_as_argon2_hash(client: TestClient, db_session: Session) -> None:
    make_user(db_session, username="hr1", role=UserRole.HR, password="MyStr0ng-Password")
    user = db_session.scalar(select(User).where(User.username == "hr1"))
    assert user is not None
    assert user.password_hash.startswith("$argon2id$")
    assert "MyStr0ng-Password" not in user.password_hash
    assert verify_password(user.password_hash, "MyStr0ng-Password")


def test_login_rate_limit_per_ip_returns_429(client: TestClient, db_session: Session) -> None:
    make_user(db_session, username="hr1", role=UserRole.HR)
    # The default test settings allow 20 attempts / 300 s per IP.
    statuses = [_login(client, "hr1", "wrong").status_code for _ in range(21)]
    assert 429 in statuses
    last = _login(client, "hr1", FIXTURE_PASSWORD)
    assert last.status_code == 429
    assert "retry-after" in {k.lower() for k in last.headers}


def test_successful_login_resets_failure_counter(client: TestClient, db_session: Session) -> None:
    make_user(db_session, username="hr1", role=UserRole.HR)
    for _ in range(3):
        assert _login(client, "hr1", "wrong").status_code == 401
    assert _login(client, "hr1", FIXTURE_PASSWORD).status_code == 200

    user = db_session.scalar(select(User).where(User.username == "hr1"))
    assert user is not None and user.failed_login_count == 0 and user.locked_until is None


def test_login_records_audit_events(client: TestClient, db_session: Session) -> None:
    make_user(db_session, username="hr1", role=UserRole.HR)
    _login(client, "hr1", "wrong")
    _login(client, "hr1", FIXTURE_PASSWORD)

    events = db_session.scalars(select(AuditEvent).order_by(AuditEvent.created_at)).all()
    actions = [event.action for event in events]
    assert AuditAction.LOGIN_FAILURE in actions
    assert AuditAction.LOGIN_SUCCESS in actions
    success = events[-1]
    assert success.username == "hr1"
    # No password material anywhere in the audit trail.
    assert all(FIXTURE_PASSWORD not in (event.details or "") for event in events)


def test_limiter_window_slides_and_allows_again() -> None:
    # Focused unit test for the sliding window: once the oldest attempt leaves
    # the window, new attempts are allowed again.
    from app.rate_limiting import SlidingWindowRateLimiter

    limiter = SlidingWindowRateLimiter(limit=2, window_seconds=60)
    base = time.monotonic()
    assert limiter.check("ip", now=base).allowed
    assert limiter.check("ip", now=base + 1).allowed
    assert not limiter.check("ip", now=base + 2).allowed
    assert limiter.check("ip", now=base + 61).allowed
