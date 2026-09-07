"""Universal SMTP adapter (phase 9).

Stdlib-only (``smtplib`` + ``email``) delivery with explicit encryption
modes (``none``/``starttls``/``tls``), timeouts, a rendered-message size
cap and classified errors:

* authentication/config failures and 5xx recipient rejections are
  permanent (the message will never deliver as-is);
* 4xx responses, timeouts and connection failures are temporary and feed
  the worker's bounded retry;
* provider ``accepted`` (``250 OK`` from the relay) is recorded as
  ``accepted`` — it never means «delivered» and never means «read».

Security rules:

* the password travels only inside the SMTP AUTH exchange — it is never
  logged, persisted or returned (log lines carry error classes only);
* header injection is rejected: addresses, display names and the subject
  must not contain CR/LF; rendering goes through ``EmailMessage``, which
  encodes Russian text and display names as RFC 2047/6532 UTF-8;
* the adapter sends to exactly one explicit recipient per call — the
  caller (worker) resolves it from the verified binding, never from an
  untrusted payload.
"""

from __future__ import annotations

import contextlib
import logging
import smtplib
import socket
from dataclasses import dataclass
from email.message import EmailMessage
from email.utils import formataddr, parseaddr
from typing import Literal

logger = logging.getLogger(__name__)

SendOutcome = Literal["accepted", "temp_error", "perm_error"]

# Safe error classes (logged, stored, shown to users/admins).
AUTH = "smtp_auth"
CONFIG = "smtp_config"
RECIPIENT_REFUSED = "smtp_recipient_refused"
TEMPORARY = "smtp_temporary"
TIMEOUT = "smtp_timeout"
NETWORK = "smtp_network"
MESSAGE_TOO_LARGE = "smtp_message_too_large"
INVALID_ADDRESS = "smtp_invalid_address"


@dataclass(frozen=True)
class SmtpConfig:
    """SMTP connection parameters (built from Settings per call)."""

    enabled: bool
    host: str
    port: int
    encryption: Literal["none", "starttls", "tls"]
    username: str
    password: str
    from_address: str
    from_name: str
    timeout_s: float
    max_message_bytes: int

    @property
    def is_configured(self) -> bool:
        return self.enabled and bool(self.host) and bool(self.from_address)


@dataclass(frozen=True)
class SmtpSendResult:
    outcome: SendOutcome
    provider_message_id: str | None = None
    error_code: str | None = None
    error_class: str | None = None


@dataclass(frozen=True)
class SmtpCheckResult:
    ok: bool
    error_class: str | None = None


def config_from_settings(settings: object) -> SmtpConfig:
    """Build the adapter config from application Settings."""
    from app.config import Settings

    assert isinstance(settings, Settings)
    encryption: Literal["none", "starttls", "tls"] = settings.smtp_encryption
    return SmtpConfig(
        enabled=settings.smtp_enabled,
        host=settings.smtp_host,
        port=settings.smtp_port,
        encryption=encryption,
        username=settings.smtp_username,
        password=settings.smtp_password,
        from_address=settings.smtp_from_address,
        from_name=settings.smtp_from_name,
        timeout_s=settings.smtp_timeout_s,
        max_message_bytes=settings.smtp_max_message_bytes,
    )


def _has_header_break(value: str) -> bool:
    return "\r" in value or "\n" in value


def validate_mailbox(value: str, *, field: str) -> str:
    """Validate one mailbox, rejecting header-injection payloads.

    Returns the normalized ``addr-spec``. Raises ValueError on any junk
    (empty, CR/LF, multiple addresses, missing domain dot).
    """
    if _has_header_break(value):
        raise ValueError(f"{field}: заголовок содержит запрещённые символы.")
    _, address = parseaddr(value.strip())
    if not address or _has_header_break(address):
        raise ValueError(f"{field}: некорректный адрес.")
    if address.count("@") != 1:
        raise ValueError(f"{field}: некорректный адрес.")
    local, _, domain = address.partition("@")
    if (
        not local
        or not domain
        or "." not in domain
        or domain.startswith(".")
        or any(ch.isspace() for ch in address)
        or len(address) > 254
    ):
        raise ValueError(f"{field}: некорректный адрес.")
    return address


def validate_header_text(value: str, *, field: str, max_chars: int) -> str:
    """Validate a header/display value (subject, display name)."""
    if _has_header_break(value):
        raise ValueError(f"{field}: заголовок содержит запрещённые символы.")
    cleaned = " ".join(value.split())
    if not cleaned:
        raise ValueError(f"{field}: значение обязательно.")
    if len(cleaned) > max_chars:
        raise ValueError(f"{field}: слишком длинное значение.")
    return cleaned


def render_message(
    config: SmtpConfig, *, to_address: str, subject: str, text_body: str
) -> EmailMessage:
    """Render one notification as a UTF-8 plain-text message.

    ``EmailMessage`` encodes Russian subjects/display names per RFC 2047.
    Raises ValueError on header injection or an oversized message.
    """
    recipient = validate_mailbox(to_address, field="to")
    sender = validate_mailbox(config.from_address, field="from")
    clean_subject = validate_header_text(subject, field="subject", max_chars=300)
    if _has_header_break(config.from_name):
        raise ValueError("from: заголовок содержит запрещённые символы.")
    sender_name = " ".join(config.from_name.split())
    if len(sender_name) > 100:
        raise ValueError("from: слишком длинное имя отправителя.")
    footer = (
        "\n\n—\nЭто автоматическое уведомление HR Manager. "
        "Принятие письма почтовым сервером не означает прочтение."
    )
    full_body = text_body.strip() + footer if text_body.strip() else footer.strip()
    message = EmailMessage()
    message["From"] = formataddr((sender_name, sender)) if sender_name else sender
    message["To"] = recipient
    message["Subject"] = clean_subject
    # Plain text only: no HTML, no attachments — nothing to escape or smuggle.
    message.set_content(full_body, subtype="plain", charset="utf-8")
    # Deterministic, bounded size: measured on the rendered bytes.
    rendered = message.as_bytes()
    if len(rendered) > config.max_message_bytes:
        raise ValueError("message: письмо превышает допустимый размер.")
    return message


class _SmtpFactory:
    """Indirection over smtplib constructors (tests inject fakes)."""

    def plain(self, host: str, port: int, timeout: float) -> smtplib.SMTP:
        return smtplib.SMTP(host, port, timeout=timeout)

    def tls(self, host: str, port: int, timeout: float) -> smtplib.SMTP_SSL:
        return smtplib.SMTP_SSL(host, port, timeout=timeout)


DEFAULT_FACTORY = _SmtpFactory()


def _connect(config: SmtpConfig, *, factory: _SmtpFactory) -> smtplib.SMTP | smtplib.SMTP_SSL:
    """Open, secure and (optionally) authenticate one SMTP session."""
    if config.encryption == "tls":
        client: smtplib.SMTP | smtplib.SMTP_SSL = factory.tls(
            config.host, config.port, config.timeout_s
        )
    else:
        client = factory.plain(config.host, config.port, config.timeout_s)
    try:
        client.ehlo()
        if config.encryption == "starttls":
            client.starttls()
            client.ehlo()
        if config.username:
            client.login(config.username, config.password)
    except Exception:
        with contextlib.suppress(Exception):
            client.quit()
        raise
    return client


def _classify_smtp_exception(exc: Exception) -> tuple[SendOutcome, str, str]:
    """Map an smtplib failure to (outcome, error_code, error_class)."""
    if isinstance(exc, smtplib.SMTPAuthenticationError):
        return "perm_error", f"smtp_{exc.smtp_code}", AUTH
    if isinstance(exc, smtplib.SMTPRecipientsRefused):
        return "perm_error", "smtp_recipients_refused", RECIPIENT_REFUSED
    if isinstance(exc, smtplib.SMTPSenderRefused):
        return "perm_error", f"smtp_{exc.smtp_code}", CONFIG
    if isinstance(exc, smtplib.SMTPDataError):
        code = int(exc.smtp_code or 0)
        if 400 <= code <= 499:
            return "temp_error", f"smtp_{code}", TEMPORARY
        return "perm_error", f"smtp_{code}", RECIPIENT_REFUSED
    # NB: SMTPConnectError subclasses SMTPResponseException — connectivity
    # must be classified before generic response codes.
    if isinstance(exc, (smtplib.SMTPConnectError, smtplib.SMTPServerDisconnected)):
        return "temp_error", "smtp_connect", NETWORK
    if isinstance(exc, smtplib.SMTPResponseException):
        code = int(exc.smtp_code or 0)
        if code and 400 <= code <= 499:
            return "temp_error", f"smtp_{code}", TEMPORARY
        if code and 500 <= code <= 599:
            return "perm_error", f"smtp_{code}", RECIPIENT_REFUSED
        return "temp_error", "smtp_response", TEMPORARY
    if isinstance(exc, (TimeoutError, socket.timeout)):
        return "temp_error", "timeout", TIMEOUT
    if isinstance(exc, (OSError, smtplib.SMTPException)):
        # Includes TLS handshake failures: temporary (retry bounded), never
        # misreported as delivered.
        message = str(exc).lower()
        if "starttls" in message or "tls" in message or "ssl" in message:
            return "temp_error", "smtp_tls", NETWORK
        return "temp_error", "smtp_error", NETWORK
    return "temp_error", "smtp_unknown", NETWORK


def send_email(
    config: SmtpConfig,
    *,
    to_address: str,
    subject: str,
    text_body: str,
    factory: _SmtpFactory | None = None,
) -> SmtpSendResult:
    """Send one message (bounded, classified).

    Returns ``accepted`` only after the relay confirmed receipt (``send_message``
    without an exception). The provider id is the relay's queue id when the
    fake/relay exposes one — never fabricated (``None`` for plain smtplib,
    whose ``send_message`` returns only refused recipients).
    """
    active = factory or DEFAULT_FACTORY
    try:
        message = render_message(
            config, to_address=to_address, subject=subject, text_body=text_body
        )
    except ValueError as exc:
        logger.warning("smtp message rejected: %s", exc)
        detail = str(exc)
        if "размер" in detail:
            return SmtpSendResult(
                outcome="perm_error", error_code="too_large", error_class=MESSAGE_TOO_LARGE
            )
        return SmtpSendResult(
            outcome="perm_error", error_code="invalid", error_class=INVALID_ADDRESS
        )
    try:
        client = _connect(config, factory=active)
    except Exception as exc:
        outcome, code, error_class = _classify_smtp_exception(exc)
        logger.warning("smtp connect/login failed class=%s", error_class)
        return SmtpSendResult(outcome=outcome, error_code=code, error_class=error_class)
    try:
        refused = client.send_message(message)
    except Exception as exc:
        outcome, code, error_class = _classify_smtp_exception(exc)
        logger.warning("smtp send failed class=%s", error_class)
        with contextlib.suppress(Exception):
            client.quit()
        return SmtpSendResult(outcome=outcome, error_code=code, error_class=error_class)
    with contextlib.suppress(Exception):
        client.quit()
    if refused:
        # smtplib reports per-recipient refusals instead of raising when at
        # least one recipient was accepted; with a single recipient any
        # refusal is a permanent failure.
        logger.warning("smtp recipient refused")
        return SmtpSendResult(
            outcome="perm_error", error_code="smtp_refused", error_class=RECIPIENT_REFUSED
        )
    return SmtpSendResult(outcome="accepted")


def check_connection(config: SmtpConfig, *, factory: _SmtpFactory | None = None) -> SmtpCheckResult:
    """Validate host/port/encryption/credentials WITHOUT sending mail.

    Opens the session, negotiates encryption, authenticates and quits.
    """
    active = factory or DEFAULT_FACTORY
    try:
        validate_mailbox(config.from_address, field="from")
    except ValueError:
        return SmtpCheckResult(ok=False, error_class=CONFIG)
    try:
        client = _connect(config, factory=active)
    except Exception as exc:
        _, _, error_class = _classify_smtp_exception(exc)
        logger.warning("smtp check failed class=%s", error_class)
        return SmtpCheckResult(ok=False, error_class=error_class)
    with contextlib.suppress(Exception):
        client.quit()
    return SmtpCheckResult(ok=True)
