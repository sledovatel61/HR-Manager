"""Telegram Bot API integration adapter (roadmap phase 9).

Production-ready Telegram Bot API adapter:
* Configuration via settings (token and API endpoint only from environment);
* Token is NEVER logged, serialized in error messages, or stored in plaintext;
* Single-use, expiring link token with 256-bit entropy (SHA-256 hashed in DB);
* Numeric chat_id (int64) storage, not username;
* Distinction of error classes: rate limit (429 retry_after), temporary (5xx,
  timeouts), permanent (400, 404), and channel revoked/blocked (403);
* Telegram accepted != human read;
* Network calls run outside long transactions;
* Russian text escaping and message length limiting (Telegram 4096 char limit).
"""

import contextlib
import hashlib
import html
import logging
import re
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any
from uuid import UUID

import httpx
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.config import Settings
from app.models import NotificationPreference, TelegramLinkToken, User
from app.utils import utc_now

logger = logging.getLogger(__name__)

MAX_TELEGRAM_MESSAGE_LENGTH = 4096
DEFAULT_RETRY_AFTER_SECONDS = 60


class TelegramError(Exception):
    """Base exception for Telegram integration errors (secrets redacted)."""

    def __init__(
        self, message: str, *, error_class: str = "telegram_error", error_code: str | None = None
    ) -> None:
        super().__init__(message)
        self.error_class = error_class
        self.error_code = error_code


class TelegramConfigError(TelegramError):
    """Bot token or configuration is missing or invalid."""

    def __init__(self, message: str = "Telegram bot is not configured") -> None:
        super().__init__(message, error_class="channel_not_configured", error_code="not_configured")


class TelegramRateLimitError(TelegramError):
    """HTTP 429 Too Many Requests with retry_after backoff."""

    def __init__(
        self,
        retry_after: int = DEFAULT_RETRY_AFTER_SECONDS,
        message: str = "Telegram rate limit exceeded",
    ) -> None:
        super().__init__(message, error_class="rate_limited", error_code="429")
        self.retry_after = max(1, retry_after)


class TelegramBlockedError(TelegramError):
    """HTTP 403: Bot was blocked by the user or chat is inaccessible."""

    def __init__(self, message: str = "Telegram bot was blocked by user") -> None:
        super().__init__(message, error_class="bot_blocked", error_code="403")


class TelegramPermanentError(TelegramError):
    """Permanent error (HTTP 400 bad request, 404 not found, invalid chat_id)."""

    def __init__(self, message: str, error_code: str | None = "400") -> None:
        super().__init__(message, error_class="permanent_error", error_code=error_code)


class TelegramTemporaryError(TelegramError):
    """Temporary error (HTTP 5xx, timeout, network glitch) eligible for retry."""

    def __init__(
        self, message: str, error_code: str | None = "500", retry_after: int | None = None
    ) -> None:
        super().__init__(message, error_class="temporary_error", error_code=error_code)
        self.retry_after = retry_after


@dataclass(frozen=True)
class TelegramSendResult:
    accepted: bool
    provider_message_id: str | None


def _redact_token(text: str, token: str) -> str:
    """Ensure bot token never leaks in logs, traces or exception messages."""
    if not token:
        return text
    # Match standard bot token patterns (e.g. 123456:ABC-DEF...) or raw token
    redacted = text.replace(token, "[REDACTED_BOT_TOKEN]")
    redacted = re.sub(r"/bot\d+:[A-Za-z0-9_-]+", "/bot[REDACTED_BOT_TOKEN]", redacted)
    return redacted


def format_telegram_message(title: str, body: str | None = None) -> str:
    """Format notification message for Telegram with safe HTML escaping."""
    safe_title = html.escape(title.strip())
    if body and body.strip():
        safe_body = html.escape(body.strip())
        text = f"<b>{safe_title}</b>\n\n{safe_body}"
    else:
        text = f"<b>{safe_title}</b>"
    if len(text) > MAX_TELEGRAM_MESSAGE_LENGTH:
        text = text[: MAX_TELEGRAM_MESSAGE_LENGTH - 3] + "..."
    return text


def get_me(settings: Settings) -> dict[str, Any]:
    """Call Telegram Bot API getMe to test configuration without sending a message."""
    token = settings.telegram_bot_token.strip()
    if not token:
        raise TelegramConfigError("TELEGRAM_BOT_TOKEN is not configured")

    url = f"{settings.telegram_api_base_url.rstrip('/')}/bot{token}/getMe"
    try:
        with httpx.Client(timeout=settings.telegram_timeout_seconds) as client:
            response = client.get(url)
    except httpx.TimeoutException as exc:
        raise TelegramTemporaryError(
            _redact_token(f"Telegram connection timed out: {exc}", token),
            error_code="timeout",
        ) from exc
    except httpx.RequestError as exc:
        raise TelegramTemporaryError(
            _redact_token(f"Telegram network request failed: {exc}", token),
            error_code="network_error",
        ) from exc

    if response.status_code == 200:
        data = response.json()
        if data.get("ok"):
            result = data.get("result", {})
            return {
                "ok": True,
                "id": result.get("id"),
                "username": result.get("username"),
                "first_name": result.get("first_name"),
            }
        raise TelegramPermanentError(data.get("description", "Unknown Telegram error"))

    if response.status_code == 401 or response.status_code == 404:
        raise TelegramPermanentError(
            _redact_token(f"Invalid Telegram bot token ({response.status_code})", token),
            error_code=str(response.status_code),
        )
    if response.status_code == 429:
        retry_after = DEFAULT_RETRY_AFTER_SECONDS
        with contextlib.suppress(Exception):
            retry_after = int(
                response.json()
                .get("parameters", {})
                .get("retry_after", DEFAULT_RETRY_AFTER_SECONDS)
            )
        raise TelegramRateLimitError(retry_after=retry_after)
    if 500 <= response.status_code <= 599:
        raise TelegramTemporaryError(
            f"Telegram server error ({response.status_code})",
            error_code=str(response.status_code),
        )

    raise TelegramPermanentError(
        _redact_token(f"Telegram getMe failed ({response.status_code})", token),
        error_code=str(response.status_code),
    )


def send_message(
    settings: Settings,
    *,
    chat_id: int,
    text: str,
    parse_mode: str | None = "HTML",
    disable_web_page_preview: bool = True,
) -> TelegramSendResult:
    """Send a message via Telegram Bot API sendMessage.

    Returns TelegramSendResult with provider_message_id on success.
    Raises typed TelegramError subclasses on failure.
    """
    token = settings.telegram_bot_token.strip()
    if not token:
        raise TelegramConfigError("TELEGRAM_BOT_TOKEN is not configured")

    url = f"{settings.telegram_api_base_url.rstrip('/')}/bot{token}/sendMessage"
    payload: dict[str, Any] = {
        "chat_id": chat_id,
        "text": text,
        "disable_web_page_preview": disable_web_page_preview,
    }
    if parse_mode:
        payload["parse_mode"] = parse_mode

    try:
        with httpx.Client(timeout=settings.telegram_timeout_seconds) as client:
            response = client.post(url, json=payload)
    except httpx.TimeoutException as exc:
        logger.warning("Telegram sendMessage timeout for chat_id=%s", chat_id)
        raise TelegramTemporaryError(
            _redact_token(f"Telegram timeout: {exc}", token),
            error_code="timeout",
        ) from exc
    except httpx.RequestError as exc:
        logger.warning("Telegram sendMessage network error for chat_id=%s: %s", chat_id, exc)
        raise TelegramTemporaryError(
            _redact_token(f"Telegram network error: {exc}", token),
            error_code="network_error",
        ) from exc

    if response.status_code == 200:
        data = response.json()
        if data.get("ok"):
            msg_id = data.get("result", {}).get("message_id")
            return TelegramSendResult(
                accepted=True, provider_message_id=str(msg_id) if msg_id is not None else None
            )
        raise TelegramPermanentError(data.get("description", "Unknown Telegram response"))

    if response.status_code == 429:
        retry_after = DEFAULT_RETRY_AFTER_SECONDS
        try:
            params = response.json().get("parameters", {})
            retry_after = int(params.get("retry_after", DEFAULT_RETRY_AFTER_SECONDS))
        except Exception:
            pass
        logger.warning("Telegram rate limited (429), retry_after=%s", retry_after)
        raise TelegramRateLimitError(retry_after=retry_after)

    if response.status_code == 403:
        logger.warning("Telegram bot blocked by chat_id=%s", chat_id)
        raise TelegramBlockedError("Bot was blocked by user or deactivated")

    if response.status_code in (400, 404):
        desc = ""
        with contextlib.suppress(Exception):
            desc = response.json().get("description", "")
        logger.warning(
            "Telegram permanent error %s for chat_id=%s: %s", response.status_code, chat_id, desc
        )
        raise TelegramPermanentError(
            f"Telegram error {response.status_code}: {desc}"
            if desc
            else f"Telegram error {response.status_code}",
            error_code=str(response.status_code),
        )

    if 500 <= response.status_code <= 599:
        logger.warning("Telegram server error %s", response.status_code)
        raise TelegramTemporaryError(
            f"Telegram server error ({response.status_code})",
            error_code=str(response.status_code),
        )

    raise TelegramPermanentError(
        f"Telegram request failed ({response.status_code})",
        error_code=str(response.status_code),
    )


# --- Telegram Account Linking Helpers ----------------------------------------


def hash_link_token(token: str) -> str:
    """Compute SHA-256 hash of a raw linking token."""
    return hashlib.sha256(token.strip().encode("utf-8")).hexdigest()


def generate_link_token(
    db: Session,
    *,
    user_id: UUID,
    ttl_minutes: int = 15,
    now: datetime | None = None,
) -> tuple[str, TelegramLinkToken]:
    """Generate a single-use expiring linking token with 256 bits of entropy.

    Only the SHA-256 hash is saved to the database.
    """
    now = now or utc_now()
    raw_token = secrets.token_urlsafe(32)
    token_hash = hash_link_token(raw_token)
    expires_at = now + timedelta(minutes=ttl_minutes)

    token_row = TelegramLinkToken(
        user_id=user_id,
        token_hash=token_hash,
        expires_at=expires_at,
        created_at=now,
    )
    db.add(token_row)
    db.flush()
    return raw_token, token_row


def confirm_link_token(
    db: Session,
    *,
    token: str,
    chat_id: int,
    username: str | None = None,
    now: datetime | None = None,
) -> tuple[User, TelegramLinkToken]:
    """Validate a single-use linking token and bind the numeric chat_id.

    Raises ValueError / TelegramPermanentError on invalid or expired token.
    """
    now = now or utc_now()
    token_hash = hash_link_token(token)

    token_row = db.execute(
        select(TelegramLinkToken)
        .where(TelegramLinkToken.token_hash == token_hash)
        .with_for_update()
    ).scalar_one_or_none()

    if token_row is None:
        raise ValueError("Недействительный или несуществующий код привязки.")
    if token_row.used_at is not None:
        raise ValueError("Этот код привязки уже был использован.")
    if token_row.expires_at <= now:
        raise ValueError("Срок действия кода привязки истёк.")

    user = db.get(User, token_row.user_id)
    if user is None or not user.is_active:
        raise ValueError("Пользователь не найден или деактивирован.")

    # Mark token as used.
    token_row.used_at = now

    # Invalidate other pending tokens for this user.
    db.execute(
        update(TelegramLinkToken)
        .where(
            TelegramLinkToken.user_id == user.id,
            TelegramLinkToken.used_at.is_(None),
            TelegramLinkToken.id != token_row.id,
        )
        .values(used_at=now)
    )

    # Load or create NotificationPreference.
    from app.notification_service import preference_for

    pref = preference_for(db, user.id, "Europe/Moscow")
    if pref.user_id not in [p.user_id for p in db.new if isinstance(p, NotificationPreference)]:
        # Ensure row is attached to session.
        existing_pref = db.get(NotificationPreference, user.id)
        if existing_pref is None:
            db.add(pref)
        else:
            pref = existing_pref

    pref.telegram_chat_id = chat_id
    pref.telegram_username = username[:64] if username else None
    pref.telegram_linked_at = now
    pref.telegram_opt_in = True
    pref.telegram_consent_at = now
    pref.telegram_consent_source = "link_token"
    pref.telegram_consent_policy_version = "1.0"
    if "telegram" not in pref.enabled_channels:
        pref.enabled_channels = [*pref.enabled_channels, "telegram"]
    pref.updated_at = now

    db.flush()
    return user, token_row


def unlink_telegram(db: Session, *, user_id: UUID, now: datetime | None = None) -> bool:
    """Unlink Telegram account and revoke consent for a user."""
    now = now or utc_now()
    pref = db.get(NotificationPreference, user_id)
    if pref is None or pref.telegram_chat_id is None:
        return False

    pref.telegram_chat_id = None
    pref.telegram_username = None
    pref.telegram_linked_at = None
    pref.telegram_opt_in = False
    pref.telegram_consent_at = None
    pref.telegram_consent_source = None
    if "telegram" in pref.enabled_channels:
        pref.enabled_channels = [c for c in pref.enabled_channels if c != "telegram"]
    pref.updated_at = now

    # Invalidate any remaining tokens.
    db.execute(
        update(TelegramLinkToken)
        .where(
            TelegramLinkToken.user_id == user_id,
            TelegramLinkToken.used_at.is_(None),
        )
        .values(used_at=now)
    )
    db.flush()
    return True
