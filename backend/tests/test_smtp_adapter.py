"""Unit tests for universal SMTP adapter (Phase 9)."""

import smtplib
from unittest.mock import MagicMock, patch

import pytest

from app.config import Settings
from app.smtp_adapter import (
    SmtpAuthError,
    SmtpConfigError,
    SmtpHeaderInjectionError,
    SmtpRecipientError,
    SmtpSendResult,
    SmtpTimeoutError,
    build_email_message,
    send_email,
    validate_header_value,
    verify_connection,
)


def _settings(
    host: str = "smtp.example.com",
    port: int = 587,
    username: str = "testuser",
    password: str = "supersecretpassword",
    use_tls: bool = False,
    use_starttls: bool = True,
    from_email: str = "notifications@example.com",
    from_name: str = "HR Manager",
) -> Settings:
    return Settings.model_validate(
        {
            "APP_ENV": "test",
            "SECRET_KEY": "test-secret-key-32-chars-minimum-length!",
            "DATABASE_URL": "sqlite+pysqlite://",
            "SMTP_HOST": host,
            "SMTP_PORT": str(port),
            "SMTP_USERNAME": username,
            "SMTP_PASSWORD": password,
            "SMTP_USE_TLS": "true" if use_tls else "false",
            "SMTP_USE_STARTTLS": "true" if use_starttls else "false",
            "SMTP_FROM_EMAIL": from_email,
            "SMTP_FROM_NAME": from_name,
            "SMTP_TIMEOUT_SECONDS": "5.0",
        }
    )


def test_header_validation_rejects_newlines() -> None:
    assert validate_header_value("Subject", "Обычная тема") == "Обычная тема"

    with pytest.raises(SmtpHeaderInjectionError, match="Subject"):
        validate_header_value("Subject", "Тема\r\nBcc: victim@example.com")

    with pytest.raises(SmtpHeaderInjectionError, match="To"):
        validate_header_value("To", "user@example.com\nAnother: header")


def test_build_email_message_russian_encoding() -> None:
    msg, msg_id = build_email_message(
        from_email="hr@company.ru",
        from_name="Менеджер по подбору",
        to_email="candidate@mail.ru",
        subject="Приглашение на собеседование",
        body="Здравствуйте! Ждём вас на интервью.",
    )
    assert msg["Subject"] == "Приглашение на собеседование"
    assert msg["To"] == "candidate@mail.ru"
    assert "hr@company.ru" in str(msg["From"])
    assert msg["Message-ID"] == msg_id
    assert msg.get_content().strip() == "Здравствуйте! Ждём вас на интервью."


def test_verify_connection_success() -> None:
    settings = _settings()
    mock_smtp = MagicMock()
    mock_smtp.has_extn.return_value = True
    mock_smtp.noop.return_value = (250, b"OK")

    with patch("smtplib.SMTP", return_value=mock_smtp):
        res = verify_connection(settings)
        assert res["ok"] is True
        assert res["host"] == "smtp.example.com"
        assert res["port"] == 587
        assert res["authenticated"] is True
        mock_smtp.ehlo.assert_called()
        mock_smtp.starttls.assert_called_once()
        mock_smtp.login.assert_called_once_with("testuser", "supersecretpassword")
        mock_smtp.noop.assert_called_once()
        mock_smtp.quit.assert_called_once()


def test_verify_connection_unconfigured() -> None:
    settings = _settings(host="")
    with pytest.raises(SmtpConfigError, match="SMTP_HOST is not configured"):
        verify_connection(settings)


def test_verify_connection_auth_error() -> None:
    settings = _settings()
    mock_smtp = MagicMock()
    mock_smtp.has_extn.return_value = True
    mock_smtp.login.side_effect = smtplib.SMTPAuthenticationError(535, b"Authentication failed")

    with (
        patch("smtplib.SMTP", return_value=mock_smtp),
        pytest.raises(SmtpAuthError, match="authentication failed"),
    ):
        verify_connection(settings)


def test_verify_connection_timeout() -> None:
    settings = _settings()
    with (
        patch("smtplib.SMTP", side_effect=TimeoutError("Connection timed out")),
        pytest.raises(SmtpTimeoutError, match="timeout"),
    ):
        verify_connection(settings)


def test_send_email_success() -> None:
    settings = _settings()
    mock_smtp = MagicMock()
    mock_smtp.has_extn.return_value = True
    mock_smtp.send_message.return_value = {}

    with patch("smtplib.SMTP", return_value=mock_smtp):
        res = send_email(
            settings,
            to_email="anna@example.com",
            subject="Новое событие",
            body="Вам назначено интервью на 15:00",
        )
        assert isinstance(res, SmtpSendResult)
        assert res.accepted is True
        assert res.provider_message_id is not None
        assert res.provider_message_id.startswith("<")
        mock_smtp.send_message.assert_called_once()


def test_send_email_recipient_refused_550() -> None:
    settings = _settings()
    mock_smtp = MagicMock()
    mock_smtp.has_extn.return_value = True
    mock_smtp.send_message.side_effect = smtplib.SMTPRecipientsRefused(
        {"bad@example.com": (550, b"User unknown")}
    )

    with (
        patch("smtplib.SMTP", return_value=mock_smtp),
        pytest.raises(SmtpRecipientError, match="refused"),
    ):
        send_email(
            settings,
            to_email="bad@example.com",
            subject="Test",
            body="Test",
        )


def test_send_email_header_injection_prevented() -> None:
    settings = _settings()
    with pytest.raises(SmtpHeaderInjectionError):
        send_email(
            settings,
            to_email="target@example.com\r\nBcc: spam@evil.com",
            subject="Valid Subject",
            body="Body",
        )
