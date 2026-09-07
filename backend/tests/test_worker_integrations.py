"""Unit tests for worker processing Telegram and Email outbox deliveries (Phase 9)."""

from datetime import timedelta
from unittest.mock import patch

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings
from app.models import (
    DeliveryChannel,
    DeliveryStatus,
    NotificationDeliveryAttempt,
    NotificationPreference,
    NotificationType,
    UserRole,
)
from app.notification_service import schedule
from app.smtp_adapter import SmtpRecipientError, SmtpSendResult
from app.telegram_adapter import (
    TelegramBlockedError,
    TelegramRateLimitError,
    TelegramSendResult,
)
from app.utils import utc_now
from app.worker import process_row
from tests.conftest import make_user


def _worker_settings(
    tg_token: str = "123:TG_TOKEN",
    smtp_host: str = "smtp.test.com",
) -> Settings:
    return Settings.model_validate(
        {
            "APP_ENV": "test",
            "SECRET_KEY": "test-secret-key-32-chars-minimum-length!",
            "DATABASE_URL": "sqlite+pysqlite://",
            "TELEGRAM_BOT_TOKEN": tg_token,
            "SMTP_HOST": smtp_host,
            "WORKER_MAX_ATTEMPTS": "3",
            "WORKER_BACKOFF_BASE_S": "5.0",
        }
    )


def test_telegram_worker_delivery_success(db_session: Session) -> None:
    user = make_user(db_session, username="tg_worker_user", role=UserRole.HR)
    pref = NotificationPreference(
        user_id=user.id,
        timezone="Europe/Moscow",
        quiet_hours_start="21:00",
        quiet_hours_end="08:00",
        workdays=[1, 2, 3, 4, 5],
        enabled_types=[member.value for member in NotificationType],
        enabled_channels=["in_app", "telegram"],
        telegram_chat_id=123456789,
        telegram_opt_in=True,
        telegram_consent_at=utc_now(),
        telegram_consent_source="test",
        telegram_consent_policy_version="1.0",
    )
    db_session.add(pref)

    outbox = schedule(
        db_session,
        recipient_user_id=user.id,
        channel=DeliveryChannel.TELEGRAM,
        type_=NotificationType.EVENT_ASSIGNED,
        title="Новое событие",
        body="Вам назначено интервью",
        dedupe_key="tg-succ-1",
    )
    assert outbox is not None
    now = utc_now()
    outbox.status = DeliveryStatus.SENDING
    outbox.lease_expires_at = now + timedelta(seconds=120)
    db_session.commit()

    settings = _worker_settings()

    with patch(
        "app.worker.telegram_send_message",
        return_value=TelegramSendResult(accepted=True, provider_message_id="tg-msg-123"),
    ) as mock_send:
        status_result = process_row(db_session, outbox, settings=settings, now=now)
        assert status_result == "accepted"
        mock_send.assert_called_once()

    db_session.refresh(outbox)
    assert outbox.status == DeliveryStatus.ACCEPTED
    assert outbox.accepted_at is not None
    assert outbox.provider_message_id == "tg-msg-123"
    assert outbox.attempts == 1

    attempts = db_session.scalars(
        select(NotificationDeliveryAttempt).where(
            NotificationDeliveryAttempt.outbox_id == outbox.id
        )
    ).all()
    assert len(attempts) == 1
    assert attempts[0].outcome == "accepted"
    assert attempts[0].provider_message_id == "tg-msg-123"


def test_telegram_worker_delivery_rate_limit(db_session: Session) -> None:
    user = make_user(db_session, username="tg_rl_user", role=UserRole.HR)
    pref = NotificationPreference(
        user_id=user.id,
        timezone="Europe/Moscow",
        quiet_hours_start="21:00",
        quiet_hours_end="08:00",
        workdays=[1, 2, 3, 4, 5],
        enabled_types=[member.value for member in NotificationType],
        enabled_channels=["telegram"],
        telegram_chat_id=987654,
        telegram_opt_in=True,
        telegram_consent_at=utc_now(),
    )
    db_session.add(pref)

    outbox = schedule(
        db_session,
        recipient_user_id=user.id,
        channel=DeliveryChannel.TELEGRAM,
        type_=NotificationType.EVENT_ASSIGNED,
        title="Событие",
        dedupe_key="tg-rl-1",
    )
    assert outbox is not None
    now = utc_now()
    outbox.status = DeliveryStatus.SENDING
    outbox.lease_expires_at = now + timedelta(seconds=120)
    db_session.commit()

    settings = _worker_settings()

    with patch(
        "app.worker.telegram_send_message",
        side_effect=TelegramRateLimitError(retry_after=45),
    ):
        status_result = process_row(db_session, outbox, settings=settings, now=now)
        assert status_result == "queued"

    db_session.refresh(outbox)
    assert outbox.status == DeliveryStatus.QUEUED
    assert outbox.next_attempt_at is not None
    assert outbox.next_attempt_at >= now + timedelta(seconds=40)
    assert outbox.attempts == 1

    attempts = db_session.scalars(
        select(NotificationDeliveryAttempt).where(
            NotificationDeliveryAttempt.outbox_id == outbox.id
        )
    ).all()
    assert len(attempts) == 1
    assert attempts[0].outcome == "failed"
    assert attempts[0].error_class == "rate_limited"
    assert attempts[0].error_code == "429"


def test_telegram_worker_bot_blocked_fails_permanently(db_session: Session) -> None:
    user = make_user(db_session, username="tg_blocked_user", role=UserRole.HR)
    pref = NotificationPreference(
        user_id=user.id,
        timezone="Europe/Moscow",
        quiet_hours_start="21:00",
        quiet_hours_end="08:00",
        workdays=[1, 2, 3, 4, 5],
        enabled_types=[member.value for member in NotificationType],
        enabled_channels=["telegram"],
        telegram_chat_id=112233,
        telegram_opt_in=True,
        telegram_consent_at=utc_now(),
    )
    db_session.add(pref)

    outbox = schedule(
        db_session,
        recipient_user_id=user.id,
        channel=DeliveryChannel.TELEGRAM,
        type_=NotificationType.EVENT_ASSIGNED,
        title="Событие",
        dedupe_key="tg-blocked-1",
    )
    assert outbox is not None
    now = utc_now()
    outbox.status = DeliveryStatus.SENDING
    outbox.lease_expires_at = now + timedelta(seconds=120)
    db_session.commit()

    settings = _worker_settings()

    with patch(
        "app.worker.telegram_send_message",
        side_effect=TelegramBlockedError("Bot blocked"),
    ):
        status_result = process_row(db_session, outbox, settings=settings, now=utc_now())
        assert status_result == "failed"

    db_session.refresh(outbox)
    assert outbox.status == DeliveryStatus.FAILED
    assert outbox.error_class == "bot_blocked"


def test_email_worker_delivery_success(db_session: Session) -> None:
    user = make_user(db_session, username="em_worker_user", role=UserRole.HR)
    pref = NotificationPreference(
        user_id=user.id,
        timezone="Europe/Moscow",
        quiet_hours_start="21:00",
        quiet_hours_end="08:00",
        workdays=[1, 2, 3, 4, 5],
        enabled_types=[member.value for member in NotificationType],
        enabled_channels=["email"],
        email_address="worker_user@example.com",
        email_opt_in=True,
        email_consent_at=utc_now(),
        email_consent_source="settings",
        email_consent_policy_version="1.0",
    )
    db_session.add(pref)

    outbox = schedule(
        db_session,
        recipient_user_id=user.id,
        channel=DeliveryChannel.EMAIL,
        type_=NotificationType.EVENT_ASSIGNED,
        title="Интервью назначено",
        body="Кандидат: Сидоров",
        dedupe_key="em-succ-1",
    )
    assert outbox is not None
    now = utc_now()
    outbox.status = DeliveryStatus.SENDING
    outbox.lease_expires_at = now + timedelta(seconds=120)
    db_session.commit()

    settings = _worker_settings()

    with patch(
        "app.worker.smtp_send_email",
        return_value=SmtpSendResult(accepted=True, provider_message_id="<msg-999@test.com>"),
    ) as mock_send:
        status_result = process_row(db_session, outbox, settings=settings, now=now)
        assert status_result == "accepted"
        mock_send.assert_called_once()

    db_session.refresh(outbox)
    assert outbox.status == DeliveryStatus.ACCEPTED
    assert outbox.accepted_at is not None
    assert outbox.provider_message_id == "<msg-999@test.com>"
    assert outbox.attempts == 1


def test_email_worker_recipient_refused_fails_permanently(db_session: Session) -> None:
    user = make_user(db_session, username="em_refused_user", role=UserRole.HR)
    pref = NotificationPreference(
        user_id=user.id,
        timezone="Europe/Moscow",
        quiet_hours_start="21:00",
        quiet_hours_end="08:00",
        workdays=[1, 2, 3, 4, 5],
        enabled_types=[member.value for member in NotificationType],
        enabled_channels=["email"],
        email_address="bad@example.com",
        email_opt_in=True,
        email_consent_at=utc_now(),
    )
    db_session.add(pref)

    outbox = schedule(
        db_session,
        recipient_user_id=user.id,
        channel=DeliveryChannel.EMAIL,
        type_=NotificationType.EVENT_ASSIGNED,
        title="Тест",
        dedupe_key="em-refused-1",
    )
    assert outbox is not None
    now = utc_now()
    outbox.status = DeliveryStatus.SENDING
    outbox.lease_expires_at = now + timedelta(seconds=120)
    db_session.commit()

    settings = _worker_settings()

    with patch(
        "app.worker.smtp_send_email",
        side_effect=SmtpRecipientError("550 User not found"),
    ):
        status_result = process_row(db_session, outbox, settings=settings, now=utc_now())
        assert status_result == "failed"

    db_session.refresh(outbox)
    assert outbox.status == DeliveryStatus.FAILED
    assert outbox.error_class == "recipient_rejected"


def test_worker_skips_when_opt_in_missing(db_session: Session) -> None:
    user = make_user(db_session, username="no_optin_user", role=UserRole.HR)
    pref = NotificationPreference(
        user_id=user.id,
        timezone="Europe/Moscow",
        quiet_hours_start="21:00",
        quiet_hours_end="08:00",
        workdays=[1, 2, 3, 4, 5],
        enabled_types=[member.value for member in NotificationType],
        enabled_channels=["telegram"],
        telegram_chat_id=555555,
        telegram_opt_in=False,  # NO OPT-IN
    )
    db_session.add(pref)

    outbox = schedule(
        db_session,
        recipient_user_id=user.id,
        channel=DeliveryChannel.TELEGRAM,
        type_=NotificationType.EVENT_ASSIGNED,
        title="Тест",
        dedupe_key="no-optin-1",
    )
    assert outbox is not None
    now = utc_now()
    outbox.status = DeliveryStatus.SENDING
    outbox.lease_expires_at = now + timedelta(seconds=120)
    db_session.commit()

    settings = _worker_settings()

    status_result = process_row(db_session, outbox, settings=settings, now=utc_now())
    assert status_result == "skipped"

    db_session.refresh(outbox)
    assert outbox.status == DeliveryStatus.SKIPPED
    assert outbox.error_class == "no_consent"
