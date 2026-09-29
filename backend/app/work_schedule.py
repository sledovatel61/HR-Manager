"""«График выхода на работу» (Phase 18) — общая логика.

Модуль собирает дневной график выходов из двух источников:

* кандидаты с заполненной датой выхода (``candidates.start_date``);
* служебные строки без кандидата (``schedule_entries``) — «Увольнение
  13:00–14:00», «перевод», «отработка грузчик», «медосмотр».

Правила, зафиксированные здесь (и проверяемые тестами):

* soft-deleted кандидаты не попадают в график никогда;
* ``rejected`` («Отказ») показывается с пометкой «не вышел» и по умолчанию
  отфильтрован — его включает ``include_rejected=true`` (или явный фильтр
  ``stage=rejected``);
* ``fired`` («Уволен») остаётся в графике с пометкой «уволен»: строка не
  исчезает молча;
* порядок строк — дата, затем время (строки без времени идут в конце дня),
  затем ФИО/текст; нумерация внутри дня считается один раз на сервере, чтобы
  экран, Excel и печать совпадали;
* права переиспользуют существующую модель: HR видит своих кандидатов
  (``owner_user_id``), руководитель/администратор — всех; активный грант
  ``candidate_documents_all`` или ``pilot_full_access`` расширяет HR до всей
  базы. Новой модели прав не вводится.
"""

from __future__ import annotations

import io
from collections.abc import Iterable, Sequence
from datetime import date, time
from typing import Any, cast
from uuid import UUID

from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.worksheet import Worksheet
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import (
    AccessGrantScope,
    Candidate,
    CandidateStage,
    ScheduleEntry,
    User,
    UserRole,
)
from app.schemas import (
    ScheduleEntryOut,
    WorkScheduleRow,
)

# Этапы, на которых разрешено назначать дату выхода.
START_STAGES: tuple[CandidateStage, ...] = (
    CandidateStage.OFFER,
    CandidateStage.HIRED,
    CandidateStage.STARTED,
)

# Текстовые поля блока «Выход на работу»: значение записывается только если
# поле реально пришло в запросе (см. ``model_fields_set``), поэтому null
# означает «очистить», а отсутствие поля — «не менять».
START_TEXT_FIELDS: tuple[str, ...] = (
    "start_organization",
    "start_department",
    "shift",
    "start_comment",
)
START_DATE_FIELDS: tuple[str, ...] = ("start_date", "start_time")

# Пометки строк. ``rejected`` — «не вышел», ``fired`` — «уволен»: строка не
# пропадает молча, но и не выглядит как планируемый выход.
_STARTED_STATUS = ("started", "Вышел")
_NOT_CAME_STATUS = ("not_came", "Не вышел")
_DISMISSED_STATUS = ("dismissed", "Уволен")
_PLANNED_STATUS = ("planned", "Планируется")

_STATUS_BY_STAGE: dict[CandidateStage, tuple[str, str]] = {
    CandidateStage.STARTED: _STARTED_STATUS,
    CandidateStage.REJECTED: _NOT_CAME_STATUS,
    CandidateStage.FIRED: _DISMISSED_STATUS,
}

# Пометка внутри колонки «ФИО» — интерфейс и Excel показывают одно и то же.
_NAME_NOTES: dict[str, str] = {
    "not_came": "не вышел",
    "dismissed": "уволен",
}

_EXCEL_HEADERS = (
    "№",
    "ФИО",
    "Время",
    "Организация",
    "Отдел",
    "Должность",
    "Смена",
    "Комментарий",
    "Ответственный HR",
)
_EXCEL_WIDTHS = (5, 34, 12, 20, 26, 24, 11, 34, 20)

_WEEKDAYS = ("пн", "вт", "ср", "чт", "пт", "сб", "вс")
_MONTHS = (
    "янв.",
    "февр.",
    "мар.",
    "апр.",
    "мая",
    "июн.",
    "июл.",
    "авг.",
    "сент.",
    "окт.",
    "нояб.",
    "дек.",
)

# CSV/формулы-инъекции: значения, начинающиеся с этих символов, обязаны
# попадать в файл как текст (а не как формула).
_FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")


def day_label(day: date) -> str:
    """«пн, 10 авг. 2026» — формат строки-разделителя из образца."""
    return f"{_WEEKDAYS[day.weekday()]}, {day.day} {_MONTHS[day.month - 1]} {day.year}"


def day_label_short(day: date) -> str:
    """«10.08.2026» — компактная дата для подписей и имён файлов."""
    return f"{day.day:02d}.{day.month:02d}.{day.year:04d}"


def hr_sees_all_candidates(db: Session, user: User) -> bool:
    """Может ли пользователь видеть кандидатов вне своей очереди.

    Руководитель и администратор — да. HR — только при активном гранте
    ``candidate_documents_all`` (scope «все кандидаты») или
    ``pilot_full_access``. Проверка переиспользует ``app.documents.has_grant``,
    чтобы не дублировать модель прав.
    """

    # Identity-map копия могла устареть (роль/грант менялись в другой сессии).
    fresh = db.get(User, user.id, populate_existing=True) or user
    if fresh.role != UserRole.HR:
        return True
    from app.documents import has_grant

    return has_grant(db, fresh, AccessGrantScope.CANDIDATE_DOCUMENTS_ALL)


def visible_candidate_conditions(db: Session, user: User) -> list[Any]:
    """Условия видимости кандидатов (та же модель, что у карточки)."""

    if hr_sees_all_candidates(db, user):
        return []
    return [Candidate.owner_user_id == user.id]


def _cleaned(value: str | None) -> str:
    return (value or "").strip().casefold()


def _matches(value: str | None, needle: str) -> bool:
    return needle in _cleaned(value)


def build_rows(
    db: Session,
    *,
    user: User,
    date_from: date | None = None,
    date_to: date | None = None,
    organization: str | None = None,
    department: str | None = None,
    position: str | None = None,
    shift: str | None = None,
    owner_id: UUID | None = None,
    stage: CandidateStage | None = None,
    query: str | None = None,
    include_rejected: bool = False,
) -> list[WorkScheduleRow]:
    """Собрать, отфильтровать, отсортировать и пронумеровать строки графика.

    Фильтры по подразделениям/должности/смене и поиск выполняются в Python по
    ``casefold``: это единственный способ одинаково фильтровать кириллицу на
    PostgreSQL и на SQLite (SQLite ``lower()`` не сворачивает регистр
    кириллицы). Объём строк ограничен самим смыслом графика — в него попадают
    только кандидаты с назначенной датой выхода.
    """

    scope = visible_candidate_conditions(db, user)
    candidate_stmt = select(Candidate).where(Candidate.deleted_at.is_(None))
    if scope:
        candidate_stmt = candidate_stmt.where(*scope)
    if date_from is not None:
        candidate_stmt = candidate_stmt.where(Candidate.start_date >= date_from)
    if date_to is not None:
        candidate_stmt = candidate_stmt.where(Candidate.start_date <= date_to)
    # HR всегда видит только свои строки; для руководителя/администратора
    # фильтр по ответственному работает явно.
    if owner_id is not None and not scope:
        candidate_stmt = candidate_stmt.where(Candidate.owner_user_id == owner_id)

    candidates_with_dates = list(db.scalars(candidate_stmt).all())
    # Кандидаты без даты выхода в графике не участвуют: график — про конкретный
    # день. Если дата была очищена в карточке, кандидат исчезает из графика,
    # но остальные поля места работы сохраняются.
    candidates_with_dates = [item for item in candidates_with_dates if item.start_date is not None]

    entry_stmt = select(ScheduleEntry)
    if date_from is not None:
        entry_stmt = entry_stmt.where(ScheduleEntry.entry_date >= date_from)
    if date_to is not None:
        entry_stmt = entry_stmt.where(ScheduleEntry.entry_date <= date_to)
    entries = list(db.scalars(entry_stmt).all())

    rows: list[WorkScheduleRow] = []
    for candidate in candidates_with_dates:
        status, status_label = _STATUS_BY_STAGE.get(candidate.stage, _PLANNED_STATUS)
        rows.append(
            WorkScheduleRow(
                kind="candidate",
                id=candidate.id,
                candidate_id=candidate.id,
                entry_date=candidate.start_date,  # type: ignore[arg-type]
                number=0,
                start_time=candidate.start_time,
                full_name=candidate.full_name,
                display_name=_display_name(candidate.full_name, status),
                organization=candidate.start_organization,
                department=candidate.start_department,
                position=candidate.position,
                shift=candidate.shift,
                comment=candidate.start_comment,
                owner_user_id=candidate.owner_user_id,
                owner_username=candidate.owner_username,
                stage=candidate.stage,
                status=status,  # type: ignore[arg-type]
                status_label=status_label,
            )
        )

    author_ids = {entry.author_user_id for entry in entries if entry.author_user_id is not None}
    authors: dict[UUID, str] = {}
    if author_ids:
        authors = {
            user.id: user.username
            for user in db.scalars(select(User).where(User.id.in_(author_ids))).all()
        }
    for entry in entries:
        rows.append(
            WorkScheduleRow(
                kind="entry",
                id=entry.id,
                candidate_id=None,
                entry_date=entry.entry_date,
                number=0,
                start_time=entry.time_from,
                end_time=entry.time_to,
                full_name=None,
                display_name=entry.title,
                organization=entry.organization,
                department=entry.department,
                position="",
                shift=None,
                comment=entry.comment,
                owner_user_id=entry.author_user_id,
                owner_username=(
                    authors.get(entry.author_user_id) if entry.author_user_id is not None else None
                ),
                stage=None,
                status="planned",
                status_label="",
            )
        )

    needle = _cleaned(query) if query else ""
    selected: list[WorkScheduleRow] = []
    for row in rows:
        if row.kind == "candidate":
            if stage is not None and row.stage != stage:
                continue
            hide_rejected = (
                row.status == "not_came"
                and not include_rejected
                and stage != CandidateStage.REJECTED
            )
            if hide_rejected:
                # Отказавшиеся по умолчанию отфильтрованы (переключатель в UI).
                continue
            if shift and not _matches(row.shift, _cleaned(shift)):
                continue
        else:
            # Служебная строка не относится к этапам воронки и должности:
            # при явном фильтре по ним она не показывается.
            if stage is not None or position:
                continue
            if owner_id is not None and row.owner_user_id != owner_id and not scope:
                continue
        if organization and not _matches(row.organization, _cleaned(organization)):
            continue
        if department and not _matches(row.department, _cleaned(department)):
            continue
        if position and not _matches(row.position, _cleaned(position)):
            continue
        if needle:
            haystack = " ".join(
                part
                for part in (
                    row.full_name,
                    row.display_name,
                    row.organization,
                    row.department,
                    row.position,
                    row.shift,
                    row.comment,
                )
                if part
            )
            if needle not in haystack.casefold():
                continue
        selected.append(row)

    selected.sort(key=_sort_key)
    _number_rows(selected)
    return selected


def _display_name(full_name: str, status: str) -> str:
    note = _NAME_NOTES.get(status)
    return f"{full_name} — {note}" if note else full_name


def _sort_key(row: WorkScheduleRow) -> tuple:
    """Дата → время (без времени — в конец дня) → тип → ФИО/текст → id."""

    has_time = row.start_time is not None
    return (
        row.entry_date,
        not has_time,
        row.start_time or time.min,
        0 if row.kind == "candidate" else 1,
        (row.full_name or row.display_name).casefold(),
        str(row.id),
    )


def _number_rows(rows: list[WorkScheduleRow]) -> None:
    """Сквозная нумерация внутри дня — как в образце «График приемов»."""

    current_day: date | None = None
    number = 0
    for row in rows:
        if row.entry_date != current_day:
            current_day = row.entry_date
            number = 0
        number += 1
        row.number = number


def day_summaries(rows: Sequence[WorkScheduleRow]) -> list[tuple[date, int, int]]:
    """Дни в порядке следования: (дата, число выходов, всего строк)."""

    summary: list[tuple[date, int, int]] = []
    for row in rows:
        if summary and summary[-1][0] == row.entry_date:
            day, planned, total = summary[-1]
            summary[-1] = (
                day,
                planned + (1 if row.kind == "candidate" else 0),
                total + 1,
            )
        else:
            summary.append((row.entry_date, 1 if row.kind == "candidate" else 0, 1))
    return summary


def entry_out(entry: ScheduleEntry) -> ScheduleEntryOut:
    """Ответ CRUD служебной строки (с автором, без утечки лишних полей)."""

    return ScheduleEntryOut(
        id=entry.id,
        entry_date=entry.entry_date,
        time_from=entry.time_from,
        time_to=entry.time_to,
        title=entry.title,
        organization=entry.organization,
        department=entry.department,
        comment=entry.comment,
        author_user_id=entry.author_user_id,
        author_username=entry.author_username or None,
        created_at=entry.created_at,
        updated_at=entry.updated_at,
    )


def entry_audit_details(entry: ScheduleEntry, *, changed: Iterable[str] | None = None) -> str:
    """Строка аудита служебной записи (без персональных данных)."""

    if changed is not None:
        return "; ".join(changed) if changed else "без изменений"
    return f"date={entry.entry_date.isoformat()} time={format_time_range(entry)}"


def format_time_range(entry: ScheduleEntry) -> str:
    """«13:00–14:00», «13:00» или «—» для служебной строки."""

    if entry.time_from is None:
        return "—"
    start = entry.time_from.strftime("%H:%M")
    if entry.time_to is None:
        return start
    return f"{start}–{entry.time_to.strftime('%H:%M')}"


# --- Excel ------------------------------------------------------------------


def _write_text(cell: Any, value: Any) -> None:
    """Записать значение так, чтобы «=cmd» не стало формулой."""

    cell.value = value
    if isinstance(value, str) and value[:1] in _FORMULA_PREFIXES:
        # openpyxl по умолчанию помечает строку с ведущим «=» как формулу;
        # принудительный строковый тип делает значение обычным текстом.
        cell.data_type = "s"


def build_xlsx(
    rows: Sequence[WorkScheduleRow],
    *,
    period_from: date | None,
    period_to: date | None,
    filters: dict[str, str],
    include_rejected: bool,
    generated_at_label: str,
) -> bytes:
    """Собрать .xlsx «График выхода на работу» в формате образца.

    Строка-заголовок дня (жирная, с заливкой, «пн, 10 авг. 2026»), под ней
    пронумерованные строки дня; ширина колонок подобрана, печать — альбомная,
    шапка таблицы повторяется на каждой странице. Значения, начинающиеся с
    ``= + - @``, записываются текстом.
    """

    workbook = Workbook()
    sheet = cast(Worksheet, workbook.active)
    sheet.title = "График выхода"

    header_font = Font(bold=True, color="FFFFFF")
    header_fill = PatternFill("solid", fgColor="1F4E79")
    day_font = Font(bold=True)
    day_fill = PatternFill("solid", fgColor="DCE6F1")
    title_font = Font(bold=True, size=14)
    service_fill = PatternFill("solid", fgColor="FFF2CC")
    thin = Side(style="thin", color="B7B7B7")
    border = Border(left=thin, right=thin, top=thin, bottom=thin)

    last_column = len(_EXCEL_HEADERS)
    last_letter = get_column_letter(last_column)

    _write_text(sheet.cell(row=1, column=1), "График выхода на работу")
    sheet.cell(row=1, column=1).font = title_font
    sheet.merge_cells(f"A1:{last_letter}1")

    if period_from is not None and period_to is not None:
        period = f"Период: {day_label_short(period_from)} – {day_label_short(period_to)}"
    elif period_from is not None:
        period = f"Период: с {day_label_short(period_from)}"
    elif period_to is not None:
        period = f"Период: по {day_label_short(period_to)}"
    else:
        period = "Период: весь график"
    filters_line = ", ".join(f"{name}: {value}" for name, value in filters.items() if value)
    if include_rejected:
        filters_line = "; ".join(part for part in (filters_line, "показаны отказавшиеся") if part)
    subtitle = f"{period}. {filters_line}" if filters_line else period
    _write_text(sheet.cell(row=2, column=1), subtitle)
    sheet.merge_cells(f"A2:{last_letter}2")

    _write_text(sheet.cell(row=3, column=1), f"Сформировано: {generated_at_label}")
    sheet.merge_cells(f"A3:{last_letter}3")

    header_row = 4
    for index, title in enumerate(_EXCEL_HEADERS, start=1):
        cell = sheet.cell(row=header_row, column=index, value=title)
        cell.font = header_font
        cell.fill = header_fill
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        cell.border = border

    for index, width in enumerate(_EXCEL_WIDTHS, start=1):
        sheet.column_dimensions[get_column_letter(index)].width = width

    row_index = header_row + 1
    current_day: date | None = None
    day_candidates = 0
    if rows:
        for row in rows:
            if row.entry_date != current_day:
                if current_day is not None:
                    row_index += 1  # пустая строка между днями
                current_day = row.entry_date
                day_candidates = sum(
                    1
                    for item in rows
                    if item.entry_date == current_day and item.kind == "candidate"
                )
                day_cell = sheet.cell(
                    row=row_index,
                    column=1,
                    value=f"{day_label(current_day)} — выходов: {day_candidates}",
                )
                day_cell.font = day_font
                day_cell.fill = day_fill
                day_cell.alignment = Alignment(horizontal="left")
                sheet.merge_cells(
                    start_row=row_index, start_column=1, end_row=row_index, end_column=last_column
                )
                for column in range(1, last_column + 1):
                    sheet.cell(row=row_index, column=column).border = border
                row_index += 1

            time_text = "—"
            if row.start_time is not None:
                time_text = row.start_time.strftime("%H:%M")
            elif row.kind == "entry":
                time_text = "—"
            values: tuple[Any, ...] = (
                row.number,
                row.display_name,
                time_text,
                row.organization or "—",
                row.department or "—",
                row.position or "—",
                row.shift or "—",
                row.comment or "—",
                row.owner_username or "—",
            )
            for column, value in enumerate(values, start=1):
                data_cell = sheet.cell(row=row_index, column=column)
                _write_text(data_cell, value)
                data_cell.border = border
                if row.kind == "entry":
                    data_cell.fill = service_fill
                if column in (1, 3):
                    data_cell.alignment = Alignment(horizontal="center")
            row_index += 1

    if not rows:
        empty = sheet.cell(row=row_index, column=1)
        empty.value = "На выбранный период выходов нет."
        empty.font = Font(italic=True)
        sheet.merge_cells(
            start_row=row_index, start_column=1, end_row=row_index, end_column=last_column
        )
        row_index += 1
    else:
        row_index += 1
        total_candidates = sum(1 for item in rows if item.kind == "candidate")
        totals = sheet.cell(
            row=row_index,
            column=1,
            value=f"Итого выходов за период: {total_candidates}",
        )
        totals.font = day_font

    # Печать: альбомная ориентация, шапка таблицы повторяется на каждой
    # странице, таблица ужимается по ширине страницы.
    sheet.page_setup.orientation = "landscape"
    sheet.page_setup.paperSize = sheet.PAPERSIZE_A4
    sheet.page_setup.fitToWidth = 1
    sheet.page_setup.fitToHeight = 0
    if sheet.sheet_properties.pageSetUpPr is not None:
        sheet.sheet_properties.pageSetUpPr.fitToPage = True
    sheet.print_title_rows = f"{header_row}:{header_row}"
    sheet.print_options.horizontalCentered = False
    sheet.freeze_panes = f"A{header_row + 1}"

    buffer = io.BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()
