"""Small cross-cutting helpers used across the backend."""

from datetime import UTC, datetime

from fastapi import Request
from starlette.datastructures import Headers


def utc_now() -> datetime:
    """Return the current time as a timezone-aware UTC datetime."""
    return datetime.now(UTC)


def ensure_aware(value: datetime) -> datetime:
    """Normalize a possibly-naive datetime (SQLite) to timezone-aware UTC.

    SQLAlchemy returns timezone-naive datetimes on SQLite even for
    ``DateTime(timezone=True)`` columns; PostgreSQL returns aware ones.
    Business logic must compare datetimes, so normalize in one place.
    """
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value


def client_ip(request: Request) -> str | None:
    """Best-effort client IP.

    The stack runs behind nginx, which sets ``X-Forwarded-For``. The
    left-most (original client) address is used; the value is stored for the
    audit trail and rate limiting and is never trusted for authorization.
    """
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        first = forwarded.split(",")[0].strip()
        if first:
            return first[:64]
    if request.client is not None:
        return request.client.host[:64]
    return None


def user_agent(headers: Headers) -> str | None:
    """Return a truncated User-Agent string for the audit trail."""
    value = headers.get("user-agent")
    return value[:400] if value else None


def normalize_phone(value: str | None) -> str | None:
    """Normalize a phone for duplicate detection.

    Keeps digits only, canonicalizes an 11-digit leading ``8`` to ``7``
    (Russian ``8-XXX-…`` equals ``+7-XXX-…``), and returns a single
    ``+<digits>`` form so that different input spellings of the same number
    compare equal. Never logged and never returned by the API — the raw
    display value lives in ``Candidate.phone``.
    """

    if value is None:
        return None
    digits = "".join(ch for ch in value if ch.isdigit())
    if not digits:
        return None
    if len(digits) == 11 and digits[0] == "8":
        digits = "7" + digits[1:]
    return "+" + digits[:20]


def normalize_email(value: str | None) -> str | None:
    """Normalize an email for duplicate detection (trim + lowercase)."""
    if value is None:
        return None
    cleaned = value.strip().lower()
    return cleaned or None


def normalize_full_name(value: str) -> str:
    """Normalize a full name for search: trim + Unicode casefold.

    Python's ``casefold`` handles Cyrillic correctly, unlike SQLite's
    ASCII-only ``lower()``, so search behaves the same on both databases.
    """
    return value.strip().casefold()
