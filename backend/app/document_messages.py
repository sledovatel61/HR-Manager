"""Manual Phase 11 sends through the Phase 10 idempotency/outbox contract."""

from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.candidate_messages import allowed_candidate_channels, render_candidate_message
from app.config import Settings
from app.documents import (
    advisory,
    candidate_for,
    current_set,
    missing_items,
    payload_digest,
    queue_documents,
)
from app.models import CandidateMessageRequest, DeliveryChannel, User
from app.schemas import (
    CandidateMessagePreviewOut,
    CandidateMessagePreviewRequest,
    CandidateMessageSendOut,
    CandidateMessageSendRequest,
)


def document_message(
    db: Session,
    candidate_id: str,
    user: User,
    payload: CandidateMessageSendRequest | CandidateMessagePreviewRequest,
    settings: Settings,
) -> CandidateMessageSendOut | CandidateMessagePreviewOut:
    from app.routers.candidate_messages import _to_message_out

    sending = isinstance(payload, CandidateMessageSendRequest)
    if (
        payload.documents is not None
        or payload.event_id is not None
        or payload.document_set_id is None
    ):
        raise HTTPException(
            422, "Укажите снимок списка; названия документов и событие не принимаются."
        )
    # Global HTTP-key lock first; candidate lock then serializes different keys.
    if sending:
        assert isinstance(payload, CandidateMessageSendRequest)
        advisory(db, f"document-http:{payload.idempotency_key}")
    candidate = candidate_for(db, UUID(candidate_id), user, mutate=sending)
    digest = payload_digest(
        {"user": str(user.id), "candidate": candidate_id, **payload.model_dump(mode="json")}
    )
    if sending:
        assert isinstance(payload, CandidateMessageSendRequest)
        replay = db.scalar(
            select(CandidateMessageRequest).where(
                CandidateMessageRequest.idempotency_key == payload.idempotency_key
            )
        )
        if replay:
            if replay.user_id != user.id or replay.payload_hash != digest:
                raise HTTPException(409, "Ключ уже использован для другого запроса.")
            return CandidateMessageSendOut.model_validate(replay.response)
    snapshot = current_set(db, candidate.id)
    if snapshot is None or snapshot.id != payload.document_set_id:
        raise HTTPException(409, "Список кандидата изменился. Обновите документы.")
    items = missing_items(db, snapshot)
    if not items:
        raise HTTPException(409, "Нет недостающих обязательных документов.")
    allowed = allowed_candidate_channels(db, candidate=candidate, settings=settings)
    if payload.channel is not None:
        if payload.channel not in ("email", "telegram"):
            raise HTTPException(422, "Недопустимый канал.")
        allowed = [channel for channel in allowed if channel.value == payload.channel]
    message = render_candidate_message(
        payload.message_type, candidate=candidate, documents=[item["name"] for item in items]
    )
    if not sending:
        return CandidateMessagePreviewOut(
            title=message.title, body=message.body, channels=[c.value for c in allowed]
        )
    if not allowed:
        raise HTTPException(409, "Нет разрешённого канала и действующего согласия.")
    assert isinstance(payload, CandidateMessageSendRequest)
    rows = []
    for channel in allowed:
        # Durable logical dedupe for the same receipt-state, not just HTTP retry.
        # An explicit change of item version permits a new request.
        from app.models import CandidateDocumentItem, NotificationOutbox

        versions = list(
            db.scalars(
                select(CandidateDocumentItem.version)
                .where(CandidateDocumentItem.set_id == snapshot.id)
                .order_by(CandidateDocumentItem.key)
            )
        )
        revision_hash = payload_digest({"versions": versions})
        key = f"documents:{snapshot.id}:{payload.message_type}:{channel.value}:{revision_hash}"
        row = queue_documents(
            db,
            candidate=candidate,
            user=user,
            snapshot=snapshot,
            message_type=payload.message_type,
            channel=DeliveryChannel(channel),
            dedupe_key=key,
            settings=settings,
        )
        if row is None:
            # queue_candidate_message adds channel/candidate to its key; find
            # the already persisted operation by exact context/type/channel.
            row = db.scalar(
                select(NotificationOutbox)
                .where(
                    NotificationOutbox.recipient_candidate_id == candidate.id,
                    NotificationOutbox.object_id == snapshot.id,
                    NotificationOutbox.channel == channel,
                    NotificationOutbox.idempotency_key.contains(key),
                )
                .limit(1)
            )
        if row:
            rows.append(row)
    result = CandidateMessageSendOut(
        messages=[_to_message_out(row, user.username) for row in rows],
        channels=[row.channel.value for row in rows],
    )
    db.add(
        CandidateMessageRequest(
            idempotency_key=payload.idempotency_key,
            user_id=user.id,
            candidate_id=candidate.id,
            message_type=payload.message_type,
            payload_hash=digest,
            response=result.model_dump(mode="json"),
        )
    )
    from app.documents import audit

    audit(db, user, f"message set={snapshot.id} type={payload.message_type}", candidate)
    from app.audit import record_event
    from app.models import AuditAction

    record_event(
        db,
        AuditAction.CANDIDATE_MESSAGE_QUEUED,
        actor=user,
        candidate_id=candidate.id,
        details=f"set={snapshot.id} type={payload.message_type}",
        commit=False,
    )
    db.commit()
    return result
