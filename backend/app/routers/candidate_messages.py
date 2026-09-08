"""One-way candidate messages API (phase 10).

Russian one-way messages to candidates (interview scheduled / reminder /
rescheduled / cancelled, document request / reminder) over the existing
transactional outbox and worker. No send ever happens inside the HTTP
request: endpoints only queue rows and render previews.

Server-side authorization:

* unauthenticated — 401; every mutation is CSRF-protected by the session
  dependency (double-submit), like all other routers;
* an HR works only with their own candidates (foreign → 404, no existence
  leak);
* a manager (head of the responsible area) sees every candidate's messages;
* an administrator does NOT get candidate messages just for the role —
  only with an explicit active ``pilot_full_access`` grant (403 otherwise,
  the card itself stays accessible as before);
* soft-deleted candidates are 404 for everyone.

Anti-abuse: per-user sliding-window rate limits on manual sends and the
Telegram invite/confirm actions; a duplicate guard refuses queueing the
same message type for the same candidate/event while one is still pending.

Privacy: audit details carry only channel/type/event identifiers — never
the message text, the address or the chat id. Masked targets are returned
instead of exact ones wherever the exact value is not strictly needed.
"""

from __future__ import annotations

import hashlib
import re
import secrets
from collections.abc import Callable
from datetime import datetime, timedelta
from typing import TYPE_CHECKING
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy import delete, func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.audit import record_event
from app.candidate_messages import (
    CANDIDATE_MESSAGE_TYPE_KEYS,
    DOCUMENT_MESSAGE_TYPES,
    INTERVIEW_MESSAGE_TYPES,
    MAX_DOCUMENT_ITEM_LENGTH,
    MAX_DOCUMENT_ITEMS,
    RenderedMessage,
    _active_invite_exists,
    _display_timezone,
    allowed_candidate_channels,
    candidate_channel_state,
    candidate_consent,
    queue_candidate_message_all_channels,
    record_candidate_consent,
    render_candidate_message,
    sanitize_inline,
    telegram_link_for,
)
from app.config import Settings
from app.db import get_db
from app.deps import get_current_user
from app.models import (
    AccessGrant,
    AccessGrantScope,
    AuditAction,
    Candidate,
    CandidateTelegramLink,
    CandidateTelegramLinkToken,
    DeliveryChannel,
    DeliveryStatus,
    Event,
    EventStatus,
    EventType,
    NotificationOutbox,
    NotificationSource,
    NotificationType,
    TelegramLink,
    TelegramPollState,
    TelegramStartEvent,
    User,
    UserRole,
)
from app.notification_service import (
    cancel_pending_candidate_channel_messages,
    format_local,
    is_duplicate_key_error,
)
from app.rate_limiting import SlidingWindowRateLimiter
from app.schemas import (
    CandidateChannelsOut,
    CandidateChannelStateOut,
    CandidateConsentOut,
    CandidateConsentUpdate,
    CandidateMessageCancelOut,
    CandidateMessageListOut,
    CandidateMessageOut,
    CandidateMessagePreviewOut,
    CandidateMessagePreviewRequest,
    CandidateMessageSendOut,
    CandidateMessageSendRequest,
    CandidateTelegramConfirmOut,
    CandidateTelegramInviteOut,
)
from app.utils import client_ip, ensure_aware, user_agent, utc_now

if TYPE_CHECKING:
    from app.telegram import TelegramConfig, TelegramUpdatesResult

router = APIRouter(prefix="/candidates/{candidate_id}", tags=["candidate-messages"])

_MAX_LIST_LIMIT = 100
_DEFAULT_LIST_LIMIT = 50

_BOT_USERNAME_RE = re.compile(r"^[A-Za-z0-9_]{5,32}$")

# --- Access --------------------------------------------------------------------


def _get_candidate_or_404(db: Session, candidate_id: str) -> Candidate:
    try:
        parsed = UUID(candidate_id)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Кандидат не найден."
        ) from None
    candidate = db.get(Candidate, parsed)
    if candidate is None or candidate.deleted_at is not None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Кандидат не найден.")
    return candidate


def _require_message_access(db: Session, user: User, candidate: Candidate) -> None:
    """Who may see/queue candidate messages (server-side, always).

    HR — only their own candidates (404 hides the foreign card).
    Manager — the whole responsible area (all candidates).
    Admin — NOT by role alone: only an explicit active pilot grant opens
    the candidate-communication scope (403 otherwise).
    """
    if user.role == UserRole.HR:
        if candidate.owner_user_id != user.id:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Кандидат не найден.")
        return
    if user.role == UserRole.MANAGER:
        return
    grant = db.execute(
        select(AccessGrant.id).where(
            AccessGrant.user_id == user.id,
            AccessGrant.scope == AccessGrantScope.PILOT_FULL_ACCESS,
            AccessGrant.revoked_at.is_(None),
        )
    ).scalar()
    if grant is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                "Доступ к сообщениям кандидатов не входит в роль администратора. "
                "Он выдаётся отдельным правилом доступа (пилотный полный доступ)."
            ),
        )


def _accessible_candidate(db: Session, candidate_id: str, user: User) -> Candidate:
    candidate = _get_candidate_or_404(db, candidate_id)
    _require_message_access(db, user, candidate)
    return candidate


# --- Rate limiting (process-local, like the login limiter) ---------------------


_limiters: dict[str, SlidingWindowRateLimiter] = {}
_limiter_shape: tuple[int, int] | None = None


def _limiter_for(action: str, settings: Settings) -> SlidingWindowRateLimiter:
    global _limiter_shape
    shape = (settings.candidate_message_rate_limit, settings.candidate_message_rate_window_s)
    if _limiter_shape != shape:
        _limiters.clear()
        _limiter_shape = shape
    limiter = _limiters.get(action)
    if limiter is None:
        limiter = SlidingWindowRateLimiter(limit=shape[0], window_seconds=shape[1])
        _limiters[action] = limiter
    return limiter


def reset_candidate_message_limiters() -> None:
    """Clear all candidate-message rate counters (used between tests)."""
    for limiter in _limiters.values():
        limiter.reset()


def _enforce_rate_limit(action: str, user: User, settings: Settings) -> None:
    result = _limiter_for(action, settings).check(f"{action}:{user.id}")
    if not result.allowed:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Слишком много операций. Повторите позже.",
            headers={"Retry-After": str(result.retry_after_seconds)},
        )


# --- Small helpers ---------------------------------------------------------------


def mask_email(address: str) -> str:
    local, _, domain = address.partition("@")
    head = local[:1] if local else ""
    return f"{head}***@{domain}" if domain else "***"


def mask_chat_id(chat_id: int) -> str:
    digits = "".join(ch for ch in str(abs(chat_id)) if ch.isdigit())
    return "••••" + (digits[-4:] if len(digits) >= 4 else digits)


def _channel_configured(settings: Settings, channel: DeliveryChannel) -> bool:
    if channel == DeliveryChannel.EMAIL:
        from app.smtp import config_from_settings as smtp_config_from_settings

        return smtp_config_from_settings(settings).is_configured
    from app.telegram import config_from_settings as telegram_config_from_settings

    return telegram_config_from_settings(settings).is_configured


def _channel_state_out(
    db: Session, *, candidate: Candidate, channel: DeliveryChannel, settings: Settings
) -> CandidateChannelStateOut:
    state = candidate_channel_state(db, candidate=candidate, channel=channel, settings=settings)
    now = utc_now()
    consent = candidate_consent(db, candidate.id, channel)
    target_masked: str | None = None
    has_target = False
    invite_active = False
    if channel == DeliveryChannel.EMAIL:
        if candidate.email:
            has_target = True
            target_masked = mask_email(candidate.email)
    else:
        link = telegram_link_for(db, candidate.id)
        if link is not None and link.chat_id is not None:
            has_target = True
            target_masked = mask_chat_id(link.chat_id)
        invite_active = _active_invite_exists(db, candidate.id, now)
    configured = _channel_configured(settings, channel)
    return CandidateChannelStateOut(
        channel=channel.value,
        state=state,
        configured=configured,
        target_masked=target_masked,
        has_target=has_target,
        invite_active=invite_active,
        consent=(
            CandidateConsentOut(
                channel=channel.value,
                granted=consent.granted,
                granted_at=consent.granted_at,
                source=consent.source,
                policy_version=consent.policy_version,
            )
            if consent is not None
            else None
        ),
    )


def _audit(
    db: Session,
    request: Request,
    action: AuditAction,
    *,
    actor: User,
    candidate: Candidate,
    details: str,
    commit: bool = True,
) -> None:
    record_event(
        db,
        action,
        actor=actor,
        candidate_id=candidate.id,
        ip_address=client_ip(request),
        user_agent=user_agent(request.headers),
        details=details,
        commit=commit,
    )


def _message_type_or_422(message_type: str) -> str:
    if message_type not in CANDIDATE_MESSAGE_TYPE_KEYS:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Неизвестный тип сообщения.",
        )
    return message_type


def _channel_or_422(channel: str | None) -> DeliveryChannel | None:
    if channel is None:
        return None
    try:
        return DeliveryChannel(channel)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Неизвестный канал.",
        ) from None


def _validate_send_payload(
    db: Session, candidate: Candidate, payload: CandidateMessageSendRequest
) -> tuple[str, Event | None, list[str]]:
    """Validate type/event/documents; returns (type_key, event, documents)."""
    message_type = _message_type_or_422(payload.message_type)

    event: Event | None = None
    if message_type in INTERVIEW_MESSAGE_TYPES:
        if payload.event_id is None:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Для сообщений о собеседовании укажите событие.",
            )
        event = db.get(Event, payload.event_id)
        if event is None or event.candidate_id != candidate.id or event.type != EventType.INTERVIEW:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Событие не найдено или не является собеседованием этого кандидата.",
            )
        if message_type == "interview_reminder":
            if event.status != EventStatus.SCHEDULED:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail="Напоминание можно отправить только по запланированному собеседованию.",
                )
            if ensure_aware(event.starts_at) <= utc_now():
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail="Собеседование уже началось — напоминание неактуально.",
                )

    documents: list[str] = []
    if message_type in DOCUMENT_MESSAGE_TYPES:
        if not payload.documents:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Укажите хотя бы один документ.",
            )
        if len(payload.documents) > MAX_DOCUMENT_ITEMS:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"Не больше {MAX_DOCUMENT_ITEMS} документов в одном сообщении.",
            )
        for item in payload.documents:
            if not item or not item.strip():
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail="Пустая строка в списке документов.",
                )
            if len(item) > MAX_DOCUMENT_ITEM_LENGTH:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail=f"Каждый документ — не больше {MAX_DOCUMENT_ITEM_LENGTH} символов.",
                )
        documents = [item.strip() for item in payload.documents]
    elif payload.documents:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Список документов применяется только к сообщениям о документах.",
        )

    if message_type not in INTERVIEW_MESSAGE_TYPES and payload.location:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Место указывается только для сообщений о собеседовании.",
        )
    return message_type, event, documents


def _render_for(
    db: Session,
    candidate: Candidate,
    settings: Settings,
    message_type: str,
    event: Event | None,
    documents: list[str],
    location: str | None,
) -> RenderedMessage:
    starts_local = None
    if event is not None:
        timezone = _display_timezone(db, candidate, settings)
        starts_local = format_local(event.starts_at, timezone)
    return render_candidate_message(
        message_type,
        candidate=candidate,
        starts_at_local=starts_local,
        previous_starts_at_local=None,
        location=sanitize_inline(location, max_length=300) if location else None,
        documents=documents or None,
    )


def _to_message_out(row: NotificationOutbox) -> CandidateMessageOut:
    return CandidateMessageOut(
        id=row.id,
        message_type=row.notification_type.value,
        channel=row.channel.value,
        status=row.status.value,
        source=row.source.value,
        title=row.title,
        body=row.body,
        event_id=row.object_id if row.object_type == "event" else None,
        scheduled_at=row.scheduled_at,
        scheduled_at_effective=row.scheduled_at_effective,
        queued_at=row.queued_at,
        accepted_at=row.accepted_at,
        delivered_at=row.delivered_at,
        failed_at=row.failed_at,
        cancelled_at=row.cancelled_at,
        attempts=row.attempts,
        next_attempt_at=row.next_attempt_at,
        error_code=row.error_code,
        error_class=row.error_class,
        provider_message_id=row.provider_message_id,
    )


# --- Channels -------------------------------------------------------------------


@router.get(
    "/channels",
    response_model=CandidateChannelsOut,
    summary="Candidate channel consent states",
)
def get_candidate_channels(
    candidate_id: str,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> CandidateChannelsOut:
    """Consent state of both channels plus the send-eligible list.

    States: ``not_connected`` (no address/binding or the channel is not
    configured globally), ``pending`` (waiting for the candidate's Start or
    a recorded consent), ``allowed``, ``forbidden`` (explicit refusal or a
    revoked binding), ``temporarily_unavailable`` (recent temporary
    provider error). Targets are masked.
    """
    candidate = _accessible_candidate(db, candidate_id, user)
    settings: Settings = request.app.state.settings
    email_out = _channel_state_out(
        db, candidate=candidate, channel=DeliveryChannel.EMAIL, settings=settings
    )
    telegram_out = _channel_state_out(
        db, candidate=candidate, channel=DeliveryChannel.TELEGRAM, settings=settings
    )
    allowed = allowed_candidate_channels(db, candidate=candidate, settings=settings)
    return CandidateChannelsOut(
        email=email_out,
        telegram=telegram_out,
        allowed_channels=[channel.value for channel in allowed],
    )


@router.post(
    "/channels/email/consent",
    response_model=CandidateConsentOut,
    summary="Record or revoke the candidate's email consent",
)
def set_email_consent(
    candidate_id: str,
    payload: CandidateConsentUpdate,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> CandidateConsentOut:
    """Record (or revoke) that the candidate agreed to receive email.

    The consent is pinned to the current address from the candidate card
    (the only allowed address); a later change of the address re-opens the
    confirmation. Revocation stops every pending email message of this
    candidate. The worker re-validates everything at send time.
    """
    candidate = _accessible_candidate(db, candidate_id, user)
    if payload.granted and not candidate.email:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="У кандидата не указан адрес электронной почты.",
        )
    consent = record_candidate_consent(
        db,
        candidate=candidate,
        channel=DeliveryChannel.EMAIL,
        granted=payload.granted,
        granted_by_user_id=user.id,
        source="hr_recorded",
        commit=False,
    )
    _audit(
        db,
        request,
        AuditAction.CANDIDATE_CHANNEL_CONSENT_UPDATED,
        actor=user,
        candidate=candidate,
        details=f"channel=email granted={payload.granted}",
        commit=False,
    )
    db.commit()
    return CandidateConsentOut(
        channel=consent.channel,
        granted=consent.granted,
        granted_at=consent.granted_at,
        source=consent.source,
        policy_version=consent.policy_version,
    )


@router.post(
    "/channels/telegram/consent",
    response_model=CandidateConsentOut,
    summary="Record or revoke the candidate's Telegram consent",
)
def set_telegram_consent(
    candidate_id: str,
    payload: CandidateConsentUpdate,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> CandidateConsentOut:
    """Record (or revoke) the candidate's Telegram consent on HR's behalf.

    The candidate's own voluntary /start records the consent automatically
    (source ``telegram_start``); this endpoint covers decisions the HR
    records explicitly. Revocation stops every pending Telegram message.
    """
    candidate = _accessible_candidate(db, candidate_id, user)
    consent = record_candidate_consent(
        db,
        candidate=candidate,
        channel=DeliveryChannel.TELEGRAM,
        granted=payload.granted,
        granted_by_user_id=user.id,
        source="hr_recorded",
        commit=False,
    )
    _audit(
        db,
        request,
        AuditAction.CANDIDATE_CHANNEL_CONSENT_UPDATED,
        actor=user,
        candidate=candidate,
        details=f"channel=telegram granted={payload.granted}",
        commit=False,
    )
    db.commit()
    return CandidateConsentOut(
        channel=consent.channel,
        granted=consent.granted,
        granted_at=consent.granted_at,
        source=consent.source,
        policy_version=consent.policy_version,
    )


# --- Telegram invitation flow -----------------------------------------------------


def _token_hash(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _telegram_config(settings: Settings) -> TelegramConfig:
    from app.telegram import config_from_settings

    return config_from_settings(settings)


def _active_invite(
    db: Session, candidate_id: UUID, now: datetime
) -> CandidateTelegramLinkToken | None:
    return (
        db.execute(
            select(CandidateTelegramLinkToken)
            .where(
                CandidateTelegramLinkToken.candidate_id == candidate_id,
                CandidateTelegramLinkToken.consumed_at.is_(None),
                CandidateTelegramLinkToken.expires_at > now,
            )
            .order_by(CandidateTelegramLinkToken.created_at.desc())
        )
        .scalars()
        .first()
    )


@router.post(
    "/channels/telegram/invite",
    response_model=CandidateTelegramInviteOut,
    status_code=status.HTTP_201_CREATED,
    summary="Create a one-shot Telegram invitation for the candidate",
)
def create_telegram_invite(
    candidate_id: str,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> CandidateTelegramInviteOut:
    """Create a one-shot invitation deep link for this candidate.

    The raw token is shown once (only its SHA-256 hash is stored). The HR
    passes the link to the candidate outside the system; the channel stays
    pending until the candidate voluntarily opens the bot and presses
    Start. Older unconsumed invitations are superseded.
    """
    candidate = _accessible_candidate(db, candidate_id, user)
    settings: Settings = request.app.state.settings
    _enforce_rate_limit("candidate-telegram-invite", user, settings)
    config = _telegram_config(settings)
    if not config.is_configured:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Telegram-канал не настроен. Обратитесь к администратору.",
        )
    bot_username = settings.telegram_bot_username.strip().lstrip("@")
    if not _BOT_USERNAME_RE.match(bot_username):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Telegram-канал настроен неполностью (нет имени бота).",
        )
    now = utc_now()
    # One active invitation per candidate (supersede older ones).
    db.execute(
        update(CandidateTelegramLinkToken)
        .where(
            CandidateTelegramLinkToken.candidate_id == candidate.id,
            CandidateTelegramLinkToken.consumed_at.is_(None),
        )
        .values(consumed_at=now, consume_reason="superseded")
    )
    raw_token = secrets.token_urlsafe(32)
    expires_at = now + timedelta(minutes=settings.candidate_telegram_link_ttl_minutes)
    db.add(
        CandidateTelegramLinkToken(
            candidate_id=candidate.id,
            token_hash=_token_hash(raw_token),
            created_at=now,
            expires_at=expires_at,
        )
    )
    _audit(
        db,
        request,
        AuditAction.CANDIDATE_TELEGRAM_INVITE_CREATED,
        actor=user,
        candidate=candidate,
        details="channel=telegram",
        commit=False,
    )
    db.commit()
    return CandidateTelegramInviteOut(
        deep_link=f"https://t.me/{bot_username}?start={raw_token}", expires_at=expires_at
    )


def _poll_state_for_update(db: Session) -> TelegramPollState:
    """Load the singleton poll offset under a row lock, creating it once."""
    state = db.execute(
        select(TelegramPollState).where(TelegramPollState.id == 1).with_for_update()
    ).scalar_one_or_none()
    if state is not None:
        return state
    fresh = TelegramPollState(id=1, last_update_id=None, updated_at=utc_now())
    try:
        with db.begin_nested():
            db.add(fresh)
            db.flush()
    except IntegrityError as exc:
        if not is_duplicate_key_error(exc):
            raise
        state = db.execute(
            select(TelegramPollState).where(TelegramPollState.id == 1).with_for_update()
        ).scalar_one()
        return state
    return fresh


# Test hook: the poll callable (unit tests monkeypatch it).
_poll_starts_impl: Callable[..., TelegramUpdatesResult] | None = None


def _poll_starts(config: TelegramConfig, *, offset: int | None) -> TelegramUpdatesResult:
    if _poll_starts_impl is not None:
        return _poll_starts_impl(config, offset=offset)
    from app.telegram import get_start_updates

    return get_start_updates(config, offset=offset)


@router.post(
    "/channels/telegram/confirm",
    response_model=CandidateTelegramConfirmOut,
    summary="Confirm the candidate's Telegram connection",
)
def confirm_telegram_link(
    candidate_id: str,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> CandidateTelegramConfirmOut:
    """Bind the candidate's chat after they pressed Start in the bot.

    Polls the shared bot inbox once (the same getUpdates offset as the
    user flow — every observed /start is persisted, so concurrent confirms
    of different recipients never lose each other's events). Without the
    Start event yet, the invitation stays active and the caller retries
    (409). A chat already active on another recipient fails closed.
    """
    candidate = _accessible_candidate(db, candidate_id, user)
    settings: Settings = request.app.state.settings
    _enforce_rate_limit("candidate-telegram-confirm", user, settings)
    config = _telegram_config(settings)
    if not config.is_configured:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Telegram-канал не настроен. Обратитесь к администратору.",
        )
    now = utc_now()
    token = _active_invite(db, candidate.id, now)
    if token is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Нет активного приглашения. Создайте новое.",
        )
    if token.candidate_id != candidate.id:
        # Defense in depth: the lookup is already candidate-scoped.
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Приглашение принадлежит другому кандидату.",
        )
    state = _poll_state_for_update(db)
    offset = state.last_update_id + 1 if state.last_update_id is not None else None
    poll = _poll_starts(config, offset=offset)
    if not poll.ok:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Telegram временно недоступен. Повторите попытку позже.",
        )
    # Record every observed /start BEFORE advancing the offset (the same
    # contract as the user linking flow).
    for start in poll.updates:
        try:
            with db.begin_nested():
                db.add(
                    TelegramStartEvent(
                        token_hash=_token_hash(start.token),
                        chat_id=start.chat_id,
                        seen_at=now,
                    )
                )
                db.flush()
        except IntegrityError as exc:
            if not is_duplicate_key_error(exc):
                raise
    if poll.max_update_id is not None:
        state.last_update_id = poll.max_update_id
        state.updated_at = now
    db.execute(
        delete(TelegramStartEvent).where(TelegramStartEvent.seen_at < now - timedelta(days=1))
    )
    event = db.get(TelegramStartEvent, token.token_hash)
    if event is None:
        db.commit()  # persist the advanced offset + observed events
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "Мы пока не видим запуск бота с этим кодом. "
                "Передайте ссылку кандидату, дождитесь нажатия «Запустить» и повторите."
            ),
        )
    # One active chat serves one recipient: refuse chats already bound to
    # another candidate or to an internal user (fail closed, no takeover).
    holder_candidate = (
        db.execute(
            select(CandidateTelegramLink.candidate_id).where(
                CandidateTelegramLink.chat_id == event.chat_id,
                CandidateTelegramLink.revoked_at.is_(None),
                CandidateTelegramLink.candidate_id != candidate.id,
            )
        )
        .scalars()
        .first()
    )
    holder_user = (
        db.execute(
            select(TelegramLink.user_id).where(
                TelegramLink.chat_id == event.chat_id,
                TelegramLink.revoked_at.is_(None),
            )
        )
        .scalars()
        .first()
    )
    if holder_candidate is not None or holder_user is not None:
        db.execute(
            delete(TelegramStartEvent).where(TelegramStartEvent.token_hash == token.token_hash)
        )
        _audit(
            db,
            request,
            AuditAction.CANDIDATE_TELEGRAM_LINKED,
            actor=user,
            candidate=candidate,
            details="channel=telegram result=conflict",
            commit=False,
        )
        db.commit()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Этот чат Telegram уже привязан к другому получателю.",
        )
    try:
        consumed_result = db.execute(
            update(CandidateTelegramLinkToken)
            .where(
                CandidateTelegramLinkToken.id == token.id,
                CandidateTelegramLinkToken.consumed_at.is_(None),
            )
            .values(consumed_at=now, consume_reason="linked")
        )
        if (consumed_result.rowcount or 0) != 1:  # type: ignore[attr-defined]
            db.rollback()
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Приглашение уже использовано. Создайте новое.",
            )
        link = telegram_link_for(db, candidate.id)
        if link is None:
            link = CandidateTelegramLink(candidate_id=candidate.id, created_at=now)
            db.add(link)
        link.chat_id = event.chat_id
        link.linked_at = now
        link.revoked_at = None
        link.revoke_reason = None
        link.last_error_class = None
        link.last_error_at = None
        link.updated_at = now
        db.execute(
            delete(TelegramStartEvent).where(TelegramStartEvent.token_hash == token.token_hash)
        )
        # The candidate's voluntary Start IS the consent for Telegram — but
        # an explicit HR refusal is never overwritten by it (fail-closed).
        existing_consent = candidate_consent(db, candidate.id, DeliveryChannel.TELEGRAM)
        if existing_consent is None:
            record_candidate_consent(
                db,
                candidate=candidate,
                channel=DeliveryChannel.TELEGRAM,
                granted=True,
                granted_by_user_id=None,
                source="telegram_start",
                commit=False,
            )
        _audit(
            db,
            request,
            AuditAction.CANDIDATE_TELEGRAM_LINKED,
            actor=user,
            candidate=candidate,
            details="channel=telegram",
            commit=False,
        )
        db.commit()
    except IntegrityError as exc:
        # Lost a same-chat race: the partial unique index is the backstop.
        db.rollback()
        if not is_duplicate_key_error(exc):
            raise
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Этот чат Telegram уже привязан. Повторите с новым приглашением.",
        ) from None
    link_state = candidate_channel_state(
        db, candidate=candidate, channel=DeliveryChannel.TELEGRAM, settings=settings
    )
    return CandidateTelegramConfirmOut(linked=True, state=link_state)


@router.post(
    "/channels/telegram/unlink",
    response_model=CandidateTelegramConfirmOut,
    summary="Unlink the candidate's Telegram",
)
def unlink_telegram(
    candidate_id: str,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> CandidateTelegramConfirmOut:
    """Revoke the candidate's Telegram binding and stop pending sends."""
    candidate = _accessible_candidate(db, candidate_id, user)
    settings: Settings = request.app.state.settings
    link = telegram_link_for(db, candidate.id)
    now = utc_now()
    if link is not None:
        link.revoked_at = now
        link.revoke_reason = "manual"
        link.updated_at = now
    cancel_pending_candidate_channel_messages(
        db, candidate_id=candidate.id, channel=DeliveryChannel.TELEGRAM, now=now
    )
    _audit(
        db,
        request,
        AuditAction.CANDIDATE_TELEGRAM_UNLINKED,
        actor=user,
        candidate=candidate,
        details="channel=telegram",
        commit=False,
    )
    db.commit()
    link_state = candidate_channel_state(
        db, candidate=candidate, channel=DeliveryChannel.TELEGRAM, settings=settings
    )
    return CandidateTelegramConfirmOut(linked=False, state=link_state)


# --- Messages: history, preview, send, cancel --------------------------------------


@router.get(
    "/messages",
    response_model=CandidateMessageListOut,
    summary="Candidate message history",
)
def list_candidate_messages(
    candidate_id: str,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    limit: int = Query(default=_DEFAULT_LIST_LIMIT, ge=1, le=_MAX_LIST_LIMIT),
    offset: int = Query(default=0, ge=0),
) -> CandidateMessageListOut:
    """Immutable history of one-way messages (newest first, paginated)."""
    candidate = _accessible_candidate(db, candidate_id, user)
    filters = [NotificationOutbox.recipient_candidate_id == candidate.id]
    total = db.execute(
        select(func.count()).select_from(NotificationOutbox).where(*filters)
    ).scalar_one()
    rows = (
        db.execute(
            select(NotificationOutbox)
            .where(*filters)
            .order_by(NotificationOutbox.queued_at.desc(), NotificationOutbox.id.desc())
            .limit(limit)
            .offset(offset)
        )
        .scalars()
        .all()
    )
    return CandidateMessageListOut(
        items=[_to_message_out(row) for row in rows],
        total=int(total),
        limit=limit,
        offset=offset,
    )


def _no_allowed_channels_response(channel: DeliveryChannel | None) -> HTTPException:
    if channel is not None:
        return HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "Канал не разрешён для этого кандидата: нужно действующее "
                "согласие и подключенный адрес/чат."
            ),
        )
    return HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail=(
            "Нет разрешённых каналов: нужно согласие кандидата и подключённый "
            "адрес электронной почты или Telegram."
        ),
    )


def _pending_duplicate_exists(
    db: Session, candidate: Candidate, message_type: str, event_id: UUID | None
) -> bool:
    type_value = {
        "interview_scheduled": NotificationType.CANDIDATE_INTERVIEW_SCHEDULED,
        "interview_reminder": NotificationType.CANDIDATE_INTERVIEW_REMINDER,
        "interview_rescheduled": NotificationType.CANDIDATE_INTERVIEW_RESCHEDULED,
        "interview_cancelled": NotificationType.CANDIDATE_INTERVIEW_CANCELLED,
        "document_request": NotificationType.CANDIDATE_DOCUMENT_REQUEST,
        "document_reminder": NotificationType.CANDIDATE_DOCUMENT_REMINDER,
    }[message_type]
    return (
        db.execute(
            select(NotificationOutbox.id)
            .where(
                NotificationOutbox.recipient_candidate_id == candidate.id,
                NotificationOutbox.notification_type == type_value,
                NotificationOutbox.object_id == event_id,
                NotificationOutbox.status.in_([DeliveryStatus.QUEUED, DeliveryStatus.SENDING]),
            )
            .limit(1)
        ).scalar()
        is not None
    )


@router.post(
    "/messages/preview",
    response_model=CandidateMessagePreviewOut,
    summary="Preview a candidate message (nothing is queued)",
)
def preview_candidate_message(
    candidate_id: str,
    payload: CandidateMessagePreviewRequest,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> CandidateMessagePreviewOut:
    """Render the exact text the candidate would receive (no queueing)."""
    candidate = _accessible_candidate(db, candidate_id, user)
    settings: Settings = request.app.state.settings
    message_type, event, documents = _validate_send_payload(db, candidate, payload)
    message = _render_for(db, candidate, settings, message_type, event, documents, payload.location)
    allowed = allowed_candidate_channels(db, candidate=candidate, settings=settings)
    return CandidateMessagePreviewOut(
        title=message.title,
        body=message.body,
        channels=[channel.value for channel in allowed],
    )


@router.post(
    "/messages/send",
    response_model=CandidateMessageSendOut,
    status_code=status.HTTP_201_CREATED,
    summary="Queue a manual candidate message",
)
def send_candidate_message(
    candidate_id: str,
    payload: CandidateMessageSendRequest,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> CandidateMessageSendOut:
    """Queue one manual message for every allowed channel (or one channel).

    The server renders the text, resolves the channels from the CURRENT
    consent state and queues outbox rows — the worker performs the actual
    delivery later (outside any HTTP request), re-validating consent,
    address, candidate state, event freshness and cancellation right
    before the provider call. Duplicate pending messages of the same type
    are refused; the action is rate-limited and audited.
    """
    candidate = _accessible_candidate(db, candidate_id, user)
    settings: Settings = request.app.state.settings
    _enforce_rate_limit("candidate-message-send", user, settings)
    message_type, event, documents = _validate_send_payload(db, candidate, payload)
    channel = _channel_or_422(payload.channel)
    allowed = allowed_candidate_channels(db, candidate=candidate, settings=settings)
    if channel is not None:
        if channel not in allowed:
            raise _no_allowed_channels_response(channel)
    elif not allowed:
        raise _no_allowed_channels_response(None)
    if _pending_duplicate_exists(db, candidate, message_type, event.id if event else None):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Такое сообщение уже ожидает отправки этому кандидату.",
        )
    message = _render_for(db, candidate, settings, message_type, event, documents, payload.location)
    now = utc_now()
    request_key = secrets.token_urlsafe(12)
    rows = queue_candidate_message_all_channels(
        db,
        candidate=candidate,
        message=message,
        message_type_key=message_type,
        source=NotificationSource.MANUAL,
        event_id=event.id if event else None,
        initiator_user_id=user.id,
        dedupe_key=f"manual:{request_key}",
        scheduled_at=now,
        settings=settings,
        only_channel=channel,
    )
    _audit(
        db,
        request,
        AuditAction.CANDIDATE_MESSAGE_QUEUED,
        actor=user,
        candidate=candidate,
        details=(
            f"type={message_type} channels={','.join(r.channel.value for r in rows)}"
            + (f" event={event.id}" if event is not None else "")
        ),
        commit=False,
    )
    db.commit()
    for row in rows:
        db.refresh(row)
    return CandidateMessageSendOut(
        messages=[_to_message_out(row) for row in rows],
        channels=[row.channel.value for row in rows],
    )


@router.post(
    "/messages/{message_id}/cancel",
    response_model=CandidateMessageCancelOut,
    summary="Cancel a pending candidate message",
)
def cancel_candidate_message(
    candidate_id: str,
    message_id: str,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> CandidateMessageCancelOut:
    """Cancel a still-queued message of this candidate.

    A message already being sent cannot be cancelled this way (the worker
    may have handed it to the provider); terminal statuses are immutable —
    both cases return 409.
    """
    candidate = _accessible_candidate(db, candidate_id, user)
    try:
        parsed = UUID(message_id)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Сообщение не найдено."
        ) from None
    row = db.get(NotificationOutbox, parsed)
    if row is None or row.recipient_candidate_id != candidate.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Сообщение не найдено.")
    if row.status != DeliveryStatus.QUEUED:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Отправка уже выполняется или завершена — отмена невозможна.",
        )
    row.status = DeliveryStatus.CANCELLED
    row.cancelled_at = utc_now()
    _audit(
        db,
        request,
        AuditAction.CANDIDATE_MESSAGE_CANCELLED,
        actor=user,
        candidate=candidate,
        details=f"channel={row.channel.value} type={row.notification_type.value}",
        commit=False,
    )
    db.commit()
    return CandidateMessageCancelOut(id=row.id, status=row.status.value)
