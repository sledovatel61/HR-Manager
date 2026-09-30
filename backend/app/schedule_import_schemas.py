"""Pydantic-схемы импорта графика выхода из Excel.

Два шага: ``превью`` (разбор и сопоставление без записи) и ``подтверждение``
(явные действия по строкам, атомарная запись). Личные данные в превью
маскируются (телефон), отчёт об ошибках отдаётся в ответе — файл на сервере
не сохраняется ни на одном шаге.
"""

from __future__ import annotations

from datetime import date, time
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field

from app.models import CandidateStage

RowKind = Literal["candidate", "service", "skip"]
SuggestedAction = Literal["create", "match", "service", "skip"]
DecisionAction = Literal["create", "match", "service", "skip"]
MatchReason = Literal["exact_name", "phone", "partial"]
RowResult = Literal["created", "matched", "updated", "service", "skipped", "error"]


class ImportMatchInfo(BaseModel):
    """Кандидат из картотеки, найденный для строки импорта."""

    candidate_id: UUID
    full_name: str = Field(max_length=200)
    stage: CandidateStage
    reason: MatchReason
    # phone/точное совпадение с единственным кандидатом — сопоставление
    # надёжное; только ФИО — требует подтверждения пользователем.
    confident: bool


class ImportRowPreview(BaseModel):
    """Одна строка в превью: что распознано и что предлагается сделать."""

    row_index: int
    sheet_row: int
    entry_date: date
    full_name: str | None = None
    time_display: str = ""
    time_from: time | None = None
    time_to: time | None = None
    organization: str | None = None
    department: str | None = None
    position: str | None = None
    shift: str | None = None
    comment: str | None = None
    phone_masked: str | None = None
    kind: RowKind
    name_confidence: Literal["full", "partial"] | None = None
    suggested_action: SuggestedAction
    match: ImportMatchInfo | None = None
    match_options: list[ImportMatchInfo] = Field(default_factory=list)
    already_imported: bool = False
    warnings: list[str] = Field(default_factory=list)
    parse_error: str | None = None


class ImportPreviewSummary(BaseModel):
    rows_total: int
    days_total: int
    candidate_rows: int
    service_rows: int
    skipped_rows: int
    error_rows: int
    new_count: int
    match_count: int
    ambiguous_count: int


class WorkScheduleImportPreview(BaseModel):
    """Ответ «проверить без сохранения»."""

    file_name: str
    file_sha256: str
    sheet_title: str
    days: list[date] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    rows: list[ImportRowPreview] = Field(default_factory=list)
    summary: ImportPreviewSummary


class ImportRowDecision(BaseModel):
    """Явное действие пользователя по одной строке превью."""

    row_index: int = Field(ge=1)
    action: DecisionAction
    candidate_id: UUID | None = None


class ImportDecisions(BaseModel):
    """Payload подтверждения (JSON-поле ``decisions`` в multipart-запросе)."""

    decisions: list[ImportRowDecision] = Field(default_factory=list)


class ImportRowResult(BaseModel):
    row_index: int
    sheet_row: int
    entry_date: date | None = None
    time_display: str = ""
    action_label: str = ""
    result: RowResult
    candidate_id: UUID | None = None
    entry_id: UUID | None = None
    reason: str | None = None


class WorkScheduleImportResult(BaseModel):
    """Итог подтверждённого импорта + отчёт (санитизированный CSV)."""

    import_id: UUID
    created: int
    matched: int
    updated: int
    service_created: int
    skipped: int
    errors: int
    rows: list[ImportRowResult] = Field(default_factory=list)
    report_csv: str
