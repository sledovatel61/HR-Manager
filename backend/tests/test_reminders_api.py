"""Unit tests for the personal reminders API (SQLite, RBAC, versioning)."""

from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.models import Candidate, CandidateSource, CandidateStage, UserRole
from app.utils import utc_now
from tests.conftest import FIXTURE_PASSWORD, make_user

NOW = datetime(2026, 9, 4, 12, 0, 0, tzinfo=UTC)


def _login(client: TestClient, username: str) -> str:
    response = client.post("/auth/login", json={"username": username, "password": FIXTURE_PASSWORD})
    assert response.status_code == 200, response.text
    return response.json()["csrf_token"]


def _payload(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "title": "Позвонить кандидату",
        "due_at": (utc_now() + timedelta(days=1)).isoformat(),
        "timezone": "Europe/Moscow",
        "importance": "normal",
        "recurrence": "none",
    }
    payload.update(overrides)
    return payload


def test_create_list_get_own_only(client: TestClient, db_session: Session) -> None:
    make_user(db_session, username="hr1", role=UserRole.HR)
    make_user(db_session, username="hr2", role=UserRole.HR)
    csrf = _login(client, "hr1")

    created = client.post("/reminders", json=_payload(), headers={"X-CSRF-Token": csrf})
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["owner_user_id"] != ""
    assert body["assignee_user_id"] == body["owner_user_id"]
    assert body["version"] == 1

    listing = client.get("/reminders").json()
    assert listing["total"] == 1
    assert listing["items"][0]["id"] == body["id"]

    # A foreign HR sees nothing (404 on the direct id).
    csrf2 = _login(client, "hr2")
    assert client.get("/reminders").json()["total"] == 0
    assert client.get(f"/reminders/{body['id']}").status_code == 404
    assert (
        client.patch(
            f"/reminders/{body['id']}",
            json={"expected_version": 1, "title": "X"},
            headers={"X-CSRF-Token": csrf2},
        ).status_code
        == 404
    )


def test_update_version_conflict(client: TestClient, db_session: Session) -> None:
    make_user(db_session, username="hr1", role=UserRole.HR)
    csrf = _login(client, "hr1")
    reminder_id = client.post("/reminders", json=_payload(), headers={"X-CSRF-Token": csrf}).json()[
        "id"
    ]

    ok = client.patch(
        f"/reminders/{reminder_id}",
        json={"expected_version": 1, "title": "Новый заголовок"},
        headers={"X-CSRF-Token": csrf},
    )
    assert ok.status_code == 200
    assert ok.json()["title"] == "Новый заголовок"
    assert ok.json()["version"] == 2

    stale = client.patch(
        f"/reminders/{reminder_id}",
        json={"expected_version": 1, "title": "Проиграет"},
        headers={"X-CSRF-Token": csrf},
    )
    assert stale.status_code == 409


def test_complete_and_cancel(client: TestClient, db_session: Session) -> None:
    make_user(db_session, username="hr1", role=UserRole.HR)
    csrf = _login(client, "hr1")
    first = client.post("/reminders", json=_payload(), headers={"X-CSRF-Token": csrf}).json()
    completed = client.post(f"/reminders/{first['id']}/complete", headers={"X-CSRF-Token": csrf})
    assert completed.status_code == 200
    assert completed.json()["status"] == "completed"
    assert completed.json()["completed_at"] is not None

    # Completed reminders cannot be edited or cancelled.
    assert (
        client.post(f"/reminders/{first['id']}/cancel", headers={"X-CSRF-Token": csrf}).status_code
        == 409
    )

    second = client.post("/reminders", json=_payload(), headers={"X-CSRF-Token": csrf}).json()
    cancelled = client.post(f"/reminders/{second['id']}/cancel", headers={"X-CSRF-Token": csrf})
    assert cancelled.status_code == 200
    assert cancelled.json()["status"] == "cancelled"
    # Cancel is idempotent.
    again = client.post(f"/reminders/{second['id']}/cancel", headers={"X-CSRF-Token": csrf})
    assert again.status_code == 200


def test_validation_errors(client: TestClient, db_session: Session) -> None:
    make_user(db_session, username="hr1", role=UserRole.HR)
    csrf = _login(client, "hr1")

    assert (
        client.post(
            "/reminders", json=_payload(timezone="Mars/Olympus"), headers={"X-CSRF-Token": csrf}
        ).status_code
        == 422
    )
    assert (
        client.post(
            "/reminders", json=_payload(recurrence="hourly"), headers={"X-CSRF-Token": csrf}
        ).status_code
        == 422
    )
    assert (
        client.post(
            "/reminders", json=_payload(title=""), headers={"X-CSRF-Token": csrf}
        ).status_code
        == 422
    )


def test_candidate_link_scoped_to_visibility(client: TestClient, db_session: Session) -> None:
    owner = make_user(db_session, username="owner", role=UserRole.HR)
    foreign_hr = make_user(db_session, username="foreign", role=UserRole.HR)
    make_user(db_session, username="manager", role=UserRole.MANAGER)
    candidate = Candidate(
        full_name="Кандидат",
        full_name_normalized="кандидат",
        source=CandidateSource.REFERRAL,
        position="Dev",
        owner_user_id=owner.id,
        stage=CandidateStage.NEW,
        stage_position=0,
        created_at=utc_now(),
        updated_at=utc_now(),
    )
    db_session.add(candidate)
    db_session.commit()

    # The owner HR can link their own candidate.
    csrf_owner = _login(client, "owner")
    ok = client.post(
        "/reminders",
        json=_payload(candidate_id=str(candidate.id)),
        headers={"X-CSRF-Token": csrf_owner},
    )
    assert ok.status_code == 201, ok.text

    # Delegation to an assignee without candidate access is refused.
    denied_delegation = client.post(
        "/reminders",
        json=_payload(candidate_id=str(candidate.id), assignee_user_id=str(foreign_hr.id)),
        headers={"X-CSRF-Token": csrf_owner},
    )
    assert denied_delegation.status_code == 422

    # A foreign HR cannot link a candidate outside their scope (422, same
    # as «not found» — no leak).
    csrf_foreign = _login(client, "foreign")
    denied = client.post(
        "/reminders",
        json=_payload(candidate_id=str(candidate.id)),
        headers={"X-CSRF-Token": csrf_foreign},
    )
    assert denied.status_code == 422


def test_assignee_delegation_and_edit_restriction(client: TestClient, db_session: Session) -> None:
    owner = make_user(db_session, username="owner", role=UserRole.HR)
    other_hr = make_user(db_session, username="other_hr", role=UserRole.HR)
    candidate = Candidate(
        full_name="Кандидат",
        full_name_normalized="кандидат",
        source=CandidateSource.REFERRAL,
        position="Dev",
        owner_user_id=owner.id,
        stage=CandidateStage.NEW,
        stage_position=0,
        created_at=utc_now(),
        updated_at=utc_now(),
    )
    db_session.add(candidate)
    db_session.commit()

    csrf_owner = _login(client, "owner")
    created = client.post(
        "/reminders",
        json=_payload(assignee_user_id=str(other_hr.id)),
        headers={"X-CSRF-Token": csrf_owner},
    )
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["assignee_user_id"] == str(other_hr.id)

    # The OWNER still sees the reminder they delegated (owner-or-assignee
    # scope; regression for the assignee-only list filter).
    owner_list = client.get("/reminders").json()
    assert owner_list["total"] == 1
    assert owner_list["items"][0]["id"] == body["id"]

    # The assignee sees the reminder but cannot edit it.
    csrf_other = _login(client, "other_hr")
    assert client.get(f"/reminders/{body['id']}").status_code == 200
    assert (
        client.patch(
            f"/reminders/{body['id']}",
            json={"expected_version": 1, "title": "X"},
            headers={"X-CSRF-Token": csrf_other},
        ).status_code
        == 403
    )
    # …but can complete it.
    assert (
        client.post(
            f"/reminders/{body['id']}/complete", headers={"X-CSRF-Token": csrf_other}
        ).status_code
        == 200
    )


def test_list_scope_is_owner_or_assignee(client: TestClient, db_session: Session) -> None:
    """Regression: the list must include reminders where the user is the
    OWNER (even when delegated) or the ASSIGNEE — and nothing else."""
    owner = make_user(db_session, username="owner", role=UserRole.HR)
    other_hr = make_user(db_session, username="other_hr", role=UserRole.HR)
    make_user(db_session, username="bystander", role=UserRole.HR)
    candidate = Candidate(
        full_name="Кандидат",
        full_name_normalized="кандидат",
        source=CandidateSource.REFERRAL,
        position="Dev",
        owner_user_id=owner.id,
        stage=CandidateStage.NEW,
        stage_position=0,
        created_at=utc_now(),
        updated_at=utc_now(),
    )
    db_session.add(candidate)
    db_session.commit()

    # Delegated reminder: owner != assignee.
    csrf_owner = _login(client, "owner")
    delegated = client.post(
        "/reminders",
        json=_payload(assignee_user_id=str(other_hr.id)),
        headers={"X-CSRF-Token": csrf_owner},
    )
    assert delegated.status_code == 201, delegated.text
    delegated_id = delegated.json()["id"]

    # Own reminder: owner == assignee.
    own = client.post("/reminders", json=_payload(), headers={"X-CSRF-Token": csrf_owner})
    assert own.status_code == 201

    # The owner sees BOTH (one as owner, one as owner+assignee).
    owner_list = client.get("/reminders").json()
    assert owner_list["total"] == 2
    assert {item["id"] for item in owner_list["items"]} == {delegated_id, own.json()["id"]}

    # The assignee sees the delegated one only.
    _login(client, "other_hr")
    assignee_list = client.get("/reminders").json()
    assert assignee_list["total"] == 1
    assert assignee_list["items"][0]["id"] == delegated_id

    # A bystander sees nothing and gets 404 on the direct id.
    _login(client, "bystander")
    assert client.get("/reminders").json()["total"] == 0
    assert client.get(f"/reminders/{delegated_id}").status_code == 404
