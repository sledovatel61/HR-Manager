"""Closed Phase 11 contract. No arbitrary templates, expressions or recipients."""

import re
from datetime import datetime
from typing import Literal, Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.models import CandidateStage


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", from_attributes=True)


class DocumentItem(StrictModel):
    key: str = Field(pattern=r"^[a-z][a-z0-9_-]{0,63}$")
    name: str = Field(min_length=1, max_length=120)
    explanation: str = Field(default="", max_length=300)
    required: bool = True

    @model_validator(mode="after")
    def nonempty_name(self) -> Self:
        if not self.name.strip():
            raise ValueError("Название документа обязательно.")
        return self

    @field_validator("name", "explanation")
    @classmethod
    def safe_text(cls, value: str) -> str:
        if any(ord(c) < 32 for c in value) or any(c in value for c in "<>{}"):
            raise ValueError("Только безопасный однострочный текст.")
        value = value.strip()
        if value and not re.search("[А-Яа-яЁё]", value):
            raise ValueError("Используйте русское название/пояснение.")
        return value


class VersionInput(StrictModel):
    name: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=500)
    items: list[DocumentItem] = Field(min_length=1, max_length=20)

    @model_validator(mode="after")
    def unique_keys(self) -> Self:
        if len({item.key for item in self.items}) != len(self.items):
            raise ValueError("Ключи документов должны быть уникальны.")
        if not self.name.strip():
            raise ValueError("Укажите название.")
        return self


class ListCreate(VersionInput):
    stage: CandidateStage | None = None


class NewVersion(VersionInput):
    expected_version: int = Field(ge=1, strict=True)


class ExpectedVersion(StrictModel):
    expected_version: int = Field(ge=1, strict=True)


class VersionOut(StrictModel):
    id: UUID
    list_id: UUID
    number: int
    stage: str
    state: Literal["draft", "published", "archived"]
    name: str
    description: str
    items: list[DocumentItem]
    author_id: UUID
    created_at: datetime
    published_at: datetime | None


class ListOut(StrictModel):
    id: UUID
    stage: str
    version: int
    versions: list[VersionOut]


class ListsOut(StrictModel):
    items: list[ListOut]
    can_manage: bool


class ApplyList(StrictModel):
    version_id: UUID
    expected_revision: int = Field(ge=0, strict=True)


class ItemUpdate(ExpectedVersion):
    set_id: UUID
    state: Literal["missing", "received"]


class CandidateItemOut(DocumentItem):
    state: Literal["missing", "received"]
    version: int
    changed_by: UUID
    changed_at: datetime


class CandidateDocumentsOut(StrictModel):
    revision: int
    set_id: UUID | None = None
    exact_version: VersionOut | None = None
    items: list[CandidateItemOut] = []
    missing_required: list[str] = []


class RuleParams(StrictModel):
    trigger: Literal["stage_transition", "scheduled_reminder"]
    action: Literal["apply_list", "document_request", "document_reminder"]
    stage: CandidateStage
    list_id: UUID
    list_version_id: UUID | None = None
    missing_required: bool = True
    channel: Literal["email", "telegram"] | None = None
    days: int | None = Field(default=None, ge=1, le=30, strict=True)

    @model_validator(mode="after")
    def valid_combination(self) -> Self:
        if self.action == "apply_list":
            if (
                self.trigger != "stage_transition"
                or self.channel
                or self.days
                or self.list_version_id
            ):
                raise ValueError(
                    "Применение актуального списка доступно только при переходе этапа."
                )
        else:
            if not self.channel or not self.missing_required:
                raise ValueError("Сообщения требуют канал и недостающие обязательные документы.")
            if (self.action == "document_reminder") != (self.days is not None):
                raise ValueError("Срок 1–30 дней задаётся только для напоминания.")
        if self.trigger == "scheduled_reminder" and self.action != "document_reminder":
            raise ValueError("Триггер срока поддерживает только напоминание.")
        return self


class RuleInput(StrictModel):
    name: str = Field(min_length=1, max_length=120)
    enabled: bool = True
    params: RuleParams

    @field_validator("name")
    @classmethod
    def not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Укажите название правила.")
        return value.strip()


class RuleUpdate(RuleInput):
    expected_version: int = Field(ge=1, strict=True)


class RuleOut(RuleInput):
    id: UUID
    version: int
    created_at: datetime
    updated_at: datetime


class ExecutionOut(StrictModel):
    id: UUID
    rule_version: int
    trigger_id: UUID
    trigger_version: int
    candidate_id: UUID
    action: str
    outcome: str
    created_at: datetime
    outbox_id: UUID
