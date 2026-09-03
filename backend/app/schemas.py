"""Pydantic request/response schemas.

Health schemas (phase 0) live alongside the identity schemas (phase 2).
Passwords are only ever *accepted* on input — no schema returns a password
or a password hash.
"""

import re
from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator

from app.models import (
    AuditAction,
    CandidateInteractionType,
    CandidateSource,
    CandidateStage,
    UserRole,
)
from app.utils import normalize_phone

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
