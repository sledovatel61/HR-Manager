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
    JSON,
    BigInteger,
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
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


class PilotWorkingMode(StrEnum):
    """Phase 12: the working role the pilot owner selected in the Windows
    installer (HR / manager / administrator). It is a profile field for the
    starter interface ONLY — it never weakens or replaces RBAC; the pilot
    owner always has server role ``admin`` plus an explicit
    ``pilot_full_access`` grant."""

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
    EVENT_CANCELLED = "event_cancelled"
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
    # Phase 14: готовность пилота к запуску (read-only, redacted).
    PILOT_READINESS_VIEWED = "pilot_readiness_viewed"
    BACKUP_RETENTION_CLEANED = "backup_retention_cleaned"
    DEPLOY_RECORDED = "deploy_recorded"
    RELEASE_RECORDED = "release_recorded"
    # Phase 8: notification contour and pilot access.
    PREFERENCE_UPDATED = "preference_updated"
    NOTIFICATION_RETRY = "notification_retry"
    NOTIFICATION_CANCEL = "notification_cancel"
    NOTIFICATION_URGENT_OVERRIDE = "notification_urgent_override"
    QUEUE_DIAGNOSTICS_VIEWED = "queue_diagnostics_viewed"
    PILOT_USER_CREATED = "pilot_user_created"
    PILOT_ACCESS_GRANTED = "pilot_access_granted"
    PILOT_ACCESS_REVOKED = "pilot_access_revoked"
    # Phase 9: external channels (Telegram/SMTP), consent and bindings.
    TELEGRAM_LINK_STARTED = "telegram_link_started"
    TELEGRAM_LINK_CONFIRMED = "telegram_link_confirmed"
    TELEGRAM_LINK_CONFLICT = "telegram_link_conflict"
    TELEGRAM_UNLINKED = "telegram_unlinked"
    TELEGRAM_CONSENT_UPDATED = "telegram_consent_updated"
    TELEGRAM_CHECKED = "telegram_checked"
    TELEGRAM_TEST_QUEUED = "telegram_test_queued"
    CHANNEL_AUTO_REVOKED = "channel_auto_revoked"
    EMAIL_ADDRESS_SET = "email_address_set"
    EMAIL_VERIFIED = "email_verified"
    EMAIL_REMOVED = "email_removed"
    EMAIL_CONSENT_UPDATED = "email_consent_updated"
    SMTP_CHECKED = "smtp_checked"
    SMTP_TEST_QUEUED = "smtp_test_queued"
    # Phase 10: one-way candidate communications.
    DOCUMENT_CHANGED = "document_changed"
    DOCUMENT_RULE_CHANGED = "document_rule_changed"
    CANDIDATE_MESSAGE_QUEUED = "candidate_message_queued"
    CANDIDATE_MESSAGE_CANCELLED = "candidate_message_cancelled"
    CANDIDATE_CHANNEL_CONSENT_UPDATED = "candidate_channel_consent_updated"
    CANDIDATE_TELEGRAM_INVITE_CREATED = "candidate_telegram_invite_created"
    CANDIDATE_TELEGRAM_LINKED = "candidate_telegram_linked"
    CANDIDATE_TELEGRAM_UNLINKED = "candidate_telegram_unlinked"
    CANDIDATE_EMAIL_CONFIRM_INITIATED = "candidate_email_confirm_initiated"
    CANDIDATE_EMAIL_CONFIRMED = "candidate_email_confirmed"
    # Phase 12: local pilot first-run (Windows installer exchange).
    PILOT_OWNER_CLAIMED = "pilot_owner_claimed"
    PILOT_OWNER_CREATED = "pilot_owner_created"
    PILOT_SETUP_REJECTED = "pilot_setup_rejected"
    # Phase 13: Windows pilot update channel.
    UPDATE_CHECK_STARTED = "update_check_started"
    UPDATE_CHECK_SUCCEEDED = "update_check_succeeded"
    UPDATE_CHECK_FAILED = "update_check_failed"
    UPDATE_DOWNLOAD_STARTED = "update_download_started"
    UPDATE_DOWNLOAD_SUCCEEDED = "update_download_succeeded"
    UPDATE_DOWNLOAD_FAILED = "update_download_failed"
    UPDATE_INSTALL_REQUESTED = "update_install_requested"
    UPDATE_ENGINE_REPORTED = "update_engine_reported"


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
    # Phase 12: working mode selected in the Windows installer by the pilot
    # owner. Informational only (starter interface); never used for access
    # decisions. ``None`` for users created outside the first-run flow.
    working_mode: Mapped[PilotWorkingMode | None] = mapped_column(
        Enum(
            PilotWorkingMode,
            native_enum=False,
            length=16,
            values_callable=lambda enum_cls: [member.value for member in enum_cls],
        ),
        nullable=True,
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
            length=64,
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
    """Lifecycle of an event: planned, done, postponed or cancelled.

    ``completed`` and ``cancelled`` are terminal (no further edits).
    ``postponed`` requires a new ``starts_at`` (postponing always
    re-schedules). ``cancelled`` was added in phase 8 (notification
    trigger «событие отменено»); it is a documented extension of the
    phase-5 vocabulary.
    """

    SCHEDULED = "scheduled"
    COMPLETED = "completed"
    POSTPONED = "postponed"
    CANCELLED = "cancelled"


class EventHistoryKind(StrEnum):
    """Kinds of immutable business-history entries for an event."""

    CREATED = "created"
    UPDATED = "updated"
    RESCHEDULED = "rescheduled"
    COMPLETED = "completed"
    POSTPONED = "postponed"
    CANCELLED = "cancelled"
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
            "status IN ('scheduled', 'completed', 'postponed', 'cancelled')",
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
        CheckConstraint(
            "(status = 'cancelled' AND cancelled_at IS NOT NULL) "
            "OR (status <> 'cancelled' AND cancelled_at IS NULL)",
            name="ck_events_cancelled_at_consistent",
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
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
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
            "'postponed', 'cancelled', 'assignee_changed')",
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


def _sql_list(values: list[str]) -> str:
    """Render a closed vocabulary as a SQL IN-list for CHECK constraints."""
    return ", ".join(f"'{value}'" for value in values)


# --- Phase 8: notification foundation and pilot mode --------------------------


class NotificationType(StrEnum):
    """Closed vocabulary of internal notification types (phase 8)."""

    EVENT_ASSIGNED = "event_assigned"
    EVENT_APPROACHING = "event_approaching"
    EVENT_OVERDUE = "event_overdue"
    EVENT_RESCHEDULED = "event_rescheduled"
    EVENT_CANCELLED = "event_cancelled"
    CANDIDATE_TRANSFERRED = "candidate_transferred"
    REMINDER_DUE = "reminder_due"
    REMINDER_OVERDUE = "reminder_overdue"
    SYSTEM_ALERT = "system_alert"

    # Phase 10: one-way candidate messages. These types are only ever
    # scheduled on outbox rows addressed to a *candidate* (never to an
    # internal user, never as in-app notifications).
    CANDIDATE_INTERVIEW_SCHEDULED = "candidate_interview_scheduled"
    CANDIDATE_INTERVIEW_REMINDER = "candidate_interview_reminder"
    CANDIDATE_INTERVIEW_RESCHEDULED = "candidate_interview_rescheduled"
    CANDIDATE_INTERVIEW_CANCELLED = "candidate_interview_cancelled"
    CANDIDATE_DOCUMENT_REQUEST = "candidate_document_request"
    CANDIDATE_DOCUMENT_REMINDER = "candidate_document_reminder"
    # The double opt-in letter itself. It is the ONLY candidate email that
    # is delivered without a granted email consent: delivering it is the
    # purpose of the flow. The worker re-validates the token, the address
    # and the candidate state right before the provider call.
    CANDIDATE_EMAIL_CONFIRM = "candidate_email_confirm"


class NotificationPriority(StrEnum):
    LOW = "low"
    NORMAL = "normal"
    HIGH = "high"


class NotificationSource(StrEnum):
    """Who produced the notification (outbox records the same source)."""

    SYSTEM = "system"
    RULE = "rule"
    REMINDER = "reminder"
    MANUAL = "manual"


class DeliveryChannel(StrEnum):
    """Delivery channels. Only ``in_app`` is implemented in phase 8;
    ``email``/``telegram`` are reserved by the contract (phase 9) and any
    job queued for them is honestly marked ``skipped``."""

    IN_APP = "in_app"
    EMAIL = "email"
    TELEGRAM = "telegram"


class DeliveryStatus(StrEnum):
    """Outbox row lifecycle: queued -> sending -> accepted/delivered;
    failed (terminal after retries), cancelled (explicit), skipped
    (channel not configured — never a fake delivered)."""

    QUEUED = "queued"
    SENDING = "sending"
    ACCEPTED = "accepted"
    DELIVERED = "delivered"
    FAILED = "failed"
    CANCELLED = "cancelled"
    SKIPPED = "skipped"


class AttemptOutcome(StrEnum):
    ACCEPTED = "accepted"
    DELIVERED = "delivered"
    FAILED = "failed"
    SKIPPED = "skipped"
    CANCELLED = "cancelled"


class ReminderRecurrence(StrEnum):
    """Closed recurrence set for personal reminders (phase 8 contract)."""

    NONE = "none"
    DAILY = "daily"
    WORKDAYS = "workdays"
    WEEKLY = "weekly"


class ReminderStatus(StrEnum):
    ACTIVE = "active"
    COMPLETED = "completed"
    CANCELLED = "cancelled"


class ReminderImportance(StrEnum):
    LOW = "low"
    NORMAL = "normal"
    HIGH = "high"


class AccessGrantScope(StrEnum):
    """Explicit access grants. ``pilot_full_access`` marks the single pilot
    user of phase 8: the backend keeps all role checks active (the grant is
    an explicit, audited designation — it never bypasses RBAC)."""

    PILOT_FULL_ACCESS = "pilot_full_access"
    DOCUMENT_LISTS_MANAGE = "document_lists_manage"
    CANDIDATE_DOCUMENTS_ALL = "candidate_documents_all"
    UPDATE_CHANNEL_MANAGE = "update_channel_manage"


_NOTIFICATION_TYPES = [member.value for member in NotificationType]
_PRIORITIES = [member.value for member in NotificationPriority]
_SOURCES = [member.value for member in NotificationSource]
_CHANNELS = [member.value for member in DeliveryChannel]
_STATUSES = [member.value for member in DeliveryStatus]
_OUTCOMES = [member.value for member in AttemptOutcome]
_RECURRENCES = [member.value for member in ReminderRecurrence]
_REMINDER_STATUSES = [member.value for member in ReminderStatus]
_IMPORTANCES = [member.value for member in ReminderImportance]
_GRANT_SCOPES = [member.value for member in AccessGrantScope]


class NotificationPreference(Base):
    """Per-user notification settings (timezone, quiet hours, workdays,
    enabled types/channels). A missing row means system defaults; the row
    is created lazily on first read/write by the owning user.

    Phase 9 adds explicit per-channel consent: an external channel delivers
    only when its ``*_opt_in`` flag is true AND a valid binding (Telegram
    chat_id / verified email) exists AND the global configuration is
    enabled. Existing users keep ``opt_in=false`` (never enabled silently).
    """

    __tablename__ = "notification_preferences"
    __table_args__ = (
        CheckConstraint(
            "length(timezone) BETWEEN 1 AND 64",
            name="ck_notification_preferences_tz_len",
        ),
        # Portable format guard: HH:MM shape. Full digit validation lives
        # in the API layer (SQLite has no '~' regex operator).
        CheckConstraint(
            "quiet_hours_start LIKE '__:__'",
            name="ck_notification_preferences_quiet_start_format",
        ),
        CheckConstraint(
            "quiet_hours_end LIKE '__:__'",
            name="ck_notification_preferences_quiet_end_format",
        ),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    timezone: Mapped[str] = mapped_column(String(64), nullable=False)
    quiet_hours_start: Mapped[str] = mapped_column(String(5), nullable=False)
    quiet_hours_end: Mapped[str] = mapped_column(String(5), nullable=False)
    workdays: Mapped[list] = mapped_column(JSON, nullable=False)
    enabled_types: Mapped[list] = mapped_column(JSON, nullable=False)
    enabled_channels: Mapped[list] = mapped_column(JSON, nullable=False)
    # Explicit consent for external channels (phase 9). A channel activates
    # ONLY when both ``*_opt_in`` and ``*_consent_granted`` are explicitly
    # true (the consent API requires both flags to agree); timestamp, source
    # (e.g. "web-ui") and terms version are recorded with every change.
    # ``None``/missing is never consent (fail-closed).
    telegram_opt_in: Mapped[bool] = mapped_column(default=False, nullable=False)
    telegram_consent_granted: Mapped[bool] = mapped_column(default=False, nullable=False)
    telegram_consent_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    telegram_consent_source: Mapped[str | None] = mapped_column(String(32), nullable=True)
    telegram_consent_policy_version: Mapped[str | None] = mapped_column(String(32), nullable=True)
    email_opt_in: Mapped[bool] = mapped_column(default=False, nullable=False)
    email_consent_granted: Mapped[bool] = mapped_column(default=False, nullable=False)
    email_consent_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    email_consent_source: Mapped[str | None] = mapped_column(String(32), nullable=True)
    email_consent_policy_version: Mapped[str | None] = mapped_column(String(32), nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utc_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime, default=utc_now, onupdate=utc_now, nullable=False
    )

    user: Mapped[User] = relationship()


class Notification(Base):
    """A logical in-app notification (immutable snapshot; history never
    edited — a corrected resend creates a new outbox row)."""

    __tablename__ = "notifications"
    __table_args__ = (
        CheckConstraint(
            f"type IN ({_sql_list(_NOTIFICATION_TYPES)})",
            name="ck_notifications_type_valid",
        ),
        CheckConstraint(
            f"priority IN ({_sql_list(_PRIORITIES)})",
            name="ck_notifications_priority_valid",
        ),
        CheckConstraint(
            f"source IN ({_sql_list(_SOURCES)})",
            name="ck_notifications_source_valid",
        ),
        CheckConstraint("length(trim(title)) > 0", name="ck_notifications_title_not_blank"),
        Index("ix_notifications_user_created", "user_id", "created_at"),
        Index("ix_notifications_user_unread", "user_id", "read_at"),
        Index(
            "uq_notifications_dedupe_key",
            "dedupe_key",
            unique=True,
            postgresql_where=text("dedupe_key IS NOT NULL"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=_new_uuid)
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    type: Mapped[NotificationType] = mapped_column(
        Enum(
            NotificationType,
            native_enum=False,
            length=48,
            values_callable=lambda enum_cls: [member.value for member in enum_cls],
        ),
        nullable=False,
    )
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    body: Mapped[str | None] = mapped_column(Text, nullable=True)
    priority: Mapped[NotificationPriority] = mapped_column(
        Enum(
            NotificationPriority,
            native_enum=False,
            length=16,
            values_callable=lambda enum_cls: [member.value for member in enum_cls],
        ),
        nullable=False,
        default=NotificationPriority.NORMAL,
    )
    source: Mapped[NotificationSource] = mapped_column(
        Enum(
            NotificationSource,
            native_enum=False,
            length=16,
            values_callable=lambda enum_cls: [member.value for member in enum_cls],
        ),
        nullable=False,
    )
    object_type: Mapped[str | None] = mapped_column(String(32), nullable=True)
    object_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    # Structured, PII-free metadata: only ids and timestamps. Never names,
    # notes or other personal data. (Column is ``metadata``; the attribute is
    # ``meta`` to avoid shadowing DeclarativeBase.metadata.)
    meta: Mapped[dict | None] = mapped_column("metadata", JSON, nullable=True)
    dedupe_key: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utc_now, nullable=False)
    read_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    dismissed_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)

    user: Mapped[User] = relationship()


class Reminder(Base):
    """A personal reminder with an owner, an assignee (defaults to owner),
    an optional candidate/event link, a UTC due time and an IANA display
    timezone. ``version`` is the optimistic-concurrency counter."""

    __tablename__ = "reminders"
    __table_args__ = (
        CheckConstraint(
            f"recurrence IN ({_sql_list(_RECURRENCES)})",
            name="ck_reminders_recurrence_valid",
        ),
        CheckConstraint(
            f"status IN ({_sql_list(_REMINDER_STATUSES)})",
            name="ck_reminders_status_valid",
        ),
        CheckConstraint(
            f"importance IN ({_sql_list(_IMPORTANCES)})",
            name="ck_reminders_importance_valid",
        ),
        CheckConstraint("length(trim(title)) > 0", name="ck_reminders_title_not_blank"),
        CheckConstraint(
            "length(timezone) BETWEEN 1 AND 64",
            name="ck_reminders_tz_len",
        ),
        CheckConstraint(
            "(status = 'completed' AND completed_at IS NOT NULL) "
            "OR (status <> 'completed' AND completed_at IS NULL)",
            name="ck_reminders_completed_at_consistent",
        ),
        Index("ix_reminders_assignee_due", "assignee_user_id", "due_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=_new_uuid)
    owner_user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    assignee_user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    candidate_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("candidates.id", ondelete="SET NULL"), nullable=True
    )
    event_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("events.id", ondelete="SET NULL"), nullable=True
    )
    due_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False)
    timezone: Mapped[str] = mapped_column(String(64), nullable=False)
    importance: Mapped[ReminderImportance] = mapped_column(
        Enum(
            ReminderImportance,
            native_enum=False,
            length=16,
            values_callable=lambda enum_cls: [member.value for member in enum_cls],
        ),
        nullable=False,
        default=ReminderImportance.NORMAL,
    )
    recurrence: Mapped[ReminderRecurrence] = mapped_column(
        Enum(
            ReminderRecurrence,
            native_enum=False,
            length=16,
            values_callable=lambda enum_cls: [member.value for member in enum_cls],
        ),
        nullable=False,
        default=ReminderRecurrence.NONE,
    )
    status: Mapped[ReminderStatus] = mapped_column(
        Enum(
            ReminderStatus,
            native_enum=False,
            length=16,
            values_callable=lambda enum_cls: [member.value for member in enum_cls],
        ),
        nullable=False,
        default=ReminderStatus.ACTIVE,
    )
    completed_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    # Monotonic occurrence counter: each due occurrence (recurrence step)
    # bumps it; the delivery dedupe key embeds it, so a repeated worker pass
    # can never deliver one occurrence twice.
    occurrence: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utc_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime, default=utc_now, onupdate=utc_now, nullable=False
    )

    owner: Mapped[User] = relationship(foreign_keys=[owner_user_id])
    assignee: Mapped[User] = relationship(foreign_keys=[assignee_user_id])
    candidate: Mapped[Candidate | None] = relationship(foreign_keys=[candidate_id])

    @property
    def owner_username(self) -> str:
        return self.owner.username if self.owner is not None else ""

    @property
    def assignee_username(self) -> str:
        return self.assignee.username if self.assignee is not None else ""


class NotificationOutbox(Base):
    """The transactional delivery queue (outbox). One row per logical
    message per channel; the row itself is a state machine, the append-only
    ``notification_delivery_attempts`` table is its immutable history."""

    __tablename__ = "notification_outbox"
    __table_args__ = (
        Index(
            "ix_document_rule_outbox_discovery",
            "rule_id",
            "template_version",
            "object_type",
            "object_id",
        ),
        CheckConstraint(
            f"channel IN ({_sql_list(_CHANNELS)})",
            name="ck_notification_outbox_channel_valid",
        ),
        CheckConstraint(
            f"notification_type IN ({_sql_list(_NOTIFICATION_TYPES)})",
            name="ck_notification_outbox_type_valid",
        ),
        CheckConstraint(
            f"source IN ({_sql_list(_SOURCES)})",
            name="ck_notification_outbox_source_valid",
        ),
        CheckConstraint(
            f"status IN ({_sql_list(_STATUSES)})",
            name="ck_notification_outbox_status_valid",
        ),
        CheckConstraint("attempts >= 0", name="ck_notification_outbox_attempts_nonnegative"),
        CheckConstraint(
            "(recipient_user_id IS NOT NULL AND external_recipient IS NULL "
            "AND recipient_candidate_id IS NULL) "
            "OR (recipient_user_id IS NULL AND external_recipient IS NOT NULL "
            "AND recipient_candidate_id IS NULL) "
            "OR (recipient_user_id IS NULL AND external_recipient IS NULL "
            "AND recipient_candidate_id IS NOT NULL)",
            name="ck_notification_outbox_exactly_one_recipient",
        ),
        CheckConstraint(
            "(status = 'sending' AND lease_expires_at IS NOT NULL) "
            "OR (status <> 'sending' AND lease_expires_at IS NULL)",
            name="ck_notification_outbox_lease_consistent",
        ),
        Index(
            "ix_notification_outbox_queued_due",
            "status",
            "scheduled_at",
            postgresql_where=text("status = 'queued'"),
        ),
        Index(
            "ix_notification_outbox_sending_lease",
            "status",
            "lease_expires_at",
            postgresql_where=text("status = 'sending'"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=_new_uuid)
    recipient_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=True
    )
    # Reserved for phase 9 external recipients (Telegram/SMTP); exactly one
    # of recipient_user_id / external_recipient must be set (CHECK above).
    external_recipient: Mapped[str | None] = mapped_column(String(500), nullable=True)
    # Phase 10: the row is a one-way message to a *candidate*. The worker
    # resolves the concrete address/chat from the candidate's own consented
    # channel state at send time — never from the scheduling payload.
    recipient_candidate_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("candidates.id", ondelete="CASCADE"), nullable=True
    )
    channel: Mapped[DeliveryChannel] = mapped_column(
        Enum(
            DeliveryChannel,
            native_enum=False,
            length=16,
            values_callable=lambda enum_cls: [member.value for member in enum_cls],
        ),
        nullable=False,
    )
    # Snapshot of the planned logical notification (type + origin). The
    # in-app delivery materializes a notifications row from these.
    notification_type: Mapped[NotificationType] = mapped_column(
        Enum(
            NotificationType,
            native_enum=False,
            length=48,
            values_callable=lambda enum_cls: [member.value for member in enum_cls],
        ),
        nullable=False,
    )
    source: Mapped[NotificationSource] = mapped_column(
        Enum(
            NotificationSource,
            native_enum=False,
            length=16,
            values_callable=lambda enum_cls: [member.value for member in enum_cls],
        ),
        nullable=False,
        default=NotificationSource.SYSTEM,
    )
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    body: Mapped[str | None] = mapped_column(Text, nullable=True)
    template: Mapped[str | None] = mapped_column(String(64), nullable=True)
    template_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    initiator_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    rule_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    document_context: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    object_type: Mapped[str | None] = mapped_column(String(32), nullable=True)
    object_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    # Snapshot of the business object's optimistic version at queue time
    # (phase 10: the interview's ``version``). The worker refuses to send
    # a message whose object mutated afterwards — the rendered text may no
    # longer be true.
    object_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # Original requested send time (UTC). Quiet hours never rewrite it:
    # the effective (shifted) time lives in scheduled_at_effective for audit.
    scheduled_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    scheduled_at_effective: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    queued_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utc_now, nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    accepted_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    delivered_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    failed_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    cancelled_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    status: Mapped[DeliveryStatus] = mapped_column(
        Enum(
            DeliveryStatus,
            native_enum=False,
            length=16,
            values_callable=lambda enum_cls: [member.value for member in enum_cls],
        ),
        nullable=False,
        default=DeliveryStatus.QUEUED,
    )
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    next_attempt_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    # Safe error classification only — never messages, provider responses or
    # anything that could carry PII.
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error_class: Mapped[str | None] = mapped_column(String(32), nullable=True)
    provider_message_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    idempotency_key: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    # Snapshot of the channel consent/permission state at scheduling time.
    # Explicitly no secrets, tokens or passwords.
    consent_snapshot: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    # True when an admin confirmed a manual send inside quiet hours
    # (audited separately).
    quiet_hours_bypassed: Mapped[bool] = mapped_column(default=False, nullable=False)
    lease_expires_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utc_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime, default=utc_now, onupdate=utc_now, nullable=False
    )

    attempts_history: Mapped[list["NotificationDeliveryAttempt"]] = relationship(
        back_populates="outbox",
        cascade="all, delete-orphan",
        order_by="NotificationDeliveryAttempt.attempt_no",
    )


class NotificationDeliveryAttempt(Base):
    """Append-only attempt history. Rows are created once and never updated
    or edited: the accepted record of what was attempted."""

    __tablename__ = "notification_delivery_attempts"
    __table_args__ = (
        UniqueConstraint(
            "outbox_id", "attempt_no", name="uq_notification_delivery_attempts_outbox_attempt"
        ),
        CheckConstraint(
            f"outcome IN ({_sql_list(_OUTCOMES)})",
            name="ck_notification_delivery_attempts_outcome_valid",
        ),
        CheckConstraint("attempt_no >= 1", name="ck_notification_delivery_attempts_no_positive"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=_new_uuid)
    outbox_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("notification_outbox.id", ondelete="CASCADE"), nullable=False
    )
    attempt_no: Mapped[int] = mapped_column(Integer, nullable=False)
    started_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utc_now, nullable=False)
    finished_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utc_now, nullable=False)
    outcome: Mapped[AttemptOutcome] = mapped_column(
        Enum(
            AttemptOutcome,
            native_enum=False,
            length=16,
            values_callable=lambda enum_cls: [member.value for member in enum_cls],
        ),
        nullable=False,
    )
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error_class: Mapped[str | None] = mapped_column(String(32), nullable=True)
    provider_message_id: Mapped[str | None] = mapped_column(String(255), nullable=True)

    outbox: Mapped[NotificationOutbox] = relationship(back_populates="attempts_history")


class WorkerHeartbeat(Base):
    """Singleton row (id=1) written by the active worker process. The
    worker healthcheck and /ops/status read it; there is no PII here."""

    __tablename__ = "worker_heartbeat"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    worker_id: Mapped[str] = mapped_column(String(64), nullable=False)
    pid: Mapped[int] = mapped_column(Integer, nullable=False)
    started_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False)
    processed_total: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    failed_total: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    current_lease_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime, default=utc_now, onupdate=utc_now, nullable=False
    )


class AccessGrant(Base):
    """Explicit, audited access grants. An active grant is unique per user
    and scope (partial unique index); grants never bypass role checks."""

    __tablename__ = "access_grants"
    __table_args__ = (
        CheckConstraint(
            f"scope IN ({_sql_list(_GRANT_SCOPES)})",
            name="ck_access_grants_scope_valid",
        ),
        Index(
            "uq_access_grants_active",
            "user_id",
            "scope",
            unique=True,
            postgresql_where=text("revoked_at IS NULL"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=_new_uuid)
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    scope: Mapped[AccessGrantScope] = mapped_column(
        Enum(
            AccessGrantScope,
            native_enum=False,
            length=32,
            values_callable=lambda enum_cls: [member.value for member in enum_cls],
        ),
        nullable=False,
    )
    granted_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    granted_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utc_now, nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    revoke_reason: Mapped[str | None] = mapped_column(String(500), nullable=True)

    user: Mapped[User] = relationship(foreign_keys=[user_id])
    granted_by: Mapped[User | None] = relationship(foreign_keys=[granted_by_user_id])

    @property
    def username(self) -> str:
        return self.user.username if self.user is not None else ""

    @property
    def granted_by_username(self) -> str | None:
        return self.granted_by.username if self.granted_by is not None else None


# --- Phase 9: Telegram and email channel bindings -----------------------------


class TelegramLink(Base):
    """One user's Telegram binding. The recipient identifier is the numeric
    ``chat_id`` — never a username. ``revoked_at`` marks an explicit unlink
    (or an automatic revoke after Telegram reported «blocked»); re-linking
    clears it and replaces ``chat_id``. Delivery statistics carry safe
    error classes only (no message text, no provider payloads).

    An active ``chat_id`` is globally unique (partial unique index over
    non-revoked rows): one Telegram chat can never serve two active users,
    so a binding can never be silently taken over. Relinking to a chat
    held by another active user fails closed (the holder must unlink
    first); same-user relink is always allowed.
    """

    __tablename__ = "telegram_links"
    __table_args__ = (
        Index(
            "uq_telegram_links_chat_id_active",
            "chat_id",
            unique=True,
            postgresql_where=text("revoked_at IS NULL AND chat_id IS NOT NULL"),
            sqlite_where=text("revoked_at IS NULL AND chat_id IS NOT NULL"),
        ),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    chat_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    linked_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    revoke_reason: Mapped[str | None] = mapped_column(String(64), nullable=True)
    last_sent_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    last_error_class: Mapped[str | None] = mapped_column(String(32), nullable=True)
    last_error_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utc_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime, default=utc_now, onupdate=utc_now, nullable=False
    )

    user: Mapped[User] = relationship()

    @property
    def is_linked(self) -> bool:
        """A binding that may receive messages right now."""
        return self.chat_id is not None and self.revoked_at is None


class TelegramLinkToken(Base):
    """One-shot expiring linking token. Only the SHA-256 hash is stored —
    the raw value is shown to the owning user once at creation. A token is
    consumed exactly once (atomic compare-and-set on ``consumed_at``); a
    newer token supersedes older unconsumed ones of the same user."""

    __tablename__ = "telegram_link_tokens"
    __table_args__ = (
        Index("ix_telegram_link_tokens_user_id", "user_id"),
        Index("ix_telegram_link_tokens_expires_at", "expires_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=_new_uuid)
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utc_now, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False)
    consumed_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    consume_reason: Mapped[str | None] = mapped_column(String(16), nullable=True)

    user: Mapped[User] = relationship()


class TelegramStartEvent(Base):
    """Observed ``/start <token>`` events from the bot's getUpdates inbox.

    The confirm endpoint scans getUpdates once per attempt; every ``/start``
    payload seen is recorded here (token hash → chat_id) and the shared
    poll offset advances past the scanned updates, so concurrent confirms
    of different users never lose each other's events. Rows are pruned
    opportunistically (linking tokens live minutes, not days)."""

    __tablename__ = "telegram_start_events"

    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    chat_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    seen_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utc_now, nullable=False)


class TelegramPollState(Base):
    """Singleton row (id=1) with the getUpdates offset of the bot inbox."""

    __tablename__ = "telegram_poll_state"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    last_update_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime, default=utc_now, onupdate=utc_now, nullable=False
    )


class UserEmail(Base):
    """One user's email address for the SMTP channel. Only a *verified*
    address may receive notifications: setting an address creates a pending
    value plus a one-shot verification token delivered to that mailbox;
    confirming the token promotes it to ``email``. Delivery statistics
    carry safe error classes only."""

    __tablename__ = "user_emails"

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    email: Mapped[str | None] = mapped_column(String(254), nullable=True)
    verified_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    pending_email: Mapped[str | None] = mapped_column(String(254), nullable=True)
    verification_token_hash: Mapped[str | None] = mapped_column(
        String(64), nullable=True, unique=True
    )
    verification_expires_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    verification_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_sent_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    last_error_class: Mapped[str | None] = mapped_column(String(32), nullable=True)
    last_error_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utc_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime, default=utc_now, onupdate=utc_now, nullable=False
    )

    user: Mapped[User] = relationship()

    @property
    def is_verified(self) -> bool:
        return self.email is not None and self.verified_at is not None


# --- Phase 10: one-way candidate communications -------------------------------


_CANDIDATE_CONSENT_CHANNELS = ["email", "telegram"]


class CandidateTelegramLink(Base):
    """A candidate's Telegram binding (phase 10). The candidate connects
    voluntarily: HR shows a one-shot deep link, the candidate opens the bot
    and presses Start — only then is the numeric ``chat_id`` stored (never a
    username). Revocation is explicit (unlink) or automatic when Telegram
    reports the chat blocked/gone. Delivery statistics carry safe error
    classes only.

    An active ``chat_id`` is unique among *candidates* (partial unique
    index); the confirm flow additionally refuses chats already active on
    an internal user's binding, so one chat never serves two recipients.
    """

    __tablename__ = "candidate_telegram_links"
    __table_args__ = (
        Index(
            "uq_candidate_telegram_links_chat_id_active",
            "chat_id",
            unique=True,
            postgresql_where=text("revoked_at IS NULL AND chat_id IS NOT NULL"),
            sqlite_where=text("revoked_at IS NULL AND chat_id IS NOT NULL"),
        ),
    )

    candidate_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("candidates.id", ondelete="CASCADE"), primary_key=True
    )
    chat_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    linked_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    revoke_reason: Mapped[str | None] = mapped_column(String(64), nullable=True)
    last_sent_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    last_error_class: Mapped[str | None] = mapped_column(String(32), nullable=True)
    last_error_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utc_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime, default=utc_now, onupdate=utc_now, nullable=False
    )

    candidate: Mapped[Candidate] = relationship()

    @property
    def is_linked(self) -> bool:
        return self.chat_id is not None and self.revoked_at is None


class CandidateTelegramLinkToken(Base):
    """One-shot expiring invitation token for a candidate's Telegram link.

    Only the SHA-256 hash is stored; the raw value is rendered once into a
    bot deep link shown to the HR (who passes it to the candidate outside
    the system). A newer invitation supersedes older unconsumed ones of the
    same candidate; consumption is an atomic compare-and-set.
    """

    __tablename__ = "candidate_telegram_link_tokens"
    __table_args__ = (
        Index("ix_candidate_telegram_link_tokens_candidate_id", "candidate_id"),
        Index("ix_candidate_telegram_link_tokens_expires_at", "expires_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=_new_uuid)
    candidate_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("candidates.id", ondelete="CASCADE"), nullable=False
    )
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utc_now, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False)
    consumed_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    consume_reason: Mapped[str | None] = mapped_column(String(16), nullable=True)


class CandidateEmailConfirmToken(Base):
    """One-shot expiring token of a candidate's email double opt-in.

    HR initiates the letter; the raw token is an HMAC of the server secret
    and this row's id — it is re-derived in memory by the worker right
    before the provider call and NEVER persisted (only its SHA-256 hash
    lives here, and the queued letter body stores a placeholder instead
    of the URL). Only the candidate's own click on the link may grant the
    email consent; a newer initiation supersedes older unconsumed tokens
    of the same candidate (at most one active token is enforced by a
    partial unique index). The token is bound to the normalized address
    it was issued for.
    """

    __tablename__ = "candidate_email_confirm_tokens"
    __table_args__ = (
        Index("ix_candidate_email_confirm_tokens_candidate_id", "candidate_id"),
        Index("ix_candidate_email_confirm_tokens_expires_at", "expires_at"),
        # At most ONE unconsumed (active) token per candidate — the DB-level
        # backstop of the initiation serialization: a newer initiation must
        # supersede the previous token before inserting its own.
        Index(
            "uq_candidate_email_confirm_tokens_one_active",
            "candidate_id",
            unique=True,
            postgresql_where=text("consumed_at IS NULL"),
            sqlite_where=text("consumed_at IS NULL"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=_new_uuid)
    candidate_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("candidates.id", ondelete="CASCADE"), nullable=False
    )
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    # The normalized card address this confirmation was issued for.
    email_normalized: Mapped[str] = mapped_column(String(254), nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utc_now, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False)
    consumed_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    consume_reason: Mapped[str | None] = mapped_column(String(16), nullable=True)


class CandidateMessageRequest(Base):
    """Idempotency record of one manual candidate-message HTTP request.

    The client generates the key when the operation starts and reuses it
    on retries. The key is bound to the acting user, the candidate and a
    hash of the exact payload; a replay of the same key+payload returns
    the stored original response without queueing anything again. Same
    key with a different payload (or a different user) is refused.
    """

    __tablename__ = "candidate_message_requests"
    __table_args__ = (
        Index("ix_candidate_message_requests_user_id", "user_id"),
        Index("ix_candidate_message_requests_candidate_id", "candidate_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=_new_uuid)
    idempotency_key: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    candidate_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("candidates.id", ondelete="CASCADE"), nullable=False
    )
    message_type: Mapped[str] = mapped_column(String(48), nullable=False)
    # SHA-256 of the canonical payload (user, candidate, type, event,
    # documents, channel). The key itself is never logged or echoed.
    payload_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    response: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utc_now, nullable=False)


class CandidateChannelConsent(Base):
    """A candidate's per-channel consent record (phase 10).

    One row per (candidate, channel). ``granted`` is the recorded decision;
    it is never inferred from the absence of a row (fail-closed). For email
    the decision is pinned to the normalized address it covers — changing
    the address in the candidate card re-opens the confirmation. For
    Telegram a voluntary ``/start`` by the candidate records the consent
    with ``source='telegram_start'`` (no HR actor); an HR-recorded decision
    uses ``source='hr_recorded'`` and stores the deciding user. Revoking
    consent stops pending sends of that channel (the worker re-validates at
    send time as well).
    """

    __tablename__ = "candidate_channel_consents"
    __table_args__ = (
        CheckConstraint(
            f"channel IN ({_sql_list(_CANDIDATE_CONSENT_CHANNELS)})",
            name="ck_candidate_channel_consents_channel_valid",
        ),
        CheckConstraint(
            "granted IS NOT NULL AND granted_at IS NOT NULL",
            name="ck_candidate_channel_consents_decision_complete",
        ),
        CheckConstraint(
            "source IN ('hr_recorded', 'telegram_start', 'email_confirm')",
            name="ck_candidate_channel_consents_source_valid",
        ),
        Index("ix_candidate_channel_consents_candidate_id", "candidate_id"),
    )

    candidate_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("candidates.id", ondelete="CASCADE"), primary_key=True
    )
    channel: Mapped[str] = mapped_column(String(16), primary_key=True)
    granted: Mapped[bool] = mapped_column(nullable=False, default=False)
    granted_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utc_now, nullable=False)
    # Who recorded the decision (NULL for the candidate's own /start).
    granted_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    source: Mapped[str] = mapped_column(String(32), nullable=False)
    policy_version: Mapped[str | None] = mapped_column(String(32), nullable=True)
    # Email-only: the normalized address this consent was recorded for.
    email_normalized: Mapped[str | None] = mapped_column(String(254), nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utc_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime, default=utc_now, onupdate=utc_now, nullable=False
    )


# Phase 11: content snapshots and a closed, personal automation constructor.
class DocumentList(Base):
    __tablename__ = "document_lists"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=_new_uuid)
    stage: Mapped[str] = mapped_column(String(32), default="", nullable=False)
    version: Mapped[int] = mapped_column(default=1, nullable=False)
    author_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", name="fk_document_lists_author", ondelete="RESTRICT")
    )
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utc_now, onupdate=utc_now)
    __table_args__ = (CheckConstraint("version > 0", name="ck_document_lists_version"),)


class DocumentListVersion(Base):
    __tablename__ = "document_list_versions"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=_new_uuid)
    list_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("document_lists.id", name="fk_document_versions_list", ondelete="RESTRICT")
    )
    number: Mapped[int] = mapped_column(nullable=False)
    stage: Mapped[str] = mapped_column(String(32), nullable=False)
    state: Mapped[str] = mapped_column(String(16), default="draft", nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    description: Mapped[str] = mapped_column(String(500), default="", nullable=False)
    items: Mapped[list] = mapped_column(JSON, nullable=False)
    author_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", name="fk_document_versions_author", ondelete="RESTRICT")
    )
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utc_now)
    published_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    __table_args__ = (
        UniqueConstraint("list_id", "number", name="uq_document_versions_number"),
        CheckConstraint("number > 0", name="ck_document_versions_number"),
        CheckConstraint(
            "state IN ('draft','published','archived')", name="ck_document_versions_state"
        ),
        Index(
            "uq_document_versions_published_scope",
            "stage",
            unique=True,
            postgresql_where=text("state = 'published'"),
            sqlite_where=text("state = 'published'"),
        ),
    )


class CandidateDocumentSet(Base):
    __tablename__ = "candidate_document_sets"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=_new_uuid)
    candidate_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("candidates.id", name="fk_document_sets_candidate", ondelete="RESTRICT")
    )
    list_version_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(
            "document_list_versions.id", name="fk_document_sets_version", ondelete="RESTRICT"
        )
    )
    revision: Mapped[int] = mapped_column(nullable=False)
    snapshot: Mapped[dict] = mapped_column(JSON, nullable=False)
    author_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", name="fk_document_sets_author", ondelete="RESTRICT")
    )
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utc_now)
    __table_args__ = (
        UniqueConstraint("candidate_id", "revision", name="uq_document_sets_revision"),
    )


class CandidateDocumentItem(Base):
    __tablename__ = "candidate_document_items"
    set_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("candidate_document_sets.id", name="fk_document_items_set", ondelete="RESTRICT"),
        primary_key=True,
    )
    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    state: Mapped[str] = mapped_column(String(16), default="missing", nullable=False)
    version: Mapped[int] = mapped_column(default=1, nullable=False)
    changed_by: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", name="fk_document_items_author", ondelete="RESTRICT")
    )
    changed_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utc_now)
    __table_args__ = (
        CheckConstraint("state IN ('missing','received')", name="ck_document_items_state"),
        CheckConstraint("version > 0", name="ck_document_items_version"),
    )


class DocumentRule(Base):
    __tablename__ = "document_rules"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=_new_uuid)
    owner_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", name="fk_document_rules_owner", ondelete="RESTRICT")
    )
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    enabled: Mapped[bool] = mapped_column(default=True, nullable=False)
    version: Mapped[int] = mapped_column(default=1, nullable=False)
    params: Mapped[dict] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utc_now, onupdate=utc_now)
    __table_args__ = (
        CheckConstraint("version > 0", name="ck_document_rules_version"),
        Index("ix_document_rules_owner", "owner_id"),
    )


class DocumentRuleExecution(Base):
    __tablename__ = "document_rule_executions"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=_new_uuid)
    rule_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("document_rules.id", name="fk_rule_executions_rule", ondelete="RESTRICT")
    )
    rule_version: Mapped[int] = mapped_column(nullable=False)
    candidate_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("candidates.id", name="fk_rule_executions_candidate", ondelete="RESTRICT")
    )
    trigger_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    trigger_version: Mapped[int] = mapped_column(nullable=False)
    action: Mapped[str] = mapped_column(String(32), nullable=False)
    outcome: Mapped[str] = mapped_column(String(32), nullable=False)
    params: Mapped[dict] = mapped_column(JSON, nullable=False)
    dedupe_key: Mapped[str] = mapped_column(String(255), nullable=False)
    outbox_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("notification_outbox.id", name="fk_rule_executions_outbox", ondelete="RESTRICT")
    )
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utc_now)
    __table_args__ = (
        UniqueConstraint("dedupe_key", name="uq_rule_executions_dedupe"),
        CheckConstraint(
            "action IN ('apply_list','document_request','document_reminder')",
            name="ck_rule_executions_action",
        ),
        CheckConstraint(
            "outcome IN ('applied','queued','skipped','cancelled','failed')",
            name="ck_rule_executions_outcome",
        ),
    )


class BootstrapExchange(Base):
    """Phase 12: one-time local first-run exchange between the Windows
    installer engine and the loopback backend.

    Only the SHA-256 hash of the exchange token is persisted (never the raw
    token). A row is inserted by the startup bootstrap when
    ``PILOT_BOOTSTRAP_EXCHANGE_TOKEN`` is configured and the user table is
    empty; the claim endpoint atomically consumes it (``consumed_at``) and
    emits a short-lived one-time ticket (see ``BootstrapTicket``). Rows are
    append-only: a PostgreSQL trigger rejects every UPDATE except the single
    NULL->timestamp consumption and any DELETE."""

    __tablename__ = "bootstrap_exchanges"
    __table_args__ = (
        Index("ix_bootstrap_exchanges_token_hash", "token_hash", unique=True),
        Index("ix_bootstrap_exchanges_consumed", "consumed_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=_new_uuid)
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utc_now, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False)
    consumed_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    consumed_ticket_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    client_ip: Mapped[str | None] = mapped_column(String(64), nullable=True)


class BootstrapTicket(Base):
    """Phase 12: one-time, short-lived first-run ticket handed from the
    installer to the browser (URL fragment) after a successful exchange
    claim. Only the SHA-256 hash is persisted. ``redeem`` consumes the
    ticket and creates the single pilot owner atomically; the pending
    surname/working mode/timezone are stored with the ticket so the user
    never re-enters them."""

    __tablename__ = "bootstrap_tickets"
    __table_args__ = (
        Index("ix_bootstrap_tickets_token_hash", "ticket_hash", unique=True),
        Index("ix_bootstrap_tickets_consumed", "consumed_at"),
        CheckConstraint(
            "working_mode IN ('hr', 'manager', 'admin')",
            name="ck_bootstrap_tickets_working_mode_valid",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=_new_uuid)
    ticket_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    surname: Mapped[str] = mapped_column(String(60), nullable=False)
    working_mode: Mapped[str] = mapped_column(String(16), nullable=False)
    timezone: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utc_now, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False)
    consumed_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    redeemed_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    client_ip: Mapped[str | None] = mapped_column(String(64), nullable=True)
