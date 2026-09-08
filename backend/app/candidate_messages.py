"""One-way Russian messages to candidates (phase 10).

Six message types only — interview scheduled / reminder / rescheduled /
cancelled and document request / reminder. Everything is one-way: no
replies, no chat, no inbound mail, no document uploads through channels.

Security model (mirrors the phase-8/9 outbox contract):

* every message goes through the SAME PostgreSQL outbox and worker —
  there is no send inside an HTTP request and no new queue;
* the concrete recipient (email address, Telegram chat id) is resolved by
  the WORKER from the candidate's own state at send time — never from a
  client payload;
* a channel is eligible only with a recorded consent (fail-closed: no row
  or ``granted=false`` never sends); for email the consent is pinned to
  the normalized address it was recorded for, so a changed address
  re-opens the confirmation;
* message texts are snapshotted at scheduling time (immutable history);
  logs, metrics, audit details and error fields never contain the text,
  the address, the chat id or any candidate PII;
* ``accepted`` means the provider took the message — never «delivered»,
  never «read».
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import CANDIDATE_CONSENT_POLICY_VERSION, Settings
from app.models import (
    Candidate,
    CandidateChannelConsent,
    CandidateTelegramLink,
    CandidateTelegramLinkToken,
    DeliveryChannel,
    Event,
    NotificationOutbox,
    NotificationPreference,
    NotificationSource,
    NotificationType,
)
from app.notification_service import schedule
from app.utils import ensure_aware, normalize_email, utc_now

logger = logging.getLogger(__name__)

# Closed vocabulary of candidate message types (API contract).
CANDIDATE_MESSAGE_TYPES: dict[str, NotificationType] = {
    "interview_scheduled": NotificationType.CANDIDATE_INTERVIEW_SCHEDULED,
    "interview_reminder": NotificationType.CANDIDATE_INTERVIEW_REMINDER,
    "interview_rescheduled": NotificationType.CANDIDATE_INTERVIEW_RESCHEDULED,
    "interview_cancelled": NotificationType.CANDIDATE_INTERVIEW_CANCELLED,
    "document_request": NotificationType.CANDIDATE_DOCUMENT_REQUEST,
    "document_reminder": NotificationType.CANDIDATE_DOCUMENT_REMINDER,
}
CANDIDATE_MESSAGE_TYPE_KEYS = frozenset(CANDIDATE_MESSAGE_TYPES)
INTERVIEW_MESSAGE_TYPES = frozenset(
    {"interview_scheduled", "interview_reminder", "interview_rescheduled", "interview_cancelled"}
)
DOCUMENT_MESSAGE_TYPES = frozenset({"document_request", "document_reminder"})

# Safe-substitution limits (server-side, before any template render).
MAX_DOCUMENT_ITEMS = 20
MAX_DOCUMENT_ITEM_LENGTH = 200
MAX_BODY_LENGTH = 4000

# Channel states shown to the HR (closed vocabulary, mirrored in the UI).
CHANNEL_STATE_NOT_CONNECTED = "not_connected"
CHANNEL_STATE_PENDING = "pending"
CHANNEL_STATE_ALLOWED = "allowed"
CHANNEL_STATE_FORBIDDEN = "forbidden"
CHANNEL_STATE_TEMPORARILY_UNAVAILABLE = "temporarily_unavailable"

# Temporary provider classes reported on candidate bindings (mirror of the
# phase-9 user-channel classes).
_TEMP_ERROR_CLASSES = frozenset(
    {
        "telegram_rate_limited",
        "telegram_timeout",
        "telegram_network",
        "telegram_server",
        "telegram_bad_response",
        "transport_error",
        "smtp_temporary",
        "smtp_timeout",
        "smtp_network",
        "smtp_auth",
        "smtp_config",
    }
)
_TEMP_ERROR_WINDOW = timedelta(hours=1)

# Control characters are stripped from every substituted value: they can
# neither break the plain-text templates nor smuggle markup (channels are
# rendered as plain text — Telegram sends without parse_mode, SMTP bodies
# are plain text — but the templates must stay single-line per field).
_CONTROL_CHARS = re.compile(r"[\x00-\x1f\x7f\u2028\u2029]")


def sanitize_inline(value: str, *, max_length: int) -> str:
    """One safe line: strip control chars, collapse whitespace, cap length."""
    cleaned = _CONTROL_CHARS.sub(" ", value)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned[:max_length]


@dataclass(frozen=True)
class RenderedMessage:
    """A fully rendered one-way message (exact text, immutable once queued)."""

    title: str
    body: str


def _greeting(candidate: Candidate) -> str:
    """Formal greeting with the full stored name (no guesswork about which
    part is the given name — the exact stored value, sanitized)."""
    name = sanitize_inline(candidate.full_name, max_length=100)
    return f"Здравствуйте, {name}!" if name else "Здравствуйте!"


def _position_line(candidate: Candidate) -> str:
    position = sanitize_inline(candidate.position, max_length=200)
    return f"Позиция: {position}." if position else ""


def _documents_block(documents: list[str]) -> str:
    items = "\n".join(
        f"— {sanitize_inline(item, max_length=MAX_DOCUMENT_ITEM_LENGTH)}" for item in documents
    )
    return items


def render_candidate_message(
    message_type: str,
    *,
    candidate: Candidate,
    starts_at_local: str | None = None,
    previous_starts_at_local: str | None = None,
    documents: list[str] | None = None,
) -> RenderedMessage:
    """Render one of the six one-way message types (Russian, plain text).

    All substituted values pass through :func:`sanitize_inline`; the
    templates carry no markup, so the result is safe for both plain-text
    channels (Telegram without parse_mode, SMTP text body). The interview
    time (already localized by the caller) is the ONLY event-derived
    value: the server renders from its own state, never from a client
    payload. The events model carries no free-form location field, so the
    templates deliberately have no «Место» line.
    """
    if message_type not in CANDIDATE_MESSAGE_TYPE_KEYS:
        raise ValueError(f"unknown candidate message type: {message_type!r}")

    greeting = _greeting(candidate)
    position_line = _position_line(candidate)

    if message_type == "interview_scheduled":
        body = (
            f"{greeting}\n\n"
            f"Ваше собеседование назначено на {starts_at_local}."
            + (f"\n{position_line}" if position_line else "")
            + "\n\nЕсли планы изменились, сообщите вашему HR-менеджеру."
        )
        return RenderedMessage(title="Собеседование назначено", body=body)

    if message_type == "interview_reminder":
        body = (
            f"{greeting}\n\n"
            f"Напоминаем: ваше собеседование состоится {starts_at_local}."
            + (f"\n{position_line}" if position_line else "")
            + "\n\nЖдём вас!"
        )
        return RenderedMessage(title="Напоминание о собеседовании", body=body)

    if message_type == "interview_rescheduled":
        body = (
            f"{greeting}\n\n"
            f"Ваше собеседование, запланированное на {previous_starts_at_local}, "
            f"перенесено на {starts_at_local}."
            + (f"\n{position_line}" if position_line else "")
            + "\n\nЕсли новое время не подходит, сообщите вашему HR-менеджеру."
        )
        return RenderedMessage(title="Собеседование перенесено", body=body)

    if message_type == "interview_cancelled":
        body = (
            f"{greeting}\n\n"
            f"Собеседование, запланированное на {starts_at_local}, отменено."
            + (f"\n{position_line}" if position_line else "")
            + "\n\nВаш HR-менеджер свяжется с вами, чтобы согласовать новые сроки."
        )
        return RenderedMessage(title="Собеседование отменено", body=body)

    if message_type == "document_request":
        items = _documents_block(documents or [])
        body = (
            f"{greeting}\n\n"
            "Для продолжения оформления нам нужны следующие документы:\n"
            f"{items}\n\n"
            "Пожалуйста, передайте их вашему HR-менеджеру."
        )
        return RenderedMessage(title="Запрос документов", body=body)

    # document_reminder
    items = _documents_block(documents or [])
    body = (
        f"{greeting}\n\n"
        "Напоминаем: мы всё ещё ожидаем от вас следующие документы:\n"
        f"{items}\n\n"
        "Пожалуйста, передайте их вашему HR-менеджеру."
    )
    return RenderedMessage(title="Напоминание о документах", body=body)


# --- Channel eligibility and state ---------------------------------------------


def candidate_consent(
    db: Session, candidate_id: UUID, channel: DeliveryChannel
) -> CandidateChannelConsent | None:
    return db.get(CandidateChannelConsent, (candidate_id, channel.value))


def _email_consent_is_current(
    consent: CandidateChannelConsent | None, candidate: Candidate
) -> bool:
    """A valid email consent: granted by the candidate's OWN confirmation
    click (double opt-in) and pinned to the exact normalized address it
    covered. An HR-recorded grant never unlocks regular messages."""
    if consent is None or not consent.granted:
        return False
    if consent.source != "email_confirm":
        return False
    if not candidate.email:
        return False
    return consent.email_normalized == normalize_email(candidate.email)


def telegram_link_for(db: Session, candidate_id: UUID) -> CandidateTelegramLink | None:
    return db.get(CandidateTelegramLink, candidate_id)


def _has_recent_temp_error(
    last_error_class: str | None, last_error_at: datetime | None, now: datetime
) -> bool:
    return (
        last_error_class in _TEMP_ERROR_CLASSES
        and last_error_at is not None
        and (now - last_error_at) <= _TEMP_ERROR_WINDOW
    )


def candidate_channel_state(
    db: Session,
    *,
    candidate: Candidate,
    channel: DeliveryChannel,
    settings: Settings,
    now: datetime | None = None,
) -> str:
    """The honest state of one channel for one candidate (no PII inside)."""
    now = now or utc_now()
    if channel == DeliveryChannel.TELEGRAM:
        from app.telegram import config_from_settings as telegram_config_from_settings

        if not telegram_config_from_settings(settings).is_configured:
            return CHANNEL_STATE_NOT_CONNECTED
        link = telegram_link_for(db, candidate.id)
        if link is not None and link.chat_id is not None:
            if link.revoked_at is not None:
                return CHANNEL_STATE_FORBIDDEN
            consent = candidate_consent(db, candidate.id, channel)
            if consent is None:
                return CHANNEL_STATE_PENDING
            if not consent.granted:
                return CHANNEL_STATE_FORBIDDEN
            if _has_recent_temp_error(link.last_error_class, link.last_error_at, now):
                return CHANNEL_STATE_TEMPORARILY_UNAVAILABLE
            return CHANNEL_STATE_ALLOWED
        # No active binding: an explicit refusal still shows «forbidden».
        consent = candidate_consent(db, candidate.id, channel)
        if consent is not None and not consent.granted:
            return CHANNEL_STATE_FORBIDDEN
        return (
            CHANNEL_STATE_PENDING
            if _active_invite_exists(db, candidate.id, now)
            else CHANNEL_STATE_NOT_CONNECTED
        )

    if channel == DeliveryChannel.EMAIL:
        from app.smtp import config_from_settings as smtp_config_from_settings

        if not smtp_config_from_settings(settings).is_configured:
            return CHANNEL_STATE_NOT_CONNECTED
        if not candidate.email:
            return CHANNEL_STATE_NOT_CONNECTED
        consent = candidate_consent(db, candidate.id, channel)
        if consent is not None and not consent.granted:
            return CHANNEL_STATE_FORBIDDEN
        if not _email_consent_is_current(consent, candidate):
            # Not confirmed by the candidate for this address yet (or the
            # address changed): waiting for the confirmation click.
            return CHANNEL_STATE_PENDING
        return CHANNEL_STATE_ALLOWED

    return CHANNEL_STATE_NOT_CONNECTED


def _active_invite_exists(db: Session, candidate_id: UUID, now: datetime) -> bool:
    return (
        db.execute(
            select(CandidateTelegramLinkToken.id)
            .where(
                CandidateTelegramLinkToken.candidate_id == candidate_id,
                CandidateTelegramLinkToken.consumed_at.is_(None),
                CandidateTelegramLinkToken.expires_at > now,
            )
            .limit(1)
        ).scalar()
        is not None
    )


def allowed_candidate_channels(
    db: Session, *, candidate: Candidate, settings: Settings, now: datetime | None = None
) -> list[DeliveryChannel]:
    """Channels a NEW candidate message may be queued for right now."""
    now = now or utc_now()
    channels: list[DeliveryChannel] = []
    for channel in (DeliveryChannel.EMAIL, DeliveryChannel.TELEGRAM):
        state = candidate_channel_state(
            db, candidate=candidate, channel=channel, settings=settings, now=now
        )
        if state == CHANNEL_STATE_ALLOWED:
            channels.append(channel)
    return channels


# --- Consent bookkeeping --------------------------------------------------------


def record_candidate_consent(
    db: Session,
    *,
    candidate: Candidate,
    channel: DeliveryChannel,
    granted: bool,
    granted_by_user_id: UUID | None,
    source: str,
    commit: bool = True,
) -> CandidateChannelConsent:
    """Upsert the per-channel consent decision (audited by the caller).

    Email may be GRANTED only by the candidate's own confirmation click
    (``source='email_confirm'``) — an HR call can never unlock regular
    email messages by itself (double opt-in). Revocation stays available
    to the HR for both channels and is immediate/fail-closed.
    """
    from app.notification_service import cancel_pending_candidate_channel_messages

    if channel == DeliveryChannel.EMAIL and granted and source != "email_confirm":
        raise ValueError("email consent may only be granted by the candidate's confirmation")

    consent = candidate_consent(db, candidate.id, channel)
    now = utc_now()
    if consent is None:
        consent = CandidateChannelConsent(
            candidate_id=candidate.id,
            channel=channel.value,
            source=source,
            policy_version=CANDIDATE_CONSENT_POLICY_VERSION,
            created_at=now,
        )
        db.add(consent)
    consent.granted = granted
    consent.granted_at = now
    consent.granted_by_user_id = granted_by_user_id
    consent.source = source
    consent.policy_version = CANDIDATE_CONSENT_POLICY_VERSION
    consent.updated_at = now
    if channel == DeliveryChannel.EMAIL:
        consent.email_normalized = normalize_email(candidate.email) if candidate.email else None
    else:
        consent.email_normalized = None
    if not granted:
        # A revoked consent stops every pending send of that channel.
        cancel_pending_candidate_channel_messages(
            db, candidate_id=candidate.id, channel=channel, now=now
        )
    if commit:
        db.commit()
    return consent


# --- Scheduling -----------------------------------------------------------------


def _consent_snapshot(db: Session, candidate: Candidate, channel: DeliveryChannel) -> dict:
    """PII-free snapshot of the consent state at scheduling time."""
    consent = candidate_consent(db, candidate.id, channel)
    return {
        "channel": channel.value,
        "granted": bool(consent is not None and consent.granted),
        "source": consent.source if consent is not None else None,
        "policy_version": CANDIDATE_CONSENT_POLICY_VERSION,
    }


def queue_candidate_message(
    db: Session,
    *,
    candidate: Candidate,
    channel: DeliveryChannel,
    message: RenderedMessage,
    message_type_key: str,
    source: NotificationSource,
    event_id: UUID | None = None,
    event_version: int | None = None,
    initiator_user_id: UUID | None = None,
    dedupe_key: str,
    scheduled_at: datetime | None = None,
) -> NotificationOutbox | None:
    """Queue one candidate message on one channel (transactional outbox).

    The recipient is the *candidate* — the concrete address/chat id is
    resolved by the worker at send time. ``event_version`` snapshots the
    interview's optimistic version: the worker refuses to send the row if
    the event mutated after rendering (reschedule/cancel/complete).
    Returns the row or ``None`` when the idempotency key already exists
    (duplicate business event).
    """
    type_ = CANDIDATE_MESSAGE_TYPES[message_type_key]
    return schedule(
        db,
        recipient_user_id=None,
        recipient_candidate_id=candidate.id,
        channel=channel,
        type_=type_,
        source=source,
        title=message.title,
        body=message.body[:MAX_BODY_LENGTH],
        object_type="event" if event_id is not None else None,
        object_id=event_id,
        object_version=event_version if event_id is not None else None,
        dedupe_key=f"cand:{dedupe_key}:{channel.value}",
        scheduled_at=scheduled_at,
        template=f"candidate_{message_type_key}",
        template_version=1,
        initiator_user_id=initiator_user_id,
        consent_snapshot=_consent_snapshot(db, candidate, channel),
    )


def queue_candidate_message_all_channels(
    db: Session,
    *,
    candidate: Candidate,
    message: RenderedMessage,
    message_type_key: str,
    source: NotificationSource,
    event_id: UUID | None = None,
    event_version: int | None = None,
    initiator_user_id: UUID | None = None,
    dedupe_key: str,
    scheduled_at: datetime | None = None,
    settings: Settings,
    only_channel: DeliveryChannel | None = None,
) -> list[NotificationOutbox]:
    """Queue the message on every allowed channel (or one explicit one).

    Channels are recomputed from the CURRENT consent state; without a
    recorded consent nothing is queued (fail-closed, never silent).
    """
    channels = allowed_candidate_channels(db, candidate=candidate, settings=settings)
    if only_channel is not None:
        channels = [only_channel] if only_channel in channels else []
    rows: list[NotificationOutbox] = []
    for channel in channels:
        row = queue_candidate_message(
            db,
            candidate=candidate,
            channel=channel,
            message=message,
            message_type_key=message_type_key,
            source=source,
            event_id=event_id,
            event_version=event_version,
            initiator_user_id=initiator_user_id,
            dedupe_key=dedupe_key,
            scheduled_at=scheduled_at,
        )
        if row is not None:
            rows.append(row)
    return rows


# --- Event-driven planning (called from the events router, same transaction) ----


def _display_timezone(db: Session, candidate: Candidate, settings: Settings) -> str:
    """Candidates have no own preference: interviews are presented in the
    responsible HR's timezone (fallback: the system default)."""
    preference = db.get(NotificationPreference, candidate.owner_user_id)
    if preference is not None and preference.timezone:
        return preference.timezone
    return settings.notification_default_timezone


def plan_candidate_interview_messages(
    db: Session,
    *,
    event: Event,
    candidate: Candidate,
    settings: Settings,
    now: datetime | None = None,
    previous_starts_at: datetime | None = None,
) -> list[NotificationOutbox]:
    """Plan the candidate messages derived from one interview mutation.

    Called inside the event router's transaction:

    * on create / re-plan — «интервью назначено» now plus a reminder at
      every configured offset before ``starts_at`` (only future instants);
    * on reschedule — «интервью перенесено» now (the stale plan, including
      the old reminder, is cancelled by the caller through
      ``cancel_pending_for_object``).
    """
    from app.notification_service import format_local

    now = now or utc_now()
    if event.type.value != "interview" or event.status.value != "scheduled":
        return []
    timezone = _display_timezone(db, candidate, settings)
    starts = ensure_aware(event.starts_at)
    starts_local = format_local(starts, timezone)
    previous_local = (
        format_local(ensure_aware(previous_starts_at), timezone)
        if previous_starts_at is not None
        else None
    )
    rows: list[NotificationOutbox] = []
    if previous_starts_at is None:
        rows.extend(
            queue_candidate_message_all_channels(
                db,
                candidate=candidate,
                message=render_candidate_message(
                    "interview_scheduled", candidate=candidate, starts_at_local=starts_local
                ),
                message_type_key="interview_scheduled",
                source=NotificationSource.SYSTEM,
                event_id=event.id,
                event_version=event.version,
                dedupe_key=f"{event.id}:{event.version}:scheduled",
                scheduled_at=now,
                settings=settings,
            )
        )
    else:
        rows.extend(
            queue_candidate_message_all_channels(
                db,
                candidate=candidate,
                message=render_candidate_message(
                    "interview_rescheduled",
                    candidate=candidate,
                    starts_at_local=starts_local,
                    previous_starts_at_local=previous_local,
                ),
                message_type_key="interview_rescheduled",
                source=NotificationSource.SYSTEM,
                event_id=event.id,
                event_version=event.version,
                dedupe_key=f"{event.id}:{event.version}:rescheduled",
                scheduled_at=now,
                settings=settings,
            )
        )
    for offset_h in settings.candidate_reminder_hours():
        at = starts - timedelta(hours=offset_h)
        if at <= now:
            continue
        rows.extend(
            queue_candidate_message_all_channels(
                db,
                candidate=candidate,
                message=render_candidate_message(
                    "interview_reminder", candidate=candidate, starts_at_local=starts_local
                ),
                message_type_key="interview_reminder",
                source=NotificationSource.SYSTEM,
                event_id=event.id,
                event_version=event.version,
                dedupe_key=f"{event.id}:{event.version}:reminder:{offset_h}",
                scheduled_at=at,
                settings=settings,
            )
        )
    return rows


# The letter body never carries the confirmation URL itself: this
# placeholder is substituted by the worker IN MEMORY right before the
# provider call, so no persisted field (outbox body/title, request
# snapshots, audit, backups) ever contains a working confirmation link.
CONFIRM_URL_PLACEHOLDER = "[[CANDIDATE_EMAIL_CONFIRM_URL]]"

_EMAIL_CONFIRM_TOKEN_INFO = b"hr-manager:candidate-email-confirm-token:v1"


def _email_confirm_derivation_key(settings: Settings) -> bytes:
    """A dedicated key derived from the server secret (never stored)."""
    return hmac.new(
        settings.secret_key.encode("utf-8"),
        _EMAIL_CONFIRM_TOKEN_INFO,
        hashlib.sha256,
    ).digest()


def derive_email_confirm_token(token_id: UUID, settings: Settings) -> str:
    """The raw one-time token: HMAC(derived server key, token row id).

    Deterministic on purpose. The database stores only the SHA-256 hash
    of this value; the worker re-derives the raw token (and the URL) in
    memory at send time, so the capability exists outside the request
    that issued it and outside the worker that mails it — but never in
    any persisted field or backup. Rotating SECRET_KEY invalidates the
    not-yet-confirmed links (bounded by the token TTL).
    """
    return hmac.new(
        _email_confirm_derivation_key(settings),
        b"email-confirm:" + token_id.bytes,
        hashlib.sha256,
    ).hexdigest()


def email_confirm_url(token_id: UUID, settings: Settings) -> str:
    """The public one-shot link of a confirmation token (memory only)."""
    base_url = settings.candidate_email_confirm_base_url.strip()
    token = derive_email_confirm_token(token_id, settings)
    return f"{base_url}/candidates/email/confirm?token={token}"


def queue_candidate_email_confirm(
    db: Session,
    *,
    candidate: Candidate,
    token_id: UUID,
    expires_at: datetime,
    initiator_user_id: UUID,
) -> NotificationOutbox | None:
    """Queue the double opt-in letter (the ONLY consent-free candidate email).

    The body carries :data:`CONFIRM_URL_PLACEHOLDER` instead of the URL:
    the raw token is never persisted anywhere — the worker re-validates
    the token, the card address and the candidate state right before the
    provider call, substitutes the link in memory and skips dead links.
    """
    expires_local = expires_at.strftime("%d.%m.%Y %H:%M UTC")
    body = (
        f"Здравствуйте, {sanitize_inline(candidate.full_name, max_length=100)}!\n\n"
        "При оформлении вашей кандидатуры был указан этот адрес электронной "
        "почты. Чтобы мы могли присылать вам сообщения о собеседованиях и "
        "документах, подтвердите согласие получать письма.\n\n"
        f"Перейдите по ссылке: {CONFIRM_URL_PLACEHOLDER}\n\n"
        f"Ссылка действительна до {expires_local}. "
        "Если вы не давали согласие, просто проигнорируйте это письмо."
    )
    return schedule(
        db,
        recipient_user_id=None,
        recipient_candidate_id=candidate.id,
        channel=DeliveryChannel.EMAIL,
        type_=NotificationType.CANDIDATE_EMAIL_CONFIRM,
        source=NotificationSource.SYSTEM,
        title="Подтвердите согласие на сообщения по почте",
        body=body[:MAX_BODY_LENGTH],
        object_type="candidate_email_token",
        object_id=token_id,
        dedupe_key=f"email-confirm:{token_id}",
        scheduled_at=utc_now(),
        template="candidate_email_confirm",
        template_version=1,
        initiator_user_id=initiator_user_id,
    )


def plan_candidate_interview_cancelled(
    db: Session,
    *,
    event: Event,
    candidate: Candidate,
    settings: Settings,
) -> list[NotificationOutbox]:
    """Queue «интервью отменено» (the caller cancels the stale plan first)."""
    from app.notification_service import format_local

    timezone = _display_timezone(db, candidate, settings)
    starts_local = format_local(ensure_aware(event.starts_at), timezone)
    return queue_candidate_message_all_channels(
        db,
        candidate=candidate,
        message=render_candidate_message(
            "interview_cancelled", candidate=candidate, starts_at_local=starts_local
        ),
        message_type_key="interview_cancelled",
        source=NotificationSource.SYSTEM,
        event_id=event.id,
        event_version=event.version,
        dedupe_key=f"{event.id}:{event.version}:cancelled",
        scheduled_at=utc_now(),
        settings=settings,
    )
