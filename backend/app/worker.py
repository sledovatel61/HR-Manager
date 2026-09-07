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
from datetime import datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import Settings
from app.db import build_engine
from app.models import (
    AccessGrant,
    AccessGrantScope,
    DeliveryStatus,
    NotificationDeliveryAttempt,
    NotificationOutbox,
    NotificationSource,
    NotificationType,
    Reminder,
    ReminderStatus,
    WorkerHeartbeat,
)
from app.notification_service import deliver_in_app, preference_for, schedule_notification_row
from app.quiet_hours import effective_send_time, next_occurrence
from app.smtp_adapter import (
    SmtpAuthError,
    SmtpConnectionError,
    SmtpHeaderInjectionError,
    SmtpRecipientError,
    SmtpTemporaryError,
    SmtpTimeoutError,
)
from app.smtp_adapter import (
    send_email as smtp_send_email,
)
from app.telegram_adapter import (
    TelegramBlockedError,
    TelegramPermanentError,
    TelegramRateLimitError,
    TelegramTemporaryError,
    format_telegram_message,
)
from app.telegram_adapter import (
    send_message as telegram_send_message,
)
from app.utils import utc_now

logger = logging.getLogger(__name__)

LEASE_EXPIRED_ERROR_CLASS = "lease_expired"
CHANNEL_NOT_CONFIGURED_ERROR_CLASS = "channel_not_configured"


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

    # Quiet hours check
    if (
        not locked.quiet_hours_bypassed
        and locked.recipient_user_id is not None
        and locked.scheduled_at is not None
    ):
        preference = preference_for(
            db, locked.recipient_user_id, settings.notification_default_timezone
        )
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

    channel = locked.channel.value
    provider_msg_id: str | None = None
    outcome: str = "failed"

    try:
        if channel == "in_app":
            deliver_in_app(db, locked, now=now)
            locked.status = DeliveryStatus.DELIVERED
            locked.delivered_at = now
            outcome = "delivered"
        elif channel == "telegram":
            if not settings.telegram_bot_token.strip():
                locked.status = DeliveryStatus.SKIPPED
                locked.failed_at = now
                locked.error_class = CHANNEL_NOT_CONFIGURED_ERROR_CLASS
                outcome = "skipped"
            else:
                chat_id: int | None = None
                skipped_or_failed = False
                if locked.recipient_user_id is not None:
                    pref = preference_for(
                        db, locked.recipient_user_id, settings.notification_default_timezone
                    )
                    if pref.telegram_chat_id is None or not pref.telegram_opt_in:
                        locked.status = DeliveryStatus.SKIPPED
                        locked.failed_at = now
                        locked.error_class = (
                            "no_consent"
                            if pref.telegram_chat_id is not None
                            else "recipient_not_linked"
                        )
                        outcome = "skipped"
                        skipped_or_failed = True
                    else:
                        chat_id = pref.telegram_chat_id
                elif locked.external_recipient is not None:
                    try:
                        chat_id = int(locked.external_recipient)
                    except ValueError:
                        locked.status = DeliveryStatus.FAILED
                        locked.failed_at = now
                        locked.error_class = "invalid_recipient"
                        outcome = "failed"
                        skipped_or_failed = True

                if chat_id is not None and not skipped_or_failed:
                    msg_text = format_telegram_message(locked.title, locked.body)
                    tg_res = telegram_send_message(settings, chat_id=chat_id, text=msg_text)
                    locked.status = DeliveryStatus.ACCEPTED
                    locked.accepted_at = now
                    locked.provider_message_id = tg_res.provider_message_id
                    provider_msg_id = tg_res.provider_message_id
                    outcome = "accepted"

        elif channel == "email":
            if not settings.smtp_host.strip():
                locked.status = DeliveryStatus.SKIPPED
                locked.failed_at = now
                locked.error_class = CHANNEL_NOT_CONFIGURED_ERROR_CLASS
                outcome = "skipped"
            else:
                to_email: str | None = None
                skipped_or_failed = False
                if locked.recipient_user_id is not None:
                    pref = preference_for(
                        db, locked.recipient_user_id, settings.notification_default_timezone
                    )
                    if not pref.email_address or not pref.email_opt_in:
                        locked.status = DeliveryStatus.SKIPPED
                        locked.failed_at = now
                        locked.error_class = (
                            "no_consent" if pref.email_address else "recipient_not_configured"
                        )
                        outcome = "skipped"
                        skipped_or_failed = True
                    else:
                        to_email = pref.email_address
                elif locked.external_recipient is not None:
                    to_email = locked.external_recipient

                if to_email is not None and not skipped_or_failed:
                    smtp_res = smtp_send_email(
                        settings,
                        to_email=to_email,
                        subject=locked.title,
                        body=locked.body or locked.title,
                    )
                    locked.status = DeliveryStatus.ACCEPTED
                    locked.accepted_at = now
                    locked.provider_message_id = smtp_res.provider_message_id
                    provider_msg_id = smtp_res.provider_message_id
                    outcome = "accepted"
        else:
            locked.status = DeliveryStatus.SKIPPED
            locked.failed_at = now
            locked.error_class = CHANNEL_NOT_CONFIGURED_ERROR_CLASS
            outcome = "skipped"

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
                error_code=locked.error_code,
                provider_message_id=provider_msg_id,
            )
        )
        db.commit()
        return locked.status.value

    except TelegramRateLimitError as exc:
        db.rollback()
        logger.warning(
            "Telegram rate limit for row %s (retry_after=%s)", locked.id, exc.retry_after
        )
        return _reschedule_rate_limited(db, locked, retry_after=exc.retry_after, now=now)
    except (
        TelegramBlockedError,
        TelegramPermanentError,
        SmtpAuthError,
        SmtpRecipientError,
        SmtpHeaderInjectionError,
    ) as exc:
        db.rollback()
        error_cls = getattr(exc, "error_class", "permanent_error")
        error_cd = getattr(exc, "error_code", None)
        logger.warning("Permanent delivery failure for outbox row %s: %s", locked.id, error_cls)
        return _record_terminal_failure(
            db, locked, error_class=error_cls, error_code=error_cd, now=now
        )
    except (
        TelegramTemporaryError,
        SmtpTemporaryError,
        SmtpTimeoutError,
        SmtpConnectionError,
    ) as exc:
        db.rollback()
        error_cls = getattr(exc, "error_class", "temporary_error")
        error_cd = getattr(exc, "error_code", None)
        logger.warning("Temporary delivery error for outbox row %s: %s", locked.id, error_cls)
        return _record_failure(
            db, locked, settings=settings, error_class=error_cls, error_code=error_cd, now=now
        )
    except Exception as exc:
        db.rollback()
        logger.warning(
            "Unexpected exception delivering outbox row %s (attempt %s): %s",
            locked.id,
            locked.attempts + 1,
            exc,
        )
        return _record_failure(db, locked, settings=settings, error_class="unknown", now=now)


def _reschedule_rate_limited(
    db: Session, row: NotificationOutbox, *, retry_after: int, now: datetime
) -> str:
    """Handle rate limit with explicit retry_after postponement."""
    row = db.execute(
        select(NotificationOutbox)
        .where(NotificationOutbox.id == row.id)
        .with_for_update()
        .execution_options(populate_existing=True)
    ).scalar_one()
    row.attempts += 1
    row.status = DeliveryStatus.QUEUED
    row.started_at = None
    row.lease_expires_at = None
    row.next_attempt_at = now + timedelta(seconds=retry_after)
    row.error_class = "rate_limited"
    row.error_code = "429"
    db.add(
        NotificationDeliveryAttempt(
            outbox_id=row.id,
            attempt_no=row.attempts,
            started_at=row.started_at or now,
            finished_at=now,
            outcome="failed",
            error_class="rate_limited",
            error_code="429",
        )
    )
    db.commit()
    return "queued"


def _record_terminal_failure(
    db: Session,
    row: NotificationOutbox,
    *,
    error_class: str,
    error_code: str | None,
    now: datetime,
) -> str:
    """Record permanent failure immediately without further retries."""
    row = db.execute(
        select(NotificationOutbox)
        .where(NotificationOutbox.id == row.id)
        .with_for_update()
        .execution_options(populate_existing=True)
    ).scalar_one()
    row.attempts += 1
    row.status = DeliveryStatus.FAILED
    row.failed_at = now
    row.next_attempt_at = None
    row.lease_expires_at = None
    row.error_class = error_class
    row.error_code = error_code
    db.add(
        NotificationDeliveryAttempt(
            outbox_id=row.id,
            attempt_no=row.attempts,
            started_at=row.started_at or now,
            finished_at=now,
            outcome="failed",
            error_class=error_class,
            error_code=error_code,
        )
    )
    _alert_pilot_on_terminal_failure(db, row=row, now=now)
    db.commit()
    return row.status.value


def _record_failure(
    db: Session,
    row: NotificationOutbox,
    *,
    settings: Settings,
    error_class: str = "unknown",
    error_code: str | None = None,
    now: datetime,
) -> str:
    """Bounded exponential backoff; terminal failure alerts the pilot."""
    row = db.execute(
        select(NotificationOutbox)
        .where(NotificationOutbox.id == row.id)
        .with_for_update()
        .execution_options(populate_existing=True)
    ).scalar_one()
    row.attempts += 1
    attempt_no = row.attempts
    row.lease_expires_at = None
    row.error_class = error_class
    row.error_code = error_code
    if attempt_no < settings.worker_max_attempts:
        row.status = DeliveryStatus.QUEUED
        row.started_at = None
        backoff = min(
            settings.worker_backoff_cap_s,
            settings.worker_backoff_base_s * (2 ** (attempt_no - 1)),
        )
        row.next_attempt_at = now + timedelta(seconds=backoff)
    else:
        row.status = DeliveryStatus.FAILED
        row.failed_at = now
        row.next_attempt_at = None
        _alert_pilot_on_terminal_failure(db, row=row, now=now)
    db.add(
        NotificationDeliveryAttempt(
            outbox_id=row.id,
            attempt_no=attempt_no,
            started_at=row.started_at or now,
            finished_at=now,
            outcome="failed",
            error_class=error_class,
            error_code=error_code,
        )
    )
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
    schedule_notification_row(
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
