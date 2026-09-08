"""Candidate documents API (phase 11) — ``/candidates/{id}/documents``.

The candidate keeps an exact snapshot of the applied list version; the
receipt fact of every item changes under optimistic concurrency. Manual
document request/reminder messages are rendered by the server from the
CURRENTLY missing items of the applied version and queued through the
phase-10 outbox (the worker re-validates consent, channel and the missing
items right before the provider call).

Authorization is the phase-10 candidate-communication scope (the document
flow feeds candidate messages): HR — own candidates (foreign → 404),
manager — all, admin — only with an active pilot grant (403), deleted
candidates — 404 for everyone.

Only the fact of receipt is stored — no files, no document numbers.
Audit rows carry ids/keys/counts, never document names.
"""

from __future__ import annotations

import hashlib
import json
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.audit import record_event
from app.candidate_messages import allowed_candidate_channels
from app.config import Settings
from app.db import get_db
from app.deps import get_current_user
from app.document_lists import (
    DOCUMENT_ASSIGNMENT_OBJECT_TYPE,
    DocumentListError,
    apply_published_list,
    assignment_history,
    current_assignment,
    missing_items,
    queue_document_message,
    set_item_status,
)
from app.models import (
    AuditAction,
    Candidate,
    CandidateDocumentAssignment,
    CandidateDocumentItem,
    CandidateDocumentStatus,
    CandidateMessageRequest,
    DeliveryChannel,
    DeliveryStatus,
    NotificationOutbox,
    NotificationSource,
    NotificationType,
    User,
)
from app.notification_service import is_duplicate_key_error
from app.routers.candidate_messages import (
    _accessible_candidate,
    _enforce_rate_limit,
    _idempotency_lookup,
    _no_allowed_channels_response,
    _pending_duplicate_exists,
    _to_message_out,
)
from app.schemas import (
    CandidateDocumentApplyRequest,
    CandidateDocumentAssignmentOut,
    CandidateDocumentItemOut,
    CandidateDocumentItemUpdate,
    CandidateDocumentMessageRequest,
    CandidateDocumentsOut,
    CandidateMessageSendOut,
    CandidateMissingDocumentOut,
    CandidateMissingDocumentsOut,
)
from app.utils import client_ip, user_agent, utc_now

router = APIRouter(prefix="/candidates/{candidate_id}/documents", tags=["candidate-documents"])


# --- Helpers ---------------------------------------------------------------------


def _audit(
    db: Session,
    request: Request,
    action: AuditAction,
    *,
    actor: User,
    candidate: Candidate,
    details: str,
) -> None:
    record_event(
        db,
        action,
        actor=actor,
        candidate_id=candidate.id,
        ip_address=client_ip(request),
        user_agent=user_agent(request.headers),
        details=details,
        commit=False,
    )


def _item_out(item: CandidateDocumentItem) -> CandidateDocumentItemOut:
    return CandidateDocumentItemOut(
        id=item.id,
        item_key=item.item_key,
        name=item.name_snapshot,
        explanation=item.explanation_snapshot,
        is_required=item.is_required,
        sort_order=item.sort_order,
        status=item.status.value,
        version=item.version,
        changed_by_user_id=item.changed_by_user_id,
        changed_by_username=item.changed_by_username,
        changed_at=item.changed_at,
    )


def _assignment_out(
    db: Session, assignment: CandidateDocumentAssignment
) -> CandidateDocumentAssignmentOut:
    items = sorted(assignment.items, key=lambda entry: entry.sort_order)
    assigned_by = (
        db.get(User, assignment.assigned_by_user_id)
        if assignment.assigned_by_user_id is not None
        else None
    )
    return CandidateDocumentAssignmentOut(
        id=assignment.id,
        candidate_id=assignment.candidate_id,
        list_id=assignment.list_id,
        list_name=assignment.list_name_snapshot,
        version_id=assignment.version_id,
        version_number=assignment.version_number,
        assigned_at=assignment.assigned_at,
        assigned_by_user_id=assignment.assigned_by_user_id,
        assigned_by_username=assigned_by.username if assigned_by is not None else None,
        assigned_by_rule_id=assignment.assigned_by_rule_id,
        items=[_item_out(item) for item in items],
        missing_required_count=sum(
            1
            for item in items
            if item.is_required and item.status == CandidateDocumentStatus.MISSING
        ),
        received_count=sum(1 for item in items if item.status == CandidateDocumentStatus.RECEIVED),
    )


def _documents_out(db: Session, candidate: Candidate) -> CandidateDocumentsOut:
    current = current_assignment(db, candidate.id)
    return CandidateDocumentsOut(
        current=_assignment_out(db, current) if current is not None else None,
        history=[_assignment_out(db, row) for row in assignment_history(db, candidate.id)],
    )


def _conflict(detail: str) -> HTTPException:
    return HTTPException(status_code=status.HTTP_409_CONFLICT, detail=detail)


# --- Read -------------------------------------------------------------------------


@router.get("", response_model=CandidateDocumentsOut, summary="Candidate's document snapshot")
def get_candidate_documents(
    candidate_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> CandidateDocumentsOut:
    candidate = _accessible_candidate(db, candidate_id, user)
    return _documents_out(db, candidate)


@router.get(
    "/missing",
    response_model=CandidateMissingDocumentsOut,
    summary="Only the items still missing from the applied list",
)
def get_missing_documents(
    candidate_id: str,
    required_only: bool = False,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> CandidateMissingDocumentsOut:
    candidate = _accessible_candidate(db, candidate_id, user)
    assignment = current_assignment(db, candidate.id)
    if assignment is None:
        return CandidateMissingDocumentsOut(items=[], required_only=required_only)
    return CandidateMissingDocumentsOut(
        list_id=assignment.list_id,
        version_id=assignment.version_id,
        version_number=assignment.version_number,
        required_only=required_only,
        items=[
            CandidateMissingDocumentOut(
                item_key=item.item_key,
                name=item.name_snapshot,
                is_required=item.is_required,
                sort_order=item.sort_order,
            )
            for item in missing_items(assignment, required_only=required_only)
        ],
    )


# --- Apply -----------------------------------------------------------------------


@router.post(
    "/apply",
    response_model=CandidateDocumentsOut,
    status_code=status.HTTP_201_CREATED,
    summary="Apply the current published version of a list",
)
def apply_document_list(
    candidate_id: str,
    payload: CandidateDocumentApplyRequest,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> CandidateDocumentsOut:
    """Snapshot the published version's items onto the candidate.

    Replacing an existing list requires ``replace=true`` (409 otherwise);
    the previous assignment is closed and kept as history. Two concurrent
    applications serialize on the candidate lock; the loser gets 409.
    """
    candidate = _accessible_candidate(db, candidate_id, user)
    try:
        assignment, replaced = apply_published_list(
            db,
            candidate=candidate,
            list_id=payload.list_id,
            actor_user_id=user.id,
            replace=payload.replace,
        )
    except DocumentListError as exc:
        db.rollback()
        if exc.code == "not_published":
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=exc.message) from None
        raise _conflict(exc.message) from None
    _audit(
        db,
        request,
        AuditAction.CANDIDATE_DOCUMENT_LIST_APPLIED,
        actor=user,
        candidate=candidate,
        details=(
            f"list={assignment.list_id} version={assignment.version_number} "
            f"items={len(assignment.items)}"
            + (f" replaced_assignment={replaced.id}" if replaced is not None else "")
        ),
    )
    db.commit()
    return _documents_out(db, candidate)


# --- Item status ------------------------------------------------------------------


@router.patch(
    "/items/{item_id}",
    response_model=CandidateDocumentsOut,
    summary="Mark a document received / missing (optimistic concurrency)",
)
def update_document_item(
    candidate_id: str,
    item_id: str,
    payload: CandidateDocumentItemUpdate,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> CandidateDocumentsOut:
    candidate = _accessible_candidate(db, candidate_id, user)
    try:
        parsed = UUID(item_id)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Элемент списка не найден."
        ) from None
    item = db.execute(
        select(CandidateDocumentItem)
        .join(
            CandidateDocumentAssignment,
            CandidateDocumentAssignment.id == CandidateDocumentItem.assignment_id,
        )
        .where(
            CandidateDocumentItem.id == parsed,
            CandidateDocumentAssignment.candidate_id == candidate.id,
        )
    ).scalar_one_or_none()
    if item is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Элемент списка не найден."
        )
    assignment = db.get(CandidateDocumentAssignment, item.assignment_id)
    if assignment is None or assignment.replaced_at is not None:
        raise _conflict("Этот список документов уже заменён — отметки в нём закрыты.")
    try:
        changed = set_item_status(
            db,
            item=item,
            status=CandidateDocumentStatus(payload.status),
            expected_version=payload.expected_version,
            actor_user_id=user.id,
        )
    except DocumentListError as exc:
        db.rollback()
        raise _conflict(exc.message) from None
    if not changed:
        db.rollback()
        return _documents_out(db, candidate)
    action = (
        AuditAction.CANDIDATE_DOCUMENT_RECEIVED
        if payload.status == "received"
        else AuditAction.CANDIDATE_DOCUMENT_UNRECEIVED
    )
    _audit(
        db,
        request,
        action,
        actor=user,
        candidate=candidate,
        details=f"assignment={assignment.id} item_key={item.item_key} version={item.version}",
    )
    db.commit()
    return _documents_out(db, candidate)


# --- Manual document messages from the applied list --------------------------------------


def _document_payload_hash(
    *, user_id: UUID, candidate_id: UUID, message_type: str, channel: str | None, keys: list[str]
) -> str:
    canonical = json.dumps(
        {
            "user_id": str(user_id),
            "candidate_id": str(candidate_id),
            "message_type": message_type,
            "channel": channel,
            "keys": keys,
            "kind": "document_list",
        },
        sort_keys=True,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


@router.post(
    "/messages",
    response_model=CandidateMessageSendOut,
    status_code=status.HTTP_201_CREATED,
    summary="Queue a document request/reminder from the applied list",
)
def send_document_message(
    candidate_id: str,
    payload: CandidateDocumentMessageRequest,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> CandidateMessageSendOut:
    """Queue a document request/reminder rendered from the applied list.

    Only the CURRENTLY missing items are listed; the row snapshots the
    assignment, list/version ids and item keys so the worker can skip the
    send if nothing is missing any more. Idempotent per client key, rate
    limited and audited like the phase-10 manual send.
    """
    candidate = _accessible_candidate(db, candidate_id, user)
    settings: Settings = request.app.state.settings
    _enforce_rate_limit("candidate-message-send", user, settings)
    channel = DeliveryChannel(payload.channel) if payload.channel is not None else None
    assignment = current_assignment(db, candidate.id)
    if assignment is None:
        raise _conflict("Кандидату не применён список документов.")
    missing = missing_items(assignment, required_only=False)
    keys = [item.item_key for item in missing]
    idempotency_key = payload.idempotency_key.strip()
    payload_hash = _document_payload_hash(
        user_id=user.id,
        candidate_id=candidate.id,
        message_type=payload.message_type,
        channel=channel.value if channel is not None else None,
        keys=keys,
    )
    replay = _idempotency_lookup(db, idempotency_key)
    if replay is not None:
        if replay.user_id != user.id or replay.payload_hash != payload_hash:
            raise _conflict("Ключ идемпотентности уже использован с другим запросом.")
        db.rollback()
        return CandidateMessageSendOut.model_validate(replay.response)
    if not missing:
        raise _conflict("Все документы из списка уже получены — отправлять нечего.")
    allowed = allowed_candidate_channels(db, candidate=candidate, settings=settings)
    if channel is not None:
        if channel not in allowed:
            raise _no_allowed_channels_response(channel)
    elif not allowed:
        raise _no_allowed_channels_response(None)
    if _pending_duplicate_exists(db, candidate, payload.message_type, None) or (
        _pending_document_duplicate(db, candidate, payload.message_type)
    ):
        raise _conflict("Такое сообщение уже ожидает отправки этому кандидату.")

    record = CandidateMessageRequest(
        idempotency_key=idempotency_key,
        user_id=user.id,
        candidate_id=candidate.id,
        message_type=payload.message_type,
        payload_hash=payload_hash,
        response={},
    )
    db.add(record)
    try:
        db.flush()
    except IntegrityError as exc:
        if not is_duplicate_key_error(exc):
            raise
        db.rollback()
        replay = _idempotency_lookup(db, idempotency_key)
        if replay is None:
            raise _conflict("Запрос обрабатывается, повторите попытку.") from None
        if replay.user_id != user.id or replay.payload_hash != payload_hash:
            raise _conflict("Ключ идемпотентности уже использован с другим запросом.") from None
        return CandidateMessageSendOut.model_validate(replay.response)

    rows, plan = queue_document_message(
        db,
        candidate=candidate,
        assignment=assignment,
        message_type_key=payload.message_type,
        source=NotificationSource.MANUAL,
        dedupe_key=f"manual:{idempotency_key}",
        settings=settings,
        scheduled_at=utc_now(),
        initiator_user_id=user.id,
        only_channel=channel,
    )
    if plan is None:
        db.rollback()
        raise _conflict("Все документы из списка уже получены — отправлять нечего.")
    _audit(
        db,
        request,
        AuditAction.CANDIDATE_DOCUMENT_MESSAGE_QUEUED,
        actor=user,
        candidate=candidate,
        details=(
            f"type={payload.message_type} assignment={assignment.id} "
            f"version={assignment.version_number} items={len(plan.item_keys)} "
            f"channels={','.join(row.channel.value for row in rows)}"
        ),
    )
    result = CandidateMessageSendOut(
        messages=[_to_message_out(row, user.username) for row in rows],
        channels=[row.channel.value for row in rows],
    )
    record.response = result.model_dump(mode="json")
    db.commit()
    for row in rows:
        db.refresh(row)
    return result


def _pending_document_duplicate(db: Session, candidate: Candidate, message_type: str) -> bool:
    """A queued/sending document message of this type built from ANY
    assignment (object_id is the assignment id, so the phase-10 check with
    ``object_id IS NULL`` does not see it)."""
    type_value = (
        NotificationType.CANDIDATE_DOCUMENT_REQUEST
        if message_type == "document_request"
        else NotificationType.CANDIDATE_DOCUMENT_REMINDER
    )
    return (
        db.execute(
            select(NotificationOutbox.id)
            .where(
                NotificationOutbox.recipient_candidate_id == candidate.id,
                NotificationOutbox.notification_type == type_value,
                NotificationOutbox.object_type == DOCUMENT_ASSIGNMENT_OBJECT_TYPE,
                NotificationOutbox.status.in_([DeliveryStatus.QUEUED, DeliveryStatus.SENDING]),
            )
            .limit(1)
        ).scalar()
        is not None
    )
