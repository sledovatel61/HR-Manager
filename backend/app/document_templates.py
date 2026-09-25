"""Document template operations (phase 16).

Writes join the caller's transaction, exactly like the phase 11 document
module. Locking model:

* template-level operations take ``SELECT ... FOR UPDATE`` on the parent row and
  check the optimistic ``revision`` counter — that serializes concurrent
  version creation, renames, activation and archiving of one template;
* activation additionally locks the currently active version row before
  archiving it, so the partial unique index (one active version per template)
  can never be violated by two racing activations;
* generation takes the candidate mutation advisory lock (shared with phase 10
  sends and phase 11 receipt changes) plus a per-candidate generation advisory
  lock, so the per-candidate document revision is allocated deterministically.

Access control reuses the accepted phase 11 policy verbatim:
:func:`app.documents.can_manage` (admin or an active ``document_lists_manage``
grant) for template content and :func:`app.documents.can_access` (owner HR /
manager, or an explicit ``candidate_documents_all`` grant) for candidate
documents — a foreign candidate answers 404, never 403.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.audit import record_event
from app.documents import (
    advisory,
    can_access,
    can_manage,
    candidate_for,
)
from app.models import (
    AuditAction,
    Candidate,
    CandidateDocumentGeneration,
    CandidateSource,
    CandidateStage,
    DocumentTemplate,
    DocumentTemplateVersion,
    User,
)
from app.notification_service import preference_for
from app.template_render import (
    EMPTY_VALUE,
    MAX_VERSIONS_PER_TEMPLATE,
    RenderedDocument,
    render_document,
    sanitize_value,
)
from app.template_schemas import (
    GenerateRequest,
    GenerationOut,
    GenerationsOut,
    NewTemplateVersion,
    PreviewOut,
    TemplateCreate,
    TemplateOut,
    TemplateRename,
    TemplatesOut,
    TemplateVersionOut,
)
from app.utils import ensure_aware, utc_now

# Russian labels for the closed dictionaries. The frontend keeps its own copy
# for chips (frontend/src/types.ts); tests assert that both cover every enum
# member, so a new stage/source cannot silently render as a raw key.
STAGE_LABELS: dict[CandidateStage, str] = {
    CandidateStage.NEW: "Новый",
    CandidateStage.CONTACTED: "Контакт",
    CandidateStage.REACHED: "Дозвон",
    CandidateStage.INTERVIEW_SCHEDULED: "Собеседование назначено",
    CandidateStage.INTERVIEW_DONE: "Собеседование проведено",
    CandidateStage.OFFER: "Оффер",
    CandidateStage.HIRED: "Оформлен",
    CandidateStage.STARTED: "Вышел",
    CandidateStage.PROBATION: "Испытательный срок",
    CandidateStage.FIRED: "Уволен",
    CandidateStage.REJECTED: "Отказ",
}

SOURCE_LABELS: dict[CandidateSource, str] = {
    CandidateSource.SITE: "Сайт компании",
    CandidateSource.REFERRAL: "Рекомендация сотрудника",
    CandidateSource.HH_MANUAL: "Внешний портал (ручной ввод)",
    CandidateSource.UNIVERSITY: "Вуз / стажировка",
    CandidateSource.EVENT: "Карьерное мероприятие",
    CandidateSource.AGENCY: "Кадровое агентство",
    CandidateSource.INBOUND_CALL: "Входящий звонок",
}

DOWNLOAD_FORMATS = ("html", "txt")
_CONTENT_TYPES = {"html": "text/html; charset=utf-8", "txt": "text/plain; charset=utf-8"}


def require_manage(db: Session, user: User) -> None:
    if not can_manage(db, user):
        raise HTTPException(403, "Нет права управления шаблонами документов.")


def user_timezone(db: Session, user: User, default_timezone: str) -> str:
    """The timezone used for rendered dates: the acting user's own preference."""
    preference = preference_for(db, user.id, default_timezone)
    name = preference.timezone or default_timezone
    try:
        ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        name = default_timezone
    return name


def placeholder_values(
    db: Session,
    *,
    candidate: Candidate,
    user: User,
    timezone: str,
    now: datetime,
) -> dict[str, str]:
    """Build the allowlisted values for one candidate.

    Every value is sanitized by the renderer as well; this function only maps
    stored fields to tokens. Missing optional values render as an em dash so a
    document never contains an accidental empty substitution.
    """
    try:
        zone = ZoneInfo(timezone)
    except (ZoneInfoNotFoundError, ValueError):
        raise HTTPException(
            422, "Неизвестный часовой пояс. Обновите настройки уведомлений."
        ) from None
    owner = db.get(User, candidate.owner_user_id)
    created_local = ensure_aware(candidate.created_at).astimezone(zone)
    now_local = ensure_aware(now).astimezone(zone)
    return {
        "candidate.full_name": candidate.full_name or EMPTY_VALUE,
        "candidate.position": candidate.position or EMPTY_VALUE,
        "candidate.stage": STAGE_LABELS.get(candidate.stage, candidate.stage.value),
        "candidate.source": SOURCE_LABELS.get(candidate.source, candidate.source.value),
        "candidate.phone": candidate.phone or EMPTY_VALUE,
        "candidate.email": candidate.email or EMPTY_VALUE,
        "candidate.created_at": created_local.strftime("%d.%m.%Y"),
        "hr.full_name": (owner.full_name if owner and owner.full_name else EMPTY_VALUE),
        "system.date": now_local.strftime("%d.%m.%Y"),
        "system.datetime": now_local.strftime("%d.%m.%Y %H:%M"),
    }


# --- Reads -------------------------------------------------------------------


def list_templates(db: Session, user: User) -> TemplatesOut:
    manage = can_manage(db, user)
    query = select(DocumentTemplate).order_by(
        DocumentTemplate.created_at.desc(), DocumentTemplate.id
    )
    if not manage:
        # Non-managers only ever see templates with a published (active) version.
        query = query.where(
            DocumentTemplate.id.in_(
                select(DocumentTemplateVersion.template_id).where(
                    DocumentTemplateVersion.state == "active"
                )
            )
        )
    items = [template_out(db, row, manage=manage) for row in db.scalars(query).all()]
    return TemplatesOut(items=items, can_manage=manage)


def template_out(db: Session, row: DocumentTemplate, *, manage: bool) -> TemplateOut:
    query = select(DocumentTemplateVersion).where(DocumentTemplateVersion.template_id == row.id)
    if not manage:
        query = query.where(DocumentTemplateVersion.state == "active")
    versions = [
        TemplateVersionOut.model_validate(version)
        for version in db.scalars(query.order_by(DocumentTemplateVersion.number.desc())).all()
    ]
    return TemplateOut(
        id=row.id,
        kind=row.kind,
        scope=row.scope,
        name=row.name,
        revision=row.revision,
        author_id=row.author_id,
        created_at=row.created_at,
        updated_at=row.updated_at,
        versions=versions,
    )


def template_for(db: Session, template_id: UUID, *, lock: bool = False) -> DocumentTemplate:
    query = (
        select(DocumentTemplate)
        .where(DocumentTemplate.id == template_id)
        .execution_options(populate_existing=True)
    )
    if lock:
        query = query.with_for_update()
    row = db.scalar(query)
    if row is None:
        raise HTTPException(404, "Шаблон не найден.")
    return row


def version_for(db: Session, template_id: UUID, version_id: UUID) -> DocumentTemplateVersion:
    row = db.scalar(
        select(DocumentTemplateVersion)
        .where(
            DocumentTemplateVersion.id == version_id,
            DocumentTemplateVersion.template_id == template_id,
        )
        .execution_options(populate_existing=True)
    )
    if row is None:
        raise HTTPException(404, "Версия шаблона не найденa.")
    return row


def active_version_for(db: Session, template_id: UUID) -> DocumentTemplateVersion:
    row = db.scalar(
        select(DocumentTemplateVersion)
        .where(
            DocumentTemplateVersion.template_id == template_id,
            DocumentTemplateVersion.state == "active",
        )
        .execution_options(populate_existing=True)
    )
    if row is None:
        raise HTTPException(422, "У шаблона нет опубликованной версии.")
    return row


# --- Writes ------------------------------------------------------------------


def _parent(db: Session, template_id: UUID, expected: int) -> DocumentTemplate:
    row = template_for(db, template_id, lock=True)
    if row.revision != expected:
        raise HTTPException(409, "Версия шаблона изменилась. Обновите данные.")
    row.revision += 1
    return row


def create_template(db: Session, user: User, payload: TemplateCreate) -> TemplateOut:
    template = DocumentTemplate(
        kind=payload.kind,
        scope=payload.scope.value if payload.scope else "",
        name=payload.name,
        author_id=user.id,
    )
    db.add(template)
    db.flush()
    version = DocumentTemplateVersion(
        template_id=template.id,
        number=1,
        state="draft",
        title=payload.title,
        body=payload.body,
        placeholders=_placeholders_of(payload.body),
        author_id=user.id,
    )
    db.add(version)
    db.flush()
    record_event(
        db,
        AuditAction.DOCUMENT_TEMPLATE_CREATED,
        actor=user,
        details=f"template={template.id} version={version.id} kind={template.kind}",
        commit=False,
    )
    db.commit()
    return template_out(db, template, manage=True)


def rename_template(
    db: Session, user: User, template_id: UUID, payload: TemplateRename
) -> TemplateOut:
    row = _parent(db, template_id, payload.expected_revision)
    if row.name == payload.name:
        raise HTTPException(409, "Название уже такое. Обновите данные.")
    detail = f"rename template={row.id} kind={row.kind}"
    row.name = payload.name
    row.updated_at = utc_now()
    db.flush()
    record_event(
        db, AuditAction.DOCUMENT_TEMPLATE_RENAMED, actor=user, details=detail, commit=False
    )
    db.commit()
    return template_out(db, row, manage=True)


def add_version(
    db: Session, user: User, template_id: UUID, payload: NewTemplateVersion
) -> TemplateOut:
    template = _parent(db, template_id, payload.expected_revision)
    count = db.scalar(
        select(func.count())
        .select_from(DocumentTemplateVersion)
        .where(DocumentTemplateVersion.template_id == template.id)
    )
    if (count or 0) >= MAX_VERSIONS_PER_TEMPLATE:
        raise HTTPException(
            409,
            f"Достигнут предел {MAX_VERSIONS_PER_TEMPLATE} версий. Архивируйте шаблон "
            "и создайте новый.",
        )
    number = (
        db.scalar(
            select(func.max(DocumentTemplateVersion.number)).where(
                DocumentTemplateVersion.template_id == template.id
            )
        )
        or 0
    ) + 1
    version = DocumentTemplateVersion(
        template_id=template.id,
        number=number,
        state="draft",
        title=payload.title,
        body=payload.body,
        placeholders=_placeholders_of(payload.body),
        author_id=user.id,
    )
    db.add(version)
    db.flush()
    record_event(
        db,
        AuditAction.TEMPLATE_VERSION_CREATED,
        actor=user,
        details=(
            f"template={template.id} version={version.id} number={version.number} "
            f"kind={template.kind}"
        ),
        commit=False,
    )
    db.commit()
    return template_out(db, template, manage=True)


def activate_version(
    db: Session, user: User, template_id: UUID, version_id: UUID, expected: int
) -> TemplateOut:
    template = _parent(db, template_id, expected)
    version = version_for(db, template.id, version_id)
    if version.state == "archived":
        raise HTTPException(409, "Архивную версию нельзя опубликовать.")
    if version.state == "active":
        raise HTTPException(409, "Версия уже опубликована.")
    current = db.scalar(
        select(DocumentTemplateVersion)
        .where(
            DocumentTemplateVersion.template_id == template.id,
            DocumentTemplateVersion.state == "active",
        )
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if current is not None:
        current.state = "archived"
        db.flush()  # release the partial unique index before activating the next one
    version.state = "active"
    version.activated_at = utc_now()
    db.flush()
    record_event(
        db,
        AuditAction.TEMPLATE_VERSION_ACTIVATED,
        actor=user,
        details=(
            f"template={template.id} version={version.id} number={version.number} "
            f"kind={template.kind}"
        ),
        commit=False,
    )
    db.commit()
    return template_out(db, template, manage=True)


def archive_version(
    db: Session, user: User, template_id: UUID, version_id: UUID, expected: int
) -> TemplateOut:
    template = _parent(db, template_id, expected)
    version = version_for(db, template.id, version_id)
    if version.state == "archived":
        raise HTTPException(409, "Версия уже архивирована.")
    version.state = "archived"
    db.flush()
    record_event(
        db,
        AuditAction.TEMPLATE_VERSION_ARCHIVED,
        actor=user,
        details=(
            f"template={template.id} version={version.id} number={version.number} "
            f"kind={template.kind}"
        ),
        commit=False,
    )
    db.commit()
    return template_out(db, template, manage=True)


def _placeholders_of(body: str) -> list[str]:
    from app.template_render import extract_placeholders

    return extract_placeholders(body)


# --- Rendering and generated documents ---------------------------------------


def render_for(
    db: Session,
    *,
    template: DocumentTemplate,
    version: DocumentTemplateVersion,
    candidate: Candidate,
    user: User,
    default_timezone: str,
    now: datetime | None = None,
) -> tuple[DocumentTemplateVersion, RenderedDocument, list[str]]:
    """Render one document. The version must be active (published)."""
    if version.state != "active":
        raise HTTPException(422, "Сформировать документ можно только из опубликованной версии.")
    timezone = user_timezone(db, user, default_timezone)
    values = placeholder_values(
        db, candidate=candidate, user=user, timezone=timezone, now=now or utc_now()
    )
    placeholders = [str(token) for token in (version.placeholders or [])]
    rendered = render_document(title=version.title, body=version.body, values=values)
    return version, rendered, placeholders


def preview(
    db: Session,
    *,
    user: User,
    candidate: Candidate,
    version_id: UUID,
    default_timezone: str,
) -> PreviewOut:
    version = resolve_active_version(db, version_id)
    template = template_for(db, version.template_id)
    _, rendered, placeholders = render_for(
        db,
        template=template,
        version=version,
        candidate=candidate,
        user=user,
        default_timezone=default_timezone,
    )
    return PreviewOut(
        template_id=template.id,
        template_version_id=version.id,
        template_number=version.number,
        template_name=template.name,
        kind=template.kind,
        title=rendered.title,
        body_text=rendered.text,
        body_html=rendered.html,
        placeholders=placeholders,
    )


def resolve_active_version(db: Session, version_id: UUID) -> DocumentTemplateVersion:
    version = db.scalar(
        select(DocumentTemplateVersion)
        .where(DocumentTemplateVersion.id == version_id)
        .execution_options(populate_existing=True)
    )
    if version is None:
        raise HTTPException(404, "Версия шаблона не найдена.")
    if version.state != "active":
        raise HTTPException(422, "Доступна только опубликованная версия шаблона.")
    return version


def generate(
    db: Session,
    *,
    user: User,
    candidate: Candidate,
    payload: GenerateRequest,
    default_timezone: str,
) -> tuple[CandidateDocumentGeneration, bool]:
    """Create an immutable snapshot; returns ``(row, created)``."""
    replay = _replay(db, user=user, candidate=candidate, payload=payload)
    if replay is not None:
        return replay, False
    advisory(db, f"document-generation:{candidate.id}")
    version = resolve_active_version(db, payload.template_version_id)
    template = template_for(db, version.template_id)
    _, rendered, _ = render_for(
        db,
        template=template,
        version=version,
        candidate=candidate,
        user=user,
        default_timezone=default_timezone,
    )
    revision = (
        db.scalar(
            select(func.max(CandidateDocumentGeneration.revision)).where(
                CandidateDocumentGeneration.candidate_id == candidate.id
            )
        )
        or 0
    ) + 1
    generation = CandidateDocumentGeneration(
        candidate_id=candidate.id,
        template_id=template.id,
        template_version_id=version.id,
        template_number=version.number,
        template_name=template.name,
        template_title=rendered.title,
        kind=template.kind,
        revision=revision,
        body_text=rendered.text,
        body_html=rendered.html,
        content_sha256=content_digest(rendered.text),
        idempotency_key=payload.idempotency_key,
        created_by=user.id,
    )
    db.add(generation)
    try:
        db.flush()
    except IntegrityError:
        # Two concurrent identical requests: the partial unique key arbitrates.
        db.rollback()
        winner = _replay(db, user=user, candidate=candidate, payload=payload)
        if winner is None:
            raise HTTPException(409, "Документ уже формируется. Повторите запрос.") from None
        return winner, False
    record_event(
        db,
        AuditAction.DOCUMENT_GENERATED,
        actor=user,
        candidate_id=candidate.id,
        details=(
            f"document={generation.id} template={template.id} version={version.id} "
            f"revision={generation.revision} sha256={generation.content_sha256[:16]}"
        ),
        commit=False,
    )
    db.commit()
    return generation, True


def _replay(
    db: Session, *, user: User, candidate: Candidate, payload: GenerateRequest
) -> CandidateDocumentGeneration | None:
    existing = db.scalar(
        select(CandidateDocumentGeneration)
        .where(
            CandidateDocumentGeneration.created_by == user.id,
            CandidateDocumentGeneration.idempotency_key == payload.idempotency_key,
        )
        .execution_options(populate_existing=True)
    )
    if existing is None:
        return None
    if (
        existing.candidate_id != candidate.id
        or existing.template_version_id != payload.template_version_id
    ):
        raise HTTPException(409, "Ключ идемпотентности уже использован для другого запроса.")
    return existing


def content_digest(text: str) -> str:
    import hashlib

    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def generation_out(row: CandidateDocumentGeneration) -> GenerationOut:
    return GenerationOut.model_validate(row)


def generations_page(
    db: Session, candidate: Candidate, *, limit: int, offset: int
) -> GenerationsOut:
    total = (
        db.scalar(
            select(func.count())
            .select_from(CandidateDocumentGeneration)
            .where(CandidateDocumentGeneration.candidate_id == candidate.id)
        )
        or 0
    )
    rows = db.scalars(
        select(CandidateDocumentGeneration)
        .where(CandidateDocumentGeneration.candidate_id == candidate.id)
        .order_by(CandidateDocumentGeneration.revision.desc())
        .limit(limit)
        .offset(offset)
    ).all()
    return GenerationsOut(
        items=[generation_out(row) for row in rows], total=total, limit=limit, offset=offset
    )


def generation_for(
    db: Session, candidate: Candidate, generation_id: UUID
) -> CandidateDocumentGeneration:
    row = db.scalar(
        select(CandidateDocumentGeneration)
        .where(
            CandidateDocumentGeneration.id == generation_id,
            CandidateDocumentGeneration.candidate_id == candidate.id,
        )
        .execution_options(populate_existing=True)
    )
    if row is None:
        raise HTTPException(404, "Документ не найден.")
    return row


def download_payload(row: CandidateDocumentGeneration, *, format: str) -> tuple[str, str, bytes]:
    """Return ``(media_type, filename, content)`` for an immutable snapshot.

    Filenames carry no personal data (kind + per-candidate revision only), and
    the HTML artifact is standalone: it is meant to be printed to PDF by the
    browser, which is why no server-side PDF/DOCX conversion exists in phase 16.
    """
    if format not in DOWNLOAD_FORMATS:
        raise HTTPException(422, "Поддерживаются форматы html и txt.")
    kind = sanitize_value(row.kind, max_length=32) or "document"
    filename = f"document-{kind}-rev{row.revision}.{format}"
    content = row.body_html if format == "html" else row.body_text
    return _CONTENT_TYPES[format], filename, content.encode("utf-8")


def candidate_for_user(
    db: Session, candidate_id: UUID, user: User, *, mutate: bool = False
) -> Candidate:
    """Phase 11 access rules (404 for a foreign or deleted candidate)."""
    candidate = candidate_for(db, candidate_id, user, mutate=mutate)
    if not can_access(db, user, candidate):
        raise HTTPException(404, "Кандидат не найден.")
    return candidate
