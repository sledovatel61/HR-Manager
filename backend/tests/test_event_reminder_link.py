"""The shared event↔reminder↔candidate domain contract (UX feedback 2026-09-29).

Covers: the linked «Моё напоминание» created with the event (calendar entry
point), movement with reschedules, closing with the event, the no-duplicates
guarantee (unique link), the optional candidate link on events, the
candidate filter on the reminders list and the admin/manager view of the
assignee directory. Error paths and rights are asserted alongside every
happy path.
"""

from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.models import User, UserRole
from app.utils import utc_now
from tests.conftest import FIXTURE_PASSWORD, make_candidate, make_user


def _login(client: TestClient, username: str) -> str:
    response = client.post("/auth/login", json={"username": username, "password": FIXTURE_PASSWORD})
    assert response.status_code == 200, response.text
    return response.json()["csrf_token"]


def _ts(value: str) -> datetime:
    """Parse an ISO timestamp for comparison.

    The events API serializes SQLite round-tripped values without an offset
    while reminders always carry UTC — normalize both to aware UTC.
    """
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return parsed.replace(tzinfo=parsed.tzinfo) if parsed.tzinfo else parsed.replace(
        tzinfo=UTC
    )


def _in(minutes: int) -> str:
    return (utc_now() + timedelta(minutes=minutes)).isoformat()


def _event_payload(candidate_id: str | None, **overrides: object) -> dict:
    payload: dict = {
        "candidate_id": candidate_id,
        "type": "call",
        "title": "Созвон с кандидатом",
        "starts_at": _in(60),
        "ends_at": None,
        "remind_at": _in(45),
        "assignee_user_id": None,
    }
    payload.update(overrides)
    return payload


# --- Linked reminder lifecycle ----------------------------------------------


def test_event_with_remind_at_creates_linked_reminder(
    client: TestClient, db_session: Session
) -> None:
    hr = make_user(db_session, username="hr1", role=UserRole.HR)
    candidate = make_candidate(db_session, owner=hr)
    csrf = _login(client, "hr1")

    created = client.post(
        "/events", json=_event_payload(str(candidate.id)), headers={"X-CSRF-Token": csrf}
    )
    assert created.status_code == 201, created.text
    event_id = created.json()["id"]

    reminders = client.get("/reminders").json()
    assert reminders["total"] == 1
    linked = reminders["items"][0]
    assert linked["event_id"] == event_id
    assert linked["candidate_id"] == str(candidate.id)
    assert linked["status"] == "active"
    assert _ts(linked["due_at"]) == _ts(created.json()["remind_at"])


def test_reminder_type_event_links_at_start(client: TestClient, db_session: Session) -> None:
    hr = make_user(db_session, username="hr1", role=UserRole.HR)
    candidate = make_candidate(db_session, owner=hr)
    csrf = _login(client, "hr1")

    created = client.post(
        "/events",
        json=_event_payload(str(candidate.id), type="reminder", remind_at=None),
        headers={"X-CSRF-Token": csrf},
    )
    assert created.status_code == 201, created.text

    linked = client.get("/reminders").json()["items"][0]
    assert _ts(linked["due_at"]) == _ts(created.json()["starts_at"])


def test_event_without_reminder_moment_has_no_reminder(
    client: TestClient, db_session: Session
) -> None:
    hr = make_user(db_session, username="hr1", role=UserRole.HR)
    candidate = make_candidate(db_session, owner=hr)
    csrf = _login(client, "hr1")

    created = client.post(
        "/events",
        json=_event_payload(str(candidate.id), remind_at=None),
        headers={"X-CSRF-Token": csrf},
    )
    assert created.status_code == 201
    assert client.get("/reminders").json()["total"] == 0


def test_reschedule_moves_linked_reminder_without_duplicates(
    client: TestClient, db_session: Session
) -> None:
    hr = make_user(db_session, username="hr1", role=UserRole.HR)
    candidate = make_candidate(db_session, owner=hr)
    csrf = _login(client, "hr1")
    event_id = client.post(
        "/events", json=_event_payload(str(candidate.id)), headers={"X-CSRF-Token": csrf}
    ).json()["id"]

    moved = client.patch(
        f"/events/{event_id}",
        json={"expected_version": 1, "starts_at": _in(120), "remind_at": _in(100)},
        headers={"X-CSRF-Token": csrf},
    )
    assert moved.status_code == 200, moved.text

    reminders = client.get("/reminders").json()
    assert reminders["total"] == 1  # no duplicate
    assert _ts(reminders["items"][0]["due_at"]) == _ts(moved.json()["remind_at"])


def test_complete_event_completes_linked_reminder(client: TestClient, db_session: Session) -> None:
    hr = make_user(db_session, username="hr1", role=UserRole.HR)
    candidate = make_candidate(db_session, owner=hr)
    csrf = _login(client, "hr1")
    event_id = client.post(
        "/events", json=_event_payload(str(candidate.id)), headers={"X-CSRF-Token": csrf}
    ).json()["id"]

    done = client.patch(
        f"/events/{event_id}",
        json={"expected_version": 1, "status": "completed"},
        headers={"X-CSRF-Token": csrf},
    )
    assert done.status_code == 200, done.text

    linked = client.get("/reminders").json()["items"][0]
    assert linked["status"] == "completed"
    assert linked["completed_at"] is not None


def test_cancel_event_cancels_linked_reminder(client: TestClient, db_session: Session) -> None:
    hr = make_user(db_session, username="hr1", role=UserRole.HR)
    candidate = make_candidate(db_session, owner=hr)
    csrf = _login(client, "hr1")
    event_id = client.post(
        "/events", json=_event_payload(str(candidate.id)), headers={"X-CSRF-Token": csrf}
    ).json()["id"]

    cancelled = client.patch(
        f"/events/{event_id}",
        json={"expected_version": 1, "status": "cancelled"},
        headers={"X-CSRF-Token": csrf},
    )
    assert cancelled.status_code == 200, cancelled.text

    linked = client.get("/reminders").json()["items"][0]
    assert linked["status"] == "cancelled"


def test_clear_remind_at_cancels_linked_reminder(client: TestClient, db_session: Session) -> None:
    hr = make_user(db_session, username="hr1", role=UserRole.HR)
    candidate = make_candidate(db_session, owner=hr)
    csrf = _login(client, "hr1")
    event_id = client.post(
        "/events", json=_event_payload(str(candidate.id)), headers={"X-CSRF-Token": csrf}
    ).json()["id"]

    cleared = client.patch(
        f"/events/{event_id}",
        json={"expected_version": 1, "remind_at": None},
        headers={"X-CSRF-Token": csrf},
    )
    assert cleared.status_code == 200, cleared.text

    linked = client.get("/reminders").json()["items"][0]
    assert linked["status"] == "cancelled"


# --- No duplicates across entry points --------------------------------------


def test_second_reminder_for_same_event_is_rejected(
    client: TestClient, db_session: Session
) -> None:
    hr = make_user(db_session, username="hr1", role=UserRole.HR)
    candidate = make_candidate(db_session, owner=hr)
    csrf = _login(client, "hr1")
    event_id = client.post(
        "/events",
        json=_event_payload(str(candidate.id), remind_at=None),
        headers={"X-CSRF-Token": csrf},
    ).json()["id"]

    first = client.post(
        "/reminders",
        json={
            "title": "Ручное напоминание",
            "due_at": _in(30),
            "timezone": "Europe/Moscow",
            "candidate_id": str(candidate.id),
            "event_id": event_id,
        },
        headers={"X-CSRF-Token": csrf},
    )
    assert first.status_code == 201, first.text

    second = client.post(
        "/reminders",
        json={
            "title": "Дубль",
            "due_at": _in(31),
            "timezone": "Europe/Moscow",
            "event_id": event_id,
        },
        headers={"X-CSRF-Token": csrf},
    )
    assert second.status_code == 422
    assert "уже привязано" in second.json()["detail"]


def test_reminder_event_link_must_be_visible(client: TestClient, db_session: Session) -> None:
    hr1 = make_user(db_session, username="hr1", role=UserRole.HR)
    make_user(db_session, username="hr2", role=UserRole.HR)
    candidate = make_candidate(db_session, owner=hr1)
    csrf1 = _login(client, "hr1")
    event_id = client.post(
        "/events",
        json=_event_payload(str(candidate.id), remind_at=None),
        headers={"X-CSRF-Token": csrf1},
    ).json()["id"]

    csrf2 = _login(client, "hr2")
    foreign = client.post(
        "/reminders",
        json={
            "title": "Чужое",
            "due_at": _in(30),
            "timezone": "Europe/Moscow",
            "event_id": event_id,
        },
        headers={"X-CSRF-Token": csrf2},
    )
    assert foreign.status_code == 422
    assert "Событие не найдено" in foreign.json()["detail"]


def test_reminders_list_filters_by_candidate(client: TestClient, db_session: Session) -> None:
    hr = make_user(db_session, username="hr1", role=UserRole.HR)
    target = make_candidate(db_session, owner=hr, full_name="Целевой Кандидат")
    other = make_candidate(db_session, owner=hr, full_name="Другой Кандидат")
    csrf = _login(client, "hr1")

    for candidate in (target, other):
        client.post(
            "/events",
            json=_event_payload(str(candidate.id), type="reminder", remind_at=None),
            headers={"X-CSRF-Token": csrf},
        )

    only_target = client.get(f"/reminders?candidate_id={target.id}").json()
    assert only_target["total"] == 1
    assert only_target["items"][0]["candidate_id"] == str(target.id)


# --- Optional candidate link on events ---------------------------------------


def test_event_without_candidate_is_personal_and_scoped(
    client: TestClient, db_session: Session
) -> None:
    make_user(db_session, username="hr1", role=UserRole.HR)
    make_user(db_session, username="hr2", role=UserRole.HR)
    make_user(db_session, username="mgr", role=UserRole.MANAGER)
    csrf1 = _login(client, "hr1")

    created = client.post(
        "/events",
        json=_event_payload(None, type="reminder", remind_at=None),
        headers={"X-CSRF-Token": csrf1},
    )
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["candidate_id"] is None
    assert body["candidate_full_name"] == ""

    # The author sees it in the calendar and in reminders.
    listing = client.get("/events").json()
    assert listing["total"] == 1
    assert client.get("/reminders").json()["total"] == 1

    # A foreign HR does not.
    _login(client, "hr2")
    assert client.get("/events").json()["total"] == 0
    assert client.get(f"/events/{body['id']}").status_code == 404

    # A manager sees every event.
    _login(client, "mgr")
    assert client.get("/events").json()["total"] == 1


def test_event_candidate_must_be_accessible(client: TestClient, db_session: Session) -> None:
    hr1 = make_user(db_session, username="hr1", role=UserRole.HR)
    hr2 = make_user(db_session, username="hr2", role=UserRole.HR)
    foreign = make_candidate(db_session, owner=hr2)
    csrf1 = _login(client, "hr1")

    response = client.post(
        "/events", json=_event_payload(str(foreign.id)), headers={"X-CSRF-Token": csrf1}
    )
    assert response.status_code == 404
    assert hr1.role == UserRole.HR  # sanity: rights are enforced, not UI


# --- Assignee directory (admin/manager event creation) -----------------------


def test_admin_sees_active_hr_assignees_and_can_save_event(
    client: TestClient, db_session: Session
) -> None:
    make_user(db_session, username="boss", role=UserRole.ADMIN)
    hr = make_user(db_session, username="hr1", role=UserRole.HR, full_name="Иванова Ирина")
    make_user(db_session, username="hr_off", role=UserRole.HR, is_active=False)
    make_user(db_session, username="mgr", role=UserRole.MANAGER)
    candidate = make_candidate(db_session, owner=hr)
    csrf = _login(client, "boss")

    directory = client.get("/admin/users/hr").json()
    assert directory["total"] == 1  # inactive HR is not offered
    assert directory["items"][0]["username"] == "hr1"

    # Saving works once an active HR is selected (the acceptance scenario).
    created = client.post(
        "/events",
        json=_event_payload(str(candidate.id), assignee_user_id=str(hr.id)),
        headers={"X-CSRF-Token": csrf},
    )
    assert created.status_code == 201, created.text
    assert created.json()["assignee_user_id"] == str(hr.id)

    # Without an explicit HR assignee the server refuses with a clear error.
    missing = client.post(
        "/events", json=_event_payload(str(candidate.id)), headers={"X-CSRF-Token": csrf}
    )
    assert missing.status_code == 422
    assert "исполнителя" in missing.json()["detail"]

    # A manager is not a valid assignee.
    manager = make_user(db_session, username="mgr2", role=UserRole.MANAGER)
    wrong_role = client.post(
        "/events",
        json=_event_payload(str(candidate.id), assignee_user_id=str(manager.id)),
        headers={"X-CSRF-Token": csrf},
    )
    assert wrong_role.status_code == 422
    assert "ролью HR" in wrong_role.json()["detail"]


def test_hr_directory_is_visible_without_admin_role(
    client: TestClient, db_session: Session
) -> None:
    make_user(db_session, username="hr1", role=UserRole.HR)
    manager: User = make_user(db_session, username="mgr", role=UserRole.MANAGER)
    _login(client, "mgr")

    directory = client.get("/admin/users/hr")
    assert directory.status_code == 200
    assert [item["username"] for item in directory.json()["items"]] == ["hr1"]
    assert manager.role == UserRole.MANAGER  # any authenticated role may read
