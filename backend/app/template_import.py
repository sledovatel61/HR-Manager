"""Safe import of template material from a file (UX feedback 2026-09-29, block E).

The section previously only accepted hand-typed text, so the methodical base
living in ordinary files could not be brought in at all. This module turns a
supported **plain-text** file into a template draft, with the controls the
brief asks for:

* **extensions** — a closed list (``.txt``, ``.md``, ``.markdown``, ``.csv``).
  Anything else is refused with a message naming the supported formats;
* **size** — a hard cap, checked while reading so an oversized upload is cut
  off instead of being buffered in full;
* **content control** — bytes are decoded as strict UTF-8, a NUL byte or a
  high proportion of replacement characters means «this is not text», and
  control characters are stripped before the existing body validator runs;
* **safe naming** — the file name is only used as a *suggestion*: it is
  reduced to a safe display title, never written to disk, never used as a
  path, and never trusted as a template name;
* **audit** — the import is recorded with the template id and the format, not
  with the file contents.

The file is *not* stored as a file: only its validated text becomes the
template body, exactly like a typed one. No path is ever constructed, so
there is no traversal surface, and binary formats (``.docx``/``.pdf``) are
rejected rather than half-parsed.
"""

from __future__ import annotations

import io
import re
import unicodedata
import xml.etree.ElementTree as ElementTree
import zipfile
from dataclasses import dataclass

from app.template_render import (
    BODY_MAX_LENGTH,
    TemplateContentError,
    validate_body,
    validate_title,
)

#: Closed list of importable formats. Plain text is understood directly; a
#: ``.docx`` is opened as a ZIP and only its text is read (see
#: :func:`_docx_to_text`). ``.pdf`` is deliberately *not* here — see
#: :data:`PDF_REFUSAL`.
SUPPORTED_EXTENSIONS: tuple[str, ...] = (".txt", ".md", ".markdown", ".csv", ".docx")

#: Hard cap on the uploaded file. A template body is capped at 20 000
#: characters, so anything much larger can never become a valid body.
#: Unchanged: a bigger upload cap is not what makes more files importable.
MAX_UPLOAD_BYTES = 512 * 1024

SUPPORTED_HINT = "Допустимые форматы: " + ", ".join(SUPPORTED_EXTENSIONS) + "."

#: PDF is a page-description format, not a text format. Extracting readable
#: text from it without a rendering engine means guessing at font encodings
#: and content streams, and the result would be «half-parsed ерунда» — worse
#: than a refusal, because a garbled legal form looks authoritative. The
#: import therefore refuses PDFs and says what to do instead.
PDF_REFUSAL = (
    "PDF не читается: в нём нет обычного текста, только описание страницы. "
    "Откройте файл в Word и сохраните как .docx — или скопируйте текст в .txt/.md. "
    "После этого загрузите снова."
)

#: Reads one extra byte to tell "exactly at the limit" from "over it".
_CHUNK = 64 * 1024

# --- .docx limits (zip-bomb defence) -----------------------------------------
#: A legitimate methodical file is a few hundred KB of XML. These caps are
#: generous for real documents and small enough that a decompression bomb is
#: rejected after a bounded amount of work, never after exhausting memory.
DOCX_MAX_ENTRIES = 2048
DOCX_MAX_ENTRY_BYTES = 4 * 1024 * 1024
DOCX_MAX_TOTAL_BYTES = 16 * 1024 * 1024
#: Refuse an entry that claims far more than it stores — the classic bomb
#: shape (a few hundred bytes expanding to gigabytes).
DOCX_MAX_RATIO = 200

DOCX_DOCUMENT_PART = "word/document.xml"
_W_NS = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
# A DTD/entity declaration is the shape of an XXE or billion-laughs payload.
# ElementTree would not expand it usefully anyway, so refusing is both safer
# and clearer than parsing it.
_DOCTYPE_RE = re.compile(rb"<!DOCTYPE|<!ENTITY", re.IGNORECASE)
#: WordprocessingML elements whose only visible effect is a space or a line
#: break. Anything not listed here (styles, images, fields, macros, embedded
#: objects) contributes no text at all.
_DOCX_BREAK_TAGS: dict[str, str] = {"tab": " ", "br": "\n", "cr": "\n"}

# Characters that must never survive into a template body: NUL and the C0
# control range except tab and newline. The renderer forbids NUL separately
# (it delimits render markers), so it is dropped here rather than 500-ing.
_CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
# Path separators and traversal markers: the file name is a *title* source,
# never a path, but keeping them out makes the output obviously safe.
_UNSAFE_NAME_RE = re.compile(r"[\\/:*?\"<>|\x00-\x1f]+")
_WHITESPACE_RE = re.compile(r"\s+")
# Below this share of decoded characters the file is treated as binary.
_TEXT_RATIO = 0.9


@dataclass(frozen=True)
class ImportedTemplate:
    """The validated result of reading one uploaded file."""

    title: str
    body: str
    original_name: str
    extension: str


def safe_display_name(filename: str | None) -> str:
    """A safe, human-readable title derived from a file name.

    Never returns a path, never keeps separators, and collapses anything
    exotic into a single space. Empty result means «use your own title».
    """
    raw = (filename or "").strip()
    if not raw:
        return ""
    # A client may send a full path; only the last segment is ever used.
    base = raw.replace("\\", "/").rsplit("/", 1)[-1]
    stem = base.rsplit(".", 1)[0] if "." in base[1:] else base
    # NFKC first so look-alike spacing collapses instead of sneaking through.
    stem = unicodedata.normalize("NFKC", stem)
    stem = _UNSAFE_NAME_RE.sub(" ", stem)
    stem = _WHITESPACE_RE.sub(" ", stem).strip(" .-")
    return stem[:120]


def extension_of(filename: str | None) -> str:
    """Lower-cased extension including the dot, or ``""``."""
    raw = (filename or "").strip().replace("\\", "/").rsplit("/", 1)[-1]
    _, dot, ext = raw.rpartition(".")
    return f".{ext.lower()}" if dot else ""


def decode_upload(payload: bytes) -> str:
    """Decode uploaded bytes into text, refusing anything that is not text."""
    if not payload:
        raise TemplateContentError("Файл пуст.")
    if len(payload) > MAX_UPLOAD_BYTES:
        raise TemplateContentError(
            f"Файл больше допустимых {MAX_UPLOAD_BYTES // 1024} КБ. Сократите его."
        )
    if b"\x00" in payload:
        # NUL never occurs in text and is the renderer's marker delimiter.
        raise TemplateContentError("Файл не похож на текст: внутри обнаружены двоичные данные.")
    try:
        text = payload.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise TemplateContentError(
            "Файл должен быть в кодировке UTF-8. Пересохраните его и повторите."
        ) from exc
    if not text.strip():
        raise TemplateContentError("Файл не содержит текста.")
    suspicious = text.count("�")
    if suspicious / max(1, len(text)) > 1 - _TEXT_RATIO:
        raise TemplateContentError("Файл не похож на текст: слишком много нечитаемых символов.")
    return text


def read_template_file(
    payload: bytes, filename: str | None, title_hint: str = ""
) -> ImportedTemplate:
    """Validate an uploaded file and turn it into template content.

    Raises :class:`TemplateContentError` with a message that is safe to show
    to the user; the caller turns that into a 422.
    """
    extension = extension_of(filename)
    if extension == ".pdf":
        raise TemplateContentError(PDF_REFUSAL)
    if extension not in SUPPORTED_EXTENSIONS:
        raise TemplateContentError(
            f"Формат {extension or 'без расширения'} не поддерживается. {SUPPORTED_HINT}"
        )
    text = _docx_to_text(payload) if extension == ".docx" else decode_upload(payload)
    body = _CONTROL_RE.sub("", text)
    if len(body) > BODY_MAX_LENGTH:
        raise TemplateContentError(
            f"Текст длиннее {BODY_MAX_LENGTH} символов — это максимум одного шаблона."
        )
    title = (title_hint or "").strip() or _title_from_text(body) or safe_display_name(filename)
    if not title:
        raise TemplateContentError("Не удалось определить название — укажите его вручную.")
    return ImportedTemplate(
        title=validate_title(title),
        body=validate_body(body),
        original_name=safe_display_name(filename),
        extension=extension,
    )


def _docx_to_text(payload: bytes) -> str:
    """Read the visible text of a ``.docx`` and nothing else.

    A ``.docx`` is a ZIP archive, so this is the one place where untrusted
    compressed bytes are expanded, and it is where a zip-bomb would live.
    The defences, in order, before a single byte is inflated:

    * the archive must be a real ZIP (``BadZipFile`` → a plain refusal);
    * the entry count, each entry's declared size, their total, and the
      declared compression ratio are all capped;
    * only ``word/document.xml`` is ever opened — styles, images, embedded
      objects, macros and OLE payloads are never read at all;
    * the XML must not declare a DTD or entities, and is parsed with
      ElementTree, which does not resolve external references.

    Nothing is written to disk and no path is built from the file name, so
    there is no traversal surface either.
    """
    if not payload:
        raise TemplateContentError("Файл пуст.")
    if len(payload) > MAX_UPLOAD_BYTES:
        raise TemplateContentError(
            f"Файл больше допустимых {MAX_UPLOAD_BYTES // 1024} КБ. Сократите его."
        )
    try:
        archive = zipfile.ZipFile(io.BytesIO(payload))
    except zipfile.BadZipFile as exc:
        raise TemplateContentError(
            "Файл не является документом Word (.docx): это не ZIP-архив."
        ) from exc

    with archive:
        entries = archive.infolist()
        if len(entries) > DOCX_MAX_ENTRIES:
            raise TemplateContentError("Документ выглядит повреждённым: слишком много частей.")
        if DOCX_DOCUMENT_PART not in archive.namelist():
            raise TemplateContentError(
                "В документе нет текста: не найдена основная часть word/document.xml. "
                "Возможно, это не .docx."
            )
        total = 0
        for entry in entries:
            if entry.file_size > DOCX_MAX_ENTRY_BYTES:
                raise TemplateContentError(
                    "Документ выглядит повреждённым: внутри слишком большой файл."
                )
            if entry.compress_size > 0 and entry.file_size / entry.compress_size > DOCX_MAX_RATIO:
                raise TemplateContentError(
                    "Документ выглядит повреждённым: слишком сильное сжатие внутри архива."
                )
            total += entry.file_size
            if total > DOCX_MAX_TOTAL_BYTES:
                raise TemplateContentError(
                    "Документ выглядит повреждённым: слишком много данных после распаковки."
                )
        raw = archive.read(DOCX_DOCUMENT_PART)

    if _DOCTYPE_RE.search(raw[:4096]):
        raise TemplateContentError("Документ отклонён: внутри объявлены XML-сущности.")
    try:
        root = ElementTree.fromstring(raw)
    except ElementTree.ParseError as exc:
        raise TemplateContentError("Не удалось прочитать документ Word: файл повреждён.") from exc
    return _docx_text_from_xml(root)


def _docx_text_from_xml(root: ElementTree.Element) -> str:
    """Collect ``<w:t>`` runs, keeping paragraph and line breaks.

    Walks the tree rather than reading the XML as a string, so no markup,
    attribute or comment can leak into the template body.
    """
    parts: list[str] = []
    for element in root.iter():
        tag = element.tag
        if not tag.startswith(_W_NS):
            continue
        local = tag[len(_W_NS) :]
        if local == "t":
            if element.text:
                parts.append(element.text)
            continue
        emitted = _DOCX_BREAK_TAGS.get(local)
        if emitted == " ":
            parts.append(" ")
        elif emitted == "\n":
            parts.append("\n")
        elif local == "p" and parts and not parts[-1].endswith("\n"):
            # A paragraph ends with a line break; two in a row would be an
            # empty line, so only append when something was written since.
            parts.append("\n")
    text = "".join(parts)
    if not text.strip():
        raise TemplateContentError(
            "В документе нет текста — вероятно, это файл с картинками или таблицами без подписей."
        )
    return text


def _title_from_text(body: str) -> str:
    """First meaningful line of the file, used as the default title."""
    for raw_line in body.splitlines():
        line = raw_line.strip().lstrip("#").strip()
        line = re.sub(r"^=+\s*|\s*=+$", "", line).strip()
        if line:
            return line[:200]
    return ""


async def read_upload_limited(upload: object) -> bytes:
    """Read an ``UploadFile`` while enforcing :data:`MAX_UPLOAD_BYTES`.

    The limit is applied *while* reading so an oversized upload is discarded
    early instead of being fully buffered.
    """
    payload = bytearray()
    while True:
        chunk = await upload.read(_CHUNK)  # type: ignore[attr-defined]
        if not chunk:
            break
        payload.extend(chunk)
        if len(payload) > MAX_UPLOAD_BYTES:
            raise TemplateContentError(
                f"Файл больше допустимых {MAX_UPLOAD_BYTES // 1024} КБ. Сократите его."
            )
    return bytes(payload)
