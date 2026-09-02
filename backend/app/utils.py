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
