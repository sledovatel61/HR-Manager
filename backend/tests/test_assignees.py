"""Unit tests for the assignable-HR directory (UX feedback 2026-09-29, A/F).

The pilot installation described in ``PRODUCT_SPEC.md`` §2 has a single
account whose server role is ``admin`` plus an explicit, audited
``pilot_full_access`` grant.  Before the fix, ``GET /admin/users/hr`` and the
event/transfer assignee rules accepted **only** ``role == hr``, so such an
installation had an empty «Исполнитель» picker and could not create a single
calendar event.

These tests pin the fixed contract:

* the pilot account is assignable and is offered by the directory;
* a *plain* admin/manager without the grant stays non-assignable (the
  security boundary is not widened by accident);
* an inactive or revoked pilot grant is not assignable;
* the directory is readable by every authenticated role, not only admin;
* the event and transfer validation use the very same set, so an option the
  UI shows is never rejected by the server.
"""

from collections.abc import Iterator
from uuid import uuid4

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.models import AccessGrant, AccessGrantScope, User, UserRole
from app.routers.auth import reset_login_limiter
from tests.conftest import FIXTURE_PASSWORD, make_candidate, make_user


@pytest.fixture(autouse=True)
def _clean_limiter() -> Iterator[None]:
    reset_login_limiter()
    yield
    reset_login_limiter()


def _login(client: TestClient, username: str) -> httpx.Response:
    return client.post("/auth/login", json={"username": username, "password": FIXTURE_PASSWORD})


def _csrf(response: httpx.Response) -> str:
    return response.json()["csrf_token"]


def _grant_pilot(db: Session, user: User) -> AccessGrant:
    """Give ``user`` the audited pilot full-access grant."""
    grant = AccessGrant(user_id=user.id, scope=AccessGrantScope.PILOT_FULL_ACCESS)
    db.add(grant)
    db.commit()
    db.refresh(grant)
    return grant


def _directory(client: TestClient, username: str) -> list[str]:
    response = client.get("/admin/users/hr")
    assert response.status_code == 200
    return [item["username"] for item in response.json()["items"]]


def test_pilot_account_is_assignable_and_listed(client: TestClient, db_session: Session) -> None:
    """The pilot admin is the only HR-capable account — it must still show up."""
    pilot = make_user(
        db_session, username="pilot", role=UserRole.ADMIN, full_name="Перепечать Мария"
    )
    _grant_pilot(db_session, pilot)
    make_user(db_session, username="plain_admin", role=UserRole.ADMIN)

    _login(client, "pilot")
    names = _directory(client, "pilot")

    assert "pilot" in names
    # A plain administrator is NOT an assignee: the rule is not "any admin".
    assert "plain_admin" not in names


def test_inactive_pilot_is_not_assignable(client: TestClient, db_session: Session) -> None:
    pilot = make_user(db_session, username="pilot", role=UserRole.ADMIN, is_active=False)
    _grant_pilot(db_session, pilot)

    _login(client, "pilot")
    # Deactivated accounts cannot log in, so inspect the directory as admin.
    admin = make_user(db_session, username="root", role=UserRole.ADMIN)
    _grant_pilot(db_session, admin)
    _login(client, "root")

    assert "pilot" not in _directory(client, "root")


def test_revoked_pilot_grant_is_not_assignable(client: TestClient, db_session: Session) -> None:
    admin = make_user(db_session, username="root", role=UserRole.ADMIN)
    grant = _grant_pilot(db_session, admin)
    make_user(db_session, username="hr2", role=UserRole.HR)

    _login(client, "root")
    assert "root" in _directory(client, "root")

    # Revoking the grant removes the pilot powers: the admin is assignable no more.
    grant.revoked_at = grant.granted_at
    db_session.commit()
    db_session.expire_all()

    assert "root" not in _directory(client, "root")
    assert "hr2" in _directory(client, "root")


def test_directory_is_readable_by_a_plain_hr(client: TestClient, db_session: Session) -> None:
    """The picker is a lookup, not user administration: every role may read it."""
    make_user(db_session, username="hr1", role=UserRole.HR)
    make_user(db_session, username="hr2", role=UserRole.HR)
    adm = make_user(db_session, username="adm", role=UserRole.ADMIN)
    _grant_pilot(db_session, adm)

    _login(client, "hr1")
    names = _directory(client, "hr1")
    assert {"hr1", "hr2", "adm"} <= set(names)


def test_directory_requires_authentication(client: TestClient) -> None:
    assert client.get("/admin/users/hr").status_code == 401


def test_admin_can_create_event_assigned_to_pilot_account(
    client: TestClient, db_session: Session
) -> None:
    """Happy path from the report: the pilot admin saves a calendar event."""
    pilot = make_user(db_session, username="pilot", role=UserRole.ADMIN)
    _grant_pilot(db_session, pilot)
    candidate = make_candidate(db_session, owner=pilot, full_name="Петров Пётр")

    login = _login(client, "pilot")
    csrf = _csrf(login)
    response = client.post(
        "/events",
        json={
            "candidate_id": str(candidate.id),
            "type": "call",
            "title": "Перезвонить кандидату",
            "starts_at": "2026-10-01T09:00:00+00:00",
            "assignee_user_id": str(pilot.id),
        },
        headers={"X-CSRF-Token": csrf},
    )
    assert response.status_code == 201, response.text
    assert response.json()["assignee_user_id"] == str(pilot.id)


def test_admin_without_grant_cannot_be_an_assignee(client: TestClient, db_session: Session) -> None:
    """Plain admins/managers must still be rejected — the grant is the gate."""
    admin = make_user(db_session, username="adm", role=UserRole.ADMIN)
    hr = make_user(db_session, username="hr1", role=UserRole.HR)
    candidate = make_candidate(db_session, owner=hr)

    login = _login(client, "adm")
    csrf = _csrf(login)
    response = client.post(
        "/events",
        json={
            "candidate_id": str(candidate.id),
            "type": "call",
            "title": "Перезвонить",
            "starts_at": "2026-10-01T09:00:00+00:00",
            "assignee_user_id": str(admin.id),
        },
        headers={"X-CSRF-Token": csrf},
    )
    assert response.status_code == 422
    assert "ролью HR" in response.json()["detail"]


def test_unknown_assignee_is_rejected(client: TestClient, db_session: Session) -> None:
    admin = make_user(db_session, username="adm", role=UserRole.ADMIN)
    _grant_pilot(db_session, admin)
    candidate = make_candidate(db_session, owner=admin)

    login = _login(client, "adm")
    csrf = _csrf(login)
    response = client.post(
        "/events",
        json={
            "candidate_id": str(candidate.id),
            "type": "call",
            "title": "Перезвонить",
            "starts_at": "2026-10-01T09:00:00+00:00",
            "assignee_user_id": str(uuid4()),
        },
        headers={"X-CSRF-Token": csrf},
    )
    assert response.status_code == 422


def test_transfer_accepts_pilot_account_as_new_owner(
    client: TestClient, db_session: Session
) -> None:
    """The owner picker and the transfer validation share the same set."""
    hr1 = make_user(db_session, username="hr1", role=UserRole.HR)
    pilot = make_user(db_session, username="pilot", role=UserRole.ADMIN)
    _grant_pilot(db_session, pilot)
    candidate = make_candidate(db_session, owner=hr1)

    login = _login(client, "hr1")
    csrf = _csrf(login)
    response = client.post(
        f"/candidates/{candidate.id}/transfer",
        json={"new_owner_user_id": str(pilot.id), "reason": "Пилот ведёт подбор"},
        headers={"X-CSRF-Token": csrf},
    )
    assert response.status_code == 200, response.text
    assert response.json()["transfer"]["to_user_id"] == str(pilot.id)
