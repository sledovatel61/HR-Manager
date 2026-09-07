"""Unit tests for Telegram Bot API adapter (Phase 9)."""

from unittest.mock import MagicMock, patch

import httpx
import pytest

from app.config import Settings
from app.telegram_adapter import (
    TelegramBlockedError,
    TelegramConfigError,
    TelegramPermanentError,
    TelegramRateLimitError,
    TelegramSendResult,
    TelegramTemporaryError,
    _redact_token,
    format_telegram_message,
    get_me,
    send_message,
)


def _settings(
    token: str = "123456:TEST_TOKEN_SECRET", base_url: str = "https://api.telegram.org"
) -> Settings:
    return Settings.model_validate(
        {
            "APP_ENV": "test",
            "SECRET_KEY": "test-secret-key-32-chars-minimum-length!",
            "DATABASE_URL": "sqlite+pysqlite://",
            "TELEGRAM_BOT_TOKEN": token,
            "TELEGRAM_API_BASE_URL": base_url,
            "TELEGRAM_TIMEOUT_SECONDS": "5.0",
        }
    )


def test_redact_token() -> None:
    token = "123456:ABC-DEF1234ghIkl-zyx57W2v1u123ew11"
    raw = f"https://api.telegram.org/bot{token}/sendMessage with token {token}"
    redacted = _redact_token(raw, token)
    assert token not in redacted
    assert "[REDACTED_BOT_TOKEN]" in redacted


def test_format_telegram_message_escaping_and_length() -> None:
    title = "Событие <интервью> & 'созвон'"
    body = "Кандидат: <b>Иванов</b> & ко."
    formatted = format_telegram_message(title, body)
    assert "&lt;интервью&gt;" in formatted
    assert "&amp;" in formatted
    assert "<b>" in formatted

    # Very long message is truncated to 4096 chars
    long_body = "A" * 5000
    long_formatted = format_telegram_message("Title", long_body)
    assert len(long_formatted) <= 4096
    assert long_formatted.endswith("...")


def test_get_me_success() -> None:
    settings = _settings()
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "ok": True,
        "result": {"id": 123456789, "username": "HrTestBot", "first_name": "HR Test Bot"},
    }

    with patch("httpx.Client.get", return_value=mock_resp):
        info = get_me(settings)
        assert info["ok"] is True
        assert info["id"] == 123456789
        assert info["username"] == "HrTestBot"
        assert info["first_name"] == "HR Test Bot"


def test_get_me_unconfigured_raises() -> None:
    settings = _settings(token="")
    with pytest.raises(TelegramConfigError, match="TELEGRAM_BOT_TOKEN is not configured"):
        get_me(settings)


def test_get_me_invalid_token_401() -> None:
    settings = _settings()
    mock_resp = MagicMock()
    mock_resp.status_code = 401
    mock_resp.json.return_value = {"ok": False, "description": "Unauthorized"}

    with (
        patch("httpx.Client.get", return_value=mock_resp),
        pytest.raises(TelegramPermanentError, match="Invalid Telegram bot token"),
    ):
        get_me(settings)


def test_get_me_timeout_raises_temporary_error() -> None:
    settings = _settings()
    with (
        patch("httpx.Client.get", side_effect=httpx.TimeoutException("Read timed out")),
        pytest.raises(TelegramTemporaryError, match="timed out"),
    ):
        get_me(settings)


def test_send_message_success() -> None:
    settings = _settings()
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "ok": True,
        "result": {"message_id": 4242, "date": 1700000000},
    }

    with patch("httpx.Client.post", return_value=mock_resp) as mock_post:
        res = send_message(settings, chat_id=987654321, text="<b>Привет</b>")
        assert isinstance(res, TelegramSendResult)
        assert res.accepted is True
        assert res.provider_message_id == "4242"
        mock_post.assert_called_once()
        _args, kwargs = mock_post.call_args
        assert kwargs["json"]["chat_id"] == 987654321
        assert kwargs["json"]["text"] == "<b>Привет</b>"
        assert kwargs["json"]["parse_mode"] == "HTML"


def test_send_message_rate_limit_429() -> None:
    settings = _settings()
    mock_resp = MagicMock()
    mock_resp.status_code = 429
    mock_resp.json.return_value = {
        "ok": False,
        "description": "Too Many Requests: retry after 35",
        "parameters": {"retry_after": 35},
    }

    with (
        patch("httpx.Client.post", return_value=mock_resp),
        pytest.raises(TelegramRateLimitError) as exc_info,
    ):
        send_message(settings, chat_id=987654321, text="Test")
    assert exc_info.value.retry_after == 35
    assert exc_info.value.error_class == "rate_limited"


def test_send_message_bot_blocked_403() -> None:
    settings = _settings()
    mock_resp = MagicMock()
    mock_resp.status_code = 403
    mock_resp.json.return_value = {
        "ok": False,
        "description": "Forbidden: bot was blocked by the user",
    }

    with patch("httpx.Client.post", return_value=mock_resp), pytest.raises(TelegramBlockedError):
        send_message(settings, chat_id=987654321, text="Test")


def test_send_message_bad_request_400() -> None:
    settings = _settings()
    mock_resp = MagicMock()
    mock_resp.status_code = 400
    mock_resp.json.return_value = {
        "ok": False,
        "description": "Bad Request: chat not found",
    }

    with (
        patch("httpx.Client.post", return_value=mock_resp),
        pytest.raises(TelegramPermanentError) as exc_info,
    ):
        send_message(settings, chat_id=987654321, text="Test")
    assert exc_info.value.error_class == "permanent_error"


def test_send_message_server_error_502() -> None:
    settings = _settings()
    mock_resp = MagicMock()
    mock_resp.status_code = 502
    mock_resp.json.return_value = {"ok": False, "description": "Bad Gateway"}

    with (
        patch("httpx.Client.post", return_value=mock_resp),
        pytest.raises(TelegramTemporaryError) as exc_info,
    ):
        send_message(settings, chat_id=987654321, text="Test")
    assert exc_info.value.error_class == "temporary_error"


def test_send_message_network_error() -> None:
    settings = _settings()
    with (
        patch("httpx.Client.post", side_effect=httpx.ConnectError("Connection refused")),
        pytest.raises(TelegramTemporaryError, match="network error"),
    ):
        send_message(settings, chat_id=987654321, text="Test")
