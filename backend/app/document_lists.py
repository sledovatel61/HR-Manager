"""Document list management (Phase 11).

Business logic for versioned document lists, candidate document bindings
and the per-item missing/received tracking with optimistic concurrency.
"""

from __future__ import annotations

import logging
from datetime import datetime
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.audit import record_event
from app.models import (
    AuditAction,
    Candidate,
    CandidateDocumentItem,
    CandidateDocumentItemStatus,
    CandidateDocumentList,
    DocumentList,
    DocumentListVersion,
    DocumentListVersionStatus,
    User,
)
from app.utils import utc_now

logger = logging.getLogger(__name__)


def get_list_with_versions(db: Session, list_id: UUID) -> DocumentList | None:
    """Load a document list with all versions eagerly."""
    return db.get(DocumentList, list_id)


def list_document_lists(db: Session) -> list[DocumentList]:
    """Return all document lists (admin view)."""
    return list(
        db.scalars(
            select(DocumentList).order_by(DocumentList.created_at.desc())
        ).all()
    )


def create_document_list(
    db: Session,
    *,
    title: str,
    description: str | None,
    list_scope: dict | None,
    items: list[dict],
    author: User,
) -> tuple[DocumentList, DocumentListVersion]:
    """Create a document list with an initial draft version (v1)."""
    dl = DocumentList(
        title=title,
        description=description,
        list_scope=list_scope,
        author_user_id=author.id,
    )
    db.add(dl)
    db.flush()  # dl.id is available

    version = DocumentListVersion(
        document_list_id=dl.id,
        version_number=1,
        status=DocumentListVersionStatus.DRAFT,
        items=items,
        created_by_user_id=author.id,
    )
    db.add(version)
    db.flush()
    return dl, version


def update_draft_items(
    db: Session,
    *,
    version: DocumentListVersion,
    items: list[dict],
) -> DocumentListVersion:
    """Update a draft version's items (reject if not draft)."""
    if version.status != DocumentListVersionStatus.DRAFT:
        raise ValueError("Только черновик можно редактировать.")
    version.items = items
    return version


def create_new_draft_version(
    db: Session,
    *,
    dl: DocumentList,
    items: list[dict],
    author: User,
) -> DocumentListVersion:
    """Create a new draft version from the current state.

    The new version gets the next sequential number. Used when editing
    a published list: the published version stays immutable.
    """
    max_ver = db.execute(
        select(func.max(DocumentListVersion.version_number))
        .where(DocumentListVersion.document_list_id == dl.id)
    ).scalar() or 0

    version = DocumentListVersion(
        document_list_id=dl.id,
        version_number=max_ver + 1,
        status=DocumentListVersionStatus.DRAFT,
        items=items,
        created_by_user_id=author.id,
    )
    db.add(version)
    db.flush()
    return version


def publish_version(
    db: Session,
    *,
    version: DocumentListVersion,
    published_by: User,
) -> DocumentListVersion:
    """Atomically publish one draft version.

    Any previously published version in the same list is archived first.
    The partial unique index (one published per list) is the DB backstop.
    """
    if version.status != DocumentListVersionStatus.DRAFT:
        raise ValueError("Только черновик можно опубликовать.")

    # Archive any currently published version of the same list.
    old_published = db.execute(
        select(DocumentListVersion).where(
            DocumentListVersion.document_list_id == version.document_list_id,
            DocumentListVersion.status == DocumentListVersionStatus.PUBLISHED,
        )
    ).scalars().all()
    now = utc_now()
    for old in old_published:
        old.status = DocumentListVersionStatus.ARCHIVED
        old.archived_at = now

    version.status = DocumentListVersionStatus.PUBLISHED
    version.published_at = now
    version.published_by_user_id = published_by.id
    return version


def archive_version(
    db: Session,
    *,
    version: DocumentListVersion,
) -> DocumentListVersion:
    """Archive a published version."""
    if version.status != DocumentListVersionStatus.PUBLISHED:
        raise ValueError("Только опубликованную версию можно архивировать.")
    version.status = DocumentListVersionStatus.ARCHIVED
    version.archived_at = utc_now()
    return version


def apply_list_to_candidate(
    db: Session,
    *,
    candidate: Candidate,
    version: DocumentListVersion,
    applied_by: User,
) -> CandidateDocumentList:
    """Apply a published version to a candidate (exact snapshot).

    Creates CandidateDocumentItem rows for each item in the version.
    If the candidate already has this list applied, the binding is
    updated to the new version (items are recreated).
    """
    if version.status != DocumentListVersionStatus.PUBLISHED:
        raise ValueError("Можно применить только опубликованную версию.")

    dl_id = version.document_list_id
    existing = db.get(CandidateDocumentList, (candidate.id, dl_id))
    now = utc_now()

    if existing is not None:
        # Update to new version: remove old items and recreate.
        db.execute(
            select(CandidateDocumentItem)
            .where(
                CandidateDocumentItem.candidate_id == candidate.id,
                CandidateDocumentItem.document_list_id == dl_id,
            )
        )
        # Delete old items.
        old_items = db.execute(
            select(CandidateDocumentItem).where(
                CandidateDocumentItem.candidate_id == candidate.id,
                CandidateDocumentItem.document_list_id == dl_id,
            )
        ).scalars().all()
        for item in old_items:
            db.delete(item)

        existing.version_id = version.id
        existing.version_number = version.version_number
        existing.applied_at = now
        existing.applied_by_user_id = applied_by.id
        binding = existing
    else:
        binding = CandidateDocumentList(
            candidate_id=candidate.id,
            document_list_id=dl_id,
            version_id=version.id,
            version_number=version.version_number,
            applied_at=now,
            applied_by_user_id=applied_by.id,
        )
        db.add(binding)

    # Create per-item rows from the version snapshot.
    items_list = version.items if isinstance(version.items, list) else []
    for item_def in items_list:
        key = item_def.get("key", "")
        title = item_def.get("title", key)
        mandatory = item_def.get("mandatory", True)
        cdi = CandidateDocumentItem(
            candidate_id=candidate.id,
            document_list_id=dl_id,
            item_key=key,
            title=title,
            mandatory=mandatory,
            status=CandidateDocumentItemStatus.MISSING,
        )
        db.add(cdi)

    db.flush()
    return binding


def get_candidate_documents(
    db: Session,
    *,
    candidate_id: UUID,
) -> list[CandidateDocumentList]:
    """Return all applied document lists for a candidate."""
    return list(
        db.scalars(
            select(CandidateDocumentList)
            .where(CandidateDocumentList.candidate_id == candidate_id)
            .order_by(CandidateDocumentList.applied_at.desc())
        ).all()
    )


def get_candidate_items(
    db: Session,
    *,
    candidate_id: UUID,
    document_list_id: UUID,
) -> list[CandidateDocumentItem]:
    """Return all items for a candidate's applied document list."""
    return list(
        db.scalars(
            select(CandidateDocumentItem)
            .where(
                CandidateDocumentItem.candidate_id == candidate_id,
                CandidateDocumentItem.document_list_id == document_list_id,
            )
            .order_by(CandidateDocumentItem.item_key)
        ).all()
    )


def get_missing_mandatory_items(
    db: Session,
    *,
    candidate_id: UUID,
    document_list_id: UUID,
) -> list[CandidateDocumentItem]:
    """Return only the missing mandatory items for a candidate+list."""
    return list(
        db.scalars(
            select(CandidateDocumentItem)
            .where(
                CandidateDocumentItem.candidate_id == candidate_id,
                CandidateDocumentItem.document_list_id == document_list_id,
                CandidateDocumentItem.mandatory.is_(True),
                CandidateDocumentItem.status == CandidateDocumentItemStatus.MISSING,
            )
            .order_by(CandidateDocumentItem.item_key)
        ).all()
    )


def count_missing_mandatory(
    db: Session,
    *,
    candidate_id: UUID,
) -> int:
    """Count total missing mandatory items across all applied lists."""
    return int(
        db.execute(
            select(func.count())
            .select_from(CandidateDocumentItem)
            .where(
                CandidateDocumentItem.candidate_id == candidate_id,
                CandidateDocumentItem.mandatory.is_(True),
                CandidateDocumentItem.status == CandidateDocumentItemStatus.MISSING,
            )
        ).scalar() or 0
    )


def update_item_status(
    db: Session,
    *,
    item: CandidateDocumentItem,
    new_status: str,
    expected_version: int,
    changed_by: User,
    now: datetime | None = None,
) -> CandidateDocumentItem:
    """Update one item's status with optimistic concurrency.

    Raises ValueError on version conflict.
    """
    now = now or utc_now()
    if item.version != expected_version:
        raise ValueError(
            f"Конфликт версий: ожидали {expected_version}, текущая {item.version}."
        )

    if new_status == "received":
        item.status = CandidateDocumentItemStatus.RECEIVED
        item.received_at = now
        item.received_by_user_id = changed_by.id
    else:
        item.status = CandidateDocumentItemStatus.MISSING
        item.received_at = None
        item.received_by_user_id = None

    item.version += 1
    return item


def get_published_version(db: Session, list_id: UUID) -> DocumentListVersion | None:
    """Get the currently published version of a document list."""
    return db.execute(
        select(DocumentListVersion).where(
            DocumentListVersion.document_list_id == list_id,
            DocumentListVersion.status == DocumentListVersionStatus.PUBLISHED,
        )
    ).scalar_one_or_none()


def get_draft_version(db: Session, list_id: UUID) -> DocumentListVersion | None:
    """Get the current draft version of a document list."""
    return db.execute(
        select(DocumentListVersion).where(
            DocumentListVersion.document_list_id == list_id,
            DocumentListVersion.status == DocumentListVersionStatus.DRAFT,
        )
    ).scalar_one_or_none()
