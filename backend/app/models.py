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
    # Candidate lifecycle (roadmap phase: candidates database).
    CANDIDATE_CREATED = "candidate_created"
    CANDIDATE_UPDATED = "candidate_updated"
    CANDIDATE_STAGE_CHANGED = "candidate_stage_changed"
    CANDIDATE_DELETED = "candidate_deleted"
    CANDIDATE_RESTORED = "candidate_restored"
    CANDIDATE_INTERACTION_ADDED = "candidate_interaction_added"
    DUPLICATE_CANDIDATE_CREATED = "duplicate_candidate_created"
    CANDIDATE_TRANSFERRED = "candidate_transferred"


class CandidateStage(StrEnum):
    """Recruitment funnel stages — the single source of truth (PRODUCT_SPEC §5).

    The same vocabulary is mirrored in ``frontend/src/types.ts``
    (``CandidateStage`` / ``STAGE_LABELS``). Do not rename or add members
    without updating the frontend contract and the funnel order below.
    """

    NEW = "new"
    CONTACTED = "contacted"
    REACHED = "reached"
    INTERVIEW_SCHEDULED = "interview_scheduled"
    INTERVIEW_DONE = "interview_done"
    OFFER = "offer"
    HIRED = "hired"
    STARTED = "started"  # «вышел» (вышел на работу)
    PROBATION = "probation"
    FIRED = "fired"
    REJECTED = "rejected"


# Funnel order used for `stage_position` (sorting by stage) — single source of
# truth. Terminal outcomes (fired/rejected) come last and are not part of the
# conversion funnel.
CANDIDATE_STAGE_ORDER: tuple[CandidateStage, ...] = (
    CandidateStage.NEW,
    CandidateStage.CONTACTED,
    CandidateStage.REACHED,
    CandidateStage.INTERVIEW_SCHEDULED,
    CandidateStage.INTERVIEW_DONE,
    CandidateStage.OFFER,
    CandidateStage.HIRED,
    CandidateStage.STARTED,
    CandidateStage.PROBATION,
    CandidateStage.FIRED,
    CandidateStage.REJECTED,
)

CANDIDATE_STAGE_POSITION: dict[CandidateStage, int] = {
    stage: index for index, stage in enumerate(CANDIDATE_STAGE_ORDER)
}


class CandidateSource(StrEnum):
    """Candidate acquisition sources (same vocabulary as the design prototype).

    Admin-managed source catalogs arrive with the catalog/dictionaries phase;
    until then this closed vocabulary keeps backend and frontend aligned.
    """

    SITE = "site"
    REFERRAL = "referral"
    HH_MANUAL = "hh_manual"
    UNIVERSITY = "university"
    EVENT = "event"
    AGENCY = "agency"
    INBOUND_CALL = "inbound_call"


class CandidateInteractionType(StrEnum):
    """Kinds of recorded interactions with a candidate.

    ``transfer`` is intentionally absent: ownership transfer is a separate
    operation with its own audit trail (next phase).
    """

    CALL = "call"
    EMAIL = "email"
    MEETING = "meeting"
    NOTE = "note"
    STATUS_CHANGE = "status_change"


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
    metadata and free-form contextual details. Candidate-scoped events store
    the candidate id in ``candidate_id``; candidate personal data is never
    copied into the audit row.
    """

    __tablename__ = "audit_log"
    __table_args__ = (
        Index("ix_audit_log_created_at", "created_at"),
        Index("ix_audit_log_action", "action"),
        Index("ix_audit_log_user_id", "user_id"),
        Index("ix_audit_log_actor_user_id", "actor_user_id"),
        Index("ix_audit_log_candidate_id", "candidate_id"),
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
    # Candidate the event refers to (candidate lifecycle events only).
    candidate_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("candidates.id", ondelete="SET NULL"), nullable=True
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


class Candidate(Base):
    """A recruitment candidate — the core business entity of the system.

    Personal data (phone/email) is stored both raw (display) and normalized
    (duplicate detection). Deletion is always soft: ``deleted_at`` is set and
    deleted candidates disappear from regular lists; physical deletion does
    not exist.
    """

    __tablename__ = "candidates"
    __table_args__ = (
        CheckConstraint(
            "stage IN ('new', 'contacted', 'reached', 'interview_scheduled', "
            "'interview_done', 'offer', 'hired', 'started', 'probation', "
            "'fired', 'rejected')",
            name="ck_candidates_stage_valid",
        ),
        CheckConstraint(
            "source IN ('site', 'referral', 'hh_manual', 'university', 'event', "
            "'agency', 'inbound_call')",
            name="ck_candidates_source_valid",
        ),
        Index("ix_candidates_owner_user_id", "owner_user_id"),
        Index("ix_candidates_stage", "stage"),
        Index("ix_candidates_full_name_normalized", "full_name_normalized"),
        Index("ix_candidates_phone_normalized", "phone_normalized"),
        Index("ix_candidates_email_normalized", "email_normalized"),
        Index("ix_candidates_deleted_at", "deleted_at"),
        Index("ix_candidates_updated_at", "updated_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=_new_uuid)
    full_name: Mapped[str] = mapped_column(String(200), nullable=False)
    # Unicode-aware casefold (Python, not DB lower()): SQLite's lower() does
    # not fold Cyrillic, so search normalizes in Python on both databases.
    full_name_normalized: Mapped[str] = mapped_column(String(200), nullable=False)
    phone: Mapped[str | None] = mapped_column(String(32), nullable=True)
    phone_normalized: Mapped[str | None] = mapped_column(String(32), nullable=True)
    email: Mapped[str | None] = mapped_column(String(254), nullable=True)
    email_normalized: Mapped[str | None] = mapped_column(String(254), nullable=True)
    source: Mapped[CandidateSource] = mapped_column(
        Enum(
            CandidateSource,
            native_enum=False,
            length=32,
            values_callable=lambda enum_cls: [member.value for member in enum_cls],
        ),
        nullable=False,
    )
    position: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    owner_user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    stage: Mapped[CandidateStage] = mapped_column(
        Enum(
            CandidateStage,
            native_enum=False,
            length=32,
            values_callable=lambda enum_cls: [member.value for member in enum_cls],
        ),
        nullable=False,
        default=CandidateStage.NEW,
    )
    # Funnel position of the stage: enables correct server-side sorting by
    # stage without a client-side dictionary.
    stage_position: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False
    )
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    deleted_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )

    owner: Mapped[User] = relationship(foreign_keys=[owner_user_id])
    interactions: Mapped[list["CandidateInteraction"]] = relationship(
        back_populates="candidate", cascade="all, delete-orphan"
    )
    transfers: Mapped[list["CandidateTransfer"]] = relationship(
        back_populates="candidate",
        cascade="all, delete-orphan",
        order_by="CandidateTransfer.created_at",
    )

    @property
    def is_deleted(self) -> bool:
        return self.deleted_at is not None

    @property
    def owner_username(self) -> str:
        """Username of the responsible user (lazy relationship access)."""
        return self.owner.username if self.owner is not None else ""

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<Candidate id={self.id} stage={self.stage} owner_id={self.owner_user_id}>"


class CandidateInteraction(Base):
    """One recorded interaction with a candidate (call, email, meeting, note).

    Appended by the owning HR (or a manager/admin); entries are immutable.
    """

    __tablename__ = "candidate_interactions"
    __table_args__ = (
        CheckConstraint(
            "type IN ('call', 'email', 'meeting', 'note', 'status_change')",
            name="ck_candidate_interactions_type_valid",
        ),
        Index("ix_candidate_interactions_candidate_id", "candidate_id"),
        Index("ix_candidate_interactions_author_user_id", "author_user_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=_new_uuid)
    candidate_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("candidates.id", ondelete="CASCADE"), nullable=False
    )
    author_user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    type: Mapped[CandidateInteractionType] = mapped_column(
        Enum(
            CandidateInteractionType,
            native_enum=False,
            length=32,
            values_callable=lambda enum_cls: [member.value for member in enum_cls],
        ),
        nullable=False,
    )
    comment: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )

    candidate: Mapped[Candidate] = relationship(back_populates="interactions")
    author: Mapped[User] = relationship(foreign_keys=[author_user_id])

    @property
    def author_username(self) -> str:
        """Username of the interaction author (lazy relationship access)."""
        return self.author.username if self.author is not None else ""

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<CandidateInteraction id={self.id} candidate_id={self.candidate_id}>"


class CandidateTransfer(Base):
    """Immutable ownership-transfer record for a candidate.

    The reason is a business field of the transfer history (shown to HRs,
    managers and admins with visibility on the candidate) — it is never
    written to audit details or application logs.
    """

    __tablename__ = "candidate_transfers"
    __table_args__ = (
        CheckConstraint(
            "length(trim(reason)) > 0",
            name="ck_candidate_transfers_reason_not_blank",
        ),
        Index("ix_candidate_transfers_candidate_id", "candidate_id"),
        Index("ix_candidate_transfers_initiator_user_id", "initiator_user_id"),
        Index("ix_candidate_transfers_created_at", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=_new_uuid)
    candidate_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("candidates.id", ondelete="CASCADE"), nullable=False
    )
    initiator_user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    from_user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    to_user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    reason: Mapped[str] = mapped_column(String(500), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )

    candidate: Mapped[Candidate] = relationship(back_populates="transfers")
    initiator: Mapped[User] = relationship(foreign_keys=[initiator_user_id])
    from_user: Mapped[User] = relationship(foreign_keys=[from_user_id])
    to_user: Mapped[User] = relationship(foreign_keys=[to_user_id])

    @property
    def initiator_username(self) -> str:
        """Username of the transfer initiator (lazy relationship access)."""
        return self.initiator.username if self.initiator is not None else ""

    @property
    def from_username(self) -> str:
        """Username of the previous owner (lazy relationship access)."""
        return self.from_user.username if self.from_user is not None else ""

    @property
    def to_username(self) -> str:
        """Username of the new owner (lazy relationship access)."""
        return self.to_user.username if self.to_user is not None else ""

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<CandidateTransfer id={self.id} candidate_id={self.candidate_id}>"
