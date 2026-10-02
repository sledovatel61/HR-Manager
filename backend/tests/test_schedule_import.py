"""Тесты импорта графика выхода из Excel (фаза поверх Phase 18).

Unit (SQLite): разбор обезличенного образца и синтетических книг — даты-блоки,
объединённые ячейки, пустые строки, все формы времени, служебные строки,
телефоны, смены, ошибки формата; сопоставление и устойчивые ключи.

API (клиент на SQLite): превью без записи, подтверждённый импорт, повторный
импорт без дублей, неоднозначное ФИО, права (чужие кандидаты не видны),
атомарный откат и аудит.

Образец: ``tests/fixtures/work_schedule_sample.xlsx`` — обезличенная копия
структуры реального файла (без реальных ФИО и телефонов).
"""

from __future__ import annotations

import io
import json
from collections.abc import Iterator
from datetime import date, datetime, time
from pathlib import Path
from typing import cast
from uuid import uuid4

import httpx
import pytest
from fastapi.testclient import TestClient
from openpyxl import Workbook, load_workbook
from openpyxl.worksheet.worksheet import Worksheet
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import (
    AnalyticsFact,
    AuditAction,
    AuditEvent,
    Candidate,
    CandidateSource,
    CandidateStage,
    ScheduleEntry,
    ScheduleImport,
    ScheduleImportRow,
    UserRole,
)
from app.routers.auth import reset_login_limiter
from app.routers.work_schedule import _build_report_csv, _source_row_changed
from app.schedule_import import (
    DEFAULT_UPLOAD_FILE_NAME,
    LookupCandidate,
    ScheduleImportFormatError,
    build_row_matches,
    classify_name,
    extract_shift,
    format_phone_display,
    make_row_key,
    mask_phone_display,
    parse_block_date,
    parse_schedule_workbook,
    parse_time_value,
    sanitize_upload_filename,
    validate_xlsx_upload,
)
from app.schedule_import_schemas import ImportRowResult
from tests.conftest import FIXTURE_PASSWORD, make_candidate, make_user

FIXTURE_PATH = Path(__file__).parent / "fixtures" / "work_schedule_sample.xlsx"

DAY1 = date(2026, 8, 10)
DAY2 = date(2026, 8, 11)


@pytest.fixture(autouse=True)
def _clean_limiter() -> Iterator[None]:
    reset_login_limiter()
    yield
    reset_login_limiter()


def _fixture_bytes() -> bytes:
    return FIXTURE_PATH.read_bytes()


def _login(client: TestClient, username: str) -> str:
    response = client.post("/auth/login", json={"username": username, "password": FIXTURE_PASSWORD})
    assert response.status_code == 200, response.text
    return response.json()["csrf_token"]


def _upload(
    client: TestClient, payload: bytes, csrf: str | None = None, *, name: str = "график.xlsx"
) -> httpx.Response:
    headers = {"X-CSRF-Token": csrf} if csrf else {}
    return client.post(
        "/work-schedule/import/preview",
        files={"file": (name, io.BytesIO(payload), "application/octet-stream")},
        headers=headers,
    )


def _confirm(
    client: TestClient,
    payload: bytes,
    csrf: str,
    decisions: list[dict] | None = None,
    *,
    name: str = "график.xlsx",
) -> httpx.Response:
    data: dict[str, object] = {"decisions": json.dumps({"decisions": decisions or []})}
    return client.post(
        "/work-schedule/import",
        files={"file": (name, io.BytesIO(payload), "application/octet-stream")},
        data=data,
        headers={"X-CSRF-Token": csrf},
    )


# --- Разбор образца -------------------------------------------------------------


def test_fixture_parses_days_and_row_kinds() -> None:
    parsed = parse_schedule_workbook(_fixture_bytes())

    assert parsed.sheet_title == "Лист1"
    days = parsed.days
    assert days[0] == DAY1
    assert days[-1] == date(2026, 8, 14)
    # Дата из текста «20.08 К 14:00» добавляет день, отличный от блоков.
    assert date(2026, 8, 20) in days

    kinds = [row.kind for row in parsed.rows]
    assert (
        kinds.count("service") == 5
    )  # Увольнение, Дир логист, менеджеры, отработка, перевод? нет — см. ниже
    # Конкретные служебные строки:
    service_titles = {row.raw_name for row in parsed.rows if row.kind == "service"}
    assert "Увольнение" in service_titles
    assert "Отработка грузчик" in service_titles
    assert "Дир логист" in service_titles
    assert "менед по пер. ИТЦ" in service_titles
    # Фамилии с корнем служебного слова остаются людьми.
    assert "Переводнова Марина" not in service_titles
    assert any(
        row.raw_name == "Медосмотров Павел Павлович" and row.kind == "candidate"
        for row in parsed.rows
    )


def test_fixture_parses_time_formats_phones_and_shifts() -> None:
    parsed = parse_schedule_workbook(_fixture_bytes())
    by_name = {row.raw_name: row for row in parsed.rows if row.raw_name}

    # Текст «10.08 к 9:30» → 09:30 того же дня.
    assert by_name["Тестова Анна Ивановна"].time_from == time(9, 30)
    assert by_name["Тестова Анна Ивановна"].entry_date == DAY1
    # Число 10 в колонке времени → 10:00.
    assert by_name["Целочислов Иван Иванович"].time_from == time(10, 0)
    # «12.00» текстом → 12:00, смена из комментария.
    assert by_name["Числовиков Денис Денисович"].time_from == time(12, 0)
    assert by_name["Числовиков Денис Денисович"].shift == "2 смена"
    # Телефон в колонке H + заметка справа уходят в комментарий.
    proba = by_name["Пробова Мария Сергеевна"]
    assert proba.phone_normalized == "+79000000001"
    assert proba.phone_display is not None and "900" in proba.phone_display
    assert proba.comment == "потом перевод на упаковщика"
    # Телефон в колонке комментариев; «уехавший» текст — тоже комментарий.
    vera = by_name["Комментателефонова Вера Веровна"]
    assert vera.phone_normalized == "+79000000003"
    assert vera.comment == "подготовка"
    # Нераспознанное время не теряется — уходит в комментарий.
    med = by_name["Медосмотров Павел Павлович"]
    assert med.time_from is None
    assert med.comment == "после мед осмотра"
    assert any("не удалось разобрать время" in warning for warning in med.warnings)
    # Скобочная пометка «(перевод)» — в комментарий, ФИО без неё.
    skob = by_name["Скобкин Антон"]
    assert skob.comment == "перевод"
    # Интервал служебной строки «13:00-14-00».
    uvol = next(row for row in parsed.rows if row.raw_name == "Увольнение")
    assert uvol.time_from == time(13, 0) and uvol.time_to == time(14, 0)
    # Итог: телефон в H, смена и комментарий в I и J.
    shirok = by_name["Широкова Зинаида Зинаидовна"]
    assert shirok.phone_normalized == "+79000000004"
    assert shirok.shift == "2 смена"
    assert shirok.comment == "при наличии места"


def test_fixture_date_override_and_blocks() -> None:
    parsed = parse_schedule_workbook(_fixture_bytes())
    by_name = {row.raw_name: row for row in parsed.rows if row.raw_name}

    # Дата из текста переопределяет дату блока и помечается предупреждением.
    moved = by_name["Переносов Олег Иванович"]
    assert moved.entry_date == date(2026, 8, 20)
    assert moved.time_from == time(14, 0)
    assert any("20.08.2026" in warning for warning in moved.warnings)

    # Дубль даты блока в колонке времени — время не выдумывается.
    duble = by_name["Дубледатова Кира Львовна"]
    assert duble.entry_date == DAY1
    assert duble.time_from is None

    # Пустые слоты (время без ФИО) пропускаются.
    skipped = [row for row in parsed.rows if row.kind == "skip"]
    assert any("пустой слот" in (row.warnings[0] if row.warnings else "") for row in skipped)
    # Мусорная строка из чисел не импортируется.
    assert any(
        row.kind == "skip" and "не похоже на имя" in " ".join(row.warnings) for row in parsed.rows
    )


def test_repeated_name_rows_share_nothing_silently() -> None:
    """Повторы ФИО в файле — обе строки видны в превью (решение за человеком)."""

    parsed = parse_schedule_workbook(_fixture_bytes())
    repeated = [row for row in parsed.rows if row.raw_name == "Однофамилев Константин"]
    assert len(repeated) == 2
    assert {row.entry_date for row in repeated} == {date(2026, 8, 13)}


# --- Времена и даты: точечные проверки -------------------------------------------


def test_parse_time_value_variants() -> None:
    block = DAY1

    assert parse_time_value(time(9, 30), block_date=block, context_year=2026).time_from == time(
        9, 30
    )
    assert parse_time_value("9:30", block_date=block, context_year=2026).time_from == time(9, 30)
    assert parse_time_value("К 9:00", block_date=block, context_year=2026).time_from == time(9, 0)
    assert parse_time_value("8;00", block_date=block, context_year=2026).time_from == time(8, 0)
    assert parse_time_value(10.0, block_date=block, context_year=2026).time_from == time(10, 0)
    assert parse_time_value(0.5, block_date=block, context_year=2026).time_from == time(12, 0)
    fraction = parse_time_value("после мед осмотра", block_date=block, context_year=2026)
    assert fraction.time_from is None
    assert fraction.comment_addition == "после мед осмотра"
    ranged = parse_time_value("13:00-14-00", block_date=block, context_year=2026)
    assert ranged.time_from == time(13, 0) and ranged.time_to == time(14, 0)
    # Дата-время с ненулевым временем даёт время.
    stamped = parse_time_value(datetime(2026, 8, 10, 15, 45), block_date=block, context_year=2026)
    assert stamped.time_from == time(15, 45)


def test_parse_block_date_variants() -> None:
    assert parse_block_date(datetime(2026, 8, 10), context_year=None) == DAY1
    assert parse_block_date(date(2026, 8, 10), context_year=None) == DAY1
    assert parse_block_date("10.08.2026", context_year=None) == DAY1
    assert parse_block_date("пн, 10 авг. 2026", context_year=2026) == DAY1
    assert parse_block_date("10 августа", context_year=2026) == DAY1
    # Серийная дата Excel: 46244 = 2026-08-10 в базе 1899-12-30.
    assert parse_block_date((DAY1 - date(1899, 12, 30)).days, context_year=None) == DAY1
    assert parse_block_date("мусор", context_year=None) is None


# --- Валидация загрузки ------------------------------------------------------------


def test_upload_validation_rejects_wrong_types() -> None:
    with pytest.raises(ScheduleImportFormatError):
        validate_xlsx_upload(b"", "график.xlsx", None)
    with pytest.raises(ScheduleImportFormatError):
        validate_xlsx_upload(b"PK\x03\x04rest", "график.xlsm", None)
    with pytest.raises(ScheduleImportFormatError):
        validate_xlsx_upload(b"PK\x03\x04rest", "график.xls", None)
    with pytest.raises(ScheduleImportFormatError):
        validate_xlsx_upload("не архив".encode(), "график.xlsx", None)
    # MIME вне белого списка.
    with pytest.raises(ScheduleImportFormatError):
        validate_xlsx_upload(b"PK\x03\x04rest", "график.xlsx", "text/html")


def test_sanitize_upload_filename_table() -> None:
    """Сервер не доверяет file.filename: путь, кавычки, управляющие, юникод."""
    table: list[tuple[str | None, str]] = [
        # Полный Windows-путь — остаётся только базовое имя.
        (
            "C:\\Users\\User\\Documents\\HR\\HR Manager Desktop\\LLM"
            "\\30.09.2026\\График приемов .xlsx",
            "График приемов .xlsx",
        ),
        # Unix-путь — то же правило.
        ("/home/user/документы/график выходов.xlsx", "график выходов.xlsx"),
        # Сетевой путь.
        ("\\\\server\\share\\график.xlsx", "график.xlsx"),
        # Путь без базового имени — нейтральное значение по умолчанию.
        ("C:\\Users\\User\\", DEFAULT_UPLOAD_FILE_NAME),
        ("/home/user/", DEFAULT_UPLOAD_FILE_NAME),
        # Пустое и отсутствующее имя.
        ("", DEFAULT_UPLOAD_FILE_NAME),
        (None, DEFAULT_UPLOAD_FILE_NAME),
        ("   ", DEFAULT_UPLOAD_FILE_NAME),
        # Кавычки и управляющие символы вычищаются.
        ('"график".xlsx', "график.xlsx"),
        ("график\n\t .xlsx", "график .xlsx"),
        # Диск без разделителя («относительный» путь с диском).
        ("C:график.xlsx", "график.xlsx"),
        # Юникод сохраняется.
        ("График приёмов ☀ 2026.xlsx", "График приёмов ☀ 2026.xlsx"),
        # Только кавычки/точки — смысла нет, нейтральное значение.
        ('"""', DEFAULT_UPLOAD_FILE_NAME),
        ("...", DEFAULT_UPLOAD_FILE_NAME),
    ]
    for raw, expected in table:
        assert sanitize_upload_filename(raw) == expected, repr(raw)
        result = sanitize_upload_filename(raw)
        assert "/" not in result and "\\" not in result and ":" not in result


def test_sanitize_upload_filename_limit_keeps_multibyte_intact() -> None:
    long_name = "аб" * 300 + ".xlsx"
    result = sanitize_upload_filename(long_name)
    assert len(result) == 255
    # Рез по кодовым точкам: строка остаётся валидным UTF-8 без обрывков.
    assert result.encode("utf-8").decode("utf-8") == result
    assert result == long_name[:255]
    # Кириллическая «ы» — два байта в UTF-8: половина символа отброшена не быть.
    assert sanitize_upload_filename("ы" * 500, limit=255) == "ы" * 255


def test_macro_zip_is_rejected() -> None:
    import zipfile

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("xl/vbaProject.bin", "sub AutoOpen")
        archive.writestr("[Content_Types].xml", "<Types/>")
    with pytest.raises(ScheduleImportFormatError, match="макрос"):
        validate_xlsx_upload(buffer.getvalue(), "график.xlsx", None)


def _blank_workbook() -> tuple[Workbook, Worksheet]:
    workbook = Workbook()
    return workbook, cast(Worksheet, workbook.active)


def _workbook_bytes(workbook: Workbook) -> bytes:
    buffer = io.BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


def _current_source_workbook(
    *, dated_time: time = time(9, 30), include_undated: bool = True
) -> bytes:
    workbook, sheet = _blank_workbook()
    sheet.append(
        [
            "пР",
            "ФИО",
            "Дата и время",
            "Организация",
            "Наименование отдела",
            "должность",
            "комментарии",
        ]
    )
    if include_undated:
        sheet.append(
            [1, "Недатова Ирина Петровна", None, "ООО Пример", "Цех Один", "Кладовщик", None]
        )
    date_row = sheet.max_row + 1
    sheet.cell(row=date_row, column=2, value=datetime.combine(DAY1, time.min))
    sheet.merge_cells(start_row=date_row, start_column=2, end_row=date_row, end_column=7)
    person_row = date_row + 1
    sheet.append(
        [2, "Датова Мария Ивановна", dated_time, "ООО Пример", "Цех Один", "Грузчик", None]
    )
    sheet.append([3, "Увольнение", "13:00–14:00", None, None, None, None])
    assert sheet.max_row == person_row + 1
    return _workbook_bytes(workbook)


def test_unknown_format_raises_readable_error() -> None:
    workbook, sheet = _blank_workbook()
    sheet.append(["Раз", "Два", "Три"])
    sheet.append(["а", "б", "в"])
    buffer = io.BytesIO()
    workbook.save(buffer)
    with pytest.raises(ScheduleImportFormatError, match="заголовков"):
        parse_schedule_workbook(buffer.getvalue())


def test_header_only_file_raises() -> None:
    workbook, sheet = _blank_workbook()
    sheet.append(["№", "ФИО", "Дата и время"])
    buffer = io.BytesIO()
    workbook.save(buffer)
    with pytest.raises(ScheduleImportFormatError, match="нет"):
        parse_schedule_workbook(buffer.getvalue())


# --- Классификация, смена, телефоны, ключи -----------------------------------------


def test_classify_name_variants() -> None:
    assert classify_name("Иванов Иван Иванович")[0] == "candidate"
    assert classify_name("Иванов Иван Иванович")[1] == "full"
    assert classify_name("иванов иван")[0] == "candidate"
    assert classify_name("иванов иван")[1] == "partial"
    assert classify_name("Увольнение")[0] == "service"
    assert classify_name("Отработка грузчик")[0] == "service"
    assert classify_name("Дир логист")[0] == "service"
    assert classify_name("менеджер по персоналу. Вероника")[0] == "service"
    assert classify_name("Переводнова Марина")[0] == "candidate"
    assert classify_name("Медосмотров Павел Павлович")[0] == "candidate"
    assert classify_name("1.0")[0] == "skip"
    kind, _confidence, note = classify_name("Скобкин Антон (перевод)")
    assert kind == "candidate"
    assert note == "перевод"


def test_extract_shift_and_phones() -> None:
    assert extract_shift("1 смена") == ("1 смена", None)
    assert extract_shift("2 смена (подготовка)") == ("2 смена", "(подготовка)")
    assert extract_shift("при наличии места") == (None, "при наличии места")
    assert extract_shift(None) == (None, None)

    assert format_phone_display("79518329029") == "+7 951 832-90-29"
    assert format_phone_display("9518329029") == "+7 951 832-90-29"
    assert format_phone_display("89518329029") == "+7 951 832-90-29"
    masked = mask_phone_display("+7 951 832-90-29")
    assert masked is not None and "832" not in masked and masked.endswith("29")


def test_phone_far_right_of_comment_column_is_detected() -> None:
    """Телефон ищется в колонке комментариев и до 8 колонок правее неё.

    Фиксированных номеров колонок нет: значение стоит в колонке L (5 пустых
    колонок после комментариев) и всё равно распознаётся телефоном, не оседая
    в тексте комментария.
    """
    workbook, sheet = _blank_workbook()
    sheet.append(
        [
            "пР",
            "ФИО",
            "Дата и время",
            "Организация",
            "Наименование отдела",
            "должность",
            "комментарии",
        ]
    )
    block = sheet.cell(row=2, column=2, value=datetime(2026, 8, 12))
    block.number_format = "d mmm"
    sheet.merge_cells(start_row=2, start_column=2, end_row=2, end_column=7)
    sheet.cell(row=3, column=2, value="Дальнова Ирина Петровна")
    sheet.cell(row=3, column=3, value=time(10, 0))
    sheet.cell(row=3, column=7, value="просто заметка")
    sheet.cell(row=3, column=12, value=79000000009.0)  # колонка L

    parsed = parse_schedule_workbook(_workbook_bytes(workbook))
    rows = [row for row in parsed.rows if row.kind == "candidate"]
    assert len(rows) == 1
    assert rows[0].phone_display == "+7 900 000-00-09"
    assert rows[0].phone_normalized == "+79000000009"
    assert rows[0].comment == "просто заметка"


def test_row_key_is_stable_and_content_based() -> None:
    parsed = parse_schedule_workbook(_fixture_bytes())
    first = parsed.rows[0]
    assert make_row_key(first) == make_row_key(first)
    # Другая дата — другой ключ, даже при том же ФИО.
    other = (
        next(row for row in parsed.rows if row.raw_name == first.raw_name)
        if any(
            row.raw_name == first.raw_name and row.entry_date != first.entry_date
            for row in parsed.rows
        )
        else None
    )
    if other is not None:
        assert make_row_key(other) != make_row_key(first)


def test_legacy_backfill_without_source_cells_does_not_mark_identical_row_updated() -> None:
    row = next(
        item
        for item in parse_schedule_workbook(_current_source_workbook(include_undated=False)).rows
        if item.kind == "candidate"
    )
    legacy = ScheduleImportRow(
        import_id=uuid4(),
        row_key=make_row_key(row),
        source_identity=make_row_key(row),
        sheet_row=row.sheet_row,
        row_order=row.row_index,
        row_type="person",
        is_active=True,
        sync_status="added",
        decision="create",
        result="created",
        source_values=[],
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
        schedule_ready=row.entry_date is not None,
        parse_error=row.parse_error,
        warnings=list(row.warnings),
    )
    assert _source_row_changed(row, legacy) is False


def test_build_row_matches_exact_phone_partial_and_multi() -> None:
    rows = parse_schedule_workbook(_fixture_bytes()).rows
    exact = next(row for row in rows if row.raw_name == "Тестова Анна Ивановна")
    phone_row = next(row for row in rows if row.phone_normalized == "+79000000001")
    single_surname = next(row for row in rows if row.raw_name == "Грузчиков")

    candidates = [
        LookupCandidate(uuid4(), "Тестова Анна Ивановна", "тестова анна ивановна", "offer", None),
        LookupCandidate(uuid4(), "Пробова Другая", "пробова другая", "offer", "+79000000001"),
        LookupCandidate(uuid4(), "Грузчиков Иван Петрович", "грузчиков иван петрович", "new", None),
        LookupCandidate(uuid4(), "Грузчиков Пётр Иванович", "грузчиков пётр иванович", "new", None),
    ]
    matches = build_row_matches([exact, phone_row, single_surname], candidates)

    exact_match = matches[exact.row_index].match
    assert exact_match is not None
    assert exact_match["reason"] == "exact_name"
    assert exact_match["confident"] is False
    phone_match = matches[phone_row.row_index].match
    assert phone_match is not None
    assert phone_match["reason"] == "phone"
    assert phone_match["confident"] is True
    # Двойное совпадение по префиксу фамилии — варианты, а не авто-матч.
    assert matches[single_surname.row_index].match is None
    assert len(matches[single_surname.row_index].options) == 2


def test_report_csv_is_formula_injection_safe() -> None:
    results = [
        ImportRowResult(
            row_index=1,
            sheet_row=3,
            entry_date=DAY1,
            time_display="09:00",
            action_label="создать кандидата",
            result="created",
            reason='=HYPERLINK("x")',
        )
    ]
    csv_text = _build_report_csv(results)
    assert "'=HYPERLINK" in csv_text
    assert "\ufeff" in csv_text


# --- API: превью без записи ---------------------------------------------------------


def test_preview_parses_without_writing(client: TestClient, db_session: Session) -> None:
    hr = make_user(db_session, username="hr1", role=UserRole.HR)
    existing = make_candidate(db_session, owner=hr, full_name="Тестова Анна Ивановна")
    csrf = _login(client, "hr1")

    response = _upload(client, _fixture_bytes(), csrf)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["summary"]["rows_total"] == 29
    assert body["summary"]["candidate_rows"] >= 20
    assert body["summary"]["service_rows"] == 5
    assert body["days"][0] == "2026-08-10"

    # Существующий кандидат предложен к сопоставлению (телефон маскируется).
    testova = next(row for row in body["rows"] if row["full_name"] == "Тестова Анна Ивановна")
    assert testova["suggested_action"] == "match"
    assert testova["match"]["candidate_id"] == str(existing.id)
    proba = next(row for row in body["rows"] if row["full_name"] == "Пробова Мария Сергеевна")
    assert proba["phone_masked"] is not None
    assert "900 000-00" not in proba["phone_masked"]  # середина скрыта

    # Превью ничего не пишет.
    assert db_session.scalar(select(func.count()).select_from(Candidate)) == 1
    assert db_session.scalar(select(func.count()).select_from(ScheduleEntry)) == 0
    assert db_session.scalar(select(func.count()).select_from(ScheduleImport)) == 0


def test_preview_rejects_unknown_format_and_requires_auth(
    client: TestClient, db_session: Session
) -> None:
    make_user(db_session, username="hr1", role=UserRole.HR)
    csrf = _login(client, "hr1")

    without_login_client = TestClient(client.app)
    without_login = _upload(without_login_client, _fixture_bytes())
    assert without_login.status_code == 401

    bad = _upload(client, "это не эксель".encode(), csrf)
    assert bad.status_code == 422
    assert "xlsx" in bad.json()["detail"].lower()


# --- API: подтверждённый импорт -------------------------------------------------------


def test_confirm_creates_candidates_entries_and_audit(
    client: TestClient, db_session: Session
) -> None:
    hr = make_user(db_session, username="hr1", role=UserRole.HR)
    csrf = _login(client, "hr1")

    response = _confirm(client, _fixture_bytes(), csrf)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["created"] >= 20
    assert body["service_created"] == 5
    assert body["errors"] == 0

    # Импортёр остаётся автором импорта; назначение кандидатам не выставляется автоматически.
    created = list(
        db_session.scalars(
            select(Candidate).where(Candidate.source == CandidateSource.EXCEL_IMPORT)
        ).all()
    )
    assert len(created) == body["created"]
    for candidate in created:
        assert candidate.owner_user_id is None
        assert candidate.stage == CandidateStage.OFFER
        assert candidate.start_date is not None
    imported_record = db_session.scalar(
        select(ScheduleImport).order_by(ScheduleImport.created_at.desc())
    )
    assert imported_record is not None
    assert imported_record.created_by_user_id == hr.id

    testova = next(
        candidate for candidate in created if candidate.full_name == "Тестова Анна Ивановна"
    )
    assert testova.start_date == DAY1
    assert testova.start_time == time(9, 30)
    assert testova.start_organization == "ООО Пример"
    assert testova.start_department == "Цех Один"

    # Телефон записан в отображаемом виде, нормализован для дедупликации.
    proba = next(
        candidate for candidate in created if candidate.full_name == "Пробова Мария Сергеевна"
    )
    assert proba.phone_normalized == "+79000000001"
    assert proba.phone is not None and proba.phone.startswith("+7")

    # Повтор того же ФИО внутри файла не создаёт второго кандидата:
    # первая строка создаёт, вторая обновляет поля выхода у той же карточки.
    dup_rows = [
        item for item in body["rows"] if item["reason"] and "повтор ФИО в файле" in item["reason"]
    ]
    assert len(dup_rows) == 1
    assert dup_rows[0]["result"] in ("updated", "matched")
    single_name = db_session.scalars(
        select(Candidate).where(Candidate.full_name == "Однофамилев Константин")
    ).all()
    assert len(single_name) == 1
    assert dup_rows[0]["candidate_id"] == str(single_name[0].id)

    # Служебные строки — в графике.
    uvol = db_session.scalars(
        select(ScheduleEntry).where(ScheduleEntry.title == "Увольнение")
    ).all()
    assert len(uvol) == 1
    assert uvol[0].time_from == time(13, 0) and uvol[0].time_to == time(14, 0)
    assert uvol[0].author_user_id is None

    # A regular HR does not acquire visibility of unassigned candidates just
    # because they uploaded the workbook; the five imported service rows remain.
    client.post("/auth/logout", headers={"X-CSRF-Token": csrf})
    _login(client, "hr1")
    schedule = client.get("/work-schedule?from=2026-08-10&to=2026-08-31").json()
    assert schedule["total"] == body["service_created"]

    # Аудит: агрегат импорта без ФИО/телефонов.
    imported = db_session.scalars(
        select(AuditEvent).where(AuditEvent.action == AuditAction.WORK_SCHEDULE_IMPORTED)
    ).all()
    assert len(imported) == 1
    details = imported[0].details or ""
    assert "created=" in details and "file_sha256=" in details
    assert "Тестова" not in details and "900" not in details
    created_events = db_session.scalars(
        select(AuditEvent).where(AuditEvent.action == AuditAction.CANDIDATE_CREATED)
    ).all()
    assert len(created_events) == body["created"]
    for event in created_events:
        assert "excel_import" in (event.details or "")


def test_reimport_same_file_creates_no_duplicates(client: TestClient, db_session: Session) -> None:
    make_user(db_session, username="hr1", role=UserRole.HR)
    csrf = _login(client, "hr1")

    first = _confirm(client, _fixture_bytes(), csrf)
    assert first.status_code == 200
    candidates_after_first = db_session.scalar(select(func.count()).select_from(Candidate))
    entries_after_first = db_session.scalar(select(func.count()).select_from(ScheduleEntry))
    assert candidates_after_first == first.json()["created"]
    assert entries_after_first == first.json()["service_created"]

    second = _confirm(client, _fixture_bytes(), csrf)
    assert second.status_code == 200
    body = second.json()
    assert body["created"] == 0
    assert body["service_created"] == 0
    assert body["rows_added"] == 0
    assert body["rows_updated"] == 0
    assert body["rows_unchanged"] == first.json()["rows_added"]
    assert body["active_people"] == first.json()["active_people"]
    assert db_session.scalar(select(func.count()).select_from(Candidate)) == candidates_after_first
    assert db_session.scalar(select(func.count()).select_from(ScheduleEntry)) == entries_after_first

    # Превью повторного импорта помечает импортированные строки (кандидаты и
    # служебные). Пропущенные в первый раз слоты остаются доступными — это
    # осознанное поведение: их можно импортировать следующей пачкой.
    preview = _upload(client, _fixture_bytes(), csrf).json()
    assert all(
        row["already_imported"]
        for row in preview["rows"]
        if row["kind"] in ("candidate", "service")
    )


def test_current_source_rows_assignment_reimport_and_export(
    client: TestClient, db_session: Session
) -> None:
    manager = make_user(db_session, username="boss", role=UserRole.MANAGER)
    hr = make_user(db_session, username="hr-owner", role=UserRole.HR, full_name="HR Ответственный")
    csrf = _login(client, "boss")
    original = _current_source_workbook()

    preview = _upload(client, original, csrf).json()
    undated_preview = next(
        row for row in preview["rows"] if row["full_name"] == "Недатова Ирина Петровна"
    )
    assert undated_preview["row_type"] == "person"
    assert undated_preview["entry_date"] is None
    assert undated_preview["schedule_ready"] is False

    first = _confirm(client, original, csrf)
    assert first.status_code == 200, first.text
    assert first.json()["active_people"] == 2
    assert first.json()["rows_added"] == 3
    assert db_session.scalar(select(func.count()).select_from(ScheduleImport)) == 1
    db_session.expire_all()
    imported = list(
        db_session.scalars(
            select(Candidate).where(Candidate.source == CandidateSource.EXCEL_IMPORT)
        ).all()
    )
    assert len(imported) == 2
    assert all(candidate.owner_user_id is None for candidate in imported)
    assert (
        db_session.scalar(
            select(func.count())
            .select_from(AnalyticsFact)
            .where(AnalyticsFact.candidate_id.in_([candidate.id for candidate in imported]))
        )
        == 0
    )
    undated_candidate = next(
        candidate for candidate in imported if candidate.full_name == "Недатова Ирина Петровна"
    )
    dated_candidate = next(
        candidate for candidate in imported if candidate.full_name == "Датова Мария Ивановна"
    )
    assert undated_candidate.start_date is None
    import_record = db_session.scalar(select(ScheduleImport))
    assert import_record is not None and import_record.created_by_user_id == manager.id

    current = client.get("/work-schedule/import/rows")
    assert current.status_code == 200, current.text
    current_body = current.json()
    assert current_body["active_people"] == 2
    assert len(current_body["rows"]) == 3
    undated_row = next(
        row for row in current_body["rows"] if row["full_name"] == "Недатова Ирина Петровна"
    )
    dated_row = next(
        row for row in current_body["rows"] if row["full_name"] == "Датова Мария Ивановна"
    )
    assert undated_row["owner_name"] is None
    assert undated_row["schedule_ready"] is False

    # A regular HR cannot read/assign the full source table; managers can.
    client.post("/auth/logout", headers={"X-CSRF-Token": csrf})
    hr_csrf = _login(client, "hr-owner")
    assert client.get("/work-schedule/import/rows").status_code == 403
    denied = client.patch(
        "/work-schedule/import/assignments",
        json={"row_keys": [undated_row["row_key"]], "owner_user_id": str(hr.id)},
        headers={"X-CSRF-Token": hr_csrf},
    )
    assert denied.status_code == 403

    client.post("/auth/logout", headers={"X-CSRF-Token": hr_csrf})
    csrf = _login(client, "boss")
    single = client.patch(
        "/work-schedule/import/assignments",
        json={"row_keys": [undated_row["row_key"]], "owner_user_id": str(hr.id)},
        headers={"X-CSRF-Token": csrf},
    )
    assert single.status_code == 200, single.text
    assert single.json()["updated"] == 1
    bulk = client.patch(
        "/work-schedule/import/assignments",
        json={
            "row_keys": [undated_row["row_key"], dated_row["row_key"]],
            "owner_user_id": str(hr.id),
        },
        headers={"X-CSRF-Token": csrf},
    )
    assert bulk.status_code == 200, bulk.text
    assert bulk.json()["updated"] == 1
    db_session.expire_all()
    assigned_undated = db_session.get(Candidate, undated_candidate.id)
    assigned_dated = db_session.get(Candidate, dated_candidate.id)
    assert assigned_undated is not None
    assert assigned_dated is not None
    assert assigned_undated.owner_user_id == hr.id
    assert assigned_dated.owner_user_id == hr.id
    assignment_audits = list(
        db_session.scalars(
            select(AuditEvent).where(AuditEvent.action == AuditAction.CANDIDATE_ASSIGNED)
        ).all()
    )
    assert len(assignment_audits) == 2
    # Assigning an owner is auditable but does not create a backdated analytics fact.
    assert (
        db_session.scalar(
            select(func.count())
            .select_from(AnalyticsFact)
            .where(AnalyticsFact.candidate_id.in_([undated_candidate.id, dated_candidate.id]))
        )
        == 0
    )

    # Updating the source time does not create duplicate candidates or erase
    # the manually selected HR owner on either candidate.
    updated_source = _current_source_workbook(dated_time=time(10, 15))
    second = _confirm(client, updated_source, csrf)
    assert second.status_code == 200, second.text
    assert second.json()["created"] == 0
    assert second.json()["rows_updated"] >= 1
    db_session.expire_all()
    assert db_session.scalar(select(func.count()).select_from(Candidate)) == 2
    resynced_undated = db_session.get(Candidate, undated_candidate.id)
    resynced_dated = db_session.get(Candidate, dated_candidate.id)
    assert resynced_undated is not None
    assert resynced_dated is not None
    assert resynced_undated.owner_user_id == hr.id
    assert resynced_dated.owner_user_id == hr.id
    assert resynced_dated.start_time == time(10, 15)
    service_entry = db_session.scalar(
        select(ScheduleEntry).where(ScheduleEntry.title == "Увольнение")
    )
    assert service_entry is not None
    service_update = client.patch(
        f"/work-schedule/entries/{service_entry.id}",
        json={"title": "Увольнение перенесено", "time_from": "14:00:00", "time_to": "15:00:00"},
        headers={"X-CSRF-Token": csrf},
    )
    assert service_update.status_code == 200, service_update.text

    exported = client.get("/work-schedule/import/export.xlsx")
    assert exported.status_code == 200, exported.text
    workbook = load_workbook(io.BytesIO(exported.content), data_only=True)
    sheet = cast(Worksheet, workbook.active)
    headers = [cell.value for cell in sheet[1]]
    assert headers[-1] == "Ответственный HR"
    assert not {"candidate_id", "owner_user_id", "row_key"}.intersection(headers)
    assert [sheet.cell(row=row, column=2).value for row in range(2, 5)] == [
        "Недатова Ирина Петровна",
        "Датова Мария Ивановна",
        "Увольнение перенесено",
    ]
    owner_column = headers.index("Ответственный HR") + 1
    assert sheet.cell(row=2, column=owner_column).value == "HR Ответственный"
    assert sheet.cell(row=3, column=3).value == datetime.combine(DAY1, time(10, 15))
    assert sheet.cell(row=4, column=3).value == "10.08.2026 к 14:00–15:00"
    workbook.close()

    # Missing rows are deactivated but never physically deleted, and no longer
    # appear in the current export. The latest-import history remains intact.
    without_undated = _current_source_workbook(dated_time=time(10, 15), include_undated=False)
    third = _confirm(client, without_undated, csrf)
    assert third.status_code == 200, third.text
    assert third.json()["rows_missing"] == 1
    assert third.json()["active_people"] == 1
    db_session.expire_all()
    historical_rows = list(db_session.scalars(select(ScheduleImportRow)).all())
    missing = next(
        row
        for row in historical_rows
        if row.full_name == "Недатова Ирина Петровна" and row.sync_status == "missing"
    )
    assert missing.is_active is False
    assert db_session.get(Candidate, undated_candidate.id) is not None
    assert db_session.scalar(select(func.count()).select_from(ScheduleImport)) == 3
    latest_rows = client.get("/work-schedule/import/rows").json()
    assert latest_rows["active_people"] == 1
    final_export = client.get("/work-schedule/import/export.xlsx")
    final_sheet = cast(
        Worksheet, load_workbook(io.BytesIO(final_export.content), data_only=True).active
    )
    assert final_sheet.max_row == 3  # header + one person + one service row


def test_manual_candidate_mapping_is_preserved_on_reimport(
    client: TestClient, db_session: Session
) -> None:
    make_user(db_session, username="boss", role=UserRole.MANAGER)
    hr = make_user(db_session, username="hr-owner", role=UserRole.HR)
    existing = make_candidate(db_session, owner=hr, full_name="Датова Мария Ивановна")
    csrf = _login(client, "boss")
    payload = _current_source_workbook(include_undated=False)
    preview = _upload(client, payload, csrf).json()
    target_row = next(row for row in preview["rows"] if row["kind"] == "candidate")
    result = _confirm(
        client,
        payload,
        csrf,
        [
            {
                "row_index": target_row["row_index"],
                "action": "match",
                "candidate_id": str(existing.id),
            }
        ],
    )
    assert result.status_code == 200, result.text
    db_session.expire_all()
    snapshots = list(
        db_session.scalars(
            select(ScheduleImportRow).where(ScheduleImportRow.is_active.is_(True))
        ).all()
    )
    mapped = next(row for row in snapshots if row.row_type == "person")
    assert mapped.candidate_id == existing.id

    changed = _current_source_workbook(dated_time=time(11, 5), include_undated=False)
    repeated = _confirm(client, changed, csrf)
    assert repeated.status_code == 200, repeated.text
    db_session.expire_all()
    assert db_session.scalar(select(func.count()).select_from(Candidate)) == 1
    resynced_existing = db_session.get(Candidate, existing.id)
    assert resynced_existing is not None
    assert resynced_existing.start_time == time(11, 5)
    latest = list(
        db_session.scalars(
            select(ScheduleImportRow).where(ScheduleImportRow.is_active.is_(True))
        ).all()
    )
    assert next(row for row in latest if row.row_type == "person").candidate_id == existing.id


def test_importing_hr_can_resync_its_unassigned_source_candidate(
    client: TestClient, db_session: Session
) -> None:
    make_user(db_session, username="hr1", role=UserRole.HR)
    csrf = _login(client, "hr1")
    first = _confirm(client, _current_source_workbook(include_undated=False), csrf)
    assert first.status_code == 200, first.text
    candidate = db_session.scalar(
        select(Candidate).where(Candidate.source == CandidateSource.EXCEL_IMPORT)
    )
    assert candidate is not None and candidate.owner_user_id is None

    changed = _confirm(
        client,
        _current_source_workbook(dated_time=time(10, 45), include_undated=False),
        csrf,
    )
    assert changed.status_code == 200, changed.text
    assert changed.json()["created"] == 0
    db_session.expire_all()
    refreshed = db_session.get(Candidate, candidate.id)
    assert refreshed is not None
    assert refreshed.owner_user_id is None
    assert refreshed.start_time == time(10, 45)
    assert db_session.scalar(select(func.count()).select_from(Candidate)) == 1


def test_confirm_matches_existing_candidate_and_updates(
    client: TestClient, db_session: Session
) -> None:
    hr = make_user(db_session, username="hr1", role=UserRole.HR)
    existing = make_candidate(
        db_session, owner=hr, full_name="Тестова Анна Ивановна", stage=CandidateStage.OFFER
    )
    csrf = _login(client, "hr1")

    response = _confirm(client, _fixture_bytes(), csrf)
    assert response.status_code == 200, response.text
    db_session.refresh(existing)
    assert existing.start_date == DAY1
    assert existing.start_time == time(9, 30)
    assert existing.start_organization == "ООО Пример"
    # Ответственный не меняется при сопоставлении.
    assert existing.owner_user_id == hr.id
    # Новый кандидат с тем же ФИО не создаётся.
    assert (
        db_session.scalar(
            select(func.count())
            .select_from(Candidate)
            .where(Candidate.full_name == "Тестова Анна Ивановна")
        )
        == 1
    )
    # Аудит изменения полей выхода записан.
    events = db_session.scalars(
        select(AuditEvent).where(AuditEvent.action == AuditAction.CANDIDATE_START_SCHEDULE_CHANGED)
    ).all()
    assert any(event.candidate_id == existing.id for event in events)


def test_confirm_requires_choice_for_ambiguous_names(
    client: TestClient, db_session: Session
) -> None:
    manager = make_user(db_session, username="boss", role=UserRole.MANAGER)
    # Два тёзки — руководитель видит обоих, авто-сопоставление запрещено.
    one = make_candidate(db_session, owner=manager, full_name="Однофамилев Константин")
    two = make_candidate(db_session, owner=manager, full_name="Однофамилев Константин")
    csrf = _login(client, "boss")

    response = _confirm(client, _fixture_bytes(), csrf)
    assert response.status_code == 422
    assert "выберите кандидата" in response.json()["detail"]

    # Явный выбор одного из тёзок проходит и не плодит дубликат.
    parsed_rows = _upload(client, _fixture_bytes(), csrf).json()["rows"]
    ambiguous = [row for row in parsed_rows if row["full_name"] == "Однофамилев Константин"]
    assert ambiguous and ambiguous[0]["match_options"]
    decisions = [
        {"row_index": row["row_index"], "action": "match", "candidate_id": str(one.id)}
        for row in ambiguous
    ]
    ok = _confirm(client, _fixture_bytes(), csrf, decisions)
    assert ok.status_code == 200, ok.text
    # Тёзки сопоставлены, новые «Однофамилевы» не созданы.
    assert (
        db_session.scalar(
            select(func.count())
            .select_from(Candidate)
            .where(Candidate.full_name == "Однофамилев Константин")
        )
        == 2
    )
    _ = two


def test_hr_does_not_match_foreign_candidates(client: TestClient, db_session: Session) -> None:
    hr1 = make_user(db_session, username="hr1", role=UserRole.HR)
    hr2 = make_user(db_session, username="hr2", role=UserRole.HR)
    make_candidate(db_session, owner=hr2, full_name="Тестова Анна Ивановна")
    csrf = _login(client, "hr1")

    preview = _upload(client, _fixture_bytes(), csrf).json()
    testova = next(row for row in preview["rows"] if row["full_name"] == "Тестова Анна Ивановна")
    # Чужой кандидат не раскрывается: предлагается создать своего.
    assert testova["suggested_action"] == "create"
    assert testova["match"] is None
    assert testova["match_options"] == []

    response = _confirm(client, _fixture_bytes(), csrf)
    assert response.status_code == 200
    imported = db_session.scalars(
        select(Candidate).where(
            Candidate.full_name == "Тестова Анна Ивановна",
            Candidate.source == CandidateSource.EXCEL_IMPORT,
        )
    ).all()
    assert len(imported) == 1
    assert imported[0].owner_user_id is None
    _ = hr1


def test_confirm_validates_foreign_match_atomically(
    client: TestClient, db_session: Session
) -> None:
    make_user(db_session, username="hr1", role=UserRole.HR)
    make_user(db_session, username="hr2", role=UserRole.HR)
    csrf = _login(client, "hr1")

    preview = _upload(client, _fixture_bytes(), csrf).json()
    candidate_rows = [row for row in preview["rows"] if row["kind"] == "candidate"]
    foreign_id = str(uuid4())
    decisions = [
        {"row_index": candidate_rows[0]["row_index"], "action": "match", "candidate_id": foreign_id}
    ]
    response = _confirm(client, _fixture_bytes(), csrf, decisions)
    assert response.status_code == 422

    # Атомарность: ни одного кандидата, служебной записи или импорта.
    assert db_session.scalar(select(func.count()).select_from(Candidate)) == 0
    assert db_session.scalar(select(func.count()).select_from(ScheduleEntry)) == 0
    assert db_session.scalar(select(func.count()).select_from(ScheduleImport)) == 0
    assert db_session.scalar(select(func.count()).select_from(ScheduleImportRow)) == 0

    # Неизвестный индекс строки — тоже ошибка без записи.
    bad_index = _confirm(client, _fixture_bytes(), csrf, [{"row_index": 9999, "action": "skip"}])
    assert bad_index.status_code == 422
    assert db_session.scalar(select(func.count()).select_from(Candidate)) == 0


def test_confirm_user_decisions_override_defaults(client: TestClient, db_session: Session) -> None:
    make_user(db_session, username="hr1", role=UserRole.HR)
    csrf = _login(client, "hr1")

    preview = _upload(client, _fixture_bytes(), csrf).json()
    service_row = next(row for row in preview["rows"] if row["full_name"] == "Увольнение")
    some_candidate = next(row for row in preview["rows"] if row["kind"] == "candidate")
    decisions = [
        {"row_index": service_row["row_index"], "action": "skip"},
        {"row_index": some_candidate["row_index"], "action": "skip"},
    ]
    response = _confirm(client, _fixture_bytes(), csrf, decisions)
    assert response.status_code == 200, response.text
    assert response.json()["service_created"] == 4  # «Увольнение» пропущено
    assert (
        db_session.scalar(
            select(func.count())
            .select_from(ScheduleEntry)
            .where(ScheduleEntry.title == "Увольнение")
        )
        == 0
    )


# --- Санитизация имени загруженного файла (не доверяем file.filename) -----------


def test_confirm_strips_windows_path_to_base_name(client: TestClient, db_session: Session) -> None:
    make_user(db_session, username="hr1", role=UserRole.HR)
    csrf = _login(client, "hr1")
    windows_path = (
        "C:\\Users\\User\\Documents\\HR\\HR Manager Desktop\\LLM\\30.09.2026\\График приемов .xlsx"
    )

    # Превью тоже отдаёт только базовое имя.
    preview = _upload(client, _fixture_bytes(), csrf, name=windows_path)
    assert preview.status_code == 200, preview.text
    assert preview.json()["file_name"] == "График приемов .xlsx"

    confirmed = _confirm(client, _fixture_bytes(), csrf, name=windows_path)
    assert confirmed.status_code == 200, confirmed.text
    record = db_session.scalar(select(ScheduleImport))
    assert record is not None
    assert record.file_name == "График приемов .xlsx"
    assert "/" not in record.file_name and "\\" not in record.file_name
    assert "Users" not in record.file_name and "Documents" not in record.file_name


def test_confirm_path_only_filename_gets_neutral_default(
    client: TestClient, db_session: Session
) -> None:
    make_user(db_session, username="hr1", role=UserRole.HR)
    csrf = _login(client, "hr1")

    confirmed = _confirm(client, _fixture_bytes(), csrf, name="C:\\Users\\User\\")
    assert confirmed.status_code == 200, confirmed.text
    record = db_session.scalar(select(ScheduleImport))
    assert record is not None
    assert record.file_name == DEFAULT_UPLOAD_FILE_NAME
    assert "/" not in record.file_name and "\\" not in record.file_name
    assert ":" not in record.file_name


def test_confirm_overlong_filename_is_limited(client: TestClient, db_session: Session) -> None:
    make_user(db_session, username="hr1", role=UserRole.HR)
    csrf = _login(client, "hr1")
    long_name = "график" * 120 + ".xlsx"  # 726 символов

    confirmed = _confirm(client, _fixture_bytes(), csrf, name=long_name)
    assert confirmed.status_code == 200, confirmed.text
    record = db_session.scalar(select(ScheduleImport))
    assert record is not None
    assert len(record.file_name) <= 255
    # Обрезка по кодовым точкам: результат — валидный UTF-8 без обрывков.
    assert record.file_name.encode("utf-8").decode("utf-8") == record.file_name


# --- Комментарий из файла не попадает в аудит ----------------------------------

# В комментарии намеренно то, что пользователи иногда пишут в колонку заметок:
# чужое ФИО и телефон. 14 цифр — не «чистый» телефон, парсер оставляет их текстом.
PII_PHONE = "+7 900 123-45-67"
PII_COMMENT = f"Мария Петровна, тел. {PII_PHONE} доб. 123"
PII_DIGITS = "79001234567"


def _pii_comment_workbook() -> bytes:
    workbook, sheet = _blank_workbook()
    sheet.append(
        [
            "пР",
            "ФИО",
            "Дата и время",
            "Организация",
            "Наименование отдела",
            "должность",
            "комментарии",
        ]
    )
    block = sheet.cell(row=2, column=2, value=datetime(2026, 8, 10))
    block.number_format = "d mmm"
    sheet.merge_cells(start_row=2, start_column=2, end_row=2, end_column=7)
    sheet.cell(row=3, column=2, value="Тестова Анна Ивановна")
    sheet.cell(row=3, column=3, value=time(9, 30))
    sheet.cell(row=3, column=4, value="ООО Пример")
    sheet.cell(row=3, column=5, value="Цех Один")
    sheet.cell(row=3, column=6, value="уборщица")
    sheet.cell(row=3, column=7, value=PII_COMMENT)
    return _workbook_bytes(workbook)


def test_created_candidate_audit_has_no_comment_content(
    client: TestClient, db_session: Session
) -> None:
    make_user(db_session, username="hr1", role=UserRole.HR)
    csrf = _login(client, "hr1")

    response = _confirm(client, _pii_comment_workbook(), csrf)
    assert response.status_code == 200, response.text
    assert response.json()["created"] == 1

    # В карточку комментарий пишется целиком — это штатное поле записи.
    candidate = db_session.scalar(
        select(Candidate).where(Candidate.source == CandidateSource.EXCEL_IMPORT)
    )
    assert candidate is not None and candidate.start_comment == PII_COMMENT

    # В аудите содержимого комментария нет — ни при создании, ни в агрегате.
    events = db_session.scalars(select(AuditEvent)).all()
    assert events, "аудит не может быть пустым"
    for event in events:
        details = event.details or ""
        assert PII_COMMENT not in details
        assert PII_PHONE not in details
        assert PII_DIGITS not in details
        assert "Мария Петровна" not in details
    created = next(event for event in events if event.action == AuditAction.CANDIDATE_CREATED)
    assert "source=excel_import" in (created.details or "")


def test_matched_candidate_audit_keeps_structural_fields_but_not_comment(
    client: TestClient, db_session: Session
) -> None:
    hr = make_user(db_session, username="hr1", role=UserRole.HR)
    existing = make_candidate(db_session, owner=hr, full_name="Тестова Анна Ивановна")
    csrf = _login(client, "hr1")

    preview = _upload(client, _pii_comment_workbook(), csrf).json()
    row = next(item for item in preview["rows"] if item["full_name"] == "Тестова Анна Ивановна")
    response = _confirm(
        client,
        _pii_comment_workbook(),
        csrf,
        [{"row_index": row["row_index"], "action": "match", "candidate_id": str(existing.id)}],
    )
    assert response.status_code == 200, response.text
    assert response.json()["updated"] == 1

    # Комментарий обновлён в карточке, но не дословно в аудите.
    db_session.refresh(existing)
    assert existing.start_comment == PII_COMMENT
    event = db_session.scalar(
        select(AuditEvent).where(AuditEvent.action == AuditAction.CANDIDATE_START_SCHEDULE_CHANGED)
    )
    assert event is not None
    details = event.details or ""
    # Факт изменения комментария зафиксирован без содержимого.
    assert "start_comment" in details and "изменён" in details
    assert PII_COMMENT not in details and PII_PHONE not in details
    assert PII_DIGITS not in details and "Мария Петровна" not in details
    # Структурные поля по-прежнему в аудите полностью — это не свободный текст.
    assert "start_date: — -> 2026-08-10" in details
    assert "start_time: — -> 09:30" in details
    assert "start_organization: — -> ООО Пример" in details
