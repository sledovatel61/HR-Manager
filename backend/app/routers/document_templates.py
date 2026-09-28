"""Versioned document templates and generated documents (phase 16).

Textual MVP of roadmap stage 6: administrators (or `document_lists_manage`)
manage versions of textual templates through the interface; employees see the
published version and can render a document for a candidate they may access.
No files, no PDF/DOCX conversion and no delivery to candidates.
"""

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from sqlalchemy.orm import Session

from app.audit import record_event
from app.config import Settings
from app.db import get_db
from app.deps import get_current_user, get_settings_from_request
from app.document_templates import (
    activate_version,
    add_version,
    archive_version,
    candidate_for_user,
    create_template,
    download_payload,
    generate,
    generation_for,
    generations_page,
    list_templates,
    preview,
    rename_template,
    require_manage,
)
from app.models import AuditAction, User
from app.template_render import placeholder_catalog
from app.template_schemas import (
    GenerateRequest,
    GenerationOut,
    GenerationPreview,
    GenerationsOut,
    NewTemplateVersion,
    PlaceholderOut,
    PlaceholdersOut,
    PreviewOut,
    PreviewRequest,
    TemplateAction,
    TemplateCreate,
    TemplateOut,
    TemplateRename,
    TemplatesOut,
)
from app.utils import client_ip, user_agent

router = APIRouter(tags=["document-templates"])


@router.get("/document-templates", response_model=TemplatesOut)
def templates(
    db: Session = Depends(get_db), user: User = Depends(get_current_user)
) -> TemplatesOut:
    return list_templates(db, user)


@router.get("/document-templates/placeholders", response_model=PlaceholdersOut)
def placeholders(_user: User = Depends(get_current_user)) -> PlaceholdersOut:
    """The allowlist catalog. Tokens are public; values never are."""
    return PlaceholdersOut(items=[PlaceholderOut(**item) for item in placeholder_catalog()])


@router.post("/document-templates", response_model=TemplateOut, status_code=201)
def create(
    payload: TemplateCreate,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> TemplateOut:
    require_manage(db, user)
    return create_template(db, user, payload)


@router.patch("/document-templates/{template_id}", response_model=TemplateOut)
def rename(
    template_id: UUID,
    payload: TemplateRename,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> TemplateOut:
    require_manage(db, user)
    return rename_template(db, user, template_id, payload)


@router.post(
    "/document-templates/{template_id}/versions", response_model=TemplateOut, status_code=201
)
def new_version(
    template_id: UUID,
    payload: NewTemplateVersion,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> TemplateOut:
    require_manage(db, user)
    return add_version(db, user, template_id, payload)


@router.post("/document-templates/{template_id}/versions/{version_id}/{operation}")
def version_action(
    template_id: UUID,
    version_id: UUID,
    operation: str,
    payload: TemplateAction,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> TemplateOut:
    require_manage(db, user)
    if operation == "activate":
        return activate_version(db, user, template_id, version_id, payload.expected_revision)
    if operation == "archive":
        return archive_version(db, user, template_id, version_id, payload.expected_revision)
    raise HTTPException(404, "Операция не найдена.")


@router.post("/candidates/{candidate_id}/generated-documents/preview", response_model=PreviewOut)
def preview_document(
    candidate_id: UUID,
    payload: PreviewRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    settings: Settings = Depends(get_settings_from_request),
) -> PreviewOut:
    """Render without saving; the candidate sees nothing until the HR saves it."""
    candidate = candidate_for_user(db, candidate_id, user)
    return preview(
        db,
        user=user,
        candidate=candidate,
        version_id=payload.template_version_id,
        default_timezone=settings.notification_default_timezone,
    )


@router.post(
    "/candidates/{candidate_id}/generated-documents",
    response_model=GenerationPreview,
    status_code=201,
)
def generate_document(
    candidate_id: UUID,
    payload: GenerateRequest,
    response: Response,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    settings: Settings = Depends(get_settings_from_request),
) -> GenerationPreview:
    candidate = candidate_for_user(db, candidate_id, user, mutate=True)
    row, created = generate(
        db,
        user=user,
        candidate=candidate,
        payload=payload,
        default_timezone=settings.notification_default_timezone,
    )
    if not created:
        response.status_code = status.HTTP_200_OK
    return GenerationPreview.model_validate(row)


@router.get("/candidates/{candidate_id}/generated-documents", response_model=GenerationsOut)
def generated_documents(
    candidate_id: UUID,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
) -> GenerationsOut:
    candidate = candidate_for_user(db, candidate_id, user)
    return generations_page(db, candidate, limit=limit, offset=offset)


@router.get(
    "/candidates/{candidate_id}/generated-documents/{generation_id}",
    response_model=GenerationOut,
)
def generated_document(
    candidate_id: UUID,
    generation_id: UUID,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> GenerationOut:
    candidate = candidate_for_user(db, candidate_id, user)
    return GenerationOut.model_validate(generation_for(db, candidate, generation_id))


@router.get("/candidates/{candidate_id}/generated-documents/{generation_id}/download")
def download_generated_document(
    candidate_id: UUID,
    generation_id: UUID,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    format: str = Query("html"),
) -> Response:
    """Download the immutable artifact. HTML is meant to be printed to PDF.

    Audited with parameters only: no document text and no personal data ever
    reach the audit row or the logs.
    """
    candidate = candidate_for_user(db, candidate_id, user)
    row = generation_for(db, candidate, generation_id)
    media_type, filename, content = download_payload(row, format=format)
    record_event(
        db,
        AuditAction.DOCUMENT_DOWNLOADED,
        actor=user,
        candidate_id=candidate.id,
        ip_address=client_ip(request),
        user_agent=user_agent(request.headers),
        details=(
            f"document={row.id} template={row.template_id} version={row.template_version_id} "
            f"revision={row.revision} format={format} sha256={row.content_sha256[:16]}"
        ),
        commit=True,
    )
    headers = {
        "Content-Disposition": f'attachment; filename="{filename}"',
        "Cache-Control": "no-store",
        "X-Content-Type-Options": "nosniff",
    }
    if format == "html":
        headers["Content-Security-Policy"] = "default-src 'none'; style-src 'unsafe-inline'"
    return Response(content=content, media_type=media_type, headers=headers)
