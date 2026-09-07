"""Unit tests for the SMTP adapter (fake smtplib, no network)."""

import email
import logging
import smtplib
from collections.abc import Iterator
from email.header import decode_header
from typing import Any, ClassVar, cast

import pytest

from app.smtp import (
    SmtpConfig,
    check_connection,
    render_message,
    send_email,
    validate_header_text,
    validate_mailbox,
)


def _config(**overrides: object) -> SmtpConfig:
    values: dict = {
        "enabled": True,
        "host": "smtp.example.test",
        "port": 587,
        "encryption": "starttls",
        "username": "user",
        "password": "SECRET-password-must-never-appear-in-logs",
        "from_address": "noreply@example.test",
        "from_name": "HR Manager",
        "timeout_s": 10.0,
        "max_message_bytes": 524_288,
    }
    values.update(overrides)
    return SmtpConfig(**values)


class FakeSMTP:
    """In-memory smtplib double recording the security-relevant flow."""

    instances: ClassVar[list["FakeSMTP"]] = []

    def __init__(self, host: str, port: int, timeout: float) -> None:
        self.host = host
        self.port = port
        self.timeout = timeout
        self.ehlo_calls = 0
        self.starttls_calls = 0
        self.login_calls: list[tuple[str, str]] = []
        self.sent: list = []
        self.quit_calls = 0
        self.send_side_effect: Exception | None = None
        self.login_side_effect: Exception | None = None
        FakeSMTP.instances.append(self)

    # -- smtplib surface used by the adapter --
    def ehlo(self) -> None:
        self.ehlo_calls += 1

    def starttls(self) -> None:
        self.starttls_calls += 1

    def login(self, username: str, password: str) -> None:
        self.login_calls.append((username, password))
        if self.login_side_effect is not None:
            raise self.login_side_effect

    def send_message(self, message):  # type: ignore[no-untyped-def]
        if self.send_side_effect is not None:
            raise self.send_side_effect
        self.sent.append(message)
        return {}

    def quit(self) -> None:
        self.quit_calls += 1

    def close(self) -> None:
        pass


class FakeFactory:
    def __init__(self) -> None:
        self.plain_used = 0
        self.tls_used = 0
        self.client: FakeSMTP | None = None
        self.connect_side_effect: Exception | None = None

    def plain(self, host: str, port: int, timeout: float) -> FakeSMTP:
        if self.connect_side_effect is not None:
            raise self.connect_side_effect
        self.plain_used += 1
        self.client = FakeSMTP(host, port, timeout)
        return self.client

    def tls(self, host: str, port: int, timeout: float) -> FakeSMTP:
        if self.connect_side_effect is not None:
            raise self.connect_side_effect
        self.tls_used += 1
        self.client = FakeSMTP(host, port, timeout)
        return self.client


@pytest.fixture(autouse=True)
def _clean_fakes() -> Iterator[None]:
    FakeSMTP.instances.clear()
    yield
    FakeSMTP.instances.clear()


def _decode_subject(raw: str) -> str:
    parts = []
    for chunk, charset in decode_header(raw):
        if isinstance(chunk, bytes):
            parts.append(chunk.decode(charset or "utf-8"))
        else:
            parts.append(chunk)
    return "".join(parts)


def test_send_starttls_flow_and_russian_encoding() -> None:
    factory = FakeFactory()
    result = send_email(
        _config(),
        to_address="ivan@example.test",
        subject="Вам назначено собеседование",
        text_body="Добрый день! Ждём вас завтра.",
        factory=cast(Any, factory),
    )
    assert result.outcome == "accepted"
    client = factory.client
    assert client is not None
    assert factory.plain_used == 1 and factory.tls_used == 0
    assert client.starttls_calls == 1
    # EHLO before AND after STARTTLS (fresh capability list over TLS).
    assert client.ehlo_calls == 2
    assert client.login_calls == [("user", "SECRET-password-must-never-appear-in-logs")]
    assert len(client.sent) == 1
    assert client.quit_calls == 1

    raw = client.sent[0].as_bytes()
    parsed = email.message_from_bytes(raw, policy=email.policy.default)
    assert _decode_subject(str(parsed["Subject"])) == "Вам назначено собеседование"
    assert parsed["To"] == "ivan@example.test"
    assert "noreply@example.test" in str(parsed["From"])
    part = parsed.get_body(preferencelist=("plain",))
    assert part is not None
    body = part.get_content()
    assert "Добрый день" in body
    assert "не означает прочтение" in body


def test_send_tls_uses_smtps_and_plain_skips_starttls() -> None:
    factory = FakeFactory()
    result = send_email(
        _config(encryption="tls", port=465),
        to_address="a@example.test",
        subject="S",
        text_body="B",
        factory=cast(Any, factory),
    )
    assert result.outcome == "accepted"
    assert factory.tls_used == 1 and factory.plain_used == 0
    assert factory.client is not None and factory.client.starttls_calls == 0

    factory2 = FakeFactory()
    result2 = send_email(
        _config(encryption="none", username=""),
        to_address="a@example.test",
        subject="S",
        text_body="B",
        factory=cast(Any, factory2),
    )
    assert result2.outcome == "accepted"
    assert factory2.client is not None and factory2.client.starttls_calls == 0
    assert factory2.client.login_calls == []


@pytest.mark.parametrize(
    "field,value",
    [
        ("to", "victim@example.test\nBcc: evil@example.test"),
        ("to", "victim@example.test\resq"),
        ("subject", "Тема\nX-Injected: 1"),
        ("from_name", "HR\nManager"),
    ],
)
def test_header_injection_is_rejected(field: str, value: str) -> None:
    if field == "to":
        with pytest.raises(ValueError):
            render_message(_config(), to_address=value, subject="S", text_body="B")
    elif field == "subject":
        with pytest.raises(ValueError):
            render_message(_config(), to_address="a@example.test", subject=value, text_body="B")
    else:
        with pytest.raises(ValueError):
            render_message(
                _config(from_name=value), to_address="a@example.test", subject="S", text_body="B"
            )
    # And the send path classifies it as permanent (never a fake accepted).
    kwargs: dict = {"to_address": "a@example.test", "subject": "S", "text_body": "B"}
    if field == "to":
        kwargs["to_address"] = value
    elif field == "subject":
        kwargs["subject"] = value
    result = send_email(
        _config(from_name=value) if field == "from_name" else _config(),
        factory=cast(Any, FakeFactory()),
        **kwargs,
    )
    assert result.outcome == "perm_error"
    assert result.error_class == "smtp_invalid_address"


def test_oversized_message_is_rejected() -> None:
    result = send_email(
        _config(max_message_bytes=4096),
        to_address="a@example.test",
        subject="S",
        text_body="x" * 100_000,
        factory=cast(Any, FakeFactory()),
    )
    assert result.outcome == "perm_error"
    assert result.error_class == "smtp_message_too_large"


def test_auth_failure_is_permanent() -> None:
    factory = FakeFactory()

    class AuthFactory(FakeFactory):
        def plain(self, host: str, port: int, timeout: float) -> FakeSMTP:
            client = super().plain(host, port, timeout)
            client.login_side_effect = smtplib.SMTPAuthenticationError(535, b"bad credentials")
            return client

    result = send_email(
        _config(),
        to_address="a@example.test",
        subject="S",
        text_body="B",
        factory=cast(Any, AuthFactory()),
    )
    assert result.outcome == "perm_error"
    assert result.error_class == "smtp_auth"
    assert factory is not None


def test_recipient_refused_is_permanent() -> None:
    class RefusingFactory(FakeFactory):
        def plain(self, host: str, port: int, timeout: float) -> FakeSMTP:
            client = super().plain(host, port, timeout)
            client.send_side_effect = smtplib.SMTPRecipientsRefused(
                {"a@example.test": (550, b"no")}
            )
            return client

    result = send_email(
        _config(),
        to_address="a@example.test",
        subject="S",
        text_body="B",
        factory=cast(Any, RefusingFactory()),
    )
    assert result.outcome == "perm_error"
    assert result.error_class == "smtp_recipient_refused"


@pytest.mark.parametrize(
    ("exc", "expected_class", "expected_outcome"),
    [
        (smtplib.SMTPResponseException(421, b"try later"), "smtp_temporary", "temp_error"),
        (smtplib.SMTPResponseException(550, b"no mailbox"), "smtp_recipient_refused", "perm_error"),
        (smtplib.SMTPServerDisconnected("gone"), "smtp_network", "temp_error"),
        (smtplib.SMTPConnectError(421, "down"), "smtp_network", "temp_error"),
        (TimeoutError(), "smtp_timeout", "temp_error"),
        (OSError("dns"), "smtp_network", "temp_error"),
    ],
)
def test_error_classification(exc: Exception, expected_class: str, expected_outcome: str) -> None:
    class FailingFactory(FakeFactory):
        def plain(self, host: str, port: int, timeout: float) -> FakeSMTP:
            client = super().plain(host, port, timeout)
            client.send_side_effect = exc
            return client

    result = send_email(
        _config(),
        to_address="a@example.test",
        subject="S",
        text_body="B",
        factory=cast(Any, FailingFactory()),
    )
    assert result.outcome == expected_outcome
    assert result.error_class == expected_class


def test_password_never_logged(caplog: pytest.LogCaptureFixture) -> None:
    class AuthFactory(FakeFactory):
        def plain(self, host: str, port: int, timeout: float) -> FakeSMTP:
            client = super().plain(host, port, timeout)
            client.login_side_effect = smtplib.SMTPAuthenticationError(535, b"no")
            return client

    with caplog.at_level(logging.WARNING, logger="app.smtp"):
        send_email(
            _config(),
            to_address="a@example.test",
            subject="Секретная тема",
            text_body="B",
            factory=cast(Any, AuthFactory()),
        )
    assert "SECRET-password-must-never-appear-in-logs" not in caplog.text
    assert "smtp_auth" in caplog.text


def test_check_connection_sends_no_mail() -> None:
    factory = FakeFactory()
    result = check_connection(_config(), factory=factory)  # type: ignore[arg-type]
    assert result.ok and result.error_class is None
    assert factory.client is not None
    assert factory.client.sent == []
    assert factory.client.login_calls != []  # credentials ARE validated
    assert factory.client.quit_calls == 1


def test_check_connection_reports_auth_failure() -> None:
    class AuthFactory(FakeFactory):
        def plain(self, host: str, port: int, timeout: float) -> FakeSMTP:
            client = super().plain(host, port, timeout)
            client.login_side_effect = smtplib.SMTPAuthenticationError(535, b"no")
            return client

    result = check_connection(_config(), factory=AuthFactory())  # type: ignore[arg-type]
    assert not result.ok
    assert result.error_class == "smtp_auth"


def test_validate_mailbox_cases() -> None:
    assert validate_mailbox("  Ivan@Example.TEST ", field="to") == "Ivan@Example.TEST"
    for bad in ["", "no-at-sign", "a@b", "a@.com", "@x.com", "a b@c.com", "a@c.com\nBcc:x"]:
        with pytest.raises(ValueError):
            validate_mailbox(bad, field="to")
    with pytest.raises(ValueError):
        validate_header_text("x\ny", field="subject", max_chars=300)
    with pytest.raises(ValueError):
        validate_header_text("   ", field="subject", max_chars=300)
