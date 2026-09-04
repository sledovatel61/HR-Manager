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
from datetime import UTC, datetime
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
from sqlalchemy.engine.interfaces import Dialect
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship
from sqlalchemy.types import TypeDecorator

from app.utils import utc_now


class Base(DeclarativeBase):
    """Declarative base for all ORM models."""


class UTCDateTime(TypeDecorator):
    """Datetime column that is always timezone-aware UTC at the ORM boundary.

    PostgreSQL ``timestamptz`` round-trips aware datetimes, but SQLite's
    DATETIME has no timezone support and returns naive values. Serializing a
    naive value shifts timestamps by the machine-local offset on non-UTC
    hosts (a Moscow reviewer saw termination times move by 3 hours). This
    decorator normalizes both directions: naive input is interpreted as UTC
    (the documented API contract), and reads always carry ``tzinfo=UTC`` so
    API responses never depend on the host timezone.
    """

    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect: Dialect) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            value = value.replace(tzinfo=UTC)
        return value.astimezone(UTC)

    def process_result_value(self, value: datetime | None, dialect: Dialect) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)


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
    # Calendar events (roadmap phase: events and calendar).
    EVENT_CREATED = "event_created"
    EVENT_UPDATED = "event_updated"
    EVENT_RESCHEDULED = "event_rescheduled"
    EVENT_COMPLETED = "event_completed"
    EVENT_POSTPONED = "event_postponed"
    EVENT_ASSIGNEE_CHANGED = "event_assignee_changed"
    # Analytics (roadmap phase: analytics and reports).
    CANDIDATE_TERMINATED = "candidate_terminated"
    ANALYTICS_EXPORTED = "analytics_exported"
    # Backup, deployment and release (roadmap phase: backup, deployment, release).
    BACKUP_STARTED = "backup_started"
    BACKUP_SUCCEEDED = "backup_succeeded"
    BACKUP_FAILED = "backup_failed"
    BACKUP_VERIFY_FAILED = "backup_verify_failed"
    BACKUP_RESTORE_DRILL_STARTED = "backup_restore_drill_started"
    BACKUP_RESTORE_DRILL_SUCCEEDED = "backup_restore_drill_succeeded"
    BACKUP_RESTORE_DRILL_FAILED = "backup_restore_drill_failed"
    BACKUP_RETENTION_CLEANED = "backup_retention_cleaned"
    DEPLOY_RECORDED = "deploy_recorded"
    RELEASE_RECORDED = "release_recorded"


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


class EventType(StrEnum):
    """Kinds of calendar events tied to a candidate (PRODUCT_SPEC §5).

    ``call``/``interview`` are scheduled activities with an optional
    ``remind_at``; ``reminder`` is a pure reminder whose ``starts_at`` is
    the reminder moment itself. No other types exist on purpose — the
    vocabulary is a closed API contract mirrored in
    ``frontend/src/types.ts``.
    """

    CALL = "call"
    INTERVIEW = "interview"
    REMINDER = "reminder"


class EventStatus(StrEnum):
    """Lifecycle of an event: planned, done or postponed.

    ``completed`` is terminal (no further edits). ``postponed`` requires a
    new ``starts_at`` (postponing always re-schedules).
    """

    SCHEDULED = "scheduled"
    COMPLETED = "completed"
    POSTPONED = "postponed"


class EventHistoryKind(StrEnum):
    """Kinds of immutable business-history entries for an event."""

    CREATED = "created"
    UPDATED = "updated"
    RESCHEDULED = "rescheduled"
    COMPLETED = "completed"
    POSTPONED = "postponed"
    ASSIGNEE_CHANGED = "assignee_changed"


class Event(Base):
    """A calendar event bound to a candidate (call, interview, reminder).

    All timestamps are timezone-aware UTC. ``version`` is the optimistic
    concurrency counter: every mutation must carry the current
    ``expected_version`` and bumps it. Deletion is physical only through
    the candidate FK cascade; there is no event delete endpoint.
    """

    __tablename__ = "events"
    __table_args__ = (
        CheckConstraint(
            "type IN ('call', 'interview', 'reminder')",
            name="ck_events_type_valid",
        ),
        CheckConstraint(
            "status IN ('scheduled', 'completed', 'postponed')",
            name="ck_events_status_valid",
        ),
        CheckConstraint(
            "length(trim(title)) > 0",
            name="ck_events_title_not_blank",
        ),
        CheckConstraint(
            "ends_at IS NULL OR ends_at > starts_at",
            name="ck_events_ends_after_starts",
        ),
        CheckConstraint(
            "remind_at IS NULL OR remind_at <= starts_at",
            name="ck_events_remind_before_start",
        ),
        CheckConstraint(
            "(status = 'completed' AND completed_at IS NOT NULL) "
            "OR (status <> 'completed' AND completed_at IS NULL)",
            name="ck_events_completed_at_consistent",
        ),
        Index("ix_events_candidate_id", "candidate_id"),
        Index("ix_events_assignee_user_id", "assignee_user_id"),
        Index("ix_events_starts_at", "starts_at"),
        Index("ix_events_status", "status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=_new_uuid)
    candidate_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("candidates.id", ondelete="CASCADE"), nullable=False
    )
    author_user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    assignee_user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    type: Mapped[EventType] = mapped_column(
        Enum(
            EventType,
            native_enum=False,
            length=32,
            values_callable=lambda enum_cls: [member.value for member in enum_cls],
        ),
        nullable=False,
    )
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[EventStatus] = mapped_column(
        Enum(
            EventStatus,
            native_enum=False,
            length=32,
            values_callable=lambda enum_cls: [member.value for member in enum_cls],
        ),
        nullable=False,
        default=EventStatus.SCHEDULED,
    )
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ends_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    remind_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False
    )

    candidate: Mapped[Candidate] = relationship(foreign_keys=[candidate_id])
    author: Mapped[User] = relationship(foreign_keys=[author_user_id])
    assignee: Mapped[User] = relationship(foreign_keys=[assignee_user_id])
    history: Mapped[list["EventHistory"]] = relationship(
        back_populates="event", cascade="all, delete-orphan", order_by="EventHistory.created_at"
    )

    @property
    def author_username(self) -> str:
        """Username of the event author (lazy relationship access)."""
        return self.author.username if self.author is not None else ""

    @property
    def assignee_username(self) -> str:
        """Username of the assignee (lazy relationship access)."""
        return self.assignee.username if self.assignee is not None else ""

    @property
    def candidate_full_name(self) -> str:
        """Candidate name for list rendering (lazy relationship access)."""
        return self.candidate.full_name if self.candidate is not None else ""

    @property
    def is_overdue(self) -> bool:
        """A scheduled event whose start has passed is overdue."""
        return self.status == EventStatus.SCHEDULED and self.starts_at <= utc_now()

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<Event id={self.id} type={self.type} status={self.status}>"


class EventHistory(Base):
    """Immutable business history of one event mutation.

    One row per mutation with typed old/new values for the safe fields
    (timestamps, ids, status). ``title``/``note`` are recorded only as
    changed-flags — their content is never copied into history or audit.
    """

    __tablename__ = "event_history"
    __table_args__ = (
        CheckConstraint(
            "kind IN ('created', 'updated', 'rescheduled', 'completed', "
            "'postponed', 'assignee_changed')",
            name="ck_event_history_kind_valid",
        ),
        Index("ix_event_history_event_id", "event_id"),
        Index("ix_event_history_created_at", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=_new_uuid)
    event_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("events.id", ondelete="CASCADE"), nullable=False
    )
    changed_by_user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    kind: Mapped[EventHistoryKind] = mapped_column(
        Enum(
            EventHistoryKind,
            native_enum=False,
            length=32,
            values_callable=lambda enum_cls: [member.value for member in enum_cls],
        ),
        nullable=False,
    )
    status_old: Mapped[str | None] = mapped_column(String(32), nullable=True)
    status_new: Mapped[str | None] = mapped_column(String(32), nullable=True)
    starts_at_old: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    starts_at_new: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    ends_at_old: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    ends_at_new: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    remind_at_old: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    remind_at_new: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    assignee_user_id_old: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    assignee_user_id_new: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    title_changed: Mapped[bool] = mapped_column(nullable=False, default=False)
    note_changed: Mapped[bool] = mapped_column(nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )

    event: Mapped[Event] = relationship(back_populates="history")
    changed_by: Mapped[User] = relationship(foreign_keys=[changed_by_user_id])

    @property
    def changed_by_username(self) -> str:
        """Username of the user who performed the mutation."""
        return self.changed_by.username if self.changed_by is not None else ""

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<EventHistory id={self.id} event_id={self.event_id} kind={self.kind}>"


class AnalyticsFactType(StrEnum):
    """Kinds of immutable analytics facts recorded in the append-only ledger.

    The ledger is the single source of truth for Phase 6 metrics. One row is
    written in the SAME transaction as the business operation it describes;
    partial unique indexes make the write idempotent per business row.
    """

    CANDIDATE_CREATED = "candidate_created"
    INTERACTION_ADDED = "interaction_added"
    STAGE_CHANGED = "stage_changed"
    TRANSFER = "transfer"
    EVENT_CREATED = "event_created"
    EVENT_COMPLETED = "event_completed"
    TERMINATED = "terminated"


class AnalyticsFact(Base):
    """Append-only ledger of analytics facts (Phase 6 single source of truth).

    ``fact_at`` is the UTC instant the fact happened (``from <= fact_at < to``
    period semantics). ``owner_user_id`` snapshots the responsible HR AT the
    fact moment (transfers do not rewrite history); ``source`` snapshots the
    candidate source at the fact moment (never the edited-later value).
    Rows are never updated or deleted by application code.
    """

    __tablename__ = "analytics_facts"
    __table_args__ = (
        CheckConstraint(
            "fact_type IN ('candidate_created', 'interaction_added', "
            "'stage_changed', 'transfer', 'event_created', 'event_completed', "
            "'terminated')",
            name="ck_analytics_facts_type_valid",
        ),
        Index("ix_analytics_facts_fact_at", "fact_at"),
        Index("ix_analytics_facts_fact_at_owner", "fact_at", "owner_user_id"),
        Index("ix_analytics_facts_fact_at_source", "fact_at", "source"),
        Index("ix_analytics_facts_candidate_id", "candidate_id"),
        Index("ix_analytics_facts_type", "fact_type"),
        # Idempotency: one fact per business row (partial unique indexes).
        Index(
            "uq_analytics_facts_created_candidate",
            "candidate_id",
            unique=True,
            postgresql_where=text("fact_type = 'candidate_created'"),
            sqlite_where=text("fact_type = 'candidate_created'"),
        ),
        Index(
            "uq_analytics_facts_interaction",
            "interaction_id",
            unique=True,
            postgresql_where=text("interaction_id IS NOT NULL"),
            sqlite_where=text("interaction_id IS NOT NULL"),
        ),
        # (event_id, fact_type, fact_at): a legitimate second completion is
        # a new fact; only exact duplicates are blocked.
        Index(
            "uq_analytics_facts_event",
            "event_id",
            "fact_type",
            "fact_at",
            unique=True,
            postgresql_where=text("event_id IS NOT NULL"),
            sqlite_where=text("event_id IS NOT NULL"),
        ),
        Index(
            "uq_analytics_facts_transfer",
            "transfer_id",
            unique=True,
            postgresql_where=text("transfer_id IS NOT NULL"),
            sqlite_where=text("transfer_id IS NOT NULL"),
        ),
        Index(
            "uq_analytics_facts_termination",
            "termination_id",
            unique=True,
            postgresql_where=text("termination_id IS NOT NULL"),
            sqlite_where=text("termination_id IS NOT NULL"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=_new_uuid)
    candidate_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("candidates.id", ondelete="CASCADE"), nullable=False
    )
    fact_type: Mapped[AnalyticsFactType] = mapped_column(
        Enum(
            AnalyticsFactType,
            native_enum=False,
            length=32,
            values_callable=lambda enum_cls: [member.value for member in enum_cls],
        ),
        nullable=False,
    )
    fact_subtype: Mapped[str | None] = mapped_column(
        String(32), nullable=True
    )  # interaction type / event type
    fact_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    stage_from: Mapped[str | None] = mapped_column(String(32), nullable=True)
    stage_to: Mapped[str | None] = mapped_column(String(32), nullable=True)
    source: Mapped[str | None] = mapped_column(String(32), nullable=True)
    owner_user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    interaction_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("candidate_interactions.id", ondelete="CASCADE"), nullable=True
    )
    event_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("events.id", ondelete="CASCADE"), nullable=True
    )
    transfer_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("candidate_transfers.id", ondelete="CASCADE"), nullable=True
    )
    termination_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("candidate_terminations.id", ondelete="CASCADE"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )

    candidate: Mapped[Candidate] = relationship(foreign_keys=[candidate_id])
    owner: Mapped[User] = relationship(foreign_keys=[owner_user_id])

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<AnalyticsFact id={self.id} type={self.fact_type}>"


class CandidateTermination(Base):
    """A business termination event (dismissal from the company) with a date
    and a non-empty safe reason.

    Deliberately separate from the ``fired`` stage: a current stage alone
    cannot prove when (or why) the termination happened. This entity is the
    analytics source of truth for the ``terminated`` metric.
    """

    __tablename__ = "candidate_terminations"
    __table_args__ = (
        CheckConstraint(
            "length(trim(reason)) > 0",
            name="ck_candidate_terminations_reason_not_blank",
        ),
        Index("ix_candidate_terminations_candidate_id", "candidate_id"),
        Index("ix_candidate_terminations_terminated_at", "terminated_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=_new_uuid)
    candidate_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("candidates.id", ondelete="CASCADE"), nullable=False
    )
    terminated_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    reason: Mapped[str] = mapped_column(String(500), nullable=False)
    created_by_user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )

    candidate: Mapped[Candidate] = relationship(foreign_keys=[candidate_id])
    created_by: Mapped[User] = relationship(foreign_keys=[created_by_user_id])

    @property
    def created_by_username(self) -> str:
        """Username of the user who recorded the termination."""
        return self.created_by.username if self.created_by is not None else ""

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<CandidateTermination id={self.id} candidate_id={self.candidate_id}>"
