"""Unit tests for the transactional outbox primitives (SQLite)."""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import (
    DeliveryChannel,
    DeliveryStatus,
    Event,
    Notification,
    NotificationOutbox,
    NotificationSource,
    NotificationType,
)
from app.notification_service import (
    cancel_pending_for_object,
    deliver_in_app,
    schedule,
    schedule_notification_row,
)
from tests.conftest import make_user

NOW = datetime(2026, 9, 4, 12, 0, 0, tzinfo=UTC)


def test_schedule_creates_outbox_row(db_session: Session) -> None:
    user = make_user(db_session, username="hr1")
    row = schedule_notification_row(
        db_session,
        type_=NotificationType.EVENT_ASSIGNED,
        recipient_user_id=user.id,
        dedupe_key="event:1:assigned:u1",
        scheduled_at=NOW,
    )
    assert row is not None
    assert row.channel == DeliveryChannel.IN_APP
    assert row.idempotency_key == "event_assigned:event:1:assigned:u1"
    assert row.status == DeliveryStatus.QUEUED
    db_session.commit()
    count = db_session.execute(select(NotificationOutbox)).scalars().all()
    assert len(count) == 1


def test_schedule_deduplicates_same_idempotency_key(db_session: Session) -> None:
    user = make_user(db_session, username="hr1")
    first = schedule_notification_row(
        db_session,
        type_=NotificationType.EVENT_OVERDUE,
        recipient_user_id=user.id,
        dedupe_key="event:1:overdue",
        scheduled_at=NOW,
    )
    assert first is not None
    db_session.commit()

    # A repeated business-event pass must be a no-op, not a duplicate.
    second = schedule_notification_row(
        db_session,
        type_=NotificationType.EVENT_OVERDUE,
        recipient_user_id=user.id,
        dedupe_key="event:1:overdue",
        scheduled_at=NOW,
    )
    assert second is None
    rows = db_session.execute(select(NotificationOutbox)).scalars().all()
    assert len(rows) == 1


def test_cancel_pending_cancels_only_queued(db_session: Session) -> None:
    user = make_user(db_session, username="hr1")
    queued = schedule_notification_row(
        db_session,
        type_=NotificationType.EVENT_APPROACHING,
        recipient_user_id=user.id,
        object_type="event",
        object_id=uuid4(),
        dedupe_key="e:1:approaching:24",
        scheduled_at=NOW,
    )
    sending = schedule_notification_row(
        db_session,
        type_=NotificationType.EVENT_APPROACHING,
        recipient_user_id=user.id,
        object_type="event",
        object_id=uuid4(),
        dedupe_key="e:1:approaching:1",
        scheduled_at=NOW,
    )
    assert queued is not None and sending is not None
    sending.status = DeliveryStatus.SENDING
    sending.lease_expires_at = NOW + timedelta(minutes=2)
    db_session.commit()

    cancelled = cancel_pending_for_object(
        db_session,
        object_type="event",
        object_id=queued.object_id or uuid4(),
        now=NOW,
    )
    assert cancelled == 1
    db_session.commit()
    db_session.refresh(queued)
    db_session.refresh(sending)
    assert queued.status == DeliveryStatus.CANCELLED
    assert queued.cancelled_at == NOW
    assert sending.status == DeliveryStatus.SENDING  # in-flight rows untouched


def test_deliver_in_app_is_idempotent(db_session: Session) -> None:
    user = make_user(db_session, username="hr1")
    row = schedule_notification_row(
        db_session,
        type_=NotificationType.EVENT_ASSIGNED,
        recipient_user_id=user.id,
        dedupe_key="event:9:assigned",
        scheduled_at=NOW,
    )
    assert row is not None
    db_session.commit()

    created = deliver_in_app(db_session, row, now=NOW)
    assert created is True
    db_session.commit()
    notifications = db_session.execute(select(Notification)).scalars().all()
    assert len(notifications) == 1
    assert notifications[0].dedupe_key == row.idempotency_key
    assert notifications[0].user_id == user.id

    # The same delivery attempted again must not duplicate the row.
    again = deliver_in_app(db_session, row, now=NOW)
    assert again is False
    db_session.commit()
    assert len(db_session.execute(select(Notification)).scalars().all()) == 1


def test_deliver_in_app_carries_type_and_source(db_session: Session) -> None:
    user = make_user(db_session, username="hr1")
    row = schedule_notification_row(
        db_session,
        type_=NotificationType.REMINDER_DUE,
        recipient_user_id=user.id,
        source=NotificationSource.REMINDER,
        dedupe_key="reminder:1:1",
        scheduled_at=NOW,
    )
    assert row is not None
    db_session.commit()
    deliver_in_app(db_session, row, now=NOW)
    db_session.commit()
    notification = db_session.execute(select(Notification)).scalar_one()
    assert notification.type == NotificationType.REMINDER_DUE
    assert notification.source == NotificationSource.REMINDER


def test_schedule_requires_exactly_one_recipient(db_session: Session) -> None:
    with pytest.raises(ValueError):
        schedule(
            db_session,
            type_=NotificationType.SYSTEM_ALERT,
            recipient_user_id=None,
            dedupe_key="x",
        )


def test_plan_event_notifications_creates_expected_rows(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.config import Settings
    from app.models import (
        Candidate,
        CandidateSource,
        CandidateStage,
        EventStatus,
        EventType,
        UserRole,
    )
    from app.notification_service import plan_event_notifications
    from app.utils import utc_now

    owner = make_user(db_session, username="owner", role=UserRole.HR)
    assignee = make_user(db_session, username="assignee", role=UserRole.HR)
    manager = make_user(db_session, username="manager", role=UserRole.MANAGER)
    candidate = Candidate(
        full_name="Тестовый кандидат",
        full_name_normalized="тестовый кандидат",
        source=CandidateSource.REFERRAL,
        position="Разработчик",
        owner_user_id=owner.id,
        stage=CandidateStage.NEW,
        stage_position=0,
        created_at=utc_now(),
        updated_at=utc_now(),
    )
    db_session.add(candidate)
    db_session.commit()

    settings = Settings.model_validate(
        {"APP_ENV": "test", "SECRET_KEY": "x", "DATABASE_URL": "sqlite+pysqlite://"}
    )
    event = Event(
        candidate_id=candidate.id,
        author_user_id=manager.id,
        assignee_user_id=assignee.id,
        type=EventType.INTERVIEW,
        title="Собеседование",
        status=EventStatus.SCHEDULED,
        starts_at=NOW + timedelta(days=2),
        version=1,
        created_at=NOW,
        updated_at=NOW,
    )
    db_session.add(event)
    db_session.commit()

    plan_event_notifications(
        db_session, event=event, author=manager, assignee=assignee, settings=settings, now=NOW
    )
    db_session.commit()
    rows = (
        db_session.execute(select(NotificationOutbox).order_by(NotificationOutbox.idempotency_key))
        .scalars()
        .all()
    )
    types = {row.notification_type for row in rows}
    assert types == {
        NotificationType.EVENT_ASSIGNED,
        NotificationType.EVENT_APPROACHING,
        NotificationType.EVENT_OVERDUE,
    }
    # The two approaching offsets (24h and 1h) both got planned.
    approaching = [r for r in rows if r.notification_type == NotificationType.EVENT_APPROACHING]
    assert len(approaching) == 2
    for row in rows:
        assert row.recipient_user_id == assignee.id


def test_plan_skips_self_assignment_notification(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.config import Settings
    from app.models import (
        Candidate,
        CandidateSource,
        CandidateStage,
        EventStatus,
        EventType,
        UserRole,
    )
    from app.notification_service import plan_event_notifications
    from app.utils import utc_now

    hr = make_user(db_session, username="hr1", role=UserRole.HR)
    candidate = Candidate(
        full_name="Кандидат",
        full_name_normalized="кандидат",
        source=CandidateSource.REFERRAL,
        position="Dev",
        owner_user_id=hr.id,
        stage=CandidateStage.NEW,
        stage_position=0,
        created_at=utc_now(),
        updated_at=utc_now(),
    )
    db_session.add(candidate)
    db_session.commit()
    event = Event(
        candidate_id=candidate.id,
        author_user_id=hr.id,
        assignee_user_id=hr.id,
        type=EventType.CALL,
        title="Звонок",
        status=EventStatus.SCHEDULED,
        starts_at=NOW + timedelta(days=1),
        version=1,
        created_at=NOW,
        updated_at=NOW,
    )
    db_session.add(event)
    db_session.commit()
    settings = Settings.model_validate(
        {"APP_ENV": "test", "SECRET_KEY": "x", "DATABASE_URL": "sqlite+pysqlite://"}
    )
    plan_event_notifications(
        db_session, event=event, author=hr, assignee=hr, settings=settings, now=NOW
    )
    db_session.commit()
    rows = db_session.execute(select(NotificationOutbox)).scalars().all()
    types = {row.notification_type for row in rows}
    assert NotificationType.EVENT_ASSIGNED not in types
