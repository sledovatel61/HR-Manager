"""Unit tests for external-channel worker delivery (SQLite, fake senders)."""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

import app.worker as worker_module
from app.config import Settings
from app.models import (
    AccessGrant,
    AccessGrantScope,
    AuditAction,
    AuditEvent,
    DeliveryChannel,
    DeliveryStatus,
    Notification,
    NotificationDeliveryAttempt,
    NotificationOutbox,
    NotificationPreference,
    NotificationType,
    TelegramLink,
    User,
    UserEmail,
    UserRole,
)
from app.notification_service import (
    EMAIL_VERIFICATION_TEMPLATE,
    schedule,
    schedule_fan_out,
)
from app.smtp import SmtpSendResult
from app.telegram import TelegramSendResult
from app.worker import process_external_row, process_row
from tests.conftest import make_user

NOW = datetime(2026, 9, 4, 12, 0, 0, tzinfo=UTC)


def _settings(**overrides: str) -> Settings:
    values = {
        "APP_ENV": "test",
        "SECRET_KEY": "x",
        "DATABASE_URL": "sqlite+pysqlite://",
        "WORKER_MAX_ATTEMPTS": "3",
        "WORKER_BACKOFF_BASE_S": "60",
        "WORKER_BACKOFF_CAP_S": "3600",
        "WORKER_LEASE_SECONDS": "120",
        "TELEGRAM_ENABLED": "true",
        "TELEGRAM_BOT_TOKEN": "test-token",
        "TELEGRAM_BOT_USERNAME": "hr_test_bot",
        "SMTP_ENABLED": "true",
        "SMTP_HOST": "smtp.example.test",
        "SMTP_FROM_ADDRESS": "noreply@example.test",
    }
    values.update(overrides)
    return Settings.model_validate(values)


def _user_with_telegram(
    db: Session, username: str, *, opt_in: bool = True, revoked: bool = False
) -> User:
    user = make_user(db, username=username, role=UserRole.HR)
    db.add(
        TelegramLink(
            user_id=user.id,
            chat_id=123456789,
            linked_at=NOW,
            revoked_at=NOW if revoked else None,
            revoke_reason="user" if revoked else None,
        )
    )
    db.add(
        NotificationPreference(
            user_id=user.id,
            timezone="Europe/Moscow",
            quiet_hours_start="21:00",
            quiet_hours_end="08:00",
            workdays=[1, 2, 3, 4, 5],
            enabled_types=["system_alert"],
            enabled_channels=["in_app", "telegram"],
            telegram_opt_in=opt_in,
            telegram_consent_granted=opt_in,
            telegram_consent_at=NOW if opt_in else None,
            telegram_consent_source="web-ui" if opt_in else None,
            telegram_consent_policy_version="phase9-v1" if opt_in else None,
        )
    )
    db.commit()
    return user


def _user_with_email(
    db: Session, username: str, *, opt_in: bool = True, verified: bool = True
) -> User:
    user = make_user(db, username=username, role=UserRole.HR)
    db.add(
        UserEmail(
            user_id=user.id,
            email="hr@example.com" if verified else None,
            verified_at=NOW if verified else None,
        )
    )
    db.add(
        NotificationPreference(
            user_id=user.id,
            timezone="Europe/Moscow",
            quiet_hours_start="21:00",
            quiet_hours_end="08:00",
            workdays=[1, 2, 3, 4, 5],
            enabled_types=["system_alert"],
            enabled_channels=["in_app", "email"],
            email_opt_in=opt_in,
            email_consent_granted=opt_in,
            email_consent_at=NOW if opt_in else None,
            email_consent_source="web-ui" if opt_in else None,
            email_consent_policy_version="phase9-v1" if opt_in else None,
        )
    )
    db.commit()
    return user


def _claimed(
    db: Session,
    user: User,
    channel: DeliveryChannel,
    *,
    template: str | None = None,
    external_recipient: str | None = None,
) -> NotificationOutbox:
    row = schedule(
        db,
        recipient_user_id=user.id if external_recipient is None else None,
        external_recipient=external_recipient,
        channel=channel,
        type_=NotificationType.SYSTEM_ALERT,
        title="Проверка канала",
        body="Тело проверки",
        dedupe_key=f"ext:{uuid4().hex}",
        scheduled_at=NOW,
        template=template,
    )
    assert row is not None
    row.status = DeliveryStatus.SENDING
    row.started_at = NOW
    row.lease_expires_at = NOW + timedelta(minutes=2)
    db.commit()
    db.refresh(row)
    return row


def _attempts(db: Session, row: NotificationOutbox) -> list[NotificationDeliveryAttempt]:
    return list(
        db.execute(
            select(NotificationDeliveryAttempt)
            .where(NotificationDeliveryAttempt.outbox_id == row.id)
            .order_by(NotificationDeliveryAttempt.attempt_no)
        )
        .scalars()
        .all()
    )


def test_telegram_accepted_with_provider_id(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    user = _user_with_telegram(db_session, "tg-ok")
    row = _claimed(db_session, user, DeliveryChannel.TELEGRAM)
    calls: list = []

    def fake_send(config, *, chat_id, title, body):  # type: ignore[no-untyped-def]
        calls.append((chat_id, title, body))
        return TelegramSendResult(outcome="accepted", provider_message_id="msg-1")

    monkeypatch.setattr(worker_module, "_send_telegram_impl", fake_send)
    status = process_external_row(db_session, row.id, settings=_settings(), now=NOW)
    assert status == "accepted"
    assert calls == [(123456789, "Проверка канала", "Тело проверки")]
    db_session.refresh(row)
    assert row.status == DeliveryStatus.ACCEPTED
    assert row.accepted_at is not None
    assert row.provider_message_id == "msg-1"
    assert row.delivered_at is None  # accepted is never delivered/read
    attempts = _attempts(db_session, row)
    assert len(attempts) == 1
    assert attempts[0].outcome.value == "accepted"
    assert attempts[0].provider_message_id == "msg-1"
    # No in-app notification is fabricated for external delivery.
    assert db_session.execute(select(Notification)).scalars().all() == []


def test_telegram_accepted_without_provider_id_stores_null(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    user = _user_with_telegram(db_session, "tg-noid")
    row = _claimed(db_session, user, DeliveryChannel.TELEGRAM)

    def fake_send(config, *, chat_id, title, body):  # type: ignore[no-untyped-def]
        return TelegramSendResult(outcome="accepted", provider_message_id=None)

    monkeypatch.setattr(worker_module, "_send_telegram_impl", fake_send)
    assert process_external_row(db_session, row.id, settings=_settings(), now=NOW) == "accepted"
    db_session.refresh(row)
    assert row.provider_message_id is None


def test_telegram_temp_error_retries_with_backoff(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    user = _user_with_telegram(db_session, "tg-temp")
    row = _claimed(db_session, user, DeliveryChannel.TELEGRAM)

    def fake_send(config, *, chat_id, title, body):  # type: ignore[no-untyped-def]
        return TelegramSendResult(
            outcome="temp_error", error_code="http_500", error_class="telegram_server"
        )

    monkeypatch.setattr(worker_module, "_send_telegram_impl", fake_send)
    assert process_external_row(db_session, row.id, settings=_settings(), now=NOW) == "queued"
    db_session.refresh(row)
    assert row.status == DeliveryStatus.QUEUED
    assert row.attempts == 1
    assert row.next_attempt_at == NOW + timedelta(seconds=60)
    assert row.error_class == "telegram_server"
    assert _attempts(db_session, row)[0].outcome.value == "failed"


def test_telegram_retry_after_is_honored(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    user = _user_with_telegram(db_session, "tg-429")
    row = _claimed(db_session, user, DeliveryChannel.TELEGRAM)

    def fake_send(config, *, chat_id, title, body):  # type: ignore[no-untyped-def]
        return TelegramSendResult(
            outcome="temp_error",
            error_code="http_429",
            error_class="telegram_rate_limited",
            retry_after_s=120,
        )

    monkeypatch.setattr(worker_module, "_send_telegram_impl", fake_send)
    assert process_external_row(db_session, row.id, settings=_settings(), now=NOW) == "queued"
    db_session.refresh(row)
    assert row.next_attempt_at == NOW + timedelta(seconds=120)


def test_telegram_retries_are_bounded_then_fail(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    pilot = make_user(db_session, username="pilot", role=UserRole.ADMIN)
    db_session.add(AccessGrant(user_id=pilot.id, scope=AccessGrantScope.PILOT_FULL_ACCESS))
    db_session.commit()
    user = _user_with_telegram(db_session, "tg-max")
    row = _claimed(db_session, user, DeliveryChannel.TELEGRAM)
    row.attempts = 2  # max is 3: this attempt exhausts the budget
    db_session.commit()

    def fake_send(config, *, chat_id, title, body):  # type: ignore[no-untyped-def]
        return TelegramSendResult(
            outcome="temp_error", error_code="timeout", error_class="telegram_timeout"
        )

    monkeypatch.setattr(worker_module, "_send_telegram_impl", fake_send)
    assert process_external_row(db_session, row.id, settings=_settings(), now=NOW) == "failed"
    db_session.refresh(row)
    assert row.status == DeliveryStatus.FAILED
    assert row.failed_at is not None
    # The pilot gets an in-app system alert (never fanned out to externals).
    alerts = (
        db_session.execute(select(NotificationOutbox).where(NotificationOutbox.channel == "in_app"))
        .scalars()
        .all()
    )
    assert any(r.notification_type == NotificationType.SYSTEM_ALERT for r in alerts)


def test_telegram_blocked_fails_and_auto_revokes(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    user = _user_with_telegram(db_session, "tg-blocked")
    row = _claimed(db_session, user, DeliveryChannel.TELEGRAM)

    def fake_send(config, *, chat_id, title, body):  # type: ignore[no-untyped-def]
        return TelegramSendResult(
            outcome="perm_error", error_code="http_403", error_class="telegram_blocked"
        )

    monkeypatch.setattr(worker_module, "_send_telegram_impl", fake_send)
    assert process_external_row(db_session, row.id, settings=_settings(), now=NOW) == "failed"
    link = db_session.get(TelegramLink, user.id)
    assert link is not None and link.revoked_at is not None
    assert link.revoke_reason == "auto_blocked"
    audit = db_session.execute(select(AuditEvent)).scalars().all()
    assert any(e.action == AuditAction.CHANNEL_AUTO_REVOKED for e in audit)
    # The audit record carries no chat id.
    auto = next(e for e in audit if e.action == AuditAction.CHANNEL_AUTO_REVOKED)
    assert "123456789" not in (auto.details or "")


def test_telegram_perm_error_without_revoke_keeps_binding(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    user = _user_with_telegram(db_session, "tg-badreq")
    row = _claimed(db_session, user, DeliveryChannel.TELEGRAM)

    def fake_send(config, *, chat_id, title, body):  # type: ignore[no-untyped-def]
        return TelegramSendResult(
            outcome="perm_error", error_code="http_400", error_class="telegram_bad_request"
        )

    monkeypatch.setattr(worker_module, "_send_telegram_impl", fake_send)
    assert process_external_row(db_session, row.id, settings=_settings(), now=NOW) == "failed"
    link = db_session.get(TelegramLink, user.id)
    assert link is not None and link.revoked_at is None


@pytest.mark.parametrize(
    ("setup", "expected_class"),
    [
        ("no-consent", "consent_missing"),
        ("revoked", "binding_revoked"),
        ("no-binding", "binding_missing"),
        ("disabled", "channel_not_configured"),
    ],
)
def test_telegram_skips_are_explicit(
    db_session: Session, monkeypatch: pytest.MonkeyPatch, setup: str, expected_class: str
) -> None:
    if setup == "no-consent":
        user = _user_with_telegram(db_session, "tg-noconsent", opt_in=False)
    elif setup == "revoked":
        user = _user_with_telegram(db_session, "tg-rev", revoked=True)
    elif setup == "no-binding":
        user = make_user(db_session, username="tg-nobind", role=UserRole.HR)
    else:
        user = _user_with_telegram(db_session, "tg-dis")
    row = _claimed(db_session, user, DeliveryChannel.TELEGRAM)

    def fake_send(config, *, chat_id, title, body):  # type: ignore[no-untyped-def]
        raise AssertionError("sender must not be called for skipped rows")

    monkeypatch.setattr(worker_module, "_send_telegram_impl", fake_send)
    settings = (
        _settings(TELEGRAM_ENABLED="false", TELEGRAM_BOT_TOKEN="", TELEGRAM_BOT_USERNAME="")
        if setup == "disabled"
        else _settings()
    )
    assert process_external_row(db_session, row.id, settings=settings, now=NOW) == "skipped"
    db_session.refresh(row)
    assert row.error_class == expected_class
    assert _attempts(db_session, row)[0].outcome.value == "skipped"


def test_email_accepted(db_session: Session, monkeypatch: pytest.MonkeyPatch) -> None:
    user = _user_with_email(db_session, "em-ok")
    row = _claimed(db_session, user, DeliveryChannel.EMAIL)
    calls: list = []

    def fake_send(config, *, to_address, subject, text_body):  # type: ignore[no-untyped-def]
        calls.append((to_address, subject))
        return SmtpSendResult(outcome="accepted")

    monkeypatch.setattr(worker_module, "_send_email_impl", fake_send)
    assert process_external_row(db_session, row.id, settings=_settings(), now=NOW) == "accepted"
    assert calls == [("hr@example.com", "Проверка канала")]
    db_session.refresh(row)
    assert row.status == DeliveryStatus.ACCEPTED
    assert row.provider_message_id is None  # plain SMTP returns no queue id


def test_email_unverified_and_no_consent_skip(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    unverified = _user_with_email(db_session, "em-unver", verified=False)
    row1 = _claimed(db_session, unverified, DeliveryChannel.EMAIL)
    noconsent = _user_with_email(db_session, "em-noconsent", opt_in=False)
    row2 = _claimed(db_session, noconsent, DeliveryChannel.EMAIL)

    def fake_send(config, *, to_address, subject, text_body):  # type: ignore[no-untyped-def]
        raise AssertionError("sender must not be called for skipped rows")

    monkeypatch.setattr(worker_module, "_send_email_impl", fake_send)
    assert process_external_row(db_session, row1.id, settings=_settings(), now=NOW) == "skipped"
    db_session.refresh(row1)
    assert row1.error_class == "address_unverified"
    assert process_external_row(db_session, row2.id, settings=_settings(), now=NOW) == "skipped"
    db_session.refresh(row2)
    assert row2.error_class == "consent_missing"


def test_verification_email_goes_to_external_recipient(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    user = make_user(db_session, username="em-verify", role=UserRole.HR)
    row = _claimed(
        db_session,
        user,
        DeliveryChannel.EMAIL,
        template=EMAIL_VERIFICATION_TEMPLATE,
        external_recipient="pending@example.com",
    )
    calls: list = []

    def fake_send(config, *, to_address, subject, text_body):  # type: ignore[no-untyped-def]
        calls.append(to_address)
        return SmtpSendResult(outcome="accepted")

    monkeypatch.setattr(worker_module, "_send_email_impl", fake_send)
    assert process_external_row(db_session, row.id, settings=_settings(), now=NOW) == "accepted"
    assert calls == ["pending@example.com"]


def test_cancel_race_wins_sender_not_called(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    user = _user_with_telegram(db_session, "tg-cancelrace")
    row = _claimed(db_session, user, DeliveryChannel.TELEGRAM)
    # An admin cancels after the claim but before processing.
    row.status = DeliveryStatus.CANCELLED
    row.cancelled_at = NOW
    row.lease_expires_at = None
    db_session.commit()

    def fake_send(config, *, chat_id, title, body):  # type: ignore[no-untyped-def]
        raise AssertionError("cancelled rows must never be sent")

    monkeypatch.setattr(worker_module, "_send_telegram_impl", fake_send)
    assert process_external_row(db_session, row.id, settings=_settings(), now=NOW) == "cancelled"


def test_unexpected_sender_exception_becomes_bounded_retry(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    user = _user_with_telegram(db_session, "tg-boom")
    row = _claimed(db_session, user, DeliveryChannel.TELEGRAM)

    def fake_send(config, *, chat_id, title, body):  # type: ignore[no-untyped-def]
        raise RuntimeError("unexpected transport bug")

    monkeypatch.setattr(worker_module, "_send_telegram_impl", fake_send)
    assert process_external_row(db_session, row.id, settings=_settings(), now=NOW) == "queued"
    db_session.refresh(row)
    assert row.error_class == "transport_error"


def test_process_row_routes_external_through_provider_path(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    user = _user_with_telegram(db_session, "tg-route")
    row = _claimed(db_session, user, DeliveryChannel.TELEGRAM)

    def fake_send(config, *, chat_id, title, body):  # type: ignore[no-untyped-def]
        return TelegramSendResult(outcome="accepted", provider_message_id="p1")

    monkeypatch.setattr(worker_module, "_send_telegram_impl", fake_send)
    # process_row shares quiet-hours/cancel handling, then delegates.
    assert process_row(db_session, row, settings=_settings(), now=NOW) == "accepted"


def test_fan_out_creates_external_rows_only_with_consent(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    outsider = make_user(db_session, username="fanout-plain", role=UserRole.HR)
    rows = schedule_fan_out(
        db_session,
        type_=NotificationType.SYSTEM_ALERT,
        recipient_user_id=outsider.id,
        dedupe_key="fanout:plain",
        scheduled_at=NOW,
        settings=_settings(),
    )
    assert len(rows) == 1  # in-app only, nothing enabled silently

    user = _user_with_telegram(db_session, "fanout-full")
    db_session.add(UserEmail(user_id=user.id, email="full@example.com", verified_at=NOW))
    preference = db_session.get(NotificationPreference, user.id)
    assert preference is not None
    preference.email_opt_in = True
    preference.email_consent_granted = True
    db_session.commit()
    rows = schedule_fan_out(
        db_session,
        type_=NotificationType.SYSTEM_ALERT,
        recipient_user_id=user.id,
        dedupe_key="fanout:full",
        scheduled_at=NOW,
        settings=_settings(),
    )
    assert len(rows) == 3
    channels = sorted(r.channel.value for r in rows if r is not None)
    assert channels == ["email", "in_app", "telegram"]
    for row in rows:
        assert row is not None
    keys = sorted(r.idempotency_key for r in rows if r is not None)
    assert keys[0].endswith(":email") or ":email" in keys[1]
    # Idempotent: a repeated fan-out schedules nothing new.
    again = schedule_fan_out(
        db_session,
        type_=NotificationType.SYSTEM_ALERT,
        recipient_user_id=user.id,
        dedupe_key="fanout:full",
        scheduled_at=NOW,
        settings=_settings(),
    )
    assert all(r is None for r in again)


def test_opt_in_without_explicit_grant_skips_without_network(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Fail-closed consent: opt_in=true alone (grant missing/false) never
    activates — not even with the channel listed in enabled_channels."""

    def fake_send(*args: object, **kwargs: object) -> object:
        raise AssertionError("sender must not be called without a complete grant")

    monkeypatch.setattr(worker_module, "_send_telegram_impl", fake_send)
    monkeypatch.setattr(worker_module, "_send_email_impl", fake_send)

    user = _user_with_telegram(db_session, "tg-halfgrant")
    preference = db_session.get(NotificationPreference, user.id)
    assert preference is not None
    preference.telegram_opt_in = True
    preference.telegram_consent_granted = False
    preference.enabled_channels = ["in_app", "telegram", "email"]
    db_session.commit()
    row = _claimed(db_session, user, DeliveryChannel.TELEGRAM)
    assert process_external_row(db_session, row.id, settings=_settings(), now=NOW) == "skipped"
    db_session.refresh(row)
    assert row.error_class == "consent_missing"

    mail_user = _user_with_email(db_session, "em-halfgrant")
    mail_preference = db_session.get(NotificationPreference, mail_user.id)
    assert mail_preference is not None
    mail_preference.email_opt_in = True
    mail_preference.email_consent_granted = False
    db_session.commit()
    mail_row = _claimed(db_session, mail_user, DeliveryChannel.EMAIL)
    assert process_external_row(db_session, mail_row.id, settings=_settings(), now=NOW) == "skipped"
    db_session.refresh(mail_row)
    assert mail_row.error_class == "consent_missing"


def test_opt_out_after_queue_stops_delivery_no_resend(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A revoke between queueing and sending stops the send: the worker
    re-validates consent at send time, so opt-out never resends."""

    def fake_send(*args: object, **kwargs: object) -> object:
        raise AssertionError("sender must not be called after opt-out")

    monkeypatch.setattr(worker_module, "_send_telegram_impl", fake_send)
    user = _user_with_telegram(db_session, "tg-optout")
    row = _claimed(db_session, user, DeliveryChannel.TELEGRAM)
    preference = db_session.get(NotificationPreference, user.id)
    assert preference is not None
    preference.telegram_opt_in = False
    preference.telegram_consent_granted = False
    db_session.commit()
    assert process_external_row(db_session, row.id, settings=_settings(), now=NOW) == "skipped"
    db_session.refresh(row)
    assert row.error_class == "consent_missing"
    assert _attempts(db_session, row)[0].outcome.value == "skipped"


def test_fan_out_ignores_enabled_channels_without_grant(db_session: Session) -> None:
    """Scheduling consults the explicit grant, never enabled_channels alone."""
    user = make_user(db_session, username="fanout-channels", role=UserRole.HR)
    db_session.add(TelegramLink(user_id=user.id, chat_id=777001, linked_at=NOW))
    db_session.add(
        NotificationPreference(
            user_id=user.id,
            timezone="Europe/Moscow",
            quiet_hours_start="21:00",
            quiet_hours_end="08:00",
            workdays=[1, 2, 3, 4, 5],
            enabled_types=["system_alert"],
            enabled_channels=["in_app", "telegram"],
            telegram_opt_in=True,
            telegram_consent_granted=False,
        )
    )
    db_session.commit()
    rows = schedule_fan_out(
        db_session,
        type_=NotificationType.SYSTEM_ALERT,
        recipient_user_id=user.id,
        dedupe_key="fanout:no-grant",
        scheduled_at=NOW,
        settings=_settings(),
    )
    assert len(rows) == 1  # in-app only
    assert rows[0] is not None and rows[0].channel == DeliveryChannel.IN_APP
