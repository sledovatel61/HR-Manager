"""Unit tests for worker processing logic (SQLite; no SKIP LOCKED)."""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings
from app.models import (
    AccessGrant,
    AccessGrantScope,
    DeliveryChannel,
    DeliveryStatus,
    Notification,
    NotificationDeliveryAttempt,
    NotificationOutbox,
    NotificationSource,
    NotificationType,
    Reminder,
    ReminderRecurrence,
    ReminderStatus,
    UserRole,
    WorkerHeartbeat,
)
from app.notification_service import schedule
from app.worker import (
    _process_reminder,
    heartbeat,
    process_row,
    recover_stale_leases,
    worker_is_healthy,
)
from tests.conftest import make_user

NOW = datetime(2026, 9, 4, 12, 0, 0, tzinfo=UTC)


def _settings() -> Settings:
    return Settings.model_validate(
        {
            "APP_ENV": "test",
            "SECRET_KEY": "x",
            "DATABASE_URL": "sqlite+pysqlite://",
            "WORKER_MAX_ATTEMPTS": "3",
            "WORKER_BACKOFF_BASE_S": "60",
            "WORKER_LEASE_SECONDS": "120",
        }
    )


def _claimed(
    db: Session,
    *,
    channel: DeliveryChannel = DeliveryChannel.IN_APP,
    scheduled_at: datetime = NOW,
) -> NotificationOutbox:
    user = make_user(db, username=f"hr-{uuid4().hex[:6]}", role=UserRole.HR)
    row = schedule(
        db,
        recipient_user_id=user.id,
        channel=channel,
        type_=NotificationType.EVENT_ASSIGNED,
        dedupe_key=f"worker-test:{uuid4().hex}",
        scheduled_at=scheduled_at,
    )
    assert row is not None
    row.status = DeliveryStatus.SENDING
    row.started_at = NOW
    row.lease_expires_at = NOW + timedelta(minutes=2)
    db.commit()
    db.refresh(row)
    return row


def test_in_app_delivery_creates_notification_and_history(db_session: Session) -> None:
    row = _claimed(db_session)
    status = process_row(db_session, row, settings=_settings(), now=NOW)
    assert status == "delivered"
    db_session.refresh(row)
    assert row.status == DeliveryStatus.DELIVERED
    assert row.delivered_at == NOW
    assert row.attempts == 1
    attempts = (
        db_session.execute(
            select(NotificationDeliveryAttempt).where(
                NotificationDeliveryAttempt.outbox_id == row.id
            )
        )
        .scalars()
        .all()
    )
    assert len(attempts) == 1
    assert attempts[0].outcome.value == "delivered"
    notification = db_session.execute(select(Notification)).scalar_one()
    assert notification.user_id == row.recipient_user_id


def test_reserved_channels_are_skipped_honestly(db_session: Session) -> None:
    row = _claimed(db_session, channel=DeliveryChannel.TELEGRAM)
    status = process_row(db_session, row, settings=_settings(), now=NOW)
    assert status == "skipped"
    db_session.refresh(row)
    assert row.status == DeliveryStatus.SKIPPED
    assert row.error_class == "channel_not_configured"
    # No fake delivered/accepted anywhere.
    assert db_session.execute(select(Notification)).scalars().all() == []


def test_quiet_hours_postpone(db_session: Session) -> None:
    # 22:00 MSK is quiet for the default 21:00-08:00 window.
    quiet_now = datetime(2026, 9, 4, 19, 0, 0, tzinfo=UTC)
    row = _claimed(db_session, scheduled_at=quiet_now)
    status = process_row(db_session, row, settings=_settings(), now=quiet_now)
    assert status == "queued"
    db_session.refresh(row)
    assert row.status == DeliveryStatus.QUEUED
    assert row.scheduled_at == quiet_now  # original preserved
    assert row.scheduled_at_effective == datetime(2026, 9, 5, 5, 0, 0, tzinfo=UTC)
    assert row.next_attempt_at == row.scheduled_at_effective
    assert db_session.execute(select(Notification)).scalars().all() == []


def test_quiet_hours_bypassed_delivers(db_session: Session) -> None:
    quiet_now = datetime(2026, 9, 4, 19, 0, 0, tzinfo=UTC)
    row = _claimed(db_session, scheduled_at=quiet_now)
    row.quiet_hours_bypassed = True
    db_session.commit()
    status = process_row(db_session, row, settings=_settings(), now=quiet_now)
    assert status == "delivered"


def test_lease_recovery_returns_sending_to_queued(db_session: Session) -> None:
    row = _claimed(db_session)
    row.lease_expires_at = NOW - timedelta(seconds=1)
    db_session.commit()
    recovered = recover_stale_leases(db_session, now=NOW, lease_seconds=120)
    assert recovered == 1
    db_session.refresh(row)
    assert row.status == DeliveryStatus.QUEUED
    assert row.lease_expires_at is None
    assert row.attempts == 1  # the crashed attempt is recorded
    attempts = (
        db_session.execute(
            select(NotificationDeliveryAttempt).where(
                NotificationDeliveryAttempt.outbox_id == row.id
            )
        )
        .scalars()
        .all()
    )
    assert attempts[0].error_class == "lease_expired"


def test_bounded_retries_then_terminal_failure_alerts_pilot(db_session: Session) -> None:
    pilot = make_user(db_session, username="pilot-user", role=UserRole.ADMIN)
    db_session.add(
        AccessGrant(
            user_id=pilot.id,
            scope=AccessGrantScope.PILOT_FULL_ACCESS,
            granted_by_user_id=pilot.id,
            granted_at=NOW,
        )
    )
    db_session.commit()

    row = _claimed(db_session)
    row.attempts = 2  # two attempts already burned
    row.next_attempt_at = None
    db_session.commit()

    # Break in_app delivery so the row fails.
    import app.worker as worker_module

    original = worker_module.deliver_in_app

    def _boom(db: Session, outbox: NotificationOutbox, *, now: datetime | None = None) -> bool:
        raise RuntimeError("simulated delivery crash")

    worker_module.deliver_in_app = _boom
    try:
        status = process_row(db_session, row, settings=_settings(), now=NOW)
    finally:
        worker_module.deliver_in_app = original

    assert status == "failed"
    db_session.refresh(row)
    assert row.status == DeliveryStatus.FAILED
    assert row.attempts == 3
    assert row.failed_at is not None

    # The pilot got a system alert (as an outbox row).
    alert_rows = (
        db_session.execute(
            select(NotificationOutbox).where(
                NotificationOutbox.notification_type == NotificationType.SYSTEM_ALERT
            )
        )
        .scalars()
        .all()
    )
    assert len(alert_rows) == 1
    assert alert_rows[0].recipient_user_id == pilot.id
    assert "неудачное" in (alert_rows[0].body or "")


def test_retry_backoff_schedule(db_session: Session) -> None:
    import app.worker as worker_module

    row = _claimed(db_session)
    row.attempts = 0
    db_session.commit()

    original = worker_module.deliver_in_app

    def _boom(db: Session, outbox: NotificationOutbox, *, now: datetime | None = None) -> bool:
        raise RuntimeError("simulated delivery crash")

    worker_module.deliver_in_app = _boom
    try:
        status = process_row(db_session, row, settings=_settings(), now=NOW)
    finally:
        worker_module.deliver_in_app = original

    assert status == "queued"
    db_session.refresh(row)
    assert row.attempts == 1
    assert row.next_attempt_at == NOW + timedelta(seconds=60)


def test_reminder_due_and_recurrence(db_session: Session) -> None:
    user = make_user(db_session, username="hr-reminder", role=UserRole.HR)
    reminder = Reminder(
        owner_user_id=user.id,
        assignee_user_id=user.id,
        title="Ежедневное",
        due_at=NOW - timedelta(minutes=1),
        timezone="Europe/Moscow",
        importance="normal",
        recurrence=ReminderRecurrence.DAILY,
        status=ReminderStatus.ACTIVE,
        occurrence=1,
        version=1,
        created_at=NOW,
        updated_at=NOW,
    )
    db_session.add(reminder)
    db_session.commit()

    _process_reminder(db_session, reminder, settings=_settings(), now=NOW)
    db_session.commit()
    db_session.refresh(reminder)

    # The notification went through the outbox with a per-occurrence key.
    outbox = db_session.execute(select(NotificationOutbox)).scalar_one()
    assert outbox.notification_type == NotificationType.REMINDER_DUE
    assert outbox.idempotency_key == f"reminder_due:{reminder.id}:1"
    assert outbox.source == NotificationSource.REMINDER
    # The recurring reminder advanced, still active.
    assert reminder.status == ReminderStatus.ACTIVE
    assert reminder.occurrence == 2
    assert reminder.due_at == NOW - timedelta(minutes=1) + timedelta(days=1)


def test_one_shot_reminder_completes(db_session: Session) -> None:
    user = make_user(db_session, username="hr-once", role=UserRole.HR)
    reminder = Reminder(
        owner_user_id=user.id,
        assignee_user_id=user.id,
        title="Разовое",
        due_at=NOW - timedelta(minutes=1),
        timezone="Europe/Moscow",
        importance="normal",
        recurrence=ReminderRecurrence.NONE,
        status=ReminderStatus.ACTIVE,
        occurrence=1,
        version=1,
        created_at=NOW,
        updated_at=NOW,
    )
    db_session.add(reminder)
    db_session.commit()
    _process_reminder(db_session, reminder, settings=_settings(), now=NOW)
    db_session.commit()
    db_session.refresh(reminder)
    assert reminder.status == ReminderStatus.COMPLETED
    assert reminder.completed_at == NOW


def test_heartbeat_and_health(db_session: Session) -> None:
    heartbeat(db_session, worker_id="test-worker", now=NOW)
    row = db_session.get(WorkerHeartbeat, 1)
    assert row is not None
    assert row.worker_id == "test-worker"

    settings = _settings()
    assert worker_is_healthy(db_session, settings=settings, now=NOW) is True
    assert (
        worker_is_healthy(db_session, settings=settings, now=NOW + timedelta(seconds=100)) is False
    )
