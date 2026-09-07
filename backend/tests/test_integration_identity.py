"""Integration tests for identity & security against a real PostgreSQL.

Run with::

    TEST_DATABASE_URL=postgresql+psycopg://user:pass@host:5432/db \
        pytest -m integration -v

The schema must already be applied (``alembic upgrade head``); fixtures
truncate the tables before each test. These mirror the SQLite unit tests for
the highest-risk flows (login, RBAC, lockout, audit) plus PostgreSQL-specific
behaviour (native UUID, timestamptz, functional unique index).
"""

from collections.abc import Iterator

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import AuditAction, AuditEvent, UserRole
from app.routers.auth import reset_login_limiter
from tests.conftest import FIXTURE_PASSWORD, make_user

pytestmark = pytest.mark.integration


@pytest.fixture(autouse=True)
def _clean_limiter() -> Iterator[None]:
    reset_login_limiter()
    yield
    reset_login_limiter()


def _login(client: TestClient, username: str, password: str = FIXTURE_PASSWORD) -> httpx.Response:
    return client.post("/auth/login", json={"username": username, "password": password})


def test_login_logout_and_me_on_postgres(pg_client: TestClient, pg_db: Session) -> None:
    make_user(pg_db, username="hr1", role=UserRole.HR)

    login = _login(pg_client, "hr1")
    assert login.status_code == 200
    body = login.json()
    assert body["user"]["role"] == "hr"
    csrf = body["csrf_token"]

    me = pg_client.get("/auth/me")
    assert me.status_code == 200
    assert me.json()["user"]["username"] == "hr1"

    logout = pg_client.post("/auth/logout", headers={"X-CSRF-Token": csrf})
    assert logout.status_code == 200
    assert pg_client.get("/auth/me").status_code == 401


def test_rbac_enforced_on_postgres(pg_client: TestClient, pg_db: Session) -> None:
    make_user(pg_db, username="admin1", role=UserRole.ADMIN)
    make_user(pg_db, username="mgr", role=UserRole.MANAGER)

    # Manager cannot reach admin endpoints.
    mgr_login = _login(pg_client, "mgr")
    mgr_csrf = mgr_login.json()["csrf_token"]
    assert pg_client.get("/admin/users", headers={"X-CSRF-Token": mgr_csrf}).status_code == 403

    # Admin can.
    admin_login = _login(pg_client, "admin1")
    admin_csrf = admin_login.json()["csrf_token"]
    created = pg_client.post(
        "/admin/users",
        json={"username": "new_hr", "role": "hr", "password": FIXTURE_PASSWORD},
        headers={"X-CSRF-Token": admin_csrf},
    )
    assert created.status_code == 201

    # The created user can authenticate.
    assert _login(pg_client, "new_hr").status_code == 200


def test_lockout_persists_on_postgres(pg_client: TestClient, pg_db: Session) -> None:
    make_user(pg_db, username="hr1", role=UserRole.HR)
    for _ in range(4):
        assert _login(pg_client, "hr1", "wrong").status_code == 401
    assert _login(pg_client, "hr1", "wrong").status_code == 423
    assert _login(pg_client, "hr1", FIXTURE_PASSWORD).status_code == 423

    locked = pg_db.scalar(select(AuditEvent).where(AuditEvent.action == AuditAction.ACCOUNT_LOCKED))
    assert locked is not None


def test_case_insensitive_unique_username_on_postgres(
    pg_client: TestClient, pg_db: Session
) -> None:
    make_user(pg_db, username="CaseUser", role=UserRole.HR)
    # Logging in with a different case works.
    assert _login(pg_client, "caseuser").status_code == 200

    # The functional unique index rejects a duplicate in any case.
    make_user(pg_db, username="admin1", role=UserRole.ADMIN)
    admin_csrf = _login(pg_client, "admin1").json()["csrf_token"]
    duplicate = pg_client.post(
        "/admin/users",
        json={"username": "CASEUSER", "role": "hr", "password": FIXTURE_PASSWORD},
        headers={"X-CSRF-Token": admin_csrf},
    )
    assert duplicate.status_code == 409


def test_timestamps_are_timezone_aware_on_postgres(pg_db: Session) -> None:
    from app.utils import utc_now

    user = make_user(pg_db, username="tzcheck", role=UserRole.HR)
    assert user.created_at.tzinfo is not None
    assert user.id.version == 4  # UUID4
    assert user.created_at <= utc_now()


def test_session_expiry_enforced_on_postgres(pg_client: TestClient, pg_db: Session) -> None:
    from datetime import timedelta

    from app.models import UserSession
    from app.utils import utc_now

    make_user(pg_db, username="hr1", role=UserRole.HR)
    login = _login(pg_client, "hr1")
    assert login.status_code == 200

    session = pg_db.scalar(select(UserSession))
    assert session is not None
    session.expires_at = utc_now() - timedelta(minutes=1)
    pg_db.commit()

    assert pg_client.get("/auth/me").status_code == 401


def test_admin_can_list_audit_trail_on_postgres(pg_client: TestClient, pg_db: Session) -> None:
    make_user(pg_db, username="admin1", role=UserRole.ADMIN)
    csrf = _login(pg_client, "admin1").json()["csrf_token"]

    response = pg_client.get("/admin/audit?limit=10", headers={"X-CSRF-Token": csrf})
    assert response.status_code == 200
    assert response.json()["total"] >= 1
    assert all("password" not in (event.get("details") or "") for event in response.json()["items"])
    # Count is consistent.
    assert pg_db.scalar(select(func.count()).select_from(AuditEvent)) == response.json()["total"]
