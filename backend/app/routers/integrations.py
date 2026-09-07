"""Integrations router: Telegram Bot API linking, universal SMTP, status, and admin test probes."""

import logging
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from sqlalchemy.orm import Session

from app.audit import record_event
from app.config import Settings
from app.db import get_db
from app.deps import get_current_user, require_roles
from app.models import (
    AuditAction,
    NotificationPreference,
    User,
    UserRole,
)
from app.rate_limiting import SlidingWindowRateLimiter
from app.schemas import (
    AdminSmtpTestConnectionOut,
    AdminTelegramTestConnectionOut,
    AdminTestSendOut,
    AdminTestSendRequest,
    IntegrationChannelStatus,
    IntegrationStatusResponse,
    TelegramLinkConfirmRequest,
    TelegramLinkInitiateOut,
)
from app.smtp_adapter import (
    SmtpError,
)
from app.smtp_adapter import (
    send_email as smtp_send_email,
)
from app.smtp_adapter import (
    verify_connection as smtp_verify_connection,
)
from app.telegram_adapter import (
    TelegramError,
    confirm_link_token,
    format_telegram_message,
    generate_link_token,
    unlink_telegram,
)
from app.telegram_adapter import (
    get_me as telegram_get_me,
)
from app.telegram_adapter import (
    send_message as telegram_send_message,
)
from app.utils import normalize_email

logger = logging.getLogger(__name__)

router = APIRouter(tags=["integrations"])

# Rate limiters for sensitive integration endpoints
_link_initiate_limiter = SlidingWindowRateLimiter(limit=10, window_seconds=300)
_test_send_limiter = SlidingWindowRateLimiter(limit=10, window_seconds=300)

_admin_only = require_roles(UserRole.ADMIN)


def _client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


@router.get(
    "/integrations/status",
    response_model=IntegrationStatusResponse,
    summary="Status of all integration channels",
)
def get_integrations_status(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> IntegrationStatusResponse:
    """Return communication channels status for the current user without exposing secrets."""
    settings: Settings = request.app.state.settings
    pref = db.get(NotificationPreference, user.id)

    # 1. Telegram status evaluation
    telegram_system_configured = bool(settings.telegram_bot_token.strip())
    telegram_linked = bool(pref and pref.telegram_chat_id is not None)
    telegram_opt_in = bool(pref and pref.telegram_opt_in)
    telegram_has_consent = bool(pref and pref.telegram_consent_at is not None)

    if not telegram_system_configured:
        telegram_state = "not_configured"
    elif not telegram_linked:
        telegram_state = "pending_confirmation"
    elif not telegram_opt_in:
        telegram_state = "revoked"
    else:
        telegram_state = "working"

    telegram_details: dict[str, Any] = {
        "bot_username": settings.telegram_bot_username or None,
    }
    if pref and pref.telegram_username:
        telegram_details["username"] = pref.telegram_username
    if pref and pref.telegram_linked_at:
        telegram_details["linked_at"] = pref.telegram_linked_at.isoformat()
    if pref and pref.telegram_chat_id is not None:
        # Mask chat_id to keep internal IDs safe
        chat_str = str(pref.telegram_chat_id)
        telegram_details["chat_id_masked"] = (
            chat_str[:3] + "..." + chat_str[-2:] if len(chat_str) > 5 else "***"
        )

    # 2. Email status evaluation
    email_system_configured = bool(settings.smtp_host.strip())
    email_address = pref.email_address if pref else None
    email_opt_in = bool(pref and pref.email_opt_in)
    email_has_consent = bool(pref and pref.email_consent_at is not None)

    if not email_system_configured or not email_address:
        email_state = "not_configured"
    elif not email_opt_in:
        email_state = "revoked"
    else:
        email_state = "working"

    email_details: dict[str, Any] = {}
    if email_address:
        email_details["address"] = email_address
    if pref and pref.email_consent_at:
        email_details["consent_at"] = pref.email_consent_at.isoformat()

    return IntegrationStatusResponse(
        telegram=IntegrationChannelStatus(
            status=telegram_state,
            configured_in_system=telegram_system_configured,
            linked=telegram_linked,
            opt_in=telegram_opt_in,
            has_consent=telegram_has_consent,
            details=telegram_details,
        ),
        email=IntegrationChannelStatus(
            status=email_state,
            configured_in_system=email_system_configured,
            linked=bool(email_address),
            opt_in=email_opt_in,
            has_consent=email_has_consent,
            details=email_details,
        ),
        in_app=IntegrationChannelStatus(
            status="working",
            configured_in_system=True,
            linked=True,
            opt_in=True,
            has_consent=True,
            details={"type": "in_app_notification_center"},
        ),
    )


# --- Telegram Linking Flow ---------------------------------------------------


@router.post(
    "/integrations/telegram/link/initiate",
    response_model=TelegramLinkInitiateOut,
    summary="Start Telegram account linking",
)
def initiate_telegram_link(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> TelegramLinkInitiateOut:
    """Generate a single-use expiring token and deep link for Telegram."""
    settings: Settings = request.app.state.settings
    if not settings.telegram_bot_token.strip():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Интеграция с Telegram не настроена на сервере.",
        )

    ip = _client_ip(request)
    limit_res = _link_initiate_limiter.check(f"{user.id}:{ip}")
    if not limit_res.allowed:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=(
                "Слишком много запросов привязки. "
                f"Повторите попытку через {limit_res.retry_after_seconds} с."
            ),
        )

    raw_token, token_row = generate_link_token(
        db,
        user_id=user.id,
        ttl_minutes=settings.telegram_link_ttl_minutes,
    )
    db.commit()

    bot_username = settings.telegram_bot_username.strip() or None
    if not bot_username:
        try:
            bot_info = telegram_get_me(settings)
            bot_username = bot_info.get("username")
        except Exception:
            bot_username = None

    deep_link = f"https://t.me/{bot_username}?start={raw_token}" if bot_username else None

    record_event(
        db,
        AuditAction.TELEGRAM_LINK_INITIATED,
        actor=user,
        ip_address=ip,
        details="telegram link token generated",
    )

    return TelegramLinkInitiateOut(
        token=raw_token,
        bot_username=bot_username,
        deep_link=deep_link,
        expires_at=token_row.expires_at,
    )


@router.post(
    "/integrations/telegram/link/confirm",
    response_model=IntegrationChannelStatus,
    summary="Confirm Telegram account linking",
)
def confirm_telegram_link(
    payload: TelegramLinkConfirmRequest,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> IntegrationChannelStatus:
    """Validate token and bind Telegram numeric chat_id to current user."""
    settings: Settings = request.app.state.settings
    ip = _client_ip(request)

    try:
        confirmed_user, _ = confirm_link_token(
            db,
            token=payload.token,
            chat_id=payload.chat_id,
            username=payload.username,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc

    # IDOR check: linking token must belong to the authenticated caller
    if confirmed_user.id != user.id:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Этот код привязки был выдан для другой учётной записи.",
        )

    # Optional welcome notification via Telegram
    if settings.telegram_bot_token.strip():
        try:
            telegram_send_message(
                settings,
                chat_id=payload.chat_id,
                text=(
                    "<b>HR Manager</b>\n\n"
                    "Telegram успешно привязан к вашей учетной записи для получения уведомлений."
                ),
            )
        except Exception as exc:
            logger.warning("Could not send Telegram welcome message: %s", exc)

    record_event(
        db,
        AuditAction.TELEGRAM_LINK_CONFIRMED,
        actor=user,
        ip_address=ip,
        details=f"telegram linked chat_id={payload.chat_id}",
    )
    db.commit()

    return IntegrationChannelStatus(
        status="working",
        configured_in_system=True,
        linked=True,
        opt_in=True,
        has_consent=True,
        details={
            "username": payload.username,
            "bot_username": settings.telegram_bot_username or None,
        },
    )


@router.post("/integrations/telegram/unlink", summary="Unlink Telegram account")
def unlink_user_telegram(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> dict[str, Any]:
    """Unlink Telegram account and revoke notification consent for current user."""
    ip = _client_ip(request)
    unlinked = unlink_telegram(db, user_id=user.id)
    if not unlinked:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Telegram не был привязан к вашей учётной записи.",
        )

    record_event(
        db,
        AuditAction.TELEGRAM_UNLINKED,
        actor=user,
        ip_address=ip,
        details="telegram unlinked and consent revoked",
    )
    db.commit()
    return {"ok": True, "message": "Telegram успешно отвязан."}


@router.post("/integrations/telegram/webhook", summary="Telegram Bot webhook receiver")
def telegram_webhook(
    update: dict[str, Any],
    request: Request,
    db: Session = Depends(get_db),
    x_telegram_bot_api_secret_token: str | None = Header(default=None),
) -> dict[str, Any]:
    """Handle incoming Telegram Bot updates (such as /start <token>)."""
    settings: Settings = request.app.state.settings

    if (
        settings.telegram_webhook_secret
        and x_telegram_bot_api_secret_token != settings.telegram_webhook_secret
    ):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Invalid secret token")

    message = update.get("message")
    if not message or not isinstance(message, dict):
        return {"ok": True}

    text = message.get("text", "")
    chat = message.get("chat", {})
    from_user = message.get("from", {})
    chat_id = chat.get("id")

    if text.startswith("/start ") and chat_id:
        raw_token = text[7:].strip()
        if raw_token:
            try:
                user, _ = confirm_link_token(
                    db,
                    token=raw_token,
                    chat_id=chat_id,
                    username=from_user.get("username"),
                )
                record_event(
                    db,
                    AuditAction.TELEGRAM_LINK_CONFIRMED,
                    actor=user,
                    details="telegram link confirmed via webhook",
                )
                db.commit()
                telegram_send_message(
                    settings,
                    chat_id=chat_id,
                    text="<b>HR Manager</b>\n\nTelegram успешно привязан к вашей учетной записи!",
                )
            except Exception as exc:
                logger.warning("Telegram webhook linking failed: %s", exc)

    return {"ok": True}


# --- Admin-only Integration Management and Diagnostics -----------------------


@router.post(
    "/admin/integrations/telegram/test-connection",
    response_model=AdminTelegramTestConnectionOut,
    summary="Test Telegram Bot API connection",
)
def admin_test_telegram_connection(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(_admin_only),
) -> AdminTelegramTestConnectionOut:
    """Admin-only probe of Telegram Bot API getMe without sending messages or leaking token."""
    settings: Settings = request.app.state.settings
    ip = _client_ip(request)

    record_event(
        db,
        AuditAction.INTEGRATION_CONNECTION_TESTED,
        actor=user,
        ip_address=ip,
        details="channel=telegram",
    )

    try:
        res = telegram_get_me(settings)
        return AdminTelegramTestConnectionOut(
            ok=True,
            bot_id=res.get("id"),
            bot_username=res.get("username"),
            first_name=res.get("first_name"),
        )
    except TelegramError as exc:
        return AdminTelegramTestConnectionOut(
            ok=False,
            error=str(exc),
        )
    except Exception as exc:
        return AdminTelegramTestConnectionOut(
            ok=False,
            error=f"Ошибка подключения к Telegram: {exc}",
        )


@router.post(
    "/admin/integrations/smtp/test-connection",
    response_model=AdminSmtpTestConnectionOut,
    summary="Test SMTP connection and auth",
)
def admin_test_smtp_connection(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(_admin_only),
) -> AdminSmtpTestConnectionOut:
    """Admin-only handshake probe of SMTP server without sending email or leaking password."""
    settings: Settings = request.app.state.settings
    ip = _client_ip(request)

    record_event(
        db,
        AuditAction.INTEGRATION_CONNECTION_TESTED,
        actor=user,
        ip_address=ip,
        details="channel=email",
    )

    try:
        res = smtp_verify_connection(settings)
        return AdminSmtpTestConnectionOut(
            ok=True,
            host=res["host"],
            port=res["port"],
            use_tls=res["use_tls"],
            use_starttls=res["use_starttls"],
            authenticated=res["authenticated"],
        )
    except SmtpError as exc:
        return AdminSmtpTestConnectionOut(
            ok=False,
            host=settings.smtp_host,
            port=settings.smtp_port,
            use_tls=settings.smtp_use_tls,
            use_starttls=settings.smtp_use_starttls,
            authenticated=bool(settings.smtp_username.strip()),
            error=str(exc),
        )
    except Exception as exc:
        return AdminSmtpTestConnectionOut(
            ok=False,
            host=settings.smtp_host,
            port=settings.smtp_port,
            use_tls=settings.smtp_use_tls,
            use_starttls=settings.smtp_use_starttls,
            authenticated=bool(settings.smtp_username.strip()),
            error=f"Ошибка проверки SMTP: {exc}",
        )


@router.post(
    "/admin/integrations/test-send",
    response_model=AdminTestSendOut,
    summary="Send an explicit test notification",
)
def admin_test_send(
    payload: AdminTestSendRequest,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(_admin_only),
) -> AdminTestSendOut:
    """Send an explicit test message through Telegram or SMTP."""
    settings: Settings = request.app.state.settings
    ip = _client_ip(request)

    limit_res = _test_send_limiter.check(f"{user.id}:{ip}")
    if not limit_res.allowed:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=(
                "Превышен лимит тестовых отправок. "
                f"Повторите через {limit_res.retry_after_seconds} с."
            ),
        )

    pref = db.get(NotificationPreference, user.id)

    if payload.channel == "telegram":
        chat_id: int | None = None
        if payload.recipient:
            try:
                chat_id = int(payload.recipient.strip())
            except ValueError:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail="Для Telegram получатель должен быть числовым chat_id.",
                ) from None
        elif pref and pref.telegram_chat_id is not None:
            chat_id = pref.telegram_chat_id
        else:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Укажите chat_id получателя или привяжите Telegram в настройках профиля.",
            )

        title = payload.subject or "Тестовое уведомление HR Manager"
        body = (
            payload.body
            or "Это проверочное сообщение, отправленное администратором из панели настроек."
        )
        msg_text = format_telegram_message(title, body)

        try:
            res = telegram_send_message(settings, chat_id=chat_id, text=msg_text)
        except TelegramError as exc:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=f"Ошибка отправки в Telegram: {exc}",
            ) from exc

        record_event(
            db,
            AuditAction.INTEGRATION_TEST_SENT,
            actor=user,
            ip_address=ip,
            details=f"channel=telegram chat_id={chat_id}",
        )
        db.commit()

        return AdminTestSendOut(
            ok=True,
            channel="telegram",
            provider_message_id=res.provider_message_id,
            message="Тестовое сообщение принято Telegram Bot API.",
        )

    if payload.channel == "email":
        to_email: str | None = None
        if payload.recipient:
            norm = normalize_email(payload.recipient.strip())
            if norm is None or "@" not in norm or "." not in norm.split("@")[-1]:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail="Некорректный адрес электронной почты получателя.",
                )
            to_email = norm
        elif pref and pref.email_address:
            to_email = pref.email_address

        if not to_email:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Укажите email получателя или настройте адрес в настройках профиля.",
            )

        subject = payload.subject or "Тестовое уведомление HR Manager"
        body = (
            payload.body
            or "Это проверочное письмо, отправленное администратором из панели настроек."
        )

        try:
            res_email = smtp_send_email(
                settings,
                to_email=to_email,
                subject=subject,
                body=body,
            )
        except SmtpError as exc:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=f"Ошибка отправки через SMTP: {exc}",
            ) from exc

        record_event(
            db,
            AuditAction.INTEGRATION_TEST_SENT,
            actor=user,
            ip_address=ip,
            details=f"channel=email to={to_email}",
        )
        db.commit()

        return AdminTestSendOut(
            ok=True,
            channel="email",
            provider_message_id=res_email.provider_message_id,
            message="Тестовое письмо принято SMTP-сервером.",
        )

    raise HTTPException(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        detail=f"Неизвестный канал: {payload.channel}",
    )
