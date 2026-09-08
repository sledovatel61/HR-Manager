"""Versioned document lists and per-candidate document snapshots (phase 11).

Domain rules implemented here (routers and the rule engine call these):

* a list has immutable versions: only a ``draft`` may be edited; publishing
  is atomic — the previously published version of the same list is
  archived in the same transaction and the partial unique index
  ``uq_document_list_versions_one_published`` is the database-level
  barrier against two concurrent publications; archiving keeps history;
* applying a list to a candidate stores an exact snapshot of the version's
  items (``candidate_document_items``): a later publication never changes
  what was asked from this candidate. At most one current assignment per
  candidate (partial unique index); replacing closes the previous one;
* the receipt state of an item changes under optimistic concurrency
  (``version``): a stale editor receives a conflict, never a silent
  overwrite;
* document request/reminder messages are rendered by the server from the
  currently missing items of the exact applied version and go through the
  phase-10 outbox with a PII-free ``object_snapshot`` (assignment id, list
  and version ids, listed item keys) that the worker re-validates right
  before the provider call.

Only the *fact* of receipt is stored — never files, scans, document
numbers or free-form notes.
"""

import logging
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from sqlalchemy import delete, func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from app.candidate_messages import (
    RenderedMessage,
    queue_candidate_message_all_channels,
    render_candidate_message,
)
from app.config import Settings
from app.models import (
    Candidate,
    CandidateDocumentAssignment,
    CandidateDocumentItem,
    CandidateDocumentStatus,
    DeliveryChannel,
    DocumentList,
    DocumentListItem,
    DocumentListStatus,
    DocumentListVersion,
    NotificationOutbox,
    NotificationSource,
)
from app.notification_service import is_duplicate_key_error, lock_candidate_for_mutation
from app.utils import utc_now

logger = logging.getLogger(__name__)

DOCUMENT_ASSIGNMENT_OBJECT_TYPE = "document_assignment"


class DocumentListError(Exception):
    """Domain error with a safe Russian message and an HTTP-ish class."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


# --- Versions ------------------------------------------------------------------


def published_version_for(db: Session, list_id: UUID) -> DocumentListVersion | None:
    """The single currently published version of a list (or None)."""
    return db.execute(
        select(DocumentListVersion)
        .where(
            DocumentListVersion.list_id == list_id,
            DocumentListVersion.status == DocumentListStatus.PUBLISHED,
        )
        .options(selectinload(DocumentListVersion.items))
    ).scalar_one_or_none()


def draft_version_for(db: Session, list_id: UUID) -> DocumentListVersion | None:
    return db.execute(
        select(DocumentListVersion)
        .where(
            DocumentListVersion.list_id == list_id,
            DocumentListVersion.status == DocumentListStatus.DRAFT,
        )
        .options(selectinload(DocumentListVersion.items))
    ).scalar_one_or_none()


def next_version_number(db: Session, list_id: UUID) -> int:
    current = db.execute(
        select(func.max(DocumentListVersion.version_number)).where(
            DocumentListVersion.list_id == list_id
        )
    ).scalar_one()
    return int(current or 0) + 1


@dataclass(frozen=True)
class ItemSpec:
    item_key: str
    name: str
    explanation: str
    is_required: bool


def replace_draft_items(db: Session, version: DocumentListVersion, items: list[ItemSpec]) -> None:
    """Replace the items of a DRAFT version in place (caller checks status
    and the optimistic row version, then bumps it).

    The old rows are deleted and flushed BEFORE the new ones are added:
    the unit of work would otherwise insert first and collide with the
    ``(version_id, sort_order)`` unique constraint.
    """
    if version.status != DocumentListStatus.DRAFT:
        raise DocumentListError("immutable", "Опубликованную или архивную версию нельзя менять.")
    if version.items:
        db.execute(delete(DocumentListItem).where(DocumentListItem.version_id == version.id))
        version.items.clear()
        db.flush()
        db.expire(version, ["items"])
    for index, item in enumerate(items):
        version.items.append(
            DocumentListItem(
                item_key=item.item_key,
                name=item.name,
                explanation=item.explanation,
                is_required=item.is_required,
                sort_order=index,
            )
        )


def publish_version(
    db: Session, version: DocumentListVersion, *, actor_user_id: UUID, now: datetime | None = None
) -> DocumentListVersion | None:
    """Publish a draft atomically; returns the version it replaced (if any).

    The previous published version is archived in the same transaction.
    The caller must hold the version row lock (``SELECT ... FOR UPDATE``)
    and have checked the optimistic row version. The partial unique index
    is the last-line barrier: a lost race surfaces as
    ``DocumentListError("conflict")`` and leaves the transaction rolled
    back by the caller.
    """
    now = now or utc_now()
    if version.status != DocumentListStatus.DRAFT:
        raise DocumentListError("not_draft", "Опубликовать можно только черновик.")
    if not version.items:
        raise DocumentListError("empty", "Нельзя опубликовать пустой список.")
    previous = db.execute(
        select(DocumentListVersion)
        .where(
            DocumentListVersion.list_id == version.list_id,
            DocumentListVersion.status == DocumentListStatus.PUBLISHED,
            DocumentListVersion.id != version.id,
        )
        .with_for_update()
    ).scalar_one_or_none()
    if previous is not None:
        previous.status = DocumentListStatus.ARCHIVED
        previous.archived_at = now
        previous.row_version += 1
        previous.updated_at = now
        # Flushed BEFORE the new version flips to published: the unit of
        # work orders UPDATEs by primary key, and the partial unique index
        # must never see two published rows even transiently.
        db.flush()
    version.status = DocumentListStatus.PUBLISHED
    version.published_at = now
    version.published_by_user_id = actor_user_id
    version.row_version += 1
    version.updated_at = now
    try:
        db.flush()
    except IntegrityError as exc:
        if not is_duplicate_key_error(exc):
            raise
        raise DocumentListError(
            "conflict", "Другая версия этого списка была опубликована одновременно."
        ) from None
    return previous


def archive_version(version: DocumentListVersion, *, now: datetime | None = None) -> None:
    """Archive a published (or discard a draft) version — history is kept."""
    now = now or utc_now()
    if version.status == DocumentListStatus.ARCHIVED:
        raise DocumentListError("already_archived", "Версия уже в архиве.")
    version.status = DocumentListStatus.ARCHIVED
    version.archived_at = now
    version.row_version += 1
    version.updated_at = now


# --- Candidate assignments -------------------------------------------------------


def current_assignment(db: Session, candidate_id: UUID) -> CandidateDocumentAssignment | None:
    return db.execute(
        select(CandidateDocumentAssignment)
        .where(
            CandidateDocumentAssignment.candidate_id == candidate_id,
            CandidateDocumentAssignment.replaced_at.is_(None),
        )
        .options(
            selectinload(CandidateDocumentAssignment.items).selectinload(
                CandidateDocumentItem.changed_by
            )
        )
    ).scalar_one_or_none()


def assignment_history(db: Session, candidate_id: UUID) -> list[CandidateDocumentAssignment]:
    return list(
        db.execute(
            select(CandidateDocumentAssignment)
            .where(
                CandidateDocumentAssignment.candidate_id == candidate_id,
                CandidateDocumentAssignment.replaced_at.is_not(None),
            )
            .options(
                selectinload(CandidateDocumentAssignment.items).selectinload(
                    CandidateDocumentItem.changed_by
                )
            )
            .order_by(CandidateDocumentAssignment.assigned_at.desc())
        )
        .scalars()
        .all()
    )


def missing_items(
    assignment: CandidateDocumentAssignment, *, required_only: bool
) -> list[CandidateDocumentItem]:
    """Items still missing, in the stable list order."""
    return sorted(
        (
            item
            for item in assignment.items
            if item.status == CandidateDocumentStatus.MISSING
            and (item.is_required or not required_only)
        ),
        key=lambda item: item.sort_order,
    )


def has_missing_required(assignment: CandidateDocumentAssignment | None) -> bool:
    return assignment is not None and bool(missing_items(assignment, required_only=True))


def apply_published_list(
    db: Session,
    *,
    candidate: Candidate,
    list_id: UUID,
    actor_user_id: UUID | None,
    rule_id: UUID | None = None,
    replace: bool = False,
    now: datetime | None = None,
) -> tuple[CandidateDocumentAssignment, CandidateDocumentAssignment | None]:
    """Apply the CURRENT published version of ``list_id`` to the candidate.

    Returns ``(new_assignment, replaced_assignment)``. Raises
    ``DocumentListError``: ``not_published`` (no published version),
    ``already_applied`` (the same version is current), ``exists`` (another
    list/version is current and ``replace`` is false) or ``conflict`` (a
    concurrent application won the unique index). The candidate advisory
    lock serializes concurrent applications on PostgreSQL; the partial
    unique index is the durable backstop.
    """
    now = now or utc_now()
    if candidate.deleted_at is not None:
        raise DocumentListError("candidate_deleted", "Кандидат удалён.")
    lock_candidate_for_mutation(db, candidate.id)
    document_list = db.get(DocumentList, list_id)
    version = published_version_for(db, list_id) if document_list is not None else None
    if document_list is None or version is None:
        raise DocumentListError("not_published", "У списка нет опубликованной версии.")
    current = current_assignment(db, candidate.id)
    if current is not None:
        if current.version_id == version.id:
            raise DocumentListError("already_applied", "Эта версия списка уже применена.")
        if not replace:
            raise DocumentListError(
                "exists",
                "Кандидату уже применён список документов. Подтвердите замену.",
            )
        current.replaced_at = now
        current.replaced_by_user_id = actor_user_id
        db.flush()
    assignment = CandidateDocumentAssignment(
        candidate_id=candidate.id,
        list_id=list_id,
        version_id=version.id,
        version_number=version.version_number,
        list_name_snapshot=document_list.name,
        assigned_by_user_id=actor_user_id,
        assigned_by_rule_id=rule_id,
        assigned_at=now,
    )
    for item in sorted(version.items, key=lambda entry: entry.sort_order):
        assignment.items.append(
            CandidateDocumentItem(
                item_key=item.item_key,
                name_snapshot=item.name,
                explanation_snapshot=item.explanation,
                is_required=item.is_required,
                sort_order=item.sort_order,
                status=CandidateDocumentStatus.MISSING,
                version=1,
            )
        )
    db.add(assignment)
    try:
        db.flush()
    except IntegrityError as exc:
        if not is_duplicate_key_error(exc):
            raise
        raise DocumentListError(
            "conflict", "Список документов был применён одновременно другим пользователем."
        ) from None
    return assignment, current


def set_item_status(
    db: Session,
    *,
    item: CandidateDocumentItem,
    status: CandidateDocumentStatus,
    expected_version: int,
    actor_user_id: UUID,
    now: datetime | None = None,
) -> bool:
    """Change the receipt state under optimistic concurrency.

    Returns True when the row changed, False when the status was already
    the requested one (a no-op). Raises ``DocumentListError("conflict")``
    on a version mismatch. The update is a single conditional UPDATE, so
    two concurrent editors can never both succeed against the same
    version (proven on PostgreSQL by the integration tests).
    """
    now = now or utc_now()
    if item.version != expected_version:
        raise DocumentListError(
            "conflict",
            f"Элемент уже изменён (ожидалась версия {expected_version}, актуальная — "
            f"{item.version}). Обновите данные и повторите.",
        )
    if item.status == status:
        return False
    result = db.execute(
        update(CandidateDocumentItem)
        .where(
            CandidateDocumentItem.id == item.id,
            CandidateDocumentItem.version == expected_version,
        )
        .values(
            status=status,
            version=expected_version + 1,
            changed_by_user_id=actor_user_id,
            changed_at=now,
        )
    )
    if result.rowcount != 1:  # type: ignore[attr-defined]
        raise DocumentListError(
            "conflict", "Элемент уже изменён другим пользователем. Обновите данные и повторите."
        )
    db.refresh(item)
    return True


# --- Document messages from the applied list ---------------------------------------


@dataclass(frozen=True)
class DocumentMessagePlan:
    message: RenderedMessage
    item_keys: list[str]
    snapshot: dict


def plan_document_message(
    *,
    candidate: Candidate,
    assignment: CandidateDocumentAssignment,
    message_type_key: str,
) -> DocumentMessagePlan | None:
    """Render a document request/reminder from the CURRENTLY missing items.

    Returns None when nothing is missing (nothing to ask for). The snapshot
    carries ids and item keys only — no names, no candidate data.
    """
    missing = missing_items(assignment, required_only=False)
    if not missing:
        return None
    message = render_candidate_message(
        message_type_key,
        candidate=candidate,
        documents=[item.name_snapshot for item in missing],
    )
    keys = [item.item_key for item in missing]
    return DocumentMessagePlan(
        message=message,
        item_keys=keys,
        snapshot={
            "assignment_id": str(assignment.id),
            "list_id": str(assignment.list_id),
            "version_id": str(assignment.version_id),
            "version_number": assignment.version_number,
            "item_keys": keys,
        },
    )


def queue_document_message(
    db: Session,
    *,
    candidate: Candidate,
    assignment: CandidateDocumentAssignment,
    message_type_key: str,
    source: NotificationSource,
    dedupe_key: str,
    settings: Settings,
    scheduled_at: datetime | None = None,
    initiator_user_id: UUID | None = None,
    rule_id: UUID | None = None,
    only_channel: DeliveryChannel | None = None,
) -> tuple[list[NotificationOutbox], DocumentMessagePlan | None]:
    """Queue a document message on every allowed channel (or one).

    Returns the queued rows and the plan (None when nothing is missing).
    The consent/channel decision is taken from the CURRENT state; the
    worker re-validates consent, channel and the still-missing items right
    before the provider call.
    """
    plan = plan_document_message(
        candidate=candidate, assignment=assignment, message_type_key=message_type_key
    )
    if plan is None:
        return [], None
    rows = queue_candidate_message_all_channels(
        db,
        candidate=candidate,
        message=plan.message,
        message_type_key=message_type_key,
        source=source,
        initiator_user_id=initiator_user_id,
        dedupe_key=dedupe_key,
        scheduled_at=scheduled_at,
        settings=settings,
        only_channel=only_channel,
        rule_id=rule_id,
        object_type=DOCUMENT_ASSIGNMENT_OBJECT_TYPE,
        object_id=assignment.id,
        object_version=None,
        object_snapshot=plan.snapshot,
    )
    return rows, plan


@dataclass(frozen=True)
class SendTimeCheck:
    """Result of the worker's send-time re-validation of a document row."""

    skip_class: str | None
    body: str | None = None


DOCUMENTS_COMPLETE_ERROR_CLASS = "documents_complete"
ASSIGNMENT_REPLACED_ERROR_CLASS = "assignment_replaced"


def revalidate_document_message(
    db: Session, *, candidate: Candidate, row: NotificationOutbox
) -> SendTimeCheck:
    """Send-time check of a document request/reminder queued from a list.

    * the assignment must still be the candidate's current one;
    * at least one of the item keys listed in the message must still be
      missing — otherwise the row is skipped without any network call;
    * when only a subset is still missing, the body is re-rendered from
      the remaining items (the exact text that leaves is what the history
      stores — the caller persists it on the row).

    Legacy phase-10 rows (free-form document names, no snapshot) are not
    subject to this check.
    """
    if row.object_type != DOCUMENT_ASSIGNMENT_OBJECT_TYPE or row.object_id is None:
        return SendTimeCheck(skip_class=None, body=row.body)
    snapshot = row.object_snapshot or {}
    listed = snapshot.get("item_keys") if isinstance(snapshot, dict) else None
    assignment = db.get(CandidateDocumentAssignment, row.object_id)
    if (
        assignment is None
        or assignment.candidate_id != candidate.id
        or assignment.replaced_at is not None
    ):
        return SendTimeCheck(skip_class=ASSIGNMENT_REPLACED_ERROR_CLASS)
    if not isinstance(listed, list) or not listed:
        return SendTimeCheck(skip_class=DOCUMENTS_COMPLETE_ERROR_CLASS)
    listed_keys = {str(key) for key in listed}
    still_missing = [
        item
        for item in missing_items(assignment, required_only=False)
        if item.item_key in listed_keys
    ]
    if not still_missing:
        return SendTimeCheck(skip_class=DOCUMENTS_COMPLETE_ERROR_CLASS)
    if len(still_missing) == len(listed_keys):
        return SendTimeCheck(skip_class=None, body=row.body)
    type_key = (
        "document_reminder"
        if row.notification_type.value.endswith("document_reminder")
        else "document_request"
    )
    rendered = render_candidate_message(
        type_key,
        candidate=candidate,
        documents=[item.name_snapshot for item in still_missing],
    )
    return SendTimeCheck(skip_class=None, body=rendered.body)
