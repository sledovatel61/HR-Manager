"""ORM models for identity and security (roadmap phase 2).

Three tables:

* ``users``          accounts, roles and lockout state;
* ``user_sessions``  short-lived server-side sessions (a cookie only holds a
                     random session id; sessions can be revoked server-side);
* ``audit_log``      append-only security/business audit trail.

The schema is intentionally written in database-agnostic SQLAlchemy types so
the same models run on PostgreSQL (development, production, integration
tests) and on in-memory SQLite (isolated unit tests, APP_ENV=test only).
PostgreSQL additionally gets native ``TIMESTAMP WITH TIME ZONE`` and native
UUID columns via the Alembic migration.
"""

import uuid
from datetime import datetime
from enum import StrEnum

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from app.utils import utc_now


class Base(DeclarativeBase):
    """Declarative base for all ORM models."""


class UserRole(StrEnum):
    """Application roles.

    ``admin`` is a superset role used for user/role administration. The HR
    role works with its own queue; managers see the whole candidate base
    (candidate features arrive in later phases).
    """

    HR = "hr"
    MANAGER = "manager"
    ADMIN = "admin"


class AuditAction(StrEnum):
    """Audited security events."""

    LOGIN_SUCCESS = "login_success"
    LOGIN_FAILURE = "login_failure"
    LOGOUT = "logout"
    ACCOUNT_LOCKED = "account_locked"
    USER_CREATED = "user_created"
    USER_UPDATED = "user_updated"
    USER_DEACTIVATED = "user_deactivated"
    USER_REACTIVATED = "user_reactivated"
    USER_UNLOCKED = "user_unlocked"
    ROLE_CHANGED = "role_changed"


def _new_uuid() -> uuid.UUID:
    """Generate a new UUID (single call site, easy to patch in tests)."""
    return uuid.uuid4()


class User(Base):
    """An application user (HR, manager or administrator)."""

    __tablename__ = "users"
    __table_args__ = (
        CheckConstraint(
            "role IN ('hr', 'manager', 'admin')",
            name="ck_users_role_valid",
        ),
        CheckConstraint(
            "failed_login_count >= 0",
            name="ck_users_failed_login_count_non_negative",
        ),
        # Usernames are unique case-insensitively.
        Index("ix_users_username_lower", text("lower(username)"), unique=True),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=_new_uuid)
    username: Mapped[str] = mapped_column(String(64), nullable=False)
    full_name: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    role: Mapped[UserRole] = mapped_column(
        Enum(
            UserRole,
            native_enum=False,
            length=16,
            values_callable=lambda enum_cls: [member.value for member in enum_cls],
        ),
        nullable=False,
    )
    # Only an Argon2id hash is ever stored — never a plaintext password.
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    is_active: Mapped[bool] = mapped_column(default=True, nullable=False)

    failed_login_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    locked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False
    )

    sessions: Mapped[list["UserSession"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<User id={self.id} username={self.username!r} role={self.role}>"


class UserSession(Base):
    """A server-side user session.

    The browser cookie holds only ``id`` (a random UUID). All state lives in
    the database, so logout and expiry revoke sessions immediately.
    """

    __tablename__ = "user_sessions"
    __table_args__ = (
        Index("ix_user_sessions_expires_at", "expires_at"),
        Index("ix_user_sessions_user_id", "user_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=_new_uuid)
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    # CSRF token bound to this session (double-submit pattern).
    csrf_token: Mapped[str] = mapped_column(String(64), nullable=False)
    ip_address: Mapped[str | None] = mapped_column(String(64), nullable=True)
    user_agent: Mapped[str | None] = mapped_column(String(400), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    user: Mapped[User] = relationship(back_populates="sessions")

    @property
    def is_active(self) -> bool:
        """Whether the session is neither revoked nor expired."""
        from app.utils import ensure_aware

        return self.revoked_at is None and ensure_aware(self.expires_at) > utc_now()

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<UserSession id={self.id} user_id={self.user_id}>"


class AuditEvent(Base):
    """Append-only audit trail entry.

    Personal data is never written here: only usernames, event names, client
    metadata and free-form contextual details.
    """

    __tablename__ = "audit_log"
    __table_args__ = (
        Index("ix_audit_log_created_at", "created_at"),
        Index("ix_audit_log_action", "action"),
        Index("ix_audit_log_user_id", "user_id"),
        Index("ix_audit_log_actor_user_id", "actor_user_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=_new_uuid)
    action: Mapped[AuditAction] = mapped_column(
        Enum(
            AuditAction,
            native_enum=False,
            length=32,
            values_callable=lambda enum_cls: [member.value for member in enum_cls],
        ),
        nullable=False,
    )
    # Subject of the event (e.g. the created/deactivated user); nullable for
    # events without a subject (a failed login for an unknown username).
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    # Who performed the action. Set to the authenticating user for login
    # events; NULL only when the username is unknown.
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    username: Mapped[str | None] = mapped_column(String(64), nullable=True)
    ip_address: Mapped[str | None] = mapped_column(String(64), nullable=True)
    user_agent: Mapped[str | None] = mapped_column(String(400), nullable=True)
    details: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<AuditEvent id={self.id} action={self.action}>"
