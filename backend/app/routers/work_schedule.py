"""«График выхода на работу» — API (Phase 18).

Эндпоинты:

* ``GET  /work-schedule`` — дневной график: сервер фильтрует, сортирует по
  дате и времени (строки без времени — в конце дня), нумерует строки внутри
  дня и возвращает плоский список (интерфейс группирует его по дням);
* ``GET  /work-schedule/suggestions`` — подсказки автодополнения из уже
  введённых значений (организация, отдел, смена) без отдельного справочника;
* ``GET  /work-schedule/export.xlsx`` — тот же набор строк в .xlsx;
* ``POST/PATCH/DELETE /work-schedule/entries`` — служебные строки без
  кандидата («Увольнение 13:00–14:00», «перевод», «медосмотр»).

Права переиспользуют существующую модель (``app.work_schedule``): HR видит
своих кандидатов, руководитель/администратор и держатели scope «все
кандидаты»/``pilot_full_access`` — всех. Все изменения даты, времени и
служебных строк пишутся в аудит (было → стало, кто, когда).
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
from datetime import UTC, date, datetime, time
from typing import Annotated, cast
from uuid import UUID

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    Query,
    Request,
    Response,
    UploadFile,
    status,
)
from pydantic import ValidationError
from sqlalchemy import distinct, select, text
from sqlalchemy.orm import Session

from app.analytics_ledger import record_fact
from app.assignees import resolve_assignee
from app.audit import record_event
from app.db import get_db
from app.deps import get_current_user
from app.models import (
    CANDIDATE_STAGE_POSITION,
    AnalyticsFactType,
    AuditAction,
    Candidate,
    CandidateSource,
    CandidateStage,
    ScheduleEntry,
    ScheduleImport,
    ScheduleImportRow,
    User,
)
from app.schedule_import import (
    MAX_IMPORT_BYTES,
    LookupCandidate,
    ParsedScheduleRow,
    ParsedScheduleSheet,
    ScheduleImportFormatError,
    build_row_matches,
    make_row_key,
    mask_phone_display,
    parse_schedule_workbook,
    row_identity_key,
    sanitize_upload_filename,
)
from app.schedule_import_schemas import (
    ActiveScheduleImportRow,
    ActiveScheduleImportRows,
    DecisionAction,
    ImportDecisions,
    ImportMatchInfo,
    ImportPreviewSummary,
    ImportRowDecision,
    ImportRowPreview,
    ImportRowResult,
    ImportSyncStatus,
    RowResult,
    ScheduleImportAssignmentInput,
    ScheduleImportAssignmentResult,
    SourceRowType,
    SuggestedAction,
    WorkScheduleImportPreview,
    WorkScheduleImportResult,
)
from app.schemas import (
    ScheduleEntryCreate,
    ScheduleEntryOut,
    ScheduleEntryUpdate,
    WorkScheduleList,
    WorkScheduleSuggestions,
)
from app.utils import client_ip, user_agent, utc_now
from app.work_schedule import (
    build_import_source_xlsx,
    build_rows,
    build_xlsx,
    can_manage_entry,
    day_label_short,
    entry_audit_details,
    entry_out,
    hr_sees_all_candidates,
    visible_candidate_conditions,
)

router = APIRouter(prefix="/work-schedule", tags=["work-schedule"])


def _split_values(values: list[str | None]) -> list[str]:
    """Уникальные непустые значения в порядке первого появления."""

    seen: dict[str, None] = {}
    for value in values:
        if value:
            cleaned = value.strip()
            if cleaned:
                seen.setdefault(cleaned, None)
    return sorted(seen, key=str.casefold)


def _entry_or_404(db: Session, entry_id: str) -> ScheduleEntry:
    """Служебная строка по id. Правка доступна всем, кто работает с графиком
    (роли HR/руководитель/администратор — проверка сессии в зависимости выше)."""

    try:
        parsed = UUID(entry_id)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Служебная строка не найдена."
        ) from None
    entry = db.get(ScheduleEntry, parsed)
    if entry is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Служебная строка не найдена."
        )
    return entry


def _check_owner_filter(db: Session, user: User, owner_id: UUID | None) -> None:
    """Фильтр по ответственному — только для тех, кто видит всех кандидатов.

    HR без гранта раньше получал свои строки и молча не понимал, почему
    фильтр «не работает»: теперь чужой ``owner`` — явная ошибка (свой id
    совпадает с обычной видимостью и допустим).
    """

    if owner_id is None or owner_id == user.id:
        return
    if not hr_sees_all_candidates(db, user):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Фильтр по ответственному доступен только тем, кто видит всех кандидатов.",
        )


def _check_entry_manageable(db: Session, user: User, entry: ScheduleEntry) -> None:
    """Правка/удаление служебной строки: автор, руководитель/администратор или
    HR с грантом «все кандидаты» / ``pilot_full_access``."""

    if can_manage_entry(db, user, entry):
        return
    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail=(
            "Служебную строку может изменить только её автор или ответственный за всех кандидатов."
        ),
    )


def _audit(
    db: Session,
    request: Request,
    action: AuditAction,
    *,
    actor: User,
    details: str,
) -> None:
    record_event(
        db,
        action,
        actor=actor,
        ip_address=client_ip(request),
        user_agent=user_agent(request.headers),
        details=details,
        commit=False,
    )


@router.get("", response_model=WorkScheduleList, summary="График выхода (фильтры и сортировка)")
def list_work_schedule(
    date_from: Annotated[date | None, Query(alias="from")] = None,
    date_to: Annotated[date | None, Query(alias="to")] = None,
    organization: str | None = Query(default=None, max_length=120),
    department: str | None = Query(default=None, max_length=120),
    position: str | None = Query(default=None, max_length=200),
    shift: str | None = Query(default=None, max_length=32),
    owner: UUID | None = Query(default=None),
    stage: CandidateStage | None = Query(default=None),
    q: str | None = Query(default=None, max_length=200),
    include_rejected: bool = Query(default=False),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> WorkScheduleList:
    """Строки графика за период с учётом прав и фильтров."""

    if date_from is not None and date_to is not None and date_to < date_from:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Конец периода не может быть раньше начала.",
        )
    _check_owner_filter(db, user, owner)
    # Явный фильтр по этапу «Отказ» уже показывает отказавшихся: эффективный
    # флаг уходит и в выборку, и в ответ (аудит/подзаголовок Excel — ниже).
    effective_include_rejected = include_rejected or stage == CandidateStage.REJECTED
    rows = build_rows(
        db,
        user=user,
        date_from=date_from,
        date_to=date_to,
        organization=organization,
        department=department,
        position=position,
        shift=shift,
        owner_id=owner,
        stage=stage,
        query=q,
        include_rejected=effective_include_rejected,
    )
    return WorkScheduleList(
        items=rows,
        total=len(rows),
        days=len({row.entry_date for row in rows}),
        period_from=date_from,
        period_to=date_to,
        include_rejected=effective_include_rejected,
    )


@router.get(
    "/suggestions",
    response_model=WorkScheduleSuggestions,
    summary="Подсказки автодополнения (организация/отдел/смена)",
)
def work_schedule_suggestions(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> WorkScheduleSuggestions:
    """Уже введённые значения в пределах доступных пользователю кандидатов."""

    from app.work_schedule import visible_candidate_conditions

    candidate_conditions = [
        Candidate.deleted_at.is_(None),
        *visible_candidate_conditions(db, user),
    ]
    organizations = _split_values(
        list(
            db.scalars(select(distinct(Candidate.start_organization)).where(*candidate_conditions))
        )
        + list(db.scalars(select(distinct(ScheduleEntry.organization))))
    )
    departments = _split_values(
        list(db.scalars(select(distinct(Candidate.start_department)).where(*candidate_conditions)))
        + list(db.scalars(select(distinct(ScheduleEntry.department))))
    )
    shifts = _split_values(list(db.scalars(select(distinct(Candidate.shift)))))
    return WorkScheduleSuggestions(
        organizations=organizations,
        departments=departments,
        shifts=shifts,
    )


@router.get("/export.xlsx", summary="Выгрузка графика в Excel (.xlsx)")
def export_work_schedule(
    request: Request,
    date_from: Annotated[date | None, Query(alias="from")] = None,
    date_to: Annotated[date | None, Query(alias="to")] = None,
    organization: str | None = Query(default=None, max_length=120),
    department: str | None = Query(default=None, max_length=120),
    position: str | None = Query(default=None, max_length=200),
    shift: str | None = Query(default=None, max_length=32),
    owner: UUID | None = Query(default=None),
    stage: CandidateStage | None = Query(default=None),
    q: str | None = Query(default=None, max_length=200),
    include_rejected: bool = Query(default=False),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> Response:
    """Тот же отфильтрованный набор строк, что и на экране, — в файл .xlsx."""

    if date_from is not None and date_to is not None and date_to < date_from:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Конец периода не может быть раньше начала.",
        )
    _check_owner_filter(db, user, owner)
    effective_include_rejected = include_rejected or stage == CandidateStage.REJECTED
    rows = build_rows(
        db,
        user=user,
        date_from=date_from,
        date_to=date_to,
        organization=organization,
        department=department,
        position=position,
        shift=shift,
        owner_id=owner,
        stage=stage,
        query=q,
        include_rejected=effective_include_rejected,
    )
    generated_at = datetime.now(UTC)
    filters = {
        "организация": organization or "",
        "отдел": department or "",
        "должность": position or "",
        "смена": shift or "",
        "этап": stage.value if stage is not None else "",
        "ответственный": str(owner) if owner is not None else "",
        "поиск": q or "",
    }
    payload = build_xlsx(
        rows,
        period_from=date_from,
        period_to=date_to,
        filters=filters,
        include_rejected=effective_include_rejected,
        generated_at_label=(
            f"{day_label_short(generated_at.date())} {generated_at.strftime('%H:%M')} UTC"
        ),
    )
    _audit(
        db,
        request,
        AuditAction.WORK_SCHEDULE_EXPORTED,
        actor=user,
        details=(
            f"rows={len(rows)} period_from={date_from or '-'} period_to={date_to or '-'} "
            f"include_rejected={effective_include_rejected}"
        ),
    )
    db.commit()
    filename = "work-schedule_{}_{}.xlsx".format(
        date_from.isoformat() if date_from else "all",
        date_to.isoformat() if date_to else "all",
    )
    return Response(
        content=payload,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.post(
    "/entries",
    response_model=ScheduleEntryOut,
    status_code=status.HTTP_201_CREATED,
    summary="Создать служебную строку графика",
)
def create_schedule_entry(
    payload: ScheduleEntryCreate,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> ScheduleEntryOut:
    entry = ScheduleEntry(
        entry_date=payload.entry_date,
        time_from=payload.time_from,
        time_to=payload.time_to,
        title=payload.title,
        organization=payload.organization,
        department=payload.department,
        comment=payload.comment,
        author_user_id=user.id,
        created_at=utc_now(),
        updated_at=utc_now(),
    )
    db.add(entry)
    db.flush()
    _audit(
        db,
        request,
        AuditAction.WORK_SCHEDULE_ENTRY_CREATED,
        actor=user,
        details=entry_audit_details(entry),
    )
    db.commit()
    db.refresh(entry)
    return entry_out(entry)


@router.patch(
    "/entries/{entry_id}",
    response_model=ScheduleEntryOut,
    summary="Изменить служебную строку графика",
)
def update_schedule_entry(
    entry_id: str,
    payload: ScheduleEntryUpdate,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> ScheduleEntryOut:
    entry = _entry_or_404(db, entry_id)
    _check_entry_manageable(db, user, entry)
    provided = payload.model_fields_set
    changes: list[str] = []

    def note(field: str, old: object, new: object) -> None:
        before = old if old is not None else "-"
        after = new if new is not None else "-"
        changes.append(f"{field}: {before} -> {after}")

    if (
        "entry_date" in provided
        and payload.entry_date is not None
        and payload.entry_date != entry.entry_date
    ):
        note("entry_date", entry.entry_date.isoformat(), payload.entry_date.isoformat())
        entry.entry_date = payload.entry_date
    for name in ("time_from", "time_to"):
        if name in provided:
            new_value = getattr(payload, name)
            old_value = getattr(entry, name)
            if new_value != old_value:
                note(
                    name,
                    old_value.strftime("%H:%M") if old_value is not None else None,
                    new_value.strftime("%H:%M") if new_value is not None else None,
                )
                setattr(entry, name, new_value)
    if "title" in provided and payload.title is not None and payload.title != entry.title:
        note("title", entry.title, payload.title)
        entry.title = payload.title
    for name in ("organization", "department", "comment"):
        if name in provided:
            new_value = getattr(payload, name)
            old_value = getattr(entry, name)
            if new_value != old_value:
                note(name, old_value, new_value)
                setattr(entry, name, new_value)

    interval_reversed = (
        entry.time_from is not None
        and entry.time_to is not None
        and entry.time_to < entry.time_from
    )
    if interval_reversed:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Конец интервала не может быть раньше начала.",
        )
    if not changes:
        return entry_out(entry)

    entry.updated_at = utc_now()
    _audit(
        db,
        request,
        AuditAction.WORK_SCHEDULE_ENTRY_UPDATED,
        actor=user,
        details="; ".join(changes),
    )
    db.commit()
    db.refresh(entry)
    return entry_out(entry)


@router.delete(
    "/entries/{entry_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Удалить служебную строку графика",
)
def delete_schedule_entry(
    entry_id: str,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> Response:
    entry = _entry_or_404(db, entry_id)
    _check_entry_manageable(db, user, entry)
    details = entry_audit_details(entry)
    db.delete(entry)
    _audit(
        db,
        request,
        AuditAction.WORK_SCHEDULE_ENTRY_DELETED,
        actor=user,
        details=details,
    )
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# --- Импорт графика выхода из Excel -------------------------------------------
#
# Два шага: «проверить без сохранения» (превью) и подтверждение с явными
# действиями по строкам. Файл читается один раз в память с ограничением
# размера и никогда не сохраняется на диск; после подтверждения в БД остаются
# только счётчики, ключи строк и связи с созданными сущностями.
#
# Права переиспользуют модель карточки: сопоставление идёт только по
# доступным пользователю кандидатам, а автор импорта не становится владельцем
# созданных карточек. Каждая исходная строка сохраняется отдельно от кандидата
# и записи графика; изменение и ручное назначение ответственности пишутся в аудит.


async def _read_import_upload(file: UploadFile) -> bytes:
    """Прочитать загрузку целиком, не превышая лимит (файл не пишется на диск)."""

    payload = bytearray()
    while True:
        chunk = await file.read(256 * 1024)
        if not chunk:
            break
        payload.extend(chunk)
        if len(payload) > MAX_IMPORT_BYTES:
            raise HTTPException(
                status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                detail=(
                    f"Файл больше {MAX_IMPORT_BYTES // (1024 * 1024)} МБ. "
                    "Разбейте график на части или сократите файл."
                ),
            )
    return bytes(payload)


def _parse_or_422(payload: bytes) -> ParsedScheduleSheet:
    try:
        return parse_schedule_workbook(payload)
    except ScheduleImportFormatError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from None


def _visible_candidates(db: Session, user: User) -> list[Candidate]:
    """Кандидаты, доступные пользователю (та же модель, что у картотеки)."""

    scope = visible_candidate_conditions(db, user)
    statement = select(Candidate).where(Candidate.deleted_at.is_(None))
    if scope:
        statement = statement.where(*scope)
    return list(db.scalars(statement).all())


def _lookup_from(candidate: Candidate) -> LookupCandidate:
    return LookupCandidate(
        id=candidate.id,
        display_name=candidate.full_name,
        full_name_normalized=candidate.full_name_normalized,
        stage=candidate.stage.value,
        phone_normalized=candidate.phone_normalized,
    )


def _linked_source_candidate(
    db: Session,
    user: User,
    previous: ScheduleImportRow | None,
    visible_candidates: dict[UUID, Candidate],
) -> Candidate | None:
    """Resolve a previous source mapping without widening HR candidate access.

    Unassigned import-created cards have no HR owner by design. The current
    importer may continue synchronizing that exact linked source row, while a
    candidate assigned to another HR remains hidden unless the user already
    has the normal all-candidates permission.
    """

    if previous is None or previous.candidate_id is None:
        return None
    visible = visible_candidates.get(previous.candidate_id)
    if visible is not None:
        return visible
    linked = db.get(Candidate, previous.candidate_id)
    if (
        linked is not None
        and linked.deleted_at is None
        and (linked.owner_user_id is None or linked.owner_user_id == user.id)
    ):
        return linked
    return None


def _active_import_rows(db: Session) -> list[ScheduleImportRow]:
    """Rows in the current source set, in source order."""

    return list(
        db.scalars(
            select(ScheduleImportRow)
            .where(ScheduleImportRow.is_active.is_(True))
            .order_by(
                ScheduleImportRow.row_order, ScheduleImportRow.sheet_row, ScheduleImportRow.id
            )
        ).all()
    )


def _source_row_type(row: ParsedScheduleRow) -> SourceRowType:
    # A person with an invalid/missing schedule value is still a person source
    # row: keep it assignable and exportable while the parse error is surfaced
    # separately in the preview/result status.
    if row.kind == "candidate":
        return "person"
    if row.parse_error is not None:
        return "error"
    return row.kind


def _reconcile_source_rows(
    rows: list[ParsedScheduleRow], previous: list[ScheduleImportRow]
) -> tuple[dict[int, ScheduleImportRow | None], dict[int, str]]:
    """Match rows by identity first, then by physical row for in-place edits.

    Date, time, position and other editable values are intentionally absent
    from the identity. Matching exact identities before physical row numbers
    means inserted/reordered source rows do not shift all existing links.
    """

    available = {item.id: item for item in previous}
    by_identity: dict[str, list[ScheduleImportRow]] = {}
    for item in previous:
        by_identity.setdefault(item.source_identity, []).append(item)
    for bucket in by_identity.values():
        bucket.sort(key=lambda item: (item.row_order, item.sheet_row, str(item.id)))

    matched: dict[int, ScheduleImportRow | None] = {}
    identities: dict[int, str] = {}
    for row in rows:
        identity = row_identity_key(row, kind_override=_source_row_type(row))
        identities[row.row_index] = identity
        candidates = [item for item in by_identity.get(identity, []) if item.id in available]
        if candidates:
            old = min(
                candidates,
                key=lambda item: (abs(item.row_order - row.row_index), item.sheet_row),
            )
            available.pop(old.id, None)
            matched[row.row_index] = old

    # If the name itself was edited in place, preserve the old row key and its
    # manual candidate/owner links using the original physical sheet row.
    for row in rows:
        if row.row_index in matched:
            continue
        row_type = _source_row_type(row)
        candidates = [
            item
            for item in available.values()
            if item.sheet_row == row.sheet_row and item.row_type == row_type
        ]
        if candidates:
            old = min(candidates, key=lambda item: (item.row_order, str(item.id)))
            available.pop(old.id, None)
            matched[row.row_index] = old
        else:
            matched[row.row_index] = None

    # New rows with identical names/phones use an occurrence suffix so each
    # source line has a distinct key and remains independently exportable.
    occurrences: dict[str, int] = {}
    used_keys: set[str] = set()
    keys: dict[int, str] = {}
    for row in rows:
        identity = identities[row.row_index]
        occurrence = occurrences.get(identity, 0)
        occurrences[identity] = occurrence + 1
        previous_match = matched[row.row_index]
        if previous_match is not None:
            key = previous_match.row_key
        else:
            key = make_row_key(row, kind_override=_source_row_type(row), occurrence=occurrence)
            while key in used_keys:
                occurrence += 1
                key = make_row_key(row, kind_override=_source_row_type(row), occurrence=occurrence)
        keys[row.row_index] = key
        used_keys.add(key)
    return matched, keys


def _source_cell_value(value: object) -> object:
    """Encode openpyxl cell values as portable JSON without the original file."""

    if isinstance(value, datetime):
        return {"__type__": "datetime", "value": value.isoformat()}
    if isinstance(value, date):
        return {"__type__": "date", "value": value.isoformat()}
    if isinstance(value, time):
        return {"__type__": "time", "value": value.isoformat()}
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def _source_row_signature(row: ParsedScheduleRow, row_type: str) -> str:
    payload = {
        "row_type": row_type,
        "full_name": row.raw_name,
        "date": row.entry_date.isoformat() if row.entry_date else None,
        "time_from": row.time_from.isoformat() if row.time_from else None,
        "time_to": row.time_to.isoformat() if row.time_to else None,
        "organization": row.organization,
        "department": row.department,
        "position": row.position,
        "shift": row.shift,
        "comment": row.comment,
        "phone": row.phone_normalized,
        "source_values": [_source_cell_value(value) for value in row.source_values],
        "parse_error": row.parse_error,
    }
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)


def _source_row_changed(row: ParsedScheduleRow, old: ScheduleImportRow | None) -> bool:
    if old is None:
        return True
    old_payload = {
        "row_type": old.row_type,
        "full_name": old.full_name,
        "date": old.entry_date.isoformat() if old.entry_date else None,
        "time_from": old.time_from.isoformat() if old.time_from else None,
        "time_to": old.time_to.isoformat() if old.time_to else None,
        "organization": old.organization,
        "department": old.department,
        "position": old.position,
        "shift": old.shift,
        "comment": old.comment,
        "phone": old.phone_normalized,
        "source_values": old.source_values,
        "parse_error": old.parse_error,
    }
    return (
        _source_row_signature(row, _source_row_type(row))
        != json.dumps(old_payload, ensure_ascii=False, sort_keys=True, default=str)
        or old.row_order != row.row_index
    )


def _serialize_schedule_import(db: Session) -> None:
    """Serialize all versions of the current master-table import on PostgreSQL."""

    bind = db.bind
    if bind is None or bind.dialect.name != "postgresql":
        return
    digest = hashlib.sha256(b"hr-manager:schedule-import:current-master").digest()
    lock_key = int.from_bytes(digest[:8], "big", signed=True)
    db.execute(text("SELECT pg_advisory_xact_lock(:lock_key)"), {"lock_key": lock_key})


def _suggested_action(
    row: ParsedScheduleRow,
    *,
    has_match: bool,
    previous: ScheduleImportRow | None,
    mapped_candidate_visible: bool,
) -> SuggestedAction:
    if row.parse_error is not None or row.kind == "skip":
        return "skip"
    if previous is not None and previous.decision == "skip":
        return "skip"
    if row.kind == "service":
        return "service"
    if previous is not None and previous.candidate_id is not None:
        return "match" if mapped_candidate_visible else "skip"
    if has_match:
        return "match"
    return "create"


def _can_manage_source_schedule(db: Session, user: User) -> bool:
    """Managers/admins and HR with the existing all-candidates grant may manage the master."""

    return hr_sees_all_candidates(db, user)


def _require_source_schedule_manager(db: Session, user: User) -> None:
    if not _can_manage_source_schedule(db, user):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                "Текущий импорт и распределение ответственности доступны руководителю "
                "или HR с правом видеть всех кандидатов."
            ),
        )


@router.get(
    "/import/rows",
    response_model=ActiveScheduleImportRows,
    summary="Текущие строки исходной таблицы",
)
def list_active_import_rows(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> ActiveScheduleImportRows:
    _require_source_schedule_manager(db, user)
    latest = db.scalar(
        select(ScheduleImport)
        .order_by(ScheduleImport.created_at.desc(), ScheduleImport.id.desc())
        .limit(1)
    )
    if latest is None:
        return ActiveScheduleImportRows(can_assign=True)

    active = _active_import_rows(db)
    candidate_ids = {row.candidate_id for row in active if row.candidate_id is not None}
    candidates = (
        {
            item.id: item
            for item in db.scalars(select(Candidate).where(Candidate.id.in_(candidate_ids))).all()
        }
        if candidate_ids
        else {}
    )
    owner_ids = {row.owner_user_id for row in active if row.owner_user_id is not None}
    owner_ids.update(
        candidate.owner_user_id
        for candidate in candidates.values()
        if candidate.owner_user_id is not None
    )
    owners = (
        {item.id: item for item in db.scalars(select(User).where(User.id.in_(owner_ids))).all()}
        if owner_ids
        else {}
    )

    items: list[ActiveScheduleImportRow] = []
    for row in active:
        candidate = candidates.get(row.candidate_id) if row.candidate_id is not None else None
        owner_id = candidate.owner_user_id if candidate is not None else row.owner_user_id
        owner = owners.get(owner_id) if owner_id is not None else None
        items.append(
            ActiveScheduleImportRow(
                row_key=row.row_key,
                row_order=row.row_order,
                sheet_row=row.sheet_row,
                row_type=cast(SourceRowType, row.row_type),
                full_name=row.full_name,
                entry_date=row.entry_date,
                time_from=row.time_from,
                time_to=row.time_to,
                organization=row.organization,
                department=row.department,
                position=row.position,
                candidate_id=row.candidate_id,
                owner_user_id=owner_id,
                owner_name=(owner.full_name or owner.username) if owner is not None else None,
                schedule_ready=row.schedule_ready,
                sync_status=cast(ImportSyncStatus, row.sync_status),
            )
        )
    people = sum(1 for row in active if row.row_type == "person")
    return ActiveScheduleImportRows(
        import_id=latest.id,
        file_name=latest.file_name,
        imported_at=latest.created_at,
        active_people=people,
        can_assign=True,
        rows=items,
    )


@router.patch(
    "/import/assignments",
    response_model=ScheduleImportAssignmentResult,
    summary="Назначить ответственного HR одному или нескольким людям из импорта",
)
def assign_import_rows(
    payload: ScheduleImportAssignmentInput,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> ScheduleImportAssignmentResult:
    _require_source_schedule_manager(db, user)
    row_keys = list(dict.fromkeys(payload.row_keys))
    if len(row_keys) != len(payload.row_keys):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="В списке строк есть повторяющиеся идентификаторы.",
        )
    rows_by_key: dict[str, ScheduleImportRow] = {}
    # Keep the query below SQLite/PostgreSQL parameter-count limits while still
    # supporting the maximum 5,000 source rows in one bulk assignment.
    for offset in range(0, len(row_keys), 500):
        chunk = row_keys[offset : offset + 500]
        chunk_rows = db.scalars(
            select(ScheduleImportRow)
            .where(ScheduleImportRow.row_key.in_(chunk), ScheduleImportRow.is_active.is_(True))
            .order_by(ScheduleImportRow.row_order)
            .with_for_update()
        ).all()
        rows_by_key.update((row.row_key, row) for row in chunk_rows)
    rows = [rows_by_key[key] for key in row_keys if key in rows_by_key]
    if len(rows) != len(row_keys):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "Набор строк уже изменился после импорта. Обновите список и повторите назначение."
            ),
        )
    if any(row.row_type != "person" for row in rows):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Ответственного можно назначить только строке с человеком.",
        )

    owner: User | None = None
    if payload.owner_user_id is not None:
        owner = resolve_assignee(
            db,
            payload.owner_user_id,
            missing_detail="Ответственный не найден или неактивен.",
            invalid_detail="Ответственным может быть только активный HR-менеджер.",
        )
    owner_id = owner.id if owner is not None else None
    now = utc_now()
    changed = 0
    for row in rows:
        candidate = db.get(Candidate, row.candidate_id) if row.candidate_id is not None else None
        old_owner_id = candidate.owner_user_id if candidate is not None else row.owner_user_id
        row.owner_user_id = owner_id
        if candidate is not None:
            candidate.owner_user_id = owner_id
            candidate.updated_at = now
        if old_owner_id == owner_id:
            continue
        changed += 1
        action = (
            AuditAction.CANDIDATE_ASSIGNED
            if candidate is not None
            else AuditAction.WORK_SCHEDULE_ROW_ASSIGNED
        )
        record_event(
            db,
            action,
            actor=user,
            candidate_id=candidate.id if candidate is not None else None,
            ip_address=client_ip(request),
            user_agent=user_agent(request.headers),
            details=f"row_key={row.row_key} from={old_owner_id or '-'} to={owner_id or '-'}",
            commit=False,
        )
    db.commit()
    return ScheduleImportAssignmentResult(
        updated=changed,
        owner_user_id=owner_id,
        owner_name=(owner.full_name or owner.username) if owner is not None else None,
    )


@router.post(
    "/import/preview",
    response_model=WorkScheduleImportPreview,
    summary="Импорт графика из Excel: проверить файл без сохранения",
)
async def preview_work_schedule_import(
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> WorkScheduleImportPreview:
    """Parse and match every source row without writing to the database."""

    payload = await _read_import_upload(file)
    parsed = _parse_or_422(payload)
    file_sha256 = hashlib.sha256(payload).hexdigest()

    candidates = _visible_candidates(db, user)
    candidates_by_id = {candidate.id: candidate for candidate in candidates}
    matches = build_row_matches(parsed.rows, [_lookup_from(candidate) for candidate in candidates])
    previous = _active_import_rows(db)
    previous_by_index, source_keys = _reconcile_source_rows(parsed.rows, previous)
    users_by_id: dict[UUID, User] = {}
    owner_ids = {row.owner_user_id for row in previous if row.owner_user_id is not None}
    for candidate in candidates:
        if candidate.owner_user_id is not None:
            owner_ids.add(candidate.owner_user_id)
    if owner_ids:
        users_by_id = {
            item.id: item for item in db.scalars(select(User).where(User.id.in_(owner_ids))).all()
        }

    preview_rows: list[ImportRowPreview] = []
    counters = {
        "candidate": 0,
        "service": 0,
        "skip": 0,
        "error": 0,
        "new": 0,
        "match": 0,
        "ambiguous": 0,
    }
    for row in parsed.rows:
        old = previous_by_index[row.row_index]
        match_result = matches[row.row_index]
        mapped_candidate = _linked_source_candidate(db, user, old, candidates_by_id)
        if mapped_candidate is not None:
            reason = (
                "phone"
                if row.phone_normalized
                and row.phone_normalized == mapped_candidate.phone_normalized
                else "exact_name"
            )
            match_result = type(match_result)(
                match={
                    "candidate_id": mapped_candidate.id,
                    "full_name": mapped_candidate.full_name,
                    "stage": mapped_candidate.stage.value,
                    "reason": reason,
                    "confident": True,
                }
            )
        has_match = match_result.match is not None or bool(match_result.options)
        suggested = _suggested_action(
            row,
            has_match=has_match,
            previous=old,
            mapped_candidate_visible=mapped_candidate is not None,
        )
        match_info = (
            ImportMatchInfo.model_validate(match_result.match) if match_result.match else None
        )
        options = [ImportMatchInfo.model_validate(item) for item in match_result.options]

        owner_id = (
            mapped_candidate.owner_user_id
            if mapped_candidate is not None
            else (old.owner_user_id if old is not None else None)
        )
        owner = users_by_id.get(owner_id) if owner_id is not None else None
        warnings = list(row.warnings)
        if row.kind == "candidate" and match_info is not None and not match_info.confident:
            warnings.append("Совпадение только по ФИО: проверьте, что это тот же человек.")
        if old is not None:
            warnings.append("Строка уже импортировалась — её данные синхронизируются без дубля.")
        if _source_row_type(row) == "person" and owner_id is None:
            warnings.append("Ответственный HR не назначен.")

        if row.parse_error is not None:
            counters["error"] += 1
        else:
            counters[row.kind] += 1
        if suggested == "create":
            counters["new"] += 1
        elif suggested == "match":
            counters["match"] += 1
        if options or (match_info is not None and not match_info.confident):
            counters["ambiguous"] += 1

        preview_rows.append(
            ImportRowPreview(
                row_index=row.row_index,
                sheet_row=row.sheet_row,
                entry_date=row.entry_date,
                full_name=row.raw_name,
                time_display=row.time_display,
                time_from=row.time_from,
                time_to=row.time_to,
                organization=row.organization,
                department=row.department,
                position=row.position,
                shift=row.shift,
                comment=row.comment,
                phone_masked=mask_phone_display(row.phone_display),
                kind=row.kind,
                row_type=_source_row_type(row),
                name_confidence=row.name_confidence,
                suggested_action=suggested,
                match=match_info,
                match_options=options,
                candidate_id=(mapped_candidate.id if mapped_candidate else None),
                source_row_key=source_keys[row.row_index],
                owner_user_id=owner_id,
                owner_name=(owner.full_name or owner.username) if owner is not None else None,
                schedule_ready=(
                    row.entry_date is not None
                    and row.kind in ("candidate", "service")
                    and row.parse_error is None
                ),
                already_imported=old is not None,
                warnings=warnings,
                parse_error=row.parse_error,
            )
        )

    summary = ImportPreviewSummary(
        rows_total=len(parsed.rows),
        days_total=len(parsed.days),
        candidate_rows=counters["candidate"],
        service_rows=counters["service"],
        skipped_rows=counters["skip"],
        error_rows=counters["error"],
        new_count=counters["new"],
        match_count=counters["match"],
        ambiguous_count=counters["ambiguous"],
    )
    return WorkScheduleImportPreview(
        file_name=sanitize_upload_filename(file.filename),
        file_sha256=file_sha256,
        sheet_title=parsed.sheet_title,
        days=parsed.days,
        warnings=list(parsed.warnings),
        rows=preview_rows,
        summary=summary,
    )


# --- Подтверждение импорта ------------------------------------------------------

_RESULT_LABELS = {
    "created": "Создан кандидат",
    "matched": "Сопоставлен (без изменений)",
    "updated": "Обновлены поля выхода",
    "service": "Создана служебная запись",
    "skipped": "Пропущена",
    "error": "Ошибка",
}
_ACTION_LABELS = {
    "create": "создать кандидата",
    "match": "сопоставить",
    "service": "служебная запись",
    "skip": "пропустить",
}


def _schedule_audit_value(value: object | None) -> str:
    if value is None:
        return "—"
    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%d %H:%M")
    if isinstance(value, date):
        return value.strftime("%Y-%m-%d")
    if isinstance(value, time):
        return value.strftime("%H:%M")
    return str(value)


def _apply_start_fields(
    candidate: Candidate,
    row: ParsedScheduleRow,
    *,
    previous_row: ScheduleImportRow | None = None,
) -> list[str]:
    """Synchronize working fields from the source row without touching owner."""

    changes: list[str] = []

    def note(field_name: str, old: object, new: object) -> None:
        changes.append(
            f"{field_name}: {_schedule_audit_value(old)} -> {_schedule_audit_value(new)}"
        )

    if candidate.start_date != row.entry_date:
        note("start_date", candidate.start_date, row.entry_date)
        candidate.start_date = row.entry_date
    if candidate.start_time != row.time_from:
        note("start_time", candidate.start_time, row.time_from)
        candidate.start_time = row.time_from
    for field_name, new_value in (
        ("start_organization", row.organization),
        ("start_department", row.department),
        ("shift", row.shift),
    ):
        old_value = getattr(candidate, field_name)
        if old_value != new_value:
            note(field_name, old_value, new_value)
            setattr(candidate, field_name, new_value)
    if candidate.position != (row.position or ""):
        note("position", candidate.position, row.position or "")
        candidate.position = row.position or ""
    if candidate.start_comment != row.comment:
        had = "был" if candidate.start_comment else "—"
        changes.append(f"start_comment: {had} -> <изменён, {len(row.comment or '')} симв.>")
        candidate.start_comment = row.comment

    # An Excel-created card may be renamed or have its phone corrected in the
    # master. Do not overwrite a manually matched non-Excel card's identity.
    if candidate.source == CandidateSource.EXCEL_IMPORT:
        was_source_name = (
            previous_row is None or previous_row.name_normalized == candidate.full_name_normalized
        )
        if was_source_name and candidate.full_name_normalized != row.name_normalized:
            changes.append("full_name: <изменено>")
            candidate.full_name = row.raw_name or candidate.full_name
            candidate.full_name_normalized = row.name_normalized
        if (
            candidate.phone_normalized != row.phone_normalized
            or candidate.phone != row.phone_display
        ):
            changes.append("phone: <изменён>")
            candidate.phone = row.phone_display
            candidate.phone_normalized = row.phone_normalized

    if changes:
        candidate.updated_at = utc_now()
    return changes


def _csv_safe(value: object) -> str:
    """Текст для CSV-отчёта без формульной инъекции («=…», «+…» — текст)."""

    if value is None:
        return ""
    text = str(value)
    if text[:1] in ("=", "+", "-", "@", "\t", "\r"):
        return "'" + text
    return text


def _build_report_csv(results: list[ImportRowResult]) -> str:
    """Отчёт по строкам импорта для скачивания (разделитель «;», BOM для Excel)."""

    buffer = io.StringIO()
    writer = csv.writer(buffer, delimiter=";", quoting=csv.QUOTE_ALL, lineterminator="\r\n")
    writer.writerow(["Строка файла", "Дата", "Время", "Действие", "Результат", "Причина"])
    for item in results:
        writer.writerow(
            [
                _csv_safe(item.sheet_row),
                _csv_safe(item.entry_date),
                _csv_safe(item.time_display),
                _csv_safe(item.action_label),
                _csv_safe(_RESULT_LABELS.get(item.result, item.result)),
                _csv_safe(item.reason),
            ]
        )
    return "\ufeff" + buffer.getvalue()


def _create_candidate_from_row(
    db: Session,
    row: ParsedScheduleRow,
    *,
    owner_user_id: UUID | None,
) -> Candidate:
    """Create the card for a source person, preserving an explicit assignment only."""

    candidate = Candidate(
        full_name=row.raw_name or "",
        full_name_normalized=row.name_normalized,
        phone=row.phone_display,
        phone_normalized=row.phone_normalized,
        email=None,
        email_normalized=None,
        source=CandidateSource.EXCEL_IMPORT,
        position=row.position or "",
        owner_user_id=owner_user_id,
        stage=CandidateStage.OFFER,
        stage_position=CANDIDATE_STAGE_POSITION[CandidateStage.OFFER],
    )
    db.add(candidate)
    db.flush()
    _apply_start_fields(candidate, row)
    # Analytics facts are attributed to a responsible HR. An intentionally
    # unassigned card has no HR attribution until it is manually allocated.
    if owner_user_id is not None:
        record_fact(
            db,
            fact_type=AnalyticsFactType.CANDIDATE_CREATED,
            candidate_id=candidate.id,
            owner_user_id=owner_user_id,
            fact_at=candidate.created_at,
            source=candidate.source.value,
        )
    return candidate


def _phone_conflict_exists(
    visible: list[Candidate], row: ParsedScheduleRow, *, exclude_id: UUID
) -> bool:
    """Есть ли среди доступных кандидатов другой с тем же телефоном."""

    if not row.phone_normalized:
        return False
    return any(
        candidate.phone_normalized == row.phone_normalized and candidate.id != exclude_id
        for candidate in visible
    )


def _parse_decisions(raw: str) -> dict[int, ImportRowDecision]:
    if not raw.strip():
        return {}
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Поле действий (decisions) должно быть JSON-массивом.",
        ) from None
    try:
        decisions = ImportDecisions.model_validate(
            data if isinstance(data, dict) else {"decisions": data}
        )
    except ValidationError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Неверный формат действий: {exc.errors()[:3]}",
        ) from None
    by_index: dict[int, ImportRowDecision] = {}
    for decision in decisions.decisions:
        if decision.row_index in by_index:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"Действие для строки №{decision.row_index} указано дважды.",
            )
        by_index[decision.row_index] = decision
    return by_index


@router.post(
    "/import",
    response_model=WorkScheduleImportResult,
    summary="Импорт графика из Excel: подтвердить и синхронизировать строки",
)
async def confirm_work_schedule_import(
    request: Request,
    file: UploadFile = File(...),
    decisions: str = Form(default=""),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> WorkScheduleImportResult:
    """Confirm the import and synchronize source rows without deleting people."""

    payload = await _read_import_upload(file)
    parsed = _parse_or_422(payload)
    file_sha256 = hashlib.sha256(payload).hexdigest()
    _serialize_schedule_import(db)

    rows_by_index = {row.row_index: row for row in parsed.rows}
    decision_map = _parse_decisions(decisions)
    unknown = [index for index in decision_map if index not in rows_by_index]
    if unknown:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Действия указаны для несуществующих строк: {sorted(unknown)[:10]}.",
        )

    previous = _active_import_rows(db)
    previous_by_index, source_keys = _reconcile_source_rows(parsed.rows, previous)
    candidates = _visible_candidates(db, user)
    candidates_by_id = {candidate.id: candidate for candidate in candidates}
    matches = build_row_matches(parsed.rows, [_lookup_from(candidate) for candidate in candidates])

    # Validate every user decision before the first write, preserving the
    # existing all-or-nothing behavior of the import endpoint.
    plans: dict[int, tuple[DecisionAction, Candidate | None]] = {}
    for row in parsed.rows:
        old = previous_by_index[row.row_index]
        decision = decision_map.get(row.row_index)
        match_result = matches[row.row_index]
        mapped_candidate = _linked_source_candidate(db, user, old, candidates_by_id)

        if decision is not None:
            action = decision.action
            chosen_id = decision.candidate_id
        else:
            action = _suggested_action(
                row,
                has_match=(match_result.match is not None or bool(match_result.options)),
                previous=old,
                mapped_candidate_visible=mapped_candidate is not None,
            )
            chosen_id = (
                mapped_candidate.id
                if mapped_candidate is not None and action == "match"
                else (
                    match_result.match["candidate_id"] if match_result.match is not None else None
                )
            )

        if row.parse_error is not None or row.kind == "skip":
            action = "skip"
        if action == "match":
            if row.kind != "candidate":
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail=(
                        f"Строка файла №{row.sheet_row}: сопоставлять можно "
                        "только строку с человеком."
                    ),
                )
            if chosen_id is None:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail=(
                        f"Строка файла №{row.sheet_row}: для сопоставления "
                        "выберите кандидата или пропустите строку."
                    ),
                )
            target = candidates_by_id.get(chosen_id)
            if target is None and mapped_candidate is not None and mapped_candidate.id == chosen_id:
                target = mapped_candidate
            if target is None:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail=(
                        f"Строка файла №{row.sheet_row}: кандидат для сопоставления "
                        "недоступен или удалён."
                    ),
                )
        else:
            target = None
        if action == "create" and row.kind != "candidate":
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=(
                    f"Строка файла №{row.sheet_row}: создать карточку можно только для человека."
                ),
            )
        if action == "create" and old is not None and old.candidate_id is not None:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=(
                    f"Строка файла №{row.sheet_row} уже сопоставлена с кандидатом. "
                    "Выберите сопоставление, чтобы не создать дубль."
                ),
            )
        if action == "service" and not (row.raw_name or row.comment):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"Строка файла №{row.sheet_row}: для служебной записи нужен текст.",
            )
        plans[row.row_index] = (action, target)

    import_record = ScheduleImport(
        file_name=sanitize_upload_filename(file.filename),
        file_sha256=file_sha256,
        sheet_title=parsed.sheet_title[:120],
        header_row=parsed.header_row,
        source_headers=parsed.headers,
        source_columns=parsed.columns,
        rows_total=len(parsed.rows),
        created_by_user_id=user.id,
        created_at=utc_now(),
    )
    db.add(import_record)
    db.flush()

    matched_previous_ids = {old.id for old in previous_by_index.values() if old is not None}
    for old in previous:
        old.is_active = False
        if old.id in matched_previous_ids:
            old.sync_status = "superseded"
            old.missing_in_import_id = None
        else:
            old.sync_status = "missing"
            old.missing_in_import_id = import_record.id
            if old.entry_id is not None:
                old_entry = db.get(ScheduleEntry, old.entry_id)
                if old_entry is not None:
                    old_entry.is_active = False
                    old_entry.updated_at = utc_now()

    results: list[ImportRowResult] = []
    created_by_name: dict[str, Candidate] = {}
    counters = {
        "created": 0,
        "matched": 0,
        "updated": 0,
        "service": 0,
        "skipped": 0,
        "errors": 0,
        "rows_added": 0,
        "rows_updated": 0,
        "rows_unchanged": 0,
    }
    now = utc_now()

    for row in parsed.rows:
        action, target = plans[row.row_index]
        old = previous_by_index[row.row_index]
        row_key = source_keys[row.row_index]
        row_type = _source_row_type(row)
        changed = _source_row_changed(row, old)
        sync_status: ImportSyncStatus = (
            "added" if old is None else ("updated" if changed else "unchanged")
        )
        if sync_status == "added":
            counters["rows_added"] += 1
        elif sync_status == "updated":
            counters["rows_updated"] += 1
        else:
            counters["rows_unchanged"] += 1

        result_label: RowResult
        candidate_link = old.candidate_id if old is not None else None
        entry_link = old.entry_id if old is not None else None
        owner_id = old.owner_user_id if old is not None else None
        reason: str | None = None

        if action == "skip":
            if old is not None and old.entry_id is not None:
                old_entry = db.get(ScheduleEntry, old.entry_id)
                if old_entry is not None:
                    old_entry.is_active = False
                    old_entry.updated_at = now
            if row_type == "error" or row.parse_error is not None:
                counters["errors"] += 1
                result_label = "error"
                reason = row.parse_error or "ошибка исходной строки"
            else:
                counters["skipped"] += 1
                result_label = "skipped"
                reason = row.parse_error or "строка сохранена в исходном наборе; действие пропущено"
            if candidate_link is not None:
                linked = db.get(Candidate, candidate_link)
                if linked is not None and linked.owner_user_id is not None:
                    owner_id = linked.owner_user_id
        elif action == "create":
            batch_candidate = created_by_name.get(row.name_normalized)
            if batch_candidate is not None:
                changes = _apply_start_fields(batch_candidate, row, previous_row=old)
                counters["updated" if changes else "matched"] += 1
                result_label = "updated" if changes else "matched"
                candidate_link = batch_candidate.id
                owner_id = batch_candidate.owner_user_id
                reason = "повтор ФИО в файле — использована та же карточка"
            else:
                # A prior explicit source-row assignment is honored; the
                # importing user is never used as the owner implicitly.
                candidate = _create_candidate_from_row(
                    db, row, owner_user_id=old.owner_user_id if old is not None else None
                )
                phone_conflict = _phone_conflict_exists(candidates, row, exclude_id=candidate.id)
                created_by_name[row.name_normalized] = candidate
                counters["created"] += 1
                candidate_link = candidate.id
                owner_id = candidate.owner_user_id
                result_label = "created"
                if phone_conflict:
                    reason = "создана отдельная карточка при совпадении телефона"
                _audit(
                    db,
                    request,
                    AuditAction.CANDIDATE_CREATED,
                    actor=user,
                    details=(
                        f"source=excel_import owner={candidate.owner_user_id or '-'} "
                        f"import={import_record.id}"
                    ),
                )
        elif action == "match":
            assert target is not None
            changes = _apply_start_fields(target, row, previous_row=old)
            candidate_link = target.id
            owner_id = target.owner_user_id
            if changes:
                counters["updated"] += 1
                result_label = "updated"
                record_event(
                    db,
                    AuditAction.CANDIDATE_START_SCHEDULE_CHANGED,
                    actor=user,
                    candidate_id=target.id,
                    ip_address=client_ip(request),
                    user_agent=user_agent(request.headers),
                    details="; ".join([*changes, f"import={import_record.id}"])[:1000],
                    commit=False,
                )
            else:
                counters["matched"] += 1
                result_label = "matched"
        else:  # service row (or a user-directed service record)
            title = (row.raw_name or row.comment or "Служебная запись")[:200]
            entry = db.get(ScheduleEntry, old.entry_id) if old and old.entry_id else None
            if row.entry_date is None:
                if entry is not None:
                    entry.is_active = False
                    entry.updated_at = now
                entry_link = entry.id if entry is not None else None
                result_label = "service"
                reason = "служебная строка сохранена без даты; запись графика пока не создана"
            elif entry is None:
                entry = ScheduleEntry(
                    entry_date=row.entry_date,
                    time_from=row.time_from,
                    time_to=row.time_to,
                    title=title,
                    organization=row.organization,
                    department=row.department,
                    comment=row.comment,
                    # Import provenance is recorded on ScheduleImport, not as
                    # a human author/responsible for a source service row.
                    author_user_id=None,
                    is_active=True,
                    created_at=now,
                    updated_at=now,
                )
                db.add(entry)
                db.flush()
                counters["service"] += 1
                entry_link = entry.id
                result_label = "service"
                _audit(
                    db,
                    request,
                    AuditAction.WORK_SCHEDULE_ENTRY_CREATED,
                    actor=user,
                    details=entry_audit_details(entry),
                )
            else:
                service_changes: list[str] = []
                for field_name, value in (
                    ("entry_date", row.entry_date),
                    ("time_from", row.time_from),
                    ("time_to", row.time_to),
                    ("title", title),
                    ("organization", row.organization),
                    ("department", row.department),
                    ("comment", row.comment),
                ):
                    if getattr(entry, field_name) != value:
                        service_changes.append(field_name)
                        setattr(entry, field_name, value)
                entry.is_active = True
                entry.updated_at = now
                entry_link = entry.id
                result_label = "service"
                if service_changes:
                    record_event(
                        db,
                        AuditAction.WORK_SCHEDULE_ENTRY_UPDATED,
                        actor=user,
                        ip_address=client_ip(request),
                        user_agent=user_agent(request.headers),
                        details=f"fields={','.join(service_changes)} import={import_record.id}",
                        commit=False,
                    )

        # The source row is always persisted, including people intentionally
        # skipped for candidate creation/matching and rows not ready for dates.
        snapshot = ScheduleImportRow(
            import_id=import_record.id,
            row_key=row_key,
            source_identity=row_identity_key(row, kind_override=row_type),
            sheet_row=row.sheet_row,
            row_order=row.row_index,
            row_type=row_type,
            is_active=True,
            sync_status=sync_status,
            decision=action,
            result=result_label,
            missing_in_import_id=None,
            source_values=[_source_cell_value(value) for value in row.source_values],
            full_name=row.raw_name,
            name_normalized=row.name_normalized,
            entry_date=row.entry_date,
            time_from=row.time_from,
            time_to=row.time_to,
            organization=row.organization,
            department=row.department,
            position=row.position,
            shift=row.shift,
            comment=row.comment,
            phone=row.phone_display,
            phone_normalized=row.phone_normalized,
            schedule_ready=(
                row.entry_date is not None
                and row.kind in ("candidate", "service")
                and row.parse_error is None
            ),
            parse_error=row.parse_error,
            warnings=list(row.warnings),
            candidate_id=candidate_link,
            entry_id=entry_link,
            owner_user_id=owner_id,
            created_at=now,
        )
        db.add(snapshot)
        results.append(
            ImportRowResult(
                row_index=row.row_index,
                sheet_row=row.sheet_row,
                entry_date=row.entry_date,
                time_display=row.time_display,
                action_label=_ACTION_LABELS.get(action, action),
                result=result_label,
                candidate_id=candidate_link,
                entry_id=entry_link,
                reason=reason,
                sync_status=sync_status,
            )
        )

    active_people = sum(1 for row in parsed.rows if _source_row_type(row) == "person")
    missing_count = len(previous) - len(matched_previous_ids)
    import_record.created_candidates = counters["created"]
    import_record.matched_candidates = counters["matched"]
    import_record.updated_candidates = counters["updated"]
    import_record.service_entries = counters["service"]
    import_record.skipped_rows = counters["skipped"]
    import_record.error_rows = counters["errors"]
    import_record.rows_added = counters["rows_added"]
    import_record.rows_updated = counters["rows_updated"]
    import_record.rows_unchanged = counters["rows_unchanged"]
    import_record.rows_missing = missing_count
    import_record.active_people = active_people

    _audit(
        db,
        request,
        AuditAction.WORK_SCHEDULE_IMPORTED,
        actor=user,
        details=(
            f"file_sha256={file_sha256} rows={len(parsed.rows)} "
            f"people_active={active_people} created={counters['created']} "
            f"matched={counters['matched']} updated={counters['updated']} "
            f"service={counters['service']} skipped={counters['skipped']} "
            f"errors={counters['errors']} rows_added={counters['rows_added']} "
            f"rows_updated={counters['rows_updated']} "
            f"rows_unchanged={counters['rows_unchanged']} rows_missing={missing_count}"
        ),
    )
    db.commit()

    return WorkScheduleImportResult(
        import_id=import_record.id,
        created=counters["created"],
        matched=counters["matched"],
        updated=counters["updated"],
        service_created=counters["service"],
        skipped=counters["skipped"],
        errors=counters["errors"],
        rows_added=counters["rows_added"],
        rows_updated=counters["rows_updated"],
        rows_unchanged=counters["rows_unchanged"],
        rows_missing=missing_count,
        active_people=active_people,
        rows=results,
        report_csv=_build_report_csv(results),
    )


@router.get("/import/export.xlsx", summary="Экспорт актуального набора исходной таблицы")
def export_active_import_xlsx(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> Response:
    """Export every current source person in input order, including undated/unassigned people."""

    _require_source_schedule_manager(db, user)
    latest = db.scalar(
        select(ScheduleImport)
        .order_by(ScheduleImport.created_at.desc(), ScheduleImport.id.desc())
        .limit(1)
    )
    if latest is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Ещё нет подтверждённого импорта исходной таблицы.",
        )
    active = _active_import_rows(db)
    payload = build_import_source_xlsx(db, latest, active)
    people_count = sum(1 for row in active if row.row_type == "person")
    _audit(
        db,
        request,
        AuditAction.WORK_SCHEDULE_EXPORTED,
        actor=user,
        details=f"mode=latest_import import={latest.id} people={people_count} rows={len(active)}",
    )
    db.commit()
    return Response(
        content=payload,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": 'attachment; filename="current-work-schedule.xlsx"'},
    )
