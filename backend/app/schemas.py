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
