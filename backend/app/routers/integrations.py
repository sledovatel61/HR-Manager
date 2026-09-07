"""External channels API: Telegram linking, email verification, consent (phase 9).

Own-channel endpoints (``/integrations/*``) let the current user link and
verify their own bindings; every mutating call is CSRF-protected by the
session dependency and server-side ownership-checked (a foreign token or
id is 404 — no existence leak). Admin endpoints
(``/admin/integrations/*``) expose global configuration state (never
secrets) plus live checks and an explicit test send.

Security notes:

* linking/verification tokens carry ~256-bit entropy (``secrets``), are
  stored as SHA-256 hashes and are single-use with a TTL;
* only the numeric Telegram ``chat_id`` is stored — never a username —
  and only after the user voluntarily opened the bot (``/start <token>``
  observed via getUpdates);
* rate limits guard every linking/verification/test/check endpoint;
* audit records cover link/unlink/consent/verify/test/check; details never
  contain tokens, chat ids, addresses or message text.
"""

from __future__ import annotations

import hashlib
import re
import secrets
import uuid
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import EmailStr
from sqlalchemy import delete, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.audit import record_event
from app.config import Settings
from app.db import get_db
from app.deps import get_current_user, require_roles
from app.models import (
    AuditAction,
    DeliveryChannel,
    NotificationPreference,
    TelegramLink,
    TelegramLinkToken,
    TelegramPollState,
    TelegramStartEvent,
    User,
    UserEmail,
    UserRole,
)
from app.notification_service import (
    is_duplicate_key_error,
    schedule_channel_test,
    schedule_verification_email,
)
from app.rate_limiting import SlidingWindowRateLimiter
from app.schemas import (
    AdminChannelsOut,
    AdminSmtpInfo,
    AdminTelegramInfo,
    ChannelCheckOut,
    ChannelState,
    ChannelTestOut,
    ConsentOut,
    ConsentUpdate,
    EmailConfirmOut,
    EmailConfirmRequest,
    EmailSetOut,
    EmailSetRequest,
    EmailStatusOut,
    IntegrationStatusOut,
    TelegramConfirmOut,
    TelegramLinkOut,
    TelegramStatusOut,
)
from app.smtp import SmtpCheckResult, SmtpConfig
from app.telegram import TelegramCheckResult, TelegramConfig, TelegramUpdatesResult
from app.utils import utc_now

router = APIRouter(tags=["integrations"])

_admin_only = require_roles(UserRole.ADMIN)

# A binding whose last failure is temporary and recent reports
# «temporarily unavailable» instead of «works».
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
_SMTP_TEMP_CLASSES = frozenset(
    {"smtp_temporary", "smtp_timeout", "smtp_network", "smtp_auth", "smtp_config"}
)

# Verification-code guessing budget per pending address.
EMAIL_VERIFICATION_MAX_ATTEMPTS = 10

_BOT_USERNAME_RE = re.compile(r"^[A-Za-z0-9_]{5,32}$")

# --- Test hooks (module-level callables; unit tests monkeypatch these,
# integration tests point Settings at stub servers and use the real ones).
_poll_starts_impl = None
_telegram_check_impl = None
_smtp_check_impl = None


def _poll_starts(config: TelegramConfig, *, offset: int | None) -> TelegramUpdatesResult:
    if _poll_starts_impl is not None:
        return _poll_starts_impl(config, offset=offset)
    from app.telegram import get_start_updates

    return get_start_updates(config, offset=offset)


def _check_telegram(config: TelegramConfig) -> TelegramCheckResult:
    if _telegram_check_impl is not None:
        return _telegram_check_impl(config)
    from app.telegram import check_connection

    return check_connection(config)


def _check_smtp(config: SmtpConfig) -> SmtpCheckResult:
    if _smtp_check_impl is not None:
        return _smtp_check_impl(config)
    from app.smtp import check_connection

    return check_connection(config)


# --- Rate limiting (process-local, like the login limiter) -------------------

_limiters: dict[str, SlidingWindowRateLimiter] = {}
_limiter_shape: tuple[int, int] | None = None


def _limiter_for(action: str, settings: Settings) -> SlidingWindowRateLimiter:
    """One sliding-window limiter per action, rebuilt when tuned."""
    global _limiter_shape
    shape = (settings.integration_rate_limit, settings.integration_rate_window_s)
    if _limiter_shape != shape:
        _limiters.clear()
        _limiter_shape = shape
    limiter = _limiters.get(action)
    if limiter is None:
        limiter = SlidingWindowRateLimiter(limit=shape[0], window_seconds=shape[1])
        _limiters[action] = limiter
    return limiter


def reset_integration_limiters() -> None:
    """Clear all integration rate counters (used between tests)."""
    for limiter in _limiters.values():
        limiter.reset()


def _enforce_rate_limit(action: str, user: User, settings: Settings) -> None:
    result = _limiter_for(action, settings).check(f"{action}:{user.id}")
    if not result.allowed:
        # The header must ride on the exception itself: headers set on the
        # injected `response` are dropped when the handler raises.
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Слишком много попыток. Повторите позже.",
            headers={"Retry-After": str(result.retry_after_seconds)},
        )


# --- Small helpers ------------------------------------------------------------


def _token_hash(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def mask_chat_id(chat_id: int) -> str:
    digits = "".join(ch for ch in str(abs(chat_id)) if ch.isdigit())
    return "••••" + (digits[-4:] if len(digits) >= 4 else digits)


def mask_email(address: str) -> str:
    local, _, domain = address.partition("@")
    head = local[:1] if local else ""
    return f"{head}***@{domain}" if domain else "***"


def _telegram_config(settings: Settings) -> TelegramConfig:
    from app.telegram import config_from_settings

    return config_from_settings(settings)


def _smtp_config(settings: Settings) -> SmtpConfig:
    from app.smtp import config_from_settings

    return config_from_settings(settings)


def _active_link_token(db: Session, user_id: object, now: datetime) -> TelegramLinkToken | None:
    return (
        db.execute(
            select(TelegramLinkToken)
            .where(
                TelegramLinkToken.user_id == user_id,
                TelegramLinkToken.consumed_at.is_(None),
                TelegramLinkToken.expires_at > now,
            )
            .order_by(TelegramLinkToken.created_at.desc())
        )
        .scalars()
        .first()
    )


def _telegram_state(
    *,
    configured: bool,
    link: TelegramLink | None,
    pending: bool,
    opt_in: bool,
    now: datetime,
) -> ChannelState:
    if not configured:
        return "not_configured"
    if pending:
        return "pending"
    if link is None or link.chat_id is None:
        return "not_configured"
    if link.revoked_at is not None:
        return "revoked"
    if not opt_in:
        return "not_configured"
    if (
        link.last_error_class in _TELEGRAM_TEMP_CLASSES
        and link.last_error_at is not None
        and (now - link.last_error_at) <= TEMP_ERROR_WINDOW
    ):
        return "temporarily_unavailable"
    return "works"


def _email_state(
    *,
    configured: bool,
    address: UserEmail | None,
    opt_in: bool,
    now: datetime,
) -> ChannelState:
    if not configured:
        return "not_configured"
    pending = (
        address is not None
        and address.pending_email is not None
        and address.verification_expires_at is not None
        and address.verification_expires_at > now
    )
    if address is None or not address.is_verified:
        return "pending" if pending else "not_configured"
    assert address is not None
    if not opt_in:
        return "not_configured"
    if (
        address.last_error_class in _SMTP_TEMP_CLASSES
        and address.last_error_at is not None
        and (now - address.last_error_at) <= TEMP_ERROR_WINDOW
    ):
        return "temporarily_unavailable"
    return "works"


def _preference_or_defaults(db: Session, user_id: object) -> NotificationPreference:
    """Load the preference row, inserting system defaults when missing."""
    from app.config import (
        DEFAULT_QUIET_HOURS_END,
        DEFAULT_QUIET_HOURS_START,
        DEFAULT_WORKDAYS,
    )
    from app.models import NotificationType

    row = db.get(NotificationPreference, user_id)
    if row is not None:
        return row
    row = NotificationPreference(
        user_id=user_id,
        timezone="Europe/Moscow",
        quiet_hours_start=DEFAULT_QUIET_HOURS_START,
        quiet_hours_end=DEFAULT_QUIET_HOURS_END,
        workdays=[int(day) for day in DEFAULT_WORKDAYS.split(",")],
        enabled_types=[member.value for member in NotificationType],
        enabled_channels=[DeliveryChannel.IN_APP.value],
    )
    db.add(row)
    db.flush()
    return row


def _set_consent(
    db: Session,
    *,
    user: User,
    channel: DeliveryChannel,
    opt_in: bool,
    now: datetime,
) -> ConsentOut:
    from app.config import CONSENT_POLICY_VERSION

    row = _preference_or_defaults(db, user.id)
    if channel == DeliveryChannel.TELEGRAM:
        row.telegram_opt_in = opt_in
        row.telegram_consent_at = now
        row.telegram_consent_source = "web-ui"
        row.telegram_consent_policy_version = CONSENT_POLICY_VERSION
    else:
        row.email_opt_in = opt_in
        row.email_consent_at = now
        row.email_consent_source = "web-ui"
        row.email_consent_policy_version = CONSENT_POLICY_VERSION
    # Keep enabled_channels consistent with consent (single source of truth
    # for external delivery is the opt-in flag; this list stays coherent).
    channels = set(row.enabled_channels or [])
    if opt_in:
        channels.add(channel.value)
    else:
        channels.discard(channel.value)
    row.enabled_channels = sorted(channels)
    row.updated_at = now
    action = (
        AuditAction.TELEGRAM_CONSENT_UPDATED
        if channel == DeliveryChannel.TELEGRAM
        else AuditAction.EMAIL_CONSENT_UPDATED
    )
    record_event(
        db,
        action,
        actor=user,
        details=f"channel={channel.value} opt_in={str(opt_in).lower()}",
        commit=False,
    )
    db.commit()
    return ConsentOut(
        channel=channel.value,
        opt_in=opt_in,
        consent_at=now,
        policy_version=CONSENT_POLICY_VERSION,
    )


# --- Own status ---------------------------------------------------------------


@router.get("/integrations/status", response_model=IntegrationStatusOut)
def integration_status(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> IntegrationStatusOut:
    """Own channels state (no secrets, masked identifiers)."""
    settings: Settings = request.app.state.settings
    now = utc_now()
    telegram_cfg = _telegram_config(settings)
    smtp_cfg = _smtp_config(settings)
    link = db.get(TelegramLink, user.id)
    address = db.get(UserEmail, user.id)
    preference = db.get(NotificationPreference, user.id)
    telegram_opt_in = bool(preference is not None and preference.telegram_opt_in)
    email_opt_in = bool(preference is not None and preference.email_opt_in)
    pending_link = _active_link_token(db, user.id, now) is not None
    return IntegrationStatusOut(
        telegram=TelegramStatusOut(
            state=_telegram_state(
                configured=telegram_cfg.is_configured,
                link=link,
                pending=pending_link,
                opt_in=telegram_opt_in,
                now=now,
            ),
            configured=telegram_cfg.is_configured,
            linked=bool(link is not None and link.is_linked),
            masked_chat_id=(
                mask_chat_id(link.chat_id)
                if link is not None and link.chat_id is not None
                else None
            ),
            pending_confirmation=pending_link,
            opt_in=telegram_opt_in,
            consent_at=preference.telegram_consent_at if preference else None,
            linked_at=link.linked_at if link else None,
        ),
        email=EmailStatusOut(
            state=_email_state(
                configured=smtp_cfg.is_configured, address=address, opt_in=email_opt_in, now=now
            ),
            configured=smtp_cfg.is_configured,
            verified=bool(address is not None and address.is_verified),
            address_masked=(
                mask_email(address.email)
                if address is not None and address.email is not None
                else None
            ),
            pending_email_masked=(
                mask_email(address.pending_email)
                if address is not None and address.pending_email is not None
                else None
            ),
            pending_confirmation=bool(
                address is not None
                and address.pending_email is not None
                and address.verification_expires_at is not None
                and address.verification_expires_at > now
            ),
            opt_in=email_opt_in,
            consent_at=preference.email_consent_at if preference else None,
        ),
    )


# --- Telegram linking ---------------------------------------------------------


@router.post(
    "/integrations/telegram/link-code",
    response_model=TelegramLinkOut,
    status_code=status.HTTP_201_CREATED,
)
def create_link_code(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> TelegramLinkOut:
    """Create a one-shot linking token rendered as a bot deep link.

    The raw token is shown once; only its hash is stored. Older unconsumed
    tokens of the same user are superseded.
    """
    settings: Settings = request.app.state.settings
    _enforce_rate_limit("telegram-link", user, settings)
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
    # Supersede older unconsumed tokens (single active token per user).
    db.execute(
        update(TelegramLinkToken)
        .where(
            TelegramLinkToken.user_id == user.id,
            TelegramLinkToken.consumed_at.is_(None),
        )
        .values(consumed_at=now, consume_reason="superseded")
    )
    raw_token = secrets.token_urlsafe(32)
    expires_at = now + timedelta(minutes=settings.telegram_link_ttl_minutes)
    db.add(
        TelegramLinkToken(
            user_id=user.id,
            token_hash=_token_hash(raw_token),
            created_at=now,
            expires_at=expires_at,
        )
    )
    record_event(
        db, AuditAction.TELEGRAM_LINK_STARTED, actor=user, details="channel=telegram", commit=False
    )
    db.commit()
    return TelegramLinkOut(
        deep_link=f"https://t.me/{bot_username}?start={raw_token}", expires_at=expires_at
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


@router.post("/integrations/telegram/confirm", response_model=TelegramConfirmOut)
def confirm_link(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> TelegramConfirmOut:
    """Bind the numeric chat_id after the user opened the bot.

    Polls the bot inbox once for the user's ``/start <token>`` event. When
    it is not there yet the token stays active and the user retries (409
    with guidance) — nothing is bound speculatively.
    """
    settings: Settings = request.app.state.settings
    _enforce_rate_limit("telegram-confirm", user, settings)
    config = _telegram_config(settings)
    if not config.is_configured:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Telegram-канал не настроен. Обратитесь к администратору.",
        )
    now = utc_now()
    token = _active_link_token(db, user.id, now)
    if token is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Нет активного кода привязки. Создайте новый код.",
        )
    state = _poll_state_for_update(db)
    offset = state.last_update_id + 1 if state.last_update_id is not None else None
    poll = _poll_starts(config, offset=offset)
    if not poll.ok:
        db.rollback()
        if poll.error_class in ("telegram_unauthorized",):
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Telegram-канал настроен неверно. Обратитесь к администратору.",
            )
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Telegram временно недоступен. Повторите попытку позже.",
        )
    # Record every observed /start BEFORE advancing the offset, so other
    # users' concurrent confirms find their own events afterwards.
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
            # Already observed (redelivered update) — keep the first chat.
    if poll.max_update_id is not None:
        state.last_update_id = poll.max_update_id
        state.updated_at = now
    # Opportunistic prune: start events older than a day can never match a
    # live linking token (TTL is minutes).
    db.execute(
        delete(TelegramStartEvent).where(TelegramStartEvent.seen_at < now - timedelta(days=1))
    )
    event = db.get(TelegramStartEvent, token.token_hash)
    if event is None:
        db.commit()  # persist the advanced offset + observed events
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "Мы пока не видим запуск бота с вашим кодом. "
                "Откройте ссылку, нажмите «Запустить» в Telegram и повторите."
            ),
        )
    # Single-use, race-safe consume: exactly one confirm wins.
    consumed_result = db.execute(
        update(TelegramLinkToken)
        .where(
            TelegramLinkToken.id == token.id,
            TelegramLinkToken.consumed_at.is_(None),
        )
        .values(consumed_at=now, consume_reason="linked")
    )
    consumed = consumed_result.rowcount  # type: ignore[attr-defined]
    if consumed != 1:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Код уже использован. Создайте новый код.",
        )
    link = db.get(TelegramLink, user.id)
    if link is None:
        link = TelegramLink(user_id=user.id, created_at=now)
        db.add(link)
    link.chat_id = event.chat_id
    link.linked_at = now
    link.revoked_at = None
    link.revoke_reason = None
    link.last_error_class = None
    link.last_error_at = None
    link.updated_at = now
    db.execute(delete(TelegramStartEvent).where(TelegramStartEvent.token_hash == token.token_hash))
    record_event(
        db,
        AuditAction.TELEGRAM_LINK_CONFIRMED,
        actor=user,
        details="channel=telegram",
        commit=False,
    )
    db.commit()
    preference = db.get(NotificationPreference, user.id)
    opt_in = bool(preference is not None and preference.telegram_opt_in)
    return TelegramConfirmOut(
        linked=True,
        state=_telegram_state(configured=True, link=link, pending=False, opt_in=opt_in, now=now),
    )


@router.post("/integrations/telegram/unlink", response_model=TelegramStatusOut)
def unlink_telegram(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> TelegramStatusOut:
    """Revoke the own Telegram binding (re-linking stays available)."""
    settings: Settings = request.app.state.settings
    now = utc_now()
    link = db.get(TelegramLink, user.id)
    if link is not None and link.is_linked:
        link.revoked_at = now
        link.revoke_reason = "user"
        link.updated_at = now
        record_event(
            db, AuditAction.TELEGRAM_UNLINKED, actor=user, details="channel=telegram", commit=False
        )
        db.commit()
        db.refresh(link)
    preference = db.get(NotificationPreference, user.id)
    opt_in = bool(preference is not None and preference.telegram_opt_in)
    configured = _telegram_config(settings).is_configured
    return TelegramStatusOut(
        state=_telegram_state(
            configured=configured, link=link, pending=False, opt_in=opt_in, now=now
        ),
        configured=configured,
        linked=bool(link is not None and link.is_linked),
        masked_chat_id=(
            mask_chat_id(link.chat_id) if link is not None and link.chat_id is not None else None
        ),
        pending_confirmation=False,
        opt_in=opt_in,
        consent_at=preference.telegram_consent_at if preference else None,
        linked_at=link.linked_at if link else None,
    )


@router.put("/integrations/telegram/consent", response_model=ConsentOut)
def telegram_consent(
    payload: ConsentUpdate,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> ConsentOut:
    """Explicit opt-in/opt-out for Telegram delivery (audited)."""
    del request
    return _set_consent(
        db, user=user, channel=DeliveryChannel.TELEGRAM, opt_in=payload.opt_in, now=utc_now()
    )


@router.post("/integrations/telegram/test", response_model=ChannelTestOut)
def telegram_test(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> ChannelTestOut:
    """Queue an explicit test message to the own linked chat."""
    settings: Settings = request.app.state.settings
    _enforce_rate_limit("telegram-test", user, settings)
    if not _telegram_config(settings).is_configured:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Telegram-канал не настроен. Обратитесь к администратору.",
        )
    link = db.get(TelegramLink, user.id)
    if link is None or not link.is_linked:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Сначала привяжите Telegram: тест отправляется в ваш чат.",
        )
    row = schedule_channel_test(
        db,
        recipient_user_id=user.id,
        channel=DeliveryChannel.TELEGRAM,
        request_id=uuid.uuid4().hex,
    )
    assert row is not None  # fresh uuid dedupe key cannot collide
    record_event(
        db,
        AuditAction.TELEGRAM_TEST_QUEUED,
        actor=user,
        details=f"channel=telegram outbox={row.id}",
        commit=False,
    )
    db.commit()
    db.refresh(row)
    return ChannelTestOut(outbox_id=row.id, status=row.status.value)


# --- Email channel ------------------------------------------------------------


def _validate_email_or_422(value: EmailStr) -> str:
    from app.smtp import validate_mailbox

    try:
        return validate_mailbox(str(value), field="email")
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from None


@router.put("/integrations/email", response_model=EmailSetOut)
def set_email(
    payload: EmailSetRequest,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> EmailSetOut:
    """Set the own notification address and queue its verification letter.

    The address becomes usable only after the mailed token is confirmed;
    a previously verified address keeps working until then.
    """
    settings: Settings = request.app.state.settings
    _enforce_rate_limit("email-set", user, settings)
    if not _smtp_config(settings).is_configured:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Email-канал не настроен. Обратитесь к администратору.",
        )
    address = _validate_email_or_422(payload.email)
    now = utc_now()
    row = db.get(UserEmail, user.id)
    if row is None:
        row = UserEmail(user_id=user.id, created_at=now)
        db.add(row)
    raw_token = secrets.token_urlsafe(32)
    row.pending_email = address
    row.verification_token_hash = _token_hash(raw_token)
    row.verification_expires_at = now + timedelta(hours=settings.email_verification_ttl_hours)
    row.verification_attempts = 0
    row.updated_at = now
    queued = schedule_verification_email(
        db, pending_email=address, token=raw_token, request_id=uuid.uuid4().hex
    )
    record_event(
        db,
        AuditAction.EMAIL_ADDRESS_SET,
        actor=user,
        details=f"masked={mask_email(address)}",
        commit=False,
    )
    db.commit()
    assert row.verification_expires_at is not None
    return EmailSetOut(
        pending_email_masked=mask_email(address),
        expires_at=row.verification_expires_at,
        verification_queued=queued is not None,
    )


@router.post("/integrations/email/confirm", response_model=EmailConfirmOut)
def confirm_email(
    payload: EmailConfirmRequest,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> EmailConfirmOut:
    """Confirm the pending address with the token from the letter."""
    settings: Settings = request.app.state.settings
    _enforce_rate_limit("email-confirm", user, settings)
    del settings
    row = db.get(UserEmail, user.id)
    now = utc_now()
    if (
        row is None
        or row.pending_email is None
        or row.verification_token_hash is None
        or row.verification_expires_at is None
    ):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Нет адреса, ожидающего подтверждения.",
        )
    row.verification_attempts += 1
    if row.verification_attempts > EMAIL_VERIFICATION_MAX_ATTEMPTS:
        db.commit()
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Слишком много попыток. Запросите новый код.",
        )
    if not secrets.compare_digest(_token_hash(payload.token.strip()), row.verification_token_hash):
        db.commit()
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Неверный или просроченный код."
        )
    if row.verification_expires_at <= now:
        db.commit()
        raise HTTPException(
            status_code=status.HTTP_410_GONE,
            detail="Срок действия кода истёк. Запросите новый код.",
        )
    row.email = row.pending_email
    row.verified_at = now
    row.pending_email = None
    row.verification_token_hash = None
    row.verification_expires_at = None
    row.verification_attempts = 0
    row.last_error_class = None
    row.last_error_at = None
    row.updated_at = now
    record_event(
        db,
        AuditAction.EMAIL_VERIFIED,
        actor=user,
        details=f"masked={mask_email(row.email)}",
        commit=False,
    )
    db.commit()
    assert row.email is not None
    return EmailConfirmOut(verified=True, address_masked=mask_email(row.email))


@router.delete("/integrations/email", status_code=status.HTTP_204_NO_CONTENT)
def remove_email(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> Response:
    """Remove the own email address (verified and pending alike)."""
    del request
    row = db.get(UserEmail, user.id)
    if row is not None and (row.email is not None or row.pending_email is not None):
        row.email = None
        row.verified_at = None
        row.pending_email = None
        row.verification_token_hash = None
        row.verification_expires_at = None
        row.verification_attempts = 0
        row.last_error_class = None
        row.last_error_at = None
        row.updated_at = utc_now()
        record_event(
            db, AuditAction.EMAIL_REMOVED, actor=user, details="channel=email", commit=False
        )
        db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.put("/integrations/email/consent", response_model=ConsentOut)
def email_consent(
    payload: ConsentUpdate,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> ConsentOut:
    """Explicit opt-in/opt-out for email delivery (audited)."""
    del request
    return _set_consent(
        db, user=user, channel=DeliveryChannel.EMAIL, opt_in=payload.opt_in, now=utc_now()
    )


# --- Admin: configuration state, checks, test send ----------------------------


@router.get("/admin/integrations/channels", response_model=AdminChannelsOut)
def admin_channels(
    request: Request,
    admin: User = Depends(_admin_only),
) -> AdminChannelsOut:
    """Global channels overview (admin only, never secrets)."""
    del admin
    settings: Settings = request.app.state.settings
    telegram_cfg = _telegram_config(settings)
    smtp_cfg = _smtp_config(settings)
    return AdminChannelsOut(
        telegram=AdminTelegramInfo(
            enabled=telegram_cfg.enabled,
            configured=telegram_cfg.is_configured,
            bot_username=settings.telegram_bot_username.strip().lstrip("@") or None,
        ),
        smtp=AdminSmtpInfo(
            enabled=smtp_cfg.enabled,
            configured=smtp_cfg.is_configured,
            host=smtp_cfg.host or None,
            port=smtp_cfg.port,
            encryption=smtp_cfg.encryption,
            from_address=smtp_cfg.from_address or None,
        ),
    )


@router.post("/admin/integrations/telegram/check", response_model=ChannelCheckOut)
def admin_telegram_check(
    request: Request,
    db: Session = Depends(get_db),
    admin: User = Depends(_admin_only),
) -> ChannelCheckOut:
    """Live Telegram token check via getMe (no message is sent)."""
    settings: Settings = request.app.state.settings
    _enforce_rate_limit("admin-telegram-check", admin, settings)
    config = _telegram_config(settings)
    if not config.is_configured:
        detail = "Telegram-канал не настроен (нет токена или он отключён)."
        record_event(db, AuditAction.TELEGRAM_CHECKED, actor=admin, details="ok=false", commit=True)
        return ChannelCheckOut(ok=False, detail=detail, error_class="not_configured")
    result = _check_telegram(config)
    record_event(
        db,
        AuditAction.TELEGRAM_CHECKED,
        actor=admin,
        details=f"ok={str(result.ok).lower()} class={result.error_class or '-'}",
        commit=True,
    )
    if result.ok:
        return ChannelCheckOut(
            ok=True,
            detail="Токен действителен, Bot API отвечает.",
            bot_username=result.bot_username,
        )
    return ChannelCheckOut(
        ok=False,
        detail="Bot API недоступен или токен недействителен.",
        error_class=result.error_class,
    )


@router.post("/admin/integrations/smtp/check", response_model=ChannelCheckOut)
def admin_smtp_check(
    request: Request,
    db: Session = Depends(get_db),
    admin: User = Depends(_admin_only),
) -> ChannelCheckOut:
    """Live SMTP handshake check (connect + encryption + login, no mail)."""
    settings: Settings = request.app.state.settings
    _enforce_rate_limit("admin-smtp-check", admin, settings)
    config = _smtp_config(settings)
    if not config.is_configured:
        detail = "SMTP-канал не настроен (нет хоста/отправителя или он отключён)."
        record_event(db, AuditAction.SMTP_CHECKED, actor=admin, details="ok=false", commit=True)
        return ChannelCheckOut(ok=False, detail=detail, error_class="not_configured")
    result = _check_smtp(config)
    record_event(
        db,
        AuditAction.SMTP_CHECKED,
        actor=admin,
        details=f"ok={str(result.ok).lower()} class={result.error_class or '-'}",
        commit=True,
    )
    if result.ok:
        return ChannelCheckOut(ok=True, detail="SMTP-сервер отвечает, учётные данные приняты.")
    return ChannelCheckOut(
        ok=False,
        detail="SMTP-сервер недоступен или учётные данные отклонены.",
        error_class=result.error_class,
    )


@router.post("/admin/integrations/smtp/test-send", response_model=ChannelTestOut)
def admin_smtp_test_send(
    request: Request,
    db: Session = Depends(get_db),
    admin: User = Depends(_admin_only),
) -> ChannelTestOut:
    """Queue an explicit test letter to the admin's own verified address.

    The recipient is fixed (own verified mailbox) — arbitrary recipients
    from the payload are not accepted. Delivery stays asynchronous: the
    worker sends it and the history shows the honest provider outcome.
    """
    settings: Settings = request.app.state.settings
    _enforce_rate_limit("admin-smtp-test", admin, settings)
    if not _smtp_config(settings).is_configured:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="SMTP-канал не настроен.",
        )
    address = db.get(UserEmail, admin.id)
    if address is None or not address.is_verified:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Сначала подтвердите собственный адрес в разделе «Интеграции».",
        )
    row = schedule_channel_test(
        db,
        recipient_user_id=admin.id,
        channel=DeliveryChannel.EMAIL,
        request_id=uuid.uuid4().hex,
    )
    assert row is not None  # fresh uuid dedupe key cannot collide
    record_event(
        db,
        AuditAction.SMTP_TEST_QUEUED,
        actor=admin,
        details=f"channel=email outbox={row.id}",
        commit=False,
    )
    db.commit()
    db.refresh(row)
    return ChannelTestOut(outbox_id=row.id, status=row.status.value)
