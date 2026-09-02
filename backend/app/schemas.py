"""Pydantic request/response schemas.

Health schemas (phase 0) live alongside the identity schemas (phase 2).
Passwords are only ever *accepted* on input — no schema returns a password
or a password hash.
"""

import re
from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.models import AuditAction, UserRole

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
