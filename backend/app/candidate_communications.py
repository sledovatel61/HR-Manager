"""Candidate communications: consent, channels, message planning (phase 10).

Candidates are NOT internal users: they never log in, and the six
operational Russian message kinds (interview scheduled / reminder /
rescheduled / cancelled, document requests and reminders) are the only
messages they receive. This module provides the *server-side* building
blocks used by the API router and the worker:

* channel state computation — the honest five states («не подключён»,
  «ожидает подтверждения», «разрешён», «запрещён», «временно недоступен»);
* one-shot token issuing/consumption for email double-opt-in and Telegram
  voluntary ``/start`` linking (hash-only storage);
* message text composition from server-side data (templates with safe
  placeholders — the client can never choose the recipient or the text
  layout);
* transactional scheduling of ``candidate_messages`` rows (the queue +
  immutable history), deduplicated by a unique idempotency key;
* quiet-hours helpers for candidate-facing sends (system defaults — a
  candidate has no personal preference row).

All network I/O happens in the worker (``app/worker.py``) — nothing here
talks to Telegram or SMTP. No PII or full message text ever reaches logs,
audit rows or metrics; the exact text lives only in the immutable
``candidate_messages`` history rows.
"""

import hashlib
import hmac
import logging
import secrets
from datetime import datetime, timedelta
from typing import Literal
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import Settings
from app.models import (
    Candidate,
    CandidateChannelPurpose,
    CandidateChannelToken,
    CandidateContactChannel,
    CandidateMessage,
    CandidateMessageSource,
    CandidateMessageType,
    Event,
    NotificationPreference,
)
from app.notification_service import format_local, is_duplicate_key_error
from app.quiet_hours import in_quiet_period, next_allowed_time
from app.utils import ensure_aware, utc_now

logger = logging.getLogger(__name__)

CANDIDATE_CONSENT_POLICY_VERSION = "phase10-v1"

# Closed vocabulary of UI-facing channel states (phase 10 contract).
ChannelStateName = Literal[
    "not_connected", "pending_confirmation", "allowed", "denied", "temporarily_unavailable"
]

EMAIL_CHANNEL = "email"
TELEGRAM_CHANNEL = "telegram"
_CANDIDATE_CHANNELS = (EMAIL_CHANNEL, TELEGRAM_CHANNEL)

# Reasons for a channel not being usable, shown to the HR (no PII).
REASON_NO_ADDRESS = "no_address"
REASON_CHANNEL_NOT_CONFIGURED = "channel_not_configured"
REASON_TEMPORARY_ERROR = "temporary_error"

# Delivery skip/cancel classes used by the worker (safe, PII-free).
CLASS_CHANNEL_NOT_CONFIGURED = "channel_not_configured"
CLASS_CONSENT_REVOKED = "consent_revoked"
CLASS_CONSENT_MISSING = "consent_missing"
CLASS_RECIPIENT_MISSING = "recipient_missing"
CLASS_RECIPIENT_CHANGED = "recipient_changed"
CLASS_CANDIDATE_UNAVAILABLE = "candidate_unavailable"
CLASS_EVENT_STALE = "event_stale"
CLASS_TOKEN_STALE = "token_stale"

# A binding whose last failure is temporary and recent reports
# «temporarily unavailable» instead of «allowed».
TEMP_ERROR_WINDOW = timedelta(hours=1)
_TELEGRAM_TEMP_CLASSES = frozenset(
    {
        "telegram_rate_limited",
        "telegram_timeout",
        "telegram_network",
        "telegram_server",
        "telegram_bad_response",
        "telegram_unauthorized",
        "transport_error",
    }
)
_SMTP_TEMP_CLASSES = frozenset({"smtp_temporary", "smtp_timeout", "smtp_network", "smtp_auth"})


# --- Small sanitizers ---------------------------------------------------------


def sanitize_single_line(value: str, *, max_length: int) -> str:
    """One-line safe text: control characters become spaces, then trimmed.

    Prevents newline/header-injection style tricks inside message bodies
    without altering ordinary Cyrillic text. Callers slice to ``max_length``.
    """
    cleaned = "".join(ch if ch >= " " else " " for ch in value)
    return cleaned.strip()[:max_length]


def _position_clause(position: str) -> str:
    if position:
        return f" на позицию «{sanitize_single_line(position, max_length=200)}»"
    return ""


def _format_when(starts_at: datetime, timezone: str) -> str:
    """«12.09.2026 в 14:00» in the configured default timezone."""
    return f"{format_local(ensure_aware(starts_at), timezone)}"


def _doc_lines(documents: list[str]) -> str:
    if not documents:
        return ""
    items = [f"• {sanitize_single_line(doc, max_length=200)}" for doc in documents]
    return "\n".join(items)


def compose_message_text(
    message_type: CandidateMessageType,
    *,
    candidate_full_name: str,
    position: str = "",
    starts_at: datetime | None = None,
    location: str | None = None,
    documents: list[str] | None = None,
    timezone: str = "UTC",
    confirm_url: str | None = None,
    revoke_url: str | None = None,
) -> tuple[str, str]:
    """Compose (title/subject, exact body) from server-side data only.

    Placeholders are substituted by the server; user-provided content
    (name, position, venue, document names) is sanitized to single lines so
    nobody can smuggle extra lines into the letter. Returns the pair stored
    verbatim in the immutable history.
    """
    name = sanitize_single_line(candidate_full_name, max_length=200) or "Кандидат"
    revoke_footer = (
        "\n\nВы получаете это сообщение, потому что согласились на сообщения "
        f"от HR Manager. Отказаться от сообщений: {revoke_url}"
        if revoke_url
        else ""
    )

    if message_type == CandidateMessageType.INTERVIEW_SCHEDULED:
        assert starts_at is not None
        when = _format_when(starts_at, timezone)
        place = (
            f"\nМесто проведения: {sanitize_single_line(location or '', max_length=200)}"
            if location
            else ""
        )
        return (
            "Собеседование назначено",
            f"Здравствуйте, {name}!\n\n"
            f"Вам назначено собеседование{_position_clause(position)} на {when}.{place}\n"
            "Пожалуйста, приходите немного заранее и возьмите с собой документы, "
            "подтверждающие личность."
            f"{revoke_footer}",
        )
    if message_type == CandidateMessageType.INTERVIEW_REMINDER:
        assert starts_at is not None
        when = _format_when(starts_at, timezone)
        place = (
            f"\nМесто проведения: {sanitize_single_line(location or '', max_length=200)}"
            if location
            else ""
        )
        return (
            "Напоминание о собеседовании",
            f"Здравствуйте, {name}!\n\n"
            f"Напоминаем, что ваше собеседование{_position_clause(position)} "
            f"состоится {when}.{place}"
            f"{revoke_footer}",
        )
    if message_type == CandidateMessageType.INTERVIEW_RESCHEDULED:
        assert starts_at is not None
        when = _format_when(starts_at, timezone)
        place = (
            f"\nНовое место проведения: {sanitize_single_line(location or '', max_length=200)}"
            if location
            else ""
        )
        return (
            "Собеседование перенесено",
            f"Здравствуйте, {name}!\n\n"
            f"Ваше собеседование{_position_clause(position)} перенесено "
            f"и теперь состоится {when}.{place}\nПриносим извинения за неудобства."
            f"{revoke_footer}",
        )
    if message_type == CandidateMessageType.INTERVIEW_CANCELLED:
        cancelled_when = _format_when(starts_at, timezone) if starts_at is not None else None
        time_part = f", назначенное на {cancelled_when}" if cancelled_when else ""
        return (
            "Собеседование отменено",
            f"Здравствуйте, {name}!\n\n"
            f"Ваше собеседование{_position_clause(position)}{time_part} отменено.\n"
            "Мы свяжемся с вами, когда появится новая информация."
            f"{revoke_footer}",
        )
    if message_type in (
        CandidateMessageType.DOCUMENTS_REQUEST,
        CandidateMessageType.DOCUMENTS_REMINDER,
    ):
        header = (
            "Напоминаем, что мы ждём от вас документы:"
            if message_type == CandidateMessageType.DOCUMENTS_REMINDER
            else "Для продолжения рассмотрения вашей заявки просим предоставить документы:"
        )
        lines = _doc_lines(documents or [])
        body = (
            f"Здравствуйте, {name}!\n\n{header}\n{lines}"
            if lines
            else f"Здравствуйте, {name}!\n\n{header}"
        )
        title = (
            "Напоминание о документах"
            if message_type == CandidateMessageType.DOCUMENTS_REMINDER
            else "Запрос документов"
        )
        return (title, body + revoke_footer)
    if message_type == CandidateMessageType.CONSENT_INVITE:
        assert confirm_url is not None
        return (
            "Подтвердите получение сообщений",
            f"Здравствуйте, {name}!\n\n"
            "HR Manager хочет отправлять вам сообщения о собеседованиях и "
            "необходимых документах на этот адрес электронной почты.\n\n"
            "Если вы согласны получать такие сообщения, подтвердите, пожалуйста, "
            f"согласие по ссылке: {confirm_url}\n\n"
            "Если вы не ожидали этого письма, просто проигнорируйте его — "
            "сообщения отправляться не будут.",
        )
    raise ValueError(f"unsupported message type {message_type}")


# --- Token helpers (hash-only, single-use, expiring) -------------------------


def token_hash(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def new_token() -> str:
    """URL-safe high-entropy raw token; only its hash is ever stored."""
    return secrets.token_urlsafe(32)


def supersede_active_tokens(
    db: Session,
    *,
    candidate_id: UUID,
    channel: str,
    purpose: CandidateChannelPurpose,
    now: datetime,
) -> None:
    """Mark older unconsumed tokens consumed with reason ``superseded``."""
    db.execute(
        update(CandidateChannelToken)
        .where(
            CandidateChannelToken.candidate_id == candidate_id,
            CandidateChannelToken.channel == channel,
            CandidateChannelToken.purpose == purpose,
            CandidateChannelToken.consumed_at.is_(None),
        )
        .values(consumed_at=now, consume_reason="superseded")
    )


def issue_token(
    db: Session,
    *,
    candidate_id: UUID,
    channel: str,
    purpose: CandidateChannelPurpose,
    ttl: timedelta,
    now: datetime,
    raw: str | None = None,
    email_address: str | None = None,
) -> CandidateChannelToken:
    """Create a one-shot expiring token (superseding older ones)."""
    raw = raw or new_token()
    supersede_active_tokens(
        db, candidate_id=candidate_id, channel=channel, purpose=purpose, now=now
    )
    row = CandidateChannelToken(
        candidate_id=candidate_id,
        channel=channel,
        purpose=purpose,
        token_hash=token_hash(raw),
        created_at=now,
        expires_at=now + ttl,
        email_address=email_address,
    )
    db.add(row)
    db.flush()
    return row


def active_token(
    db: Session,
    *,
    candidate_id: UUID,
    channel: str,
    purpose: CandidateChannelPurpose,
    now: datetime,
) -> CandidateChannelToken | None:
    return (
        db.execute(
            select(CandidateChannelToken)
            .where(
                CandidateChannelToken.candidate_id == candidate_id,
                CandidateChannelToken.channel == channel,
                CandidateChannelToken.purpose == purpose,
                CandidateChannelToken.consumed_at.is_(None),
                CandidateChannelToken.expires_at > now,
            )
            .order_by(CandidateChannelToken.created_at.desc())
        )
        .scalars()
        .first()
    )


def find_token_by_hash(db: Session, raw: str) -> CandidateChannelToken | None:
    return (
        db.execute(
            select(CandidateChannelToken).where(CandidateChannelToken.token_hash == token_hash(raw))
        )
        .scalars()
        .first()
    )


def consume_token(
    db: Session,
    token: CandidateChannelToken,
    *,
    reason: str,
    now: datetime,
    chat_id: int | None = None,
) -> bool:
    """Atomic single-use consumption. Returns False when already consumed."""
    if token.consumed_at is not None or token.expires_at <= now:
        return False
    token.consumed_at = now
    token.consume_reason = reason
    if chat_id is not None:
        token.consume_chat_id = chat_id
    return True


# --- Stateless revoke link (HMAC-signed, no stored secret tokens) -----------


def _revoke_hmac(secret_key: str, *, candidate_id: UUID, channel: str) -> str:
    message = f"{candidate_id}:{channel}".encode()
    return hmac.new(secret_key.encode("utf-8"), message, hashlib.sha256).hexdigest()


def build_revoke_url(base_url: str, *, candidate_id: UUID, channel: str, secret_key: str) -> str:
    """Public unsubscribe URL: /public/candidates/revoke/... (stateless)."""
    signature = _revoke_hmac(secret_key, candidate_id=candidate_id, channel=channel)
    return f"{base_url}public/candidates/revoke/{candidate_id}/{channel}?sig={signature}"


def verify_revoke_url(candidate_id: UUID, channel: str, signature: str, secret_key: str) -> bool:
    expected = _revoke_hmac(secret_key, candidate_id=candidate_id, channel=channel)
    return hmac.compare_digest(expected, signature)


# --- Recipient resolution and channel state ----------------------------------


def _smtp_configured(settings: Settings) -> bool:
    from app.smtp import config_from_settings

    return config_from_settings(settings).is_configured


def _telegram_configured(settings: Settings) -> bool:
    from app.telegram import config_from_settings

    return config_from_settings(settings).is_configured


def _recent_temp_error(channel: str, row: CandidateContactChannel | None, now: datetime) -> bool:
    if row is None or row.last_error_class is None or row.last_error_at is None:
        return False
    if row.last_error_class not in (
        _TELEGRAM_TEMP_CLASSES if channel == TELEGRAM_CHANNEL else _SMTP_TEMP_CLASSES
    ):
        return False
    return (now - row.last_error_at) <= TEMP_ERROR_WINDOW


def channel_state(
    db: Session,
    candidate: Candidate,
    channel: str,
    *,
    settings: Settings,
    now: datetime | None = None,
) -> tuple[ChannelStateName, str | None]:
    """Five-state channel report (state name, PII-free reason)."""
    now = now or utc_now()
    row = (
        db.execute(
            select(CandidateContactChannel).where(
                CandidateContactChannel.candidate_id == candidate.id,
                CandidateContactChannel.channel == channel,
            )
        )
        .scalars()
        .first()
    )

    if channel == EMAIL_CHANNEL:
        configured = _smtp_configured(settings)
        if row is not None and row.revoked_at is not None and not row.consent_granted:
            return "denied", None
        if row is not None and row.consent_granted and row.email_address:
            if not configured:
                return "temporarily_unavailable", REASON_CHANNEL_NOT_CONFIGURED
            if _recent_temp_error(channel, row, now):
                return "temporarily_unavailable", REASON_TEMPORARY_ERROR
            return "allowed", None
        pending = active_token(
            db,
            candidate_id=candidate.id,
            channel=EMAIL_CHANNEL,
            purpose=CandidateChannelPurpose.EMAIL_CONSENT,
            now=now,
        )
        if pending is not None:
            return "pending_confirmation", None
        return "not_connected", REASON_NO_ADDRESS if not candidate.email else None

    # Telegram
    configured = _telegram_configured(settings)
    if row is not None and row.revoked_at is not None and not row.consent_granted:
        return "denied", None
    if row is not None and row.consent_granted and row.chat_id is not None:
        if not configured:
            return "temporarily_unavailable", REASON_CHANNEL_NOT_CONFIGURED
        if _recent_temp_error(channel, row, now):
            return "temporarily_unavailable", REASON_TEMPORARY_ERROR
        return "allowed", None
    pending = active_token(
        db,
        candidate_id=candidate.id,
        channel=TELEGRAM_CHANNEL,
        purpose=CandidateChannelPurpose.TELEGRAM_LINK,
        now=now,
    )
    if pending is not None:
        return "pending_confirmation", None
    return "not_connected", None


def allowed_candidate_channels(
    db: Session, candidate: Candidate, *, settings: Settings, now: datetime | None = None
) -> list[str]:
    """Channels that may receive a NEW message right now (fail-closed).

    Only channels with a current explicit consent AND a valid server-side
    recipient AND an enabled provider configuration are listed. The worker
    re-validates everything again at send time.
    """
    now = now or utc_now()
    channels: list[str] = []
    if _smtp_configured(settings):
        row = (
            db.execute(
                select(CandidateContactChannel).where(
                    CandidateContactChannel.candidate_id == candidate.id,
                    CandidateContactChannel.channel == EMAIL_CHANNEL,
                )
            )
            .scalars()
            .first()
        )
        if row is not None and row.is_allowed:
            channels.append(EMAIL_CHANNEL)
    if _telegram_configured(settings):
        row = (
            db.execute(
                select(CandidateContactChannel).where(
                    CandidateContactChannel.candidate_id == candidate.id,
                    CandidateContactChannel.channel == TELEGRAM_CHANNEL,
                )
            )
            .scalars()
            .first()
        )
        if row is not None and row.is_allowed:
            channels.append(TELEGRAM_CHANNEL)
    return channels


def _channel_row_for_send(
    db: Session, candidate_id: UUID, channel: str
) -> CandidateContactChannel | None:
    return (
        db.execute(
            select(CandidateContactChannel).where(
                CandidateContactChannel.candidate_id == candidate_id,
                CandidateContactChannel.channel == channel,
            )
        )
        .scalars()
        .first()
    )


def _recipient_for(
    db: Session, *, candidate: Candidate, channel: str, now: datetime
) -> tuple[str | int | None, str | None]:
    """Current consented recipient of a channel, or the failure class.

    Returns (email/chat_id, None) on success and (None, reason) when the
    message must NOT go out (used by the worker right before the network
    call). Revoked/missing/outdated recipients never send.
    """
    row = _channel_row_for_send(db, candidate.id, channel)
    if row is None:
        return None, CLASS_CONSENT_MISSING
    if not row.consent_granted:
        return None, CLASS_CONSENT_REVOKED if row.revoked_at else CLASS_CONSENT_MISSING
    if channel == EMAIL_CHANNEL:
        if not row.email_address:
            return None, CLASS_RECIPIENT_MISSING
        return row.email_address, None
    if row.chat_id is None:
        return None, CLASS_RECIPIENT_MISSING
    return row.chat_id, None


# --- Scheduling (transactional; called inside the caller's transaction) ------


def schedule_candidate_message(
    db: Session,
    *,
    candidate: Candidate,
    channel: str,
    message_type: CandidateMessageType,
    source: CandidateMessageSource,
    title: str,
    body: str,
    recipient_email: str | None = None,
    recipient_chat_id: int | None = None,
    initiator_user_id: UUID | None = None,
    event_id: UUID | None = None,
    event_version: int | None = None,
    scheduled_at: datetime | None = None,
    idempotency_key: str | None = None,
    consent_snapshot: dict | None = None,
    quiet_hours_bypassed: bool = False,
    now: datetime | None = None,
) -> tuple[CandidateMessage, bool]:
    """Insert one candidate-message row (transactional, deduplicated).

    Returns ``(row, created)``. ``created=False`` ONLY when the same
    idempotency key already exists (a repeated request/worker pass must
    never duplicate). Any other integrity error propagates — it is never
    masked as a deduplication no-op.
    """
    now = now or utc_now()
    if channel == EMAIL_CHANNEL:
        assert recipient_email is not None and recipient_chat_id is None
    else:
        assert recipient_chat_id is not None and recipient_email is None
    row = CandidateMessage(
        candidate_id=candidate.id,
        channel=channel,
        message_type=message_type,
        source=source,
        recipient_email=recipient_email,
        recipient_chat_id=recipient_chat_id,
        title=title,
        body=body,
        template_version=1,
        initiator_user_id=initiator_user_id,
        event_id=event_id,
        event_version=event_version,
        scheduled_at=scheduled_at or now,
        queued_at=now,
        status="queued",
        idempotency_key=idempotency_key,
        consent_snapshot=consent_snapshot,
        quiet_hours_bypassed=quiet_hours_bypassed,
    )
    try:
        with db.begin_nested():
            db.add(row)
            db.flush()
    except IntegrityError as exc:
        if not is_duplicate_key_error(exc):
            raise
        # A repeated request/worker pass with the same idempotency key is a
        # no-op: return the existing row (never a duplicate message).
        existing = existing_by_idempotency_key(db, idempotency_key) if idempotency_key else None
        if existing is not None:
            return existing, False
        raise
    return row, True


def existing_by_idempotency_key(db: Session, key: str) -> CandidateMessage | None:
    return (
        db.execute(select(CandidateMessage).where(CandidateMessage.idempotency_key == key))
        .scalars()
        .first()
    )


def cancel_pending_candidate_messages(
    db: Session,
    *,
    candidate_id: UUID | None = None,
    channel: str | None = None,
    message_type: CandidateMessageType | None = None,
    event_id: UUID | None = None,
    now: datetime | None = None,
) -> int:
    """Cancel queued (not yet claimed) candidate messages by any filter.

    Rows already ``sending`` are left alone (their worker transaction
    re-validates consent/recipient/event just before the network call, so
    a revocation racing the send still wins). Returns the count.
    """
    now = now or utc_now()
    stmt = update(CandidateMessage).where(CandidateMessage.status == "queued")
    if candidate_id is not None:
        stmt = stmt.where(CandidateMessage.candidate_id == candidate_id)
    if channel is not None:
        stmt = stmt.where(CandidateMessage.channel == channel)
    if message_type is not None:
        stmt = stmt.where(CandidateMessage.message_type == message_type)
    if event_id is not None:
        stmt = stmt.where(CandidateMessage.event_id == event_id)
    result = db.execute(stmt.values(status="cancelled", cancelled_at=now))
    cancelled = result.rowcount if result.rowcount is not None else 0  # type: ignore[attr-defined]
    return cancelled


def cancel_for_event(db: Session, *, event_id: UUID, now: datetime | None = None) -> int:
    """Cancel queued candidate messages linked to an event (stale plan)."""
    return cancel_pending_candidate_messages(db, event_id=event_id, now=now)


def _quiet_defaults(settings: Settings) -> NotificationPreference:
    """Synthetic recipient preference for a candidate (system defaults).

    Candidates have no personal timezone/quiet-hours rows; the system
    defaults from the application settings apply (documented contract).
    """
    from app.config import DEFAULT_WORKDAYS

    return NotificationPreference(
        user_id=UUID(int=0),
        timezone=settings.notification_default_timezone,
        quiet_hours_start=settings.notification_quiet_hours_start,
        quiet_hours_end=settings.notification_quiet_hours_end,
        workdays=[int(day) for day in DEFAULT_WORKDAYS.split(",")],
        enabled_types=[],
        enabled_channels=[],
    )


def in_candidate_quiet_period(now: datetime, *, settings: Settings) -> bool:
    pref = _quiet_defaults(settings)
    return in_quiet_period(
        now,
        timezone=pref.timezone,
        quiet_hours_start=pref.quiet_hours_start,
        quiet_hours_end=pref.quiet_hours_end,
    )


def next_allowed_candidate_time(now: datetime, *, settings: Settings) -> datetime:
    pref = _quiet_defaults(settings)
    return next_allowed_time(
        now,
        timezone=pref.timezone,
        quiet_hours_start=pref.quiet_hours_start,
        quiet_hours_end=pref.quiet_hours_end,
    )


def schedule_candidate_event_message(
    db: Session,
    *,
    event: Event,
    message_type: CandidateMessageType,
    settings: Settings,
    initiator_user_id: UUID | None = None,
    scheduled_at: datetime | None = None,
    base_url: str | None = None,
    now: datetime | None = None,
) -> list[CandidateMessage]:
    """Queue one operational interview message on every allowed channel.

    Called from the events router inside the event transaction (phase 10
    automatic sends). Returns the created rows (empty when the candidate has
    no currently allowed channel — the HR can always send manually later).
    A ``base_url`` enables the email unsubscribe footer (built per channel).
    """
    candidate = event.candidate
    if candidate is None or candidate.deleted_at is not None:
        return []
    now = now or utc_now()
    rows: list[CandidateMessage] = []
    for channel in allowed_candidate_channels(db, candidate, settings=settings, now=now):
        revoke_url = None
        if channel == EMAIL_CHANNEL and base_url:
            revoke_url = build_revoke_url(
                base_url,
                candidate_id=candidate.id,
                channel=EMAIL_CHANNEL,
                secret_key=settings.secret_key,
            )
        title, body = compose_message_text(
            message_type,
            candidate_full_name=candidate.full_name,
            position=candidate.position,
            starts_at=ensure_aware(event.starts_at),
            location=getattr(event, "location", None),
            timezone=settings.notification_default_timezone,
            revoke_url=revoke_url,
        )
        # Recipient comes from the CONSENTED channel binding (the address
        # the candidate actually confirmed / the chat they bound), never
        # from the mutable candidate card.
        binding = _channel_row_for_send(db, candidate.id, channel)
        if binding is None or not binding.is_allowed:
            continue
        if channel == EMAIL_CHANNEL:
            recipient_email, recipient_chat_id = binding.email_address, None
        else:
            recipient_email, recipient_chat_id = None, binding.chat_id
        row, created = schedule_candidate_message(
            db,
            candidate=candidate,
            channel=channel,
            message_type=message_type,
            source=CandidateMessageSource.EVENT,
            title=title,
            body=body,
            recipient_email=recipient_email,
            recipient_chat_id=recipient_chat_id,
            initiator_user_id=initiator_user_id,
            event_id=event.id,
            event_version=event.version,
            scheduled_at=scheduled_at,
            idempotency_key=f"{message_type.value}:{event.id}:{event.version}:{channel}",
            consent_snapshot={
                "channel": channel,
                "granted": True,
                "policy_version": CANDIDATE_CONSENT_POLICY_VERSION,
            },
            now=now,
        )
        if created:
            rows.append(row)
    return rows


def schedule_consent_invite(
    db: Session,
    *,
    candidate: Candidate,
    base_url: str,
    settings: Settings,
    initiator_user_id: UUID | None = None,
    now: datetime | None = None,
) -> tuple[CandidateMessage, CandidateChannelToken]:
    """Queue the double-opt-in email to the candidate's card address.

    The letter carries a one-shot consent link; sending it is the explicit
    one-shot action of the HR, so quiet hours are bypassed and the worker
    re-checks the token right before delivery. Raises ValueError when the
    candidate has no valid email address.
    """
    from app.smtp import validate_mailbox

    now = now or utc_now()
    if not candidate.email:
        raise ValueError("у кандидата нет адреса электронной почты")
    address = validate_mailbox(candidate.email, field="email")
    # The raw token is generated here and embedded into the letter body;
    # only its hash is persisted (single use, expiring).
    raw = new_token()
    token = issue_token(
        db,
        candidate_id=candidate.id,
        channel=EMAIL_CHANNEL,
        purpose=CandidateChannelPurpose.EMAIL_CONSENT,
        ttl=timedelta(hours=settings.candidate_consent_token_ttl_hours),
        now=now,
        raw=raw,
        email_address=address,
    )
    confirm_url = f"{base_url}public/candidates/consent/{raw}"
    title, body = compose_message_text(
        CandidateMessageType.CONSENT_INVITE,
        candidate_full_name=candidate.full_name,
        position=candidate.position,
        timezone=settings.notification_default_timezone,
        confirm_url=confirm_url,
    )
    row, _created = schedule_candidate_message(
        db,
        candidate=candidate,
        channel=EMAIL_CHANNEL,
        message_type=CandidateMessageType.CONSENT_INVITE,
        source=CandidateMessageSource.SYSTEM,
        title=title,
        body=body,
        recipient_email=address,
        initiator_user_id=initiator_user_id,
        scheduled_at=now,
        idempotency_key=f"consent-invite:{token.id}",
        consent_snapshot={
            "channel": EMAIL_CHANNEL,
            "granted": False,
            "policy_version": CANDIDATE_CONSENT_POLICY_VERSION,
            "token_hash": token.token_hash,
        },
        quiet_hours_bypassed=True,
        now=now,
    )
    return row, token
