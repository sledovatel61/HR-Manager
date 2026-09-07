"""Setup wizard and pilot access tests (SQLite)."""

from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import (
    AccessGrant,
    AccessGrantScope,
    AuditAction,
    AuditEvent,
    User,
    UserRole,
)
from tests.conftest import FIXTURE_PASSWORD, make_user


def _login(client: TestClient, username: str, password: str = FIXTURE_PASSWORD) -> str:
    response = client.post("/auth/login", json={"username": username, "password": password})
    assert response.status_code == 200, response.text
    return response.json()["csrf_token"]


def _login_attempt(client: TestClient, username: str, password: str) -> bool:
    response = client.post("/auth/login", json={"username": username, "password": password})
    return response.status_code == 200


def test_setup_state_honest_when_empty(client: TestClient, db_session: Session) -> None:
    make_user(db_session, username="hr1", role=UserRole.HR)
    _login(client, "hr1")
    body = client.get("/setup/state").json()
    assert body["pilot_exists"] is False
    assert body["pilot_grant_active"] is False
    assert body["preferences_initialized"] is False
    assert body["worker_alive"] is False
    assert body["channels"] == {"telegram": "not_configured", "email": "not_configured"}


def test_pilot_creation_and_idempotency(client: TestClient, db_session: Session) -> None:
    make_user(db_session, username="admin", role=UserRole.ADMIN)
    csrf = _login(client, "admin")

    created = client.post(
        "/setup/pilot",
        json={"username": "pilot", "password": "PilotPass-2026-01", "full_name": "Пилот"},
        headers={"X-CSRF-Token": csrf},
    )
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["username"] == "pilot"
    assert "password" not in body

    pilot = db_session.execute(select(User).where(User.username == "pilot")).scalar_one()
    assert pilot.role == UserRole.ADMIN
    grants = (
        db_session.execute(
            select(AccessGrant).where(
                AccessGrant.user_id == pilot.id,
                AccessGrant.scope == AccessGrantScope.PILOT_FULL_ACCESS,
                AccessGrant.revoked_at.is_(None),
            )
        )
        .scalars()
        .all()
    )
    assert len(grants) == 1

    # Second run: no new user, no password reset, clear 409.
    repeat = client.post(
        "/setup/pilot",
        json={"username": "pilot", "password": "Another-Pass-2026", "full_name": "Пилот"},
        headers={"X-CSRF-Token": csrf},
    )
    assert repeat.status_code == 409

    # Existing username with a DIFFERENT password must not be overwritten.
    other = client.post(
        "/setup/pilot",
        json={"username": "admin", "password": "Another-Pass-2026", "full_name": "X"},
        headers={"X-CSRF-Token": csrf},
    )
    assert other.status_code == 409
    assert _login_attempt(client, "admin", "Another-Pass-2026") is False

    # Audited.
    actions = {event.action for event in db_session.execute(select(AuditEvent)).scalars().all()}
    assert AuditAction.PILOT_USER_CREATED in actions
    assert AuditAction.PILOT_ACCESS_GRANTED in actions


def test_pilot_requires_admin(client: TestClient, db_session: Session) -> None:
    make_user(db_session, username="hr1", role=UserRole.HR)
    csrf = _login(client, "hr1")
    response = client.post(
        "/setup/pilot",
        json={"username": "pilot", "password": "PilotPass-2026-01", "full_name": "П"},
        headers={"X-CSRF-Token": csrf},
    )
    assert response.status_code == 403


def test_grants_list_and_revoke(client: TestClient, db_session: Session) -> None:
    make_user(db_session, username="admin", role=UserRole.ADMIN)
    target = make_user(db_session, username="target", role=UserRole.ADMIN)
    csrf = _login(client, "admin")

    granted = client.post(
        "/admin/access-grants",
        json={"user_id": str(target.id)},
        headers={"X-CSRF-Token": csrf},
    )
    assert granted.status_code == 200, granted.text
    assert granted.json()["scope"] == "pilot_full_access"
    assert granted.json()["username"] == "target"

    listing = client.get("/admin/access-grants").json()
    assert len(listing["items"]) == 1

    # Duplicate grant is an idempotent no-op (same grant returned).
    again = client.post(
        "/admin/access-grants",
        json={"user_id": str(target.id)},
        headers={"X-CSRF-Token": csrf},
    )
    assert again.status_code == 200
    assert again.json()["id"] == granted.json()["id"]

    revoked = client.post(
        "/admin/access-grants",
        json={"user_id": str(target.id), "revoke": True, "revoke_reason": "pilot finished"},
        headers={"X-CSRF-Token": csrf},
    )
    assert revoked.status_code == 200
    assert revoked.json()["revoked_at"] is not None

    state = client.get("/setup/state").json()
    assert state["pilot_grant_active"] is False
    actions = {event.action for event in db_session.execute(select(AuditEvent)).scalars().all()}
    assert AuditAction.PILOT_ACCESS_REVOKED in actions


def test_pilot_full_access_matrix(client: TestClient, db_session: Session) -> None:
    """The pilot (admin + explicit grant) can do everything an HR and a
    manager can — and a plain HR cannot reach the admin surfaces."""
    from datetime import UTC, datetime, timedelta

    from app.utils import utc_now

    pilot = make_user(db_session, username="pilot", role=UserRole.ADMIN)
    plain_hr = make_user(db_session, username="plain_hr", role=UserRole.HR)
    grant = AccessGrant(
        user_id=pilot.id,
        scope=AccessGrantScope.PILOT_FULL_ACCESS,
        granted_by_user_id=pilot.id,
        granted_at=utc_now(),
    )
    db_session.add(grant)
    db_session.commit()

    csrf = _login(client, "pilot")

    # Candidate work (HR function, shared scope).
    created = client.post(
        "/candidates",
        json={
            "full_name": "Новый кандидат",
            "source": "referral",
            "position": "Разработчик",
            "owner_user_id": str(plain_hr.id),
        },
        headers={"X-CSRF-Token": csrf},
    )
    assert created.status_code == 201, created.text
    candidate = created.json()

    # Calendar event (manager function: explicit assignee).
    event = client.post(
        "/events",
        json={
            "candidate_id": candidate["id"],
            "assignee_user_id": str(plain_hr.id),
            "type": "interview",
            "title": "Собеседование",
            "starts_at": (utc_now() + timedelta(days=1)).isoformat(),
        },
        headers={"X-CSRF-Token": csrf},
    )
    assert event.status_code == 201, event.text

    # Analytics (manager/admin function); date-only bounds are accepted.
    _from = (datetime.now(UTC) - timedelta(days=30)).date().isoformat()
    _to = (datetime.now(UTC) + timedelta(days=1)).date().isoformat()
    analytics = client.get(f"/analytics/kpi?from={_from}&to={_to}")
    assert analytics.status_code == 200, analytics.text

    # Admin surfaces.
    assert client.get("/admin/users").status_code == 200
    assert client.get("/admin/ops/notifications/queue").status_code == 200
    assert client.get("/admin/access-grants").status_code == 200

    # The same admin surfaces are closed for a plain HR.
    _login(client, "plain_hr")
    assert client.get("/admin/ops/notifications/queue").status_code == 403
