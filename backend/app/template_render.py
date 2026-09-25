"""Safe, dependency-free rendering of document templates (phase 16).

The renderer is deliberately closed:

* placeholders are an allowlist of typed tokens (``{{ ns.field }}``); any other
  token, and any stray ``{``/``}`` character, is rejected with a Russian
  user-facing message;
* values are sanitized (control characters removed, whitespace collapsed,
  length capped) and **HTML-escaped**;
* only a minimal, non-HTML markup subset is understood: paragraphs (blank
  line), line breaks (single newline), ``- `` list items and ``**bold**``. Raw
  HTML is impossible: literal text is escaped first, then values are
  substituted through opaque markers that cannot be forged by user content
  (markers use NUL, which template validation forbids in the body);
* no Jinja, no ``eval``, no SQL, no expressions, no recursion.

The module is pure: no FastAPI, no SQLAlchemy, no I/O.
"""

from __future__ import annotations

import html
import re
from collections.abc import Mapping
from dataclasses import dataclass

# Server-side limits. These are protective limits against abuse, not customer
# business rules; they are documented in docs/document-placeholders.md.
BODY_MAX_LENGTH = 20000
TITLE_MAX_LENGTH = 200
NAME_MAX_LENGTH = 120
KIND_MAX_LENGTH = 32
IDEMPOTENCY_KEY_MAX_LENGTH = 64
MAX_VERSIONS_PER_TEMPLATE = 100

KIND_RE = re.compile(r"^[a-z][a-z0-9_-]{0,31}$")
_TOKEN_RE = re.compile(r"\{\{\s*([a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*)?)\s*\}\}")
# C0 controls except "\n" and "\t" (a tab is folded to a space by value/label
# sanitizing) plus DEL. NUL is forbidden because it delimits render markers.
_CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_BRACE_RE = re.compile(r"[{}]")
_WHITESPACE_RE = re.compile(r"\s+")
_BOLD_RE = re.compile(r"\*\*(?!\s)(.+?)(?<!\s)\*\*", re.DOTALL)
_MARKER_TEMPLATE = "\x00{}\x00"
_MARKER_RE = re.compile("\x00(\\d+)\x00")
_HTML_SHELL = """<!DOCTYPE html>
<html lang="ru">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title>
<style>
body {{ font-family: -apple-system, "Segoe UI", Roboto, Arial, sans-serif;
  margin: 0; padding: 32px; color: #1a1a1a; background: #ffffff; }}
article {{ max-width: 720px; margin: 0 auto; line-height: 1.5; }}
h1 {{ font-size: 20px; margin: 0 0 16px; }}
ul {{ margin: 8px 0 8px 20px; padding: 0; }}
li {{ margin: 2px 0; }}
@media print {{ body {{ padding: 0; }} }}
</style>
</head>
<body>
<article>
<h1>{title}</h1>
{body}
</article>
</body>
</html>
"""


class TemplateContentError(ValueError):
    """Rejected template content; the message is safe to show to the user."""


@dataclass(frozen=True)
class Placeholder:
    """One allowed token: what it renders and how long its value may be."""

    token: str
    description: str
    max_length: int


# The complete allowlist of the first version. Deliberately small: candidate
# fields that already exist, the responsible HR's name, and the server date.
# Event placeholders are a documented follow-up, not part of this phase.
ALLOWED_PLACEHOLDERS: tuple[Placeholder, ...] = (
    Placeholder("candidate.full_name", "ФИО кандидата", 200),
    Placeholder("candidate.position", "Должность из карточки кандидата", 200),
    Placeholder("candidate.stage", "Этап воронки (русское название)", 64),
    Placeholder("candidate.source", "Источник кандидата (русское название)", 64),
    Placeholder("candidate.phone", "Телефон кандидата (или «—»)", 32),
    Placeholder("candidate.email", "Email кандидата (или «—»)", 254),
    Placeholder("candidate.created_at", "Дата создания карточки (ДД.ММ.ГГГГ)", 10),
    Placeholder("hr.full_name", "ФИО ответственного HR", 200),
    Placeholder("system.date", "Текущая дата (ДД.ММ.ГГГГ)", 10),
    Placeholder("system.datetime", "Текущие дата и время (ДД.ММ.ГГГГ ЧЧ:ММ)", 16),
)

PLACEHOLDER_TOKENS: frozenset[str] = frozenset(item.token for item in ALLOWED_PLACEHOLDERS)
EMPTY_VALUE = "—"


def placeholder_catalog() -> list[dict[str, str]]:
    """Catalog for the UI/docs: tokens are public, values never are."""
    return [{"token": item.token, "description": item.description} for item in ALLOWED_PLACEHOLDERS]


def sanitize_value(value: str | None, *, max_length: int) -> str:
    """One safe line: no control characters, collapsed whitespace, capped."""
    if not value:
        return ""
    cleaned = _CONTROL_RE.sub(" ", str(value))
    cleaned = _WHITESPACE_RE.sub(" ", cleaned).strip()
    return cleaned[:max_length]


def validate_kind(kind: str) -> str:
    kind = (kind or "").strip()
    if not KIND_RE.match(kind):
        raise TemplateContentError(
            "Ключ шаблона: строчные латинские буквы, цифры, «-» и «_», до 32 символов."
        )
    return kind


def validate_title(title: str) -> str:
    value = sanitize_value(title, max_length=TITLE_MAX_LENGTH)
    if not value:
        raise TemplateContentError("Укажите заголовок документа.")
    return value


def validate_body(body: str | None) -> str:
    """Validate template text and return it normalized (\\r\\n -> \\n)."""
    if body is None:
        raise TemplateContentError("Текст шаблона обязателен.")
    value = body.replace("\r\n", "\n").replace("\r", "\n")
    if _CONTROL_RE.search(value):
        raise TemplateContentError("Текст шаблона содержит недопустимые управляющие символы.")
    if not value.strip():
        raise TemplateContentError("Текст шаблона обязателен.")
    if len(value) > BODY_MAX_LENGTH:
        raise TemplateContentError(
            f"Текст шаблона длиннее {BODY_MAX_LENGTH} символов. Сократите текст."
        )
    extract_placeholders(value)
    return value


def extract_placeholders(body: str) -> list[str]:
    """Return the sorted unique allowlisted tokens used by ``body``.

    Raises :class:`TemplateContentError` for an unknown token or for a stray
    brace outside a token, so a malformed template can never be saved.
    """
    found: set[str] = set()
    unknown: set[str] = set()
    literal_start = 0
    for match in _TOKEN_RE.finditer(body):
        _reject_braces(body[literal_start : match.start()])
        literal_start = match.end()
        token = match.group(1)
        if token not in PLACEHOLDER_TOKENS:
            unknown.add(token)
        else:
            found.add(token)
    _reject_braces(body[literal_start:])
    if unknown:
        raise TemplateContentError(
            "Неизвестные переменные: "
            + ", ".join(sorted(f"{{{{{token}}}}}" for token in unknown))
            + ". Доступен только список разрешённых переменных."
        )
    return sorted(found)


def _reject_braces(text: str) -> None:
    if _BRACE_RE.search(text):
        raise TemplateContentError(
            "Фигурные скобки допустимы только в переменных вида {{candidate.full_name}}."
        )


@dataclass(frozen=True)
class RenderedDocument:
    """The exact rendered artifacts stored in the immutable snapshot."""

    title: str
    text: str
    html: str


def render_document(*, title: str, body: str, values: Mapping[str, str]) -> RenderedDocument:
    """Render one document from a validated template and pre-formatted values.

    ``values`` must be built by the caller from the allowlist (see
    :data:`ALLOWED_PLACEHOLDERS`); unknown keys are ignored and missing ones
    render as an empty string. Values are sanitized and escaped here as well,
    so this function is safe even if a caller passes raw data.
    """
    safe_title = validate_title(title)
    safe_body = validate_body(body)
    max_lengths = {item.token: item.max_length for item in ALLOWED_PLACEHOLDERS}
    cleaned = {
        token: sanitize_value(values.get(token), max_length=max_lengths[token])
        for token in PLACEHOLDER_TOKENS
    }

    html_parts: list[str] = []
    text_parts: list[str] = []
    markers: list[str] = []
    cursor = 0
    for match in _TOKEN_RE.finditer(safe_body):
        literal = safe_body[cursor : match.start()]
        html_parts.append(html.escape(literal, quote=False))
        text_parts.append(literal)
        token = match.group(1)
        marker = _MARKER_TEMPLATE.format(len(markers))
        markers.append(cleaned.get(token, ""))
        html_parts.append(marker)
        text_parts.append(cleaned.get(token, ""))
        cursor = match.end()
    literal = safe_body[cursor:]
    html_parts.append(html.escape(literal, quote=False))
    text_parts.append(literal)

    body_html = _format_html("".join(html_parts), markers)
    body_text = _format_text("".join(text_parts))
    return RenderedDocument(
        title=safe_title,
        text=body_text,
        html=_HTML_SHELL.format(title=html.escape(safe_title, quote=False), body=body_html),
    )


def _format_html(escaped_with_markers: str, markers: list[str]) -> str:
    blocks: list[str] = []
    paragraph: list[str] = []
    items: list[str] = []

    def flush_paragraph() -> None:
        if paragraph:
            blocks.append("<p>" + "<br>".join(paragraph) + "</p>")
            paragraph.clear()

    def flush_list() -> None:
        if items:
            blocks.append("<ul>" + "".join(f"<li>{item}</li>" for item in items) + "</ul>")
            items.clear()

    for line in escaped_with_markers.split("\n"):
        stripped = line.strip()
        if not stripped:
            flush_paragraph()
            flush_list()
            continue
        if stripped.startswith("- "):
            flush_paragraph()
            # Bold is applied to the escaped text *before* values are
            # substituted: markup can only come from the template itself.
            items.append(_substitute_markers(_bold(stripped[2:].strip()), markers))
        else:
            flush_list()
            paragraph.append(_substitute_markers(_bold(stripped), markers))
    flush_paragraph()
    flush_list()
    return "\n".join(blocks)


def _format_text(text_with_values: str) -> str:
    """Plain-text form: markup stripped, list items kept as ``- `` lines."""
    blocks: list[str] = []
    paragraph: list[str] = []
    items: list[str] = []

    def flush_paragraph() -> None:
        if paragraph:
            blocks.append(" ".join(paragraph))
            paragraph.clear()

    def flush_list() -> None:
        if items:
            blocks.append("\n".join(f"- {item}" for item in items))
            items.clear()

    for line in text_with_values.split("\n"):
        stripped = line.strip()
        if not stripped:
            flush_paragraph()
            flush_list()
            continue
        if stripped.startswith("- "):
            flush_paragraph()
            items.append(_strip_bold(stripped[2:].strip()))
        else:
            flush_list()
            paragraph.append(_strip_bold(stripped))
    flush_paragraph()
    flush_list()
    return "\n\n".join(blocks)


def _substitute_markers(text: str, markers: list[str]) -> str:
    def replace(match: re.Match[str]) -> str:
        index = int(match.group(1))
        value = markers[index] if 0 <= index < len(markers) else ""
        return html.escape(value, quote=False)

    return _MARKER_RE.sub(replace, text)


def _bold(text: str) -> str:
    return _BOLD_RE.sub(r"<strong>\1</strong>", text)


def _strip_bold(text: str) -> str:
    return _BOLD_RE.sub(r"\1", text)
