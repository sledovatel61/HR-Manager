"""Unit tests for event-driven candidate message planning (phase 10).

The interview lifecycle hooks live inside the events router transaction:
creating an interview plans «назначено» + a reminder; rescheduling cancels
the stale plan and queues «перенесено» + a fresh reminder; cancelling
queues «отменено». Without a recorded channel consent nothing is queued
(fail-closed, never silent). All rows go through the same outbox.
"""

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings
from app.main import create_app
from app.models import (
    Candidate,
    CandidateChannelConsent,
    DeliveryStatus,
    EventType,
    NotificationOutbox,
    NotificationType,
    UserRole,
)
from app.utils import normalize_email
from tests.conftest import FIXTURE_PASSWORD, make_candidate, make_event, make_user

NOW = datetime(2026, 9, 4, 12, 0, 0, tzinfo=UTC)


def _login(client: TestClient, username: str) -> str:
    response = client.post("/auth/login", json={"username": username, "password": FIXTURE_PASSWORD})
    assert response.status_code == 200, response.text
    return response.json()["csrf_token"]


@pytest.fixture()
def channels_app(unit_engine: Any) -> Iterator[TestClient]:
    settings = Settings.model_validate(
        {
            "APP_ENV": "test",
            "APP_DEBUG": "false",
            "SECRET_KEY": "unit-test-secret-key-0123456789abcdef",
            "SMTP_ENABLED": "true",
            "SMTP_HOST": "smtp.example.test",
            "SMTP_PORT": "2525",
            "SMTP_ENCRYPTION": "starttls",
            "SMTP_USERNAME": "mailer@example.test",
            "SMTP_PASSWORD": "mailer-secret",
            "SMTP_FROM_ADDRESS": "hr@example.test",
            "TELEGRAM_ENABLED": "true",
            "TELEGRAM_BOT_TOKEN": "123:test-token",
            "TELEGRAM_API_BASE_URL": "https://telegram.example.test",
            "TELEGRAM_BOT_USERNAME": "hr_test_bot",
            "CANDIDATE_INTERVIEW_REMINDER_HOURS": "24",
        }
    )
    app = create_app(settings, engine=unit_engine)
    with TestClient(app) as test_client:
        yield test_client


def _allow(db: Session, candidate: Candidate, *channels: str) -> None:
    for channel in channels:
        db.add(
            CandidateChannelConsent(
                candidate_id=candidate.id,
                channel=channel,
                granted=True,
                granted_at=NOW,
                source="hr_recorded",
                policy_version="phase10-v1",
                email_normalized=normalize_email(candidate.email)
                if channel == "email" and candidate.email
                else None,
            )
        )
        if channel == "telegram":
            from app.models import CandidateTelegramLink

            db.add(CandidateTelegramLink(candidate_id=candidate.id, chat_id=42, linked_at=NOW))
    db.commit()


def _rows(db: Session, candidate: Candidate) -> list[NotificationOutbox]:
    return list(
        db.execute(
            select(NotificationOutbox)
            .where(NotificationOutbox.recipient_candidate_id == candidate.id)
            .order_by(NotificationOutbox.queued_at)
        )
        .scalars()
        .all()
    )


def _create_interview(
    client: TestClient, csrf: str, candidate: Candidate, *, starts_at: str
) -> dict:
    response = client.post(
        "/events",
        json={
            "candidate_id": str(candidate.id),
            "type": "interview",
            "title": "Собеседование с кандидатом",
            "starts_at": starts_at,
        },
        headers={"X-CSRF-Token": csrf},
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_create_interview_plans_scheduled_and_reminder(
    channels_app: TestClient, db_session: Session
) -> None:
    hr = make_user(db_session, username="hr1", role=UserRole.HR)
    candidate = make_candidate(db_session, owner=hr, email="hook@example.com")
    _allow(db_session, candidate, "email", "telegram")
    csrf = _login(channels_app, "hr1")

    event = _create_interview(channels_app, csrf, candidate, starts_at="2026-10-08T10:00:00+00:00")
    rows = _rows(db_session, candidate)
    types = {(row.channel.value, row.notification_type.value) for row in rows}
    # «Назначено» on both channels + a reminder 24h before on both channels.
    assert ("email", "candidate_interview_scheduled") in types
    assert ("telegram", "candidate_interview_scheduled") in types
    assert ("email", "candidate_interview_reminder") in types
    assert ("telegram", "candidate_interview_reminder") in types
    assert len(rows) == 4
    for row in rows:
        assert row.object_type == "event" and str(row.object_id) == event["id"]
        assert row.source.value == "system"
    reminders = [
        r for r in rows if r.notification_type == NotificationType.CANDIDATE_INTERVIEW_REMINDER
    ]
    assert all(r.scheduled_at is not None for r in reminders)
    # The reminder fires 24h before the interview.
    assert reminders[0].scheduled_at == datetime(2026, 10, 7, 10, 0, 0, tzinfo=UTC)


def test_create_interview_without_consent_queues_nothing(
    channels_app: TestClient, db_session: Session
) -> None:
    hr = make_user(db_session, username="hr1", role=UserRole.HR)
    candidate = make_candidate(db_session, owner=hr, email="quiet@example.com")
    csrf = _login(channels_app, "hr1")
    _create_interview(channels_app, csrf, candidate, starts_at="2026-10-08T10:00:00+00:00")
    assert _rows(db_session, candidate) == []


def test_reschedule_cancels_stale_plan_and_notifies(
    channels_app: TestClient, db_session: Session
) -> None:
    hr = make_user(db_session, username="hr1", role=UserRole.HR)
    candidate = make_candidate(db_session, owner=hr, email="resch@example.com")
    _allow(db_session, candidate, "email")
    csrf = _login(channels_app, "hr1")
    event = _create_interview(channels_app, csrf, candidate, starts_at="2026-10-08T10:00:00+00:00")
    assert len(_rows(db_session, candidate)) == 2

    response = channels_app.patch(
        f"/events/{event['id']}",
        json={
            "expected_version": event["version"],
            "starts_at": "2026-10-10T15:00:00+00:00",
        },
        headers={"X-CSRF-Token": csrf},
    )
    assert response.status_code == 200, response.text
    rows = _rows(db_session, candidate)
    by_key = {(row.notification_type.value, row.status.value) for row in rows}
    # The stale plan (scheduled + old reminder) is cancelled.
    assert ("candidate_interview_scheduled", "cancelled") in by_key
    assert ("candidate_interview_reminder", "cancelled") in by_key
    # «Перенесено» + a fresh reminder for the new time are queued.
    assert ("candidate_interview_rescheduled", "queued") in by_key
    assert ("candidate_interview_reminder", "queued") in by_key
    fresh = [
        r
        for r in rows
        if r.notification_type == NotificationType.CANDIDATE_INTERVIEW_REMINDER
        and r.status == DeliveryStatus.QUEUED
    ]
    assert fresh[0].scheduled_at == datetime(2026, 10, 9, 15, 0, 0, tzinfo=UTC)
    # The rescheduled letter mentions the previous time (from the history).
    rescheduled = next(
        r for r in rows if r.notification_type == NotificationType.CANDIDATE_INTERVIEW_RESCHEDULED
    )
    assert "08.10.2026" in (rescheduled.body or "")
    assert "10.10.2026" in (rescheduled.body or "")


def test_cancel_event_queues_cancelled_message(
    channels_app: TestClient, db_session: Session
) -> None:
    hr = make_user(db_session, username="hr1", role=UserRole.HR)
    candidate = make_candidate(db_session, owner=hr, email="cancel@example.com")
    _allow(db_session, candidate, "email")
    csrf = _login(channels_app, "hr1")
    event = _create_interview(channels_app, csrf, candidate, starts_at="2026-10-08T10:00:00+00:00")
    response = channels_app.patch(
        f"/events/{event['id']}",
        json={"expected_version": event["version"], "status": "cancelled"},
        headers={"X-CSRF-Token": csrf},
    )
    assert response.status_code == 200, response.text
    rows = _rows(db_session, candidate)
    by_key = {(row.notification_type.value, row.status.value) for row in rows}
    assert ("candidate_interview_scheduled", "cancelled") in by_key
    assert ("candidate_interview_reminder", "cancelled") in by_key
    assert ("candidate_interview_cancelled", "queued") in by_key


def test_replan_same_version_is_deduplicated(channels_app: TestClient, db_session: Session) -> None:
    """Calling the planner twice for the same event version must not create
    duplicate rows (idempotency keys)."""
    from app.candidate_messages import plan_candidate_interview_messages

    hr = make_user(db_session, username="hr1", role=UserRole.HR)
    candidate = make_candidate(db_session, owner=hr, email="dedupe@example.com")
    _allow(db_session, candidate, "email")
    event = make_event(
        db_session,
        candidate=candidate,
        author=hr,
        assignee=hr,
        type_=EventType.INTERVIEW,
        starts_at=datetime.now(UTC) + timedelta(days=3),
    )
    settings = channels_app.app.state.settings  # type: ignore[attr-defined]
    plan_candidate_interview_messages(
        db_session, event=event, candidate=candidate, settings=settings
    )
    plan_candidate_interview_messages(
        db_session, event=event, candidate=candidate, settings=settings
    )
    db_session.commit()
    assert len(_rows(db_session, candidate)) == 2  # scheduled + reminder, no dupes


def test_reminder_offset_configurable(channels_app: TestClient, db_session: Session) -> None:
    hr = make_user(db_session, username="hr1", role=UserRole.HR)
    candidate = make_candidate(db_session, owner=hr, email="off@example.com")
    _allow(db_session, candidate, "email")
    csrf = _login(channels_app, "hr1")
    event = _create_interview(channels_app, csrf, candidate, starts_at="2026-10-08T10:00:00+00:00")
    assert event["id"]
    rows = [
        r
        for r in _rows(db_session, candidate)
        if r.notification_type == NotificationType.CANDIDATE_INTERVIEW_REMINDER
    ]
    # Default offset from the app fixture: 24 hours before.
    assert rows[0].scheduled_at == datetime(2026, 10, 7, 10, 0, 0, tzinfo=UTC)
