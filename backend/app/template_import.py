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

import re
import unicodedata
from dataclasses import dataclass

from app.template_render import (
    BODY_MAX_LENGTH,
    TemplateContentError,
    validate_body,
    validate_title,
)

#: Closed list of importable formats. Chosen so a template stays plain text
#: that the (deliberately non-HTML) renderer can validate and escape.
SUPPORTED_EXTENSIONS: tuple[str, ...] = (".txt", ".md", ".markdown", ".csv")

#: Hard cap on the uploaded file. A template body is capped at 20 000
#: characters, so anything much larger can never become a valid body.
MAX_UPLOAD_BYTES = 512 * 1024

SUPPORTED_HINT = "Допустимые форматы: " + ", ".join(SUPPORTED_EXTENSIONS) + "."

#: Reads one extra byte to tell "exactly at the limit" from "over it".
_CHUNK = 64 * 1024

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
    if extension not in SUPPORTED_EXTENSIONS:
        raise TemplateContentError(
            f"Формат {extension or 'без расширения'} не поддерживается. {SUPPORTED_HINT}"
        )
    text = decode_upload(payload)
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
