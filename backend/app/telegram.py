"""Telegram Bot API adapter (phase 9).

Production adapter over the HTTPS Bot API with stdlib-only HTTP (no new
dependencies). Design rules:

* the bot token travels only inside the request URL and is NEVER logged,
  persisted or returned — log lines carry safe error classes only;
* every call has a timeout and a response-size cap;
* Telegram ``429`` honors ``retry_after`` (bounded); 5xx/timeouts/network
  failures are temporary; 400/401/403/404 are permanent;
* provider ``accepted`` (Bot API ``ok: true``) is recorded as ``accepted``
  — it never means «delivered» and never means «read by a human»;
* ``sendMessage`` has no provider-side idempotency key: an ambiguous
  response (HTTP 200 with an unparseable body) is retried bounded and may
  theoretically duplicate after a crash between Telegram accepting the
  message and us recording the attempt — documented, not hidden.

Linking uses ``getUpdates`` (short poll, persisted offset): the user opens
``https://t.me/<bot>?start=<token>`` voluntarily and presses Start; the
confirm endpoint looks for that ``/start`` event. No webhook is required,
so the stack works behind NAT without a public URL.
"""

from __future__ import annotations

import json
import logging
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal

from app.config import TELEGRAM_MAX_MESSAGE_CHARS, TELEGRAM_MAX_RESPONSE_BYTES

logger = logging.getLogger(__name__)

SendOutcome = Literal["accepted", "temp_error", "perm_error"]

# Safe error classes (logged, stored, shown to users/admins). No provider
# payloads, no message text, no tokens.
RATE_LIMITED = "telegram_rate_limited"
TIMEOUT = "telegram_timeout"
NETWORK = "telegram_network"
SERVER = "telegram_server"
BAD_REQUEST = "telegram_bad_request"
CHAT_NOT_FOUND = "telegram_chat_not_found"
BLOCKED = "telegram_blocked"
DEACTIVATED = "telegram_deactivated"
FORBIDDEN = "telegram_forbidden"
UNAUTHORIZED = "telegram_unauthorized"
BAD_RESPONSE = "telegram_bad_response"

#: Permanent failures that prove the binding itself is dead (the worker
#: auto-revokes the Telegram link on these, with an audit record).
REVOKING_ERROR_CLASSES = frozenset({BLOCKED, DEACTIVATED, CHAT_NOT_FOUND})

#: Upper bound for a honored Telegram ``retry_after`` (seconds).
MAX_RETRY_AFTER_S = 3600


@dataclass(frozen=True)
class TelegramConfig:
    """Bot API connection parameters (built from Settings per call)."""

    enabled: bool
    bot_token: str
    api_base_url: str
    timeout_s: float

    @property
    def is_configured(self) -> bool:
        return self.enabled and bool(self.bot_token) and bool(self.api_base_url)


@dataclass(frozen=True)
class TelegramSendResult:
    outcome: SendOutcome
    provider_message_id: str | None = None
    error_code: str | None = None
    error_class: str | None = None
    retry_after_s: int | None = None


@dataclass(frozen=True)
class TelegramStartUpdate:
    update_id: int
    chat_id: int
    token: str


@dataclass(frozen=True)
class TelegramUpdatesResult:
    ok: bool
    updates: tuple[TelegramStartUpdate, ...] = ()
    max_update_id: int | None = None
    error_class: str | None = None


@dataclass(frozen=True)
class TelegramCheckResult:
    ok: bool
    bot_username: str | None = None
    error_class: str | None = None


# Transport: (url, json-payload, timeout) -> (http-status, raw-body).
# The default uses urllib; tests inject fakes. The URL embeds the bot token
# and must never be logged by any transport implementation.
HttpPost = Callable[[str, dict, float], tuple[int, bytes]]


def config_from_settings(settings: object) -> TelegramConfig:
    """Build the adapter config from application Settings."""
    from app.config import Settings

    assert isinstance(settings, Settings)
    return TelegramConfig(
        enabled=settings.telegram_enabled,
        bot_token=settings.telegram_bot_token,
        api_base_url=settings.telegram_api_base_url.rstrip("/"),
        timeout_s=settings.telegram_timeout_s,
    )


def _default_http_post(url: str, payload: dict, timeout_s: float) -> tuple[int, bytes]:
    data = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        url, data=data, headers={"Content-Type": "application/json"}, method="POST"
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout_s) as response:
            status = int(response.status)
            body = response.read(TELEGRAM_MAX_RESPONSE_BYTES + 1)
    except urllib.error.HTTPError as exc:
        # HTTPError still carries the (small) JSON error body.
        try:
            body = exc.read(TELEGRAM_MAX_RESPONSE_BYTES + 1)
        except Exception:
            body = b""
        return int(exc.code), bytes(body)
    except TimeoutError:
        raise TelegramTimeoutError from None
    except (urllib.error.URLError, OSError, ValueError):
        raise TelegramNetworkError from None
    return status, bytes(body)


class TelegramTimeoutError(Exception):
    """The Bot API call exceeded its timeout (temporary)."""


class TelegramNetworkError(Exception):
    """DNS/connection/TLS failure talking to the Bot API (temporary)."""


def _parse_json_body(body: bytes) -> dict | None:
    if len(body) > TELEGRAM_MAX_RESPONSE_BYTES:
        return None
    try:
        parsed = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return None
    return parsed if isinstance(parsed, dict) else None


def _retry_after_from(payload: dict | None) -> int | None:
    if not payload:
        return None
    params = payload.get("parameters")
    if not isinstance(params, dict):
        return None
    value = params.get("retry_after")
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    seconds = int(value)
    if seconds < 0:
        return None
    return min(seconds, MAX_RETRY_AFTER_S)


def _classify_send_error(status: int, payload: dict | None) -> tuple[str, str]:
    """Map an HTTP status + Bot API body to (error_code, error_class)."""
    description = ""
    if payload:
        raw = payload.get("description")
        if isinstance(raw, str):
            description = raw.lower()
    code = f"http_{status}"
    if status == 429:
        return code, RATE_LIMITED
    if status in (401, 404):
        return code, UNAUTHORIZED
    if status == 403:
        if "bot was blocked" in description:
            return code, BLOCKED
        if "user is deactivated" in description:
            return code, DEACTIVATED
        return code, FORBIDDEN
    if status == 400:
        if "chat not found" in description:
            return code, CHAT_NOT_FOUND
        return code, BAD_REQUEST
    if 500 <= status <= 599:
        return code, SERVER
    return code, BAD_RESPONSE if status == 200 else SERVER


def _is_temporary(error_class: str) -> bool:
    return error_class in (RATE_LIMITED, TIMEOUT, NETWORK, SERVER, BAD_RESPONSE)


def truncate_utf16(text: str, limit: int = TELEGRAM_MAX_MESSAGE_CHARS) -> str:
    """Truncate to the Bot API text limit (UTF-16 code units, not chars)."""
    if len(text.encode("utf-16-le")) // 2 <= limit:
        return text
    suffix = "…"
    budget = limit - len(suffix.encode("utf-16-le")) // 2
    kept: list[str] = []
    used = 0
    for char in text:
        width = len(char.encode("utf-16-le")) // 2
        if used + width > budget:
            break
        kept.append(char)
        used += width
    return "".join(kept).rstrip() + suffix


def render_message_text(title: str, body: str | None) -> str:
    """Render one notification as plain Telegram text (no parse mode).

    Plain text needs no entity escaping and cannot break on user content.
    The honesty footer always survives truncation (the body is budgeted
    first); provider acceptance is never presented as reading.
    """
    footer = "—\nHR Manager: принятие сообщения Telegram не означает прочтение."
    clean_title = " ".join(title.split())
    head = clean_title if not (body and body.strip()) else f"{clean_title}\n\n{body.strip()}"
    footer_units = len(("\n\n" + footer).encode("utf-16-le")) // 2
    return truncate_utf16(head, TELEGRAM_MAX_MESSAGE_CHARS - footer_units) + "\n\n" + footer


def send_message(
    config: TelegramConfig,
    *,
    chat_id: int,
    title: str,
    body: str | None,
    http_post: HttpPost | None = None,
) -> TelegramSendResult:
    """Send one message via ``sendMessage`` (bounded, classified).

    Returns ``accepted`` only on Bot API ``ok: true`` with the provider
    ``message_id`` when present (never fabricated). Never raises for
    expected conditions; never logs the token, chat id or message text.
    """
    post = http_post or _default_http_post
    text = render_message_text(title, body)
    if not text:
        return TelegramSendResult(
            outcome="perm_error", error_code="empty_text", error_class=BAD_REQUEST
        )
    url = f"{config.api_base_url}/bot{config.bot_token}/sendMessage"
    try:
        status, raw = post(url, {"chat_id": chat_id, "text": text}, config.timeout_s)
    except TelegramTimeoutError:
        logger.warning("telegram sendMessage timeout")
        return TelegramSendResult(outcome="temp_error", error_code="timeout", error_class=TIMEOUT)
    except TelegramNetworkError:
        logger.warning("telegram sendMessage network error")
        return TelegramSendResult(outcome="temp_error", error_code="network", error_class=NETWORK)
    except Exception:
        logger.warning("telegram sendMessage transport failed", exc_info=True)
        return TelegramSendResult(outcome="temp_error", error_code="transport", error_class=NETWORK)

    payload = _parse_json_body(raw)
    if status == 200 and payload is not None and payload.get("ok") is True:
        message_id: str | None = None
        result = payload.get("result")
        if isinstance(result, dict):
            raw_id = result.get("message_id")
            if isinstance(raw_id, bool):
                raw_id = None
            if isinstance(raw_id, (int, float)):
                message_id = str(int(raw_id))
            elif isinstance(raw_id, str) and raw_id.strip():
                message_id = raw_id.strip()[:64]
        return TelegramSendResult(outcome="accepted", provider_message_id=message_id)

    error_code, error_class = _classify_send_error(status, payload)
    logger.warning("telegram sendMessage failed class=%s", error_class)
    if _is_temporary(error_class):
        return TelegramSendResult(
            outcome="temp_error",
            error_code=error_code,
            error_class=error_class,
            retry_after_s=_retry_after_from(payload) if error_class == RATE_LIMITED else None,
        )
    return TelegramSendResult(outcome="perm_error", error_code=error_code, error_class=error_class)


def _extract_start_token(text: str) -> str | None:
    """Extract the token from ``/start <token>`` (or ``/start@bot <token>``)."""
    stripped = text.strip()
    if not stripped.startswith("/start"):
        return None
    rest = stripped[len("/start") :]
    if rest.startswith("@"):
        # "/start@botname <token>": drop the @mention part first.
        mention_and_rest = rest[1:].split(None, 1)
        rest = "" if len(mention_and_rest) == 1 else " " + mention_and_rest[1]
    parts = rest.split()
    if not parts:
        return None
    token = parts[0].strip()
    if len(token) > 128 or any(ch.isspace() or ch in "\r\n" for ch in token):
        return None
    return token or None


def get_start_updates(
    config: TelegramConfig,
    *,
    offset: int | None,
    poll_timeout_s: int = 5,
    http_post: HttpPost | None = None,
) -> TelegramUpdatesResult:
    """Poll getUpdates once and extract ``/start`` events.

    Returns every ``/start <token>`` message seen plus the highest update
    id (the caller advances the persisted offset past scanned updates).
    """
    post = http_post or _default_http_post
    payload: dict = {"timeout": max(0, min(poll_timeout_s, 50)), "allowed_updates": ["message"]}
    if offset is not None:
        payload["offset"] = offset
    url = f"{config.api_base_url}/bot{config.bot_token}/getUpdates"
    try:
        # The HTTP timeout must cover the long-poll window plus margin.
        status, raw = post(url, payload, config.timeout_s + payload["timeout"] + 5)
    except TelegramTimeoutError:
        logger.warning("telegram getUpdates timeout")
        return TelegramUpdatesResult(ok=False, error_class=TIMEOUT)
    except TelegramNetworkError:
        logger.warning("telegram getUpdates network error")
        return TelegramUpdatesResult(ok=False, error_class=NETWORK)
    except Exception:
        logger.warning("telegram getUpdates transport failed", exc_info=True)
        return TelegramUpdatesResult(ok=False, error_class=NETWORK)

    parsed = _parse_json_body(raw)
    if status == 200 and parsed is not None and parsed.get("ok") is True:
        found: list[TelegramStartUpdate] = []
        max_update_id: int | None = None
        result = parsed.get("result")
        items = result if isinstance(result, list) else []
        for item in items[:100]:
            if not isinstance(item, dict):
                continue
            update_id = item.get("update_id")
            if isinstance(update_id, int) and not isinstance(update_id, bool):
                if max_update_id is None or update_id > max_update_id:
                    max_update_id = update_id
            else:
                continue
            message = item.get("message")
            if not isinstance(message, dict):
                continue
            text = message.get("text")
            chat = message.get("chat")
            if not isinstance(text, str) or not isinstance(chat, dict):
                continue
            chat_id = chat.get("id")
            if isinstance(chat_id, bool) or not isinstance(chat_id, int):
                continue
            token = _extract_start_token(text)
            if token is None:
                continue
            found.append(TelegramStartUpdate(update_id=update_id, chat_id=chat_id, token=token))
        return TelegramUpdatesResult(ok=True, updates=tuple(found), max_update_id=max_update_id)

    _, error_class = _classify_send_error(status, parsed)
    logger.warning("telegram getUpdates failed class=%s", error_class)
    return TelegramUpdatesResult(ok=False, error_class=error_class)


def check_connection(
    config: TelegramConfig, *, http_post: HttpPost | None = None
) -> TelegramCheckResult:
    """Validate the bot token via getMe (no message is sent)."""
    post = http_post or _default_http_post
    url = f"{config.api_base_url}/bot{config.bot_token}/getMe"
    try:
        status, raw = post(url, {}, config.timeout_s)
    except TelegramTimeoutError:
        return TelegramCheckResult(ok=False, error_class=TIMEOUT)
    except TelegramNetworkError:
        return TelegramCheckResult(ok=False, error_class=NETWORK)
    except Exception:
        logger.warning("telegram getMe transport failed", exc_info=True)
        return TelegramCheckResult(ok=False, error_class=NETWORK)
    parsed = _parse_json_body(raw)
    if status == 200 and parsed is not None and parsed.get("ok") is True:
        username: str | None = None
        result = parsed.get("result")
        if isinstance(result, dict):
            raw_name = result.get("username")
            if isinstance(raw_name, str) and raw_name.strip():
                username = raw_name.strip()[:64]
        return TelegramCheckResult(ok=True, bot_username=username)
    _, error_class = _classify_send_error(status, parsed)
    logger.warning("telegram getMe failed class=%s", error_class)
    return TelegramCheckResult(ok=False, error_class=error_class)
