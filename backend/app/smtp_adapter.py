"""Universal SMTP adapter for email notifications (roadmap phase 9).

Universal SMTP adapter:
* Host, port, TLS/STARTTLS, username and password from environment/Settings;
* Secrets are NEVER logged or exposed in API responses;
* Clean UTF-8 MIME encoding for Russian text (subject, body, display name);
* Anti-header-injection protection: newlines (\\r, \\n) strictly rejected in headers;
* Verification of SMTP connection without sending email (NOOP / EHLO);
* Dedicated admin test sending with permission and audit;
* Safe error classification (auth, recipient, timeout, connection, temporary);
* Generation and tracking of RFC 5322 Message-ID as provider_message_id;
* Provider accepted != delivered to mailbox != human read.
"""

import contextlib
import logging
import smtplib
import ssl
from dataclasses import dataclass
from email.headerregistry import Address
from email.message import EmailMessage
from email.utils import make_msgid
from typing import Any

from app.config import Settings

logger = logging.getLogger(__name__)

MAX_EMAIL_BODY_LENGTH = 100_000


class SmtpError(Exception):
    """Base exception for SMTP integration errors (secrets redacted)."""

    def __init__(
        self, message: str, *, error_class: str = "smtp_error", error_code: str | None = None
    ) -> None:
        super().__init__(message)
        self.error_class = error_class
        self.error_code = error_code


class SmtpConfigError(SmtpError):
    """SMTP host or sender is not configured."""

    def __init__(self, message: str = "SMTP is not configured") -> None:
        super().__init__(message, error_class="channel_not_configured", error_code="not_configured")


class SmtpAuthError(SmtpError):
    """SMTP authentication failed (535)."""

    def __init__(self, message: str = "SMTP authentication failed") -> None:
        super().__init__(message, error_class="auth_failed", error_code="535")


class SmtpRecipientError(SmtpError):
    """Recipient address rejected by SMTP server (550 / 551 / 553)."""

    def __init__(self, message: str = "SMTP recipient rejected") -> None:
        super().__init__(message, error_class="recipient_rejected", error_code="550")


class SmtpConnectionError(SmtpError):
    """Connection or TLS handshake to SMTP host failed."""

    def __init__(
        self, message: str = "SMTP connection failed", error_code: str | None = "connection_failed"
    ) -> None:
        super().__init__(message, error_class="connection_failed", error_code=error_code)


class SmtpTimeoutError(SmtpError):
    """SMTP connection or command timed out."""

    def __init__(self, message: str = "SMTP timeout") -> None:
        super().__init__(message, error_class="timeout", error_code="timeout")


class SmtpTemporaryError(SmtpError):
    """Temporary SMTP error (4xx reply code, server busy) eligible for retry."""

    def __init__(self, message: str, error_code: str | None = "451") -> None:
        super().__init__(message, error_class="temporary_error", error_code=error_code)


class SmtpHeaderInjectionError(SmtpError):
    """Invalid characters (newlines) detected in header fields."""

    def __init__(self, field: str) -> None:
        super().__init__(
            f"Header injection attempt detected in {field}",
            error_class="header_injection",
            error_code="400",
        )


@dataclass(frozen=True)
class SmtpSendResult:
    accepted: bool
    provider_message_id: str | None


def validate_header_value(name: str, value: str) -> str:
    """Validate that header values do not contain carriage return or newline characters."""
    if "\r" in value or "\n" in value:
        raise SmtpHeaderInjectionError(name)
    return value.strip()


def build_email_message(
    *,
    from_email: str,
    from_name: str,
    to_email: str,
    subject: str,
    body: str,
    html_body: str | None = None,
) -> tuple[EmailMessage, str]:
    """Construct a MIME EmailMessage with proper UTF-8 headers and body.

    Returns the message object and the generated RFC 5322 Message-ID.
    """
    clean_from_email = validate_header_value("From email", from_email)
    clean_from_name = validate_header_value("From name", from_name)
    clean_to_email = validate_header_value("To email", to_email)
    clean_subject = validate_header_value("Subject", subject)

    msg = EmailMessage()
    msg_id = make_msgid(
        domain=clean_from_email.split("@")[-1] if "@" in clean_from_email else "localhost"
    )
    msg["Message-ID"] = msg_id
    msg["Subject"] = clean_subject

    if clean_from_name:
        # RFC 2047 formatted display name + email address.
        from_parts = clean_from_email.split("@", 1)
        if len(from_parts) == 2:
            msg["From"] = Address(
                display_name=clean_from_name, username=from_parts[0], domain=from_parts[1]
            )
        else:
            msg["From"] = f"{clean_from_name} <{clean_from_email}>"
    else:
        msg["From"] = clean_from_email

    msg["To"] = clean_to_email

    clean_body = body[:MAX_EMAIL_BODY_LENGTH] if len(body) > MAX_EMAIL_BODY_LENGTH else body
    msg.set_content(clean_body, charset="utf-8")

    if html_body:
        clean_html = (
            html_body[:MAX_EMAIL_BODY_LENGTH]
            if len(html_body) > MAX_EMAIL_BODY_LENGTH
            else html_body
        )
        msg.add_alternative(clean_html, subtype="html", charset="utf-8")

    return msg, msg_id


def _create_smtp_client(settings: Settings) -> smtplib.SMTP:
    """Establish connection to SMTP server with TLS/STARTTLS negotiation."""
    host = settings.smtp_host.strip()
    if not host:
        raise SmtpConfigError("SMTP_HOST is not configured")

    port = settings.smtp_port
    timeout = settings.smtp_timeout_seconds

    try:
        if settings.smtp_use_tls:
            context = ssl.create_default_context()
            client: smtplib.SMTP = smtplib.SMTP_SSL(host, port, timeout=timeout, context=context)
        else:
            client = smtplib.SMTP(host, port, timeout=timeout)

        client.ehlo()

        if settings.smtp_use_starttls and not settings.smtp_use_tls:
            if client.has_extn("STARTTLS"):
                context = ssl.create_default_context()
                client.starttls(context=context)
                client.ehlo()
            else:
                logger.warning("SMTP server %s does not support STARTTLS", host)

        username = settings.smtp_username.strip()
        password = settings.smtp_password
        if username and password:
            client.login(username, password)

        return client
    except smtplib.SMTPAuthenticationError as exc:
        raise SmtpAuthError("SMTP authentication failed (invalid credentials)") from exc
    except smtplib.SMTPConnectError as exc:
        raise SmtpConnectionError(f"Could not connect to SMTP server {host}:{port}") from exc
    except (TimeoutError, smtplib.SMTPResponseException) as exc:
        if isinstance(exc, smtplib.SMTPResponseException) and 400 <= exc.smtp_code <= 499:
            raise SmtpTemporaryError(
                f"SMTP temporary error ({exc.smtp_code})", error_code=str(exc.smtp_code)
            ) from exc
        raise SmtpTimeoutError(f"SMTP connection timeout for {host}:{port}") from exc
    except (ssl.SSLError, OSError) as exc:
        raise SmtpConnectionError(f"SMTP socket/TLS error: {exc}") from exc


def verify_connection(settings: Settings) -> dict[str, Any]:
    """Test SMTP connection, TLS negotiation and authentication without sending email."""
    host = settings.smtp_host.strip()
    if not host:
        raise SmtpConfigError("SMTP_HOST is not configured")

    client = None
    try:
        client = _create_smtp_client(settings)
        # Send NOOP to verify session is active.
        status, _ = client.noop()
        if status not in (200, 250):
            raise SmtpTemporaryError(f"SMTP NOOP command returned status {status}")
        return {
            "ok": True,
            "host": host,
            "port": settings.smtp_port,
            "use_tls": settings.smtp_use_tls,
            "use_starttls": settings.smtp_use_starttls,
            "authenticated": bool(settings.smtp_username.strip()),
        }
    finally:
        if client is not None:
            try:
                client.quit()
            except Exception:
                with contextlib.suppress(Exception):
                    client.close()


def send_email(
    settings: Settings,
    *,
    to_email: str,
    subject: str,
    body: str,
    html_body: str | None = None,
    from_email: str | None = None,
    from_name: str | None = None,
) -> SmtpSendResult:
    """Send an email notification via configured SMTP.

    Returns SmtpSendResult(accepted=True, provider_message_id=msg_id).
    Raises typed SmtpError subclasses on failure.
    """
    effective_from_email = (from_email or settings.smtp_from_email).strip()
    if not effective_from_email:
        effective_from_email = f"noreply@{settings.smtp_host.strip() or 'localhost'}"

    effective_from_name = (from_name or settings.smtp_from_name).strip()

    msg, msg_id = build_email_message(
        from_email=effective_from_email,
        from_name=effective_from_name,
        to_email=to_email,
        subject=subject,
        body=body,
        html_body=html_body,
    )

    client = None
    try:
        client = _create_smtp_client(settings)
        refused = client.send_message(msg)
        if refused:
            logger.warning("SMTP recipients refused: %s", list(refused.keys()))
            raise SmtpRecipientError(f"Recipient refused by SMTP server: {list(refused.keys())}")
        return SmtpSendResult(accepted=True, provider_message_id=msg_id)
    except smtplib.SMTPRecipientsRefused as exc:
        raise SmtpRecipientError(f"Recipient {to_email} was refused") from exc
    except smtplib.SMTPDataError as exc:
        err_msg = (
            exc.smtp_error.decode("utf-8", errors="replace")
            if isinstance(exc.smtp_error, bytes)
            else str(exc.smtp_error)
        )
        if 400 <= exc.smtp_code <= 499:
            raise SmtpTemporaryError(
                f"SMTP temporary data error: {err_msg}", error_code=str(exc.smtp_code)
            ) from exc
        raise SmtpRecipientError(f"SMTP data rejected ({exc.smtp_code})") from exc
    except smtplib.SMTPServerDisconnected as exc:
        raise SmtpConnectionError(f"SMTP server disconnected prematurely: {exc}") from exc
    except SmtpError:
        raise
    except Exception as exc:
        logger.warning("Unexpected SMTP error when sending to %s: %s", to_email, exc)
        raise SmtpConnectionError(f"SMTP error: {exc}") from exc
    finally:
        if client is not None:
            try:
                client.quit()
            except Exception:
                with contextlib.suppress(Exception):
                    client.close()
