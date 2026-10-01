"""Versioned document templates and generated documents (phase 16).

Textual MVP of roadmap stage 6: administrators (or `document_lists_manage`)
manage versions of textual templates through the interface; employees see the
published version and can render a document for a candidate they may access.
No delivery to candidates. Plain-text and ``.docx`` files may be imported as
a draft (:mod:`app.template_import`); the file itself is never stored, only
the validated text that was read out of it. PDF is refused — it describes a
page, not text — and the refusal says what to do instead.
"""

from uuid import UUID

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    Query,
    Request,
    Response,
    UploadFile,
    status,
)
from sqlalchemy.orm import Session

from app.audit import record_event
from app.config import Settings
from app.db import get_db
from app.deps import get_current_user, get_settings_from_request
from app.document_templates import (
    activate_version,
    add_imported_version,
    add_version,
    archive_version,
    candidate_for_user,
    create_template,
    download_payload,
    find_same_name_template,
    generate,
    generation_for,
    generations_page,
    list_templates,
    preview,
    rename_template,
    require_manage,
    stage_label,
)
from app.library import (
    DEMO_PLACEHOLDER_VALUES,
    active_material_or_404,
    audit_material_event,
    list_materials,
    material_detail,
    material_payload,
)
from app.models import AuditAction, CandidateStage, User
from app.template_import import read_template_file, read_upload_limited
from app.template_render import TemplateContentError, placeholder_catalog, render_document
from app.template_schemas import (
    GenerateRequest,
    GenerationOut,
    GenerationPreview,
    GenerationsOut,
    LibraryMaterialDetail,
    LibraryMaterialsOut,
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


@router.post("/document-templates/import", response_model=TemplateOut, status_code=201)
async def import_template(
    kind: str = Form(...),
    name: str = Form(""),
    scope: str = Form(""),
    category: str = Form(""),
    summary: str = Form(""),
    force_new: str = Form(""),
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> TemplateOut:
    """Create a template draft from an uploaded text or ``.docx`` file.

    The upload is never written to disk: it is read once, size-capped,
    extension-checked and decoded, and only the validated text becomes the
    body. ``read_template_file`` raises a message that is safe to show, which
    becomes a 422 here.

    Re-import protection: when a template with the same kind and name already
    exists and ``force_new`` is not set to ``"1"``, the endpoint answers 409
    with a structured detail naming the existing material. The interface uses
    it to ask whether the file should become a **new version** of the existing
    material (``POST /document-templates/{id}/import-version``) or a
    deliberately separate one (repeat with ``force_new=1``). A plain retry can
    therefore never quietly breed look-alike templates.
    """
    require_manage(db, user)
    try:
        payload_bytes = await read_upload_limited(file)
        imported = read_template_file(payload_bytes, file.filename, title_hint=name)
        created = TemplateCreate(
            kind=kind,
            scope=CandidateStage(scope) if scope else None,
            name=name.strip() or imported.title,
            title=imported.title,
            body=imported.body,
            category=category,
            summary=summary,
        )
    except (TemplateContentError, ValueError) as exc:
        raise HTTPException(422, str(exc)) from exc
    if force_new != "1":
        existing = find_same_name_template(
            db, kind=created.kind, name=created.name, scope=created.scope or ""
        )
        if existing is not None:
            # Human-readable stage for the message; the structured `existing`
            # keeps the raw key (the SPA renders it with its own labels).
            scope_note = (
                f" (этап: {stage_label(existing.scope)})" if existing.scope else ""
            )
            raise HTTPException(
                409,
                {
                    "message": (
                        f"Материал «{existing.name}»{scope_note} уже существует. "
                        "Создать новую версию существующего материала или "
                        "отдельный материал?"
                    ),
                    "existing": {
                        "id": str(existing.id),
                        "name": existing.name,
                        "kind": existing.kind,
                        # Scope is part of the identity the user must see to
                        # make a meaningful choice between «новая версия» and
                        # «отдельный материал». The value is a raw funnel
                        # stage key ("" = вся база); the interface renders it
                        # with its own Russian stage labels.
                        "scope": existing.scope,
                        "revision": existing.revision,
                    },
                },
            )
    return create_template(
        db,
        user,
        created,
        origin=f"import={imported.extension} source={imported.original_name[:60]}",
    )


@router.post(
    "/document-templates/{template_id}/import-version", response_model=TemplateOut, status_code=201
)
async def import_template_version(
    template_id: UUID,
    expected_revision: int = Form(...),
    title: str = Form(""),
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> TemplateOut:
    """Add a draft version to an existing template from an uploaded file.

    The explicit «new version of an existing material» path of the duplicate
    question. Same locking and audit rules as a manual «Новая версия».
    """
    require_manage(db, user)
    try:
        payload_bytes = await read_upload_limited(file)
        imported = read_template_file(payload_bytes, file.filename, title_hint=title)
    except TemplateContentError as exc:
        raise HTTPException(422, str(exc)) from exc
    return add_imported_version(
        db,
        user,
        template_id,
        title=imported.title,
        body=imported.body,
        expected_revision=expected_revision,
        origin=f"import={imported.extension} source={imported.original_name[:60]}",
    )


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


# --- «Библиотека HR»: read-only material screens ------------------------------
#
# The library reuses the template visibility rule (only published versions,
# for every authenticated employee) and the same audit discipline as the
# candidate download: ids and format in the audit row, never material text.
# Draft materials answer 404 here even for managers — drafts live in
# «Управление материалами».

# Stricter than the candidate download: `sandbox` additionally blocks scripts
# and forms inside the served document, which only styles anyway.
_LIBRARY_HTML_CSP = "default-src 'none'; style-src 'unsafe-inline'; sandbox"


@router.get("/library/materials", response_model=LibraryMaterialsOut)
def library_materials(
    db: Session = Depends(get_db), user: User = Depends(get_current_user)
) -> LibraryMaterialsOut:
    """Card catalog of published materials (available to every role)."""
    return LibraryMaterialsOut.model_validate(list_materials(db, user))


@router.get("/library/materials/{material_id}", response_model=LibraryMaterialDetail)
def library_material(
    material_id: UUID,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> LibraryMaterialDetail:
    """Read-only view of one published material with a safe demo rendering."""
    return LibraryMaterialDetail.model_validate(material_detail(db, material_id))


@router.get("/library/materials/{material_id}/download")
def download_library_material(
    material_id: UUID,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    format: str = Query("html"),
) -> Response:
    """Download a safe copy of a material (HTML for print-to-PDF, or text).

    Audited with parameters only. The content is rendered with impersonal
    demo values, so a shared copy never carries a real candidate's data.
    """
    template, version = active_material_or_404(db, material_id)
    media_type, filename, content = material_payload(template, version, format=format)
    audit_material_event(
        db,
        action=AuditAction.LIBRARY_MATERIAL_DOWNLOADED,
        user=user,
        template=template,
        version=version,
        client_ip=client_ip(request),
        client_user_agent=user_agent(request.headers),
        format=format,
    )
    headers = {
        "Content-Disposition": f'attachment; filename="{filename}"',
        "Cache-Control": "no-store",
        "X-Content-Type-Options": "nosniff",
    }
    if format == "html":
        headers["Content-Security-Policy"] = _LIBRARY_HTML_CSP
    return Response(content=content, media_type=media_type, headers=headers)


@router.get("/library/materials/{material_id}/view")
def view_library_material(
    material_id: UUID,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> Response:
    """Standalone read-only HTML of a material, opened in a new browser tab.

    Served inline (not as an attachment) with a script-blocking CSP. Audited
    like the download: ids and category only.
    """
    template, version = active_material_or_404(db, material_id)
    rendered = render_document(
        title=version.title, body=version.body, values=DEMO_PLACEHOLDER_VALUES
    )
    audit_material_event(
        db,
        action=AuditAction.LIBRARY_MATERIAL_OPENED,
        user=user,
        template=template,
        version=version,
        client_ip=client_ip(request),
        client_user_agent=user_agent(request.headers),
    )
    filename = material_payload(template, version, format="html")[1]
    return Response(
        content=rendered.html.encode("utf-8"),
        media_type="text/html; charset=utf-8",
        headers={
            "Content-Disposition": f'inline; filename="{filename}"',
            "Cache-Control": "no-store",
            "X-Content-Type-Options": "nosniff",
            "Content-Security-Policy": _LIBRARY_HTML_CSP,
        },
    )
