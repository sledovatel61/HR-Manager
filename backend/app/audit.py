"""Audit trail helpers.

Every security-relevant event (login attempts, logout, lockouts, admin user
changes) is recorded in the append-only ``audit_log`` table. Secrets and
personal data are never written — only usernames, action names, client
metadata and short contextual details.
"""

import logging
from uuid import UUID

from sqlalchemy.orm import Session

from app.models import AuditAction, AuditEvent, User
from app.utils import utc_now

logger = logging.getLogger(__name__)

# User-Agent and details are stored truncated to column width.
_DETAILS_MAX_LENGTH = 2000
_USER_AGENT_MAX_LENGTH = 400


def record_event(
    db: Session,
    action: AuditAction,
    *,
    actor: User | UUID | None = None,
    subject: User | UUID | None = None,
    candidate_id: UUID | None = None,
    username: str | None = None,
    ip_address: str | None = None,
    user_agent: str | None = None,
    details: str | None = None,
    commit: bool = True,
) -> AuditEvent:
    """Append an audit event.

    ``actor`` / ``subject`` accept either a ``User`` or a UUID. The caller
    normally already holds the user object; login-failure for an unknown
    username passes only ``username``. ``candidate_id`` links candidate
    lifecycle events to their candidate (never personal data).
    """

    def as_uuid(value: User | UUID | None) -> UUID | None:
        if value is None:
            return None
        return value.id if isinstance(value, User) else value

    event = AuditEvent(
        action=action,
        actor_user_id=as_uuid(actor),
        user_id=as_uuid(subject),
        candidate_id=candidate_id,
        username=username[:64] if username else None,
        ip_address=ip_address[:64] if ip_address else None,
        user_agent=user_agent[:_USER_AGENT_MAX_LENGTH] if user_agent else None,
        details=details[:_DETAILS_MAX_LENGTH] if details else None,
        created_at=utc_now(),
    )
    db.add(event)
    if commit:
        db.commit()
    # Log without sensitive data: action + username + IP only.
    logger.info(
        "audit %s username=%s ip=%s",
        action.value,
        username or "-",
        ip_address or "-",
    )
    return event
