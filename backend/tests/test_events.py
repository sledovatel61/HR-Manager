"""Unit tests for calendar events (in-memory SQLite).

Covers CRUD, lifecycle transitions, period/timezone semantics, server-side
filters/sort/pagination, role rights, soft-deleted candidates, invalid
assignees, business history, PII-free audit and the transactional
atomicity of mutations. PostgreSQL integration mirrors run in
``tests/test_integration_events.py`` (including the concurrent-update and
row-lock scenarios).
"""

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta, timezone
from typing import Any
from urllib.parse import quote
from uuid import UUID, uuid4

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import (
    AuditAction,
    AuditEvent,
    Event,
    EventHistory,
    EventHistoryKind,
    EventType,
    UserRole,
)
from app.routers.auth import reset_login_limiter
from app.utils import utc_now
from tests.conftest import FIXTURE_PASSWORD, make_candidate, make_event, make_user


@pytest.fixture(autouse=True)
def _clean_limiter() -> Iterator[None]:
    reset_login_limiter()
    yield
    reset_login_limiter()


def _login(client: TestClient, username: str, password: str = FIXTURE_PASSWORD) -> httpx.Response:
    return client.post("/auth/login", json={"username": username, "password": password})


def _csrf(response: httpx.Response) -> str:
    return response.json()["csrf_token"]


def _in(minutes: int) -> str:
    return (utc_now() + timedelta(minutes=minutes)).isoformat()


def _q(value: str) -> str:
    """Percent-encode an ISO timestamp for query strings ('+' breaks URLs)."""
    return quote(value, safe="")


def _payload(candidate_id: str, **overrides: object) -> dict:
    payload: dict = {
        "candidate_id": candidate_id,
        "type": "call",
        "title": "Созвон с кандидатом",
        "starts_at": _in(60),
        "ends_at": None,
        "remind_at": None,
        "assignee_user_id": None,
    }
    payload.update(overrides)
    return payload


# --- CRUD --------------------------------------------------------------------


def test_create_read_event(client: TestClient, db_session: Session) -> None:
    hr1 = make_user(db_session, username="hr1", role=UserRole.HR)
    candidate = make_candidate(db_session, owner=hr1)
    csrf = _csrf(_login(client, "hr1"))

    created = client.post(
        "/events",
        json=_payload(
            str(candidate.id),
            ends_at=_in(90),
            remind_at=_in(30),
        ),
        headers={"X-CSRF-Token": csrf},
    )
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["type"] == "call"
    assert body["status"] == "scheduled"
    assert body["version"] == 1
    assert body["assignee_username"] == "hr1"
    assert body["author_username"] == "hr1"
    assert body["candidate_full_name"] == candidate.full_name
    assert body["completed_at"] is None
    # The API returns parseable ISO timestamps; UTC normalization of input
    # offsets is covered by the dedicated timezone test (SQLite reads drop
    # the offset, PostgreSQL keeps +00:00).
    datetime.fromisoformat(body["starts_at"])

    fetched = client.get(f"/events/{body['id']}")
    assert fetched.status_code == 200
    assert fetched.json()["id"] == body["id"]


def test_create_requires_csrf(client: TestClient, db_session: Session) -> None:
    hr1 = make_user(db_session, username="hr1", role=UserRole.HR)
    candidate = make_candidate(db_session, owner=hr1)
    _login(client, "hr1")
    response = client.post("/events", json=_payload(str(candidate.id)))
    assert response.status_code == 403


def test_create_validation_errors(client: TestClient, db_session: Session) -> None:
    hr1 = make_user(db_session, username="hr1", role=UserRole.HR)
    candidate = make_candidate(db_session, owner=hr1)
    csrf = _csrf(_login(client, "hr1"))

    # Blank title.
    blank = client.post(
        "/events", json=_payload(str(candidate.id), title="   "), headers={"X-CSRF-Token": csrf}
    )
    assert blank.status_code == 422

    # ends_at before starts_at.
    wrong_order = client.post(
        "/events",
        json=_payload(str(candidate.id), starts_at=_in(60), ends_at=_in(10)),
        headers={"X-CSRF-Token": csrf},
    )
    assert wrong_order.status_code == 422

    # remind_at after starts_at.
    late_remind = client.post(
        "/events",
        json=_payload(str(candidate.id), starts_at=_in(60), remind_at=_in(120)),
        headers={"X-CSRF-Token": csrf},
    )
    assert late_remind.status_code == 422

    # remind_at on a reminder-type event is the start itself.
    reminder_with_remind = client.post(
        "/events",
        json=_payload(str(candidate.id), type="reminder", remind_at=_in(10)),
        headers={"X-CSRF-Token": csrf},
    )
    assert reminder_with_remind.status_code == 422

    # Unknown type/status values are rejected by the closed vocabulary.
    bad_type = client.post(
        "/events", json=_payload(str(candidate.id), type="meeting"), headers={"X-CSRF-Token": csrf}
    )
    assert bad_type.status_code == 422


def test_timezone_input_is_normalized_to_utc(client: TestClient, db_session: Session) -> None:
    hr1 = make_user(db_session, username="hr1", role=UserRole.HR)
    candidate = make_candidate(db_session, owner=hr1)
    csrf = _csrf(_login(client, "hr1"))

    # +03:00 input → the same instant, normalized to UTC by the backend.
    instant = utc_now() + timedelta(hours=2)
    moscow = instant.astimezone(timezone(timedelta(hours=3))).isoformat()
    response = client.post(
        "/events",
        json=_payload(str(candidate.id), starts_at=moscow),
        headers={"X-CSRF-Token": csrf},
    )
    assert response.status_code == 201
    returned = response.json()["starts_at"]
    # SQLite drops the offset on read (naive UTC); PostgreSQL keeps it aware.
    parsed = datetime.fromisoformat(returned)
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(UTC).replace(tzinfo=None)
    expected = instant.replace(tzinfo=None)
    assert abs((parsed - expected).total_seconds()) < 60


# --- Lifecycle transitions ---------------------------------------------------


def test_complete_and_postpone_lifecycle(client: TestClient, db_session: Session) -> None:
    hr1 = make_user(db_session, username="hr1", role=UserRole.HR)
    candidate = make_candidate(db_session, owner=hr1)
    csrf = _csrf(_login(client, "hr1"))
    event = make_event(db_session, candidate=candidate, author=hr1, assignee=hr1)

    # Postpone without a new start date → 422.
    no_date = client.patch(
        f"/events/{event.id}",
        json={"expected_version": 1, "status": "postponed"},
        headers={"X-CSRF-Token": csrf},
    )
    assert no_date.status_code == 422

    # Postpone with a new date.
    postponed = client.patch(
        f"/events/{event.id}",
        json={"expected_version": 1, "status": "postponed", "starts_at": _in(240)},
        headers={"X-CSRF-Token": csrf},
    )
    assert postponed.status_code == 200, postponed.text
    assert postponed.json()["status"] == "postponed"
    assert postponed.json()["version"] == 2

    # Re-plan (postponed → scheduled) is allowed.
    replanned = client.patch(
        f"/events/{event.id}",
        json={"expected_version": 2, "status": "scheduled"},
        headers={"X-CSRF-Token": csrf},
    )
    assert replanned.status_code == 200
    assert replanned.json()["status"] == "scheduled"
    assert replanned.json()["version"] == 3

    # Complete.
    done = client.patch(
        f"/events/{event.id}",
        json={"expected_version": 3, "status": "completed"},
        headers={"X-CSRF-Token": csrf},
    )
    assert done.status_code == 200
    assert done.json()["status"] == "completed"
    assert done.json()["completed_at"] is not None
    assert done.json()["version"] == 4

    # Completed is terminal: any further PATCH → 409.
    reopen = client.patch(
        f"/events/{event.id}",
        json={"expected_version": 4, "status": "scheduled"},
        headers={"X-CSRF-Token": csrf},
    )
    assert reopen.status_code == 409


def test_reschedule_and_edit_title(client: TestClient, db_session: Session) -> None:
    hr1 = make_user(db_session, username="hr1", role=UserRole.HR)
    candidate = make_candidate(db_session, owner=hr1)
    csrf = _csrf(_login(client, "hr1"))
    event = make_event(db_session, candidate=candidate, author=hr1, assignee=hr1)

    response = client.patch(
        f"/events/{event.id}",
        json={"expected_version": 1, "starts_at": _in(300), "title": "Новое название"},
        headers={"X-CSRF-Token": csrf},
    )
    assert response.status_code == 200
    assert response.json()["title"] == "Новое название"
    assert response.json()["version"] == 2


def test_patch_with_no_changes_rejected(client: TestClient, db_session: Session) -> None:
    hr1 = make_user(db_session, username="hr1", role=UserRole.HR)
    candidate = make_candidate(db_session, owner=hr1)
    csrf = _csrf(_login(client, "hr1"))
    event = make_event(db_session, candidate=candidate, author=hr1, assignee=hr1)

    response = client.patch(
        f"/events/{event.id}",
        json={"expected_version": 1},
        headers={"X-CSRF-Token": csrf},
    )
    assert response.status_code == 422


def test_stale_expected_version_gets_409(client: TestClient, db_session: Session) -> None:
    hr1 = make_user(db_session, username="hr1", role=UserRole.HR)
    candidate = make_candidate(db_session, owner=hr1)
    csrf = _csrf(_login(client, "hr1"))
    event = make_event(db_session, candidate=candidate, author=hr1, assignee=hr1)

    first = client.patch(
        f"/events/{event.id}",
        json={"expected_version": 1, "title": "Первое изменение"},
        headers={"X-CSRF-Token": csrf},
    )
    assert first.status_code == 200

    stale = client.patch(
        f"/events/{event.id}",
        json={"expected_version": 1, "title": "Затирание"},
        headers={"X-CSRF-Token": csrf},
    )
    assert stale.status_code == 409
    # Nothing was overwritten.
    assert client.get(f"/events/{event.id}").json()["title"] == "Первое изменение"


# --- Filters, sorting, pagination -------------------------------------------


def test_period_bounds_are_half_open(client: TestClient, db_session: Session) -> None:
    hr1 = make_user(db_session, username="hr1", role=UserRole.HR)
    candidate = make_candidate(db_session, owner=hr1)
    _login(client, "hr1")

    at_minus10 = make_event(
        db_session,
        candidate=candidate,
        author=hr1,
        assignee=hr1,
        starts_at=utc_now() - timedelta(minutes=10),
    )
    spanning = make_event(
        db_session,
        candidate=candidate,
        author=hr1,
        assignee=hr1,
        starts_at=utc_now() - timedelta(minutes=30),
        ends_at=utc_now() + timedelta(minutes=30),
    )
    at_plus60 = make_event(
        db_session,
        candidate=candidate,
        author=hr1,
        assignee=hr1,
        starts_at=utc_now() + timedelta(minutes=60),
    )

    from_iso = _q((utc_now() - timedelta(minutes=15)).isoformat())
    to_iso = _q((utc_now() + timedelta(minutes=15)).isoformat())
    period = client.get(f"/events?from={from_iso}&to={to_iso}")
    assert period.status_code == 200
    ids = {item["id"] for item in period.json()["items"]}
    # The spanning event overlaps even though it started before `from`;
    # the -10 event lies inside; +60 is outside.
    assert ids == {str(spanning.id), str(at_minus10.id)}
    assert str(at_plus60.id) not in ids

    invalid = client.get(f"/events?from={_q(_in(60))}&to={_q(_in(10))}")
    assert invalid.status_code == 422


def test_filters_and_stable_sorting(client: TestClient, db_session: Session) -> None:
    hr1 = make_user(db_session, username="hr1", role=UserRole.HR)
    candidate = make_candidate(db_session, owner=hr1)
    _login(client, "hr1")

    later = make_event(
        db_session,
        candidate=candidate,
        author=hr1,
        assignee=hr1,
        type_=EventType.CALL,
        starts_at=utc_now() + timedelta(hours=5),
    )
    earlier = make_event(
        db_session,
        candidate=candidate,
        author=hr1,
        assignee=hr1,
        type_=EventType.INTERVIEW,
        starts_at=utc_now() + timedelta(hours=1),
    )
    reminder = make_event(
        db_session,
        candidate=candidate,
        author=hr1,
        assignee=hr1,
        type_=EventType.REMINDER,
        starts_at=utc_now() + timedelta(hours=3),
    )

    by_type = client.get("/events?type=interview")
    assert [item["id"] for item in by_type.json()["items"]] == [str(earlier.id)]

    sorted_desc = client.get("/events?sort=starts_at&direction=desc")
    assert [item["id"] for item in sorted_desc.json()["items"]] == [
        str(later.id),
        str(reminder.id),
        str(earlier.id),
    ]

    paged = client.get("/events?limit=2&offset=1")
    assert paged.json()["total"] == 3
    assert len(paged.json()["items"]) == 2


def test_reminder_moment_filters(client: TestClient, db_session: Session) -> None:
    hr1 = make_user(db_session, username="hr1", role=UserRole.HR)
    candidate = make_candidate(db_session, owner=hr1)
    _login(client, "hr1")

    with_remind = make_event(
        db_session,
        candidate=candidate,
        author=hr1,
        assignee=hr1,
        starts_at=utc_now() + timedelta(hours=4),
        remind_at=utc_now() + timedelta(minutes=30),
    )
    without_remind = make_event(
        db_session,
        candidate=candidate,
        author=hr1,
        assignee=hr1,
        starts_at=utc_now() + timedelta(hours=4),
    )
    reminder_type = make_event(
        db_session,
        candidate=candidate,
        author=hr1,
        assignee=hr1,
        type_=EventType.REMINDER,
        starts_at=utc_now() + timedelta(hours=2),
    )

    upcoming_reminders = client.get(
        f"/events?remind_from={_q((utc_now() + timedelta(minutes=1)).isoformat())}&status=scheduled"
    )
    ids = {item["id"] for item in upcoming_reminders.json()["items"]}
    assert ids == {str(with_remind.id), str(reminder_type.id)}
    assert str(without_remind.id) not in ids


# --- Rights ------------------------------------------------------------------


def test_hr_sees_only_own_candidates_events(client: TestClient, db_session: Session) -> None:
    hr1 = make_user(db_session, username="hr1", role=UserRole.HR)
    hr2 = make_user(db_session, username="hr2", role=UserRole.HR)
    own_candidate = make_candidate(db_session, owner=hr1)
    foreign_candidate = make_candidate(db_session, owner=hr2, full_name="Чужой")
    own_event = make_event(db_session, candidate=own_candidate, author=hr1, assignee=hr1)
    make_event(db_session, candidate=foreign_candidate, author=hr2, assignee=hr2)

    _login(client, "hr1")
    listing = client.get("/events")
    assert listing.json()["total"] == 1
    assert listing.json()["items"][0]["id"] == str(own_event.id)

    # Foreign event via direct URL → 404 (existence hidden).
    foreign_event = db_session.scalars(
        select(Event).where(Event.candidate_id == foreign_candidate.id)
    ).one()
    assert client.get(f"/events/{foreign_event.id}").status_code == 404
    assert client.get(f"/events/{foreign_event.id}/history").status_code == 404


def test_manager_and_admin_see_everything_and_filter_by_owner(
    client: TestClient, db_session: Session
) -> None:
    hr1 = make_user(db_session, username="hr1", role=UserRole.HR)
    hr2 = make_user(db_session, username="hr2", role=UserRole.HR)
    make_user(db_session, username="mgr", role=UserRole.MANAGER)
    candidate1 = make_candidate(db_session, owner=hr1)
    candidate2 = make_candidate(db_session, owner=hr2)
    make_event(db_session, candidate=candidate1, author=hr1, assignee=hr1)
    make_event(db_session, candidate=candidate2, author=hr2, assignee=hr2)

    _login(client, "mgr")
    assert client.get("/events").json()["total"] == 2
    only_hr1 = client.get(f"/events?owner_id={hr1.id}")
    assert only_hr1.json()["total"] == 1
    assert only_hr1.json()["items"][0]["candidate_id"] == str(candidate1.id)


def test_events_require_authentication(client: TestClient, db_session: Session) -> None:
    hr1 = make_user(db_session, username="hr1", role=UserRole.HR)
    candidate = make_candidate(db_session, owner=hr1)
    make_event(db_session, candidate=candidate, author=hr1, assignee=hr1)
    assert client.get("/events").status_code == 401


def test_create_event_for_foreign_candidate_hidden_as_404(
    client: TestClient, db_session: Session
) -> None:
    make_user(db_session, username="hr1", role=UserRole.HR)
    hr2 = make_user(db_session, username="hr2", role=UserRole.HR)
    foreign = make_candidate(db_session, owner=hr2)
    csrf = _csrf(_login(client, "hr1"))

    response = client.post(
        "/events", json=_payload(str(foreign.id)), headers={"X-CSRF-Token": csrf}
    )
    assert response.status_code == 404


def test_events_of_soft_deleted_candidate_are_hidden_for_everyone(
    client: TestClient, db_session: Session
) -> None:
    hr1 = make_user(db_session, username="hr1", role=UserRole.HR)
    make_user(db_session, username="adm", role=UserRole.ADMIN)
    candidate = make_candidate(db_session, owner=hr1)
    event = make_event(db_session, candidate=candidate, author=hr1, assignee=hr1)
    # Soft-delete the candidate via the API (HR).
    hr_csrf = _csrf(_login(client, "hr1"))
    assert (
        client.delete(f"/candidates/{candidate.id}", headers={"X-CSRF-Token": hr_csrf}).status_code
        == 200
    )

    # The owner HR no longer sees the event anywhere.
    assert client.get("/events").json()["total"] == 0
    assert client.get(f"/events/{event.id}").status_code == 404
    assert client.get(f"/events/{event.id}/history").status_code == 404

    # Admin shares the same policy through the events API.
    _login(client, "adm")
    assert client.get("/events").json()["total"] == 0
    assert client.get(f"/events/{event.id}").status_code == 404


def test_invalid_assignee_rules(client: TestClient, db_session: Session) -> None:
    hr1 = make_user(db_session, username="hr1", role=UserRole.HR)
    hr2 = make_user(db_session, username="hr2", role=UserRole.HR)
    inactive = make_user(db_session, username="hr_off", role=UserRole.HR, is_active=False)
    manager = make_user(db_session, username="mgr", role=UserRole.MANAGER)
    candidate = make_candidate(db_session, owner=hr1)

    # HR may only assign themselves (requesting another HR → 403).
    csrf = _csrf(_login(client, "hr1"))
    foreign_assignee = client.post(
        "/events",
        json=_payload(str(candidate.id), assignee_user_id=str(hr2.id)),
        headers={"X-CSRF-Token": csrf},
    )
    assert foreign_assignee.status_code == 403

    # Manager may assign an active HR only.
    mgr_csrf = _csrf(_login(client, "mgr"))
    inactive_assignee = client.post(
        "/events",
        json=_payload(str(candidate.id), assignee_user_id=str(inactive.id)),
        headers={"X-CSRF-Token": mgr_csrf},
    )
    assert inactive_assignee.status_code == 422

    manager_assignee = client.post(
        "/events",
        json=_payload(str(candidate.id), assignee_user_id=str(manager.id)),
        headers={"X-CSRF-Token": mgr_csrf},
    )
    assert manager_assignee.status_code == 422

    ghost = client.post(
        "/events",
        json=_payload(str(candidate.id), assignee_user_id=str(uuid4())),
        headers={"X-CSRF-Token": mgr_csrf},
    )
    assert ghost.status_code == 422

    # Manager CAN assign an active HR.
    ok = client.post(
        "/events",
        json=_payload(str(candidate.id), assignee_user_id=str(hr2.id)),
        headers={"X-CSRF-Token": mgr_csrf},
    )
    assert ok.status_code == 201
    assert ok.json()["assignee_username"] == "hr2"


def test_hr_cannot_reassign_own_event_to_someone_else(
    client: TestClient, db_session: Session
) -> None:
    hr1 = make_user(db_session, username="hr1", role=UserRole.HR)
    hr2 = make_user(db_session, username="hr2", role=UserRole.HR)
    candidate = make_candidate(db_session, owner=hr1)
    event = make_event(db_session, candidate=candidate, author=hr1, assignee=hr1)
    csrf = _csrf(_login(client, "hr1"))

    response = client.patch(
        f"/events/{event.id}",
        json={"expected_version": 1, "assignee_user_id": str(hr2.id)},
        headers={"X-CSRF-Token": csrf},
    )
    assert response.status_code == 403


def test_transferred_candidate_visibility_switches(client: TestClient, db_session: Session) -> None:
    """After a candidate transfer the old HR loses event access; the new HR
    gains it and the immutable history is preserved."""
    hr1 = make_user(db_session, username="hr1", role=UserRole.HR)
    hr2 = make_user(db_session, username="hr2", role=UserRole.HR)
    candidate = make_candidate(db_session, owner=hr1)
    csrf = _csrf(_login(client, "hr1"))
    created = client.post(
        "/events", json=_payload(str(candidate.id)), headers={"X-CSRF-Token": csrf}
    )
    assert created.status_code == 201
    event_id = created.json()["id"]

    transferred = client.post(
        f"/candidates/{candidate.id}/transfer",
        json={"new_owner_user_id": str(hr2.id), "reason": "Передача нагрузки"},
        headers={"X-CSRF-Token": csrf},
    )
    assert transferred.status_code == 200

    # Old HR: no card, no events, no history.
    assert client.get("/events").json()["total"] == 0
    assert client.get(f"/events/{event_id}").status_code == 404

    # New HR sees the event and its history.
    _login(client, "hr2")
    assert client.get(f"/events/{event_id}").status_code == 200
    history = client.get(f"/events/{event_id}/history")
    assert history.status_code == 200
    assert history.json()["items"][0]["kind"] == "created"


# --- Business history & audit -----------------------------------------------


def test_history_tracks_lifecycle_without_pii(client: TestClient, db_session: Session) -> None:
    hr1 = make_user(db_session, username="hr1", role=UserRole.HR)
    candidate = make_candidate(db_session, owner=hr1)
    csrf = _csrf(_login(client, "hr1"))
    created = client.post(
        "/events", json=_payload(str(candidate.id)), headers={"X-CSRF-Token": csrf}
    )
    assert created.status_code == 201
    event_id = created.json()["id"]

    client.patch(
        f"/events/{event_id}",
        json={"expected_version": 1, "starts_at": _in(300), "title": "Секретный заголовок"},
        headers={"X-CSRF-Token": csrf},
    )
    client.patch(
        f"/events/{event_id}",
        json={"expected_version": 2, "status": "completed"},
        headers={"X-CSRF-Token": csrf},
    )

    entries = db_session.scalars(
        select(EventHistory)
        .where(EventHistory.event_id == UUID(event_id))
        .order_by(EventHistory.created_at)
    ).all()
    assert [entry.kind for entry in entries] == [
        EventHistoryKind.CREATED,
        EventHistoryKind.RESCHEDULED,
        EventHistoryKind.COMPLETED,
    ]
    # Title content is never copied into history — only the changed flag.
    assert entries[1].title_changed is True
    assert entries[1].starts_at_old is not None
    assert entries[1].starts_at_new is not None
    assert entries[2].status_new == "completed"

    # Audit events exist with safe details only (no title/note values, no PII).
    audit_entries = db_session.scalars(
        select(AuditEvent)
        .where(AuditEvent.candidate_id == candidate.id)
        .order_by(AuditEvent.created_at)
    ).all()
    actions = {entry.action for entry in audit_entries}
    assert AuditAction.EVENT_CREATED in actions
    assert AuditAction.EVENT_RESCHEDULED in actions
    assert AuditAction.EVENT_COMPLETED in actions
    for entry in audit_entries:
        details = entry.details or ""
        assert "Секретный" not in details
        assert "Иванов" not in details
        assert candidate.phone is None or candidate.phone not in details


def test_mutation_is_atomic_when_audit_write_fails(
    client: TestClient, db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A failed audit write must roll back the whole mutation.

    The event update, the immutable business-history row and the audit event
    are staged in ONE transaction and committed with a single commit. When
    the audit part fails (here: after the audit row has been flushed), the
    rollback must leave the event untouched, add no history rows and no
    audit events.
    """
    from app.audit import record_event as real_record_event
    from app.routers import events as events_router

    hr1 = make_user(db_session, username="hr1", role=UserRole.HR)
    candidate = make_candidate(db_session, owner=hr1)
    event = make_event(db_session, candidate=candidate, author=hr1, assignee=hr1)
    csrf = _csrf(_login(client, "hr1"))

    def failing_record_event(db: Session, *args: Any, **kwargs: Any) -> None:
        real_record_event(db, *args, **kwargs)
        db.flush()
        raise RuntimeError("simulated audit write failure")

    monkeypatch.setattr(events_router, "record_event", failing_record_event)

    with pytest.raises(RuntimeError, match="simulated audit write failure"):
        client.patch(
            f"/events/{event.id}",
            json={"expected_version": 1, "title": "Не должно сохраниться"},
            headers={"X-CSRF-Token": csrf},
        )

    db_session.expire_all()
    stored = db_session.get(Event, event.id)
    assert stored is not None
    assert stored.title == "Созвон"  # unchanged
    assert stored.version == 1
    history_count = db_session.scalar(
        select(func.count()).select_from(EventHistory).where(EventHistory.event_id == event.id)
    )
    assert history_count == 0  # the fixture creates no history; the failed PATCH added none
    assert (
        db_session.scalar(
            select(func.count())
            .select_from(AuditEvent)
            .where(AuditEvent.action == AuditAction.EVENT_UPDATED)
        )
        == 0
    )


def test_create_mutation_is_atomic_when_audit_write_fails(
    client: TestClient, db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.audit import record_event as real_record_event
    from app.routers import events as events_router

    hr1 = make_user(db_session, username="hr1", role=UserRole.HR)
    candidate = make_candidate(db_session, owner=hr1)
    csrf = _csrf(_login(client, "hr1"))

    def failing_record_event(db: Session, *args: Any, **kwargs: Any) -> None:
        real_record_event(db, *args, **kwargs)
        db.flush()
        raise RuntimeError("simulated audit write failure")

    monkeypatch.setattr(events_router, "record_event", failing_record_event)

    with pytest.raises(RuntimeError, match="simulated audit write failure"):
        client.post("/events", json=_payload(str(candidate.id)), headers={"X-CSRF-Token": csrf})

    assert db_session.scalar(select(func.count()).select_from(Event)) == 0
    assert db_session.scalar(select(func.count()).select_from(EventHistory)) == 0


def test_event_not_overdue_when_scheduled_in_future(
    client: TestClient, db_session: Session
) -> None:
    hr1 = make_user(db_session, username="hr1", role=UserRole.HR)
    candidate = make_candidate(db_session, owner=hr1)
    future_event = make_event(db_session, candidate=candidate, author=hr1, assignee=hr1)
    past_event = make_event(
        db_session,
        candidate=candidate,
        author=hr1,
        assignee=hr1,
        starts_at=utc_now() - timedelta(hours=1),
    )
    _login(client, "hr1")

    overdue = client.get(f"/events?status=scheduled&to={_q(utc_now().isoformat())}")
    overdue_ids = {item["id"] for item in overdue.json()["items"]}
    assert str(past_event.id) in overdue_ids
    assert str(future_event.id) not in overdue_ids

    upcoming = client.get(f"/events?status=scheduled&from={_q(utc_now().isoformat())}")
    upcoming_ids = {item["id"] for item in upcoming.json()["items"]}
    assert str(future_event.id) in upcoming_ids
    assert str(past_event.id) not in upcoming_ids


# --- Regression tests (orchestrator review of PR #7) ------------------------


def test_manager_admin_assignee_is_required(client: TestClient, db_session: Session) -> None:
    """manager/admin must pick an active HR assignee explicitly.

    Regression: _resolve_assignee used to fall back to the current user, so
    a manager could create an event assigned to themselves (a non-HR role),
    violating the «исполнитель — только активный HR» contract.
    """
    hr1 = make_user(db_session, username="hr1", role=UserRole.HR)
    make_user(db_session, username="mgr", role=UserRole.MANAGER)
    make_user(db_session, username="adm", role=UserRole.ADMIN)
    candidate = make_candidate(db_session, owner=hr1)

    mgr_csrf = _csrf(_login(client, "mgr"))
    missing = client.post(
        "/events", json=_payload(str(candidate.id)), headers={"X-CSRF-Token": mgr_csrf}
    )
    assert missing.status_code == 422

    adm_csrf = _csrf(_login(client, "adm"))
    missing_admin = client.post(
        "/events", json=_payload(str(candidate.id)), headers={"X-CSRF-Token": adm_csrf}
    )
    assert missing_admin.status_code == 422

    # With an explicit active HR the creation works (re-login: the admin
    # session replaced the manager's cookie).
    mgr_csrf = _csrf(_login(client, "mgr"))
    ok = client.post(
        "/events",
        json=_payload(str(candidate.id), assignee_user_id=str(hr1.id)),
        headers={"X-CSRF-Token": mgr_csrf},
    )
    assert ok.status_code == 201
    assert ok.json()["assignee_username"] == "hr1"
    assert ok.json()["assignee_user_id"] == str(hr1.id)


def test_patch_clears_nullable_fields(client: TestClient, db_session: Session) -> None:
    """Explicit null clears note/ends_at/remind_at (model_fields_set).

    Regression: None previously meant «not provided», so a PATCH that only
    cleared fields was rejected with 422 «Нет изменений для применения».
    """
    hr1 = make_user(db_session, username="hr1", role=UserRole.HR)
    candidate = make_candidate(db_session, owner=hr1)
    csrf = _csrf(_login(client, "hr1"))

    # Field-by-field clearing.
    for field in ("note", "ends_at", "remind_at"):
        event = make_event(
            db_session,
            candidate=candidate,
            author=hr1,
            assignee=hr1,
            note="Старая заметка",
            starts_at=utc_now() + timedelta(hours=2),
            ends_at=utc_now() + timedelta(hours=3),
            remind_at=utc_now() + timedelta(hours=1),
        )
        response = client.patch(
            f"/events/{event.id}",
            json={"expected_version": 1, field: None},
            headers={"X-CSRF-Token": csrf},
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body[field] is None
        assert body["version"] == 2
        db_session.expire_all()
        stored = db_session.get(Event, event.id)
        assert getattr(stored, field) is None

    # Several fields in one PATCH.
    event = make_event(
        db_session,
        candidate=candidate,
        author=hr1,
        assignee=hr1,
        note="Заметка",
        starts_at=utc_now() + timedelta(hours=2),
        ends_at=utc_now() + timedelta(hours=3),
        remind_at=utc_now() + timedelta(hours=1),
    )
    response = client.patch(
        f"/events/{event.id}",
        json={"expected_version": 1, "note": None, "ends_at": None, "remind_at": None},
        headers={"X-CSRF-Token": csrf},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["note"] is None and body["ends_at"] is None and body["remind_at"] is None

    # Clearing an already-empty field is a no-op and stays rejected.
    noop = client.patch(
        f"/events/{event.id}",
        json={"expected_version": 2, "note": None},
        headers={"X-CSRF-Token": csrf},
    )
    assert noop.status_code == 422

    # starts_at is not nullable: explicit null is rejected.
    event2 = make_event(db_session, candidate=candidate, author=hr1, assignee=hr1)
    bad_start = client.patch(
        f"/events/{event2.id}",
        json={"expected_version": 1, "starts_at": None},
        headers={"X-CSRF-Token": csrf},
    )
    assert bad_start.status_code == 422


def test_patch_clear_records_history_and_audit_without_pii(
    client: TestClient, db_session: Session
) -> None:
    """Clearing fields writes business history with correct old/new values
    and audit details that contain only safe field names."""
    hr1 = make_user(db_session, username="hr1", role=UserRole.HR)
    candidate = make_candidate(db_session, owner=hr1)
    csrf = _csrf(_login(client, "hr1"))

    created = client.post(
        "/events",
        json=_payload(
            str(candidate.id),
            note="Секретная заметка",
            ends_at=_in(90),
            remind_at=_in(30),
        ),
        headers={"X-CSRF-Token": csrf},
    )
    assert created.status_code == 201
    event_id = created.json()["id"]

    cleared = client.patch(
        f"/events/{event_id}",
        json={"expected_version": 1, "note": None, "ends_at": None, "remind_at": None},
        headers={"X-CSRF-Token": csrf},
    )
    assert cleared.status_code == 200

    entries = db_session.scalars(
        select(EventHistory)
        .where(EventHistory.event_id == UUID(event_id))
        .order_by(EventHistory.created_at)
    ).all()
    assert [entry.kind for entry in entries] == [
        EventHistoryKind.CREATED,
        EventHistoryKind.RESCHEDULED,
    ]
    change = entries[1]
    assert change.note_changed is True
    assert change.ends_at_old is not None and change.ends_at_new is None
    assert change.remind_at_old is not None and change.remind_at_new is None

    audit_entries = db_session.scalars(
        select(AuditEvent).where(AuditEvent.candidate_id == candidate.id)
    ).all()
    clear_audit = [e for e in audit_entries if e.action == AuditAction.EVENT_RESCHEDULED]
    assert len(clear_audit) == 1
    details = clear_audit[0].details or ""
    assert "fields=" in details
    for name in ("ends_at", "remind_at", "note"):
        assert name in details
    # No values, no PII, no note content.
    assert "Секретная" not in details
    assert candidate.phone is None or candidate.phone not in details
