"""Unit tests for role-based access and admin user management."""

from collections.abc import Iterator
from uuid import uuid4

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import AuditAction, AuditEvent, User, UserRole
from app.routers.auth import reset_login_limiter
from app.security import verify_password
from tests.conftest import FIXTURE_PASSWORD, make_user


@pytest.fixture(autouse=True)
def _clean_limiter() -> Iterator[None]:
    reset_login_limiter()
    yield
    reset_login_limiter()


def _login(client: TestClient, username: str, password: str = FIXTURE_PASSWORD) -> httpx.Response:
    return client.post("/auth/login", json={"username": username, "password": password})


def _auth_headers(client: TestClient, username: str, role: UserRole, db: Session) -> dict[str, str]:
    make_user(db, username=username, role=role)
    response = _login(client, username)
    assert response.status_code == 200
    token: str = response.json()["csrf_token"]
    return {"X-CSRF-Token": token}


@pytest.mark.parametrize("role", [UserRole.HR, UserRole.MANAGER])
def test_non_admin_cannot_list_users(
    client: TestClient, db_session: Session, role: UserRole
) -> None:
    headers = _auth_headers(client, f"user-{role.value}", role, db_session)
    response = client.get("/admin/users", headers=headers)
    assert response.status_code == 403


@pytest.mark.parametrize("role", [UserRole.HR, UserRole.MANAGER])
def test_non_admin_cannot_create_users(
    client: TestClient, db_session: Session, role: UserRole
) -> None:
    headers = _auth_headers(client, f"user-{role.value}", role, db_session)
    response = client.post(
        "/admin/users",
        json={"username": "newbie", "role": "hr", "password": FIXTURE_PASSWORD},
        headers=headers,
    )
    assert response.status_code == 403


def test_non_admin_cannot_read_audit_log(client: TestClient, db_session: Session) -> None:
    headers = _auth_headers(client, "mgr", UserRole.MANAGER, db_session)
    response = client.get("/admin/audit", headers=headers)
    assert response.status_code == 403


def test_anonymous_access_is_unauthorized(client: TestClient) -> None:
    assert client.get("/admin/users").status_code == 401
    assert client.get("/admin/audit").status_code == 401
    assert client.get("/auth/me").status_code == 401


def test_admin_can_create_user_with_password(client: TestClient, db_session: Session) -> None:
    headers = _auth_headers(client, "admin1", UserRole.ADMIN, db_session)

    response = client.post(
        "/admin/users",
        json={
            "username": "new_hr",
            "full_name": "New HR",
            "role": "hr",
            "password": FIXTURE_PASSWORD,
        },
        headers=headers,
    )
    assert response.status_code == 201
    created = response.json()
    assert created["username"] == "new_hr"
    assert created["role"] == "hr"
    assert created["is_active"] is True
    assert "password" not in created and "password_hash" not in created

    # The new user can log in.
    login = _login(client, "new_hr", FIXTURE_PASSWORD)
    assert login.status_code == 200

    # Password is stored hashed.
    user = db_session.scalar(select(User).where(User.username == "new_hr"))
    assert user is not None
    assert user.password_hash.startswith("$argon2id$")
    assert verify_password(user.password_hash, FIXTURE_PASSWORD)

    # An audit event was recorded with the admin as actor.
    event = db_session.scalar(
        select(AuditEvent).where(AuditEvent.action == AuditAction.USER_CREATED)
    )
    assert event is not None
    assert event.actor_user_id is not None


def test_create_user_requires_strong_password(client: TestClient, db_session: Session) -> None:
    headers = _auth_headers(client, "admin1", UserRole.ADMIN, db_session)

    for bad_password in ["short", "alllettersnocaps", "123456789012", ""]:
        username = f"weak-{bad_password or 'empty'}"
        response = client.post(
            "/admin/users",
            json={"username": username, "role": "hr", "password": bad_password},
            headers=headers,
        )
        assert response.status_code == 422, (bad_password, response.status_code)


def test_create_user_duplicate_username_conflicts(client: TestClient, db_session: Session) -> None:
    headers = _auth_headers(client, "admin1", UserRole.ADMIN, db_session)
    make_user(db_session, username="existing", role=UserRole.HR)

    response = client.post(
        "/admin/users",
        json={"username": "EXISTING", "role": "hr", "password": FIXTURE_PASSWORD},
        headers=headers,
    )
    assert response.status_code == 409


def test_create_user_invalid_username_rejected(client: TestClient, db_session: Session) -> None:
    headers = _auth_headers(client, "admin1", UserRole.ADMIN, db_session)
    response = client.post(
        "/admin/users",
        json={"username": "bad user!", "role": "hr", "password": FIXTURE_PASSWORD},
        headers=headers,
    )
    assert response.status_code == 422


def test_admin_can_change_role(client: TestClient, db_session: Session) -> None:
    headers = _auth_headers(client, "admin1", UserRole.ADMIN, db_session)
    target = make_user(db_session, username="promote_me", role=UserRole.HR)

    response = client.patch(f"/admin/users/{target.id}", json={"role": "manager"}, headers=headers)
    assert response.status_code == 200
    assert response.json()["role"] == "manager"

    event = db_session.scalar(
        select(AuditEvent).where(AuditEvent.action == AuditAction.ROLE_CHANGED)
    )
    assert event is not None
    assert "hr" in (event.details or "") and "manager" in (event.details or "")


def test_admin_can_deactivate_and_reactivate_user(client: TestClient, db_session: Session) -> None:
    headers = _auth_headers(client, "admin1", UserRole.ADMIN, db_session)
    target = make_user(db_session, username="bye", role=UserRole.HR)

    deactivated = client.patch(
        f"/admin/users/{target.id}", json={"is_active": False}, headers=headers
    )
    assert deactivated.status_code == 200
    assert deactivated.json()["is_active"] is False

    # Deactivated user cannot log in.
    assert _login(client, "bye", FIXTURE_PASSWORD).status_code == 403

    reactivated = client.patch(
        f"/admin/users/{target.id}", json={"is_active": True}, headers=headers
    )
    assert reactivated.status_code == 200
    assert reactivated.json()["is_active"] is True
    assert _login(client, "bye", FIXTURE_PASSWORD).status_code == 200

    assert db_session.scalar(
        select(AuditEvent).where(AuditEvent.action == AuditAction.USER_REACTIVATED)
    ) or db_session.scalar(select(AuditEvent).where(AuditEvent.action == AuditAction.USER_UPDATED))


def test_admin_cannot_deactivate_themselves(client: TestClient, db_session: Session) -> None:
    admin = make_user(db_session, username="admin1", role=UserRole.ADMIN)
    response = _login(client, "admin1")
    headers = {"X-CSRF-Token": response.json()["csrf_token"]}

    result = client.patch(f"/admin/users/{admin.id}", json={"is_active": False}, headers=headers)
    assert result.status_code == 400


def test_admin_can_unlock_user(client: TestClient, db_session: Session) -> None:
    headers = _auth_headers(client, "admin1", UserRole.ADMIN, db_session)
    target = make_user(db_session, username="locked", role=UserRole.HR)
    target.failed_login_count = 5
    from datetime import timedelta

    from app.utils import utc_now

    target.locked_until = utc_now() + timedelta(minutes=15)
    db_session.commit()

    response = client.post(f"/admin/users/{target.id}/unlock", headers=headers)
    assert response.status_code == 200
    db_session.refresh(target)
    assert target.locked_until is None and target.failed_login_count == 0

    assert _login(client, "locked", FIXTURE_PASSWORD).status_code == 200
    assert db_session.scalar(
        select(AuditEvent).where(AuditEvent.action == AuditAction.USER_UNLOCKED)
    )


def test_admin_can_reset_password(client: TestClient, db_session: Session) -> None:
    headers = _auth_headers(client, "admin1", UserRole.ADMIN, db_session)
    target = make_user(db_session, username="reset_me", role=UserRole.HR)

    new_password = "Brand-New-Pass-2026"
    response = client.patch(
        f"/admin/users/{target.id}", json={"password": new_password}, headers=headers
    )
    assert response.status_code == 200

    # Old password fails, new password works.
    assert _login(client, "reset_me", FIXTURE_PASSWORD).status_code == 401
    assert _login(client, "reset_me", new_password).status_code == 200


def test_get_unknown_user_returns_404(client: TestClient, db_session: Session) -> None:
    headers = _auth_headers(client, "admin1", UserRole.ADMIN, db_session)
    response = client.get(f"/admin/users/{uuid4()}", headers=headers)
    assert response.status_code == 404


def test_user_list_is_paginated(client: TestClient, db_session: Session) -> None:
    _auth_headers(client, "admin1", UserRole.ADMIN, db_session)
    for index in range(5):
        make_user(db_session, username=f"hr{index}", role=UserRole.HR)

    response = client.get("/admin/users?limit=3&offset=0")
    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 6  # admin + 5 HR
    assert len(body["items"]) == 3
    assert body["limit"] == 3 and body["offset"] == 0


def test_audit_log_requires_admin_and_filters(client: TestClient, db_session: Session) -> None:
    make_user(db_session, username="hr1", role=UserRole.HR)
    # Generate hr1 audit events first; logging in swaps the client's session
    # cookie, so the admin login happens afterwards.
    _login(client, "hr1", FIXTURE_PASSWORD)
    _login(client, "hr1", "wrong-password")
    headers = _auth_headers(client, "admin1", UserRole.ADMIN, db_session)

    response = client.get("/admin/audit?action=login_success&limit=10", headers=headers)
    assert response.status_code == 200
    body = response.json()
    assert body["total"] >= 1  # at least the admin login
    assert all(event["action"] == "login_success" for event in body["items"])

    by_username = client.get("/admin/audit?username=hr1", headers=headers)
    assert by_username.status_code == 200
    assert all(event["username"] == "hr1" for event in by_username.json()["items"])
