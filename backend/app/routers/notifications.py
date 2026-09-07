"""Internal notification center API (phase 8).

The current user only ever sees their own notifications; foreign ids are
404 (no existence leak). Bulk operations are capped server-side. The
resolve endpoint re-checks current access rights before the UI navigates
to a linked object (a user who lost access to a candidate gets
``allowed: false``, never a leaked object).
"""

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.db import get_db
from app.deps import get_current_user
from app.models import (
    Candidate,
    Event,
    Notification,
    NotificationOutbox,
    Reminder,
    User,
    UserRole,
)
from app.schemas import (
    DeliveryAttemptOut,
    DeliveryInfoOut,
    DeliveryListOut,
    NotificationIdsRequest,
    NotificationList,
    NotificationOut,
    NotificationResolveOut,
)
from app.utils import utc_now

router = APIRouter(prefix="/notifications", tags=["notifications"])

_MAX_BULK_IDS = 100


def _own_notification(db: Session, notification_id: str, user: User) -> Notification:
    try:
        uid = UUID(notification_id)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Уведомление не найдено."
        ) from None
    notification = db.get(Notification, uid)
    if notification is None or notification.user_id != user.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Уведомление не найдено.")
    return notification


def _unread_count(db: Session, user: User) -> int:
    return db.execute(
        select(func.count())
        .select_from(Notification)
        .where(
            Notification.user_id == user.id,
            Notification.read_at.is_(None),
            Notification.dismissed_at.is_(None),
        )
    ).scalar_one()


@router.get("", response_model=NotificationList, summary="List my notifications")
def list_notifications(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    unread_only: bool = Query(default=False),
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
) -> NotificationList:
    """Server-side paginated list, deterministic order (created_at desc, id desc)."""
    filters = [Notification.user_id == user.id]
    if unread_only:
        filters.append(Notification.read_at.is_(None))
    total = db.execute(select(func.count()).select_from(Notification).where(*filters)).scalar_one()
    rows = (
        db.execute(
            select(Notification)
            .where(*filters)
            .order_by(Notification.created_at.desc(), Notification.id.desc())
            .limit(limit)
            .offset(offset)
        )
        .scalars()
        .all()
    )
    return NotificationList(
        items=[NotificationOut.model_validate(row) for row in rows],
        total=int(total),
        limit=limit,
        offset=offset,
        unread_count=_unread_count(db, user),
    )


@router.get("/unread-count", summary="Unread notification counter")
def unread_count(db: Session = Depends(get_db), user: User = Depends(get_current_user)) -> dict:
    return {"count": _unread_count(db, user)}


def _bulk_ids(payload: NotificationIdsRequest, user: User) -> list[UUID]:
    if len(payload.ids) > _MAX_BULK_IDS:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Не более {_MAX_BULK_IDS} уведомлений за один запрос.",
        )
    return list(dict.fromkeys(payload.ids))  # dedupe, keep order


@router.post(
    "/mark-read", status_code=status.HTTP_204_NO_CONTENT, summary="Mark notifications read"
)
def mark_read(
    payload: NotificationIdsRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> Response:
    """Own rows only; already-read rows are left untouched (idempotent)."""
    ids = _bulk_ids(payload, user)
    db.execute(
        update(Notification)
        .where(
            Notification.user_id == user.id,
            Notification.id.in_(ids),
            Notification.read_at.is_(None),
        )
        .values(read_at=utc_now())
    )
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/mark-all-read", status_code=status.HTTP_204_NO_CONTENT, summary="Mark all read")
def mark_all_read(
    db: Session = Depends(get_db), user: User = Depends(get_current_user)
) -> Response:
    db.execute(
        update(Notification)
        .where(
            Notification.user_id == user.id,
            Notification.read_at.is_(None),
        )
        .values(read_at=utc_now())
    )
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/dismiss", status_code=status.HTTP_204_NO_CONTENT, summary="Dismiss notifications")
def dismiss(
    payload: NotificationIdsRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> Response:
    """Dismissed rows stay in history (no physical deletion)."""
    ids = _bulk_ids(payload, user)
    db.execute(
        update(Notification)
        .where(
            Notification.user_id == user.id,
            Notification.id.in_(ids),
            Notification.dismissed_at.is_(None),
        )
        .values(dismissed_at=utc_now())
    )
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/{notification_id}/resolve", response_model=NotificationResolveOut)
def resolve_notification(
    notification_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> NotificationResolveOut:
    """Re-check current access to the linked object before navigation.

    Returns 200 with ``allowed`` — never the object itself. A user who lost
    access (e.g. after a transfer) simply gets ``allowed: false``.
    """
    notification = _own_notification(db, notification_id, user)
    object_type = notification.object_type
    object_id = notification.object_id
    if object_type is None or object_id is None:
        return NotificationResolveOut(allowed=False)
    allowed = False
    if object_type == "candidate":
        candidate = db.get(Candidate, object_id)
        allowed = (
            candidate is not None
            and candidate.deleted_at is None
            and (user.role != UserRole.HR or candidate.owner_user_id == user.id)
        )
    elif object_type == "event":
        event = db.get(Event, object_id)
        allowed = (
            event is not None
            and event.candidate is not None
            and event.candidate.deleted_at is None
            and (user.role != UserRole.HR or event.candidate.owner_user_id == user.id)
        )
    elif object_type == "reminder":
        reminder = db.get(Reminder, object_id)
        allowed = reminder is not None and user.id in (
            reminder.owner_user_id,
            reminder.assignee_user_id,
        )
    return NotificationResolveOut(allowed=allowed, object_type=object_type, object_id=object_id)


@router.get("/{notification_id}/delivery", response_model=DeliveryInfoOut)
def notification_delivery(
    notification_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> DeliveryInfoOut:
    """Delivery history of one of my notifications (attempts are append-only)."""
    notification = _own_notification(db, notification_id, user)
    outbox = db.execute(
        select(NotificationOutbox).where(
            NotificationOutbox.idempotency_key == notification.dedupe_key
        )
    ).scalar_one_or_none()
    if outbox is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="История отправки не найдена."
        )
    return DeliveryInfoOut(
        id=outbox.id,
        channel=outbox.channel.value,
        status=outbox.status.value,
        notification_type=outbox.notification_type.value,
        scheduled_at=outbox.scheduled_at,
        scheduled_at_effective=outbox.scheduled_at_effective,
        queued_at=outbox.queued_at,
        delivered_at=outbox.delivered_at,
        failed_at=outbox.failed_at,
        cancelled_at=outbox.cancelled_at,
        attempts=outbox.attempts,
        next_attempt_at=outbox.next_attempt_at,
        error_class=outbox.error_class,
        attempts_history=[
            DeliveryAttemptOut(
                attempt_no=attempt.attempt_no,
                started_at=attempt.started_at,
                finished_at=attempt.finished_at,
                outcome=attempt.outcome.value,
                error_code=attempt.error_code,
                error_class=attempt.error_class,
            )
            for attempt in outbox.attempts_history
        ],
    )


@router.get("/deliveries", response_model=DeliveryListOut, summary="My delivery history")
def list_deliveries(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
) -> DeliveryListOut:
    """Own outbox jobs (no titles/bodies returned — history is about
    delivery status, the message itself lives in the notification)."""
    filters = [NotificationOutbox.recipient_user_id == user.id]
    total = db.execute(
        select(func.count()).select_from(NotificationOutbox).where(*filters)
    ).scalar_one()
    rows = (
        db.execute(
            select(NotificationOutbox)
            .where(*filters)
            .order_by(NotificationOutbox.queued_at.desc(), NotificationOutbox.id.desc())
            .limit(limit)
            .offset(offset)
        )
        .scalars()
        .all()
    )
    return DeliveryListOut(
        items=[
            DeliveryInfoOut(
                id=row.id,
                channel=row.channel.value,
                status=row.status.value,
                notification_type=row.notification_type.value,
                scheduled_at=row.scheduled_at,
                scheduled_at_effective=row.scheduled_at_effective,
                queued_at=row.queued_at,
                delivered_at=row.delivered_at,
                failed_at=row.failed_at,
                cancelled_at=row.cancelled_at,
                attempts=row.attempts,
                next_attempt_at=row.next_attempt_at,
                error_class=row.error_class,
                attempts_history=[],
            )
            for row in rows
        ],
        total=int(total),
        limit=limit,
        offset=offset,
    )


@router.get(
    "/{notification_id}",
    response_model=NotificationOut,
    summary="Get one of my notifications",
)
def get_notification(
    notification_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> NotificationOut:
    notification = _own_notification(db, notification_id, user)
    return NotificationOut.model_validate(notification)
