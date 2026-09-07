"""Notification planning and delivery primitives (transactional outbox).

Business operations (events, transfers, reminders, system alerts) call the
planning helpers *inside their own transaction*, so the outbox row commits
together with the business change — a crash can never lose a notification.

All message texts are snapshots created here. They deliberately carry no
candidate PII (names/contacts never appear); a notification that links an
object only stores its ids, and the resolve endpoint re-checks the current
access rights before the UI navigates anywhere.
"""

import logging
import sqlite3
from datetime import datetime, timedelta
from uuid import UUID
from zoneinfo import ZoneInfo

from psycopg import errors as pg_errors
from sqlalchemy import update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import Settings
from app.models import (
    CandidateTransfer,
    DeliveryChannel,
    DeliveryStatus,
    Event,
    Notification,
    NotificationOutbox,
    NotificationPreference,
    NotificationPriority,
    NotificationSource,
    NotificationType,
    TelegramLink,
    User,
    UserEmail,
)
from app.utils import ensure_aware, utc_now

logger = logging.getLogger(__name__)

# Outbox templates with special worker handling (phase 9).
EMAIL_VERIFICATION_TEMPLATE = "email_verification"
CHANNEL_TEST_TEMPLATE = "channel_test"
# Templates that never require a stored opt-in (the action itself is the
# explicit one-shot consent: confirming an address, testing own channel).
CONSENT_EXEMPT_TEMPLATES = frozenset({EMAIL_VERIFICATION_TEMPLATE, CHANNEL_TEST_TEMPLATE})


def is_duplicate_key_error(exc: IntegrityError) -> bool:
    """True ONLY for the unique-violation class of IntegrityError.

    Deduplication must never mask other integrity failures (foreign-key
    violations, CHECK violations, schema drift, ...): those are bugs or
    data errors and must propagate to the caller.
    """
    orig = exc.orig
    if orig is None:
        return False
    # PostgreSQL raises psycopg.errors.UniqueViolation for UNIQUE / partial
    # unique indexes.
    if isinstance(orig, pg_errors.UniqueViolation):
        return True
    # SQLite (isolated unit tests) reports the UNIQUE class with this text.
    if isinstance(orig, sqlite3.IntegrityError):
        return "UNIQUE constraint failed" in str(orig)
    return False


def _flush_inside_savepoint(db: Session, row: object) -> bool:
    """Flush one row inside a SAVEPOINT.

    Returns True when the flush succeeded. On a duplicate-key IntegrityError
    the savepoint alone is rolled back (the row is added INSIDE the
    savepoint, so SQLAlchemy expunges it on rollback) — the caller's
    surrounding transaction stays intact (explicit contract:
    ``schedule``/``deliver_in_app`` return None/False ONLY for a duplicate
    key and never roll back the caller's unit of work). Any other exception
    propagates unchanged.
    """
    try:
        with db.begin_nested():
            db.add(row)
            db.flush()
    except IntegrityError as exc:
        if not is_duplicate_key_error(exc):
            raise
        return False
    return True


# Titles are Russian UI strings; they never contain names or other PII.
_TITLES: dict[NotificationType, str] = {
    NotificationType.EVENT_ASSIGNED: "Вам назначено событие",
    NotificationType.EVENT_APPROACHING: "Скоро начнётся событие",
    NotificationType.EVENT_OVERDUE: "Событие просрочено",
    NotificationType.EVENT_RESCHEDULED: "Событие перенесено",
    NotificationType.EVENT_CANCELLED: "Событие отменено",
    NotificationType.CANDIDATE_TRANSFERRED: "Вам передан кандидат",
    NotificationType.REMINDER_DUE: "Наступило напоминание",
    NotificationType.REMINDER_OVERDUE: "Напоминание просрочено",
    NotificationType.SYSTEM_ALERT: "Системное уведомление",
}

_EVENT_TYPE_LABELS = {"call": "Звонок", "interview": "Собеседование", "reminder": "Напоминание"}


def format_local(when_utc: datetime, timezone: str) -> str:
    """Render a UTC instant in the user's timezone for message bodies.

    Only a date/time presentation — no names are involved.
    """
    local = when_utc.astimezone(ZoneInfo(timezone))
    return local.strftime("%d.%m.%Y %H:%M")


def preference_for(db: Session, user_id: UUID, settings_timezone: str) -> NotificationPreference:
    """Load the user's preferences, or a synthetic row with system defaults.

    The synthetic row is NOT persisted here — persistence happens when the
    user opens the settings screen (the API upserts on write). System
    defaults come from the application settings.
    """
    from app.config import DEFAULT_QUIET_HOURS_END, DEFAULT_QUIET_HOURS_START, DEFAULT_WORKDAYS

    row = db.get(NotificationPreference, user_id)
    if row is not None:
        return row
    return NotificationPreference(
        user_id=user_id,
        timezone=settings_timezone,
        quiet_hours_start=DEFAULT_QUIET_HOURS_START,
        quiet_hours_end=DEFAULT_QUIET_HOURS_END,
        workdays=[int(day) for day in DEFAULT_WORKDAYS.split(",")],
        enabled_types=[member.value for member in NotificationType],
        enabled_channels=[DeliveryChannel.IN_APP.value],
    )


def schedule(
    db: Session,
    *,
    recipient_user_id: UUID | None,
    external_recipient: str | None = None,
    channel: DeliveryChannel = DeliveryChannel.IN_APP,
    type_: NotificationType,
    source: NotificationSource = NotificationSource.SYSTEM,
    title: str | None = None,
    body: str | None = None,
    priority: NotificationPriority = NotificationPriority.NORMAL,
    object_type: str | None = None,
    object_id: UUID | None = None,
    dedupe_key: str,
    scheduled_at: datetime | None = None,
    template: str | None = None,
    template_version: int | None = None,
    initiator_user_id: UUID | None = None,
    consent_snapshot: dict | None = None,
    quiet_hours_bypassed: bool = False,
) -> NotificationOutbox | None:
    """Insert one outbox row (transactional, deduplicated).

    Returns the row (flushed so ``id`` is available), or ``None`` ONLY when
    an identical idempotency key already exists (the business event was
    processed before — no duplicate is ever created). Explicit contract:

    * a duplicate key rolls back a SAVEPOINT around this single INSERT and
      leaves the caller's surrounding transaction fully usable;
    * every other error (FK/CHECK violations, schema drift, connection
      failures, ...) propagates to the caller unchanged — it is never
      masked as a deduplication no-op.
    * ``external_recipient`` bypasses the user binding/consent lookups, so
      it is accepted ONLY for the address-verification letter (EMAIL
      channel + ``EMAIL_VERIFICATION_TEMPLATE``) and must be a valid
      mailbox — anything else raises ValueError and queues nothing. All
      other templates resolve the recipient from the user's own verified
      binding at send time and ignore any stored external value.
    """
    if recipient_user_id is None and external_recipient is None:
        raise ValueError("either recipient_user_id or external_recipient is required")
    if recipient_user_id is not None and external_recipient is not None:
        raise ValueError("recipient_user_id and external_recipient are mutually exclusive")
    if external_recipient is not None:
        from app.smtp import validate_mailbox

        if channel != DeliveryChannel.EMAIL or template != EMAIL_VERIFICATION_TEMPLATE:
            raise ValueError("external_recipient is allowed only for the email verification letter")
        external_recipient = validate_mailbox(external_recipient, field="external_recipient")

    row = NotificationOutbox(
        recipient_user_id=recipient_user_id,
        external_recipient=external_recipient,
        channel=channel,
        notification_type=type_,
        source=source,
        title=title or _TITLES[type_],
        body=body,
        template=template,
        template_version=template_version,
        initiator_user_id=initiator_user_id,
        object_type=object_type,
        object_id=object_id,
        scheduled_at=scheduled_at,
        queued_at=utc_now(),
        status=DeliveryStatus.QUEUED,
        idempotency_key=f"{type_.value}:{dedupe_key}",
        consent_snapshot=consent_snapshot,
        quiet_hours_bypassed=quiet_hours_bypassed,
    )
    if not _flush_inside_savepoint(db, row):
        logger.info("notification deduplicated (idempotency_key already present)")
        return None
    return row


def cancel_pending_for_object(
    db: Session, *, object_type: str, object_id: UUID, now: datetime | None = None
) -> int:
    """Cancel queued (not yet claimed) outbox rows for a business object.

    Used when an event is rescheduled/completed/cancelled or a reminder is
    edited: the stale plan must never fire. Rows already ``sending`` are
    left alone (they finish with their own outcome). Returns the count.
    """
    now = now or utc_now()
    result = db.execute(
        update(NotificationOutbox)
        .where(
            NotificationOutbox.object_type == object_type,
            NotificationOutbox.object_id == object_id,
            NotificationOutbox.status == DeliveryStatus.QUEUED,
        )
        .values(status=DeliveryStatus.CANCELLED, cancelled_at=now)
    )
    cancelled = result.rowcount if result.rowcount is not None else 0  # type: ignore[attr-defined]
    return cancelled


def deliver_in_app(db: Session, outbox: NotificationOutbox, *, now: datetime | None = None) -> bool:
    """Atomically create the in-app notification for an outbox row.

    Idempotent: the notification carries the outbox's idempotency key as
    its dedupe key (unique partial index), so a repeated delivery attempt
    never duplicates the row. Returns True when a new row was created, and
    False ONLY for that duplicate-key case (savepoint rollback, caller's
    transaction untouched). Any other error propagates unchanged — a
    delivery failure caused by data/schema problems is never disguised as
    «already delivered».
    """
    now = now or utc_now()
    assert outbox.recipient_user_id is not None
    notification = Notification(
        user_id=outbox.recipient_user_id,
        type=outbox.notification_type,
        title=outbox.title,
        body=outbox.body,
        priority=NotificationPriority.NORMAL,
        source=outbox.source,
        object_type=outbox.object_type,
        object_id=outbox.object_id,
        meta=None,
        dedupe_key=outbox.idempotency_key,
        created_at=now,
    )
    return _flush_inside_savepoint(db, notification)


def schedule_notification_row(
    db: Session,
    *,
    type_: NotificationType,
    recipient_user_id: UUID,
    title: str | None = None,
    body: str | None = None,
    object_type: str | None = None,
    object_id: UUID | None = None,
    dedupe_key: str,
    scheduled_at: datetime | None = None,
    priority: NotificationPriority = NotificationPriority.NORMAL,
    source: NotificationSource = NotificationSource.SYSTEM,
    initiator_user_id: UUID | None = None,
) -> NotificationOutbox | None:
    """Convenience wrapper for the in-app channel (see :func:`schedule`)."""
    return schedule(
        db,
        recipient_user_id=recipient_user_id,
        channel=DeliveryChannel.IN_APP,
        type_=type_,
        source=source,
        title=title,
        body=body,
        priority=priority,
        object_type=object_type,
        object_id=object_id,
        dedupe_key=dedupe_key,
        scheduled_at=scheduled_at,
        initiator_user_id=initiator_user_id,
    )


# --- External channels (phase 9): fan-out over the same outbox -----------------


def has_channel_consent(
    preference: NotificationPreference | None, channel: DeliveryChannel
) -> bool:
    """True only for an explicit, complete grant (fail-closed).

    Activation requires BOTH ``*_opt_in`` and ``*_consent_granted`` to be
    explicitly true — a missing row, a ``None``/false flag, or a bare
    ``enabled_channels`` entry never activates a channel. The explicit
    ``is True`` comparisons stay fail-closed even for transient (never
    flushed) preference objects whose column defaults did not apply yet.
    """
    if preference is None:
        return False
    if channel == DeliveryChannel.TELEGRAM:
        return bool(
            preference.telegram_opt_in is True and preference.telegram_consent_granted is True
        )
    if channel == DeliveryChannel.EMAIL:
        return bool(preference.email_opt_in is True and preference.email_consent_granted is True)
    return False


def external_channels_for(
    db: Session, *, user_id: UUID, settings: Settings
) -> list[DeliveryChannel]:
    """Channels eligible for a NEW outbox row for ``user_id``.

    A channel is listed only when the global configuration is enabled AND
    the user has a valid binding (confirmed Telegram chat_id / verified
    email) AND an explicit opt-in consent. The worker re-validates all of
    this at send time; this check only avoids queueing hopeless rows.
    Never enabled silently: without consent the list is empty.
    """
    from app.smtp import config_from_settings as smtp_config_from_settings
    from app.telegram import config_from_settings as telegram_config_from_settings

    preference = db.get(NotificationPreference, user_id)
    telegram_consent = has_channel_consent(preference, DeliveryChannel.TELEGRAM)
    email_consent = has_channel_consent(preference, DeliveryChannel.EMAIL)
    channels: list[DeliveryChannel] = []
    if telegram_consent and telegram_config_from_settings(settings).is_configured:
        link = db.get(TelegramLink, user_id)
        if link is not None and link.is_linked:
            channels.append(DeliveryChannel.TELEGRAM)
    if email_consent and smtp_config_from_settings(settings).is_configured:
        address = db.get(UserEmail, user_id)
        if address is not None and address.is_verified:
            channels.append(DeliveryChannel.EMAIL)
    return channels


def schedule_fan_out(
    db: Session,
    *,
    type_: NotificationType,
    recipient_user_id: UUID,
    title: str | None = None,
    body: str | None = None,
    object_type: str | None = None,
    object_id: UUID | None = None,
    dedupe_key: str,
    scheduled_at: datetime | None = None,
    priority: NotificationPriority = NotificationPriority.NORMAL,
    source: NotificationSource = NotificationSource.SYSTEM,
    initiator_user_id: UUID | None = None,
    settings: Settings,
) -> list[NotificationOutbox | None]:
    """Schedule in-app delivery plus every eligible external channel.

    The in-app row keeps the phase-8 idempotency key (unchanged contract);
    external rows use ``{key}:{channel}`` keys and a consent snapshot.
    Without consent/bindings exactly one (in-app) row is created, so all
    phase-8 callers keep their behavior until the user opts in.
    """
    from app.config import CONSENT_POLICY_VERSION

    rows: list[NotificationOutbox | None] = [
        schedule_notification_row(
            db,
            type_=type_,
            recipient_user_id=recipient_user_id,
            title=title,
            body=body,
            object_type=object_type,
            object_id=object_id,
            dedupe_key=dedupe_key,
            scheduled_at=scheduled_at,
            priority=priority,
            source=source,
            initiator_user_id=initiator_user_id,
        )
    ]
    for channel in external_channels_for(db, user_id=recipient_user_id, settings=settings):
        rows.append(
            schedule(
                db,
                recipient_user_id=recipient_user_id,
                channel=channel,
                type_=type_,
                source=source,
                title=title,
                body=body,
                priority=priority,
                object_type=object_type,
                object_id=object_id,
                dedupe_key=f"{dedupe_key}:{channel.value}",
                scheduled_at=scheduled_at,
                initiator_user_id=initiator_user_id,
                consent_snapshot={
                    "channel": channel.value,
                    "opt_in": True,
                    "policy_version": CONSENT_POLICY_VERSION,
                },
                quiet_hours_bypassed=False,
            )
        )
    return rows


def schedule_verification_email(
    db: Session, *, pending_email: str, token: str, request_id: str
) -> NotificationOutbox | None:
    """Queue the address-confirmation email to a not-yet-verified mailbox.

    The external recipient is allow-listed by construction (the address the
    owning user just entered, validated server-side) — never an arbitrary
    payload value. The exact text (including the token) is snapshotted as
    required for the immutable delivery history.
    """
    return schedule(
        db,
        recipient_user_id=None,
        external_recipient=pending_email,
        channel=DeliveryChannel.EMAIL,
        type_=NotificationType.SYSTEM_ALERT,
        source=NotificationSource.SYSTEM,
        title="Подтвердите адрес электронной почты",
        body=(
            "Вы указали этот адрес для уведомлений HR Manager.\n\n"
            f"Код подтверждения: {token}\n\n"
            "Введите код в разделе «Интеграции». "
            "Если вы не запрашивали код, проигнорируйте это письмо."
        ),
        object_type=None,
        object_id=None,
        dedupe_key=f"email-verify:{request_id}",
        scheduled_at=utc_now(),
        template=EMAIL_VERIFICATION_TEMPLATE,
        template_version=1,
    )


def schedule_channel_test(
    db: Session,
    *,
    recipient_user_id: UUID,
    channel: DeliveryChannel,
    request_id: str,
) -> NotificationOutbox | None:
    """Queue an explicit test message to the user's own channel binding."""
    label = "Telegram" if channel == DeliveryChannel.TELEGRAM else "email"
    return schedule(
        db,
        recipient_user_id=recipient_user_id,
        channel=channel,
        type_=NotificationType.SYSTEM_ALERT,
        source=NotificationSource.MANUAL,
        title="Тестовое уведомление",
        body=(
            f"Это тестовое уведомление HR Manager (канал: {label}). "
            "Канал работает: сообщение принято провайдером."
        ),
        dedupe_key=f"channel-test:{channel.value}:{request_id}",
        scheduled_at=utc_now(),
        template=CHANNEL_TEST_TEMPLATE,
        template_version=1,
        initiator_user_id=recipient_user_id,
    )


# --- Event planning (called from the events router inside its transaction) ----


def plan_event_notifications(
    db: Session,
    *,
    event: Event,
    author: User,
    assignee: User,
    settings: Settings,
    now: datetime | None = None,
) -> None:
    """(Re)plan the notifications derived from one event mutation.

    * ``event_assigned`` — to the assignee, unless the author assigned
      themselves (creating something for yourself needs no bell);
    * ``event_approaching`` — for call/interview at each configured offset
      before ``starts_at`` (only future instants are planned);
    * ``event_overdue`` — at ``starts_at`` (the phase-5 definition of an
      overdue event is «scheduled and starts_at < now»).

    The caller cancels the stale plan first when rescheduling.
    """
    now = now or utc_now()
    if assignee.id != author.id:
        schedule_fan_out(
            db,
            type_=NotificationType.EVENT_ASSIGNED,
            recipient_user_id=assignee.id,
            object_type="event",
            object_id=event.id,
            dedupe_key=f"{event.id}:{event.version}:assigned:{assignee.id}",
            scheduled_at=now,
            initiator_user_id=author.id,
            settings=settings,
        )
    if event.status.value == "scheduled":
        _plan_approaching_and_overdue(db, event=event, settings=settings, now=now)


def plan_event_state_notification(
    db: Session,
    *,
    event: Event,
    recipient: User,
    type_: NotificationType,
    initiator_user_id: UUID,
    settings: Settings,
) -> None:
    """One-shot notification about a lifecycle change (rescheduled/cancelled)."""
    schedule_fan_out(
        db,
        type_=type_,
        recipient_user_id=recipient.id,
        object_type="event",
        object_id=event.id,
        dedupe_key=f"{event.id}:{event.version}:{type_.value}",
        scheduled_at=utc_now(),
        initiator_user_id=initiator_user_id,
        settings=settings,
    )


def _plan_approaching_and_overdue(
    db: Session, *, event: Event, settings: Settings, now: datetime
) -> None:
    starts = ensure_aware(event.starts_at)
    for offset_h in _approach_hours_for(event, settings):
        at = starts - timedelta(hours=offset_h)
        if at <= now:
            continue
        schedule_fan_out(
            db,
            type_=NotificationType.EVENT_APPROACHING,
            recipient_user_id=event.assignee_user_id,
            object_type="event",
            object_id=event.id,
            dedupe_key=f"{event.id}:{event.version}:approaching:{offset_h}",
            scheduled_at=at,
            settings=settings,
        )
    schedule_fan_out(
        db,
        type_=NotificationType.EVENT_OVERDUE,
        recipient_user_id=event.assignee_user_id,
        object_type="event",
        object_id=event.id,
        dedupe_key=f"{event.id}:{event.version}:overdue",
        scheduled_at=starts,
        settings=settings,
    )


def _approach_hours_for(event: Event, settings: Settings) -> list[float]:
    if event.type.value == "interview":
        raw = settings.notification_interview_approach_hours
    elif event.type.value == "call":
        raw = settings.notification_call_approach_hours
    else:
        return []
    return [float(part) for part in raw.split(",") if part]


def transfer_notification(
    db: Session, *, transfer: CandidateTransfer, new_owner: User, settings: Settings
) -> None:
    """Notify the new responsible HR that a candidate was handed to them."""
    schedule_fan_out(
        db,
        type_=NotificationType.CANDIDATE_TRANSFERRED,
        recipient_user_id=new_owner.id,
        object_type="candidate",
        object_id=transfer.candidate_id,
        dedupe_key=f"transfer:{transfer.id}",
        scheduled_at=utc_now(),
        settings=settings,
    )
