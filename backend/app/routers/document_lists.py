"""Document lists API (Phase 11).

Admin-only document-list management (CRUD, publish, archive) and
candidate-document endpoints for HR/manager/pilot users.

Authorization:
* List management (create/edit/publish/archive) — admin or users with
  explicit scope (pilot_full_access grant).
* Candidate documents — HR (own candidates), manager (all), admin with
  pilot grant.
* Soft-deleted candidates are 404.
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.audit import record_event
from app.db import get_db
from app.deps import get_current_user
from app.document_lists import (
    apply_list_to_candidate,
    archive_version,
    count_missing_mandatory,
    create_document_list,
    create_new_draft_version,
    get_candidate_documents,
    get_candidate_items,
    get_draft_version,
    get_missing_mandatory_items,
    get_published_version,
    list_document_lists,
    publish_version,
    update_draft_items,
    update_item_status,
)
from app.models import (
    AccessGrant,
    AccessGrantScope,
    AuditAction,
    Candidate,
    CandidateDocumentItem,
    CandidateDocumentList,
    DocumentList,
    DocumentListVersion,
    DocumentListVersionStatus,
    User,
    UserRole,
)
from app.schemas import (
    CandidateDocumentListOut,
    CandidateDocumentItemOut,
    CandidateDocumentListsOut,
    DocumentItemStatusUpdate,
    DocumentListCreate,
    DocumentListList,
    DocumentListOut,
    DocumentListVersionOut,
    DocumentListVersionUpdate,
)
from app.utils import client_ip, utc_now, user_agent

router = APIRouter(prefix="/admin/document-lists", tags=["document-lists"])
candidate_router = APIRouter(prefix="/candidates/{candidate_id}/documents", tags=["candidate-documents"])


# --- Admin authorization ---------------------------------------------------

def _require_admin_or_grant(db: Session, user: User) -> None:
    """Only admin (with pilot grant) may manage document lists."""
    if user.role == UserRole.ADMIN:
        grant = db.execute(
            select(AccessGrant.id).where(
                AccessGrant.user_id == user.id,
                AccessGrant.scope == AccessGrantScope.PILOT_FULL_ACCESS,
                AccessGrant.revoked_at.is_(None),
            )
        ).scalar()
        if grant is not None:
            return
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Доступ к управлению списками документов требует пилотного доступа.",
        )
    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail="Только администратор может управлять списками документов.",
    )


def _version_to_out(v: DocumentListVersion) -> DocumentListVersionOut:
    return DocumentListVersionOut(
        id=v.id,
        document_list_id=v.document_list_id,
        version_number=v.version_number,
        status=v.status.value,
        items=v.items if isinstance(v.items, list) else [],
        created_by_user_id=v.created_by_user_id,
        published_at=v.published_at,
        archived_at=v.archived_at,
        created_at=v.created_at,
    )


def _list_to_out(dl: DocumentList, db: Session) -> DocumentListOut:
    pub = get_published_version(db, dl.id)
    draft = get_draft_version(db, dl.id)
    return DocumentListOut(
        id=dl.id,
        title=dl.title,
        description=dl.description,
        list_scope=dl.list_scope,
        author_user_id=dl.author_user_id,
        created_at=dl.created_at,
        updated_at=dl.updated_at,
        published_version=_version_to_out(pub) if pub else None,
        draft_version=_version_to_out(draft) if draft else None,
    )


# --- Admin: document list CRUD --------------------------------------------

@router.get("", response_model=DocumentListList, summary="List all document lists")
def admin_list_document_lists(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> DocumentListList:
    _require_admin_or_grant(db, user)
    lists = list_document_lists(db)
    return DocumentListList(
        items=[_list_to_out(dl, db) for dl in lists],
        total=len(lists),
    )


@router.post(
    "",
    response_model=DocumentListOut,
    status_code=status.HTTP_201_CREATED,
    summary="Create a document list with initial draft version",
)
def admin_create_document_list(
    payload: DocumentListCreate,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> DocumentListOut:
    _require_admin_or_grant(db, user)
    items_dicts = [item.model_dump() for item in payload.items]
    dl, version = create_document_list(
        db,
        title=payload.title,
        description=payload.description,
        list_scope=payload.list_scope,
        items=items_dicts,
        author=user,
    )
    record_event(
        db,
        AuditAction.DOCUMENT_LIST_CREATED,
        actor=user,
        ip_address=client_ip(request),
        user_agent=user_agent(request.headers),
        details=f"list={dl.id}",
        commit=False,
    )
    db.commit()
    db.refresh(dl)
    db.refresh(version)
    return _list_to_out(dl, db)


@router.get("/{list_id}", response_model=DocumentListOut, summary="Get a document list")
def admin_get_document_list(
    list_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> DocumentListOut:
    _require_admin_or_grant(db, user)
    try:
        parsed = UUID(list_id)
    except ValueError:
        raise HTTPException(status_code=404, detail="Список не найден.") from None
    dl = db.get(DocumentList, parsed)
    if dl is None:
        raise HTTPException(status_code=404, detail="Список не найден.")
    return _list_to_out(dl, db)


@router.put(
    "/{list_id}/draft",
    response_model=DocumentListVersionOut,
    summary="Update the draft version's items",
)
def admin_update_draft(
    list_id: str,
    payload: DocumentListVersionUpdate,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> DocumentListVersionOut:
    _require_admin_or_grant(db, user)
    try:
        parsed = UUID(list_id)
    except ValueError:
        raise HTTPException(status_code=404, detail="Список не найден.") from None
    dl = db.get(DocumentList, parsed)
    if dl is None:
        raise HTTPException(status_code=404, detail="Список не найден.")
    # Get or create a draft.
    draft = db.execute(
        select(DocumentListVersion).where(
            DocumentListVersion.document_list_id == dl.id,
            DocumentListVersion.status == DocumentListVersionStatus.DRAFT,
        )
    ).scalar_one_or_none()

    if draft is not None:
        update_draft_items(db, version=draft, items=[item.model_dump() for item in payload.items])
    else:
        draft = create_new_draft_version(
            db, dl=dl, items=[item.model_dump() for item in payload.items], author=user
        )
        record_event(
            db,
            AuditAction.DOCUMENT_LIST_VERSION_CREATED,
            actor=user,
            ip_address=client_ip(request),
            user_agent=user_agent(request.headers),
            details=f"list={dl.id} version={draft.version_number}",
            commit=False,
        )
    db.commit()
    db.refresh(draft)
    return _version_to_out(draft)


@router.post(
    "/{list_id}/versions",
    response_model=DocumentListVersionOut,
    status_code=status.HTTP_201_CREATED,
    summary="Create a new draft version from current published",
)
def admin_create_version(
    list_id: str,
    payload: DocumentListVersionUpdate,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> DocumentListVersionOut:
    _require_admin_or_grant(db, user)
    try:
        parsed = UUID(list_id)
    except ValueError:
        raise HTTPException(status_code=404, detail="Список не найден.") from None
    dl = db.get(DocumentList, parsed)
    if dl is None:
        raise HTTPException(status_code=404, detail="Список не найден.")
    version = create_new_draft_version(
        db, dl=dl, items=[item.model_dump() for item in payload.items], author=user
    )
    record_event(
        db,
        AuditAction.DOCUMENT_LIST_VERSION_CREATED,
        actor=user,
        ip_address=client_ip(request),
        user_agent=user_agent(request.headers),
        details=f"list={dl.id} version={version.version_number}",
        commit=False,
    )
    db.commit()
    db.refresh(version)
    return _version_to_out(version)


@router.post(
    "/{list_id}/versions/{version_id}/publish",
    response_model=DocumentListVersionOut,
    summary="Publish a draft version (atomic: previous published → archived)",
)
def admin_publish_version(
    list_id: str,
    version_id: str,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> DocumentListVersionOut:
    _require_admin_or_grant(db, user)
    try:
        vid = UUID(version_id)
    except ValueError:
        raise HTTPException(status_code=404, detail="Версия не найдена.") from None
    version = db.get(DocumentListVersion, vid)
    if version is None or str(version.document_list_id) != list_id:
        raise HTTPException(status_code=404, detail="Версия не найдена.")
    try:
        publish_version(db, version=version, published_by=user)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from None
    record_event(
        db,
        AuditAction.DOCUMENT_LIST_PUBLISHED,
        actor=user,
        ip_address=client_ip(request),
        user_agent=user_agent(request.headers),
        details=f"list={list_id} version={version.version_number}",
        commit=False,
    )
    db.commit()
    db.refresh(version)
    return _version_to_out(version)


@router.post(
    "/{list_id}/versions/{version_id}/archive",
    response_model=DocumentListVersionOut,
    summary="Archive a published version",
)
def admin_archive_version(
    list_id: str,
    version_id: str,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> DocumentListVersionOut:
    _require_admin_or_grant(db, user)
    try:
        vid = UUID(version_id)
    except ValueError:
        raise HTTPException(status_code=404, detail="Версия не найдена.") from None
    version = db.get(DocumentListVersion, vid)
    if version is None or str(version.document_list_id) != list_id:
        raise HTTPException(status_code=404, detail="Версия не найдена.")
    try:
        archive_version(db, version=version)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from None
    record_event(
        db,
        AuditAction.DOCUMENT_LIST_ARCHIVED,
        actor=user,
        ip_address=client_ip(request),
        user_agent=user_agent(request.headers),
        details=f"list={list_id} version={version.version_number}",
        commit=False,
    )
    db.commit()
    db.refresh(version)
    return _version_to_out(version)


# --- Candidate documents ---------------------------------------------------

def _get_candidate_for_docs(db: Session, candidate_id: str, user: User) -> Candidate:
    """Resolve candidate with visibility (reuses candidate access rules)."""
    try:
        parsed = UUID(candidate_id)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Кандидат не найден."
        ) from None
    candidate = db.get(Candidate, parsed)
    if candidate is None or candidate.deleted_at is not None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Кандидат не найден.")
    # HR works with their own candidates; manager — all; admin with grant.
    if user.role == UserRole.HR:
        if candidate.owner_user_id != user.id:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Кандидат не найден.")
    elif user.role == UserRole.ADMIN:
        grant = db.execute(
            select(AccessGrant.id).where(
                AccessGrant.user_id == user.id,
                AccessGrant.scope == AccessGrantScope.PILOT_FULL_ACCESS,
                AccessGrant.revoked_at.is_(None),
            )
        ).scalar()
        if grant is None:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Доступ к документам кандидатов требует пилотного доступа.",
            )
    return candidate


def _item_to_out(item: CandidateDocumentItem) -> CandidateDocumentItemOut:
    return CandidateDocumentItemOut(
        candidate_id=item.candidate_id,
        document_list_id=item.document_list_id,
        item_key=item.item_key,
        title=item.title,
        mandatory=item.mandatory,
        status=item.status.value,
        received_at=item.received_at,
        received_by_user_id=item.received_by_user_id,
        version=item.version,
    )


@candidate_router.get(
    "",
    response_model=CandidateDocumentListsOut,
    summary="Get all applied document lists for a candidate",
)
def get_candidate_document_lists(
    candidate_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> CandidateDocumentListsOut:
    candidate = _get_candidate_for_docs(db, candidate_id, user)
    bindings = get_candidate_documents(db, candidate_id=candidate.id)
    result_items = []
    for binding in bindings:
        items = get_candidate_items(
            db, candidate_id=candidate.id, document_list_id=binding.document_list_id
        )
        dl = db.get(DocumentList, binding.document_list_id)
        result_items.append(
            CandidateDocumentListOut(
                candidate_id=binding.candidate_id,
                document_list_id=binding.document_list_id,
                document_list_title=dl.title if dl else "—",
                version_id=binding.version_id,
                version_number=binding.version_number,
                applied_at=binding.applied_at,
                applied_by_user_id=binding.applied_by_user_id,
                items=[_item_to_out(item) for item in items],
            )
        )
    missing = count_missing_mandatory(db, candidate_id=candidate.id)
    return CandidateDocumentListsOut(items=result_items, missing_mandatory_total=missing)


@candidate_router.post(
    "/{list_id}/apply",
    response_model=CandidateDocumentListOut,
    status_code=status.HTTP_201_CREATED,
    summary="Apply a published document list to a candidate",
)
def apply_document_list(
    candidate_id: str,
    list_id: str,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> CandidateDocumentListOut:
    candidate = _get_candidate_for_docs(db, candidate_id, user)
    try:
        parsed_list = UUID(list_id)
    except ValueError:
        raise HTTPException(status_code=404, detail="Список не найден.") from None
    version = get_published_version(db, parsed_list)
    if version is None:
        raise HTTPException(status_code=404, detail="Нет опубликованной версии списка.")
    binding = apply_list_to_candidate(
        db, candidate=candidate, version=version, applied_by=user
    )
    record_event(
        db,
        AuditAction.DOCUMENT_LIST_APPLIED,
        actor=user,
        candidate_id=candidate.id,
        ip_address=client_ip(request),
        user_agent=user_agent(request.headers),
        details=f"list={list_id} version={version.version_number}",
        commit=False,
    )
    db.commit()
    items = get_candidate_items(
        db, candidate_id=candidate.id, document_list_id=parsed_list
    )
    dl = db.get(DocumentList, parsed_list)
    return CandidateDocumentListOut(
        candidate_id=binding.candidate_id,
        document_list_id=binding.document_list_id,
        document_list_title=dl.title if dl else "—",
        version_id=binding.version_id,
        version_number=binding.version_number,
        applied_at=binding.applied_at,
        applied_by_user_id=binding.applied_by_user_id,
        items=[_item_to_out(item) for item in items],
    )


@candidate_router.patch(
    "/{list_id}/items/{item_key}",
    response_model=CandidateDocumentItemOut,
    summary="Update one document item's status (optimistic concurrency)",
)
def update_document_item(
    candidate_id: str,
    list_id: str,
    item_key: str,
    payload: DocumentItemStatusUpdate,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> CandidateDocumentItemOut:
    candidate = _get_candidate_for_docs(db, candidate_id, user)
    try:
        parsed_list = UUID(list_id)
    except ValueError:
        raise HTTPException(status_code=404, detail="Список не найден.") from None
    item = db.get(
        CandidateDocumentItem, (candidate.id, parsed_list, item_key)
    )
    if item is None:
        raise HTTPException(status_code=404, detail="Элемент не найден.")
    try:
        update_item_status(
            db,
            item=item,
            new_status=payload.status,
            expected_version=payload.expected_version,
            changed_by=user,
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from None

    action = (
        AuditAction.DOCUMENT_ITEM_RECEIVED
        if payload.status == "received"
        else AuditAction.DOCUMENT_ITEM_REVERTED
    )
    record_event(
        db,
        action,
        actor=user,
        candidate_id=candidate.id,
        ip_address=client_ip(request),
        user_agent=user_agent(request.headers),
        details=f"list={list_id} key={item_key}",
        commit=False,
    )
    db.commit()
    db.refresh(item)
    return _item_to_out(item)


@candidate_router.get(
    "/missing",
    response_model=list[CandidateDocumentItemOut],
    summary="Get only missing mandatory items for a candidate",
)
def get_missing_items(
    candidate_id: str,
    list_id: UUID = Query(description="Document list ID"),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> list[CandidateDocumentItemOut]:
    candidate = _get_candidate_for_docs(db, candidate_id, user)
    items = get_missing_mandatory_items(
        db, candidate_id=candidate.id, document_list_id=list_id
    )
    return [_item_to_out(item) for item in items]
