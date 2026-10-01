"""Защищённые вложения в карточке кандидата: анкеты (``.docx``) и сканы (``.pdf``).

Это отдельный от Phase 16 контур: шаблоны и сгенерированные документы остаются
текстовыми, а здесь хранится **полученный от HR файл**. Граница сознательно
расширена — и поэтому закрытый список форматов, сигнатуры и лимиты проверяются
на сервере, а не во frontend.

Что проверяется при загрузке (доверять имени файла и ``Content-Type`` нельзя):

* **расширение** — только ``.docx`` и ``.pdf`` (``SUPPORTED_EXTENSIONS``);
* **сигнатура** — ``.docx`` обязан быть ZIP-контейнером OOXML с
  ``word/document.xml``, ``.pdf`` обязан начинаться с ``%PDF-``; несоответствие
  расширения содержимому — отказ 415;
* **опасные контейнеры** — OLE2 (старые ``.doc``/``.xls`` с макросами), архивы,
  HTML/скрипты и исполняемые форматы отклоняются по сигнатуре и по опасному
  ``Content-Type``;
* **DOCX как ZIP** — контейнер только *осматривается*: имена записей из
  центрального каталога проверяются на абсолютные пути и ``..`` (Zip Slip),
  число записей и объявленные размеры ограничены (zip-bomb), макросные части
  (``vbaProject.bin``, macroEnabled-тип содержимого) запрещены. Распаковка не
  выполняется вообще, поэтому раздувать нечего;
* **PDF** — не выполняются и не принимаются действия, способные что-то
  запустить: ``/JavaScript``, ``/JS``, ``/Launch``, ``/EmbeddedFile``,
  ``/RichMedia``, ``/XFA`` отклоняются;
* **размер** — читается потоково с жёстким лимитом: превышение обрывает чтение,
  а не буферизует файл целиком.

Имя файла очищается до безопасного отображаемого имени и никогда не
используется как путь; байты хранятся в PostgreSQL (BYTEA) и отдаются только
через аутентифицированный endpoint с ``Content-Disposition``. Внутренний способ
хранения наружу не отдаётся.
"""

from __future__ import annotations

import hashlib
import io
import posixpath
import re
import tempfile
import unicodedata
import zipfile
import zlib
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import datetime

from fastapi import UploadFile
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.audit import record_event
from app.config import Settings
from app.documents import advisory, has_grant
from app.models import (
    AccessGrantScope,
    AuditAction,
    Candidate,
    CandidateAttachment,
    User,
    UserRole,
)
from app.utils import ensure_aware, utc_now

# --- Форматы -----------------------------------------------------------------

#: Закрытый список разрешённых форматов. Расширение берётся из имени, но
#: принимается только после совпадения с сигнатурой содержимого.
KIND_BY_EXTENSION: dict[str, str] = {".docx": "docx", ".pdf": "pdf"}
SUPPORTED_EXTENSIONS: tuple[str, ...] = tuple(KIND_BY_EXTENSION)
SUPPORTED_KINDS: tuple[str, ...] = ("docx", "pdf")
SUPPORTED_HINT = "Допустимые форматы: " + ", ".join(SUPPORTED_EXTENSIONS) + "."

#: MIME, который сервер сам ставит при скачивании (никогда не берётся из
#: запроса клиента).
MIME_BY_KIND: dict[str, str] = {
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "pdf": "application/pdf",
}

#: ``Content-Type``, который сам по себе говорит об опасном содержимом. Это не
#: замена проверке сигнатуры, а быстрый и понятный отказ.
DANGEROUS_CONTENT_TYPES: frozenset[str] = frozenset(
    {
        "text/html",
        "application/xhtml+xml",
        "text/javascript",
        "application/javascript",
        "application/x-javascript",
        "image/svg+xml",
        "application/hta",
        "application/x-sh",
        "application/x-bat",
        "application/x-msdownload",
        "application/x-msdos-program",
        "application/x-executable",
        "application/x-dosexec",
        "application/vnd.microsoft.portable-executable",
        "application/x-httpd-php",
        "application/x-php",
    }
)

# --- Сигнатуры ---------------------------------------------------------------

ZIP_MAGICS: tuple[bytes, ...] = (b"PK\x03\x04", b"PK\x05\x06", b"PK\x07\x08")
#: OLE2 (Compound File Binary) — контейнер старых ``.doc``/``.xls`` с макросами.
OLE2_MAGIC = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"
#: Сигнатуры архивов: анкета не может быть архивом, распаковывать их нельзя.
ARCHIVE_MAGICS: tuple[bytes, ...] = (
    b"Rar!",
    b"7z\xbc\xaf\x27\x1c",
    b"\x1f\x8b",
    b"BZh",
    b"\x50\x4b\x03\x04",  # совпадает с ZIP: обрабатывается веткой DOCX
)
PDF_MAGIC = b"%PDF-"
#: По спецификации заголовок PDF обязан быть в первых 1024 байтах.
PDF_HEADER_SCAN_BYTES = 1024

#: Действия PDF, способные выполнить код или запустить внешнюю программу.
#: ``/OpenAction`` и ``/AA`` сами по себе безобидны (навигация по страницам), но
#: указывать они могут только на уже запрещённые конструкции — и это рассуждение
#: верно ровно настолько, насколько надёжно находятся сами конструкции, поэтому
#: ниже они ищутся с учётом нормативных способов записи имени (см.
#: ``_forbidden_name_pattern``).
PDF_FORBIDDEN_MARKERS: tuple[bytes, ...] = (
    b"/JavaScript",
    b"/JS",
    b"/Launch",
    b"/EmbeddedFile",
    b"/RichMedia",
    b"/XFA",
)

#: White-space по PDF 32000-1:2008 (табл. 1): NUL, HT, LF, FF, CR, SP.
_PDF_WHITESPACE = rb"[\x00\t\n\x0c\r ]*"

#: Предел распаковки одного потока и всех потоков файла суммарно: PDF-«бомба»
#: не должна стоить ни памяти, ни бесконечного цикла.
PDF_STREAM_MAX_BYTES = 32 * 1024 * 1024
PDF_STREAM_MAX_TOTAL_BYTES = 128 * 1024 * 1024
PDF_STREAM_MAX_COUNT = 512


def _hex_escape(byte: int) -> bytes:
    """Регэксп для ``#xx``-эскейпа конкретного байта (цифры hex в любом регистре).

    PDF 32000-1:2008 §7.3.5: ``#`` внутри имени — escape-последовательность из
    двух шестнадцатеричных цифр. Любой conforming-ридер декодирует её, то есть
    ``/J#61v#61Script`` — это нормативная запись имени ``/JavaScript``, а не
    причуда конкретного парсера.
    """
    digits = []
    for char in f"{byte:02x}":
        digits.append(f"[{char}{char.upper()}]" if char.isalpha() else char)
    return ("#" + "".join(digits)).encode()


def _forbidden_name_regex(marker: bytes) -> bytes:
    """Шаблон имени: символы могут быть разделены пробелами и записаны ``#xx``.

    Внутри name-токена PDF допускает white-space, поэтому ``/Java Script`` и
    ``/Java\nScript`` часть ридеров читает как ``/JavaScript``. Шаблон ищет
    последовательность символов запрещённого имени, допуская между ними только
    white-space и ``#xx``-эскейпы.

    Честная граница точности: шаблон **не привязан к границе name-токена**, это
    гибкий поиск подстроки. Последовательность вида ``/Java Script`` (и даже
    ``a/Java Script``) внутри текстового литерала документа тоже даст 415.
    Практически такой текст в HR-документе невероятен, а до ужесточения фильтра
    точное ``/JavaScript`` отклонялось так же, поэтому выбран fail-closed:
    привязка к границе токена потребовала бы не допускать перед ``/`` символ
    имени, а в ``<</S/JavaScript>>`` перед именем стоит как раз ``S``.
    Поведение зафиксировано тестом
    ``test_forbidden_name_inside_a_text_literal_is_still_rejected``.
    """
    parts = []
    for byte in marker:
        literal = re.escape(bytes([byte]))
        parts.append(rb"(?:" + literal + rb"|" + _hex_escape(byte) + rb")" + _PDF_WHITESPACE)
    return b"".join(parts)


#: Один объединённый шаблон: движок заякоривается на ``/`` и дальше проверяет
#: альтернативы, поэтому по большому файлу это один линейный проход.
_FORBIDDEN_NAME_PATTERN = re.compile(
    b"|".join(_forbidden_name_regex(marker) for marker in PDF_FORBIDDEN_MARKERS)
)

_STREAM_KEYWORD = re.compile(rb"stream(?:\r\n|\n|\r)")

# --- Ограничения DOCX-контейнера ---------------------------------------------

DOCX_MAX_ENTRIES = 2048
DOCX_MAX_ENTRY_BYTES = 32 * 1024 * 1024
DOCX_MAX_TOTAL_BYTES = 128 * 1024 * 1024
DOCX_DOCUMENT_PART = "word/document.xml"
DOCX_CONTENT_TYPES_PART = "[Content_Types].xml"
#: Единственная часть контейнера, которую мы читаем; её размер ограничен.
DOCX_CONTENT_TYPES_READ_BYTES = 256 * 1024
DOCX_MACRO_PART_MARKERS: tuple[str, ...] = ("vbaproject.bin",)
DOCX_MACRO_CONTENT_TYPE = "macroenabled"

# --- Имя файла ---------------------------------------------------------------

FILENAME_MAX_LENGTH = 120
#: Служебные имена Windows, которые нельзя использовать как имя файла.
_RESERVED_WINDOWS_NAMES = frozenset(
    {
        "CON",
        "PRN",
        "AUX",
        "NUL",
        *(f"COM{i}" for i in range(1, 10)),
        *(f"LPT{i}" for i in range(1, 10)),
    }
)
_UNSAFE_NAME_RE = re.compile(r'[\\/:*?"<>|\x00-\x1f\x7f]+')
_WHITESPACE_RE = re.compile(r"\s+")
DEFAULT_STEM = "вложение"

#: Размер куска при потоковом чтении загрузки и при отдаче файла.
_READ_CHUNK = 256 * 1024
#: До какого размера загрузка держится в памяти, прежде чем уйти во временный
#: файл: пик потребления RAM не зависит от разрешённого лимита файла.
_SPOOL_BYTES = 1024 * 1024


class AttachmentRejected(Exception):
    """Отказ загрузки/скачивания с уже выбранным HTTP-кодом и безопасным текстом.

    Текст предназначен для показа пользователю: без путей, без внутреннего
    имени хранилища и без фрагментов содержимого.
    """

    def __init__(self, status_code: int, detail: str) -> None:
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


@dataclass(frozen=True)
class AttachmentLimits:
    """Лимиты вложений, выбранные из конфигурации приложения."""

    max_file_bytes: int
    max_total_bytes: int
    max_count: int

    @classmethod
    def from_settings(cls, settings: Settings) -> AttachmentLimits:
        return cls(
            max_file_bytes=settings.attachments_max_file_bytes,
            max_total_bytes=settings.attachments_max_total_bytes,
            max_count=settings.attachments_max_count,
        )


# --- Права доступа -----------------------------------------------------------


def _fresh_user(db: Session, user: User) -> User:
    """Перечитать пользователя: роль могли изменить прямо во время запроса."""
    return db.get(User, user.id, populate_existing=True) or user


def can_view(db: Session, user: User | None, candidate: Candidate) -> bool:
    """Видит ли пользователь вложения этого кандидата.

    Правило совпадает с видимостью самой карточки: HR — только свои кандидаты,
    руководитель и администратор — все, плюс явный грант
    ``candidate_documents_all``/``pilot_full_access``. Удалённый кандидат
    вложения не отдаёт.
    """
    if user is None:
        return False
    user = _fresh_user(db, user)
    if not user.is_active or candidate.deleted_at is not None:
        return False
    if has_grant(db, user, AccessGrantScope.CANDIDATE_DOCUMENTS_ALL):
        return True
    if user.role in (UserRole.MANAGER, UserRole.ADMIN):
        return True
    return user.role == UserRole.HR and candidate.owner_user_id == user.id


def can_manage(db: Session, user: User | None, candidate: Candidate) -> bool:
    """Может ли пользователь загружать и удалять вложения кандидата.

    Осознанно совпадает с правом видеть: загрузка/удаление доступны тому, кто
    отвечает за кандидата (owner HR), руководителю, администратору или
    держателю гранта на документы всей базы. Скрытие кнопки во frontend
    защитой не считается — проверка выполняется здесь на каждый запрос.
    """
    return can_view(db, user, candidate)


# --- Чтение загрузки ---------------------------------------------------------


async def read_upload_limited(upload: UploadFile, max_bytes: int) -> bytes:
    """Прочитать загрузку, обрывая чтение на превышении лимита.

    Данные пишутся в :class:`tempfile.SpooledTemporaryFile`, поэтому до
    достижения лимита в памяти держится не более ``_SPOOL_BYTES``; превышение
    лимита — это 413 сразу, а не полностью буферизованный огромный файл.
    """
    total = 0
    with tempfile.SpooledTemporaryFile(max_size=_SPOOL_BYTES) as buffer:
        while True:
            chunk = await upload.read(_READ_CHUNK)
            if not chunk:
                break
            total += len(chunk)
            if total > max_bytes:
                raise AttachmentRejected(
                    413,
                    "Файл больше допустимого размера "
                    f"{_human_bytes(max_bytes)}. Загрузите более лёгкий файл "
                    "или сохраните скан с меньшим разрешением.",
                )
            buffer.write(chunk)
        if total == 0:
            raise AttachmentRejected(422, "Файл пустой: загружать нечего.")
        buffer.seek(0)
        return buffer.read()


def _human_bytes(value: int) -> str:
    """Человекочитаемый размер для сообщений об ошибках."""
    if value % (1024 * 1024) == 0:
        return f"{value // (1024 * 1024)} МБ"
    if value % 1024 == 0:
        return f"{value // 1024} КБ"
    return f"{value} Б"


# --- Имя файла ---------------------------------------------------------------


def sanitize_filename(raw: str | None, kind: str) -> str:
    """Безопасное отображаемое имя с расширением разрешённого формата.

    Из значения вынимается только последняя часть пути (для обоих разделителей),
    срезаются управляющие и зарезервированные символы, служебные имена Windows
    и лишние пробелы; расширение всегда проставляется сервером по
    распознанному типу. Результат никогда не содержит разделителей и не
    интерпретируется как путь.
    """
    if kind not in SUPPORTED_KINDS:
        raise AttachmentRejected(415, f"Неподдерживаемый формат. {SUPPORTED_HINT}")
    text = unicodedata.normalize("NFKC", raw or "").replace("\x00", "")
    # basename для обоих разделителей: клиент может прислать Windows-путь.
    text = posixpath.basename(text.replace("\\", "/")).strip()
    stem = _UNSAFE_NAME_RE.sub(" ", text)
    stem = _WHITESPACE_RE.sub(" ", stem).strip(" .")
    extension = _extension_for_kind(kind)
    # Своё же расширение снимаем, чтобы не получалось «анкета.docx.docx»;
    # чужое (report.exe) остаётся — имя не теряет информацию.
    if stem.lower().endswith(extension):
        stem = stem[: -len(extension)].rstrip(" .")
    if stem.upper() in _RESERVED_WINDOWS_NAMES:
        stem = ""
    max_stem = FILENAME_MAX_LENGTH - len(extension)
    stem = stem[:max_stem].rstrip(" .")
    if not stem:
        stem = DEFAULT_STEM
    return f"{stem}{extension}"


def _extension_for_kind(kind: str) -> str:
    for extension, value in KIND_BY_EXTENSION.items():
        if value == kind:
            return extension
    raise AttachmentRejected(415, f"Неподдерживаемый формат. {SUPPORTED_HINT}")


# --- Проверка содержимого ----------------------------------------------------


def detect_kind(payload: bytes, filename: str | None, content_type: str | None) -> str:
    """Определить тип по сигнатуре и сверить его с расширением.

    Возвращает ``"docx"`` или ``"pdf"``; всё остальное — отказ 415 с текстом,
    который можно показать пользователю.
    """
    if not payload:
        raise AttachmentRejected(422, "Файл пустой: загружать нечего.")
    declared = (content_type or "").split(";")[0].strip().lower()
    if declared in DANGEROUS_CONTENT_TYPES:
        raise AttachmentRejected(
            415,
            "Такой тип файла запрещён: исполняемые файлы, скрипты, HTML и "
            f"архивы не принимаются. {SUPPORTED_HINT}",
        )
    extension = posixpath.splitext(posixpath.basename(filename or ""))[1].lower()
    if extension not in KIND_BY_EXTENSION:
        raise AttachmentRejected(415, f"Неподдерживаемый формат файла. {SUPPORTED_HINT}")
    expected = KIND_BY_EXTENSION[extension]

    if payload.startswith(OLE2_MAGIC):
        raise AttachmentRejected(
            415,
            "Это старый формат Office с поддержкой макросов. Откройте файл в "
            "Word и сохраните как .docx.",
        )
    if payload.startswith(ARCHIVE_MAGICS) and not payload.startswith(ZIP_MAGICS):
        raise AttachmentRejected(415, f"Архивы не принимаются. {SUPPORTED_HINT}")

    if payload.startswith(ZIP_MAGICS):
        actual = "docx"
        validate_docx_container(payload)
    elif PDF_MAGIC in payload[:PDF_HEADER_SCAN_BYTES]:
        actual = "pdf"
        validate_pdf(payload)
    else:
        raise AttachmentRejected(
            415,
            "Содержимое файла не соответствует его расширению: файл повреждён "
            f"или это не документ. {SUPPORTED_HINT}",
        )

    if actual != expected:
        raise AttachmentRejected(
            415,
            f"Расширение файла не соответствует его содержимому. Проверьте файл: {SUPPORTED_HINT}",
        )
    return actual


def validate_docx_container(payload: bytes) -> None:
    """Осмотреть DOCX как ZIP-контейнер, ничего не распаковывая.

    Проверяются: читаемость контейнера, имена записей (без абсолютных путей и
    ``..`` — Zip Slip), число записей и объявленные размеры (zip-bomb),
    обязательные части OOXML и отсутствие макросов.
    """
    try:
        archive = zipfile.ZipFile(io.BytesIO(payload))
    except zipfile.BadZipFile as exc:
        raise AttachmentRejected(
            415, "Файл с расширением .docx не является документом Word."
        ) from exc
    with archive:
        infos = archive.infolist()
        if not infos:
            raise AttachmentRejected(415, "Документ Word пуст и не может быть сохранён.")
        if len(infos) > DOCX_MAX_ENTRIES:
            raise AttachmentRejected(415, "Документ Word содержит слишком много частей.")
        names: set[str] = set()
        total = 0
        for info in infos:
            name = info.filename
            if name.startswith(("/", "\\")) or ".." in name.replace("\\", "/").split("/"):
                raise AttachmentRejected(415, "Документ Word содержит недопустимые пути.")
            if any(marker in name.lower() for marker in DOCX_MACRO_PART_MARKERS):
                raise AttachmentRejected(
                    415, "Документы с макросами запрещены. Сохраните файл как обычный .docx."
                )
            if info.file_size > DOCX_MAX_ENTRY_BYTES:
                raise AttachmentRejected(415, "Документ Word содержит слишком большую часть.")
            total += info.file_size
            if total > DOCX_MAX_TOTAL_BYTES:
                raise AttachmentRejected(415, "Документ Word слишком большой после распаковки.")
            names.add(name)
        if DOCX_DOCUMENT_PART not in names or DOCX_CONTENT_TYPES_PART not in names:
            raise AttachmentRejected(415, "Файл с расширением .docx не является документом Word.")
        types_info = archive.getinfo(DOCX_CONTENT_TYPES_PART)
        if types_info.file_size > DOCX_CONTENT_TYPES_READ_BYTES:
            raise AttachmentRejected(415, "Документ Word содержит слишком большую часть.")
        with archive.open(DOCX_CONTENT_TYPES_PART) as handle:
            declared = handle.read(DOCX_CONTENT_TYPES_READ_BYTES + 1).lower()
        if len(declared) > DOCX_CONTENT_TYPES_READ_BYTES:
            raise AttachmentRejected(415, "Документ Word содержит слишком большую часть.")
        if DOCX_MACRO_CONTENT_TYPE.encode() in declared:
            raise AttachmentRejected(
                415, "Документы с макросами запрещены. Сохраните файл как обычный .docx."
            )


def _iter_deflate_streams(payload: bytes) -> Iterator[bytes]:
    """Распакованные потоки PDF (FlateDecode).

    В PDF 1.5+ объекты штатно лежат в object streams, сжатых FlateDecode, — там
    запрещённой конструкции в сырых байтах нет вовсе. Пробуем распаковать каждый
    поток: не-Flate (изображения, шрифты, содержимое страниц) просто не
    распакуется, а битый или обрезанный поток пропускается — испорченный файл не
    повод отвечать 500. Размер жёстко ограничен, чтобы распаковка не стала
    способом съесть память.
    """
    produced = 0
    opened = 0
    for match in _STREAM_KEYWORD.finditer(payload):
        if opened >= PDF_STREAM_MAX_COUNT or produced >= PDF_STREAM_MAX_TOTAL_BYTES:
            return
        start = match.end()
        end = payload.find(b"endstream", start)
        if end < 0:
            continue  # обрезанный файл: потока нет целиком
        opened += 1
        raw = payload[start:end]
        budget = min(PDF_STREAM_MAX_BYTES, PDF_STREAM_MAX_TOTAL_BYTES - produced)
        data = _inflate(raw, budget)
        if not data:
            continue
        produced += len(data)
        yield data


def _inflate(raw: bytes, budget: int) -> bytes:
    """Распаковать поток в пределах ``budget`` байт; ошибка — пустой результат."""
    if budget <= 0 or not raw:
        return b""
    for wbits in (15, -15):  # zlib-обёртка и «голый» deflate от кривых продюсеров
        try:
            return zlib.decompressobj(wbits).decompress(raw, budget)
        except zlib.error:
            continue
    return b""


def _has_forbidden_name(payload: bytes) -> bool:
    """Есть ли в файле запрещённое имя — в сыром виде или в распакованном потоке."""
    if _FORBIDDEN_NAME_PATTERN.search(payload) is not None:
        return True
    return any(
        _FORBIDDEN_NAME_PATTERN.search(chunk) is not None
        for chunk in _iter_deflate_streams(payload)
    )


def validate_pdf(payload: bytes) -> None:
    """Отклонить PDF, который пытается что-то выполнить или вложить.

    Сервер не выполняет и не рендерит PDF, поэтому проверка сводится к отказу
    от конструкций, способных запустить код при открытии файла на машине HR.

    Имена ищутся не простым поиском подстроки: учитываются white-space внутри
    name-токена, ``#xx``-эскейпы и содержимое распакованных FlateDecode-потоков.
    Проверка остаётся эвристической — сервер не реализует полный разбор PDF, —
    но известные способы обхода (пробел внутри имени, ``#xx``, сжатый object
    stream) ею закрыты; список и границы честности описаны в
    ``docs/candidate-attachments.md``.
    """
    if _has_forbidden_name(payload):
        raise AttachmentRejected(
            415,
            "PDF содержит запрещённые элементы (скрипты, вложенные файлы "
            "или запуск программ). Сохраните документ без них.",
        )


# --- Хранение ----------------------------------------------------------------


def ensure_enabled(settings: Settings) -> None:
    """Отказ 403, если вложения выключены конфигурацией."""
    if not settings.attachments_enabled:
        raise AttachmentRejected(
            403, "Вложения отключены администратором. Обратитесь к администратору."
        )


def list_attachments(db: Session, candidate_id: object) -> list[CandidateAttachment]:
    """Активные вложения кандидата, новые сверху (blob не загружается)."""
    return list(
        db.scalars(
            select(CandidateAttachment)
            .where(
                CandidateAttachment.candidate_id == candidate_id,
                CandidateAttachment.deleted_at.is_(None),
            )
            .order_by(
                CandidateAttachment.uploaded_at.desc(),
                CandidateAttachment.filename,
            )
        )
    )


def _totals(db: Session, candidate_id: object) -> tuple[int, int]:
    """(число активных вложений, их суммарный размер) для контроля квоты."""
    row = db.execute(
        select(
            func.count(CandidateAttachment.id),
            func.coalesce(func.sum(CandidateAttachment.size_bytes), 0),
        ).where(
            CandidateAttachment.candidate_id == candidate_id,
            CandidateAttachment.deleted_at.is_(None),
        )
    ).one()
    return int(row[0]), int(row[1])


def create_attachment(
    db: Session,
    *,
    candidate: Candidate,
    actor: User,
    payload: bytes,
    filename: str | None,
    content_type: str | None,
    settings: Settings,
    ip_address: str | None = None,
    user_agent: str | None = None,
) -> CandidateAttachment:
    """Проверить, сохранить и проаудировать одно вложение.

    Квоты проверяются под advisory-блокировкой кандидата, поэтому две
    параллельные загрузки не могут вместе превысить лимит; конфликт имён
    ловится частичным уникальным индексом и превращается в 409.
    """
    ensure_enabled(settings)
    limits = AttachmentLimits.from_settings(settings)
    if len(payload) > limits.max_file_bytes:
        raise AttachmentRejected(
            413, f"Файл больше допустимого размера {_human_bytes(limits.max_file_bytes)}."
        )
    kind = detect_kind(payload, filename, content_type)
    name = sanitize_filename(filename, kind)

    advisory(db, f"candidate_attachments:{candidate.id}")
    count, total = _totals(db, candidate.id)
    if count >= limits.max_count:
        raise AttachmentRejected(
            413,
            f"У кандидата уже {count} вложений — это предел. "
            "Удалите ненужное и повторите загрузку.",
        )
    if total + len(payload) > limits.max_total_bytes:
        raise AttachmentRejected(
            413,
            "Превышен общий объём вложений кандидата "
            f"({_human_bytes(limits.max_total_bytes)}). Удалите ненужные файлы.",
        )

    attachment = CandidateAttachment(
        candidate_id=candidate.id,
        filename=name,
        kind=kind,
        size_bytes=len(payload),
        sha256=hashlib.sha256(payload).hexdigest(),
        content=payload,
        uploaded_by_user_id=actor.id,
        uploaded_at=utc_now(),
    )
    db.add(attachment)
    try:
        db.flush()
    except IntegrityError as exc:
        db.rollback()
        raise AttachmentRejected(
            409, f"Файл с именем «{name}» уже загружен. Переименуйте файл и повторите."
        ) from exc

    record_event(
        db,
        AuditAction.CANDIDATE_ATTACHMENT_UPLOADED,
        actor=actor,
        candidate_id=candidate.id,
        ip_address=ip_address,
        user_agent=user_agent,
        # Ни имени файла, ни содержимого: только идентификаторы и метрики.
        details=f"attachment={attachment.id} kind={kind} bytes={attachment.size_bytes}",
        commit=False,
    )
    db.commit()
    db.refresh(attachment)
    return attachment


def get_attachment(db: Session, candidate: Candidate, attachment_id: object) -> CandidateAttachment:
    """Активное вложение кандидата; 404 для чужого, удалённого или несуществующего."""
    attachment = db.get(CandidateAttachment, attachment_id)
    if (
        attachment is None
        or attachment.candidate_id != candidate.id
        or attachment.deleted_at is not None
    ):
        raise AttachmentRejected(404, "Вложение не найдено.")
    return attachment


def delete_attachment(
    db: Session,
    *,
    candidate: Candidate,
    attachment: CandidateAttachment,
    actor: User,
    ip_address: str | None = None,
    user_agent: str | None = None,
) -> CandidateAttachment:
    """Мягкое удаление с блокировкой строки и аудитом.

    Параллельное удаление одного вложения двумя пользователями даёт 409 тому,
    кто пришёл вторым: строка перечитывается под ``FOR UPDATE``. Байты
    остаются в базе (политика проекта запрещает молча уничтожать данные) —
    физическая очистка выполняется только вместе с резервной копией и только
    администратором БД.
    """
    locked = db.scalar(
        select(CandidateAttachment)
        .where(CandidateAttachment.id == attachment.id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if locked is None or locked.candidate_id != candidate.id:
        raise AttachmentRejected(404, "Вложение не найдено.")
    if locked.deleted_at is not None:
        raise AttachmentRejected(409, "Вложение уже удалено другим пользователем.")
    locked.deleted_at = utc_now()
    locked.deleted_by_user_id = actor.id
    record_event(
        db,
        AuditAction.CANDIDATE_ATTACHMENT_DELETED,
        actor=actor,
        candidate_id=candidate.id,
        ip_address=ip_address,
        user_agent=user_agent,
        details=f"attachment={locked.id} kind={locked.kind} bytes={locked.size_bytes}",
        commit=False,
    )
    db.commit()
    db.refresh(locked)
    return locked


def record_download(
    db: Session,
    *,
    candidate: Candidate,
    attachment: CandidateAttachment,
    actor: User,
    ip_address: str | None = None,
    user_agent: str | None = None,
) -> None:
    """Аудит скачивания: id вложения, тип и размер — без имени файла и текста."""
    record_event(
        db,
        AuditAction.CANDIDATE_ATTACHMENT_DOWNLOADED,
        actor=actor,
        candidate_id=candidate.id,
        ip_address=ip_address,
        user_agent=user_agent,
        details=f"attachment={attachment.id} kind={attachment.kind} bytes={attachment.size_bytes}",
        commit=True,
    )


# --- Отдача файла ------------------------------------------------------------


def content_disposition(filename: str) -> str:
    """``Content-Disposition`` с ASCII-fallback и RFC 5987 ``filename*``.

    Всегда ``attachment``: встроенный просмотр PDF/DOCX в браузере не
    используется, чтобы содержимое чужого документа не исполнялось в нашем
    origin.
    """
    ascii_name = filename.encode("ascii", "ignore").decode("ascii") or "attachment"
    ascii_name = re.sub(r'["\\\r\n]', "_", ascii_name).strip() or "attachment"
    from urllib.parse import quote

    encoded = quote(filename, safe="")
    return f"attachment; filename=\"{ascii_name}\"; filename*=UTF-8''{encoded}"


def read_payload(attachment: CandidateAttachment) -> bytes:
    """Загрузить байты вложения до того, как сессия запроса будет закрыта.

    Сессионная зависимость FastAPI закрывается до отправки тела ответа, поэтому
    ленивая загрузка отложенной колонки внутри генератора отдачи не сработает.
    Размер строки ограничен конфигурацией (``ATTACHMENTS_MAX_FILE_BYTES``), так
    что пик потребления памяти известен заранее.
    """
    return bytes(attachment.content)


def iter_content(payload: bytes, chunk_size: int = _READ_CHUNK) -> Iterator[bytes]:
    """Отдать байты вложения кусками: поток в HTTP, а не один большой блок."""
    for start in range(0, len(payload), chunk_size):
        yield payload[start : start + chunk_size]


def uploaded_at_iso(attachment: CandidateAttachment) -> datetime:
    """Осознанное UTC-время загрузки для API-ответа."""
    return ensure_aware(attachment.uploaded_at)
