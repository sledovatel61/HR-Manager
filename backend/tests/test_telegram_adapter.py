"""Unit tests for the Telegram Bot API adapter (fake transport, no network)."""

import json
import logging

import pytest

from app.telegram import (
    TelegramConfig,
    TelegramTimeoutError,
    _extract_start_token,
    check_connection,
    get_start_updates,
    render_message_text,
    send_message,
    truncate_utf16,
)

CONFIG = TelegramConfig(
    enabled=True,
    bot_token="TEST-TOKEN-must-never-appear-in-logs",
    api_base_url="https://api.telegram.org",
    timeout_s=10.0,
)


def _ok_response(message_id: object = 42) -> tuple[int, bytes]:
    return 200, json.dumps({"ok": True, "result": {"message_id": message_id}}).encode()


def test_send_success_returns_provider_id() -> None:
    calls: list[tuple[str, dict]] = []

    def fake(url: str, payload: dict, timeout: float) -> tuple[int, bytes]:
        calls.append((url, payload))
        return _ok_response(777)

    result = send_message(CONFIG, chat_id=12345, title="Заголовок", body="Тело", http_post=fake)
    assert result.outcome == "accepted"
    assert result.provider_message_id == "777"
    assert result.error_class is None
    # Plain text, numeric chat id, bounded HTTP timeout.
    assert calls[0][1]["chat_id"] == 12345
    assert "Заголовок" in calls[0][1]["text"]
    assert "parse_mode" not in calls[0][1]


def test_send_success_without_provider_id_is_still_accepted() -> None:
    def fake(url: str, payload: dict, timeout: float) -> tuple[int, bytes]:
        return 200, json.dumps({"ok": True, "result": {}}).encode()

    result = send_message(CONFIG, chat_id=1, title="T", body=None, http_post=fake)
    assert result.outcome == "accepted"
    assert result.provider_message_id is None  # never fabricated


def test_send_429_honors_retry_after() -> None:
    def fake(url: str, payload: dict, timeout: float) -> tuple[int, bytes]:
        return 429, json.dumps(
            {"ok": False, "error_code": 429, "parameters": {"retry_after": 34}}
        ).encode()

    result = send_message(CONFIG, chat_id=1, title="T", body=None, http_post=fake)
    assert result.outcome == "temp_error"
    assert result.error_class == "telegram_rate_limited"
    assert result.retry_after_s == 34


def test_send_429_retry_after_is_bounded() -> None:
    def fake(url: str, payload: dict, timeout: float) -> tuple[int, bytes]:
        return 429, json.dumps(
            {"ok": False, "error_code": 429, "parameters": {"retry_after": 99999}}
        ).encode()

    result = send_message(CONFIG, chat_id=1, title="T", body=None, http_post=fake)
    assert result.outcome == "temp_error"
    assert result.retry_after_s == 3600


@pytest.mark.parametrize(
    ("status", "description", "expected_class", "expected_outcome"),
    [
        (400, "Bad Request: chat not found", "telegram_chat_not_found", "perm_error"),
        (400, "Bad Request: message text is empty", "telegram_bad_request", "perm_error"),
        (401, "Unauthorized", "telegram_unauthorized", "perm_error"),
        (404, "Not Found", "telegram_unauthorized", "perm_error"),
        (403, "Forbidden: bot was blocked by the user", "telegram_blocked", "perm_error"),
        (403, "Forbidden: user is deactivated", "telegram_deactivated", "perm_error"),
        (403, "Forbidden: something else", "telegram_forbidden", "perm_error"),
        (500, "Internal Server Error", "telegram_server", "temp_error"),
        (502, "Bad Gateway", "telegram_server", "temp_error"),
    ],
)
def test_send_error_classification(
    status: int, description: str, expected_class: str, expected_outcome: str
) -> None:
    def fake(url: str, payload: dict, timeout: float) -> tuple[int, bytes]:
        return status, json.dumps(
            {"ok": False, "error_code": status, "description": description}
        ).encode()

    result = send_message(CONFIG, chat_id=1, title="T", body=None, http_post=fake)
    assert result.outcome == expected_outcome
    assert result.error_class == expected_class
    assert result.provider_message_id is None


def test_send_timeout_and_network_are_temporary() -> None:
    def boom_timeout(url: str, payload: dict, timeout: float) -> tuple[int, bytes]:
        raise TelegramTimeoutError

    from app.telegram import TelegramNetworkError

    def boom_network(url: str, payload: dict, timeout: float) -> tuple[int, bytes]:
        raise TelegramNetworkError

    assert (
        send_message(CONFIG, chat_id=1, title="T", body=None, http_post=boom_timeout).error_class
        == "telegram_timeout"
    )
    assert (
        send_message(CONFIG, chat_id=1, title="T", body=None, http_post=boom_network).error_class
        == "telegram_network"
    )


def test_send_ambiguous_200_is_temporary_never_accepted() -> None:
    def fake(url: str, payload: dict, timeout: float) -> tuple[int, bytes]:
        return 200, b"this is not json{{"

    result = send_message(CONFIG, chat_id=1, title="T", body=None, http_post=fake)
    assert result.outcome == "temp_error"
    assert result.error_class == "telegram_bad_response"


def test_send_oversized_response_is_rejected() -> None:
    def fake(url: str, payload: dict, timeout: float) -> tuple[int, bytes]:
        return 200, b"x" * (70 * 1024)

    result = send_message(CONFIG, chat_id=1, title="T", body=None, http_post=fake)
    assert result.outcome == "temp_error"
    assert result.error_class == "telegram_bad_response"


def test_token_chat_and_text_never_logged(caplog: pytest.LogCaptureFixture) -> None:
    def fake(url: str, payload: dict, timeout: float) -> tuple[int, bytes]:
        return 400, json.dumps({"ok": False, "description": "Bad Request: chat not found"}).encode()

    with caplog.at_level(logging.WARNING, logger="app.telegram"):
        send_message(
            CONFIG, chat_id=987654321, title="Секретный заголовок", body="Тело", http_post=fake
        )
    assert "TEST-TOKEN-must-never-appear-in-logs" not in caplog.text
    assert "987654321" not in caplog.text
    assert "Секретный заголовок" not in caplog.text
    assert "telegram_chat_not_found" in caplog.text


def test_render_message_text_truncates_to_bot_limit() -> None:
    text = render_message_text("Заголовок", "Тело " * 5000)
    utf16_units = len(text.encode("utf-16-le")) // 2
    assert utf16_units <= 4096
    assert text.startswith("Заголовок")
    assert "не означает прочтение" in text


def test_truncate_counts_utf16_code_units() -> None:
    # Emoji are astral (2 UTF-16 units each) — a naive char count overflows.
    text = truncate_utf16("😀" * 5000, 4096)
    assert len(text.encode("utf-16-le")) // 2 <= 4096
    assert truncate_utf16("короткий", 4096) == "короткий"


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("/start abc123", "abc123"),
        ("/start@mybot abc123", "abc123"),
        ("/start", None),
        ("/start@mybot", None),
        ("hello", None),
        ("/stop abc", None),
        ("", None),
    ],
)
def test_extract_start_token(text: str, expected: str | None) -> None:
    assert _extract_start_token(text) == expected


def test_get_start_updates_parses_mixed_inbox() -> None:
    body = {
        "ok": True,
        "result": [
            {"update_id": 10, "message": {"chat": {"id": 111}, "text": "/start tok-A"}},
            {"update_id": 11, "message": {"chat": {"id": 222}, "text": "просто текст"}},
            {"update_id": 12, "message": {"chat": {"id": 333}, "text": "/start"}},
            {"update_id": 13, "edited_message": {"chat": {"id": 444}, "text": "/start tok-B"}},
            {"update_id": 14, "message": {"chat": {"id": 555}, "text": "/start@bot tok-C"}},
            {"update_id": 15, "nonsense": True},
        ],
    }

    seen: list[dict] = []

    def fake(url: str, payload: dict, timeout: float) -> tuple[int, bytes]:
        seen.append(payload)
        return 200, json.dumps(body).encode()

    result = get_start_updates(CONFIG, offset=10, http_post=fake)
    assert result.ok
    assert [(u.token, u.chat_id) for u in result.updates] == [("tok-A", 111), ("tok-C", 555)]
    assert result.max_update_id == 15
    assert seen[0]["offset"] == 10


def test_get_start_updates_failure_is_classified() -> None:
    def fake(url: str, payload: dict, timeout: float) -> tuple[int, bytes]:
        return 401, json.dumps({"ok": False, "description": "Unauthorized"}).encode()

    result = get_start_updates(CONFIG, offset=None, http_post=fake)
    assert not result.ok
    assert result.error_class == "telegram_unauthorized"


def test_check_connection_ok_and_failure() -> None:
    def fake_ok(url: str, payload: dict, timeout: float) -> tuple[int, bytes]:
        assert url.endswith("/getMe")
        return 200, json.dumps({"ok": True, "result": {"username": "hr_test_bot"}}).encode()

    def fake_bad(url: str, payload: dict, timeout: float) -> tuple[int, bytes]:
        return 404, json.dumps({"ok": False, "description": "Not Found"}).encode()

    ok = check_connection(CONFIG, http_post=fake_ok)
    assert ok.ok and ok.bot_username == "hr_test_bot"
    bad = check_connection(CONFIG, http_post=fake_bad)
    assert not bad.ok and bad.error_class == "telegram_unauthorized"


def test_transport_crash_logs_type_and_request_id_not_token_url(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """An unexpected transport crash logs only its type + request_id: the
    exception message may echo the token-bearing request URL."""

    def boom(url: str, payload: dict, timeout: float) -> tuple[int, bytes]:
        raise RuntimeError(f"boom while posting {url} chat=1122334455")

    with caplog.at_level(logging.WARNING, logger="app.telegram"):
        result = send_message(
            CONFIG,
            chat_id=1122334455,
            title="T",
            body="secret-body",
            http_post=boom,
            request_id="req-789",
        )
    assert result.outcome == "temp_error"
    assert result.error_class == "telegram_network"
    assert "type=RuntimeError" in caplog.text
    assert "request_id=req-789" in caplog.text
    assert "TEST-TOKEN-must-never-appear-in-logs" not in caplog.text
    assert "1122334455" not in caplog.text
    assert "secret-body" not in caplog.text
