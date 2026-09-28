"""Pydantic contract for versioned document templates (phase 16).

Closed contract: no arbitrary templates, no free-form HTML, no expressions and
no file uploads. Body/title/kind validation lives in :mod:`app.template_render`
so the same rules apply to save-time validation and to rendering.
"""

from datetime import datetime
from typing import Literal, Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.models import CandidateStage
from app.template_render import (
    IDEMPOTENCY_KEY_MAX_LENGTH,
    NAME_MAX_LENGTH,
    TemplateContentError,
    validate_body,
    validate_kind,
    validate_title,
)

TEMPLATE_STATES = ("draft", "active", "archived")


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", from_attributes=True)


def _clean_name(value: str) -> str:
    cleaned = " ".join((value or "").split())
    if not cleaned:
        raise TemplateContentError("Укажите название шаблона.")
    if len(cleaned) > NAME_MAX_LENGTH:
        raise TemplateContentError(f"Название шаблона длиннее {NAME_MAX_LENGTH} символов.")
    return cleaned


class TemplateVersionInput(StrictModel):
    """Content of one version: a title and the template text."""

    title: str
    body: str

    @field_validator("title")
    @classmethod
    def valid_title(cls, value: str) -> str:
        return validate_title(value)

    @field_validator("body")
    @classmethod
    def valid_body(cls, value: str) -> str:
        return validate_body(value)


class TemplateCreate(TemplateVersionInput):
    """Create a template together with its first (draft) version."""

    kind: str
    scope: CandidateStage | None = None
    name: str

    @field_validator("kind")
    @classmethod
    def valid_kind(cls, value: str) -> str:
        return validate_kind(value)

    @field_validator("name")
    @classmethod
    def valid_name(cls, value: str) -> str:
        return _clean_name(value)


class TemplateRename(StrictModel):
    """Rename a template. The display name is not part of a version's content,
    but every rename is audited and bumps the optimistic counter."""

    name: str
    expected_revision: int = Field(ge=1, strict=True)

    @field_validator("name")
    @classmethod
    def valid_name(cls, value: str) -> str:
        return _clean_name(value)


class NewTemplateVersion(TemplateVersionInput):
    expected_revision: int = Field(ge=1, strict=True)


class TemplateAction(StrictModel):
    expected_revision: int = Field(ge=1, strict=True)


class TemplateVersionOut(StrictModel):
    id: UUID
    template_id: UUID
    number: int
    state: Literal["draft", "active", "archived"]
    title: str
    body: str
    placeholders: list[str]
    author_id: UUID
    created_at: datetime
    activated_at: datetime | None


class TemplateOut(StrictModel):
    id: UUID
    kind: str
    scope: str
    name: str
    revision: int
    author_id: UUID
    created_at: datetime
    updated_at: datetime
    versions: list[TemplateVersionOut]


class TemplatesOut(StrictModel):
    items: list[TemplateOut]
    can_manage: bool


class PlaceholderOut(StrictModel):
    token: str
    description: str


class PlaceholdersOut(StrictModel):
    items: list[PlaceholderOut]


class PreviewRequest(StrictModel):
    """Render without saving. The template version must be ``active``."""

    template_version_id: UUID


class PreviewOut(StrictModel):
    template_id: UUID
    template_version_id: UUID
    template_number: int
    template_name: str
    kind: str
    title: str
    body_text: str
    body_html: str
    placeholders: list[str]


class GenerateRequest(PreviewRequest):
    """Persist an immutable snapshot of the rendered document."""

    idempotency_key: str = Field(min_length=8, max_length=IDEMPOTENCY_KEY_MAX_LENGTH)

    @field_validator("idempotency_key")
    @classmethod
    def clean_key(cls, value: str) -> str:
        cleaned = (value or "").strip()
        if len(cleaned) < 8:
            raise ValueError("Ключ идемпотентности слишком короткий.")
        return cleaned


class GenerationOut(StrictModel):
    id: UUID
    candidate_id: UUID
    template_id: UUID
    template_version_id: UUID
    template_number: int
    template_name: str
    template_title: str
    kind: str
    revision: int
    body_text: str
    content_sha256: str
    created_by: UUID
    created_at: datetime


class GenerationPreview(GenerationOut):
    """A generation plus the stored HTML artifact (used by the generate call)."""

    body_html: str


class GenerationsOut(StrictModel):
    items: list[GenerationOut]
    total: int
    limit: int
    offset: int

    @model_validator(mode="after")
    def consistent(self) -> Self:
        if self.limit < 1:
            raise ValueError("limit должен быть положительным.")
        return self
