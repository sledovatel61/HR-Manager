"""Event/transfer notification planning through the API (SQLite, outbox).

Business operations commit outbox rows in the SAME transaction: these tests
assert the exact planned rows (assigned/approaching/overdue/rescheduled/
cancelled/transferred) and the cancel-on-reschedule behaviour.
"""

from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import (
    Candidate,
    CandidateSource,
    CandidateStage,
    DeliveryStatus,
    NotificationOutbox,
    NotificationType,
    User,
    UserRole,
)
from app.utils import utc_now
from tests.conftest import FIXTURE_PASSWORD, make_user

NOW = datetime(2026, 9, 4, 12, 0, 0, tzinfo=UTC)


def _login(client: TestClient, username: str) -> str:
    response = client.post("/auth/login", json={"username": username, "password": FIXTURE_PASSWORD})
    assert response.status_code == 200, response.text
    return response.json()["csrf_token"]


def _fixture(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> tuple[User, User, Candidate, datetime]:
    owner = make_user(db_session, username="owner", role=UserRole.HR)
    assignee = make_user(db_session, username="assignee", role=UserRole.HR)
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

    future = utc_now() + timedelta(days=2)
    monkeypatch.setattr("app.utils.utc_now", lambda: NOW)
    return owner, assignee, candidate, future


def _rows(db_session: Session, status: DeliveryStatus | None = None) -> list[NotificationOutbox]:
    query = select(NotificationOutbox)
    if status is not None:
        query = query.where(NotificationOutbox.status == status)
    return list(db_session.execute(query).scalars().all())


def test_create_event_plans_notifications(
    client: TestClient, db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    _owner, assignee, candidate, future = _fixture(db_session, monkeypatch)
    csrf = _login(client, "manager")
    response = client.post(
        "/events",
        json={
            "candidate_id": str(candidate.id),
            "assignee_user_id": str(assignee.id),
            "type": "interview",
            "title": "Собеседование",
            "starts_at": future.isoformat().replace("+00:00", "Z"),
        },
        headers={"X-CSRF-Token": csrf},
    )
    assert response.status_code == 201, response.text
    rows = _rows(db_session)
    types = {row.notification_type for row in rows}
    assert types == {
        NotificationType.EVENT_ASSIGNED,
        NotificationType.EVENT_APPROACHING,
        NotificationType.EVENT_OVERDUE,
    }
    assigned = [r for r in rows if r.notification_type == NotificationType.EVENT_ASSIGNED]
    assert len(assigned) == 1
    assert assigned[0].recipient_user_id == assignee.id
    # The offsets 24h and 1h are both scheduled in the future.
    approaching = [r for r in rows if r.notification_type == NotificationType.EVENT_APPROACHING]
    assert len(approaching) == 2
    overdue = [r for r in rows if r.notification_type == NotificationType.EVENT_OVERDUE]
    assert overdue[0].scheduled_at == future


def test_reschedule_cancels_stale_plan(
    client: TestClient, db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    _owner, assignee, candidate, future = _fixture(db_session, monkeypatch)
    csrf = _login(client, "manager")
    created = client.post(
        "/events",
        json={
            "candidate_id": str(candidate.id),
            "assignee_user_id": str(assignee.id),
            "type": "interview",
            "title": "Собеседование",
            "starts_at": future.isoformat().replace("+00:00", "Z"),
        },
        headers={"X-CSRF-Token": csrf},
    )
    event = created.json()
    planned = {row.idempotency_key for row in _rows(db_session)}

    moved = future + timedelta(days=1)
    updated = client.patch(
        f"/events/{event['id']}",
        json={
            "expected_version": 1,
            "starts_at": moved.isoformat().replace("+00:00", "Z"),
        },
        headers={"X-CSRF-Token": csrf},
    )
    assert updated.status_code == 200, updated.text

    rows = _rows(db_session)
    # Old approaching/overdue rows are cancelled; new plan + rescheduled
    # notice exist.
    cancelled = [r for r in rows if r.status == DeliveryStatus.CANCELLED]
    assert any(r.idempotency_key in planned for r in cancelled)
    rescheduled = [r for r in rows if r.notification_type == NotificationType.EVENT_RESCHEDULED]
    assert len(rescheduled) == 1
    assert rescheduled[0].recipient_user_id == assignee.id
    queued = [r for r in rows if r.status == DeliveryStatus.QUEUED]
    overdue = [r for r in queued if r.notification_type == NotificationType.EVENT_OVERDUE]
    assert len(overdue) == 1
    assert overdue[0].scheduled_at == moved


def test_cancel_event_notifies_and_blocks_edits(
    client: TestClient, db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    _owner, assignee, candidate, future = _fixture(db_session, monkeypatch)
    csrf = _login(client, "manager")
    created = client.post(
        "/events",
        json={
            "candidate_id": str(candidate.id),
            "assignee_user_id": str(assignee.id),
            "type": "call",
            "title": "Звонок",
            "starts_at": future.isoformat().replace("+00:00", "Z"),
        },
        headers={"X-CSRF-Token": csrf},
    )
    event_id = created.json()["id"]

    cancelled = client.patch(
        f"/events/{event_id}",
        json={"expected_version": 1, "status": "cancelled"},
        headers={"X-CSRF-Token": csrf},
    )
    assert cancelled.status_code == 200, cancelled.text
    body = cancelled.json()
    assert body["status"] == "cancelled"
    assert body["cancelled_at"] is not None

    rows = _rows(db_session)
    cancelled_rows = [r for r in rows if r.notification_type == NotificationType.EVENT_CANCELLED]
    assert len(cancelled_rows) == 1
    assert cancelled_rows[0].recipient_user_id == assignee.id
    # The stale plan was cancelled too.
    assert any(r.status == DeliveryStatus.CANCELLED for r in rows)

    # A cancelled event is terminal.
    edit = client.patch(
        f"/events/{event_id}",
        json={"expected_version": 2, "title": "X"},
        headers={"X-CSRF-Token": csrf},
    )
    assert edit.status_code == 409


def test_complete_event_cancels_plan(
    client: TestClient, db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    _owner, assignee, candidate, future = _fixture(db_session, monkeypatch)
    csrf = _login(client, "manager")
    created = client.post(
        "/events",
        json={
            "candidate_id": str(candidate.id),
            "assignee_user_id": str(assignee.id),
            "type": "call",
            "title": "Звонок",
            "starts_at": future.isoformat().replace("+00:00", "Z"),
        },
        headers={"X-CSRF-Token": csrf},
    )
    event_id = created.json()["id"]
    completed = client.patch(
        f"/events/{event_id}",
        json={"expected_version": 1, "status": "completed"},
        headers={"X-CSRF-Token": csrf},
    )
    assert completed.status_code == 200
    rows = _rows(db_session)
    assert not any(
        row.status == DeliveryStatus.QUEUED
        and row.notification_type != NotificationType.EVENT_ASSIGNED
        for row in rows
    )


def test_transfer_notifies_new_owner(
    client: TestClient, db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    _owner, assignee, candidate, _future = _fixture(db_session, monkeypatch)
    csrf = _login(client, "manager")
    response = client.post(
        f"/candidates/{candidate.id}/transfer",
        json={"new_owner_user_id": str(assignee.id), "reason": "перераспределение"},
        headers={"X-CSRF-Token": csrf},
    )
    assert response.status_code == 200, response.text
    rows = _rows(db_session)
    transferred = [r for r in rows if r.notification_type == NotificationType.CANDIDATE_TRANSFERRED]
    assert len(transferred) == 1
    assert transferred[0].recipient_user_id == assignee.id
    assert transferred[0].object_id == candidate.id


def test_event_state_notification_dedupe(
    client: TestClient, db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Repeated scheduling of the same lifecycle notification is a no-op."""
    _owner, assignee, candidate, future = _fixture(db_session, monkeypatch)
    csrf = _login(client, "manager")
    created = client.post(
        "/events",
        json={
            "candidate_id": str(candidate.id),
            "assignee_user_id": str(assignee.id),
            "type": "call",
            "title": "Звонок",
            "starts_at": future.isoformat().replace("+00:00", "Z"),
        },
        headers={"X-CSRF-Token": csrf},
    )
    event_id = created.json()["id"]
    # Cancel twice: the second attempt hits the terminal-state guard, so the
    # notification can only ever be planned once for a given version.
    first = client.patch(
        f"/events/{event_id}",
        json={"expected_version": 1, "status": "cancelled"},
        headers={"X-CSRF-Token": csrf},
    )
    assert first.status_code == 200
    second = client.patch(
        f"/events/{event_id}",
        json={"expected_version": 2, "status": "cancelled"},
        headers={"X-CSRF-Token": csrf},
    )
    assert second.status_code == 409
    rows = [r for r in _rows(db_session) if r.notification_type == NotificationType.EVENT_CANCELLED]
    assert len(rows) == 1
