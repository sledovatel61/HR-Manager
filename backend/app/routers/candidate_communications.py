"""Candidate communications API (phase 10).

Endpoints under ``/candidates/{candidate_id}/communications/*`` let an HR
(or manager/admin, exactly per the candidate visibility rules) manage a
candidate's per-channel consent, preview/send the six Russian operational
message kinds and view the immutable delivery history. Everything is
server-side: the recipient (consented email address / voluntarily bound
Telegram chat), the message text and the subject are derived by the server,
never taken from the client payload.

Public endpoints under ``/public/candidates/*`` serve the candidate-facing
double-opt-in link (email consent) and the stateless unsubscribe link. They
are unauthenticated by design (a candidate has no account) and protected by
one-shot high-entropy tokens / an HMAC signature.

Every mutating session endpoint is CSRF-protected by the shared session
dependency and audited. The interface is not a security boundary — all
checks are repeated on the server (and again by the worker at send time).
"""

import re
from datetime import datetime, timedelta
from typing import Literal, cast
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import HTMLResponse
from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.audit import record_event
from app.candidate_communications import (
    CANDIDATE_CONSENT_POLICY_VERSION,
    EMAIL_CHANNEL,
    TELEGRAM_CHANNEL,
    active_token,
    build_revoke_url,
    cancel_pending_candidate_messages,
    channel_state,
    compose_message_text,
    consume_token,
    existing_by_idempotency_key,
    find_token_by_hash,
    in_candidate_quiet_period,
    issue_token,
    new_token,
    next_allowed_candidate_time,
    schedule_candidate_message,
    schedule_consent_invite,
    token_hash,
    verify_revoke_url,
)
from app.config import Settings
from app.db import get_db
from app.deps import get_current_user
from app.models import (
    AuditAction,
    Candidate,
    CandidateChannelPurpose,
    CandidateContactChannel,
    CandidateMessage,
    CandidateMessageSource,
    CandidateMessageType,
    Event,
    EventStatus,
    EventType,
    TelegramPollState,
    TelegramStartEvent,
    User,
    UserRole,
)
from app.notification_service import is_duplicate_key_error
from app.rate_limiting import SlidingWindowRateLimiter
from app.schemas import (
    CandidateChannelsOut,
    CandidateChannelStatusOut,
    CandidateConsentRequestOut,
    CandidateMessageCancelOut,
    CandidateMessageList,
    CandidateMessageOut,
    CandidateMessagePreviewOut,
    CandidateMessageSendOut,
    CandidateMessageSendRequest,
    CandidateRevokeOut,
    CandidateTelegramConfirmOut,
    CandidateTelegramLinkOut,
)
from app.smtp import config_from_settings as smtp_config_from_settings
from app.telegram import config_from_settings as telegram_config_from_settings
from app.telegram import get_start_updates
from app.utils import client_ip, ensure_aware, utc_now

router = APIRouter(prefix="/candidates", tags=["candidate-communications"])
public_router = APIRouter(prefix="/public/candidates", tags=["public"])

_MAX_LIST_LIMIT = 100
_DEFAULT_LIST_LIMIT = 30
_BOT_USERNAME_RE = re.compile(r"^[A-Za-z0-9_]{5,32}$")

# Module-level rate-limiters: per HR user per action, plus a per-IP budget
# for the candidate-facing public endpoints.
_limiters: dict[str, SlidingWindowRateLimiter] = {}


def _limiter_for(
    scope: str, settings: Settings, *, public: bool = False
) -> SlidingWindowRateLimiter:
    if public:
        limit, window = 30, 300
    else:
        limit = settings.candidate_message_rate_limit
        window = settings.candidate_message_rate_window_s
    key = f"{'public' if public else 'user'}:{scope}:{limit}:{window}"
    limiter = _limiters.get(key)
    if limiter is None:
        limiter = SlidingWindowRateLimiter(limit=limit, window_seconds=window)
        _limiters[key] = limiter
    return limiter


def reset_candidate_limiters() -> None:
    """Clear all candidate-communication rate counters (used between tests)."""
    for limiter in _limiters.values():
        limiter.reset()


def _enforce_user_rate_limit(action: str, user: User, settings: Settings) -> None:
    result = _limiter_for(action, settings).check(f"{action}:{user.id}")
    if not result.allowed:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Слишком много операций. Повторите позже.",
            headers={"Retry-After": str(result.retry_after_seconds)},
        )


def _enforce_public_rate_limit(action: str, request: Request, settings: Settings) -> None:
    ip = client_ip(request) or "unknown"
    result = _limiter_for(action, settings, public=True).check(f"{action}:{ip}")
    if not result.allowed:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Слишком много запросов. Повторите позже.",
            headers={"Retry-After": str(result.retry_after_seconds)},
        )


# --- Small helpers ------------------------------------------------------------


def _visible_candidate(db: Session, candidate_id: UUID, user: User) -> Candidate:
    candidate = db.get(Candidate, candidate_id)
    if candidate is None or candidate.deleted_at is not None:
        raise HTTPException(status_code=404, detail="Кандидат не найден.")
    if user.role == UserRole.HR and candidate.owner_user_id != user.id:
        raise HTTPException(status_code=404, detail="Кандидат не найден.")
    return candidate


def mask_recipient(
    channel: str, *, email: str | None = None, chat_id: int | None = None
) -> str | None:
    """Mask a recipient so raw PII never reaches the frontend/logs."""
    if channel == EMAIL_CHANNEL and email:
        local, _, domain = email.partition("@")
        head = local[:1] if local else ""
        return f"{head}***@{domain}" if domain else "***"
    if channel == TELEGRAM_CHANNEL and chat_id is not None:
        digits = "".join(ch for ch in str(abs(chat_id)) if ch.isdigit())
        return "••••" + (digits[-4:] if len(digits) >= 4 else digits)
    return None


def _message_out(row: CandidateMessage) -> CandidateMessageOut:
    out = CandidateMessageOut.model_validate(row)
    out.recipient_masked = mask_recipient(
        row.channel, email=row.recipient_email, chat_id=row.recipient_chat_id
    )
    return out


def _channel_row(db: Session, candidate: Candidate, channel: str) -> CandidateContactChannel | None:
    return (
        db.execute(
            select(CandidateContactChannel).where(
                CandidateContactChannel.candidate_id == candidate.id,
                CandidateContactChannel.channel == channel,
            )
        )
        .scalars()
        .first()
    )


def _require_channel(channel: str) -> None:
    if channel not in (EMAIL_CHANNEL, TELEGRAM_CHANNEL):
        raise HTTPException(status_code=422, detail="Неизвестный канал.")


def _event_context(
    db: Session,
    candidate: Candidate,
    message_type: CandidateMessageType,
    event_id: UUID | None,
    *,
    now: datetime,
) -> Event | None:
    """The linked interview event, validated for a manual interview message."""
    interview_types = (
        CandidateMessageType.INTERVIEW_SCHEDULED,
        CandidateMessageType.INTERVIEW_REMINDER,
        CandidateMessageType.INTERVIEW_RESCHEDULED,
        CandidateMessageType.INTERVIEW_CANCELLED,
    )
    if message_type not in interview_types:
        if event_id is not None:
            raise HTTPException(
                status_code=422, detail="Для этого типа сообщения событие не нужно."
            )
        return None
    if event_id is None:
        raise HTTPException(
            status_code=422, detail="Для сообщения о собеседовании укажите событие-собеседование."
        )
    event = db.get(Event, event_id)
    if event is None or event.candidate_id != candidate.id or event.type != EventType.INTERVIEW:
        raise HTTPException(status_code=404, detail="Событие-собеседование не найдено.")
    if message_type == CandidateMessageType.INTERVIEW_CANCELLED:
        if event.status != EventStatus.CANCELLED:
            raise HTTPException(
                status_code=409,
                detail="Собеседование ещё не отменено — отправлять сообщение об отмене нельзя.",
            )
        return event
    if event.status in (EventStatus.COMPLETED, EventStatus.CANCELLED):
        raise HTTPException(
            status_code=409, detail="Собеседование завершено или отменено — сообщение неактуально."
        )
    if (
        message_type
        in (
            CandidateMessageType.INTERVIEW_SCHEDULED,
            CandidateMessageType.INTERVIEW_REMINDER,
            CandidateMessageType.INTERVIEW_RESCHEDULED,
        )
        and ensure_aware(event.starts_at) <= now
    ):
        raise HTTPException(
            status_code=409, detail="Собеседование уже началось — сообщение неактуально."
        )
    return event


def _documents_or_error(message_type: CandidateMessageType, documents: list[str]) -> list[str]:
    if message_type in (
        CandidateMessageType.DOCUMENTS_REQUEST,
        CandidateMessageType.DOCUMENTS_REMINDER,
    ):
        return documents
    if documents:
        raise HTTPException(
            status_code=422, detail="Список документов нужен только для сообщений о документах."
        )
    return []


def _resolved_preview(
    db: Session,
    *,
    candidate: Candidate,
    message_type: CandidateMessageType,
    channel: str,
    event_id: UUID | None,
    documents: list[str],
    settings: Settings,
    request: Request,
    now: datetime,
) -> tuple[str, str, Event | None]:
    """Validate the manual-send context and compose the exact message text."""
    _require_channel(channel)
    documents = _documents_or_error(message_type, documents)
    event = _event_context(db, candidate, message_type, event_id, now=now)
    revoke_url = None
    if channel == EMAIL_CHANNEL:
        revoke_url = build_revoke_url(
            str(request.base_url),
            candidate_id=candidate.id,
            channel=EMAIL_CHANNEL,
            secret_key=settings.secret_key,
        )
    title, body = compose_message_text(
        message_type,
        candidate_full_name=candidate.full_name,
        position=candidate.position,
        starts_at=ensure_aware(event.starts_at) if event is not None else None,
        location=event.location if event is not None else None,
        documents=documents,
        timezone=settings.notification_default_timezone,
        revoke_url=revoke_url,
    )
    return title, body, event


def _allowed_or_409(candidate: Candidate, channel: str, db: Session, settings: Settings) -> None:
    state, _reason = channel_state(db, candidate, channel, settings=settings)
    if state != "allowed":
        raise HTTPException(
            status_code=409,
            detail=(
                "Канал не разрешён для отправки: подтвердите согласие кандидата и подключите канал."
            ),
        )


def _channel_recipient(
    candidate: Candidate, channel: str, db: Session
) -> tuple[str | None, int | None]:
    row = _channel_row(db, candidate, channel)
    if row is None:
        return None, None
    return (row.email_address, None) if channel == EMAIL_CHANNEL else (None, row.chat_id)


def _html_page(title: str, body_text: str) -> str:
    """Tiny static confirmation page for email links (no dynamic content)."""
    return (
        "<!doctype html><html lang='ru'><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width,initial-scale=1'>"
        f"<title>{title}</title></head><body style='font-family:sans-serif;max-width:32em;"
        "margin:2em auto;line-height:1.5;'>"
        f"<h1>{title}</h1><p>{body_text}</p></body></html>"
    )


# --- Channel overview ---------------------------------------------------------


@router.get(
    "/{candidate_id}/communications/channels",
    response_model=CandidateChannelsOut,
    summary="Per-channel consent state of a candidate",
)
def list_channels(
    candidate_id: UUID,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> CandidateChannelsOut:
    settings: Settings = request.app.state.settings
    candidate = _visible_candidate(db, candidate_id, user)
    now = utc_now()
    entries: list[CandidateChannelStatusOut] = []
    for channel in (EMAIL_CHANNEL, TELEGRAM_CHANNEL):
        state, reason = channel_state(db, candidate, channel, settings=settings, now=now)
        row = _channel_row(db, candidate, channel)
        pending = None
        if state == "pending_confirmation":
            purpose = (
                CandidateChannelPurpose.EMAIL_CONSENT
                if channel == EMAIL_CHANNEL
                else CandidateChannelPurpose.TELEGRAM_LINK
            )
            token = active_token(
                db, candidate_id=candidate.id, channel=channel, purpose=purpose, now=now
            )
            pending = token.expires_at if token is not None else None
        entries.append(
            CandidateChannelStatusOut(
                channel=cast(Literal["email", "telegram"], channel),
                state=state,
                reason=reason,
                recipient_masked=mask_recipient(
                    channel, email=row.email_address, chat_id=row.chat_id
                )
                if row is not None
                else None,
                consent_at=row.consent_at if row is not None else None,
                consent_source=row.consent_source if row is not None else None,
                pending_expires_at=pending,
            )
        )
    return CandidateChannelsOut(channels=entries)


# --- Email double opt-in ------------------------------------------------------


@router.post(
    "/{candidate_id}/communications/email/consent-request",
    response_model=CandidateConsentRequestOut,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Send the double-opt-in consent letter to the candidate email",
)
def email_consent_request(
    candidate_id: UUID,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> CandidateConsentRequestOut:
    settings: Settings = request.app.state.settings
    _enforce_user_rate_limit("candidate-consent-request", user, settings)
    candidate = _visible_candidate(db, candidate_id, user)
    state, _reason = channel_state(db, candidate, EMAIL_CHANNEL, settings=settings)
    if state == "allowed":
        raise HTTPException(status_code=409, detail="Email-канал уже разрешён.")
    if not smtp_config_from_settings(settings).is_configured:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Почтовый канал не настроен. Обратитесь к администратору.",
        )
    if not candidate.email:
        raise HTTPException(status_code=422, detail="У кандидата нет адреса электронной почты.")
    now = utc_now()
    message, token = schedule_consent_invite(
        db,
        candidate=candidate,
        base_url=str(request.base_url),
        settings=settings,
        initiator_user_id=user.id,
        now=now,
    )
    record_event(
        db,
        AuditAction.CANDIDATE_CONSENT_REQUESTED,
        actor=user,
        candidate_id=candidate.id,
        details=f"channel={EMAIL_CHANNEL} message={message.id}",
        commit=False,
    )
    db.commit()
    return CandidateConsentRequestOut(
        message_id=message.id,
        state="pending_confirmation",
        expires_at=token.expires_at,
    )


# --- Telegram linking (voluntary /start) --------------------------------------


@router.post(
    "/{candidate_id}/communications/telegram/link",
    response_model=CandidateTelegramLinkOut,
    status_code=status.HTTP_201_CREATED,
    summary="Issue a one-shot Telegram linking code for the candidate",
)
def telegram_link(
    candidate_id: UUID,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> CandidateTelegramLinkOut:
    settings: Settings = request.app.state.settings
    _enforce_user_rate_limit("candidate-tg-link", user, settings)
    candidate = _visible_candidate(db, candidate_id, user)
    state, _reason = channel_state(db, candidate, TELEGRAM_CHANNEL, settings=settings)
    if state == "allowed":
        raise HTTPException(status_code=409, detail="Telegram-канал уже подключён.")
    config = telegram_config_from_settings(settings)
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
    raw = new_token()
    token = issue_token(
        db,
        candidate_id=candidate.id,
        channel=TELEGRAM_CHANNEL,
        purpose=CandidateChannelPurpose.TELEGRAM_LINK,
        ttl=timedelta(minutes=settings.candidate_telegram_token_ttl_minutes),
        now=now,
        raw=raw,
    )
    record_event(
        db,
        AuditAction.CANDIDATE_CONSENT_REQUESTED,
        actor=user,
        candidate_id=candidate.id,
        details=f"channel={TELEGRAM_CHANNEL} token={token.id}",
        commit=False,
    )
    db.commit()
    return CandidateTelegramLinkOut(
        deep_link=f"https://t.me/{bot_username}?start={raw}",
        expires_at=token.expires_at,
        state="pending_confirmation",
    )


def _poll_state_for_update(db: Session) -> TelegramPollState:
    """Load the singleton poll offset under a row lock, creating it once."""
    state = db.execute(
        select(TelegramPollState).where(TelegramPollState.id == 1).with_for_update()
    ).scalar_one_or_none()
    if state is not None:
        return state
    candidate = TelegramPollState(id=1, last_update_id=None, updated_at=utc_now())
    try:
        with db.begin_nested():
            db.add(candidate)
            db.flush()
    except IntegrityError as exc:
        if not is_duplicate_key_error(exc):
            raise
        state = db.execute(
            select(TelegramPollState).where(TelegramPollState.id == 1).with_for_update()
        ).scalar_one()
        return state
    return candidate


@router.post(
    "/{candidate_id}/communications/telegram/confirm",
    response_model=CandidateTelegramConfirmOut,
    summary="Poll the bot once and bind the candidate's chat on /start",
)
def telegram_confirm(
    candidate_id: UUID,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> CandidateTelegramConfirmOut:
    settings: Settings = request.app.state.settings
    _enforce_user_rate_limit("candidate-tg-confirm", user, settings)
    candidate = _visible_candidate(db, candidate_id, user)
    config = telegram_config_from_settings(settings)
    if not config.is_configured:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Telegram-канал не настроен. Обратитесь к администратору.",
        )
    now = utc_now()
    token = active_token(
        db,
        candidate_id=candidate.id,
        channel=TELEGRAM_CHANNEL,
        purpose=CandidateChannelPurpose.TELEGRAM_LINK,
        now=now,
    )
    if token is None:
        state, _reason = channel_state(db, candidate, TELEGRAM_CHANNEL, settings=settings, now=now)
        if state == "allowed":
            return CandidateTelegramConfirmOut(state="allowed", detail="Telegram-канал подключён.")
        return CandidateTelegramConfirmOut(
            state="not_connected", detail="Нет активного кода привязки. Создайте новый код."
        )

    state_row = _poll_state_for_update(db)
    offset = state_row.last_update_id + 1 if state_row.last_update_id is not None else None
    poll = get_start_updates(config, offset=offset)
    if not poll.ok:
        db.rollback()
        if poll.error_class == "telegram_unauthorized":
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Telegram-канал настроен неверно. Обратитесь к администратору.",
            )
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Telegram временно недоступен. Повторите попытку позже.",
        )
    # Record every observed /start BEFORE advancing the shared offset so
    # other confirms never lose their events.
    for start in poll.updates:
        try:
            with db.begin_nested():
                db.add(
                    TelegramStartEvent(
                        token_hash=token_hash(start.token),
                        chat_id=start.chat_id,
                        seen_at=now,
                    )
                )
                db.flush()
        except IntegrityError as exc:
            if not is_duplicate_key_error(exc):
                raise
    if poll.max_update_id is not None:
        state_row.last_update_id = poll.max_update_id
        state_row.updated_at = now
    # Opportunistic prune of events that can no longer match a live token.
    db.execute(
        delete(TelegramStartEvent).where(TelegramStartEvent.seen_at < now - timedelta(days=1))
    )

    # Look for an event matching THIS candidate's active token hash.
    start_event = db.get(TelegramStartEvent, token.token_hash)
    if start_event is None:
        db.commit()  # persist the advanced offset + observed events
        return CandidateTelegramConfirmOut(
            state="pending_confirmation",
            detail=(
                "Мы пока не видим запуск бота с этим кодом. Отправьте кандидату ссылку, "
                "пусть он откроет её в Telegram и нажмёт «Запустить», затем повторите проверку."
            ),
        )
    # A chat bound to another candidate fails closed — no takeover.
    holder = (
        db.execute(
            select(Candidate.id)
            .join(
                CandidateContactChannel,
                CandidateContactChannel.candidate_id == Candidate.id,
            )
            .where(
                CandidateContactChannel.channel == TELEGRAM_CHANNEL,
                CandidateContactChannel.chat_id == start_event.chat_id,
                CandidateContactChannel.consent_granted.is_(True),
                CandidateContactChannel.candidate_id != candidate.id,
            )
        )
        .scalars()
        .first()
    )
    if holder is not None:
        db.execute(
            delete(TelegramStartEvent).where(TelegramStartEvent.token_hash == token.token_hash)
        )
        record_event(
            db,
            AuditAction.CANDIDATE_CONSENT_REQUESTED,
            actor=user,
            candidate_id=candidate.id,
            details="channel=telegram conflict=chat_taken",
            commit=False,
        )
        db.commit()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Этот чат Telegram уже привязан к другому кандидату.",
        )
    try:
        consumed = consume_token(db, token, reason="linked", now=now, chat_id=start_event.chat_id)
        if not consumed:
            db.rollback()
            raise HTTPException(status_code=409, detail="Код уже использован. Создайте новый код.")
        row = _channel_row(db, candidate, TELEGRAM_CHANNEL)
        if row is None:
            row = CandidateContactChannel(
                candidate_id=candidate.id, channel=TELEGRAM_CHANNEL, created_at=now
            )
            db.add(row)
        row.chat_id = start_event.chat_id
        row.linked_at = now
        row.consent_granted = True
        row.consent_at = now
        row.consent_source = "telegram_start"
        row.consent_policy_version = CANDIDATE_CONSENT_POLICY_VERSION
        row.revoked_at = None
        row.revoke_reason = None
        row.last_error_class = None
        row.last_error_at = None
        row.updated_at = now
        db.execute(
            delete(TelegramStartEvent).where(TelegramStartEvent.token_hash == token.token_hash)
        )
        record_event(
            db,
            AuditAction.CANDIDATE_CONSENT_GRANTED,
            actor=user,
            candidate_id=candidate.id,
            details=(
                f"channel={TELEGRAM_CHANNEL} source=telegram_start "
                f"version={CANDIDATE_CONSENT_POLICY_VERSION}"
            ),
            commit=False,
        )
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        if not is_duplicate_key_error(exc):
            raise
        db.execute(
            delete(TelegramStartEvent).where(TelegramStartEvent.token_hash == token.token_hash)
        )
        record_event(
            db,
            AuditAction.CANDIDATE_CONSENT_REQUESTED,
            actor=user,
            candidate_id=candidate.id,
            details="channel=telegram conflict=chat_taken",
            commit=False,
        )
        db.commit()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Этот чат Telegram уже привязан к другому кандидату.",
        ) from exc
    return CandidateTelegramConfirmOut(
        state="allowed",
        detail="Telegram-канал подключён: кандидат получит сообщения в этот чат.",
    )


# --- Revoke -------------------------------------------------------------------


@router.post(
    "/{candidate_id}/communications/{channel}/revoke",
    response_model=CandidateRevokeOut,
    summary="Revoke consent for a channel and cancel its pending messages",
)
def revoke_channel(
    candidate_id: UUID,
    channel: str,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> CandidateRevokeOut:
    settings: Settings = request.app.state.settings
    _enforce_user_rate_limit("candidate-revoke", user, settings)
    _require_channel(channel)
    candidate = _visible_candidate(db, candidate_id, user)
    now = utc_now()
    row = _channel_row(db, candidate, channel)
    if row is None:
        row = CandidateContactChannel(candidate_id=candidate.id, channel=channel, created_at=now)
        db.add(row)
    row.consent_granted = False
    row.consent_at = None
    row.consent_source = None
    row.consent_policy_version = None
    row.email_address = None
    row.chat_id = None
    row.linked_at = None
    row.revoked_at = now
    row.revoke_reason = "hr_revoked"
    row.updated_at = now
    # Stop every queued message of this candidate/channel immediately.
    cancel_pending_candidate_messages(db, candidate_id=candidate.id, channel=channel, now=now)
    record_event(
        db,
        AuditAction.CANDIDATE_CONSENT_REVOKED,
        actor=user,
        candidate_id=candidate.id,
        details=f"channel={channel} source=hr",
        commit=False,
    )
    db.commit()
    state, _reason = channel_state(db, candidate, channel, settings=settings, now=now)
    return CandidateRevokeOut(channel=channel, state=state)  # type: ignore[arg-type]


# --- Preview / manual send ----------------------------------------------------


@router.post(
    "/{candidate_id}/communications/preview",
    response_model=CandidateMessagePreviewOut,
    summary="Server-rendered preview of a message (nothing is queued)",
)
def preview_message(
    candidate_id: UUID,
    payload: CandidateMessageSendRequest,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> CandidateMessagePreviewOut:
    settings: Settings = request.app.state.settings
    candidate = _visible_candidate(db, candidate_id, user)
    now = utc_now()
    title, body, _event = _resolved_preview(
        db,
        candidate=candidate,
        message_type=payload.message_type,
        channel=payload.channel,
        event_id=payload.event_id,
        documents=payload.documents,
        settings=settings,
        request=request,
        now=now,
    )
    return CandidateMessagePreviewOut(
        title=title,
        body=body,
        quiet_hours_now=in_candidate_quiet_period(now, settings=settings),
        will_send=True,
    )


@router.post(
    "/{candidate_id}/communications/send",
    response_model=CandidateMessageSendOut,
    status_code=status.HTTP_201_CREATED,
    summary="Queue a manual candidate message (async delivery by the worker)",
)
def send_message(
    candidate_id: UUID,
    payload: CandidateMessageSendRequest,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> CandidateMessageSendOut:
    settings: Settings = request.app.state.settings
    _enforce_user_rate_limit("candidate-send", user, settings)
    candidate = _visible_candidate(db, candidate_id, user)
    _allowed_or_409(candidate, payload.channel, db, settings)
    now = utc_now()
    if in_candidate_quiet_period(now, settings=settings) and not payload.confirm_quiet_hours:
        next_at = next_allowed_candidate_time(now, settings=settings)
        raise HTTPException(
            status_code=409,
            detail=(
                "Сейчас тихие часы (по умолчанию сообщения кандидатам не отправляются "
                f"с 21:00 до 08:00). Ближайшее доступное время — "
                f"{next_at.strftime('%d.%m.%Y %H:%M')}. Подтвердите немедленную отправку, "
                "если сообщение срочное."
            ),
        )
    title, body, event = _resolved_preview(
        db,
        candidate=candidate,
        message_type=payload.message_type,
        channel=payload.channel,
        event_id=payload.event_id,
        documents=payload.documents,
        settings=settings,
        request=request,
        now=now,
    )
    recipient_email, recipient_chat_id = _channel_recipient(candidate, payload.channel, db)
    if payload.channel == EMAIL_CHANNEL and not recipient_email:
        raise HTTPException(status_code=409, detail="Нет разрешённого адреса электронной почты.")
    if payload.channel == TELEGRAM_CHANNEL and recipient_chat_id is None:
        raise HTTPException(status_code=409, detail="Нет подключённого чата Telegram.")

    idempotency_key = None
    if payload.idempotency_key:
        idempotency_key = f"manual:{user.id}:{payload.idempotency_key}"
        existing = existing_by_idempotency_key(db, idempotency_key)
        if existing is not None:
            db.commit()
            return CandidateMessageSendOut(message=_message_out(existing), duplicate=True)

    row, _created = schedule_candidate_message(
        db,
        candidate=candidate,
        channel=payload.channel,
        message_type=payload.message_type,
        source=CandidateMessageSource.MANUAL,
        title=title,
        body=body,
        recipient_email=recipient_email,
        recipient_chat_id=recipient_chat_id,
        initiator_user_id=user.id,
        event_id=event.id if event is not None else None,
        event_version=event.version if event is not None else None,
        scheduled_at=now,
        idempotency_key=idempotency_key,
        consent_snapshot={
            "channel": payload.channel,
            "granted": True,
            "policy_version": CANDIDATE_CONSENT_POLICY_VERSION,
        },
        quiet_hours_bypassed=payload.confirm_quiet_hours,
        now=now,
    )
    bypass_note = " quiet_hours_bypassed=1" if payload.confirm_quiet_hours else ""
    record_event(
        db,
        AuditAction.CANDIDATE_MESSAGE_SENT,
        actor=user,
        candidate_id=candidate.id,
        details=(
            f"channel={payload.channel} type={payload.message_type.value} source=manual "
            f"message={row.id}{bypass_note}"
        ),
        commit=False,
    )
    db.commit()
    return CandidateMessageSendOut(message=_message_out(row), duplicate=False)


# --- History ------------------------------------------------------------------


@router.get(
    "/{candidate_id}/communications/history",
    response_model=CandidateMessageList,
    summary="Immutable message history of a candidate",
)
def list_history(
    candidate_id: UUID,
    request: Request,
    limit: int = Query(default=_DEFAULT_LIST_LIMIT, ge=1, le=_MAX_LIST_LIMIT),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> CandidateMessageList:
    del request
    candidate = _visible_candidate(db, candidate_id, user)
    total = (
        db.scalar(
            select(func.count())
            .select_from(CandidateMessage)
            .where(CandidateMessage.candidate_id == candidate.id)
        )
        or 0
    )
    rows = (
        db.execute(
            select(CandidateMessage)
            .where(CandidateMessage.candidate_id == candidate.id)
            .order_by(CandidateMessage.created_at.desc(), CandidateMessage.id.desc())
            .limit(limit)
            .offset(offset)
        )
        .scalars()
        .all()
    )
    return CandidateMessageList(
        items=[_message_out(row) for row in rows], total=total, limit=limit, offset=offset
    )


@router.get(
    "/{candidate_id}/communications/history/{message_id}",
    response_model=CandidateMessageOut,
    summary="One message from the candidate history",
)
def message_detail(
    candidate_id: UUID,
    message_id: UUID,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> CandidateMessageOut:
    del request
    candidate = _visible_candidate(db, candidate_id, user)
    row = (
        db.execute(
            select(CandidateMessage).where(
                CandidateMessage.id == message_id,
                CandidateMessage.candidate_id == candidate.id,
            )
        )
        .scalars()
        .first()
    )
    if row is None:
        raise HTTPException(status_code=404, detail="Сообщение не найдено.")
    return _message_out(row)


@router.post(
    "/{candidate_id}/communications/{message_id}/cancel",
    response_model=CandidateMessageCancelOut,
    summary="Cancel a queued candidate message",
)
def cancel_message(
    candidate_id: UUID,
    message_id: UUID,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> CandidateMessageCancelOut:
    settings: Settings = request.app.state.settings
    _enforce_user_rate_limit("candidate-cancel", user, settings)
    candidate = _visible_candidate(db, candidate_id, user)
    row = (
        db.execute(
            select(CandidateMessage)
            .where(
                CandidateMessage.id == message_id,
                CandidateMessage.candidate_id == candidate.id,
            )
            .with_for_update()
        )
        .scalars()
        .first()
    )
    if row is None:
        raise HTTPException(status_code=404, detail="Сообщение не найдено.")
    cancelled = False
    if row.status == "queued":
        row.status = "cancelled"
        row.cancelled_at = utc_now()
        cancelled = True
        record_event(
            db,
            AuditAction.CANDIDATE_MESSAGE_CANCELLED,
            actor=user,
            candidate_id=candidate.id,
            details=f"channel={row.channel} type={row.message_type.value} message={row.id}",
            commit=False,
        )
    db.commit()
    return CandidateMessageCancelOut(message=_message_out(row), cancelled=cancelled)


# --- Public candidate-facing endpoints (no session) ---------------------------


@public_router.get("/consent/{raw_token}", response_class=HTMLResponse)
def public_email_consent(
    raw_token: str,
    request: Request,
    db: Session = Depends(get_db),
) -> HTMLResponse:
    """Double-opt-in confirmation link from the consent letter."""
    settings: Settings = request.app.state.settings
    _enforce_public_rate_limit("consent", request, settings)
    now = utc_now()
    token = find_token_by_hash(db, raw_token)
    if (
        token is None
        or token.channel != EMAIL_CHANNEL
        or token.purpose != CandidateChannelPurpose.EMAIL_CONSENT
    ):
        return HTMLResponse(
            content=_html_page(
                "Ссылка недействительна",
                "Эта ссылка недействительна или устарела. Сообщения отправляться не будут.",
            ),
            status_code=200,
        )
    if ensure_aware(token.expires_at) <= now or token.consumed_at is not None:
        return HTMLResponse(
            content=_html_page(
                "Ссылка уже использована",
                "Согласие уже было подтверждено или срок ссылки истёк.",
            ),
            status_code=200,
        )
    candidate = db.get(Candidate, token.candidate_id)
    if candidate is None or candidate.deleted_at is not None:
        return HTMLResponse(
            content=_html_page("Не найдено", "Обращение не найдено."), status_code=200
        )
    if not consume_token(db, token, reason="confirmed", now=now):
        db.commit()
        return HTMLResponse(
            content=_html_page("Ссылка уже использована", "Согласие уже было подтверждено ранее."),
            status_code=200,
        )
    row = _channel_row(db, candidate, EMAIL_CHANNEL)
    address = token.email_address or candidate.email
    if row is None:
        row = CandidateContactChannel(
            candidate_id=candidate.id, channel=EMAIL_CHANNEL, created_at=now
        )
        db.add(row)
    row.email_address = address
    row.consent_granted = True
    row.consent_at = now
    row.consent_source = "candidate_email_link"
    row.consent_policy_version = CANDIDATE_CONSENT_POLICY_VERSION
    row.revoked_at = None
    row.revoke_reason = None
    row.updated_at = now
    record_event(
        db,
        AuditAction.CANDIDATE_CONSENT_GRANTED,
        candidate_id=candidate.id,
        details=(
            f"channel={EMAIL_CHANNEL} source=candidate_email_link "
            f"version={CANDIDATE_CONSENT_POLICY_VERSION}"
        ),
        commit=False,
    )
    db.commit()
    return HTMLResponse(
        content=_html_page(
            "Согласие подтверждено",
            "Спасибо! Вы согласились получать сообщения о собеседованиях и документах. "
            "Отказаться от сообщений можно в любой момент по ссылке в письме.",
        ),
        status_code=200,
    )


@public_router.get("/revoke/{candidate_id}/{channel}", response_class=HTMLResponse)
def public_email_revoke(
    candidate_id: UUID,
    channel: str,
    request: Request,
    db: Session = Depends(get_db),
) -> HTMLResponse:
    """Stateless unsubscribe link (HMAC-signed; embedded in every email)."""
    settings: Settings = request.app.state.settings
    _enforce_public_rate_limit("revoke", request, settings)
    _require_channel(channel)
    if channel != EMAIL_CHANNEL:
        raise HTTPException(status_code=404, detail="Не найдено.")
    signature = request.query_params.get("sig", "")
    if not verify_revoke_url(candidate_id, channel, signature, settings.secret_key):
        raise HTTPException(status_code=404, detail="Не найдено.")
    candidate = db.get(Candidate, candidate_id)
    if candidate is None or candidate.deleted_at is not None:
        return HTMLResponse(
            content=_html_page("Не найдено", "Обращение не найдено."), status_code=200
        )
    now = utc_now()
    row = _channel_row(db, candidate, EMAIL_CHANNEL)
    if row is not None:
        row.consent_granted = False
        row.consent_at = None
        row.consent_source = None
        row.consent_policy_version = None
        row.revoked_at = now
        row.revoke_reason = "candidate_revoked"
        row.updated_at = now
        cancel_pending_candidate_messages(
            db, candidate_id=candidate.id, channel=EMAIL_CHANNEL, now=now
        )
        record_event(
            db,
            AuditAction.CANDIDATE_CONSENT_REVOKED,
            candidate_id=candidate.id,
            details=f"channel={EMAIL_CHANNEL} source=public_link",
            commit=False,
        )
    db.commit()
    return HTMLResponse(
        content=_html_page(
            "Отписка выполнена",
            "Вы отказались от сообщений. Новые сообщения на этот адрес отправляться не будут.",
        ),
        status_code=200,
    )
