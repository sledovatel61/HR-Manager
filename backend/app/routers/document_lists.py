"""Document lists API (phase 11).

* ``/document-lists`` (admin only) — create/edit lists, edit draft items,
  publish/archive versions. Every mutating call is CSRF-protected (session
  dependency) and guarded by optimistic concurrency (``expected_version``
  / ``expected_row_version``) checked under a row lock.
* ``/document-lists/published`` (any authenticated user) — the published
  versions only (what may be applied to a candidate or referenced by a
  rule). Drafts and history are never exposed outside the admin API.

Lists are never physically deleted; a published or applied version cannot
be removed (RESTRICT foreign keys + no delete endpoint). Audit rows carry
ids, version numbers and item counts — never item texts.
"""

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from app.audit import record_event
from app.db import get_db
from app.deps import get_current_user, require_roles
from app.document_lists import (
    DocumentListError,
    ItemSpec,
    archive_version,
    draft_version_for,
    next_version_number,
    publish_version,
    published_version_for,
    replace_draft_items,
)
from app.models import (
    AuditAction,
    DocumentList,
    DocumentListStatus,
    DocumentListVersion,
    User,
    UserRole,
)
from app.schemas import (
    DocumentListCreate,
    DocumentListItemOut,
    DocumentListList,
    DocumentListOut,
    DocumentListSummaryOut,
    DocumentListUpdate,
    DocumentListVersionActionRequest,
    DocumentListVersionItemsUpdate,
    DocumentListVersionOut,
    PublishedDocumentListList,
    PublishedDocumentListOut,
)
from app.utils import client_ip, user_agent, utc_now

router = APIRouter(prefix="/document-lists", tags=["document-lists"])

_admin_only = require_roles(UserRole.ADMIN)


# --- Helpers ---------------------------------------------------------------------


def _parse_uuid(value: str, *, detail: str) -> UUID:
    try:
        return UUID(value)
    except ValueError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=detail) from None


def _list_or_404(db: Session, list_id: str, *, for_update: bool = False) -> DocumentList:
    parsed = _parse_uuid(list_id, detail="Список документов не найден.")
    stmt = select(DocumentList).where(DocumentList.id == parsed)
    if for_update:
        stmt = stmt.with_for_update().execution_options(populate_existing=True)
    document_list = db.execute(stmt).scalar_one_or_none()
    if document_list is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Список документов не найден."
        )
    return document_list


def _version_or_404(
    db: Session, list_id: str, version_id: str, *, for_update: bool = False
) -> DocumentListVersion:
    parsed_list = _parse_uuid(list_id, detail="Список документов не найден.")
    parsed_version = _parse_uuid(version_id, detail="Версия списка не найдена.")
    stmt = select(DocumentListVersion).where(
        DocumentListVersion.id == parsed_version, DocumentListVersion.list_id == parsed_list
    )
    if for_update:
        stmt = stmt.with_for_update().execution_options(populate_existing=True)
    version = db.execute(stmt).scalar_one_or_none()
    if version is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Версия списка не найдена."
        )
    return version


def _audit(
    db: Session,
    request: Request,
    action: AuditAction,
    *,
    actor: User,
    details: str,
) -> None:
    record_event(
        db,
        action,
        actor=actor,
        ip_address=client_ip(request),
        user_agent=user_agent(request.headers),
        details=details,
        commit=False,
    )


def _conflict(detail: str) -> HTTPException:
    return HTTPException(status_code=status.HTTP_409_CONFLICT, detail=detail)


def _version_out(version: DocumentListVersion) -> DocumentListVersionOut:
    return DocumentListVersionOut(
        id=version.id,
        list_id=version.list_id,
        version_number=version.version_number,
        status=version.status.value,
        row_version=version.row_version,
        published_at=version.published_at,
        archived_at=version.archived_at,
        created_at=version.created_at,
        updated_at=version.updated_at,
        items=[
            DocumentListItemOut.model_validate(item)
            for item in sorted(version.items, key=lambda entry: entry.sort_order)
        ],
    )


def _list_out(db: Session, document_list: DocumentList) -> DocumentListOut:
    versions = list(
        db.execute(
            select(DocumentListVersion)
            .where(DocumentListVersion.list_id == document_list.id)
            .options(selectinload(DocumentListVersion.items))
            .order_by(DocumentListVersion.version_number.desc())
        )
        .scalars()
        .all()
    )
    published = next((v for v in versions if v.status == DocumentListStatus.PUBLISHED), None)
    return DocumentListOut(
        id=document_list.id,
        name=document_list.name,
        description=document_list.description,
        scope_position=document_list.scope_position,
        scope_stage=document_list.scope_stage,
        version=document_list.version,
        created_at=document_list.created_at,
        updated_at=document_list.updated_at,
        published_version_id=published.id if published else None,
        published_version_number=published.version_number if published else None,
        versions=[_version_out(v) for v in versions],
    )


def _specs(items: list) -> list[ItemSpec]:
    return [
        ItemSpec(
            item_key=item.item_key,
            name=item.name,
            explanation=item.explanation,
            is_required=item.is_required,
        )
        for item in items
    ]


# --- Published lists (any authenticated user) --------------------------------------


@router.get(
    "/published",
    response_model=PublishedDocumentListList,
    summary="Published document lists (for applying and rules)",
)
def list_published(
    db: Session = Depends(get_db),
    _user: User = Depends(get_current_user),
) -> PublishedDocumentListList:
    rows = db.execute(
        select(DocumentList, DocumentListVersion)
        .join(
            DocumentListVersion,
            (DocumentListVersion.list_id == DocumentList.id)
            & (DocumentListVersion.status == DocumentListStatus.PUBLISHED),
        )
        .options(selectinload(DocumentListVersion.items))
        .order_by(DocumentList.name, DocumentList.created_at)
    ).all()
    return PublishedDocumentListList(
        items=[
            PublishedDocumentListOut(
                id=document_list.id,
                name=document_list.name,
                description=document_list.description,
                scope_position=document_list.scope_position,
                scope_stage=document_list.scope_stage,
                published_version_id=version.id,
                published_version_number=version.version_number,
                items=[
                    DocumentListItemOut.model_validate(item)
                    for item in sorted(version.items, key=lambda entry: entry.sort_order)
                ],
            )
            for document_list, version in rows
        ]
    )


# --- Admin: lists ------------------------------------------------------------------


@router.get("", response_model=DocumentListList, summary="All document lists (admin)")
def list_document_lists(
    db: Session = Depends(get_db),
    _admin: User = Depends(_admin_only),
) -> DocumentListList:
    lists = list(
        db.execute(select(DocumentList).order_by(DocumentList.created_at, DocumentList.id))
        .scalars()
        .all()
    )
    versions = list(
        db.execute(select(DocumentListVersion).order_by(DocumentListVersion.version_number.desc()))
        .scalars()
        .all()
    )
    by_list: dict[UUID, list[DocumentListVersion]] = {}
    for version in versions:
        by_list.setdefault(version.list_id, []).append(version)
    items: list[DocumentListSummaryOut] = []
    for document_list in lists:
        own = by_list.get(document_list.id, [])
        published = next((v for v in own if v.status == DocumentListStatus.PUBLISHED), None)
        draft = next((v for v in own if v.status == DocumentListStatus.DRAFT), None)
        items.append(
            DocumentListSummaryOut(
                id=document_list.id,
                name=document_list.name,
                description=document_list.description,
                scope_position=document_list.scope_position,
                scope_stage=document_list.scope_stage,
                version=document_list.version,
                published_version_id=published.id if published else None,
                published_version_number=published.version_number if published else None,
                draft_version_id=draft.id if draft else None,
                versions_count=len(own),
                created_at=document_list.created_at,
                updated_at=document_list.updated_at,
            )
        )
    return DocumentListList(items=items, total=len(items))


@router.post(
    "",
    response_model=DocumentListOut,
    status_code=status.HTTP_201_CREATED,
    summary="Create a document list with its first draft",
)
def create_document_list(
    payload: DocumentListCreate,
    request: Request,
    db: Session = Depends(get_db),
    admin: User = Depends(_admin_only),
) -> DocumentListOut:
    now = utc_now()
    document_list = DocumentList(
        name=payload.name,
        description=payload.description,
        scope_position=payload.scope_position,
        scope_stage=payload.scope_stage,
        created_by_user_id=admin.id,
        version=1,
        created_at=now,
        updated_at=now,
    )
    db.add(document_list)
    db.flush()
    version = DocumentListVersion(
        list_id=document_list.id,
        version_number=1,
        status=DocumentListStatus.DRAFT,
        row_version=1,
        created_by_user_id=admin.id,
        created_at=now,
        updated_at=now,
    )
    db.add(version)
    db.flush()
    replace_draft_items(db, version, _specs(payload.items))
    db.flush()
    _audit(
        db,
        request,
        AuditAction.DOCUMENT_LIST_CREATED,
        actor=admin,
        details=f"list={document_list.id} version=1 items={len(payload.items)}",
    )
    db.commit()
    return _list_out(db, document_list)


@router.get("/{list_id}", response_model=DocumentListOut, summary="One list with versions")
def get_document_list(
    list_id: str,
    db: Session = Depends(get_db),
    _admin: User = Depends(_admin_only),
) -> DocumentListOut:
    return _list_out(db, _list_or_404(db, list_id))


@router.patch("/{list_id}", response_model=DocumentListOut, summary="Edit the list header")
def update_document_list(
    list_id: str,
    payload: DocumentListUpdate,
    request: Request,
    db: Session = Depends(get_db),
    admin: User = Depends(_admin_only),
) -> DocumentListOut:
    document_list = _list_or_404(db, list_id, for_update=True)
    if document_list.version != payload.expected_version:
        raise _conflict(
            f"Список уже изменён (ожидалась версия {payload.expected_version}, актуальная — "
            f"{document_list.version}). Обновите данные и повторите."
        )
    changes: list[str] = []
    if payload.name is not None and payload.name != document_list.name:
        document_list.name = payload.name
        changes.append("name")
    if payload.description is not None and payload.description != document_list.description:
        document_list.description = payload.description
        changes.append("description")
    if payload.clear_scope_position:
        if document_list.scope_position is not None:
            document_list.scope_position = None
            changes.append("scope_position")
    elif payload.scope_position is not None and payload.scope_position != (
        document_list.scope_position
    ):
        document_list.scope_position = payload.scope_position
        changes.append("scope_position")
    if payload.clear_scope_stage:
        if document_list.scope_stage is not None:
            document_list.scope_stage = None
            changes.append("scope_stage")
    elif payload.scope_stage is not None and payload.scope_stage != document_list.scope_stage:
        document_list.scope_stage = payload.scope_stage
        changes.append("scope_stage")
    if not changes:
        db.rollback()
        return _list_out(db, document_list)
    document_list.version += 1
    document_list.updated_at = utc_now()
    _audit(
        db,
        request,
        AuditAction.DOCUMENT_LIST_UPDATED,
        actor=admin,
        details=f"list={document_list.id} fields={','.join(changes)}",
    )
    db.commit()
    return _list_out(db, document_list)


# --- Admin: versions -----------------------------------------------------------------


@router.post(
    "/{list_id}/versions",
    response_model=DocumentListVersionOut,
    status_code=status.HTTP_201_CREATED,
    summary="Create a new draft version (copy of the published one)",
)
def create_version(
    list_id: str,
    request: Request,
    db: Session = Depends(get_db),
    admin: User = Depends(_admin_only),
) -> DocumentListVersionOut:
    """Start a new draft. At most one draft per list exists at a time
    (409 otherwise); the draft starts as a copy of the published version's
    items so an edit never touches the immutable published rows. The list
    header row is locked so concurrent creations serialize and the unique
    ``(list_id, version_number)`` is the last barrier."""
    document_list = _list_or_404(db, list_id, for_update=True)
    if draft_version_for(db, document_list.id) is not None:
        raise _conflict("У списка уже есть черновик — отредактируйте или опубликуйте его.")
    published = published_version_for(db, document_list.id)
    now = utc_now()
    version = DocumentListVersion(
        list_id=document_list.id,
        version_number=next_version_number(db, document_list.id),
        status=DocumentListStatus.DRAFT,
        row_version=1,
        created_by_user_id=admin.id,
        created_at=now,
        updated_at=now,
    )
    db.add(version)
    db.flush()
    if published is not None:
        replace_draft_items(
            db,
            version,
            [
                ItemSpec(
                    item_key=item.item_key,
                    name=item.name,
                    explanation=item.explanation,
                    is_required=item.is_required,
                )
                for item in sorted(published.items, key=lambda entry: entry.sort_order)
            ],
        )
        db.flush()
    _audit(
        db,
        request,
        AuditAction.DOCUMENT_LIST_VERSION_CREATED,
        actor=admin,
        details=f"list={document_list.id} version={version.version_number}",
    )
    db.commit()
    db.refresh(version)
    return _version_out(version)


@router.put(
    "/{list_id}/versions/{version_id}/items",
    response_model=DocumentListVersionOut,
    summary="Replace the items of a draft version",
)
def update_version_items(
    list_id: str,
    version_id: str,
    payload: DocumentListVersionItemsUpdate,
    request: Request,
    db: Session = Depends(get_db),
    admin: User = Depends(_admin_only),
) -> DocumentListVersionOut:
    version = _version_or_404(db, list_id, version_id, for_update=True)
    if version.status != DocumentListStatus.DRAFT:
        raise _conflict(
            "Опубликованную или архивную версию нельзя менять — создайте новый черновик."
        )
    if version.row_version != payload.expected_row_version:
        raise _conflict(
            f"Версия уже изменена (ожидалась ревизия {payload.expected_row_version}, актуальная — "
            f"{version.row_version}). Обновите данные и повторите."
        )
    replace_draft_items(db, version, _specs(payload.items))
    version.row_version += 1
    version.updated_at = utc_now()
    db.flush()
    _audit(
        db,
        request,
        AuditAction.DOCUMENT_LIST_VERSION_UPDATED,
        actor=admin,
        details=(
            f"list={version.list_id} version={version.version_number} items={len(payload.items)}"
        ),
    )
    db.commit()
    db.refresh(version)
    return _version_out(version)


@router.post(
    "/{list_id}/versions/{version_id}/publish",
    response_model=DocumentListVersionOut,
    summary="Publish a draft (archives the previous published version)",
)
def publish(
    list_id: str,
    version_id: str,
    payload: DocumentListVersionActionRequest,
    request: Request,
    db: Session = Depends(get_db),
    admin: User = Depends(_admin_only),
) -> DocumentListVersionOut:
    # The list header is locked first so two publications of the same list
    # serialize; the partial unique index is the durable backstop.
    _list_or_404(db, list_id, for_update=True)
    version = _version_or_404(db, list_id, version_id, for_update=True)
    if version.row_version != payload.expected_row_version:
        raise _conflict(
            f"Версия уже изменена (ожидалась ревизия {payload.expected_row_version}, актуальная — "
            f"{version.row_version}). Обновите данные и повторите."
        )
    try:
        previous = publish_version(db, version, actor_user_id=admin.id)
    except DocumentListError as exc:
        db.rollback()
        raise _conflict(exc.message) from None
    _audit(
        db,
        request,
        AuditAction.DOCUMENT_LIST_VERSION_PUBLISHED,
        actor=admin,
        details=(
            f"list={version.list_id} version={version.version_number} items={len(version.items)}"
            + (f" archived_version={previous.version_number}" if previous is not None else "")
        ),
    )
    if previous is not None:
        _audit(
            db,
            request,
            AuditAction.DOCUMENT_LIST_VERSION_ARCHIVED,
            actor=admin,
            details=f"list={version.list_id} version={previous.version_number} reason=superseded",
        )
    db.commit()
    db.refresh(version)
    return _version_out(version)


@router.post(
    "/{list_id}/versions/{version_id}/archive",
    response_model=DocumentListVersionOut,
    summary="Archive a version (history is kept)",
)
def archive(
    list_id: str,
    version_id: str,
    payload: DocumentListVersionActionRequest,
    request: Request,
    db: Session = Depends(get_db),
    admin: User = Depends(_admin_only),
) -> DocumentListVersionOut:
    version = _version_or_404(db, list_id, version_id, for_update=True)
    if version.row_version != payload.expected_row_version:
        raise _conflict(
            f"Версия уже изменена (ожидалась ревизия {payload.expected_row_version}, актуальная — "
            f"{version.row_version}). Обновите данные и повторите."
        )
    try:
        archive_version(version)
    except DocumentListError as exc:
        db.rollback()
        raise _conflict(exc.message) from None
    _audit(
        db,
        request,
        AuditAction.DOCUMENT_LIST_VERSION_ARCHIVED,
        actor=admin,
        details=f"list={version.list_id} version={version.version_number} reason=manual",
    )
    db.commit()
    db.refresh(version)
    return _version_out(version)


@router.get(
    "/{list_id}/versions/{version_id}",
    response_model=DocumentListVersionOut,
    summary="One version with items",
)
def get_version(
    list_id: str,
    version_id: str,
    db: Session = Depends(get_db),
    _admin: User = Depends(_admin_only),
) -> DocumentListVersionOut:
    return _version_out(_version_or_404(db, list_id, version_id))


def published_count(db: Session) -> int:
    """Number of published lists (used by the vocabulary endpoint)."""
    return int(
        db.execute(
            select(func.count())
            .select_from(DocumentListVersion)
            .where(DocumentListVersion.status == DocumentListStatus.PUBLISHED)
        ).scalar_one()
    )
