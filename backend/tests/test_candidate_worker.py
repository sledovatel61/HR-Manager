"""Unit tests for the candidate-message worker bus (phase 10, SQLite).

Claims/processes candidate rows exactly like the phase-8/9 worker does for
outbox rows. Provider I/O is replaced with fake senders — no network, no
real contacts. Covers: accepted handoff, bounded retries, honest skip for a
disabled channel, re-validation (consent / recipient / event / token), quiet
hours, cancel-wins and lease recovery.
"""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

import app.worker as worker_module
from app.candidate_communications import (
    CANDIDATE_CONSENT_POLICY_VERSION,
    EMAIL_CHANNEL,
    TELEGRAM_CHANNEL,
)
from app.config import Settings
from app.models import (
    AccessGrant,
    AccessGrantScope,
    Candidate,
    CandidateContactChannel,
    CandidateMessage,
    CandidateMessageAttempt,
    CandidateMessageSource,
    CandidateMessageType,
    Event,
    EventStatus,
    EventType,
    UserRole,
)
from app.smtp import SmtpSendResult
from app.utils import utc_now
from tests.conftest import make_candidate, make_event, make_user

NOW = datetime(2026, 9, 8, 12, 0, 0, tzinfo=UTC)


def _settings(**overrides: object) -> Settings:
    values: dict[str, object] = {
        "APP_ENV": "test",
        "SECRET_KEY": "phase10-worker-secret",
        "DATABASE_URL": "sqlite+pysqlite://",
        "WORKER_MAX_ATTEMPTS": "3",
        "WORKER_BACKOFF_BASE_S": "60",
        "WORKER_BACKOFF_CAP_S": "3600",
        "WORKER_LEASE_SECONDS": "120",
        "NOTIFICATION_DEFAULT_TIMEZONE": "UTC",
        "NOTIFICATION_QUIET_HOURS_START": "21:00",
        "NOTIFICATION_QUIET_HOURS_END": "08:00",
        "SMTP_ENABLED": "true",
        "SMTP_HOST": "smtp.example.test",
        "SMTP_FROM_ADDRESS": "noreply@example.test",
        "TELEGRAM_ENABLED": "true",
        "TELEGRAM_BOT_TOKEN": "test-token",
        "TELEGRAM_BOT_USERNAME": "hr_test_bot",
    }
    values.update(overrides)
    return Settings.model_validate(values)


def _bind(
    db: Session,
    candidate: Candidate,
    channel: str,
    *,
    email: str | None = None,
    chat_id: int | None = None,
    revoked: bool = False,
) -> CandidateContactChannel:
    row = CandidateContactChannel(
        candidate_id=candidate.id,
        channel=channel,
        email_address=email if channel == EMAIL_CHANNEL else None,
        chat_id=chat_id if channel == TELEGRAM_CHANNEL else None,
        consent_granted=not revoked,
        consent_at=NOW if not revoked else None,
        consent_source="candidate_email_link" if not revoked and channel == EMAIL_CHANNEL else None,
        consent_policy_version=CANDIDATE_CONSENT_POLICY_VERSION if not revoked else None,
        revoked_at=NOW if revoked else None,
        revoke_reason="hr_revoked" if revoked else None,
    )
    db.add(row)
    db.commit()
    return row


def _candidate(db: Session, username: str, *, email: str = "candidate@example.test") -> Candidate:
    owner = make_user(db, username=username, role=UserRole.HR)
    return make_candidate(db, owner=owner, email=email)


def _queue(
    db: Session,
    candidate: Candidate,
    *,
    channel: str = EMAIL_CHANNEL,
    email: str | None = None,
    chat_id: int | None = None,
    message_type: CandidateMessageType = CandidateMessageType.DOCUMENTS_REQUEST,
    event: Event | None = None,
    event_version: int | None = None,
    bypass: bool = False,
    status: str = "sending",
) -> CandidateMessage:
    row = CandidateMessage(
        candidate_id=candidate.id,
        channel=channel,
        message_type=message_type,
        source=CandidateMessageSource.MANUAL,
        title="Запрос документов",
        body="Здравствуйте! Нужны документы.",
        recipient_email=email,
        recipient_chat_id=chat_id,
        event_id=event.id if event is not None else None,
        event_version=event_version if event is not None else None,
        scheduled_at=NOW,
        queued_at=NOW,
        status="queued",
        idempotency_key=f"w:{uuid4().hex}",
        consent_snapshot={"channel": channel, "granted": True},
        quiet_hours_bypassed=bypass,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    if status == "sending":
        row.status = "sending"
        row.started_at = NOW
        row.lease_expires_at = NOW + timedelta(seconds=120)
        db.commit()
        db.refresh(row)
    return row


def _attempts(db: Session, row: CandidateMessage) -> list[CandidateMessageAttempt]:
    return list(
        db.execute(
            select(CandidateMessageAttempt)
            .where(CandidateMessageAttempt.message_id == row.id)
            .order_by(CandidateMessageAttempt.attempt_no)
        )
        .scalars()
        .all()
    )


def test_candidate_email_accepted_with_provider_id(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    candidate = _candidate(db_session, "w-email-ok")
    _bind(db_session, candidate, EMAIL_CHANNEL, email="candidate@example.test")
    row = _queue(db_session, candidate, email="candidate@example.test", status="sending")
    calls: list = []

    def fake_send(config, *, to_address, subject, text_body):  # type: ignore[no-untyped-def]
        calls.append((to_address, subject, text_body))
        return SmtpSendResult(outcome="accepted", provider_message_id="mail-1")

    monkeypatch.setattr(worker_module, "_send_email_impl", fake_send)
    status = worker_module.process_candidate_message(
        db_session, row.id, settings=_settings(), now=NOW
    )
    assert status == "accepted"
    assert calls == [("candidate@example.test", "Запрос документов", row.body)]
    db_session.refresh(row)
    assert row.status == "accepted"
    assert row.accepted_at == NOW
    assert row.delivered_at is None  # accepted is never 'delivered'/'read'
    assert row.provider_message_id == "mail-1"
    attempts = _attempts(db_session, row)
    assert len(attempts) == 1
    assert attempts[0].outcome == "accepted"
    # Channel health updated: last send ok, no error.
    channel = db_session.execute(
        select(CandidateContactChannel).where(CandidateContactChannel.candidate_id == candidate.id)
    ).scalar_one()
    assert channel.last_sent_at is not None
    assert channel.last_error_class is None


def test_candidate_consent_revoked_after_queue_cancels_without_network(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    candidate = _candidate(db_session, "w-revoke")
    _bind(db_session, candidate, EMAIL_CHANNEL, email="candidate@example.test")
    row = _queue(db_session, candidate, email="candidate@example.test", status="sending")
    # The candidate opts out while the row is claimed.
    channel = db_session.execute(
        select(CandidateContactChannel).where(CandidateContactChannel.candidate_id == candidate.id)
    ).scalar_one()
    channel.consent_granted = False
    channel.consent_at = None
    channel.revoked_at = NOW
    db_session.commit()

    def fake_send(config, *, to_address, subject, text_body):  # type: ignore[no-untyped-def]
        raise AssertionError("revoked consent must never reach the network")

    monkeypatch.setattr(worker_module, "_send_email_impl", fake_send)
    status = worker_module.process_candidate_message(
        db_session, row.id, settings=_settings(), now=NOW
    )
    assert status == "cancelled"
    db_session.refresh(row)
    assert row.status == "cancelled"
    assert row.error_class == "consent_revoked"
    assert _attempts(db_session, row) == []


def test_candidate_recipient_changed_after_queue_cancels(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    candidate = _candidate(db_session, "w-recipient")
    _bind(db_session, candidate, EMAIL_CHANNEL, email="old@example.test")
    row = _queue(db_session, candidate, email="old@example.test", status="sending")
    # Re-consent on a NEW address supersedes the queued snapshot.
    channel = db_session.execute(
        select(CandidateContactChannel).where(CandidateContactChannel.candidate_id == candidate.id)
    ).scalar_one()
    channel.email_address = "new@example.test"
    db_session.commit()

    def fake_send(config, *, to_address, subject, text_body):  # type: ignore[no-untyped-def]
        raise AssertionError("a letter for the old address must not be sent")

    monkeypatch.setattr(worker_module, "_send_email_impl", fake_send)
    status = worker_module.process_candidate_message(
        db_session, row.id, settings=_settings(), now=NOW
    )
    assert status == "cancelled"
    db_session.refresh(row)
    assert row.error_class == "recipient_changed"


def test_candidate_channel_disabled_skips_honestly(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = _settings(SMTP_ENABLED="false")
    candidate = _candidate(db_session, "w-disabled")
    _bind(db_session, candidate, EMAIL_CHANNEL, email="candidate@example.test")
    row = _queue(db_session, candidate, email="candidate@example.test", status="sending")

    def fake_send(config, *, to_address, subject, text_body):  # type: ignore[no-untyped-def]
        raise AssertionError("disabled channel must not be called")

    monkeypatch.setattr(worker_module, "_send_email_impl", fake_send)
    status = worker_module.process_candidate_message(db_session, row.id, settings=settings, now=NOW)
    assert status == "skipped"
    db_session.refresh(row)
    assert row.status == "skipped"
    assert row.error_class == "channel_not_configured"
    assert row.failed_at == NOW
    assert _attempts(db_session, row)[0].outcome == "skipped"


def test_candidate_event_version_mismatch_cancels_stale_letter(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    owner = make_user(db_session, username="w-stale", role=UserRole.HR)
    candidate = make_candidate(db_session, owner=owner, email="candidate@example.test")
    _bind(db_session, candidate, EMAIL_CHANNEL, email="candidate@example.test")
    event = make_event(
        db_session,
        candidate=candidate,
        author=owner,
        assignee=owner,
        type_=EventType.INTERVIEW,
        starts_at=NOW + timedelta(days=2),
        title="Собеседование",
    )
    row = _queue(
        db_session,
        candidate,
        email="candidate@example.test",
        message_type=CandidateMessageType.INTERVIEW_SCHEDULED,
        event=event,
        event_version=event.version,
        status="sending",
    )
    # The event was rescheduled after the row was queued (version bumped).
    event.version = 2
    db_session.commit()

    def fake_send(config, *, to_address, subject, text_body):  # type: ignore[no-untyped-def]
        raise AssertionError("stale event letter must not go out")

    monkeypatch.setattr(worker_module, "_send_email_impl", fake_send)
    status = worker_module.process_candidate_message(
        db_session, row.id, settings=_settings(), now=NOW
    )
    assert status == "cancelled"
    db_session.refresh(row)
    assert row.error_class == "event_stale"


def test_candidate_event_cancelled_after_queue_cancels_letter(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    owner = make_user(db_session, username="w-cancelled", role=UserRole.HR)
    candidate = make_candidate(db_session, owner=owner, email="candidate@example.test")
    _bind(db_session, candidate, EMAIL_CHANNEL, email="candidate@example.test")
    event = make_event(
        db_session,
        candidate=candidate,
        author=owner,
        assignee=owner,
        type_=EventType.INTERVIEW,
        starts_at=NOW + timedelta(days=2),
        title="Собеседование",
        status=EventStatus.SCHEDULED,
    )
    row = _queue(
        db_session,
        candidate,
        email="candidate@example.test",
        message_type=CandidateMessageType.INTERVIEW_REMINDER,
        event=event,
        event_version=event.version,
        status="sending",
    )
    event.status = EventStatus.CANCELLED
    event.cancelled_at = NOW
    db_session.commit()

    def fake_send(config, *, to_address, subject, text_body):  # type: ignore[no-untyped-def]
        raise AssertionError("cancelled interview must not remind")

    monkeypatch.setattr(worker_module, "_send_email_impl", fake_send)
    assert (
        worker_module.process_candidate_message(db_session, row.id, settings=_settings(), now=NOW)
        == "cancelled"
    )
    db_session.refresh(row)
    assert row.error_class == "event_stale"


def test_candidate_quiet_hours_defer_then_deliver(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = _settings()
    candidate = _candidate(db_session, "w-quiet")
    _bind(db_session, candidate, EMAIL_CHANNEL, email="candidate@example.test")
    row = _queue(db_session, candidate, email="candidate@example.test", status="sending")
    night = datetime(2026, 9, 8, 22, 30, tzinfo=UTC)  # inside 21:00-08:00

    def fake_send(config, *, to_address, subject, text_body):  # type: ignore[no-untyped-def]
        raise AssertionError("quiet hours must defer, not send")

    monkeypatch.setattr(worker_module, "_send_email_impl", fake_send)
    status = worker_module.process_candidate_message(
        db_session, row.id, settings=settings, now=night
    )
    assert status == "queued"
    db_session.refresh(row)
    assert row.status == "queued"
    assert row.attempts == 0
    assert row.next_attempt_at == datetime(2026, 9, 9, 8, 0, tzinfo=UTC)
    assert row.scheduled_at_effective == datetime(2026, 9, 9, 8, 0, tzinfo=UTC)

    # At the allowed time the same row is delivered.
    def real_send(config, *, to_address, subject, text_body):  # type: ignore[no-untyped-def]
        return SmtpSendResult(outcome="accepted")

    monkeypatch.setattr(worker_module, "_send_email_impl", real_send)
    claimed = worker_module.claim_candidate_batch(
        db_session, now=row.next_attempt_at or utc_now(), batch_size=10, lease_seconds=120
    )
    assert row.id in {c.id for c in claimed}
    status = worker_module.process_candidate_message(
        db_session, row.id, settings=settings, now=row.next_attempt_at or utc_now()
    )
    assert status == "accepted"


def test_candidate_bypassed_quiet_hours_sends_immediately(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    candidate = _candidate(db_session, "w-bypass")
    _bind(db_session, candidate, EMAIL_CHANNEL, email="candidate@example.test")
    row = _queue(
        db_session,
        candidate,
        email="candidate@example.test",
        bypass=True,
        status="sending",
    )
    night = datetime(2026, 9, 8, 22, 30, tzinfo=UTC)

    def fake_send(config, *, to_address, subject, text_body):  # type: ignore[no-untyped-def]
        return SmtpSendResult(outcome="accepted", provider_message_id="bypass-1")

    monkeypatch.setattr(worker_module, "_send_email_impl", fake_send)
    assert (
        worker_module.process_candidate_message(db_session, row.id, settings=_settings(), now=night)
        == "accepted"
    )


def test_candidate_temp_error_retries_then_fails(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = _settings(WORKER_MAX_ATTEMPTS="2")
    candidate = _candidate(db_session, "w-retry")
    _bind(db_session, candidate, EMAIL_CHANNEL, email="candidate@example.test")

    # First attempt: temporary failure -> requeued with bounded backoff.
    row = _queue(db_session, candidate, email="candidate@example.test", status="sending")

    def failing(config, *, to_address, subject, text_body):  # type: ignore[no-untyped-def]
        return SmtpSendResult(
            outcome="temp_error", error_code="smtp_timeout", error_class="smtp_timeout"
        )

    monkeypatch.setattr(worker_module, "_send_email_impl", failing)
    assert (
        worker_module.process_candidate_message(db_session, row.id, settings=settings, now=NOW)
        == "queued"
    )
    db_session.refresh(row)
    assert row.attempts == 1
    assert row.next_attempt_at == NOW + timedelta(seconds=60)

    # Claim again and exhaust the remaining attempt -> terminal failure.
    row.status = "sending"
    row.started_at = row.next_attempt_at
    row.lease_expires_at = row.next_attempt_at + timedelta(seconds=120)
    db_session.commit()
    assert (
        worker_module.process_candidate_message(db_session, row.id, settings=settings, now=NOW)
        == "failed"
    )
    db_session.refresh(row)
    assert row.status == "failed"
    assert row.failed_at == NOW
    assert row.error_class == "smtp_timeout"
    attempts = _attempts(db_session, row)
    assert len(attempts) == 2
    assert all(a.outcome == "failed" for a in attempts)


def test_candidate_lease_recovery_returns_row_to_queue(
    db_session: Session,
) -> None:
    candidate = _candidate(db_session, "w-lease")
    _bind(db_session, candidate, EMAIL_CHANNEL, email="candidate@example.test")
    row = _queue(
        db_session,
        candidate,
        email="candidate@example.test",
        status="sending",
    )
    recovered = worker_module.recover_stale_candidate_leases(
        db_session, now=NOW + timedelta(minutes=5), lease_seconds=120
    )
    assert recovered == 1
    db_session.refresh(row)
    assert row.status == "queued"
    assert row.attempts == 1
    assert row.lease_expires_at is None
    attempt = _attempts(db_session, row)[0]
    assert attempt.outcome == "failed"
    assert attempt.error_class == "lease_expired"


def test_cancel_wins_after_claim_sender_not_called(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    candidate = _candidate(db_session, "w-cancel")
    _bind(db_session, candidate, EMAIL_CHANNEL, email="candidate@example.test")
    row = _queue(db_session, candidate, email="candidate@example.test", status="sending")
    # An HR cancels the claimed row before processing starts (the lease is
    # released the same way the real cancel path does).
    row.status = "cancelled"
    row.cancelled_at = NOW
    row.started_at = None
    row.lease_expires_at = None
    db_session.commit()

    def fake_send(config, *, to_address, subject, text_body):  # type: ignore[no-untyped-def]
        raise AssertionError("cancelled rows must never be sent")

    monkeypatch.setattr(worker_module, "_send_email_impl", fake_send)
    assert (
        worker_module.process_candidate_message(db_session, row.id, settings=_settings(), now=NOW)
        == "cancelled"
    )
    db_session.refresh(row)
    assert _attempts(db_session, row) == []


def test_terminal_failure_alerts_pilot_without_pii(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = _settings(WORKER_MAX_ATTEMPTS="1")
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
    candidate = _candidate(db_session, "w-pilot")
    _bind(db_session, candidate, EMAIL_CHANNEL, email="candidate@example.test")
    row = _queue(db_session, candidate, email="candidate@example.test", status="sending")

    def failing(config, *, to_address, subject, text_body):  # type: ignore[no-untyped-def]
        return SmtpSendResult(
            outcome="perm_error", error_code="smtp_rejected", error_class="smtp_rejected"
        )

    monkeypatch.setattr(worker_module, "_send_email_impl", failing)
    assert (
        worker_module.process_candidate_message(db_session, row.id, settings=settings, now=NOW)
        == "failed"
    )
    # The pilot alert exists as an outbox row and carries no message text
    # or recipient PII.
    from app.models import NotificationOutbox, NotificationType

    alerts = (
        db_session.execute(
            select(NotificationOutbox).where(
                NotificationOutbox.notification_type == NotificationType.SYSTEM_ALERT
            )
        )
        .scalars()
        .all()
    )
    assert len(alerts) == 1
    assert alerts[0].recipient_user_id == pilot.id
    assert row.body not in (alerts[0].body or "")
    assert "candidate@example.test" not in (alerts[0].body or "")


def test_candidate_claim_batch_claims_only_due(db_session: Session) -> None:
    candidate = _candidate(db_session, "w-claim")
    _bind(db_session, candidate, EMAIL_CHANNEL, email="candidate@example.test")
    due = _queue(db_session, candidate, email="candidate@example.test", status="queued")
    later = _queue(
        db_session,
        candidate,
        email="candidate@example.test",
        status="queued",
    )
    later.scheduled_at = NOW + timedelta(hours=3)
    db_session.commit()
    claimed = worker_module.claim_candidate_batch(
        db_session, now=NOW, batch_size=10, lease_seconds=120
    )
    ids = {row.id for row in claimed}
    assert due.id in ids
    assert later.id not in ids
    for row in claimed:
        assert row.status == "sending"
        assert row.lease_expires_at is not None
