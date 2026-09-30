"""Импорт графика выхода из Excel — разбор файла (без БД и без сети).

Модуль превращает «рабочий» Excel-график выхода (образец: «График приемов»)
в плоский список строк, готовых к сопоставлению с кандидатами:

* заголовки таблицы распознаются по тексту («ФИО», «Дата и время», …), а не
  по номерам строк — файл с другой шапкой даёт понятную ошибку формата;
* дневные блоки (объединённая ячейка с датой, например «пн, 10 авг.»)
  задают дату всех строк блока; дата, явно указанная в тексте времени
  («20.08 К 14:00»), переопределяет дату блока с предупреждением;
* время понимается как значение времени Excel, дата-время, число
  (10 → 10:00, доля суток 0.5 → 12:00, серийный номер → дата) и текст
  («10.08 к 9:30», «К 9:00», «12.00», «8;00», интервал «13:00-14:00»);
  не распознанный текст не теряется — уходит в комментарий строки;
* служебные строки («Увольнение», «Отработка грузчик», «менеджер по
  персоналу…») отделяются от людей и предлагаются как записи графика
  (``schedule_entries`` из Phase 18);
* телефон и «уехавшие» вправо примечания собираются из колонок после
  комментариев; телефоны нормализуются для сопоставления;
* «1 смена»/«2 смена» извлекаются из комментария в поле смены.

Модуль ничего не пишет в БД и не логирует содержимое строк: он только
возвращает структуры данных. Сопоставление, права и запись — в роутере.
"""

from __future__ import annotations

import hashlib
import io
import re
import zipfile
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from typing import Literal, cast

from openpyxl import load_workbook
from openpyxl.worksheet.worksheet import Worksheet

from app.utils import normalize_full_name, normalize_phone

# --- Ограничения загрузки -----------------------------------------------------

#: Максимальный размер загружаемого .xlsx (график на год — десятки КБ).
MAX_IMPORT_BYTES = 5 * 1024 * 1024
#: Максимум строк после шапки — защита от «бесконечных» файлов.
MAX_IMPORT_ROWS = 5000
#: Сколько колонок справа от комментария просматривается на телефоны/примечания.
MAX_EXTRA_COLUMNS = 8
#: Шапка ищется в первых строках листа.
HEADER_SCAN_ROWS = 30

XLSX_MIME_TYPES = {
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "application/octet-stream",
    "application/zip",
    "application/x-zip-compressed",
    "binary/octet-stream",
    "",
}

#: Ограничения моделей (модели кандидатов/служебных строк).
MAX_FULL_NAME = 200
MAX_ORGANIZATION = 120
MAX_DEPARTMENT = 120
MAX_POSITION = 200
MAX_SHIFT = 32
MAX_COMMENT = 300

RowKind = Literal["candidate", "service", "skip"]
NameConfidence = Literal["full", "partial"]

# --- Регулярные выражения -----------------------------------------------------

# Интервал «13:00-14-00», «13.00 – 14.00» (встречается в служебных строках).
_TIME_RANGE_RE = re.compile(
    r"(\d{1,2})\s*[:.;,-]\s*(\d{2})\s*[-–—]\s*(\d{1,2})\s*[:.;,-]\s*(\d{2})"
)
# Время в тексте: «9:30», «12.00», «8;00», «К 9:00».
_TIME_RE = re.compile(r"(?<!\d)(\d{1,2})\s*[:.;]\s*(\d{2})(?!\d)")
# Дата в тексте: «10.08», «10.08.2026», «10.08.» (предшествует «к 9:30»).
_DATE_PREFIX_RE = re.compile(r"(\d{1,2})\s*\.\s*(\d{1,2})(?:\s*\.\s*(\d{2,4}))?\s*\.?")
# «10 августа 2026», «пн, 10 авг. 2026», «10 авг» — русские даты блоков.
_RU_DATE_RE = re.compile(r"(\d{1,2})\s+([а-яёА-ЯЁ]+)\.?(?:\s+(\d{4}))?")
# Текст без управляющих символов (защита от мусора в ячейках).
_CONTROL_CHARS_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
# Скобочные пометки в ФИО: «Дарманаев Анвар (перевод)» → пометка в комментарий.
_PAREN_NOTE_RE = re.compile(r"\(([^)]*)\)|（([^）]*)）")
# Смена в комментарии: «1 смена», «2 смена (подготовка)», «2 см», «(2 смена)».
_SHIFT_RE = re.compile(r"(?<!\d)(\d)\s*-?\s*смен\w*", re.IGNORECASE)

#: Ключевые слова служебных строк (без кандидата). Однословные сравниваются
#: по полному совпадению слова (фамилия «Переводнова» не должна срабатывать
#: на «перевод»), многословные — по вхождению подстроки.
SERVICE_KEYWORDS = (
    "увольнение",
    "отработка",
    "перевод",
    "медосмотр",
    "мед осмотр",
    "собеседование",
    "отпуск",
    "больничный",
    "прогул",
    "прием на работу",
)
#: Начало строки — должность/роль вместо человека (вакантный слот).
ROLE_PREFIXES = (
    "менеджер по",
    "менед по",
    "менеджер ",
    "директор",
    "дир ",
    "дир.",
    "бухгалтер",
    "секретар",
    "начальник",
    "заведующ",
    "руководитель ",
    "специалист",
)

#: Русские месяцы по первым трём буквам основы (+ вариант «мая»).
_RU_MONTHS = {
    "янв": 1,
    "фев": 2,
    "мар": 3,
    "апр": 4,
    "май": 5,
    "мая": 5,
    "июн": 6,
    "июл": 7,
    "авг": 8,
    "сен": 9,
    "окт": 10,
    "ноя": 11,
    "дек": 12,
}

# --- Исключения ----------------------------------------------------------------


class ScheduleImportFormatError(Exception):
    """Файл не похож на график выхода: неизвестный формат (422 в API)."""


# --- Структуры результата -------------------------------------------------------


@dataclass(frozen=True)
class ParsedScheduleRow:
    """Одна распознанная строка графика (кандидат, служебная или пустой слот)."""

    row_index: int  # порядковый номер среди распознанных строк (стабильный ключ)
    sheet_row: int  # номер строки в Excel (для отчёта)
    entry_date: date
    raw_name: str | None
    name_normalized: str
    time_from: time | None
    time_to: time | None
    time_display: str
    organization: str | None
    department: str | None
    position: str | None
    shift: str | None
    comment: str | None
    phone_display: str | None
    phone_normalized: str | None
    kind: RowKind
    name_confidence: NameConfidence | None
    warnings: tuple[str, ...] = ()
    parse_error: str | None = None


@dataclass
class ParsedScheduleSheet:
    """Результат разбора листа: строки + структурные проблемы."""

    sheet_title: str
    header_row: int
    rows: list[ParsedScheduleRow] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def days(self) -> list[date]:
        seen: list[date] = []
        for row in self.rows:
            if row.entry_date not in seen:
                seen.append(row.entry_date)
        return seen


# --- Валидация и чтение файла ----------------------------------------------------


def validate_xlsx_upload(payload: bytes, filename: str | None, content_type: str | None) -> None:
    """Отсечь всё, что не является обычным .xlsx, до разбора.

    Макросы (``.xlsm``), старый формат ``.xls`` и произвольные архивы не
    принимаются: содержимое ячеек никогда не исполняется, но сам факт
    отказа уменьшает поверхность атаки и исключает «сюрпризы» для Марии.
    """

    if not payload:
        raise ScheduleImportFormatError("Файл пуст. Выберите файл графика в формате .xlsx.")
    if len(payload) > MAX_IMPORT_BYTES:
        raise ScheduleImportFormatError(
            f"Файл больше {MAX_IMPORT_BYTES // (1024 * 1024)} МБ. "
            "Разбейте график на части или сократите файл."
        )
    name = (filename or "").strip().lower()
    if name.endswith((".xlsm", ".xlsb", ".xlam")):
        raise ScheduleImportFormatError(
            "Файлы с макросами (.xlsm/.xlsb) не принимаются. Сохраните график как .xlsx."
        )
    if name.endswith(".xls"):
        raise ScheduleImportFormatError(
            "Старый формат .xls не поддерживается. Сохраните файл как .xlsx и повторите."
        )
    if name and not name.endswith(".xlsx"):
        raise ScheduleImportFormatError("Нужен файл графика в формате .xlsx.")
    if payload[:4] != b"PK\x03\x04":
        raise ScheduleImportFormatError("Файл повреждён или не является документом Excel (.xlsx).")
    if content_type and content_type.split(";")[0].strip().lower() not in XLSX_MIME_TYPES:
        raise ScheduleImportFormatError(
            "Тип файла не похож на документ Excel (.xlsx). Сохраните файл заново как .xlsx."
        )
    try:
        with zipfile.ZipFile(io.BytesIO(payload)) as archive:
            names = archive.namelist()
    except zipfile.BadZipFile as exc:
        raise ScheduleImportFormatError("Файл повреждён: архив Excel не читается.") from exc
    if any("vbaProject" in item for item in names):
        raise ScheduleImportFormatError(
            "В файле найдены макросы. Сохраните график как .xlsx без макросов."
        )


def read_workbook(payload: bytes) -> list[Worksheet]:
    """Открыть книгу без вычисления формул и без исполнения содержимого."""

    try:
        workbook = load_workbook(io.BytesIO(payload), data_only=True, read_only=False)
    except Exception as exc:  # openpyxl кидает разные типы на битых файлах
        raise ScheduleImportFormatError(
            "Не удалось открыть файл как документ Excel. Сохраните его заново как .xlsx."
        ) from exc
    sheets = [cast(Worksheet, sheet) for sheet in workbook.worksheets]
    if not sheets:
        raise ScheduleImportFormatError("В файле нет ни одного листа.")
    return sheets


# --- Разбор дат и времени --------------------------------------------------------


def _clean_text(value: object) -> str:
    if value is None:
        return ""
    text = str(value)
    text = _CONTROL_CHARS_RE.sub("", text)
    return re.sub(r"\s+", " ", text).strip()


def _excel_serial_to_date(serial: float) -> date | None:
    """Серийная дата Excel (1 → 1900-01-01, база 1899-12-30) → date."""

    try:
        converted = date(1899, 12, 30) + timedelta(days=int(serial))
    except OverflowError:
        return None
    if date(2000, 1, 1) <= converted <= date(2100, 12, 31):
        return converted
    return None


def _make_time(hours: int, minutes: int) -> time | None:
    if 0 <= hours <= 23 and 0 <= minutes <= 59:
        return time(hours, minutes)
    return None


def parse_block_date(value: object, *, context_year: int | None) -> date | None:
    """Дата дневного блока: значение времени Excel, число, текст («10.08»,
    «10 августа 2026», «пн, 10 авг. 2026»). Не распознано — None."""

    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if value >= 59:
            return _excel_serial_to_date(float(value))
        return None
    text = _clean_text(value)
    if not text:
        return None
    match = re.search(r"(\d{1,2})[./](\d{1,2})(?:[./](\d{2,4}))?", text)
    if match:
        day, month = int(match.group(1)), int(match.group(2))
        year_raw = match.group(3)
        year = _resolve_year(year_raw, context_year)
        candidate_date = _safe_date(day, month, year)
        if candidate_date is not None:
            return candidate_date
    ru = _RU_DATE_RE.search(text)
    if ru:
        ru_month = _RU_MONTHS.get(ru.group(2).lower()[:3])
        if ru_month is not None:
            year = _resolve_year(ru.group(3), context_year)
            candidate_date = _safe_date(int(ru.group(1)), ru_month, year)
            if candidate_date is not None:
                return candidate_date
    return None


def _resolve_year(year_raw: str | None, context_year: int | None) -> int:
    if year_raw:
        parsed = int(year_raw)
        return 2000 + parsed if parsed < 100 else parsed
    return context_year or date.today().year


def _safe_date(day: int, month: int, year: int) -> date | None:
    try:
        return date(year, month, day)
    except ValueError:
        return None


@dataclass(frozen=True)
class TimeParse:
    time_from: time | None
    time_to: time | None
    display: str
    date_override: date | None
    comment_addition: str | None
    warnings: tuple[str, ...]


def parse_time_value(
    value: object, *, block_date: date | None, context_year: int | None
) -> TimeParse:
    """Время строки графика во всех формах, встречающихся в реальных файлах.

    Не распознанный текст возвращается в ``comment_addition`` — строка не
    теряет информацию, а предупреждение попадает в отчёт.
    """

    if value is None or _clean_text(value) == "":
        return TimeParse(None, None, "", None, None, ())

    if isinstance(value, datetime):
        if value.hour or value.minute:
            clock = value.time().replace(second=0, microsecond=0)
            return TimeParse(clock, None, clock.strftime("%H:%M"), None, None, ())
        # Дата в колонке времени: дубль блока или перенос на другую дату.
        cell_date = value.date()
        if block_date is not None and cell_date != block_date:
            return TimeParse(
                None,
                None,
                "",
                cell_date,
                None,
                (f"дата из ячейки времени: {cell_date.strftime('%d.%m.%Y')}",),
            )
        return TimeParse(None, None, "", None, None, ())

    if isinstance(value, time):
        clock = value.replace(second=0, microsecond=0)
        return TimeParse(clock, None, clock.strftime("%H:%M"), None, None, ())

    if isinstance(value, (int, float)) and not isinstance(value, bool):
        number = float(value)
        if 0 < number < 1:
            seconds = round(number * 86400)
            clock = time((seconds // 3600) % 24, (seconds % 3600) // 60)
            return TimeParse(clock, None, clock.strftime("%H:%M"), None, None, ())
        if 1 <= number < 24:
            hours = int(number)
            minutes = round((number - hours) * 60) % 60
            hour_clock = _make_time(hours, minutes)
            if hour_clock is not None:
                return TimeParse(hour_clock, None, hour_clock.strftime("%H:%M"), None, None, ())
        serial_date = _excel_serial_to_date(number)
        if serial_date is not None and (block_date is None or serial_date != block_date):
            return TimeParse(
                None,
                None,
                "",
                serial_date,
                None,
                (f"дата из числовой ячейки времени: {serial_date.strftime('%d.%m.%Y')}",),
            )
        raw = _clean_text(value)
        return TimeParse(None, None, "", None, raw, (f"не удалось разобрать время: «{raw}»",))

    return _parse_time_text(_clean_text(value), block_date=block_date, context_year=context_year)


def _parse_time_text(text: str, *, block_date: date | None, context_year: int | None) -> TimeParse:
    """Текстовое время: «10.08 к 9:30», «К 9:00», «12.00», «после мед осмотра»."""

    remainder = text
    date_override: date | None = None
    warnings: list[str] = []

    interval = _TIME_RANGE_RE.search(remainder)
    if interval:
        start = _make_time(int(interval.group(1)), int(interval.group(2)))
        end = _make_time(int(interval.group(3)), int(interval.group(4)))
        if start is not None and end is not None and end >= start:
            display = f"{start.strftime('%H:%M')}–{end.strftime('%H:%M')}"
            return TimeParse(start, end, display, None, None, ())

    date_match = _DATE_PREFIX_RE.search(remainder)
    if date_match:
        day, month = int(date_match.group(1)), int(date_match.group(2))
        if 1 <= month <= 12 and 1 <= day <= 31:
            year = _resolve_year(date_match.group(3), context_year)
            found = _safe_date(day, month, year)
            rest = _DATE_PREFIX_RE.sub("", remainder, count=1).strip(" \tкК.,")
            has_more_time = _TIME_RE.search(rest) is not None
            if found is not None and has_more_time:
                remainder = rest
                if block_date is None or found != block_date:
                    date_override = found
                    warnings.append(f"дата из текста: {found.strftime('%d.%m.%Y')}")
            elif found is not None and not has_more_time:
                # Одинокая пара «а.б» в колонке времени почти всегда время
                # («12.00», «13.01»): датой её считаем только если она не
                # может быть временем (час > 23 или минуты > 59).
                if day > 23 or month > 59:
                    if block_date is None or found != block_date:
                        date_override = found
                        warnings.append(f"дата из текста: {found.strftime('%d.%m.%Y')}")
                    return TimeParse(None, None, "", date_override, None, tuple(warnings))
                # Иначе даём _TIME_RE распознать её как время ниже.

    clock_match = _TIME_RE.search(remainder)
    if clock_match:
        clock = _make_time(int(clock_match.group(1)), int(clock_match.group(2)))
        if clock is not None:
            return TimeParse(
                clock, None, clock.strftime("%H:%M"), date_override, None, tuple(warnings)
            )

    # «12.00» без двоеточия уже пойман _TIME_RE; сюда попадают «после мед
    # осмотра» и похожие заметки — их не теряем, переносим в комментарий.
    if date_override is not None:
        return TimeParse(None, None, "", date_override, None, tuple(warnings))
    return TimeParse(None, None, "", None, text, (f"не удалось разобрать время: «{text}»",))


# --- Классификация строк ----------------------------------------------------------


def classify_name(raw_name: str) -> tuple[RowKind, NameConfidence, str | None]:
    """Кто в строке: человек, служебная запись или неразборчиво.

    Возвращает (тип, уверенность в ФИО, текст пометки в скобках). Скобочные
    пометки («(перевод)») вырезаются из имени и подсказываются как часть
    комментария — так «Дарманаев Анвар (перевод)» остаётся кандидатом.
    """

    notes: list[str] = []

    def _grab(match: re.Match[str]) -> str:
        notes.append((match.group(1) or match.group(2) or "").strip())
        return " "

    without_notes = _PAREN_NOTE_RE.sub(_grab, raw_name)
    without_notes = re.sub(r"\s+", " ", without_notes).strip()
    folded = without_notes.casefold()

    if not folded:
        return ("service", "partial", "; ".join(notes) or None)

    # Ключевые слова служебных строк ищем по словам целиком: иначе фамилия
    # «Переводнова» ложно срабатывала бы на «перевод».
    words_folded = re.split(r"[\s.,;:!?\-–—()]+", folded)
    for keyword in SERVICE_KEYWORDS:
        if " " in keyword:
            if keyword in folded:
                return ("service", "partial", "; ".join(notes) or None)
        elif keyword in words_folded:
            return ("service", "partial", "; ".join(notes) or None)
    for prefix in ROLE_PREFIXES:
        if folded.startswith(prefix):
            return ("service", "partial", "; ".join(notes) or None)

    words = [word for word in without_notes.split() if word]
    letter_words = [
        word for word in words if re.match(r"^[^\W\d_][^\W\d_.\-']*$", word, re.UNICODE)
    ]
    # Мусор вместо ФИО («1.0», «12», «№5»): ни одного слова из букв — строка
    # не импортируется ни как человек, ни как служебная запись.
    meaningful = [word for word in letter_words if len(word) >= 2]
    if not meaningful:
        return ("skip", "partial", "; ".join(notes) or None)
    capitalized = all(word[0].isupper() for word in letter_words) if letter_words else False
    if 2 <= len(letter_words) <= 4 and capitalized and letter_words == words:
        return ("candidate", "full", "; ".join(notes) or None)
    return ("candidate", "partial", "; ".join(notes) or None)


def extract_shift(comment: str | None) -> tuple[str | None, str | None]:
    """«1 смена»/«2 смена (подготовка)» из комментария → поле смены.

    Остаток комментария сохраняется; если комментарий состоял только из
    смены — он очищается. Слишком длинные «смены» не выносятся.
    """

    if not comment:
        return (None, comment)
    match = _SHIFT_RE.search(comment)
    if match is None:
        return (None, comment or None)
    shift_text = f"{match.group(1)} смена"
    rest = (comment[: match.start()] + comment[match.end() :]).strip(" \t;,.—-")
    rest = re.sub(r"\s+", " ", rest).strip()
    return (shift_text[:MAX_SHIFT], rest or None)


# --- Поиск телефонов и «уехавших» вправо значений ----------------------------------


def _digits_only(value: object) -> str:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        number = float(value)
        if number.is_integer():
            return str(int(number))
        return ""
    if isinstance(value, str):
        text = value.strip()
        try:
            number = float(text)
        except ValueError:
            return "".join(ch for ch in text if ch.isdigit())
        # «89281223412.0» в текстовой ячейке — тот же телефон, что числом.
        if number.is_integer() and abs(number) >= 1e9:
            return str(int(number))
        return "".join(ch for ch in text if ch.isdigit())
    return ""


def _looks_like_phone(value: object) -> str | None:
    """10–11 значащих цифр (возможно с «8»/«+7») — похоже на телефон."""

    digits = _digits_only(value)
    if len(digits) in (10, 11):
        return digits
    return None


def format_phone_display(digits: str) -> str:
    """79518329029 → «+7 951 832-90-29» (10 цифр дополняются до +7)."""

    if len(digits) == 10:
        digits = "7" + digits
    elif len(digits) == 11 and digits[0] == "8":
        digits = "7" + digits[1:]
    if len(digits) == 11 and digits[0] == "7":
        return f"+7 {digits[1:4]} {digits[4:7]}-{digits[7:9]}-{digits[9:11]}"
    return "+" + digits


def mask_phone_display(display: str | None) -> str | None:
    """Маскировка для превью: «+7 ••• •••-90-29» — сверить можно, копировать нельзя."""

    if not display:
        return None
    digits = "".join(ch for ch in display if ch.isdigit())
    if len(digits) < 4:
        return "•" * len(display)
    tail = digits[-2:]
    return f"+{digits[0]} ••• •••-••-{tail}"


def _clip(value: str | None, limit: int, warnings: list[str], label: str) -> str | None:
    if value is None:
        return None
    cleaned = value.strip()
    if not cleaned:
        return None
    if len(cleaned) > limit:
        warnings.append(f"{label}: значение длиннее {limit} символов — обрезано")
        return cleaned[:limit]
    return cleaned


# --- Разбор листа -------------------------------------------------------------------


@dataclass(frozen=True)
class _HeaderMap:
    header_row: int
    name_col: int
    time_col: int
    organization_col: int | None
    department_col: int | None
    position_col: int | None
    comment_col: int | None


def _find_header(sheet: Worksheet) -> _HeaderMap | None:
    """Строка заголовков по тексту («ФИО» обязательна, «время» обязательно)."""

    max_row = min(sheet.max_row or 1, HEADER_SCAN_ROWS)
    for row in sheet.iter_rows(min_row=1, max_row=max_row):
        texts: dict[int, str] = {}
        for cell in row:
            cleaned = _clean_text(cell.value)
            if cleaned and cell.column is not None:
                texts[cell.column] = cleaned.casefold()
        name_col = None
        time_col = None
        organization_col = department_col = position_col = comment_col = None
        for column, text in texts.items():
            if name_col is None and "фио" in text:
                name_col = column
            elif time_col is None and "время" in text:
                time_col = column
            elif organization_col is None and "организаци" in text:
                organization_col = column
            elif department_col is None and "отдел" in text:
                department_col = column
            elif position_col is None and "должност" in text:
                position_col = column
            elif comment_col is None and "коммент" in text:
                comment_col = column
        row_number = row[0].row if row else None
        if name_col is not None and time_col is not None and row_number is not None:
            return _HeaderMap(
                header_row=row_number,
                name_col=name_col,
                time_col=time_col,
                organization_col=organization_col,
                department_col=department_col,
                position_col=position_col,
                comment_col=comment_col,
            )
    return None


def _cell(sheet: Worksheet, row_index: int, column: int | None) -> object:
    if column is None:
        return None
    return sheet.cell(row=row_index, column=column).value


def _row_is_empty(
    sheet: Worksheet, row_index: int, header: _HeaderMap, *, exclude_col: int | None = None
) -> bool:
    """Пусты ли отображаемые колонки строки (колонку-источник даты исключаем)."""

    columns = [
        header.name_col,
        header.time_col,
        header.organization_col,
        header.department_col,
        header.position_col,
        header.comment_col,
    ]
    for column in columns:
        if column is None or column == exclude_col:
            continue
        if _clean_text(sheet.cell(row=row_index, column=column).value):
            return False
    return True


def parse_sheet(sheet: Worksheet) -> ParsedScheduleSheet | None:
    """Разобрать один лист. Лист без шапки возвращает None (ищется следующий)."""

    header = _find_header(sheet)
    if header is None:
        return None

    result = ParsedScheduleSheet(sheet_title=sheet.title, header_row=header.header_row)
    current_date: date | None = None
    max_row = sheet.max_row or header.header_row
    parsed = 0

    for sheet_row in range(header.header_row + 1, max_row + 1):
        if parsed >= MAX_IMPORT_ROWS:
            result.warnings.append(
                f"Обработаны первые {MAX_IMPORT_ROWS} строк — файл обрезан по лимиту."
            )
            break
        name_value = sheet.cell(row=sheet_row, column=header.name_col).value

        # Дневной блок: дата в колонке ФИО (объединённая ячейка) и пустые
        # остальные колонки. Дата в колонке времени без ФИО — тоже блок.
        if not _clean_text(name_value):
            time_value = _cell(sheet, sheet_row, header.time_col)
            block_date = parse_block_date(
                time_value,
                context_year=current_date.year if current_date else None,
            )
            if block_date is not None and _row_is_empty(
                sheet, sheet_row, header, exclude_col=header.time_col
            ):
                current_date = block_date
                continue
            if _row_is_empty(sheet, sheet_row, header):
                continue
            parsed_row = _parse_data_row(
                sheet, sheet_row, header, current_date, result, name_value=None
            )
            if parsed_row is not None:
                result.rows.append(parsed_row)
                parsed += 1
            continue

        block_date = parse_block_date(
            name_value, context_year=current_date.year if current_date else None
        )
        if block_date is not None and _row_is_empty(
            sheet, sheet_row, header, exclude_col=header.name_col
        ):
            current_date = block_date
            continue

        parsed_row = _parse_data_row(
            sheet, sheet_row, header, current_date, result, name_value=name_value
        )
        if parsed_row is not None:
            result.rows.append(parsed_row)
            parsed += 1

    return result


def _parse_data_row(
    sheet: Worksheet,
    sheet_row: int,
    header: _HeaderMap,
    current_date: date | None,
    result: ParsedScheduleSheet,
    *,
    name_value: object,
) -> ParsedScheduleRow | None:
    """Одна строка с данными (или пустой слот с временем)."""

    warnings: list[str] = []
    row_index = len(result.rows) + 1

    raw_name = _clean_text(name_value)
    organization = _clean_text(_cell(sheet, sheet_row, header.organization_col)) or None
    department = _clean_text(_cell(sheet, sheet_row, header.department_col)) or None
    position = _clean_text(_cell(sheet, sheet_row, header.position_col)) or None
    comment = _clean_text(_cell(sheet, sheet_row, header.comment_col)) or None
    time_value = _cell(sheet, sheet_row, header.time_col)

    # Пустой слот: нет ФИО (только время или реквизиты) — не импортируется.
    if not raw_name:
        if time_value is None and organization is None and department is None and position is None:
            return None
        if current_date is None:
            return ParsedScheduleRow(
                row_index=row_index,
                sheet_row=sheet_row,
                entry_date=date.today(),
                raw_name=None,
                name_normalized="",
                time_from=None,
                time_to=None,
                time_display="",
                organization=None,
                department=None,
                position=None,
                shift=None,
                comment=None,
                phone_display=None,
                phone_normalized=None,
                kind="skip",
                name_confidence=None,
                warnings=("нет ФИО",),
                parse_error="Строка до первой даты-блока и без ФИО.",
            )
        return ParsedScheduleRow(
            row_index=row_index,
            sheet_row=sheet_row,
            entry_date=current_date,
            raw_name=None,
            name_normalized="",
            time_from=None,
            time_to=None,
            time_display="",
            organization=None,
            department=None,
            position=None,
            shift=None,
            comment=None,
            phone_display=None,
            phone_normalized=None,
            kind="skip",
            name_confidence=None,
            warnings=("пустой слот без ФИО — пропускается",),
        )

    context_year = current_date.year if current_date else None
    parsed_time = parse_time_value(time_value, block_date=current_date, context_year=context_year)
    warnings.extend(parsed_time.warnings)

    entry_date = parsed_time.date_override or current_date
    if entry_date is None:
        return ParsedScheduleRow(
            row_index=row_index,
            sheet_row=sheet_row,
            entry_date=date.today(),
            raw_name=raw_name,
            name_normalized=normalize_full_name(raw_name),
            time_from=parsed_time.time_from,
            time_to=parsed_time.time_to,
            time_display=parsed_time.display,
            organization=_clip(organization, MAX_ORGANIZATION, warnings, "организация"),
            department=_clip(department, MAX_DEPARTMENT, warnings, "отдел"),
            position=_clip(position, MAX_POSITION, warnings, "должность"),
            shift=None,
            comment=_clip(comment, MAX_COMMENT, warnings, "комментарий"),
            phone_display=None,
            phone_normalized=None,
            kind="skip",
            name_confidence="partial",
            warnings=tuple(warnings),
            parse_error="Строка находится до первой строки с датой — некуда привязать.",
        )

    # Телефон и «уехавшие» вправо примечания: колонки после комментариев
    # (в реальных файлах телефон и заметки стоят где попало — до 8 колонок).
    extra_texts: list[str] = []
    phone_digits: str | None = None
    start_col = (header.comment_col or header.position_col or header.time_col) + 1
    # Колонка комментария иногда сама содержит телефон (значением целиком):
    # проверяем сырую ячейку, чтобы «89281223412.0» не стал текстом с точкой.
    comment_raw = _cell(sheet, sheet_row, header.comment_col)
    comment_phone = _looks_like_phone(comment_raw)
    if comment_phone is not None:
        phone_digits = comment_phone
        comment = None
    for column in range(start_col, start_col + MAX_EXTRA_COLUMNS):
        value = sheet.cell(row=sheet_row, column=column).value
        if value is None or _clean_text(value) == "":
            continue
        digits = _looks_like_phone(value)
        if digits is not None and phone_digits is None:
            phone_digits = digits
            continue
        extra_texts.append(_clean_text(value))

    # Скобочные пометки ФИО («(перевод)») и не распознанный текст времени
    # присоединяются к комментарию — данные не теряются.
    kind, confidence, paren_note = classify_name(raw_name)
    display_name = _PAREN_NOTE_RE.sub(" ", raw_name)
    display_name = re.sub(r"\s+", " ", display_name).strip()
    if kind == "skip":
        warnings.append("значение в колонке ФИО не похоже на имя — строка пропускается")

    comment_parts: list[str] = []
    for part in (comment, *extra_texts, paren_note, parsed_time.comment_addition):
        cleaned_part = (part or "").strip()
        # Повторы не дублируем («после мед осмотра» бывает и в комментарии,
        # и в не распознанном времени).
        if cleaned_part and cleaned_part.casefold() not in {p.casefold() for p in comment_parts}:
            comment_parts.append(cleaned_part)
    full_comment = "; ".join(comment_parts) or None

    shift, full_comment = extract_shift(full_comment)

    phone_display = format_phone_display(phone_digits) if phone_digits else None

    return ParsedScheduleRow(
        row_index=row_index,
        sheet_row=sheet_row,
        entry_date=entry_date,
        raw_name=display_name[:MAX_FULL_NAME],
        name_normalized=normalize_full_name(display_name),
        time_from=parsed_time.time_from,
        time_to=parsed_time.time_to,
        time_display=parsed_time.display,
        organization=_clip(organization, MAX_ORGANIZATION, warnings, "организация"),
        department=_clip(department, MAX_DEPARTMENT, warnings, "отдел"),
        position=_clip(position, MAX_POSITION, warnings, "должность"),
        shift=shift,
        comment=_clip(full_comment, MAX_COMMENT, warnings, "комментарий"),
        phone_display=phone_display,
        phone_normalized=normalize_phone(phone_display) if phone_display else None,
        kind=kind,
        name_confidence=confidence,
        warnings=tuple(warnings),
    )


def parse_schedule_workbook(payload: bytes) -> ParsedScheduleSheet:
    """Разобрать загруженный файл: первый лист с распознаваемой шапкой."""

    validate_xlsx_upload(payload, filename="upload.xlsx", content_type=None)
    sheets = read_workbook(payload)
    for sheet in sheets[:5]:
        parsed = parse_sheet(sheet)
        if parsed is None:
            continue
        if not parsed.rows:
            raise ScheduleImportFormatError(
                f"Лист «{parsed.sheet_title}»: заголовки найдены, но строк с данными нет."
            )
        return parsed
    raise ScheduleImportFormatError(
        "Не найдена строка заголовков («ФИО», «Дата и время»). "
        "Проверьте, что загружаете график выхода, сохранённый как .xlsx."
    )


# --- Ключ идемпотентности -----------------------------------------------------------


# --- Сопоставление с кандидатами ---------------------------------------------------


@dataclass(frozen=True)
class LookupCandidate:
    """Минимальный портрет кандидата для сопоставления (без ПДн в логах)."""

    id: object  # UUID
    display_name: str
    full_name_normalized: str
    stage: str
    phone_normalized: str | None


@dataclass(frozen=True)
class RowMatch:
    """Результат сопоставления одной строки с картотекой."""

    match: dict | None  # ImportMatchInfo-совместимый словарь
    options: tuple[dict, ...] = ()


def _match_info(candidate: LookupCandidate, *, reason: str, confident: bool) -> dict:
    return {
        "candidate_id": candidate.id,
        "full_name": candidate.display_name,
        "stage": candidate.stage,
        "reason": reason,
        "confident": confident,
    }


def _partial_name_match(row_normalized: str, candidate_normalized: str) -> bool:
    """«Никитин» ≈ «Никитин Роман Александрович», «Коршунов Олег» ≈ полное.

    Каждое слово строки обязано быть префиксом соответствующего слова
    кандидата (инициал с точкой — тоже префикс из одной буквы). Строка не
    длиннее имени кандидата; полное равенство сюда не попадает (это точное
    совпадение, оно обрабатывается отдельно).
    """

    row_words = row_normalized.split()
    candidate_words = candidate_normalized.split()
    if not row_words or not candidate_words:
        return False
    if len(row_words) > len(candidate_words):
        return False
    if len(row_words) == len(candidate_words) and row_normalized == candidate_normalized:
        return False
    # Длина строки не превышает длину имени кандидата (проверено выше),
    # поэтому пары слов выровнены по началу имени.
    for row_word, candidate_word in zip(row_words, candidate_words, strict=False):
        stem = row_word.rstrip(".")
        if not stem or not candidate_word.startswith(stem):
            return False
    return True


def build_row_matches(
    rows: list[ParsedScheduleRow],
    candidates: list[LookupCandidate],
) -> dict[int, RowMatch]:
    """Сопоставить строки с доступными кандидатами.

    Порядок доверия: телефон (надёжный признак) → точное ФИО (единственный
    кандидат) → точное ФИО (несколько — пусть выбирает человек) → префикс/
    инициалы. Совпадение по одному ФИО «надёжным» не помечается: однофамильцы
    существуют, окончательное слово — за пользователем.
    """

    by_name: dict[str, list[LookupCandidate]] = {}
    by_phone: dict[str, list[LookupCandidate]] = {}
    for candidate in candidates:
        by_name.setdefault(candidate.full_name_normalized, []).append(candidate)
        if candidate.phone_normalized:
            by_phone.setdefault(candidate.phone_normalized, []).append(candidate)

    result: dict[int, RowMatch] = {}
    for row in rows:
        if row.kind != "candidate" or not row.name_normalized:
            result[row.row_index] = RowMatch(match=None)
            continue

        if row.phone_normalized:
            phone_hits = by_phone.get(row.phone_normalized, [])
            if len(phone_hits) == 1:
                result[row.row_index] = RowMatch(
                    match=_match_info(phone_hits[0], reason="phone", confident=True)
                )
                continue

        name_hits = by_name.get(row.name_normalized, [])
        if len(name_hits) == 1:
            result[row.row_index] = RowMatch(
                match=_match_info(name_hits[0], reason="exact_name", confident=False)
            )
            continue
        if len(name_hits) > 1:
            result[row.row_index] = RowMatch(
                match=None,
                options=tuple(
                    _match_info(item, reason="exact_name", confident=False)
                    for item in name_hits[:5]
                ),
            )
            continue

        # Префикс/инициалы: быстро отбрасываем несовместимые первые слова.
        row_first = row.name_normalized.split()[0].rstrip(".")
        partial_hits: list[LookupCandidate] = []
        for candidate in candidates:
            candidate_first = candidate.full_name_normalized.split()[0]
            if not candidate_first.startswith(row_first):
                continue
            if _partial_name_match(row.name_normalized, candidate.full_name_normalized):
                partial_hits.append(candidate)
                if len(partial_hits) > 5:
                    break
        if len(partial_hits) == 1:
            result[row.row_index] = RowMatch(
                match=_match_info(partial_hits[0], reason="partial", confident=False)
            )
            continue
        if len(partial_hits) > 1:
            result[row.row_index] = RowMatch(
                match=None,
                options=tuple(
                    _match_info(item, reason="partial", confident=False)
                    for item in partial_hits[:5]
                ),
            )
            continue
        result[row.row_index] = RowMatch(match=None)
    return result


def make_row_key(row: ParsedScheduleRow, *, kind_override: str | None = None) -> str:
    """Устойчивый ключ строки: повторный импорт того же файла не плодит дубли.

    Ключ считается только от содержания строки (дата, нормализованное имя,
    время, тип) — не от номера строки в файле, поэтому он одинаков при
    повторной загрузке того же графика и при пересчёте на подтверждении.
    """

    kind = kind_override or ("service" if row.kind == "service" else "candidate")
    basis = "|".join(
        (
            row.entry_date.isoformat(),
            kind,
            row.name_normalized,
            row.time_from.isoformat() if row.time_from else "",
            row.time_to.isoformat() if row.time_to else "",
        )
    )
    return hashlib.sha256(basis.encode("utf-8")).hexdigest()
