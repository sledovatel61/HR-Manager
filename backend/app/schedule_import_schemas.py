"""Pydantic contracts for the schedule import and its current source rows.

The source person, a Candidate card, a ScheduleEntry and service/invalid rows
are separate concepts. Import previews expose their association without
requiring a person to be immediately converted into a dated schedule entry.
"""

from __future__ import annotations

from datetime import date, datetime, time
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field

from app.models import CandidateStage

RowKind = Literal["candidate", "service", "skip"]
SourceRowType = Literal["person", "service", "skip", "error"]
ImportSyncStatus = Literal["added", "updated", "unchanged", "missing"]
SuggestedAction = Literal["create", "match", "service", "skip"]
DecisionAction = Literal["create", "match", "service", "skip"]
MatchReason = Literal["exact_name", "phone", "partial"]
RowResult = Literal["created", "matched", "updated", "service", "skipped", "error"]


class ImportMatchInfo(BaseModel):
    """Candidate from the card catalog suggested for a source person row."""

    candidate_id: UUID
    full_name: str = Field(max_length=200)
    stage: CandidateStage
    reason: MatchReason
    confident: bool


class ImportRowPreview(BaseModel):
    """One parsed source row, even when it cannot yet become a schedule row."""

    row_index: int
    sheet_row: int
    entry_date: date | None = None
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
    row_type: SourceRowType
    name_confidence: Literal["full", "partial"] | None = None
    suggested_action: SuggestedAction
    match: ImportMatchInfo | None = None
    match_options: list[ImportMatchInfo] = Field(default_factory=list)
    candidate_id: UUID | None = None
    source_row_key: str | None = None
    owner_user_id: UUID | None = None
    owner_name: str | None = None
    schedule_ready: bool = False
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
    """Read-only preview; no database writes occur on this endpoint."""

    file_name: str
    file_sha256: str
    sheet_title: str
    days: list[date] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    rows: list[ImportRowPreview] = Field(default_factory=list)
    summary: ImportPreviewSummary


class ImportRowDecision(BaseModel):
    """Explicit action for one parsed row in a confirmed import."""

    row_index: int = Field(ge=1)
    action: DecisionAction
    candidate_id: UUID | None = None


class ImportDecisions(BaseModel):
    """Multipart JSON payload used by the import confirmation endpoint."""

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
    sync_status: ImportSyncStatus | None = None


class WorkScheduleImportResult(BaseModel):
    """Import action results plus the source-set synchronization counts."""

    import_id: UUID
    created: int
    matched: int
    updated: int
    service_created: int
    skipped: int
    errors: int
    rows_added: int = 0
    rows_updated: int = 0
    rows_unchanged: int = 0
    rows_missing: int = 0
    active_people: int = 0
    rows: list[ImportRowResult] = Field(default_factory=list)
    report_csv: str


class ActiveScheduleImportRow(BaseModel):
    """Current source row exposed for manual assignment and status display."""

    row_key: str
    row_order: int
    sheet_row: int
    row_type: SourceRowType
    full_name: str | None = None
    entry_date: date | None = None
    time_from: time | None = None
    time_to: time | None = None
    organization: str | None = None
    department: str | None = None
    position: str | None = None
    candidate_id: UUID | None = None
    owner_user_id: UUID | None = None
    owner_name: str | None = None
    schedule_ready: bool
    sync_status: str


class ActiveScheduleImportRows(BaseModel):
    import_id: UUID | None = None
    file_name: str | None = None
    imported_at: datetime | None = None
    active_people: int = 0
    can_assign: bool = False
    rows: list[ActiveScheduleImportRow] = Field(default_factory=list)


class ScheduleImportAssignmentInput(BaseModel):
    row_keys: list[str] = Field(min_length=1, max_length=5000)
    owner_user_id: UUID | None = None


class ScheduleImportAssignmentResult(BaseModel):
    updated: int
    owner_user_id: UUID | None = None
    owner_name: str | None = None
