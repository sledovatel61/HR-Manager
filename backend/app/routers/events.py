"""Calendar events: server-side filters, lifecycle and immutable history.

Contract highlights (documented in docs/ARCHITECTURE.md):
* types ``call | interview | reminder``, statuses ``scheduled | completed |
  postponed`` — closed vocabularies mirrored in ``frontend/src/types.ts``;
* all timestamps are UTC (timezone-aware); the API reads ISO 8601 with an
  offset/Z and the UI renders them in the browser's local timezone;
* period filters ``from``/``to`` are a half-open interval ``[from, to)``
  matched against the event span ``[starts_at, ends_at)`` (a null
  ``ends_at`` is the degenerate span at ``starts_at``);
* ``remind_from``/``remind_to`` filter by the *reminder moment*: the
  event's ``remind_at`` when present, otherwise ``starts_at`` for
  ``type=reminder`` events (calls/interviews without ``remind_at`` have no
  reminder moment);
* every mutation is one transaction: event update + immutable business
  history row + audit event commit together with a single ``db.commit()``;
* optimistic concurrency: ``PATCH`` requires ``expected_version`` and
  checks it under a row lock — a stale editor receives 409 and never
  overwrites a newer version;
* events of soft-deleted candidates are hidden from every role through the
  events API (documented policy; the audit log remains the admin view).
"""

from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import Session

from app.audit import record_event
from app.db import get_db
from app.deps import get_current_user
from app.models import (
    AuditAction,
    Candidate,
    Event,
    EventHistory,
    EventHistoryKind,
    EventStatus,
    EventType,
    User,
    UserRole,
)
from app.schemas import (
    EventCreate,
    EventHistoryList,
    EventHistoryOut,
    EventList,
    EventOut,
    EventUpdate,
)
from app.utils import client_ip, user_agent, utc_now

router = APIRouter(prefix="/events", tags=["events"])

_MAX_LIST_LIMIT = 100
_DEFAULT_LIST_LIMIT = 50

_SAFE_FIELD_NAMES = {
    "title": "title",
    "note": "note",
    "starts_at": "starts_at",
    "ends_at": "ends_at",
    "remind_at": "remind_at",
    "status": "status",
    "assignee_user_id": "assignee_user_id",
}


# --- Visibility --------------------------------------------------------------


def _can_see_event(user: User, event: Event) -> bool:
    """HRs see events of their own candidates only; managers/admins all."""
    if user.role != UserRole.HR:
        return True
    return event.candidate is not None and event.candidate.owner_user_id == user.id


def _event_visibility_condition(user: User) -> list:
    """Visibility predicate for list queries via the candidate join."""
    if user.role == UserRole.HR:
        return [Candidate.owner_user_id == user.id]
    return []


def _get_visible_event(db: Session, event_id: str, user: User) -> Event:
    """Resolve an event with visibility rules; 404 hides foreign events and
    events of soft-deleted candidates (for every role)."""
    try:
        parsed = UUID(event_id)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Событие не найдено."
        ) from None
    event = db.get(Event, parsed)
    if event is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Событие не найдено.")
    if not _can_see_event(user, event):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Событие не найдено.")
    if event.candidate is not None and event.candidate.deleted_at is not None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Событие не найдено.")
    return event


def _get_visible_candidate_for_event(db: Session, candidate_id: UUID, user: User) -> Candidate:
    """The candidate an event is created for: visible + not soft-deleted."""
    candidate = db.get(Candidate, candidate_id)
    if candidate is None or candidate.deleted_at is not None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Кандидат не найден.")
    if user.role == UserRole.HR and candidate.owner_user_id != user.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Кандидат не найден.")
    return candidate


# --- Assignee rules ----------------------------------------------------------


def _resolve_assignee(db: Session, user: User, requested_assignee_id: UUID | None) -> User:
    """Assignee rules (mirror candidate ownership): an HR schedules events
    only for themselves; managers/admins may assign any active HR."""
    if user.role == UserRole.HR:
        if requested_assignee_id not in (None, user.id):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="HR может назначать исполнителем только себя.",
            )
        return user
    if requested_assignee_id is None:
        return user
    assignee = db.get(User, requested_assignee_id)
    if assignee is None or not assignee.is_active or assignee.role != UserRole.HR:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Исполнитель должен быть активным пользователем с ролью HR.",
        )
    return assignee


# --- Field validation --------------------------------------------------------


def _validate_event_fields(
    event_type: EventType,
    starts_at: datetime,
    ends_at: datetime | None,
    remind_at: datetime | None,
) -> None:
    if ends_at is not None and ends_at <= starts_at:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Окончание должно быть позже начала.",
        )
    if remind_at is not None and remind_at > starts_at:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Напоминание должно быть не позже начала события.",
        )
    if event_type == EventType.REMINDER and remind_at is not None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Для события-напоминания момент напоминания — это его дата начала; "
            "отдельное remind_at не задаётся.",
        )


# --- Audit (single-transaction) ---------------------------------------------


def _audit_event(
    db: Session,
    request: Request,
    action: AuditAction,
    *,
    actor: User,
    event: Event,
    changed_fields: list[str] | None = None,
    commit: bool,
) -> None:
    """Audit an event mutation with technical ids and safe field names only
    — never PII, titles, notes or field values."""
    fields = ",".join(_SAFE_FIELD_NAMES[name] for name in (changed_fields or []))
    details = f"candidate={event.candidate_id} event={event.id}"
    if fields:
        details += f" fields={fields}"
    record_event(
        db,
        action,
        actor=actor,
        candidate_id=event.candidate_id,
        ip_address=client_ip(request),
        user_agent=user_agent(request.headers),
        details=details,
        commit=commit,
    )


# --- List --------------------------------------------------------------------


@router.get("", response_model=EventList, summary="List events (server-side filters)")
def list_events(
    from_: datetime | None = Query(default=None, alias="from"),
    to: datetime | None = Query(default=None),
    owner_id: UUID | None = Query(default=None),
    candidate_id: UUID | None = Query(default=None),
    type: EventType | None = Query(default=None),
    status_filter: EventStatus | None = Query(default=None, alias="status"),
    remind_from: datetime | None = Query(default=None),
    remind_to: datetime | None = Query(default=None),
    sort: str = Query(default="starts_at", pattern="^(starts_at|created_at|updated_at)$"),
    direction: str = Query(default="asc", pattern="^(asc|desc)$"),
    limit: int = Query(default=_DEFAULT_LIST_LIMIT, ge=1, le=_MAX_LIST_LIMIT),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> EventList:
    """Paginated events for the calendar, upcoming/overdue and reminders.

    * ``[from, to)`` filters by overlap with the event span
      ``[starts_at, ends_at)`` (half-open interval; a null ``ends_at`` is
      the degenerate span at ``starts_at``);
    * ``[remind_from, remind_to)`` filters by the reminder moment
      (``remind_at``, or ``starts_at`` for ``type=reminder``);
    * events of soft-deleted candidates never appear here;
    * sorting is stable: sort column, then ``id`` ascending.
    """
    if from_ is not None and to is not None and from_ >= to:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Граница периода 'from' должна быть раньше 'to'.",
        )

    conditions = [*_event_visibility_condition(user)]
    # Soft-deleted candidates are always excluded from the calendar.
    conditions.append(Candidate.deleted_at.is_(None))

    if to is not None:
        conditions.append(Event.starts_at < to)
    if from_ is not None:
        conditions.append(
            or_(
                Event.ends_at > from_,
                and_(Event.ends_at.is_(None), Event.starts_at >= from_),
            )
        )
    if owner_id is not None:
        conditions.append(Candidate.owner_user_id == owner_id)
    if candidate_id is not None:
        conditions.append(Event.candidate_id == candidate_id)
    if type is not None:
        conditions.append(Event.type == type)
    if status_filter is not None:
        conditions.append(Event.status == status_filter)

    # Reminder-moment filters (documented contract extension).
    if remind_from is not None or remind_to is not None:
        range_conditions: list = [Event.remind_at.is_not(None)]
        type_conditions: list = [Event.type == EventType.REMINDER, Event.remind_at.is_(None)]
        if remind_from is not None:
            range_conditions.append(Event.remind_at >= remind_from)
            type_conditions.append(Event.starts_at >= remind_from)
        if remind_to is not None:
            range_conditions.append(Event.remind_at < remind_to)
            type_conditions.append(Event.starts_at < remind_to)
        conditions.append(or_(and_(*range_conditions), and_(*type_conditions)))

    sort_column = {
        "starts_at": Event.starts_at,
        "created_at": Event.created_at,
        "updated_at": Event.updated_at,
    }[sort]
    order = sort_column.asc() if direction == "asc" else sort_column.desc()
    stmt = select(Event).join(Candidate, Event.candidate_id == Candidate.id).where(*conditions)
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    events = db.scalars(stmt.order_by(order, Event.id.asc()).limit(limit).offset(offset)).all()
    return EventList(
        items=[EventOut.model_validate(event) for event in events],
        total=total,
        limit=limit,
        offset=offset,
    )


# --- Create ------------------------------------------------------------------


@router.post(
    "", response_model=EventOut, status_code=status.HTTP_201_CREATED, summary="Create an event"
)
def create_event(
    payload: EventCreate,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> EventOut:
    """Create a scheduled event for an accessible, non-deleted candidate.

    One transaction: event + business-history row + audit event, a single
    ``db.commit()``.
    """
    candidate = _get_visible_candidate_for_event(db, payload.candidate_id, user)
    assignee = _resolve_assignee(db, user, payload.assignee_user_id)
    _validate_event_fields(payload.type, payload.starts_at, payload.ends_at, payload.remind_at)

    event = Event(
        candidate_id=candidate.id,
        author_user_id=user.id,
        assignee_user_id=assignee.id,
        type=payload.type,
        title=payload.title,
        note=payload.note,
        status=EventStatus.SCHEDULED,
        starts_at=payload.starts_at,
        ends_at=payload.ends_at,
        remind_at=payload.remind_at,
        completed_at=None,
        version=1,
    )
    db.add(event)
    db.flush()  # event.id for history/audit

    history = EventHistory(
        event_id=event.id,
        changed_by_user_id=user.id,
        kind=EventHistoryKind.CREATED,
        status_new=EventStatus.SCHEDULED.value,
    )
    db.add(history)
    _audit_event(
        db,
        request,
        AuditAction.EVENT_CREATED,
        actor=user,
        event=event,
        changed_fields=["title", "starts_at", "ends_at", "remind_at", "assignee_user_id"],
        commit=False,
    )
    db.commit()
    db.refresh(event)
    return EventOut.model_validate(event)


# --- Read --------------------------------------------------------------------


@router.get("/{event_id}", response_model=EventOut, summary="Get an event")
def get_event(
    event_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> EventOut:
    event = _get_visible_event(db, event_id, user)
    return EventOut.model_validate(event)


# --- Update ------------------------------------------------------------------


@router.patch("/{event_id}", response_model=EventOut, summary="Update an event")
def update_event(
    event_id: str,
    payload: EventUpdate,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> EventOut:
    """Edit, reschedule, complete or postpone an event.

    ``expected_version`` is required (optimistic concurrency): the check
    runs under a row lock, so a stale editor gets 409 and never overwrites
    a newer version. ``completed`` is terminal. Mutation + business history
    + audit commit in ONE transaction.
    """
    event = _get_visible_event(db, event_id, user)

    # Lock the row and re-read the current state (populate_existing defeats
    # the identity-map cache); then validate the optimistic version.
    locked = db.execute(
        select(Event)
        .where(Event.id == event.id)
        .with_for_update()
        .execution_options(populate_existing=True)
    ).scalar_one()
    if locked.candidate is not None and locked.candidate.deleted_at is not None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Событие не найдено.")
    if not _can_see_event(user, locked):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Событие не найдено.")
    if locked.version != payload.expected_version:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "Событие уже изменено (ожидалась версия "
                f"{payload.expected_version}, актуальная — {locked.version}). "
                "Обновите данные и повторите."
            ),
        )
    if locked.status == EventStatus.COMPLETED:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Завершённое событие нельзя изменять.",
        )

    changed_fields: list[str] = []

    new_status = payload.status if payload.status is not None else locked.status
    if (
        new_status == EventStatus.POSTPONED
        and locked.status != EventStatus.POSTPONED
        and payload.starts_at is None
    ):
        # Postponing always re-schedules: a new start date is mandatory.
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Откладывание события требует новую дату начала.",
        )
    if payload.status is not None and payload.status != locked.status:
        changed_fields.append("status")

    if payload.starts_at is not None and payload.starts_at != locked.starts_at:
        changed_fields.append("starts_at")
    if payload.ends_at is not None and payload.ends_at != locked.ends_at:
        changed_fields.append("ends_at")
    if payload.remind_at is not None and payload.remind_at != locked.remind_at:
        changed_fields.append("remind_at")
    if payload.title is not None and payload.title != locked.title:
        changed_fields.append("title")
    if payload.note is not None and payload.note != locked.note:
        changed_fields.append("note")

    new_starts = payload.starts_at if payload.starts_at is not None else locked.starts_at
    new_ends = payload.ends_at if payload.ends_at is not None else locked.ends_at
    new_remind = locked.remind_at
    if new_status == EventStatus.COMPLETED:
        new_remind = None  # done events no longer remind
    elif payload.remind_at is not None:
        new_remind = payload.remind_at

    # Assignee change (validated against the role model).
    new_assignee = locked.assignee
    if payload.assignee_user_id is not None and payload.assignee_user_id != locked.assignee_user_id:
        if user.role == UserRole.HR:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="HR может назначать исполнителем только себя.",
            )
        assignee = db.get(User, payload.assignee_user_id)
        if assignee is None or not assignee.is_active or assignee.role != UserRole.HR:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Исполнитель должен быть активным пользователем с ролью HR.",
            )
        new_assignee = assignee
        changed_fields.append("assignee_user_id")

    _validate_event_fields(locked.type, new_starts, new_ends, new_remind)

    if not changed_fields:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Нет изменений для применения.",
        )

    # --- Derive the single business-history kind and audit action ---
    if new_status == EventStatus.COMPLETED and locked.status != EventStatus.COMPLETED:
        kind = EventHistoryKind.COMPLETED
        audit_action = AuditAction.EVENT_COMPLETED
    elif new_status == EventStatus.POSTPONED and locked.status != EventStatus.POSTPONED:
        kind = EventHistoryKind.POSTPONED
        audit_action = AuditAction.EVENT_POSTPONED
    elif "assignee_user_id" in changed_fields:
        kind = EventHistoryKind.ASSIGNEE_CHANGED
        audit_action = AuditAction.EVENT_ASSIGNEE_CHANGED
    elif "starts_at" in changed_fields or "ends_at" in changed_fields:
        kind = EventHistoryKind.RESCHEDULED
        audit_action = AuditAction.EVENT_RESCHEDULED
    else:
        kind = EventHistoryKind.UPDATED
        audit_action = AuditAction.EVENT_UPDATED

    history = EventHistory(
        event_id=locked.id,
        changed_by_user_id=user.id,
        kind=kind,
        status_old=locked.status.value,
        status_new=new_status.value,
        starts_at_old=locked.starts_at,
        starts_at_new=new_starts,
        ends_at_old=locked.ends_at,
        ends_at_new=new_ends,
        remind_at_old=locked.remind_at,
        remind_at_new=new_remind,
        assignee_user_id_old=locked.assignee_user_id,
        assignee_user_id_new=new_assignee.id,
        title_changed="title" in changed_fields,
        note_changed="note" in changed_fields,
    )

    locked.status = new_status
    locked.starts_at = new_starts
    locked.ends_at = new_ends
    locked.remind_at = new_remind
    locked.title = payload.title if payload.title is not None else locked.title
    locked.note = payload.note if payload.note is not None else locked.note
    locked.assignee_user_id = new_assignee.id
    locked.completed_at = utc_now() if new_status == EventStatus.COMPLETED else None
    locked.version = locked.version + 1
    locked.updated_at = utc_now()

    db.add(history)
    _audit_event(
        db,
        request,
        audit_action,
        actor=user,
        event=locked,
        changed_fields=changed_fields,
        commit=False,
    )
    db.commit()
    db.refresh(locked)
    return EventOut.model_validate(locked)


# --- Business history --------------------------------------------------------


@router.get(
    "/{event_id}/history",
    response_model=EventHistoryList,
    summary="List the immutable business history of an event",
)
def list_event_history(
    event_id: str,
    limit: int = Query(default=_DEFAULT_LIST_LIMIT, ge=1, le=_MAX_LIST_LIMIT),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> EventHistoryList:
    """Paginated mutation history (oldest first) with the event's
    visibility rules."""
    event = _get_visible_event(db, event_id, user)
    total = (
        db.scalar(
            select(func.count()).select_from(EventHistory).where(EventHistory.event_id == event.id)
        )
        or 0
    )
    entries = db.scalars(
        select(EventHistory)
        .where(EventHistory.event_id == event.id)
        .order_by(EventHistory.created_at.asc(), EventHistory.id)
        .limit(limit)
        .offset(offset)
    ).all()
    return EventHistoryList(
        items=[EventHistoryOut.model_validate(entry) for entry in entries],
        total=total,
        limit=limit,
        offset=offset,
    )
