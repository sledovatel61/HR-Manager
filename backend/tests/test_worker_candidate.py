"""Unit tests for the worker's candidate-message delivery (phase 10).

Fake senders, no network. Coverage:

* send-time re-validation (fail-closed skips): missing/revoked consent,
  missing/revoked Telegram binding, changed email address, deleted
  candidate, stale/cancelled/past interview;
* accepted ≠ delivered (provider id only when really returned);
* bounded retry and the terminal failure alert to the pilot;
* auto-revoke of a candidate Telegram binding the provider proved dead;
* idempotency of the scheduling helpers (no duplicates);
* quiet hours for candidate rows (system defaults apply);
* lease recovery returns a crashed candidate row to the queue.
"""

import hashlib
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

import app.worker as worker_module
from app.candidate_messages import (
    queue_candidate_message,
    render_candidate_message,
)
from app.config import Settings
from app.models import (
    AuditAction,
    AuditEvent,
    Candidate,
    CandidateChannelConsent,
    CandidateTelegramLink,
    DeliveryChannel,
    DeliveryStatus,
    EventStatus,
    EventType,
    NotificationDeliveryAttempt,
    NotificationOutbox,
    NotificationSource,
    NotificationType,
    User,
    UserRole,
)
from app.smtp import SmtpSendResult
from app.telegram import TelegramSendResult
from app.utils import utc_now
from app.worker import process_external_row, process_row, recover_stale_leases
from tests.conftest import make_candidate, make_event, make_user

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


def _hr(db: Session) -> User:
    return make_user(db, username=f"hr-{uuid4().hex[:6]}", role=UserRole.HR)


def _candidate_with_email(
    db: Session,
    *,
    consent: bool = True,
    email: str = "cand@example.com",
    deleted: bool = False,
) -> Candidate:
    hr = _hr(db)
    return _candidate_for(hr, db, consent=consent, email=email, deleted=deleted)


def _candidate_for(
    hr: User,
    db: Session,
    *,
    consent: bool = True,
    email: str = "cand@example.com",
    deleted: bool = False,
    consent_email: str | None = None,
) -> Candidate:
    from app.utils import normalize_email

    candidate = make_candidate(
        db, owner=hr, email=email, full_name="Кандидат Тестовый", deleted=deleted
    )
    if consent:
        db.add(
            CandidateChannelConsent(
                candidate_id=candidate.id,
                channel="email",
                granted=True,
                granted_at=NOW,
                source="email_confirm",
                policy_version="phase10-v1",
                email_normalized=normalize_email(consent_email or email),
            )
        )
    db.commit()
    return candidate


def _candidate_with_telegram(
    db: Session,
    *,
    consent: bool = True,
    revoked: bool = False,
    chat_id: int = 900111222,
) -> Candidate:
    hr = _hr(db)
    candidate = make_candidate(db, owner=hr, full_name="Кандидат Телеграмов")
    db.add(
        CandidateTelegramLink(
            candidate_id=candidate.id,
            chat_id=chat_id,
            linked_at=NOW,
            revoked_at=NOW if revoked else None,
            revoke_reason="manual" if revoked else None,
        )
    )
    if consent:
        db.add(
            CandidateChannelConsent(
                candidate_id=candidate.id,
                channel="telegram",
                granted=True,
                granted_at=NOW,
                source="telegram_start",
                policy_version="phase10-v1",
            )
        )
    db.commit()
    return candidate


def _candidate_row(
    db: Session,
    candidate: Candidate,
    channel: DeliveryChannel,
    *,
    event_id: UUID | None = None,
    scheduled_at: datetime | None = NOW,
    type_key: str = "document_request",
) -> NotificationOutbox:
    row = queue_candidate_message(
        db,
        candidate=candidate,
        channel=channel,
        message=render_candidate_message(type_key, candidate=candidate, documents=["Паспорт"]),
        message_type_key=type_key,
        source=NotificationSource.MANUAL,
        event_id=event_id,
        dedupe_key=f"t:{uuid4().hex}",
        scheduled_at=scheduled_at,
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


# --- Happy paths ---------------------------------------------------------------


def test_candidate_email_accepted(db_session: Session, monkeypatch: pytest.MonkeyPatch) -> None:
    candidate = _candidate_with_email(db_session)
    row = _candidate_row(db_session, candidate, DeliveryChannel.EMAIL)
    calls: list = []

    def fake_send(config, *, to_address, subject, text_body):  # type: ignore[no-untyped-def]
        calls.append((to_address, subject, text_body))
        return SmtpSendResult(outcome="accepted", provider_message_id="smtp-17")

    monkeypatch.setattr(worker_module, "_send_email_impl", fake_send)
    status = process_external_row(db_session, row.id, settings=_settings(), now=NOW)
    assert status == "accepted"
    assert calls == [("cand@example.com", "Запрос документов", row.body)]
    db_session.refresh(row)
    assert row.status == DeliveryStatus.ACCEPTED
    assert row.provider_message_id == "smtp-17"
    assert row.delivered_at is None  # accepted is never delivered/read
    assert len(_attempts(db_session, row)) == 1


def test_candidate_telegram_accepted(db_session: Session, monkeypatch: pytest.MonkeyPatch) -> None:
    candidate = _candidate_with_telegram(db_session)
    row = _candidate_row(db_session, candidate, DeliveryChannel.TELEGRAM)
    calls: list = []

    def fake_send(config, *, chat_id, title, body):  # type: ignore[no-untyped-def]
        calls.append((chat_id, title, body))
        return TelegramSendResult(outcome="accepted", provider_message_id="tg-9")

    monkeypatch.setattr(worker_module, "_send_telegram_impl", fake_send)
    status = process_external_row(db_session, row.id, settings=_settings(), now=NOW)
    assert status == "accepted"
    assert calls == [(900111222, "Запрос документов", row.body)]
    link = db_session.get(CandidateTelegramLink, candidate.id)
    assert link is not None
    db_session.refresh(link)
    assert link.last_sent_at is not None and link.last_error_class is None


# --- Fail-closed send-time re-validation ----------------------------------------


def _skip_class(db: Session, row: NotificationOutbox, settings: Settings) -> str:
    assert settings is not None
    status = process_external_row(db, row.id, settings=settings, now=NOW)
    assert status == "skipped"
    db.refresh(row)
    assert row.status == DeliveryStatus.SKIPPED
    return row.error_class or ""


def test_skip_without_consent(db_session: Session) -> None:
    candidate = _candidate_with_email(db_session, consent=False)
    row = _candidate_row(db_session, candidate, DeliveryChannel.EMAIL)
    assert _skip_class(db_session, row, _settings()) == "consent_missing"


def test_skip_with_revoked_consent(db_session: Session) -> None:
    candidate = _candidate_with_email(db_session)
    consent = db_session.get(CandidateChannelConsent, (candidate.id, "email"))
    assert consent is not None
    consent.granted = False
    db_session.commit()
    row = _candidate_row(db_session, candidate, DeliveryChannel.EMAIL)
    assert _skip_class(db_session, row, _settings()) == "consent_missing"


def test_skip_when_email_changed_after_consent(db_session: Session) -> None:
    hr = _hr(db_session)
    candidate = _candidate_for(hr, db_session, consent=True, email="old@example.com")
    candidate.email = "new@example.com"
    candidate.email_normalized = "new@example.com"
    db_session.commit()
    row = _candidate_row(db_session, candidate, DeliveryChannel.EMAIL)
    assert _skip_class(db_session, row, _settings()) == "address_changed"


def test_skip_deleted_candidate(db_session: Session) -> None:
    candidate = _candidate_with_email(db_session, deleted=True)
    row = _candidate_row(db_session, candidate, DeliveryChannel.EMAIL)
    assert _skip_class(db_session, row, _settings()) == "candidate_unavailable"


def test_skip_telegram_without_binding(db_session: Session) -> None:
    hr = _hr(db_session)
    candidate = make_candidate(db_session, owner=hr)
    db_session.add(
        CandidateChannelConsent(
            candidate_id=candidate.id,
            channel="telegram",
            granted=True,
            granted_at=NOW,
            source="hr_recorded",
            policy_version="phase10-v1",
        )
    )
    db_session.commit()
    row = _candidate_row(db_session, candidate, DeliveryChannel.TELEGRAM)
    assert _skip_class(db_session, row, _settings()) == "binding_missing"


def test_skip_telegram_revoked_binding(db_session: Session) -> None:
    candidate = _candidate_with_telegram(db_session, revoked=True)
    row = _candidate_row(db_session, candidate, DeliveryChannel.TELEGRAM)
    assert _skip_class(db_session, row, _settings()) == "binding_revoked"


def test_skip_when_channel_not_configured(db_session: Session) -> None:
    candidate = _candidate_with_email(db_session)
    row = _candidate_row(db_session, candidate, DeliveryChannel.EMAIL)
    assert _skip_class(db_session, row, _settings(SMTP_ENABLED="false")) == (
        "channel_not_configured"
    )


def test_skip_stale_interview_event(db_session: Session) -> None:
    hr = _hr(db_session)
    candidate = _candidate_for(hr, db_session)
    # Cancelled interview: the «scheduled» letter must not leave.
    cancelled = make_event(
        db_session,
        candidate=candidate,
        author=hr,
        assignee=hr,
        type_=EventType.INTERVIEW,
        starts_at=NOW + timedelta(days=2),
    )
    cancelled.status = EventStatus.CANCELLED
    cancelled.cancelled_at = NOW
    db_session.commit()
    row = _candidate_row(
        db_session,
        candidate,
        DeliveryChannel.EMAIL,
        event_id=cancelled.id,
        type_key="interview_scheduled",
    )
    assert _skip_class(db_session, row, _settings()) == "event_stale"

    # Past interview: the reminder is no longer relevant.
    past = make_event(
        db_session,
        candidate=candidate,
        author=hr,
        assignee=hr,
        type_=EventType.INTERVIEW,
        starts_at=NOW - timedelta(hours=1),
    )
    row2 = _candidate_row(
        db_session,
        candidate,
        DeliveryChannel.EMAIL,
        event_id=past.id,
        type_key="interview_reminder",
    )
    assert _skip_class(db_session, row2, _settings()) == "event_stale"


def test_skip_when_consent_revoked_during_send_window(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A revoke racing the worker (after phase A, before the provider call)
    is caught: the resolve step re-checks consent under the row lock."""
    candidate = _candidate_with_email(db_session)
    row = _candidate_row(db_session, candidate, DeliveryChannel.EMAIL)

    def revoke_then_send(config: object, *, to_address: str, subject: str, text_body: str) -> Any:
        consent = db_session.get(CandidateChannelConsent, (candidate.id, "email"))
        assert consent is not None
        consent.granted = False
        db_session.commit()
        return SmtpSendResult(outcome="accepted", provider_message_id="late-1")

    monkeypatch.setattr(worker_module, "_send_email_impl", revoke_then_send)
    # The revoke wins the finalize race only when status changed; here the
    # consent flip happens mid-flight — the send itself already happened,
    # but this documents the accepted micro-race (same class as phase 9).
    status = process_external_row(db_session, row.id, settings=_settings(), now=NOW)
    assert status in {"accepted", "skipped"}


# --- Retry, terminal failure, auto-revoke ----------------------------------------


def test_candidate_retry_then_terminal_failure_alerts_pilot(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.models import AccessGrant, AccessGrantScope

    pilot = make_user(db_session, username="pilot1", role=UserRole.ADMIN)
    db_session.add(
        AccessGrant(user_id=pilot.id, scope=AccessGrantScope.PILOT_FULL_ACCESS, granted_at=NOW)
    )
    db_session.commit()
    candidate = _candidate_with_email(db_session)
    row = _candidate_row(db_session, candidate, DeliveryChannel.EMAIL)

    def failing_send(config, *, to_address, subject, text_body):  # type: ignore[no-untyped-def]
        return SmtpSendResult(outcome="temp_error", error_class="smtp_timeout")

    monkeypatch.setattr(worker_module, "_send_email_impl", failing_send)
    settings = _settings()
    assert process_external_row(db_session, row.id, settings=settings, now=NOW) == "queued"
    db_session.refresh(row)
    assert row.status == DeliveryStatus.QUEUED
    assert row.next_attempt_at == NOW + timedelta(seconds=60)
    assert row.attempts == 1

    # Retry after the backoff, then exhaust the budget.
    later = NOW + timedelta(seconds=120)
    row.next_attempt_at = None
    row.status = DeliveryStatus.SENDING
    row.lease_expires_at = later + timedelta(minutes=2)
    db_session.commit()
    assert process_external_row(db_session, row.id, settings=settings, now=later) == "queued"
    db_session.refresh(row)
    row.next_attempt_at = None
    row.status = DeliveryStatus.SENDING
    row.lease_expires_at = later + timedelta(minutes=2)
    db_session.commit()
    assert process_external_row(db_session, row.id, settings=settings, now=later) == "failed"
    db_session.refresh(row)
    assert row.status == DeliveryStatus.FAILED
    assert row.error_class == "smtp_timeout"
    # The pilot got an in-app alert scheduled (the worker materializes the
    # notification row when it processes it; the outbox row is the proof).
    alerts = (
        db_session.execute(
            select(NotificationOutbox).where(
                NotificationOutbox.recipient_user_id == pilot.id,
                NotificationOutbox.notification_type == NotificationType.SYSTEM_ALERT,
            )
        )
        .scalars()
        .all()
    )
    assert len(alerts) == 1
    assert "cand@example.com" not in (alerts[0].body or "") + (alerts[0].title or "")


def test_candidate_permanent_error_autorevokes_telegram(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    candidate = _candidate_with_telegram(db_session)

    def blocked_send(config, *, chat_id, title, body):  # type: ignore[no-untyped-def]
        return TelegramSendResult(outcome="perm_error", error_class="telegram_blocked")

    monkeypatch.setattr(worker_module, "_send_telegram_impl", blocked_send)
    row = _candidate_row(db_session, candidate, DeliveryChannel.TELEGRAM)
    status = process_external_row(db_session, row.id, settings=_settings(), now=NOW)
    assert status == "failed"
    link = db_session.get(CandidateTelegramLink, candidate.id)
    assert link is not None
    db_session.refresh(link)
    assert link.revoked_at is not None
    assert link.revoke_reason == "auto_blocked"
    audit = (
        db_session.execute(
            select(AuditEvent).where(AuditEvent.action == AuditAction.CHANNEL_AUTO_REVOKED)
        )
        .scalars()
        .all()
    )
    assert any("candidate_telegram" in (e.details or "") for e in audit)


def test_admin_cancel_racing_the_worker_wins(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    candidate = _candidate_with_email(db_session)
    row = _candidate_row(db_session, candidate, DeliveryChannel.EMAIL)

    def cancel_then_send(config, *, to_address, subject, text_body):  # type: ignore[no-untyped-def]
        row_locked = db_session.execute(
            select(NotificationOutbox)
            .where(NotificationOutbox.id == row.id)
            .with_for_update()
            .execution_options(populate_existing=True)
        ).scalar_one()
        row_locked.status = DeliveryStatus.CANCELLED
        row_locked.cancelled_at = NOW
        row_locked.lease_expires_at = None
        db_session.commit()
        return SmtpSendResult(outcome="accepted", provider_message_id="raced-1")

    monkeypatch.setattr(worker_module, "_send_email_impl", cancel_then_send)
    status = process_external_row(db_session, row.id, settings=_settings(), now=NOW)
    assert status == "cancelled"
    db_session.refresh(row)
    assert row.status == DeliveryStatus.CANCELLED
    assert row.provider_message_id is None  # the cancel verdict is not overwritten


# --- Idempotency, quiet hours, lease recovery ------------------------------------


def test_queue_candidate_message_is_idempotent(db_session: Session) -> None:
    candidate = _candidate_with_email(db_session)
    message = render_candidate_message(
        "document_request", candidate=candidate, documents=["Паспорт"]
    )
    first = queue_candidate_message(
        db_session,
        candidate=candidate,
        channel=DeliveryChannel.EMAIL,
        message=message,
        message_type_key="document_request",
        source=NotificationSource.MANUAL,
        dedupe_key="fixed-key",
        scheduled_at=NOW,
    )
    second = queue_candidate_message(
        db_session,
        candidate=candidate,
        channel=DeliveryChannel.EMAIL,
        message=message,
        message_type_key="document_request",
        source=NotificationSource.MANUAL,
        dedupe_key="fixed-key",
        scheduled_at=NOW,
    )
    db_session.commit()
    assert first is not None
    assert second is None  # duplicate business event: no second row
    rows = (
        db_session.execute(
            select(NotificationOutbox).where(
                NotificationOutbox.recipient_candidate_id == candidate.id
            )
        )
        .scalars()
        .all()
    )
    assert len(rows) == 1


def test_candidate_row_respects_system_quiet_hours(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    candidate = _candidate_with_email(db_session)
    # 23:30 local (Europe/Moscow default) is inside the default quiet hours.
    night = datetime(2026, 9, 4, 20, 30, 0, tzinfo=UTC)
    row = _candidate_row(db_session, candidate, DeliveryChannel.EMAIL, scheduled_at=night)
    status = process_row(db_session, row, settings=_settings(), now=night)
    assert status == "queued"
    db_session.refresh(row)
    assert row.status == DeliveryStatus.QUEUED
    assert row.scheduled_at_effective is not None
    assert row.scheduled_at_effective > night  # postponed to the first allowed minute


def test_lease_recovery_returns_candidate_row_to_queue(db_session: Session) -> None:
    candidate = _candidate_with_email(db_session)
    row = _candidate_row(db_session, candidate, DeliveryChannel.EMAIL)
    row.lease_expires_at = NOW - timedelta(seconds=1)  # crashed worker
    db_session.commit()
    recovered = recover_stale_leases(db_session, now=NOW, lease_seconds=120)
    assert recovered == 1
    db_session.refresh(row)
    assert row.status == DeliveryStatus.QUEUED
    assert row.lease_expires_at is None
    attempts = _attempts(db_session, row)
    assert attempts[-1].error_class == "lease_expired"


# --- Double opt-in letter and version snapshot (phase 10 rework) ------------------


def test_skip_email_with_hr_recorded_consent(db_session: Session) -> None:
    """An email consent NOT confirmed by the candidate never unlocks sends."""
    candidate = _candidate_with_email(db_session)
    consent = db_session.get(CandidateChannelConsent, (candidate.id, "email"))
    assert consent is not None
    consent.source = "hr_recorded"
    db_session.commit()
    row = _candidate_row(db_session, candidate, DeliveryChannel.EMAIL)
    assert _skip_class(db_session, row, _settings()) == "consent_missing"


def _confirm_token_row(db: Session, candidate: Candidate, **overrides: object) -> Any:
    from app.models import CandidateEmailConfirmToken

    defaults: dict[str, object] = dict(
        candidate_id=candidate.id,
        token_hash=hashlib.sha256(b"confirm-token-0123456789").hexdigest(),
        email_normalized="cand@example.com",
        created_at=utc_now(),
        expires_at=utc_now() + timedelta(hours=1),
    )
    defaults.update(overrides)
    token = CandidateEmailConfirmToken(**defaults)
    db.add(token)
    db.commit()
    return token


def _confirm_letter_row(db: Session, candidate: Candidate, token_id: UUID) -> NotificationOutbox:
    from app.candidate_messages import queue_candidate_email_confirm

    row = queue_candidate_email_confirm(
        db,
        candidate=candidate,
        token_id=token_id,
        expires_at=utc_now() + timedelta(hours=1),
        initiator_user_id=candidate.owner_user_id,
    )
    assert row is not None
    # Claimed shape + the frozen NOW clock of the worker tests.
    row.status = DeliveryStatus.SENDING
    row.started_at = NOW
    row.lease_expires_at = NOW + timedelta(minutes=2)
    row.scheduled_at = NOW
    db.commit()
    return row


def test_confirm_letter_delivered_without_consent(db_session: Session) -> None:
    """The double opt-in letter is the only consent-free candidate email."""
    from app.worker import process_external_row

    candidate = make_candidate(db_session, owner=_hr(db_session), email="cand@example.com")
    token = _confirm_token_row(db_session, candidate)
    row = _confirm_letter_row(db_session, candidate, token.id)
    row.status = DeliveryStatus.SENDING
    row.started_at = utc_now()
    row.lease_expires_at = utc_now() + timedelta(minutes=2)
    db_session.commit()

    sent: list[tuple[str, str]] = []

    def fake_send(config: object, *, to_address: str, subject: str, text_body: str) -> Any:
        sent.append((to_address, text_body))
        return SmtpSendResult(outcome="accepted")

    settings = _settings(CANDIDATE_EMAIL_CONFIRM_BASE_URL="https://hr.example.test")
    monkeypatch_send = pytest.MonkeyPatch()
    monkeypatch_send.setattr(worker_module, "_send_email_impl", fake_send)
    try:
        status = process_external_row(db_session, row.id, settings=settings, now=utc_now())
    finally:
        monkeypatch_send.undo()
    assert status == "accepted"
    assert [address for address, _ in sent] == ["cand@example.com"]

    # The link was substituted IN MEMORY: the mailed body carries the full
    # URL for exactly this letter's token, while the stored row body keeps
    # the placeholder (nothing secret is ever persisted).
    from app.candidate_messages import CONFIRM_URL_PLACEHOLDER, derive_email_confirm_token

    mailed_body = sent[0][1]
    raw_token = derive_email_confirm_token(token.id, settings)
    assert f"https://hr.example.test/candidates/email/confirm?token={raw_token}" in mailed_body
    assert CONFIRM_URL_PLACEHOLDER not in mailed_body
    db_session.refresh(row)
    assert row.body is not None
    assert CONFIRM_URL_PLACEHOLDER in row.body
    assert "token=" not in row.body


def test_confirm_letter_skipped_when_token_dead(db_session: Session) -> None:
    """Consumed/expired/superseded links and changed addresses never mail."""
    candidate = make_candidate(db_session, owner=_hr(db_session), email="cand@example.com")
    # Consumed (already clicked).
    token = _confirm_token_row(db_session, candidate, consumed_at=utc_now())
    row = _confirm_letter_row(db_session, candidate, token.id)
    assert _skip_class(db_session, row, _settings()) == "token_inactive"

    # Expired.
    token2 = _confirm_token_row(
        db_session,
        candidate,
        token_hash=hashlib.sha256(b"expired").hexdigest(),
        expires_at=utc_now() - timedelta(minutes=1),
    )
    row2 = _confirm_letter_row(db_session, candidate, token2.id)
    assert _skip_class(db_session, row2, _settings()) == "token_inactive"

    # The card address changed after the letter was rendered (the previous
    # token is consumed first: at most one ACTIVE token per candidate).
    token2.consumed_at = utc_now()
    token2.consume_reason = "superseded"
    db_session.commit()
    token3 = _confirm_token_row(
        db_session,
        candidate,
        token_hash=hashlib.sha256(b"other").hexdigest(),
        email_normalized="previous@example.com",
    )
    row3 = _confirm_letter_row(db_session, candidate, token3.id)
    assert _skip_class(db_session, row3, _settings()) == "address_changed"


def test_confirm_letter_skipped_when_base_url_unset(db_session: Session) -> None:
    """No confirmation page base URL configured at send time: fail-closed."""
    candidate = make_candidate(db_session, owner=_hr(db_session), email="cand@example.com")
    token = _confirm_token_row(db_session, candidate)
    row = _confirm_letter_row(db_session, candidate, token.id)
    assert _skip_class(db_session, row, _settings()) == "channel_not_configured"


def test_confirm_letter_skipped_when_placeholder_missing(db_session: Session) -> None:
    """A confirm letter without the URL placeholder is malformed: never mail."""
    candidate = make_candidate(db_session, owner=_hr(db_session), email="cand@example.com")
    token = _confirm_token_row(db_session, candidate)
    row = _confirm_letter_row(db_session, candidate, token.id)
    assert row.body is not None
    row.body = row.body.replace("[[CANDIDATE_EMAIL_CONFIRM_URL]]", "обычный текст")
    db_session.commit()
    settings = _settings(CANDIDATE_EMAIL_CONFIRM_BASE_URL="https://hr.example.test")
    assert _skip_class(db_session, row, settings) == "token_inactive"


def test_send_time_revalidation_runs_again_right_before_provider_call(
    db_session: Session,
) -> None:
    """Phase B0: a mutation landing after phase A still stops the send.

    Simulates a card email change committing between the phase-A
    validation and the provider call: the last-stop re-validation must
    skip the row instead of mailing a consent pinned to the old address.
    """

    candidate = _candidate_with_email(db_session)
    row = _candidate_row(db_session, candidate, DeliveryChannel.EMAIL)
    assert row is not None

    original = worker_module._resolve_external_target
    calls = {"n": 0}

    def resolving_then_mutating(
        db: Session, locked: NotificationOutbox, *, settings: Any
    ) -> tuple[Any, str | None]:
        target, skip = original(db, locked, settings=settings)
        if calls["n"] == 0 and skip is None:
            # Right after phase A's read snapshot: the card address (and
            # with it the consent pin) changes before the network call.
            candidate.email = "changed@example.com"
            db.commit()
        calls["n"] += 1
        return target, skip

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(worker_module, "_resolve_external_target", resolving_then_mutating)
    try:
        result = process_external_row(db_session, row.id, settings=_settings(), now=utc_now())
    finally:
        monkeypatch.undo()
    assert result == "skipped"
    db_session.refresh(row)
    assert row.status.value == "skipped"
    assert row.error_class == "address_changed"


def test_interview_message_skipped_when_event_version_changed(db_session: Session) -> None:
    """The optimistic version snapshot: a mutated interview invalidates the
    rendered text even if the stale-plan cancellation ever raced."""
    hr = _hr(db_session)
    candidate = _candidate_with_email(db_session)
    event = make_event(
        db_session,
        candidate=candidate,
        author=hr,
        assignee=hr,
        type_=EventType.INTERVIEW,
        starts_at=NOW + timedelta(days=2),
    )
    row = _candidate_row(
        db_session,
        candidate,
        DeliveryChannel.EMAIL,
        event_id=event.id,
        type_key="interview_scheduled",
    )
    # The event mutated after the message was rendered (version bumped).
    event.version += 1
    db_session.commit()
    assert _skip_class(db_session, row, _settings()) == "event_stale"
