"""Pydantic request/response schemas.

Health schemas (phase 0) live alongside the identity schemas (phase 2).
Passwords are only ever *accepted* on input — no schema returns a password
or a password hash.
"""

import re
from datetime import UTC, datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, EmailStr, Field, ValidationInfo, field_validator

from app.models import (
    AuditAction,
    CandidateInteractionType,
    CandidateSource,
    CandidateStage,
    EventHistoryKind,
    EventStatus,
    EventType,
    UserRole,
)
from app.utils import normalize_phone


def _as_utc(value: datetime) -> datetime:
    """Canonical form of incoming timestamps: timezone-aware UTC.

    ISO 8601 with an offset/Z is expected; a naive value is interpreted as
    UTC (documented contract). All stored timestamps are UTC; the UI renders
    them in the browser's local timezone.
    """
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


# --- Health (phase 0) -------------------------------------------------------


class DatabaseHealth(BaseModel):
    """Health of the database connection."""

    status: Literal["ok", "error"]
    latency_ms: int | None = None


class HealthResponse(BaseModel):
    """Body of ``GET /health``."""

    status: Literal["ok", "degraded"]
    service: str
    version: str
    environment: str
    checks: dict[str, DatabaseHealth]


# --- Auth (phase 2) ---------------------------------------------------------

USERNAME_PATTERN = re.compile(r"^[A-Za-z0-9._-]+$")


class LoginRequest(BaseModel):
    """Credentials for ``POST /auth/login``."""

    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=128)

    @field_validator("username")
    @classmethod
    def _normalize_username(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("Имя пользователя обязательно.")
        return cleaned


class UserOut(BaseModel):
    """Public representation of a user (never includes password data)."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    username: str
    full_name: str
    role: UserRole
    is_active: bool
    locked_until: datetime | None = None
    last_login_at: datetime | None = None
    created_at: datetime
    # Phase 12: the pilot owner's chosen working mode (None for non-pilot
    # accounts) and whether the owner must still set their own password.
    working_mode: str | None = None
    password_change_required: bool = False


class CurrentUserOut(BaseModel):
    """``GET /auth/me`` payload: the user plus the session CSRF token."""

    user: UserOut
    csrf_token: str


class LogoutRequest(BaseModel):
    """Logout has no required fields; the body keeps the endpoint JSON-first."""


# --- User administration (phase 2) ------------------------------------------


class UserCreate(BaseModel):
    """Admin payload for creating a user. Password is mandatory."""

    username: str = Field(min_length=3, max_length=64)
    full_name: str = Field(default="", max_length=200)
    role: UserRole
    password: str = Field(min_length=1, max_length=128)

    @field_validator("username")
    @classmethod
    def _validate_username(cls, value: str) -> str:
        cleaned = value.strip()
        if not USERNAME_PATTERN.match(cleaned):
            raise ValueError(
                "Имя пользователя может содержать только латинские буквы, цифры, "
                "точку, дефис и подчёркивание."
            )
        return cleaned

    @field_validator("full_name")
    @classmethod
    def _strip_full_name(cls, value: str) -> str:
        return value.strip()


class UserUpdate(BaseModel):
    """Admin payload for updating a user. All fields are optional."""

    full_name: str | None = Field(default=None, max_length=200)
    role: UserRole | None = None
    password: str | None = Field(default=None, max_length=128)
    is_active: bool | None = None

    @field_validator("full_name")
    @classmethod
    def _strip_full_name(cls, value: str | None) -> str | None:
        return value.strip() if value is not None else None


class UserList(BaseModel):
    """Paginated list of users."""

    items: list[UserOut]
    total: int
    limit: int
    offset: int


# --- Audit log (phase 2) ----------------------------------------------------


class AuditEventOut(BaseModel):
    """One audit trail entry."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    action: AuditAction
    user_id: UUID | None = None
    actor_user_id: UUID | None = None
    candidate_id: UUID | None = None
    username: str | None = None
    ip_address: str | None = None
    user_agent: str | None = None
    details: str | None = None
    created_at: datetime


class AuditList(BaseModel):
    """Paginated audit trail."""

    items: list[AuditEventOut]
    total: int
    limit: int
    offset: int


# --- Candidates (roadmap phase: candidates database) ------------------------

PHONE_MAX_LENGTH = 32
EMAIL_MAX_LENGTH = 254


class CandidateCreate(BaseModel):
    """Payload for ``POST /candidates``.

    ``owner_user_id`` defaults to the creator. When the creator is an HR,
    the server forces the owner to the creator (HRs work their own queue);
    managers/admins may assign any active user. ``confirm_duplicate`` must
    be explicitly true when a similar candidate already exists (see
    ``PRODUCT_SPEC.md`` §4).
    """

    full_name: str = Field(min_length=1, max_length=200)
    phone: str | None = Field(default=None, max_length=PHONE_MAX_LENGTH)
    email: EmailStr | None = Field(default=None, max_length=EMAIL_MAX_LENGTH)
    source: CandidateSource
    position: str = Field(default="", max_length=200)
    owner_user_id: UUID | None = None
    confirm_duplicate: bool = False

    @field_validator("full_name", "position")
    @classmethod
    def _strip_text(cls, value: str) -> str:
        return value.strip()

    @field_validator("full_name")
    @classmethod
    def _full_name_not_blank(cls, value: str) -> str:
        if not value:
            raise ValueError("ФИО кандидата обязательно.")
        return value

    @field_validator("phone")
    @classmethod
    def _phone_has_digits(cls, value: str | None) -> str | None:
        if value is not None and not normalize_phone(value):
            raise ValueError("Телефон должен содержать цифры.")
        return value.strip() if value is not None else None


class CandidateUpdate(BaseModel):
    """Payload for ``PATCH /candidates/{id}``. All fields are optional.

    ``owner_user_id`` is intentionally absent: ownership transfer is a
    separate audited operation implemented in the next phase
    (``POST /candidates/{id}/transfer``), per the roadmap.
    """

    full_name: str | None = Field(default=None, min_length=1, max_length=200)
    phone: str | None = Field(default=None, max_length=PHONE_MAX_LENGTH)
    email: EmailStr | None = Field(default=None, max_length=EMAIL_MAX_LENGTH)
    source: CandidateSource | None = None
    position: str | None = Field(default=None, max_length=200)
    stage: CandidateStage | None = None
    confirm_duplicate: bool = False

    @field_validator("full_name", "position")
    @classmethod
    def _strip_optional_text(cls, value: str | None) -> str | None:
        return value.strip() if value is not None else None

    @field_validator("phone")
    @classmethod
    def _phone_optional_has_digits(cls, value: str | None) -> str | None:
        if value is not None and not normalize_phone(value):
            raise ValueError("Телефон должен содержать цифры.")
        return value.strip() if value is not None else None


class CandidateOut(BaseModel):
    """Public candidate representation.

    The normalized phone/email are internal duplicate-detection values and
    are never exposed. ``owner_username`` helps list screens avoid an extra
    round-trip per row.
    """

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    full_name: str
    phone: str | None = None
    email: str | None = None
    source: CandidateSource
    position: str
    stage: CandidateStage
    owner_user_id: UUID
    owner_username: str
    created_at: datetime
    updated_at: datetime
    deleted_at: datetime | None = None
    deleted_by_user_id: UUID | None = None
    is_deleted: bool = False


class CandidateList(BaseModel):
    """Paginated candidate list."""

    items: list[CandidateOut]
    total: int
    limit: int
    offset: int


class DuplicateCandidateDetail(BaseModel):
    """409 body when a similar candidate exists (``PRODUCT_SPEC.md`` §4)."""

    message: str
    duplicates: list[CandidateOut]


class InteractionCreate(BaseModel):
    """Payload for ``POST /candidates/{id}/interactions``."""

    type: CandidateInteractionType
    comment: str = Field(min_length=1, max_length=2000)

    @field_validator("comment")
    @classmethod
    def _strip_comment(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Комментарий обязателен.")
        return value


class InteractionOut(BaseModel):
    """One recorded candidate interaction."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    candidate_id: UUID
    author_user_id: UUID
    author_username: str
    type: CandidateInteractionType
    comment: str
    created_at: datetime


class InteractionList(BaseModel):
    """Paginated interaction history."""

    items: list[InteractionOut]
    total: int
    limit: int
    offset: int


# --- HR directory (phase 4): minimal user cards for owner pickers ------------


class UserListItem(BaseModel):
    """Minimal, safe user card for owner/HR pickers (no admin fields)."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    username: str
    full_name: str
    role: UserRole
    is_active: bool


class UserListItems(BaseModel):
    """Non-paginated list of minimal user cards."""

    items: list[UserListItem]
    total: int


# --- Candidate transfer history (phase 4) ------------------------------------


class CandidateTransferCreate(BaseModel):
    """Payload for ``POST /candidates/{id}/transfer``."""

    new_owner_user_id: UUID
    reason: str = Field(min_length=1, max_length=500)

    @field_validator("reason")
    @classmethod
    def _reason_not_blank(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Причина передачи обязательна.")
        return value


class TransferOut(BaseModel):
    """One immutable ownership-transfer record."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    candidate_id: UUID
    initiator_user_id: UUID
    initiator_username: str
    from_user_id: UUID
    from_username: str
    to_user_id: UUID
    to_username: str
    reason: str
    created_at: datetime


class TransferList(BaseModel):
    """Paginated transfer history."""

    items: list[TransferOut]
    total: int
    limit: int
    offset: int


class CandidateTransferOut(BaseModel):
    """``POST /candidates/{id}/transfer`` response: the new record plus the
    refreshed candidate, so the UI can update itself without refetching."""

    transfer: TransferOut
    candidate: CandidateOut


# --- Calendar events (phase 5) ----------------------------------------------


class EventCreate(BaseModel):
    """Payload for ``POST /events``.

    ``status`` always starts as ``scheduled`` — transitions happen through
    PATCH. ``assignee_user_id`` defaults per role rules server-side.
    """

    candidate_id: UUID
    type: EventType
    title: str = Field(min_length=1, max_length=200)
    note: str | None = Field(default=None, max_length=2000)
    starts_at: datetime
    ends_at: datetime | None = None
    remind_at: datetime | None = None
    assignee_user_id: UUID | None = None

    @field_validator("title")
    @classmethod
    def _title_not_blank(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Название события обязательно.")
        return value

    @field_validator("note")
    @classmethod
    def _strip_note(cls, value: str | None) -> str | None:
        return value.strip() if value is not None and value.strip() else None

    @field_validator("starts_at", "ends_at", "remind_at")
    @classmethod
    def _times_are_utc(cls, value: datetime | None) -> datetime | None:
        return _as_utc(value) if value is not None else None

    @field_validator("ends_at")
    @classmethod
    def _ends_after_starts(cls, value: datetime | None, info: ValidationInfo) -> datetime | None:
        starts_at = info.data.get("starts_at")
        if value is not None and starts_at is not None and value <= starts_at:
            raise ValueError("Окончание должно быть позже начала.")
        return value

    @field_validator("remind_at")
    @classmethod
    def _remind_before_start(cls, value: datetime | None, info: ValidationInfo) -> datetime | None:
        starts_at = info.data.get("starts_at")
        if value is not None and starts_at is not None and value > starts_at:
            raise ValueError("Напоминание должно быть не позже начала события.")
        return value


class EventUpdate(BaseModel):
    """Payload for ``PATCH /events/{id}`` — all fields optional.

    ``expected_version`` is REQUIRED: the optimistic-concurrency guard. On
    mismatch the server answers 409 without applying anything, so a stale
    editor can never silently overwrite a newer version.
    """

    expected_version: int = Field(ge=1)
    title: str | None = Field(default=None, max_length=200)
    note: str | None = Field(default=None, max_length=2000)
    starts_at: datetime | None = None
    ends_at: datetime | None = None
    remind_at: datetime | None = None
    status: EventStatus | None = None
    assignee_user_id: UUID | None = None

    @field_validator("title")
    @classmethod
    def _title_not_blank(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        if not value:
            raise ValueError("Название события обязательно.")
        return value

    @field_validator("note")
    @classmethod
    def _strip_note(cls, value: str | None) -> str | None:
        return value.strip() if value is not None and value.strip() else None

    @field_validator("starts_at", "ends_at", "remind_at")
    @classmethod
    def _times_are_utc(cls, value: datetime | None) -> datetime | None:
        return _as_utc(value) if value is not None else None


class EventOut(BaseModel):
    """Public event representation."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    candidate_id: UUID
    candidate_full_name: str
    type: EventType
    title: str
    note: str | None = None
    status: EventStatus
    starts_at: datetime
    ends_at: datetime | None = None
    remind_at: datetime | None = None
    completed_at: datetime | None = None
    cancelled_at: datetime | None = None
    author_user_id: UUID
    author_username: str
    assignee_user_id: UUID
    assignee_username: str
    version: int
    created_at: datetime
    updated_at: datetime


class EventList(BaseModel):
    """Paginated event list."""

    items: list[EventOut]
    total: int
    limit: int
    offset: int


class EventHistoryOut(BaseModel):
    """One immutable business-history entry of an event mutation."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    event_id: UUID
    changed_by_user_id: UUID
    changed_by_username: str
    kind: EventHistoryKind
    status_old: str | None = None
    status_new: str | None = None
    starts_at_old: datetime | None = None
    starts_at_new: datetime | None = None
    ends_at_old: datetime | None = None
    ends_at_new: datetime | None = None
    remind_at_old: datetime | None = None
    remind_at_new: datetime | None = None
    assignee_user_id_old: UUID | None = None
    assignee_user_id_new: UUID | None = None
    title_changed: bool = False
    note_changed: bool = False
    created_at: datetime


class EventHistoryList(BaseModel):
    """Paginated event business history."""

    items: list[EventHistoryOut]
    total: int
    limit: int
    offset: int


# --- Terminations (analytics phase) -----------------------------------------


class CandidateTerminationCreate(BaseModel):
    """Payload for ``POST /candidates/{id}/termination``.

    ``terminated_at`` follows the same ISO 8601 contract as the rest of the
    API: an offset/Z value, or a naive value interpreted as UTC. The reason
    is required and stripped non-empty; it must not contain personal data of
    other people.
    """

    terminated_at: datetime
    reason: str = Field(min_length=1, max_length=500)

    @field_validator("reason")
    @classmethod
    def _strip_reason(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Причина увольнения обязательна.")
        return value

    @field_validator("terminated_at")
    @classmethod
    def _terminated_at_utc(cls, value: datetime) -> datetime:
        return _as_utc(value)


class CandidateTerminationOut(BaseModel):
    """Public termination record (safe: no free-form personal data)."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    candidate_id: UUID
    terminated_at: datetime
    reason: str
    created_by_user_id: UUID
    created_by_username: str

    @field_validator("terminated_at")
    @classmethod
    def _terminated_at_utc(cls, value: datetime) -> datetime:
        # SQLite DATETIME returns naive values; emit an explicit UTC offset
        # so responses never shift with the host's local timezone.
        return _as_utc(value)


class CandidateTerminationList(BaseModel):
    """All termination records of one candidate, newest first."""

    items: list[CandidateTerminationOut]
    total: int


# --- Analytics (analytics phase) ---------------------------------------------


class AnalyticsPeriodOut(BaseModel):
    """Normalized period echoed back by every analytics endpoint."""

    from_: datetime = Field(serialization_alias="from", validation_alias="from")
    to: datetime
    timezone: str


class AnalyticsFiltersOut(BaseModel):
    """Echo of the applied filters (None = not filtered)."""

    hr_id: UUID | None = None
    source: str | None = None


class AnalyticsKpisOut(BaseModel):
    """The ten KPI numbers for the period."""

    created_candidates: int
    processed_candidates: int
    calls: int
    reached: int
    interviews_scheduled: int
    interviews_done: int
    offers: int
    hired: int
    dismissed: int
    terminated: int


class AnalyticsConversionOut(BaseModel):
    """Cohort conversion between two consecutive funnel stages."""

    from_stage: str
    to_stage: str
    numerator: int
    denominator: int
    rate: float | None = None


class AnalyticsSourceRowOut(BaseModel):
    """Per-source breakdown row (fact-time source snapshot)."""

    source: str
    created: int
    hired: int
    dismissed: int
    terminated: int


class AnalyticsHrRowOut(BaseModel):
    """Per-HR breakdown row (fact-time responsible HR)."""

    hr_id: UUID
    username: str
    created: int
    processed: int
    hired: int
    dismissed: int
    terminated: int


class AnalyticsKpiResponse(BaseModel):
    """Body of ``GET /analytics/kpi``."""

    period: AnalyticsPeriodOut
    filters: AnalyticsFiltersOut
    scope: Literal["team"]
    kpis: AnalyticsKpisOut
    conversions: list[AnalyticsConversionOut]
    by_source: list[AnalyticsSourceRowOut]
    by_hr: list[AnalyticsHrRowOut]


class AnalyticsFunnelStageOut(BaseModel):
    """One funnel stage and how many unique candidates reached it."""

    stage: str
    reached: int


class AnalyticsFunnelResponse(BaseModel):
    """Body of ``GET /analytics/funnel``."""

    period: AnalyticsPeriodOut
    filters: AnalyticsFiltersOut
    stages: list[AnalyticsFunnelStageOut]
    conversions: list[AnalyticsConversionOut]


# --- Ops contour: backup / deployment / monitoring (phase 7) -----------------


class OpsBackupSignal(BaseModel):
    """Backup-related monitoring signals (never PII or dump contents)."""

    available: bool
    last_backup_at: str | None = None
    age_seconds: float | None = None
    ok: bool | None = None
    size_bytes: int | None = None
    last_check_at: str | None = None
    last_check_ok: bool | None = None
    last_drill_at: str | None = None
    last_drill_ok: bool | None = None


class OpsMigrationSignal(BaseModel):
    """Schema migration state of the connected database."""

    current_revision: str | None
    expected_revision: str | None
    ok: bool | None


class OpsStatusResponse(BaseModel):
    """Body of ``GET /ops/status`` (no PII, no connection details)."""

    status: Literal["ok", "degraded"]
    service: str
    version: str
    environment: str
    release_sha: str
    uptime_seconds: float
    time: str
    database: DatabaseHealth
    migrations: OpsMigrationSignal
    backup: OpsBackupSignal
    # Phase 8: notification queue/worker signal (counts + worker liveness
    # only — no titles, bodies or recipient data).
    notifications: dict | None = None


class OpsBackupHealthResponse(BaseModel):
    """Body of ``GET /ops/backup-health`` (HTTP 503 when unhealthy)."""

    status: Literal["ok", "degraded"]
    fresh: bool
    age_seconds: float | None
    last_backup_at: str | None
    message: str


class OpsBackupTriggerRequest(BaseModel):
    """Payload for ``POST /admin/ops/backup`` (manual backup trigger)."""

    reason: str = Field(min_length=1, max_length=200)
    request_id: str | None = Field(default=None, max_length=64)


class OpsBackupTriggerResponse(BaseModel):
    """202 response of a manual backup trigger."""

    status: Literal["accepted"] = "accepted"
    request_id: str
    message: str


class OpsDrillTriggerRequest(BaseModel):
    """Payload for ``POST /admin/ops/restore-drill``."""

    file: str | None = Field(default=None, max_length=200)


class OpsReleaseRecordRequest(BaseModel):
    """Payload for ``POST /admin/ops/releases`` (deploy audit record)."""

    sha: str = Field(min_length=7, max_length=64)
    status: Literal["deployed", "rolled_back", "failed"] = "deployed"
    details: str | None = Field(default=None, max_length=500)


# --- Phase 8: notifications, reminders, preferences, pilot setup -------------


class NotificationOut(BaseModel):
    """Public in-app notification (own rows only)."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    type: str
    title: str
    body: str | None = None
    priority: str
    source: str
    object_type: str | None = None
    object_id: UUID | None = None
    created_at: datetime
    read_at: datetime | None = None
    dismissed_at: datetime | None = None


class NotificationList(BaseModel):
    """Paginated notification list with the live unread counter."""

    items: list[NotificationOut]
    total: int
    limit: int
    offset: int
    unread_count: int


class NotificationIdsRequest(BaseModel):
    """Bulk mark-read/dismiss payload (server-enforced limit)."""

    ids: list[UUID] = Field(min_length=1, max_length=100)


class NotificationResolveOut(BaseModel):
    """Result of re-checking access to a notification's linked object."""

    allowed: bool
    object_type: str | None = None
    object_id: UUID | None = None


class DeliveryAttemptOut(BaseModel):
    """One immutable attempt record."""

    model_config = ConfigDict(from_attributes=True)

    attempt_no: int
    started_at: datetime
    finished_at: datetime
    outcome: str
    error_code: str | None = None
    error_class: str | None = None
    provider_message_id: str | None = None


class DeliveryInfoOut(BaseModel):
    """Delivery history of one outbox job (own notifications only).

    ``provider_message_id`` is exposed only in the owner's own scope (and
    never in admin queue counters). ``accepted`` means the provider took
    the message — it is not «delivered» and not «read by a human».
    """

    id: UUID
    channel: str
    status: str
    notification_type: str
    scheduled_at: datetime | None = None
    scheduled_at_effective: datetime | None = None
    queued_at: datetime
    accepted_at: datetime | None = None
    delivered_at: datetime | None = None
    failed_at: datetime | None = None
    cancelled_at: datetime | None = None
    attempts: int
    next_attempt_at: datetime | None = None
    error_code: str | None = None
    error_class: str | None = None
    provider_message_id: str | None = None
    attempts_history: list[DeliveryAttemptOut] = []


class DeliveryListOut(BaseModel):
    """Paginated own delivery history."""

    items: list[DeliveryInfoOut]
    total: int
    limit: int
    offset: int


class ReminderCreate(BaseModel):
    """Create a personal reminder."""

    title: str = Field(min_length=1, max_length=200)
    note: str | None = Field(default=None, max_length=2000)
    candidate_id: UUID | None = None
    event_id: UUID | None = None
    due_at: datetime
    timezone: str = Field(min_length=1, max_length=64)
    importance: Literal["low", "normal", "high"] = "normal"
    recurrence: Literal["none", "daily", "workdays", "weekly"] = "none"
    assignee_user_id: UUID | None = None

    @field_validator("due_at")
    @classmethod
    def _due_at_utc(cls, value: datetime) -> datetime:
        return _as_utc(value)


class ReminderUpdate(BaseModel):
    """Partial update; explicit null clears nullable fields."""

    expected_version: int = Field(ge=1)
    title: str | None = Field(default=None, min_length=1, max_length=200)
    note: str | None = Field(default=None, max_length=2000)
    candidate_id: UUID | None = None
    event_id: UUID | None = None
    due_at: datetime | None = None
    timezone: str | None = Field(default=None, min_length=1, max_length=64)
    importance: Literal["low", "normal", "high"] | None = None
    recurrence: Literal["none", "daily", "workdays", "weekly"] | None = None
    assignee_user_id: UUID | None = None

    @field_validator("due_at")
    @classmethod
    def _due_at_utc(cls, value: datetime | None) -> datetime | None:
        return _as_utc(value) if value is not None else None


class ReminderOut(BaseModel):
    """Public reminder representation."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    owner_user_id: UUID
    owner_username: str
    assignee_user_id: UUID
    assignee_username: str
    title: str
    note: str | None = None
    candidate_id: UUID | None = None
    event_id: UUID | None = None
    due_at: datetime
    timezone: str
    importance: str
    recurrence: str
    status: str
    completed_at: datetime | None = None
    occurrence: int
    version: int
    created_at: datetime
    updated_at: datetime


class ReminderList(BaseModel):
    """Paginated reminder list."""

    items: list[ReminderOut]
    total: int
    limit: int
    offset: int


class PreferenceUpdate(BaseModel):
    """Own notification preferences."""

    timezone: str = Field(min_length=1, max_length=64)
    quiet_hours_start: str = Field(pattern=r"^[0-9]{2}:[0-9]{2}$")
    quiet_hours_end: str = Field(pattern=r"^[0-9]{2}:[0-9]{2}$")
    workdays: list[int] = Field(min_length=1, max_length=7)
    enabled_types: list[str] = Field(min_length=0, max_length=32)
    enabled_channels: list[str] = Field(min_length=0, max_length=8)


class PreferenceOut(BaseModel):
    """Current preferences (system defaults when never initialized)."""

    model_config = ConfigDict(from_attributes=True)

    timezone: str
    quiet_hours_start: str
    quiet_hours_end: str
    workdays: list[int]
    enabled_types: list[str]
    enabled_channels: list[str]
    initialized: bool


class TimezonesOut(BaseModel):
    """IANA timezones offered by the settings screen (curated list)."""

    timezones: list[str]


class QueueDiagnosticsOut(BaseModel):
    """Admin queue diagnostics: counts and statuses only — no PII."""

    counts: dict[str, int]
    oldest_queued_at: datetime | None = None
    stuck_sending: int
    worker: dict


class OutboxActionOut(BaseModel):
    """Result of an admin retry/cancel on one outbox row."""

    id: UUID
    status: str


class OutboxRetryRequest(BaseModel):
    """Admin retry; ``bypass_quiet_hours`` requires the urgent override."""

    bypass_quiet_hours: bool = False


class PilotCreateRequest(BaseModel):
    """Create the pilot account (setup wizard; admin only)."""

    username: str = Field(min_length=3, max_length=64)
    password: str = Field(min_length=12, max_length=128)
    full_name: str = Field(default="", max_length=200)


class PilotCreateOut(BaseModel):
    """Pilot creation result — no password is ever returned."""

    user_id: UUID
    username: str


class AccessGrantOut(BaseModel):
    """An explicit access grant (pilot designation)."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    user_id: UUID
    username: str
    scope: str
    granted_by_username: str | None = None
    granted_at: datetime
    revoked_at: datetime | None = None
    revoke_reason: str | None = None


class AccessGrantList(BaseModel):
    """All grants (history, including revoked)."""

    items: list[AccessGrantOut]


class AccessGrantRequest(BaseModel):
    """Grant or revoke explicitly confirmed access."""

    scope: Literal["pilot_full_access", "document_lists_manage", "candidate_documents_all"] = (
        "pilot_full_access"
    )

    user_id: UUID
    revoke: bool = False
    revoke_reason: str | None = Field(default=None, max_length=500)


class SetupStateOut(BaseModel):
    """Honest setup status: what works, what is not configured."""

    pilot_exists: bool
    pilot_grant_active: bool
    preferences_initialized: bool
    worker_alive: bool
    channels: dict[str, str]


# --- Phase 12: local pilot first-run exchange ---------------------------------


class FirstRunStatusOut(BaseModel):
    """Unauthenticated, PII-free first-run signal for the installer/SPA.

    ``pending`` is true only when no user exists yet AND a first-run token is
    armed. ``needs_password`` is true when the single pilot owner already
    exists but has not set their own password yet (a lost session before the
    UI password step) — the installer re-arms a fresh token and reopens the
    first-run page to continue setup. No surname, token, working mode or other
    personal data is exposed here.
    """

    pending: bool
    needs_password: bool = False


class FirstRunClaimRequest(BaseModel):
    """One-shot first-run claim. The surname and working mode are read
    server-side from the installer-injected environment (never trusted from
    the client and never carried in the URL). The exchange token is the only
    client-supplied value."""

    exchange_token: str = Field(min_length=16, max_length=256)


class PilotPasswordSetRequest(BaseModel):
    """Owner sets their own password after the first-run exchange."""

    password: str = Field(min_length=1, max_length=128)


# --- Phase 9: Telegram/SMTP integrations, bindings, consent -------------------


ChannelState = Literal["not_configured", "pending", "works", "temporarily_unavailable", "revoked"]


class TelegramStatusOut(BaseModel):
    """Own Telegram binding state (no secrets, masked identifiers)."""

    state: ChannelState
    configured: bool
    linked: bool
    masked_chat_id: str | None = None
    pending_confirmation: bool = False
    opt_in: bool = False
    consent_at: datetime | None = None
    linked_at: datetime | None = None


class EmailStatusOut(BaseModel):
    """Own email channel state (no secrets, masked addresses)."""

    state: ChannelState
    configured: bool
    verified: bool
    address_masked: str | None = None
    pending_email_masked: str | None = None
    pending_confirmation: bool = False
    opt_in: bool = False
    consent_at: datetime | None = None


class IntegrationStatusOut(BaseModel):
    """Combined own-channels status for the integrations screen."""

    telegram: TelegramStatusOut
    email: EmailStatusOut


class TelegramLinkOut(BaseModel):
    """One-shot linking token rendered as a bot deep link (shown once)."""

    deep_link: str
    expires_at: datetime


class TelegramConfirmOut(BaseModel):
    """Result of a linking-confirm attempt."""

    linked: bool
    state: ChannelState


class ConsentUpdate(BaseModel):
    """Explicit opt-in/opt-out for one external channel.

    Contract (fail-closed): BOTH flags are required and MUST agree.
    Activation needs ``opt_in=true`` together with an explicit
    ``consent_granted=true``; anything else is a 422 with no state
    change. Deactivation (``false``/``false``) is always honored and
    stops new sends; re-enabling needs a fresh explicit grant.
    """

    opt_in: bool
    consent_granted: bool


class ConsentOut(BaseModel):
    """Stored consent (timestamp, source and terms version are audited)."""

    channel: str
    opt_in: bool
    consent_granted: bool
    consent_at: datetime | None = None
    policy_version: str | None = None


class EmailSetRequest(BaseModel):
    """Set (or change) the own notification email address."""

    email: EmailStr = Field(max_length=254)


class EmailSetOut(BaseModel):
    """Pending verification state (the token travels only via email)."""

    pending_email_masked: str
    expires_at: datetime
    verification_queued: bool


class EmailConfirmRequest(BaseModel):
    """Confirm the pending address with the token from the letter."""

    token: str = Field(min_length=8, max_length=128)


class EmailConfirmOut(BaseModel):
    """Result of an address-confirm attempt."""

    verified: bool
    address_masked: str


class AdminTelegramInfo(BaseModel):
    """Global Telegram configuration state (admin only, no secrets)."""

    enabled: bool
    configured: bool
    bot_username: str | None = None


class AdminSmtpInfo(BaseModel):
    """Global SMTP configuration state (admin only, no secrets)."""

    enabled: bool
    configured: bool
    host: str | None = None
    port: int | None = None
    encryption: str | None = None
    from_address: str | None = None


class AdminChannelsOut(BaseModel):
    """Global channels overview for administrators (no secrets)."""

    telegram: AdminTelegramInfo
    smtp: AdminSmtpInfo


class ChannelCheckOut(BaseModel):
    """Result of a live configuration check (no message is sent)."""

    ok: bool
    detail: str
    error_class: str | None = None
    bot_username: str | None = None


class ChannelTestOut(BaseModel):
    """An explicitly queued test message (delivery stays async/honest)."""

    outbox_id: UUID
    status: str


# --- Phase 10: one-way candidate communications ---------------------------------


class CandidateConsentUpdate(BaseModel):
    """Record or revoke a candidate's consent for one channel.

    The decision is recorded by the HR (source ``hr_recorded``); for
    Telegram the candidate's own voluntary /start records it with source
    ``telegram_start``. Revocation immediately stops pending sends of the
    channel and requires a fresh explicit decision to re-enable.
    """

    granted: bool


class CandidateConsentOut(BaseModel):
    """The stored per-channel consent decision (no PII beyond the mask)."""

    channel: str
    granted: bool
    granted_at: datetime | None = None
    source: str | None = None
    policy_version: str | None = None


class CandidateChannelStateOut(BaseModel):
    """State of one candidate channel: not_connected | pending | allowed |
    forbidden | temporarily_unavailable."""

    channel: str
    state: str
    configured: bool
    # Masked target (e.g. «i***@example.com», «••••1234») — never the exact
    # address/chat id, which lives only in the candidate card / binding.
    target_masked: str | None = None
    has_target: bool = False
    invite_active: bool = False
    consent: CandidateConsentOut | None = None


class CandidateChannelsOut(BaseModel):
    """Both channels of one candidate plus the send-eligible list."""

    email: CandidateChannelStateOut
    telegram: CandidateChannelStateOut
    allowed_channels: list[str]


class CandidateTelegramInviteOut(BaseModel):
    """A one-shot invitation deep link (shown once, hash-only storage)."""

    deep_link: str
    expires_at: datetime


class CandidateTelegramConfirmOut(BaseModel):
    """Result of an invitation confirmation attempt."""

    linked: bool
    state: str


class CandidateMessageSendRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    """Manual one-way message request (server renders the exact text).

    ``event_id`` is required for the interview types (the interview the
    message is about — its data is read server-side only); ``documents``
    is required for the document types. The channel (when given) must be
    allowed for the candidate. The client generates ``idempotency_key``
    when the operation starts and reuses it on retries: the same key with
    the same payload replays the original response, a different payload
    is refused. No recipient, text or chat id is ever accepted.
    """

    message_type: str
    event_id: UUID | None = None
    documents: list[str] | None = None
    document_set_id: UUID | None = None
    channel: str | None = None
    idempotency_key: str = Field(min_length=8, max_length=255)


class CandidateMessagePreviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    """Preview payload: the closed vocabulary only, nothing is queued.

    Deliberately NOT a subclass of the send request — the idempotency key
    belongs to mutating operations only.
    """

    message_type: str
    event_id: UUID | None = None
    documents: list[str] | None = None
    document_set_id: UUID | None = None
    channel: str | None = None


class CandidateMessagePreviewOut(BaseModel):
    """Rendered text plus the channels the message would go to."""

    title: str
    body: str
    channels: list[str]


class CandidateMessageOut(BaseModel):
    document_context: dict | None = None
    rule_id: UUID | None = None
    template_version: int | None = None

    """One immutable candidate message (history entry).

    The exact text is part of the immutable history and is visible only to
    users with candidate-message access. ``accepted`` means the provider
    took the message — never «delivered», never «read».
    """

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    message_type: str
    channel: str
    status: str
    source: str
    title: str
    body: str | None = None
    event_id: UUID | None = None
    initiator_user_id: UUID | None = None
    initiator_username: str | None = None
    scheduled_at: datetime | None = None
    scheduled_at_effective: datetime | None = None
    queued_at: datetime
    accepted_at: datetime | None = None
    delivered_at: datetime | None = None
    failed_at: datetime | None = None
    cancelled_at: datetime | None = None
    attempts: int
    next_attempt_at: datetime | None = None
    error_code: str | None = None
    error_class: str | None = None
    provider_message_id: str | None = None


class CandidateMessageListOut(BaseModel):
    """Paginated candidate message history (newest first)."""

    items: list[CandidateMessageOut]
    total: int
    limit: int
    offset: int


class CandidateMessageSendOut(BaseModel):
    """Queued rows of one manual send (one per channel)."""

    messages: list[CandidateMessageOut]
    channels: list[str]


class CandidateEmailConfirmationOut(BaseModel):
    """Result of initiating the candidate's email double opt-in letter."""

    queued: bool
    email_masked: str
    expires_at: datetime


class CandidateMessageCancelOut(BaseModel):
    """Result of cancelling a pending message."""

    id: UUID
    status: str
