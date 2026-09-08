"""Versioned content and candidate receipt facts. No files or free-form notes."""

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_db
from app.deps import get_current_user
from app.document_schemas import (
    ApplyList,
    CandidateDocumentsOut,
    ExpectedVersion,
    ItemUpdate,
    ListCreate,
    ListOut,
    ListsOut,
    NewVersion,
    VersionOut,
)
from app.documents import (
    advisory,
    apply_version,
    audit,
    can_manage,
    candidate_for,
    current_set,
    documents_out,
)
from app.models import CandidateDocumentItem, DocumentList, DocumentListVersion, User
from app.utils import utc_now

router = APIRouter(tags=["documents"])


def manager(db: Session, user: User) -> None:
    if not can_manage(db, user):
        raise HTTPException(403, "Нет права управления списками документов.")


def parent(db: Session, list_id: UUID, expected: int) -> DocumentList:
    row = db.scalar(
        select(DocumentList)
        .where(DocumentList.id == list_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if row is None:
        raise HTTPException(404, "Список не найден.")
    if row.version != expected:
        raise HTTPException(409, "Версия списка изменилась. Обновите данные.")
    row.version += 1
    return row


def list_out(db: Session, row: DocumentList, *, manage: bool) -> ListOut:
    query = select(DocumentListVersion).where(DocumentListVersion.list_id == row.id)
    if not manage:
        query = query.where(DocumentListVersion.state == "published")
    return ListOut(
        id=row.id,
        stage=row.stage,
        version=row.version,
        versions=[
            VersionOut.model_validate(v)
            for v in db.scalars(query.order_by(DocumentListVersion.number.desc()))
        ],
    )


@router.get("/document-lists", response_model=ListsOut)
def lists(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    limit: int = Query(100, ge=1, le=100),
    offset: int = Query(0, ge=0),
) -> ListsOut:
    manage = can_manage(db, user)
    query = select(DocumentList).order_by(DocumentList.created_at.desc(), DocumentList.id)
    if not manage:
        query = query.where(
            DocumentList.id.in_(
                select(DocumentListVersion.list_id).where(DocumentListVersion.state == "published")
            )
        )
    return ListsOut(
        items=[
            list_out(db, row, manage=manage)
            for row in db.scalars(query.limit(limit).offset(offset))
        ],
        can_manage=manage,
    )


@router.post("/document-lists", response_model=ListOut, status_code=201)
def create(
    payload: ListCreate, db: Session = Depends(get_db), user: User = Depends(get_current_user)
) -> ListOut:
    manager(db, user)
    row = DocumentList(stage=payload.stage.value if payload.stage else "", author_id=user.id)
    db.add(row)
    db.flush()
    version = DocumentListVersion(
        list_id=row.id,
        number=1,
        stage=row.stage,
        author_id=user.id,
        **payload.model_dump(mode="json", exclude={"stage"}),
    )
    db.add(version)
    db.flush()
    audit(db, user, f"create list={row.id} version={version.id}")
    db.commit()
    return list_out(db, row, manage=True)


@router.post("/document-lists/{list_id}/versions", response_model=ListOut, status_code=201)
def new_version(
    list_id: UUID,
    payload: NewVersion,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> ListOut:
    manager(db, user)
    row = parent(db, list_id, payload.expected_version)
    # Parent counter is also an allocation counter; gaps after publication are intentional.
    version = DocumentListVersion(
        list_id=row.id,
        number=row.version,
        stage=row.stage,
        author_id=user.id,
        **payload.model_dump(mode="json", exclude={"expected_version"}),
    )
    db.add(version)
    db.flush()
    audit(db, user, f"draft list={row.id} version={version.id}")
    db.commit()
    return list_out(db, row, manage=True)


@router.post("/document-lists/{list_id}/versions/{version_id}/{operation}", response_model=ListOut)
def transition(
    list_id: UUID,
    version_id: UUID,
    operation: str,
    payload: ExpectedVersion,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> ListOut:
    manager(db, user)
    if operation not in ("publish", "archive"):
        raise HTTPException(404, "Операция не найдена.")
    # Global publication lock FIRST, including lists with no published predecessor.
    advisory(db, "document-publication")
    row = parent(db, list_id, payload.expected_version)
    version = db.get(DocumentListVersion, version_id)
    if version is None or version.list_id != row.id:
        raise HTTPException(404, "Версия не найдена.")
    if operation == "publish":
        if version.state != "draft":
            raise HTTPException(409, "Публиковать можно только черновик.")
        for old in db.scalars(
            select(DocumentListVersion)
            .where(DocumentListVersion.stage == row.stage, DocumentListVersion.state == "published")
            .with_for_update()
        ):
            old.state = "archived"
            if old.list_id != row.id:
                other = db.scalar(
                    select(DocumentList)
                    .where(DocumentList.id == old.list_id)
                    .with_for_update()
                    .execution_options(populate_existing=True)
                )
                assert other is not None
                other.version += 1
        db.flush()  # Release unique scope before assigning the new current version.
        version.state = "published"
        version.published_at = utc_now()
    else:
        if version.state == "archived":
            raise HTTPException(409, "Версия уже архивирована.")
        version.state = "archived"
    audit(db, user, f"{operation} list={row.id} version={version.id}")
    db.commit()
    return list_out(db, row, manage=True)


@router.get("/candidates/{candidate_id}/documents", response_model=CandidateDocumentsOut)
def candidate_documents(
    candidate_id: UUID, db: Session = Depends(get_db), user: User = Depends(get_current_user)
) -> CandidateDocumentsOut:
    return documents_out(db, candidate_for(db, candidate_id, user))


@router.post("/candidates/{candidate_id}/documents", response_model=CandidateDocumentsOut)
def apply(
    candidate_id: UUID,
    payload: ApplyList,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> CandidateDocumentsOut:
    candidate = candidate_for(db, candidate_id, user, mutate=True)
    apply_version(db, candidate, user, payload.version_id, payload.expected_revision)
    db.commit()
    return documents_out(db, candidate)


@router.patch("/candidates/{candidate_id}/documents/{key}", response_model=CandidateDocumentsOut)
def item_state(
    candidate_id: UUID,
    key: str,
    payload: ItemUpdate,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> CandidateDocumentsOut:
    candidate = candidate_for(db, candidate_id, user, mutate=True)
    snapshot = current_set(db, candidate.id)
    if snapshot is None or snapshot.id != payload.set_id:
        raise HTTPException(409, "Список кандидата уже изменён.")
    item = db.get(CandidateDocumentItem, (snapshot.id, key))
    if item is None:
        raise HTTPException(404, "Позиция не найдена.")
    if item.version != payload.expected_version:
        raise HTTPException(409, "Статус уже изменён. Обновите данные.")
    item.state, item.version = payload.state, item.version + 1
    item.changed_by, item.changed_at = user.id, utc_now()
    audit(
        db,
        user,
        f"receipt set={snapshot.id} key={item.key} state={item.state} revision={item.version}",
        candidate,
    )
    db.commit()
    return documents_out(db, candidate)
