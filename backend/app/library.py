"""«Библиотека HR» — read-first view over document templates.

The library is *not* a second template entity: every material is an ordinary
:class:`app.models.DocumentTemplate` with a published (active) version, plus
the library fields (``category``, ``summary``) introduced for the card
screen. This module only adds the read side — cards, the read-only detail
with a safely rendered fragment, and downloads — so the existing versioning,
audit, optimistic locking and access policy stay authoritative.

Access model: published materials are visible to every authenticated employee
(the same rule :func:`app.document_templates.list_templates` applies to
non-managers). Draft materials never appear here — including for managers,
whose draft preview lives in «Управление материалами».

Placeholder values: library materials are meant to be readable without a
candidate card. If a material still uses placeholders (for example after an
import), the detail screen substitutes obviously impersonal demo values and
explains each token in human words; real values are only ever filled in the
candidate card flow, which keeps its existing authorization.
"""

from __future__ import annotations

from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.audit import record_event
from app.models import (
    AuditAction,
    DocumentTemplate,
    DocumentTemplateVersion,
    User,
)
from app.template_render import (
    render_document,
    render_fragment,
    sanitize_value,
    validate_kind,
)

#: Closed category dictionary for the library screen. The keys are stored on
#: the template; the labels are the words an HR uses. An empty string is a
#: legacy/uncategorized value («Все» shows it, chips do not).
LIBRARY_CATEGORIES: tuple[str, ...] = (
    "interview",
    "candidate_docs",
    "calls",
    "onboarding",
    "memos",
    "position",
)

CATEGORY_LABELS: dict[str, str] = {
    "interview": "Собеседование",
    "candidate_docs": "Документы кандидата",
    "calls": "Звонки и сообщения",
    "onboarding": "Онбординг",
    "memos": "Памятки HR",
    "position": "Вопросники по должностям",
}

#: Human words for every allowlisted token, shown when a material needs
#: substitution. The library detail screen never renders raw ``{{ … }}``
#: tokens to a regular HR: it shows this explanation and a demo preview.
PLACEHOLDER_HINTS: dict[str, str] = {
    "candidate.full_name": "ФИО кандидата подставится из карточки кандидата",
    "candidate.position": "должность подставится из карточки кандидата",
    "candidate.stage": "этап воронки подставится из карточки кандидата",
    "candidate.source": "источник подставится из карточки кандидата",
    "candidate.phone": "телефон подставится из карточки кандидата",
    "candidate.email": "email подставится из карточки кандидата",
    "candidate.created_at": "дата создания карточки подставится автоматически",
    "hr.full_name": "ФИО ответственного HR подставится из карточки кандидата",
    "system.date": "дата формирования документа подставится автоматически",
    "system.datetime": "дата и время формирования подставятся автоматически",
}

#: Clearly impersonal demo values for the preview of a material that uses
#: placeholders. They are never stored anywhere — they exist only so the HR
#: can see how the finished document will look.
DEMO_PLACEHOLDER_VALUES: dict[str, str] = {
    "candidate.full_name": "Иван Тестовый (пример)",
    "candidate.position": "Должность (пример)",
    "candidate.stage": "Собеседование назначено (пример)",
    "candidate.source": "Сайт компании (пример)",
    "candidate.phone": "+7 900 000-00-00 (пример)",
    "candidate.email": "candidate@example.com",
    "candidate.created_at": "01.01.2026",
    "hr.full_name": "Мария Специалистова (пример)",
    "system.date": "01.01.2026",
    "system.datetime": "01.01.2026 10:00",
}

DOWNLOAD_FORMATS = ("html", "txt")
_CONTENT_TYPES = {"html": "text/html; charset=utf-8", "txt": "text/plain; charset=utf-8"}

SUMMARY_MAX_LENGTH = 300
SEED_KEY_MAX_LENGTH = 64


def category_label(category: str) -> str:
    """Russian label for a category key, «Без категории» for the legacy ``''``."""
    return CATEGORY_LABELS.get(category, "Без категории")


def validate_category(value: str | None) -> str:
    """Normalize a category from the API/seed to a known key or ``''``."""
    cleaned = (value or "").strip()
    if not cleaned:
        return ""
    if cleaned not in LIBRARY_CATEGORIES:
        raise ValueError(
            "Неизвестная категория материала. Допустимые значения: " + ", ".join(LIBRARY_CATEGORIES)
        )
    return cleaned


def validate_summary(value: str | None) -> str:
    """One-sentence purpose of a material, sanitized and capped."""
    return sanitize_value(value, max_length=SUMMARY_MAX_LENGTH)


# --- Reads -------------------------------------------------------------------


def published_materials(db: Session) -> list[tuple[DocumentTemplate, DocumentTemplateVersion]]:
    """Every template that has an active version, with that version.

    This is the exact visibility rule non-managers already get from
    :func:`app.document_templates.list_templates`; the library simply never
    widens it.
    """
    rows: list[tuple[DocumentTemplate, DocumentTemplateVersion]] = []
    versions = db.scalars(
        select(DocumentTemplateVersion)
        .where(DocumentTemplateVersion.state == "active")
        .order_by(DocumentTemplateVersion.activated_at.desc(), DocumentTemplateVersion.id)
    ).all()
    for version in versions:
        template = db.get(DocumentTemplate, version.template_id)
        if template is not None:
            rows.append((template, version))
    return rows


def _material_out(
    template: DocumentTemplate, version: DocumentTemplateVersion
) -> dict[str, object]:
    return {
        "id": template.id,
        "name": template.name,
        "kind": template.kind,
        "category": template.category,
        "summary": template.summary,
        "scope": template.scope,
        "version_id": version.id,
        "version_number": version.number,
        "title": version.title,
        "published_at": version.activated_at,
        "has_placeholders": bool(version.placeholders),
    }


def _active_material(
    db: Session, material_id: UUID
) -> tuple[DocumentTemplate, DocumentTemplateVersion]:
    """The template plus its active version, or 404.

    A template without a published version does not exist for the library —
    for managers the same id answers 404 here too, because draft preview
    belongs to «Управление материалами», which uses the manage endpoints.

    The import of :func:`app.document_templates.template_for` is local on
    purpose: that module imports the schemas, which import this module, so a
    top-level import would be circular.
    """
    from app.document_templates import template_for

    template = template_for(db, material_id)
    version = db.scalar(
        select(DocumentTemplateVersion)
        .where(
            DocumentTemplateVersion.template_id == template.id,
            DocumentTemplateVersion.state == "active",
        )
        .execution_options(populate_existing=True)
    )
    if version is None:
        raise HTTPException(404, "Материал не найден.")
    return template, version


def list_materials(db: Session, user: User) -> dict[str, object]:
    """Card list for the library main screen."""
    from app.documents import can_manage

    items = [_material_out(template, version) for template, version in published_materials(db)]
    return {
        "items": items,
        "categories": [{"key": key, "label": CATEGORY_LABELS[key]} for key in LIBRARY_CATEGORIES],
        # Same flag as the manage list: the library shows the «Управление
        # материалами» entry only to those the server would let through.
        "can_manage": can_manage(db, user),
    }


def material_detail(db: Session, material_id: UUID) -> dict[str, object]:
    """Read-only view data for one published material.

    ``body`` is the version's validated markup (the same text «Управление
    материалами» edits), ``body_html``/``body_text`` are rendered with demo
    values so the HR sees a finished-looking document instead of raw tokens.
    """
    template, version = _active_material(db, material_id)
    placeholders = [str(token) for token in (version.placeholders or [])]
    fragment = render_fragment(
        title=version.title, body=version.body, values=DEMO_PLACEHOLDER_VALUES
    )
    data = _material_out(template, version)
    data.update(
        {
            "body": version.body,
            "body_html": fragment.html,
            "body_text": fragment.text,
            "placeholders": placeholders,
            "placeholder_hints": [
                {
                    "token": token,
                    "hint": PLACEHOLDER_HINTS.get(token, "значение подставится автоматически"),
                }
                for token in placeholders
            ],
            "updated_at": template.updated_at,
        }
    )
    return data


# --- Downloads / new-window view ---------------------------------------------


def material_payload(
    template: DocumentTemplate, version: DocumentTemplateVersion, *, format: str
) -> tuple[str, str, bytes]:
    """``(media_type, filename, content)`` for a safe copy of a material.

    Filenames carry the sanitized kind and the version number — no personal
    data, no raw template name. The HTML artifact is standalone and is meant
    to be printed to PDF by the browser. Values render with the impersonal
    demo substitution, so a downloaded copy never contains a real candidate.
    """
    if format not in DOWNLOAD_FORMATS:
        raise HTTPException(422, "Поддерживаются форматы html и txt.")
    kind = sanitize_value(validate_kind(template.kind), max_length=32) or "material"
    filename = f"material-{kind}-v{version.number}.{format}"
    rendered = render_document(
        title=version.title, body=version.body, values=DEMO_PLACEHOLDER_VALUES
    )
    content = rendered.html if format == "html" else rendered.text
    return _CONTENT_TYPES[format], filename, content.encode("utf-8")


def audit_material_event(
    db: Session,
    *,
    action: AuditAction,
    user: User,
    template: DocumentTemplate,
    version: DocumentTemplateVersion,
    client_ip: str | None,
    client_user_agent: str | None,
    format: str | None = None,
) -> None:
    """Audit a material open/download with parameters only.

    Details carry ids, version number, category and (for downloads) the
    format — never the material text and never personal data, mirroring the
    phase 16 download audit.
    """
    detail = (
        f"material={template.id} version={version.id} number={version.number} "
        f"kind={template.kind} category={template.category}"
    )
    if format:
        detail += f" format={format}"
    record_event(
        db,
        action,
        actor=user,
        ip_address=client_ip,
        user_agent=client_user_agent,
        details=detail,
        commit=True,
    )


def active_material_or_404(
    db: Session, material_id: UUID
) -> tuple[DocumentTemplate, DocumentTemplateVersion]:
    """Public lookup for the download/view routes: published material or 404."""
    return _active_material(db, material_id)
