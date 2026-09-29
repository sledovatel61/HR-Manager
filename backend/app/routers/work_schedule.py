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

from datetime import UTC, date, datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from sqlalchemy import distinct, select
from sqlalchemy.orm import Session

from app.audit import record_event
from app.db import get_db
from app.deps import get_current_user
from app.models import (
    AuditAction,
    Candidate,
    CandidateStage,
    ScheduleEntry,
    User,
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
    day_label_short,
    entry_audit_details,
    entry_out,
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
        include_rejected=include_rejected or stage == CandidateStage.REJECTED,
    )
    return WorkScheduleList(
        items=rows,
        total=len(rows),
        days=len({row.entry_date for row in rows}),
        period_from=date_from,
        period_to=date_to,
        include_rejected=include_rejected,
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
        include_rejected=include_rejected or stage == CandidateStage.REJECTED,
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
        include_rejected=include_rejected,
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
            f"include_rejected={include_rejected}"
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
