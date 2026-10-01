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
from typing import Annotated
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
from sqlalchemy import distinct, select
from sqlalchemy.orm import Session

from app.analytics_ledger import record_fact
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
    sanitize_upload_filename,
)
from app.schedule_import_schemas import (
    DecisionAction,
    ImportDecisions,
    ImportMatchInfo,
    ImportPreviewSummary,
    ImportRowDecision,
    ImportRowPreview,
    ImportRowResult,
    RowResult,
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
# доступным пользователю кандидатам, новые кандидаты закрепляются за тем, кто
# импортирует. Все изменения пишутся в существующий аудит (без содержимого
# файла и ПДн в деталях) одной транзакцией — ошибка откатывает всю пачку.


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


def _existing_row_keys(db: Session, keys: set[str]) -> set[str]:
    """Ключи строк, уже импортированные ранее (идемпотентность повтора)."""

    if not keys:
        return set()
    found = db.scalars(
        select(ScheduleImportRow.row_key).where(ScheduleImportRow.row_key.in_(keys))
    ).all()
    return set(found)


def _suggested_action(
    row: ParsedScheduleRow,
    *,
    has_match: bool,
    already_imported: bool,
) -> SuggestedAction:
    if row.parse_error is not None or row.kind == "skip":
        return "skip"
    if already_imported:
        return "skip"
    if row.kind == "service":
        return "service"
    if has_match:
        return "match"
    return "create"


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
    """Разбор и сопоставление без записи: что будет создано/обновлено/пропущено."""

    payload = await _read_import_upload(file)
    parsed = _parse_or_422(payload)
    file_sha256 = hashlib.sha256(payload).hexdigest()

    candidates = _visible_candidates(db, user)
    matches = build_row_matches(parsed.rows, [_lookup_from(c) for c in candidates])
    imported_keys = _existing_row_keys(db, {make_row_key(row) for row in parsed.rows})

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
        match_result = matches[row.row_index]
        key = make_row_key(row)
        already = key in imported_keys
        suggested = _suggested_action(
            row,
            has_match=match_result.match is not None or bool(match_result.options),
            already_imported=already,
        )
        match_info = None
        if match_result.match is not None:
            match_info = ImportMatchInfo.model_validate(match_result.match)
        options = [ImportMatchInfo.model_validate(item) for item in match_result.options]

        warnings = list(row.warnings)
        if row.kind == "candidate" and match_info is not None and not match_info.confident:
            warnings.append("Совпадение только по ФИО: проверьте, что это тот же человек.")
        if already:
            warnings.append("Строка уже импортировалась ранее — повторно не создаётся.")

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
                name_confidence=row.name_confidence,
                suggested_action=suggested,
                match=match_info,
                match_options=options,
                already_imported=already,
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


def _apply_start_fields(candidate: Candidate, row: ParsedScheduleRow) -> list[str]:
    """Перенести поля строки на кандидата; вернуть список изменений для аудита.

    Дата ставится всегда (в этом смысл импорта); время и текстовые поля —
    только если они реально есть в строке, чтобы не затирать вручную введённые
    значения пустотой из файла.
    """

    changes: list[str] = []

    def note(field_name: str, old: object, new: object) -> None:
        changes.append(
            f"{field_name}: {_schedule_audit_value(old)} -> {_schedule_audit_value(new)}"
        )

    if candidate.start_date != row.entry_date:
        note("start_date", candidate.start_date, row.entry_date)
        candidate.start_date = row.entry_date
    if row.time_from is not None and candidate.start_time != row.time_from:
        note("start_time", candidate.start_time, row.time_from)
        candidate.start_time = row.time_from
    if row.organization and candidate.start_organization != row.organization:
        note("start_organization", candidate.start_organization, row.organization)
        candidate.start_organization = row.organization
    if row.department and candidate.start_department != row.department:
        note("start_department", candidate.start_department, row.department)
        candidate.start_department = row.department
    if row.position and candidate.position != row.position:
        note("position", candidate.position, row.position)
        candidate.position = row.position
    if row.shift and candidate.shift != row.shift:
        note("shift", candidate.shift, row.shift)
        candidate.shift = row.shift
    if row.comment and candidate.start_comment != row.comment:
        note("start_comment", candidate.start_comment, row.comment)
        candidate.start_comment = row.comment
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


def _create_candidate_from_row(db: Session, row: ParsedScheduleRow, *, owner: User) -> Candidate:
    """Кандидат из строки графика: только данные из файла, ничего не выдумано.

    Ответственный — тот, кто импортирует (как при создании карточки); этап —
    «Оффер», потому что модель разрешает дату выхода только на финальных
    этапах, а строка графика и есть план выхода. Телефон записывается, только
    если он реально был в файле.
    """

    candidate = Candidate(
        full_name=row.raw_name or "",
        full_name_normalized=row.name_normalized,
        phone=row.phone_display,
        phone_normalized=row.phone_normalized,
        email=None,
        email_normalized=None,
        source=CandidateSource.EXCEL_IMPORT,
        position=row.position or "",
        owner_user_id=owner.id,
        stage=CandidateStage.OFFER,
        stage_position=CANDIDATE_STAGE_POSITION[CandidateStage.OFFER],
    )
    db.add(candidate)
    db.flush()
    _apply_start_fields(candidate, row)
    record_fact(
        db,
        fact_type=AnalyticsFactType.CANDIDATE_CREATED,
        candidate_id=candidate.id,
        owner_user_id=owner.id,
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
    summary="Импорт графика из Excel: подтвердить и записать",
)
async def confirm_work_schedule_import(
    request: Request,
    file: UploadFile = File(...),
    decisions: str = Form(default=""),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> WorkScheduleImportResult:
    """Атомарный импорт подтверждённых строк: кандидаты + «График выхода».

    Файл перечитывается и переразбирается на подтверждении (превью ничего не
    хранит), действия сверяются с распознанными строками. Любая ошибка
    откатывает всю пачку — половина кандидатов не остаётся.
    """

    payload = await _read_import_upload(file)
    parsed = _parse_or_422(payload)
    file_sha256 = hashlib.sha256(payload).hexdigest()
    rows_by_index = {row.row_index: row for row in parsed.rows}
    decision_map = _parse_decisions(decisions)

    unknown = [index for index in decision_map if index not in rows_by_index]
    if unknown:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Действия указаны для несуществующих строк: {sorted(unknown)[:10]}.",
        )

    candidates = _visible_candidates(db, user)
    candidates_by_id = {candidate.id: candidate for candidate in candidates}
    matches = build_row_matches(parsed.rows, [_lookup_from(c) for c in candidates])
    imported_keys = _existing_row_keys(db, {make_row_key(row) for row in parsed.rows})

    # --- Валидация действий до первой записи -------------------------------
    plans: dict[int, tuple[DecisionAction, Candidate | None]] = {}
    for row in parsed.rows:
        decision = decision_map.get(row.row_index)
        if decision is not None:
            action = decision.action
            chosen_id = decision.candidate_id
        else:
            match_result = matches[row.row_index]
            action = _suggested_action(
                row,
                has_match=match_result.match is not None or bool(match_result.options),
                already_imported=make_row_key(row) in imported_keys,
            )
            chosen_id = (
                match_result.match["candidate_id"] if match_result.match is not None else None
            )

        if row.parse_error is not None:
            action = "skip"
        if row.kind == "skip":
            action = "skip"
        if action == "match":
            if chosen_id is None:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail=(
                        f"Строка файла №{row.sheet_row}: для сопоставления "
                        "выберите кандидата или пропустите строку."
                    ),
                )
            target = candidates_by_id.get(chosen_id)
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
        if action == "create" and not row.raw_name:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"Строка файла №{row.sheet_row}: без ФИО кандидата создать нельзя.",
            )
        if action == "service" and not (row.raw_name or row.comment):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=(f"Строка файла №{row.sheet_row}: для служебной записи нужен текст."),
            )
        plans[row.row_index] = (action, target)

    # --- Применение одной транзакцией ---------------------------------------
    import_record = ScheduleImport(
        file_name=sanitize_upload_filename(file.filename),
        file_sha256=file_sha256,
        sheet_title=parsed.sheet_title[:120],
        rows_total=len(parsed.rows),
        created_by_user_id=user.id,
        created_at=utc_now(),
    )
    db.add(import_record)
    db.flush()

    results: list[ImportRowResult] = []
    seen_keys: set[str] = set()
    created_by_name: dict[str, Candidate] = {}
    counters = {
        "created": 0,
        "matched": 0,
        "updated": 0,
        "service": 0,
        "skipped": 0,
        "errors": 0,
    }

    for row in parsed.rows:
        action, target = plans[row.row_index]
        key = make_row_key(row)
        result_label: RowResult
        candidate_link: UUID | None = None
        entry_link: UUID | None = None
        reason: str | None = None

        if action == "skip":
            counters["skipped"] += 1
            result_label = "skipped"
            reason = row.parse_error or (
                "пустой слот без ФИО" if row.kind == "skip" else "пропущена пользователем"
            )
        elif key in imported_keys:
            counters["skipped"] += 1
            result_label = "skipped"
            reason = "уже импортировалась ранее"
        elif key in seen_keys:
            counters["skipped"] += 1
            result_label = "skipped"
            reason = "дубль внутри файла"
        elif action == "create":
            # Повтор того же ФИО внутри пачки не создаёт второго кандидата:
            # первая строка создаёт, остальные обновляют поля выхода.
            batch_candidate = created_by_name.get(row.name_normalized)
            if batch_candidate is not None:
                changes = _apply_start_fields(batch_candidate, row)
                counters["updated" if changes else "matched"] += 1
                result_label = "updated" if changes else "matched"
                candidate_link = batch_candidate.id
                reason = "повтор ФИО в файле — поля выхода обновлены"
            else:
                candidate = _create_candidate_from_row(db, row, owner=user)
                phone_conflict = _phone_conflict_exists(candidates, row, exclude_id=candidate.id)
                created_by_name[row.name_normalized] = candidate
                counters["created"] += 1
                candidate_link = candidate.id
                result_label = "created"
                if phone_conflict:
                    reason = "создан, хотя кандидат с таким телефоном уже есть"
                _audit(
                    db,
                    request,
                    AuditAction.DUPLICATE_CANDIDATE_CREATED
                    if phone_conflict
                    else AuditAction.CANDIDATE_CREATED,
                    actor=user,
                    details=(f"source=excel_import owner={user.id} import={import_record.id}"),
                )
            db.add(
                ScheduleImportRow(
                    import_id=import_record.id,
                    row_key=key,
                    sheet_row=row.sheet_row,
                    result=result_label,
                    candidate_id=candidate_link,
                    created_at=utc_now(),
                )
            )
            seen_keys.add(key)
        elif action == "match":
            assert target is not None  # проверено на валидации
            changes = _apply_start_fields(target, row)
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
            candidate_link = target.id
            db.add(
                ScheduleImportRow(
                    import_id=import_record.id,
                    row_key=key,
                    sheet_row=row.sheet_row,
                    result=result_label,
                    candidate_id=target.id,
                    created_at=utc_now(),
                )
            )
            seen_keys.add(key)
        else:  # service
            entry = ScheduleEntry(
                entry_date=row.entry_date,
                time_from=row.time_from,
                time_to=row.time_to,
                title=(row.raw_name or row.comment or "Служебная запись")[:200],
                organization=row.organization,
                department=row.department,
                comment=row.comment,
                author_user_id=user.id,
                created_at=utc_now(),
                updated_at=utc_now(),
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
            db.add(
                ScheduleImportRow(
                    import_id=import_record.id,
                    row_key=key,
                    sheet_row=row.sheet_row,
                    result=result_label,
                    entry_id=entry.id,
                    created_at=utc_now(),
                )
            )
            seen_keys.add(key)

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
            )
        )

    import_record.created_candidates = counters["created"]
    import_record.matched_candidates = counters["matched"]
    import_record.updated_candidates = counters["updated"]
    import_record.service_entries = counters["service"]
    import_record.skipped_rows = counters["skipped"]
    import_record.error_rows = counters["errors"]
    _audit(
        db,
        request,
        AuditAction.WORK_SCHEDULE_IMPORTED,
        actor=user,
        details=(
            f"file_sha256={file_sha256} rows={len(parsed.rows)} "
            f"created={counters['created']} matched={counters['matched']} "
            f"updated={counters['updated']} service={counters['service']} "
            f"skipped={counters['skipped']} errors={counters['errors']}"
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
        rows=results,
        report_csv=_build_report_csv(results),
    )
