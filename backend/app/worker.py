"""The notification worker: PostgreSQL-backed delivery with safe concurrency.

One loop, four steps:

1. heartbeat — upsert the singleton ``worker_heartbeat`` row (used by the
   Compose healthcheck and the /ops/status signal);
2. lease recovery — jobs stuck in ``sending`` past their lease return to
   ``queued`` (a crashed worker never loses a job; the interrupted attempt
   is recorded as failed/lease_expired in the append-only history);
3. claim and process a batch with ``SELECT ... FOR UPDATE SKIP LOCKED`` —
   any number of worker instances may run in parallel without double
   delivery; every row is processed in its own short transaction (no
   long-lived transaction, no network calls inside one);
4. reminder scan — due personal reminders become notifications and
   recurring ones advance to their next occurrence without duplicates.

Quiet hours are applied at processing time (original vs effective times are
kept separately); ``email``/``telegram`` jobs are honestly marked
``skipped`` (channels are reserved for phase 9 — never a fake delivered).

The worker never logs PII: message texts are not logged, only ids, statuses
and safe error classes.
"""

import logging
import os
import signal
import socket
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from uuid import UUID

from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from app.audit import record_event
from app.config import Settings
from app.db import build_engine
from app.models import (
    AccessGrant,
    AccessGrantScope,
    AuditAction,
    Candidate,
    CandidateChannelConsent,
    CandidateTelegramLink,
    DeliveryChannel,
    DeliveryStatus,
    Event,
    EventStatus,
    NotificationDeliveryAttempt,
    NotificationOutbox,
    NotificationPreference,
    NotificationSource,
    NotificationType,
    Reminder,
    ReminderStatus,
    TelegramLink,
    User,
    UserEmail,
    WorkerHeartbeat,
)
from app.notification_service import (
    CONSENT_EXEMPT_TEMPLATES,
    EMAIL_VERIFICATION_TEMPLATE,
    deliver_in_app,
    has_channel_consent,
    preference_for,
    schedule_fan_out,
    schedule_notification_row,
)
from app.quiet_hours import effective_send_time, next_occurrence
from app.smtp import SmtpConfig, SmtpSendResult
from app.telegram import REVOKING_ERROR_CLASSES, TelegramConfig, TelegramSendResult
from app.utils import ensure_aware, utc_now

logger = logging.getLogger(__name__)

LEASE_EXPIRED_ERROR_CLASS = "lease_expired"
CHANNEL_NOT_CONFIGURED_ERROR_CLASS = "channel_not_configured"
BINDING_MISSING_ERROR_CLASS = "binding_missing"
BINDING_REVOKED_ERROR_CLASS = "binding_revoked"
CONSENT_MISSING_ERROR_CLASS = "consent_missing"
ADDRESS_UNVERIFIED_ERROR_CLASS = "address_unverified"
RECIPIENT_UNAVAILABLE_ERROR_CLASS = "recipient_unavailable"
RECIPIENT_MISSING_ERROR_CLASS = "recipient_missing"
# Phase 10 (candidate rows).
CANDIDATE_UNAVAILABLE_ERROR_CLASS = "candidate_unavailable"
EVENT_STALE_ERROR_CLASS = "event_stale"
ADDRESS_CHANGED_ERROR_CLASS = "address_changed"

# Candidate message types whose linked event must still be fresh at send
# time (see _candidate_event_is_stale).
_CANDIDATE_EVENT_FRESH_TYPES = frozenset(
    {
        NotificationType.CANDIDATE_INTERVIEW_SCHEDULED,
        NotificationType.CANDIDATE_INTERVIEW_REMINDER,
        NotificationType.CANDIDATE_INTERVIEW_RESCHEDULED,
    }
)

# Test hooks: module-level sender callables. Unit tests monkeypatch these;
# integration tests point Settings at stub servers and use the real ones.
_send_telegram_impl = None
_send_email_impl = None


def _send_telegram(
    config: TelegramConfig,
    *,
    chat_id: int,
    title: str,
    body: str | None,
    request_id: str | None = None,
) -> TelegramSendResult:
    if _send_telegram_impl is not None:
        return _send_telegram_impl(config, chat_id=chat_id, title=title, body=body)
    from app.telegram import send_message

    return send_message(config, chat_id=chat_id, title=title, body=body, request_id=request_id)


def _send_email(
    config: SmtpConfig,
    *,
    to_address: str,
    subject: str,
    text_body: str,
    request_id: str | None = None,
) -> SmtpSendResult:
    if _send_email_impl is not None:
        return _send_email_impl(config, to_address=to_address, subject=subject, text_body=text_body)
    from app.smtp import send_email

    return send_email(
        config, to_address=to_address, subject=subject, text_body=text_body, request_id=request_id
    )


@dataclass
class _ExternalTarget:
    """Resolved send parameters for one external outbox row."""

    channel: DeliveryChannel
    chat_id: int | None = None
    email: str | None = None
    title: str = ""
    body: str | None = None


def _worker_id() -> str:
    return f"{socket.gethostname()}-{os.getpid()}-{uuid.uuid4().hex[:8]}"


def heartbeat(db: Session, *, worker_id: str, now: datetime) -> None:
    """Upsert the singleton worker heartbeat row."""
    existing = db.get(WorkerHeartbeat, 1)
    if existing is None:
        db.add(
            WorkerHeartbeat(
                id=1,
                worker_id=worker_id,
                pid=os.getpid(),
                started_at=now,
                last_seen_at=now,
            )
        )
    else:
        existing.worker_id = worker_id
        existing.pid = os.getpid()
        existing.last_seen_at = now
        existing.updated_at = now
    db.commit()


def recover_stale_leases(db: Session, *, now: datetime, lease_seconds: int) -> int:
    """Return stuck ``sending`` rows to ``queued`` and record the crashed
    attempt in the append-only history. Returns the recovered count."""
    stale = (
        db.execute(
            select(NotificationOutbox)
            .where(
                NotificationOutbox.status == DeliveryStatus.SENDING,
                NotificationOutbox.lease_expires_at < now,
            )
            .order_by(NotificationOutbox.lease_expires_at)
            .limit(1000)
        )
        .scalars()
        .all()
    )
    for row in stale:
        row.attempts += 1
        db.add(
            NotificationDeliveryAttempt(
                outbox_id=row.id,
                attempt_no=row.attempts,
                started_at=row.started_at or now,
                finished_at=now,
                outcome="failed",
                error_class=LEASE_EXPIRED_ERROR_CLASS,
            )
        )
        row.status = DeliveryStatus.QUEUED
        row.started_at = None
        row.lease_expires_at = None
        row.next_attempt_at = now
    if stale:
        db.commit()
    return len(stale)


def claim_batch(
    db: Session, *, now: datetime, batch_size: int, lease_seconds: int
) -> list[NotificationOutbox]:
    """Atomically claim up to ``batch_size`` due jobs.

    PostgreSQL only: SKIP LOCKED makes concurrent workers safe. Rows are
    claimed with a fresh lease and committed before processing starts.
    """
    rows = (
        db.execute(
            select(NotificationOutbox)
            .where(
                NotificationOutbox.status == DeliveryStatus.QUEUED,
                (NotificationOutbox.scheduled_at.is_(None))
                | (NotificationOutbox.scheduled_at <= now),
                (NotificationOutbox.next_attempt_at.is_(None))
                | (NotificationOutbox.next_attempt_at <= now),
            )
            .order_by(
                NotificationOutbox.scheduled_at.asc().nulls_first(),
                NotificationOutbox.created_at.asc(),
            )
            .limit(batch_size)
            .with_for_update(skip_locked=True)
            .execution_options(populate_existing=True)
        )
        .scalars()
        .all()
    )
    claimed = list(rows)
    for row in claimed:
        row.status = DeliveryStatus.SENDING
        row.started_at = now
        row.lease_expires_at = now + timedelta(seconds=lease_seconds)
    if claimed:
        db.commit()
        # Reload so callers can read status/id even after this session is
        # closed (commit expires attributes by default).
        for row in claimed:
            db.refresh(row)
    return claimed


def process_row(db: Session, row: NotificationOutbox, *, settings: Settings, now: datetime) -> str:
    """Process one claimed outbox row in its own transaction.

    Re-selects the row under a row lock (an admin cancel may have raced the
    claim) and then finalizes on the *locked* instance — the instance passed
    in may be detached (the claim ran in another session), so all writes
    must go through the freshly loaded object.

    Returns the final status value. Never raises for expected conditions;
    unexpected exceptions are converted to a bounded retry or terminal
    failure inside this function.
    """
    locked = db.execute(
        select(NotificationOutbox)
        .where(NotificationOutbox.id == row.id)
        .with_for_update()
        .execution_options(populate_existing=True)
    ).scalar_one()
    if locked.status != DeliveryStatus.SENDING:
        db.rollback()
        return locked.status.value
    try:
        if (
            not locked.quiet_hours_bypassed
            and locked.scheduled_at is not None
            and (locked.recipient_user_id is not None or locked.recipient_candidate_id is not None)
        ):
            if locked.recipient_user_id is not None:
                preference = preference_for(
                    db, locked.recipient_user_id, settings.notification_default_timezone
                )
            else:
                # Phase 10: candidates have no personal preferences; the
                # system-wide quiet hours/timezone apply (external messages
                # are not sent at night by default, PRODUCT_SPEC §8).
                preference = _candidate_quiet_preference(settings)
            effective = effective_send_time(locked.scheduled_at, preference=preference, now_utc=now)
            if effective > now:
                # Inside quiet hours: postpone to the first allowed minute.
                # The original time stays in scheduled_at; the effective one
                # is stored separately for audit.
                locked.scheduled_at_effective = effective
                locked.next_attempt_at = effective
                locked.status = DeliveryStatus.QUEUED
                locked.started_at = None
                locked.lease_expires_at = None
                db.commit()
                return "queued"
        if locked.channel == DeliveryChannel.IN_APP:
            deliver_in_app(db, locked, now=now)
            locked.status = DeliveryStatus.DELIVERED
            locked.delivered_at = now
            outcome = "delivered"
        else:
            # External channels (phase 9): the network call happens OUTSIDE
            # any transaction (see process_external_row below). Returning its
            # final status here keeps the quiet-hours/cancel handling above
            # shared for all channels.
            db.commit()  # release the row lock before any network I/O
            return process_external_row(db, locked.id, settings=settings, now=now)
        locked.attempts += 1
        locked.next_attempt_at = None
        locked.lease_expires_at = None
        db.add(
            NotificationDeliveryAttempt(
                outbox_id=locked.id,
                attempt_no=locked.attempts,
                started_at=locked.started_at or now,
                finished_at=now,
                outcome=outcome,
                error_class=locked.error_class,
                provider_message_id=locked.provider_message_id,
            )
        )
        db.commit()
        return locked.status.value
    except Exception as exc:
        # An infrastructure crash (DB/ORM bug), NOT a provider verdict: it
        # must never inherit the stale error_class of a previous attempt.
        # Only the exception type is logged — messages may carry row data.
        db.rollback()
        logger.warning(
            "outbox row %s failed (attempt %s) type=%s",
            locked.id,
            locked.attempts + 1,
            type(exc).__name__,
        )
        return _record_failure(db, locked, settings=settings, now=now, error_class="internal_error")


def _candidate_quiet_preference(settings: Settings) -> NotificationPreference:
    """Synthetic quiet-hours schedule for candidate messages (phase 10).

    Candidates have no preference row of their own; the system defaults
    apply (timezone + quiet hours + workdays from the settings).
    """
    return NotificationPreference(
        user_id=uuid.UUID(int=0),  # synthetic, never flushed
        timezone=settings.notification_default_timezone,
        quiet_hours_start=settings.notification_quiet_hours_start,
        quiet_hours_end=settings.notification_quiet_hours_end,
        workdays=[int(day) for day in settings.notification_workdays.split(",")],
        enabled_types=[member.value for member in NotificationType],
        enabled_channels=[DeliveryChannel.IN_APP.value],
    )


def _candidate_event_is_stale(db: Session, locked: NotificationOutbox, *, now: datetime) -> bool:
    """True when the interview this message is about is no longer fresh.

    The reminder/scheduled/rescheduled letters must not leave after the
    interview was moved, cancelled, completed or already began: the event
    is re-read and re-checked right before the provider call (the stale
    plan is normally cancelled by the mutation itself — this is the
    send-time backstop for races and legacy rows).
    """
    if locked.notification_type not in _CANDIDATE_EVENT_FRESH_TYPES:
        return False
    if locked.object_type != "event" or locked.object_id is None:
        return False
    event = db.get(Event, locked.object_id)
    if event is None:
        return True
    if event.status != EventStatus.SCHEDULED:
        return True
    return ensure_aware(event.starts_at) <= now


def _resolve_candidate_target(
    db: Session, locked: NotificationOutbox, *, settings: Settings
) -> tuple[_ExternalTarget | None, str | None]:
    """Resolve a phase-10 candidate row (fail-closed at send time).

    The recipient is derived exclusively from server-side state: the
    candidate's own consent record, their card address (for email — the
    consent is pinned to its normalized value) or their Telegram binding.
    A stale event, revoked consent, changed address, deleted candidate or
    missing binding skips the row without any network call.
    """
    from app.smtp import config_from_settings as smtp_config_from_settings
    from app.telegram import config_from_settings as telegram_config_from_settings

    now = utc_now()
    candidate_id = locked.recipient_candidate_id
    assert candidate_id is not None
    candidate = db.get(Candidate, candidate_id)
    if candidate is None or candidate.deleted_at is not None:
        return None, CANDIDATE_UNAVAILABLE_ERROR_CLASS
    if _candidate_event_is_stale(db, locked, now=now):
        return None, EVENT_STALE_ERROR_CLASS

    if locked.channel == DeliveryChannel.TELEGRAM:
        if not telegram_config_from_settings(settings).is_configured:
            return None, CHANNEL_NOT_CONFIGURED_ERROR_CLASS
        link = db.get(CandidateTelegramLink, candidate_id)
        if link is None or link.chat_id is None:
            return None, BINDING_MISSING_ERROR_CLASS
        if link.revoked_at is not None:
            return None, BINDING_REVOKED_ERROR_CLASS
        consent = db.get(CandidateChannelConsent, (candidate_id, DeliveryChannel.TELEGRAM.value))
        if consent is None or not consent.granted:
            return None, CONSENT_MISSING_ERROR_CLASS
        return (
            _ExternalTarget(
                channel=DeliveryChannel.TELEGRAM,
                chat_id=link.chat_id,
                title=locked.title,
                body=locked.body,
            ),
            None,
        )

    if locked.channel == DeliveryChannel.EMAIL:
        if not smtp_config_from_settings(settings).is_configured:
            return None, CHANNEL_NOT_CONFIGURED_ERROR_CLASS
        if not candidate.email:
            return None, BINDING_MISSING_ERROR_CLASS
        consent = db.get(CandidateChannelConsent, (candidate_id, DeliveryChannel.EMAIL.value))
        if consent is None or not consent.granted:
            return None, CONSENT_MISSING_ERROR_CLASS
        from app.utils import normalize_email

        if consent.email_normalized != normalize_email(candidate.email):
            # The address changed after the consent was recorded: the
            # consent does not cover the new address — fail closed.
            return None, ADDRESS_CHANGED_ERROR_CLASS
        return (
            _ExternalTarget(
                channel=DeliveryChannel.EMAIL,
                email=candidate.email,
                title=locked.title,
                body=locked.body,
            ),
            None,
        )

    return None, CHANNEL_NOT_CONFIGURED_ERROR_CLASS


def _resolve_external_target(
    db: Session, locked: NotificationOutbox, *, settings: Settings
) -> tuple[_ExternalTarget | None, str | None]:
    """Resolve where an external row must go, or why it must be skipped.

    Returns (target, None) when the message may be sent, else (None,
    skip_error_class). Consent, bindings and the global configuration are
    re-validated here at send time (scheduling-time checks are advisory).
    Phase-10 candidate rows are resolved from the candidate's own state
    (see :func:`_resolve_candidate_target`).
    """
    from app.smtp import config_from_settings as smtp_config_from_settings
    from app.telegram import config_from_settings as telegram_config_from_settings

    if locked.recipient_candidate_id is not None:
        return _resolve_candidate_target(db, locked, settings=settings)

    channel = locked.channel
    template = locked.template
    consent_exempt = template in CONSENT_EXEMPT_TEMPLATES

    if channel == DeliveryChannel.TELEGRAM:
        if not telegram_config_from_settings(settings).is_configured:
            return None, CHANNEL_NOT_CONFIGURED_ERROR_CLASS
        user_id = locked.recipient_user_id
        if user_id is None:
            return None, RECIPIENT_MISSING_ERROR_CLASS
        user = db.get(User, user_id)
        if user is None or not user.is_active:
            return None, RECIPIENT_UNAVAILABLE_ERROR_CLASS
        link = db.get(TelegramLink, user_id)
        if link is None or link.chat_id is None:
            return None, BINDING_MISSING_ERROR_CLASS
        if link.revoked_at is not None:
            return None, BINDING_REVOKED_ERROR_CLASS
        if not consent_exempt:
            preference = db.get(NotificationPreference, user_id)
            if not has_channel_consent(preference, DeliveryChannel.TELEGRAM):
                return None, CONSENT_MISSING_ERROR_CLASS
        return (
            _ExternalTarget(
                channel=channel, chat_id=link.chat_id, title=locked.title, body=locked.body
            ),
            None,
        )

    if channel == DeliveryChannel.EMAIL:
        if not smtp_config_from_settings(settings).is_configured:
            return None, CHANNEL_NOT_CONFIGURED_ERROR_CLASS
        if template == EMAIL_VERIFICATION_TEMPLATE:
            # The confirmation mail for a not-yet-verified address: the
            # recipient is allow-listed by construction (validated at queue
            # time), quiet hours and consent do not apply. Re-validated
            # here as well: rows predating the queue-time guard (or forged
            # rows) skip without any network call.
            from app.smtp import validate_mailbox

            to_address = locked.external_recipient
            if not to_address:
                return None, RECIPIENT_MISSING_ERROR_CLASS
            try:
                to_address = validate_mailbox(to_address, field="external_recipient")
            except ValueError:
                return None, RECIPIENT_UNAVAILABLE_ERROR_CLASS
            return (
                _ExternalTarget(
                    channel=channel, email=to_address, title=locked.title, body=locked.body
                ),
                None,
            )
        user_id = locked.recipient_user_id
        if user_id is None:
            return None, RECIPIENT_MISSING_ERROR_CLASS
        user = db.get(User, user_id)
        if user is None or not user.is_active:
            return None, RECIPIENT_UNAVAILABLE_ERROR_CLASS
        address = db.get(UserEmail, user_id)
        if address is None or not address.is_verified or not address.email:
            return None, ADDRESS_UNVERIFIED_ERROR_CLASS
        if not consent_exempt:
            preference = db.get(NotificationPreference, user_id)
            if not has_channel_consent(preference, DeliveryChannel.EMAIL):
                return None, CONSENT_MISSING_ERROR_CLASS
        return (
            _ExternalTarget(
                channel=channel, email=address.email, title=locked.title, body=locked.body
            ),
            None,
        )

    return None, CHANNEL_NOT_CONFIGURED_ERROR_CLASS


def _note_external_success(
    db: Session,
    user_id: UUID | None,
    channel: DeliveryChannel,
    *,
    candidate_id: UUID | None = None,
) -> None:
    """Record a successful provider handoff on the binding (PII-free)."""
    now = utc_now()
    if candidate_id is not None:
        # Phase 10: the candidate's own binding.
        if channel == DeliveryChannel.TELEGRAM:
            candidate_link = db.get(CandidateTelegramLink, candidate_id)
            if candidate_link is not None:
                candidate_link.last_sent_at = now
                candidate_link.last_error_class = None
                candidate_link.last_error_at = None
        return
    if user_id is None:
        return
    if channel == DeliveryChannel.TELEGRAM:
        link = db.get(TelegramLink, user_id)
        if link is not None:
            link.last_sent_at = now
            link.last_error_class = None
            link.last_error_at = None
    elif channel == DeliveryChannel.EMAIL:
        address = db.get(UserEmail, user_id)
        if address is not None:
            address.last_sent_at = now
            address.last_error_class = None
            address.last_error_at = None


def _note_external_error(
    db: Session,
    user_id: UUID | None,
    channel: DeliveryChannel,
    error_class: str,
    *,
    candidate_id: UUID | None = None,
) -> None:
    """Record a delivery failure class on the binding (PII-free)."""
    now = utc_now()
    if candidate_id is not None:
        # Phase 10: the candidate's own binding (email has no separate
        # candidate binding — the state derives from the consent/card).
        if channel == DeliveryChannel.TELEGRAM:
            candidate_link = db.get(CandidateTelegramLink, candidate_id)
            if candidate_link is not None:
                candidate_link.last_error_class = error_class
                candidate_link.last_error_at = now
        return
    if user_id is None:
        return
    if channel == DeliveryChannel.TELEGRAM:
        link = db.get(TelegramLink, user_id)
        if link is not None:
            link.last_error_class = error_class
            link.last_error_at = now
    elif channel == DeliveryChannel.EMAIL:
        address = db.get(UserEmail, user_id)
        if address is not None:
            address.last_error_class = error_class
            address.last_error_at = now


def _advisory_send_lock_held(db: Session, outbox_id: object) -> bool:
    """Best-effort cross-process single-flight for one external send.

    PostgreSQL only: ``pg_try_advisory_lock`` on a per-row key, held on the
    session's connection from phase A through phase C (released in the
    ``finally`` of :func:`process_external_row`; a crashed worker's death
    releases it server-side via the dropped connection). Without it two
    workers could pass the phase-A check together and send twice — the
    check and the finalize are split by the network call. Returns False
    when another worker already sends this row (the caller backs off and
    the lease keeps the row safe). Other dialects (SQLite unit tests) run
    single-threaded and skip the lock.
    """
    bind = db.get_bind()
    if bind is None or bind.dialect.name != "postgresql":
        return True
    # Pin one pooled connection to this session: advisory locks are
    # per-connection, so phases A and C must share it across the network
    # call (released back to the pool when the caller closes the session).
    db.connection()
    key = f"outbox-send:{outbox_id}"
    held = db.execute(
        text("SELECT pg_try_advisory_lock(hashtext(:key))"), {"key": key}
    ).scalar_one()
    return bool(held)


def _advisory_send_lock_release(db: Session, outbox_id: object) -> None:
    bind = db.get_bind()
    if bind is None or bind.dialect.name != "postgresql":
        return
    key = f"outbox-send:{outbox_id}"
    try:
        db.execute(text("SELECT pg_advisory_unlock(hashtext(:key))"), {"key": key})
        db.commit()
    except Exception:
        db.rollback()


def process_external_row(
    db: Session, outbox_id: object, *, settings: Settings, now: datetime
) -> str:
    """Deliver one claimed email/telegram row with real provider I/O.

    Three phases: (A) short transaction — re-lock, re-validate, extend the
    lease, then commit to release the lock; (B) provider call with NO open
    transaction; (C) short transaction — re-lock and finalize atomically
    (an admin cancel racing the send wins: its status is left untouched).

    A PostgreSQL advisory lock spans all three phases so parallel workers
    can never send the same row twice (a worker that finds the lock busy
    backs off with ``sending`` — the lease keeps the row safe).

    Provider ``accepted`` is stored as ``accepted`` with the provider
    message id only when the provider returned one — never ``delivered``,
    never «read». Returns the final status value.
    """
    if not _advisory_send_lock_held(db, outbox_id):
        db.rollback()
        return DeliveryStatus.SENDING.value
    try:
        return _process_external_row_locked(db, outbox_id, settings=settings, now=now)
    finally:
        _advisory_send_lock_release(db, outbox_id)


def _process_external_row_locked(
    db: Session, outbox_id: object, *, settings: Settings, now: datetime
) -> str:
    """Phases A/B/C of external delivery (the advisory lock is held)."""
    from app.smtp import config_from_settings as smtp_config_from_settings
    from app.telegram import config_from_settings as telegram_config_from_settings

    # Phase A: resolve under a row lock, then release before the network.
    locked = db.execute(
        select(NotificationOutbox)
        .where(NotificationOutbox.id == outbox_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    ).scalar_one()
    if locked.status != DeliveryStatus.SENDING:
        db.rollback()
        return locked.status.value
    target, skip_class = _resolve_external_target(db, locked, settings=settings)
    if target is None or skip_class is not None:
        assert skip_class is not None
        locked.status = DeliveryStatus.SKIPPED
        locked.failed_at = now
        locked.error_class = skip_class
        locked.attempts += 1
        locked.next_attempt_at = None
        locked.lease_expires_at = None
        db.add(
            NotificationDeliveryAttempt(
                outbox_id=locked.id,
                attempt_no=locked.attempts,
                started_at=locked.started_at or now,
                finished_at=now,
                outcome="skipped",
                error_class=skip_class,
            )
        )
        db.commit()
        return locked.status.value
    channel = target.channel
    recipient_user_id = locked.recipient_user_id
    recipient_candidate_id = locked.recipient_candidate_id
    channel_value = channel.value
    # Fresh lease covering the bounded provider call (recovery re-queues on
    # crash exactly like for in-app rows).
    locked.lease_expires_at = now + timedelta(seconds=settings.worker_lease_seconds)
    db.commit()

    # Phase B: provider I/O with no open transaction.
    outcome: str
    provider_message_id: str | None = None
    error_code: str | None = None
    error_class: str | None = None
    retry_after_s: int | None = None
    try:
        if channel_value == DeliveryChannel.TELEGRAM.value:
            assert target.chat_id is not None
            result: TelegramSendResult | SmtpSendResult = _send_telegram(
                telegram_config_from_settings(settings),
                chat_id=target.chat_id,
                title=target.title,
                body=target.body,
                request_id=str(outbox_id),
            )
            if isinstance(result, TelegramSendResult):
                retry_after_s = result.retry_after_s
        else:
            assert target.email is not None
            result = _send_email(
                smtp_config_from_settings(settings),
                to_address=target.email,
                subject=target.title,
                text_body=target.body or "",
                request_id=str(outbox_id),
            )
        outcome = result.outcome
        provider_message_id = result.provider_message_id
        error_code = result.error_code
        error_class = result.error_class
    except Exception as exc:
        # The adapters never raise for expected conditions; this backstop
        # converts anything unexpected into a bounded retry (never a fake
        # accepted, never a silent drop). Type only: messages/tracebacks
        # may echo recipient data.
        logger.warning(
            "external delivery transport raised unexpectedly type=%s request_id=%s",
            type(exc).__name__,
            outbox_id,
        )
        outcome, error_code, error_class = "temp_error", "transport", "transport_error"

    # Phase C: atomic finalize under a fresh row lock.
    final = db.execute(
        select(NotificationOutbox)
        .where(NotificationOutbox.id == outbox_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    ).scalar_one()
    if final.status != DeliveryStatus.SENDING:
        # An admin cancel/retry raced the provider call and won; its
        # decision stands (the message may already have left — an accepted,
        # documented micro-race, same class as any check-then-act send).
        db.rollback()
        return final.status.value

    # The lease CHECK requires status/lease consistency at every flush, so
    # the terminal state is assigned BEFORE any helper query runs (helper
    # queries trigger autoflush).
    final.attempts += 1
    if outcome == "accepted":
        final.status = DeliveryStatus.ACCEPTED
        final.accepted_at = now
        if provider_message_id:
            final.provider_message_id = provider_message_id
        final.error_code = None
        final.error_class = None
        final.next_attempt_at = None
        final.lease_expires_at = None
        db.add(
            NotificationDeliveryAttempt(
                outbox_id=final.id,
                attempt_no=final.attempts,
                started_at=now,
                finished_at=now,
                outcome="accepted",
                provider_message_id=provider_message_id,
            )
        )
        _note_external_success(db, recipient_user_id, channel, candidate_id=recipient_candidate_id)
        db.commit()
        return final.status.value

    # Failure: decide the terminal state first, then record the classified
    # attempt and side effects (binding stats, auto-revoke, pilot alert).
    final.error_code = error_code
    final.error_class = error_class or "unknown"
    terminally_failed = outcome == "perm_error" or final.attempts >= settings.worker_max_attempts
    if terminally_failed:
        final.status = DeliveryStatus.FAILED
        final.failed_at = now
        final.next_attempt_at = None
    else:
        if retry_after_s is not None:
            delay = timedelta(seconds=retry_after_s)
        else:
            delay = timedelta(seconds=_backoff_delay_s(settings, final.attempts))
        final.status = DeliveryStatus.QUEUED
        final.started_at = None
        final.next_attempt_at = now + delay
    final.lease_expires_at = None
    db.add(
        NotificationDeliveryAttempt(
            outbox_id=final.id,
            attempt_no=final.attempts,
            started_at=now,
            finished_at=now,
            outcome="failed",
            error_code=error_code,
            error_class=error_class or "unknown",
        )
    )
    _note_external_error(
        db, recipient_user_id, channel, final.error_class, candidate_id=recipient_candidate_id
    )
    if (
        outcome == "perm_error"
        and channel == DeliveryChannel.TELEGRAM
        and (error_class or "") in REVOKING_ERROR_CLASSES
    ):
        if recipient_candidate_id is not None:
            _auto_revoke_candidate_telegram(
                db, candidate_id=recipient_candidate_id, error_class=final.error_class
            )
        else:
            _auto_revoke_telegram(db, user_id=recipient_user_id, error_class=final.error_class)
    if terminally_failed:
        _alert_pilot_on_terminal_failure(db, row=final, now=now)
    db.commit()
    return final.status.value


def _auto_revoke_telegram(db: Session, *, user_id: UUID | None, error_class: str) -> None:
    """Revoke a Telegram binding the provider proved dead (blocked/chat
    gone/deactivated), with an audit record. Re-linking stays available."""
    if user_id is None:
        return
    link = db.get(TelegramLink, user_id)
    if link is None or link.revoked_at is not None:
        return
    link.revoked_at = utc_now()
    link.revoke_reason = "auto_blocked"
    record_event(
        db,
        AuditAction.CHANNEL_AUTO_REVOKED,
        subject=user_id,
        details=f"channel=telegram reason=auto_blocked class={error_class}",
        commit=False,
    )


def _auto_revoke_candidate_telegram(db: Session, *, candidate_id: UUID, error_class: str) -> None:
    """Revoke a candidate's Telegram binding the provider proved dead
    (blocked/chat gone/deactivated), with an audit record. Re-inviting
    stays available. The recorded consent is left as-is: the binding is
    the missing piece, and an HR-recorded refusal must not be erased."""
    link = db.get(CandidateTelegramLink, candidate_id)
    if link is None or link.revoked_at is not None:
        return
    link.revoked_at = utc_now()
    link.revoke_reason = "auto_blocked"
    record_event(
        db,
        AuditAction.CHANNEL_AUTO_REVOKED,
        candidate_id=candidate_id,
        details=f"channel=candidate_telegram reason=auto_blocked class={error_class}",
        commit=False,
    )


def _backoff_delay_s(settings: Settings, attempt_no: int) -> float:
    """Bounded exponential backoff shared by all channels."""
    return min(
        settings.worker_backoff_cap_s,
        settings.worker_backoff_base_s * (2 ** (attempt_no - 1)),
    )


def _record_failure(
    db: Session,
    row: NotificationOutbox,
    *,
    settings: Settings,
    now: datetime,
    error_class: str | None = None,
) -> str:
    """Bounded exponential backoff; terminal failure alerts the pilot.

    ``error_class`` overrides the stored class (clearing the stale code):
    infrastructure crashes pass ``internal_error`` so they are never
    misreported under a previous attempt's provider verdict.
    """
    row = db.execute(
        select(NotificationOutbox)
        .where(NotificationOutbox.id == row.id)
        .with_for_update()
        .execution_options(populate_existing=True)
    ).scalar_one()
    if error_class is not None:
        row.error_class = error_class
        row.error_code = None
    row.attempts += 1
    attempt_no = row.attempts
    row.lease_expires_at = None
    db.add(
        NotificationDeliveryAttempt(
            outbox_id=row.id,
            attempt_no=attempt_no,
            started_at=row.started_at or now,
            finished_at=now,
            outcome="failed",
            error_class=row.error_class or "unknown",
            error_code=row.error_code,
        )
    )
    if attempt_no < settings.worker_max_attempts:
        backoff = _backoff_delay_s(settings, attempt_no)
        row.status = DeliveryStatus.QUEUED
        row.started_at = None
        row.next_attempt_at = now + timedelta(seconds=backoff)
    else:
        row.status = DeliveryStatus.FAILED
        row.failed_at = now
        row.next_attempt_at = None
        _alert_pilot_on_terminal_failure(db, row=row, now=now)
    db.commit()
    return row.status.value


def _alert_pilot_on_terminal_failure(
    db: Session, *, row: NotificationOutbox, now: datetime
) -> None:
    """A terminal queue failure surfaces to the pilot user as a system
    alert (safe error class only — no PII, no provider payloads)."""
    pilot_ids = (
        db.execute(
            select(AccessGrant.user_id).where(
                AccessGrant.scope == AccessGrantScope.PILOT_FULL_ACCESS,
                AccessGrant.revoked_at.is_(None),
            )
        )
        .scalars()
        .all()
    )
    for user_id in pilot_ids:
        # In-app only on purpose: fanning terminal-failure alerts to the
        # failing external channel could loop (external fails -> alert ->
        # external fails ...). The in-app alert always lands.
        schedule_notification_row(
            db,
            type_=NotificationType.SYSTEM_ALERT,
            recipient_user_id=user_id,
            source=NotificationSource.SYSTEM,
            title="Ошибка очереди уведомлений",
            body=(
                "Задание очереди исчерпало попытки доставки и помечено как "
                f"неудачное (класс ошибки: {row.error_class or 'unknown'})."
            ),
            dedupe_key=f"system:{row.id}",
            scheduled_at=now,
        )


def scan_due_reminders(db: Session, *, settings: Settings, now: datetime, batch_size: int) -> int:
    """Deliver due personal reminders and advance recurring ones.

    Each reminder is claimed with SKIP LOCKED and processed in its own
    transaction. The delivery goes through the outbox with an idempotency
    key per occurrence, so parallel workers can never double-deliver.
    Returns the number of processed reminders.
    """
    rows = (
        db.execute(
            select(Reminder)
            .where(Reminder.status == ReminderStatus.ACTIVE, Reminder.due_at <= now)
            .order_by(Reminder.due_at.asc())
            .limit(batch_size)
            .with_for_update(skip_locked=True)
            .execution_options(populate_existing=True)
        )
        .scalars()
        .all()
    )
    processed = 0
    for reminder in rows:
        try:
            _process_reminder(db, reminder, settings=settings, now=now)
            db.commit()
            processed += 1
        except Exception:
            db.rollback()
            logger.warning(
                "reminder %s processing failed; will retry on the next pass", reminder.id
            )
    return processed


def _process_reminder(
    db: Session, reminder: Reminder, *, settings: Settings, now: datetime
) -> None:
    overdue = now >= reminder.due_at + timedelta(minutes=5)
    type_ = NotificationType.REMINDER_OVERDUE if overdue else NotificationType.REMINDER_DUE
    title = "Напоминание: " + reminder.title if not overdue else "Просрочено: " + reminder.title
    schedule_fan_out(
        db,
        type_=type_,
        recipient_user_id=reminder.assignee_user_id,
        source=NotificationSource.REMINDER,
        title=title[:300],
        body=reminder.note,
        object_type="reminder",
        object_id=reminder.id,
        dedupe_key=f"{reminder.id}:{reminder.occurrence}",
        scheduled_at=now,
        initiator_user_id=reminder.owner_user_id,
        settings=settings,
    )
    if reminder.recurrence.value == "none":
        reminder.status = ReminderStatus.COMPLETED
        reminder.completed_at = now
    else:
        preference = preference_for(
            db, reminder.owner_user_id, settings.notification_default_timezone
        )
        reminder.due_at = next_occurrence(
            reminder.due_at,
            timezone=reminder.timezone,
            recurrence=reminder.recurrence.value,
            workdays=preference.workdays,
        )
        reminder.occurrence += 1
    reminder.updated_at = now


def queue_counts(db: Session, *, now: datetime) -> dict:
    """PII-free queue diagnostics for /ops/status and the admin screen."""
    count_rows = db.execute(
        select(NotificationOutbox.status, func.count()).group_by(NotificationOutbox.status)
    ).all()
    counts: dict[str, int] = {str(status): int(total) for status, total in count_rows}
    oldest_queued = db.execute(
        select(func.min(NotificationOutbox.queued_at)).where(
            NotificationOutbox.status == DeliveryStatus.QUEUED
        )
    ).scalar_one()
    stuck = db.execute(
        select(func.count())
        .select_from(NotificationOutbox)
        .where(
            NotificationOutbox.status == DeliveryStatus.SENDING,
            NotificationOutbox.lease_expires_at < now,
        )
    ).scalar_one()
    heartbeat_row = db.get(WorkerHeartbeat, 1)
    return {
        "counts": counts,
        "oldest_queued_at": oldest_queued,
        "stuck_sending": int(stuck),
        "worker": (
            {
                "alive": (now - heartbeat_row.last_seen_at).total_seconds() < 45,
                "last_seen_at": heartbeat_row.last_seen_at,
                "processed_total": heartbeat_row.processed_total,
                "failed_total": heartbeat_row.failed_total,
            }
            if heartbeat_row is not None
            else {"alive": False}
        ),
    }


def run_worker(settings: Settings) -> None:
    """Run the worker loop until SIGTERM/SIGINT."""
    engine = build_engine(settings)
    worker_id = _worker_id()
    stop = False

    def _stop(_signum: int, _frame: object) -> None:
        nonlocal stop
        stop = True

    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)

    logger.info("notification worker %s started (pid=%s)", worker_id, os.getpid())
    try:
        while not stop:
            started = time.monotonic()
            now = utc_now()
            with Session(engine) as db:
                try:
                    heartbeat(db, worker_id=worker_id, now=now)
                    recover_stale_leases(db, now=now, lease_seconds=settings.worker_lease_seconds)
                    claimed = claim_batch(
                        db,
                        now=now,
                        batch_size=settings.worker_batch_size,
                        lease_seconds=settings.worker_lease_seconds,
                    )
                    scan_due_reminders(
                        db, settings=settings, now=now, batch_size=settings.worker_batch_size
                    )
                except Exception:
                    db.rollback()
                    logger.exception("worker pass failed; retrying after the poll interval")
                    claimed = []
                # Process each claimed row in its own transaction (the rows
                # were already committed as 'sending' by claim_batch).
                for row in claimed:
                    with Session(engine) as row_db:
                        outcome_status = process_row(row_db, row, settings=settings, now=utc_now())
                        _bump_heartbeat(
                            row_db, worker_id=worker_id, failed=(outcome_status == "failed")
                        )
            elapsed = time.monotonic() - started
            if not stop:
                time.sleep(max(0.1, settings.worker_poll_interval_s - elapsed))
    finally:
        engine.dispose()
        logger.info("notification worker %s stopped", worker_id)


def _bump_heartbeat(db: Session, *, worker_id: str, failed: bool) -> None:
    row = db.get(WorkerHeartbeat, 1)
    if row is None:
        return
    row.processed_total += 1
    if failed:
        row.failed_total += 1
    row.last_seen_at = utc_now()
    row.updated_at = utc_now()
    db.commit()


def worker_is_healthy(db: Session, *, settings: Settings, now: datetime | None = None) -> bool:
    """Readiness signal: a fresh heartbeat means a worker is actively
    polling (the heartbeat is written at the top of every pass)."""
    now = now or utc_now()
    row = db.get(WorkerHeartbeat, 1)
    if row is None:
        return False
    return (now - row.last_seen_at).total_seconds() <= settings.worker_stale_after_s
