"""Integration tests for the notification queue and worker (PostgreSQL).

These cover exactly the behaviours that SQLite cannot: SKIP LOCKED
concurrency, lease recovery across sessions, transactional outbox
atomicity with real commits/rollbacks, and the end-to-end event →
outbox → worker → notification pipeline.
"""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, delete, select
from sqlalchemy.orm import Session

from app.config import Settings
from app.models import (
    Candidate,
    CandidateSource,
    CandidateStage,
    DeliveryStatus,
    Notification,
    NotificationDeliveryAttempt,
    NotificationOutbox,
    NotificationType,
    Reminder,
    ReminderRecurrence,
    ReminderStatus,
    UserRole,
    WorkerHeartbeat,
)
from app.notification_service import schedule_notification_row
from app.utils import utc_now
from app.worker import claim_batch, process_row, recover_stale_leases, scan_due_reminders
from tests.conftest import FIXTURE_PASSWORD, make_user

pytestmark = pytest.mark.integration

NOW = datetime(2026, 9, 4, 12, 0, 0, tzinfo=UTC)


def _clean_queue(db: Session) -> None:
    """Isolate queue-based tests: pg_db shares the database with the rest
    of the integration suite, so every test starts from an empty queue."""
    db.execute(delete(NotificationDeliveryAttempt))
    db.execute(delete(NotificationOutbox))
    db.execute(delete(Reminder))
    db.execute(delete(Notification))
    db.execute(delete(WorkerHeartbeat))
    db.commit()


def _settings() -> Settings:
    return Settings.model_validate(
        {
            "APP_ENV": "test",
            "SECRET_KEY": "x",
            "DATABASE_URL": "postgresql+psycopg://postgres@127.0.0.1:5432/hr_manager_test",
            "WORKER_MAX_ATTEMPTS": "3",
            "WORKER_BACKOFF_BASE_S": "60",
            "WORKER_LEASE_SECONDS": "120",
        }
    )


def _login(client: TestClient, username: str) -> str:
    response = client.post("/auth/login", json={"username": username, "password": FIXTURE_PASSWORD})
    assert response.status_code == 200, response.text
    return response.json()["csrf_token"]


def test_claim_skip_locked_no_overlap(pg_db: Session, pg_engine: Engine) -> None:
    """Two workers claiming concurrently never get the same row."""
    _clean_queue(pg_db)
    recipient = make_user(pg_db, username="hr-claim", role=UserRole.HR)
    for _index in range(6):
        schedule_notification_row(
            pg_db,
            type_=NotificationType.EVENT_ASSIGNED,
            recipient_user_id=recipient.id,
            dedupe_key=f"claim-{uuid4().hex}",
            scheduled_at=NOW,
        )
    pg_db.commit()

    from sqlalchemy.orm import Session as OrmSession

    first = claim_batch(pg_db, now=NOW, batch_size=4, lease_seconds=120)
    second_session = OrmSession(pg_engine)
    try:
        second = claim_batch(second_session, now=NOW, batch_size=4, lease_seconds=120)
    finally:
        second_session.close()

    first_ids = {row.id for row in first}
    second_ids = {row.id for row in second}
    assert len(first_ids) == 4
    assert len(second_ids) == 2
    assert first_ids.isdisjoint(second_ids)
    assert all(row.status == DeliveryStatus.SENDING for row in list(first) + list(second))


def test_lease_recovery_and_reprocessing_after_crash(pg_db: Session, pg_engine: Engine) -> None:
    _clean_queue(pg_db)
    recipient = make_user(pg_db, username="hr-lease", role=UserRole.HR)
    schedule_notification_row(
        pg_db,
        type_=NotificationType.EVENT_ASSIGNED,
        recipient_user_id=recipient.id,
        dedupe_key=f"lease-{uuid4().hex}",
        scheduled_at=NOW,
    )
    pg_db.commit()

    claimed = claim_batch(pg_db, now=NOW, batch_size=5, lease_seconds=120)
    assert len(claimed) == 1
    # Simulate a crash: the lease expires without any processing.
    pg_db.commit()

    stale_now = NOW + timedelta(seconds=200)
    recovered = recover_stale_leases(pg_db, now=stale_now, lease_seconds=120)
    assert recovered == 1

    # A second worker pass claims it again and delivers exactly once.
    claimed_again = claim_batch(pg_db, now=stale_now, batch_size=5, lease_seconds=120)
    assert len(claimed_again) == 1
    status = process_row(pg_db, claimed_again[0], settings=_settings(), now=stale_now)
    assert status == "delivered"
    notifications = pg_db.execute(select(Notification)).scalars().all()
    assert len(notifications) == 1
    attempts = pg_db.execute(select(NotificationDeliveryAttempt)).scalars().all()
    assert {attempt.attempt_no for attempt in attempts} == {1, 2}
    assert any(attempt.error_class == "lease_expired" for attempt in attempts)


def test_end_to_end_event_to_notification(pg_client: TestClient, pg_db: Session) -> None:
    """API event → transactional outbox → worker claim/process → notification."""
    _clean_queue(pg_db)
    owner = make_user(pg_db, username="hr-e2e-owner", role=UserRole.HR)
    assignee = make_user(pg_db, username="hr-e2e-assignee", role=UserRole.HR)
    make_user(pg_db, username="mgr-e2e", role=UserRole.MANAGER)
    candidate = Candidate(
        full_name="Кандидат E2E",
        full_name_normalized="кандидат e2e",
        source=CandidateSource.REFERRAL,
        position="Dev",
        owner_user_id=owner.id,
        stage=CandidateStage.NEW,
        stage_position=0,
        created_at=utc_now(),
        updated_at=utc_now(),
    )
    pg_db.add(candidate)
    pg_db.commit()

    csrf = _login(pg_client, "mgr-e2e")
    future = utc_now() + timedelta(hours=2)
    created = pg_client.post(
        "/events",
        json={
            "candidate_id": str(candidate.id),
            "assignee_user_id": str(assignee.id),
            "type": "call",
            "title": "Звонок",
            "starts_at": future.isoformat(),
        },
        headers={"X-CSRF-Token": csrf},
    )
    assert created.status_code == 201, created.text

    # The outbox rows committed with the event (transactional outbox).
    rows = (
        pg_db.execute(
            select(NotificationOutbox).where(
                NotificationOutbox.notification_type != NotificationType.EVENT_APPROACHING
            )
        )
        .scalars()
        .all()
    )
    assert any(row.notification_type == NotificationType.EVENT_ASSIGNED for row in rows)

    # Process everything due now with the worker machinery.
    settings = _settings()
    claimed = claim_batch(pg_db, now=utc_now(), batch_size=20, lease_seconds=120)
    assert len(claimed) >= 1
    for row in claimed:
        process_row(pg_db, row, settings=settings, now=utc_now())

    notifications = (
        pg_db.execute(select(Notification).where(Notification.user_id == assignee.id))
        .scalars()
        .all()
    )
    assert len(notifications) == 1
    assert notifications[0].type == NotificationType.EVENT_ASSIGNED

    # Reprocessing the same rows delivers nothing new (idempotent).
    claimed_again = claim_batch(pg_db, now=utc_now(), batch_size=20, lease_seconds=120)
    for row in claimed_again:
        process_row(pg_db, row, settings=settings, now=utc_now())
    assert len(pg_db.execute(select(Notification)).scalars().all()) == 1


def test_outbox_transactionality_rollback(pg_db: Session) -> None:
    """A rolled-back business transaction leaves no outbox row behind."""
    _clean_queue(pg_db)
    recipient = make_user(pg_db, username="hr-rollback", role=UserRole.HR)
    schedule_notification_row(
        pg_db,
        type_=NotificationType.EVENT_ASSIGNED,
        recipient_user_id=recipient.id,
        dedupe_key=f"rollback-{uuid4().hex}",
        scheduled_at=NOW,
    )
    pg_db.rollback()  # business transaction failed after scheduling
    rows = (
        pg_db.execute(
            select(NotificationOutbox).where(NotificationOutbox.recipient_user_id == recipient.id)
        )
        .scalars()
        .all()
    )
    assert rows == []


def test_parallel_workers_process_reminders_without_duplicates(
    pg_db: Session, pg_engine: Engine
) -> None:
    from sqlalchemy.orm import Session as OrmSession

    _clean_queue(pg_db)
    user = make_user(pg_db, username="hr-recurring", role=UserRole.HR)
    reminder = Reminder(
        owner_user_id=user.id,
        assignee_user_id=user.id,
        title="Ежедневное",
        due_at=utc_now() - timedelta(minutes=1),
        timezone="Europe/Moscow",
        importance="normal",
        recurrence=ReminderRecurrence.DAILY,
        status=ReminderStatus.ACTIVE,
        occurrence=1,
        version=1,
        created_at=utc_now(),
        updated_at=utc_now(),
    )
    pg_db.add(reminder)
    pg_db.commit()

    settings = _settings()
    first = scan_due_reminders(pg_db, settings=settings, now=utc_now(), batch_size=10)
    assert first == 1
    # A second worker scanning the same second must find nothing due.
    second_session = OrmSession(pg_engine)
    try:
        second = scan_due_reminders(second_session, settings=settings, now=utc_now(), batch_size=10)
    finally:
        second_session.close()
    assert second == 0

    outbox = (
        pg_db.execute(
            select(NotificationOutbox).where(
                NotificationOutbox.notification_type == NotificationType.REMINDER_DUE
            )
        )
        .scalars()
        .all()
    )
    assert len(outbox) == 1  # one occurrence, one delivery


def test_cancel_race_wins_over_stale_claim(pg_db: Session) -> None:
    """A cancel between claim and processing makes the worker back off."""
    _clean_queue(pg_db)
    recipient = make_user(pg_db, username="hr-race", role=UserRole.HR)
    schedule_notification_row(
        pg_db,
        type_=NotificationType.EVENT_ASSIGNED,
        recipient_user_id=recipient.id,
        dedupe_key=f"race-{uuid4().hex}",
        scheduled_at=NOW,
    )
    pg_db.commit()

    claimed = claim_batch(pg_db, now=NOW, batch_size=5, lease_seconds=120)
    assert len(claimed) == 1

    # Admin cancels while the worker holds the lease (direct row write
    # stands in for the admin endpoint here — same UPDATE).
    row = pg_db.execute(
        select(NotificationOutbox).where(NotificationOutbox.id == claimed[0].id).with_for_update()
    ).scalar_one()
    row.status = DeliveryStatus.CANCELLED
    row.cancelled_at = NOW
    row.lease_expires_at = None
    pg_db.commit()

    status = process_row(pg_db, claimed[0], settings=_settings(), now=NOW)
    assert status == "cancelled"
    assert pg_db.execute(select(Notification)).scalars().all() == []


def test_admin_queue_diagnostics_has_no_pii(pg_client: TestClient, pg_db: Session) -> None:
    _clean_queue(pg_db)
    recipient = make_user(pg_db, username="hr-diag", role=UserRole.HR)
    make_user(pg_db, username="admin-diag", role=UserRole.ADMIN)
    schedule_notification_row(
        pg_db,
        type_=NotificationType.SYSTEM_ALERT,
        recipient_user_id=recipient.id,
        title="Секретная тема переговоров",
        body="Тело с персональными данными",
        dedupe_key=f"diag-{uuid4().hex}",
        scheduled_at=NOW,
    )
    pg_db.commit()

    _login(pg_client, "admin-diag")
    body = pg_client.get("/admin/ops/notifications/queue").json()
    assert set(body["counts"].keys()) <= {
        "queued",
        "sending",
        "accepted",
        "delivered",
        "failed",
        "cancelled",
        "skipped",
    }
    assert "Секретная" not in str(body)
    assert "персональными" not in str(body)
    assert "worker" in body

    # The ops status carries the signal too.
    status_body = pg_client.get("/ops/status").json()
    assert "notifications" in status_body
    assert "Секретная" not in str(status_body)
