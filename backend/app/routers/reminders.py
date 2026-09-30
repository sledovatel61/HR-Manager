"""Personal reminders API (phase 8).

Ownership rules: the owner edits; owner or assignee may complete/cancel;
lists show reminders where the current user is owner or assignee.
Candidate-linked reminders are only allowed when the assignee has current
access to that candidate (no information leaks through delegation).
"""

from uuid import UUID
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.db import get_db
from app.deps import get_current_user
from app.event_reminders import can_see_event, linked_reminder
from app.models import (
    Candidate,
    Event,
    Reminder,
    ReminderImportance,
    ReminderRecurrence,
    ReminderStatus,
    User,
    UserRole,
)
from app.notification_service import cancel_pending_for_object
from app.schemas import ReminderCreate, ReminderList, ReminderOut, ReminderUpdate
from app.utils import utc_now

router = APIRouter(prefix="/reminders", tags=["reminders"])


def _valid_timezone(name: str) -> None:
    try:
        ZoneInfo(name)
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Неизвестная часовая зона IANA: {name}",
        ) from None


def _own_reminder(db: Session, reminder_id: str, user: User) -> Reminder:
    try:
        uid = UUID(reminder_id)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Напоминание не найдено."
        ) from None
    reminder = db.get(Reminder, uid)
    if reminder is None or user.id not in (reminder.owner_user_id, reminder.assignee_user_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Напоминание не найдено.")
    return reminder


def _resolve_assignee(
    db: Session, user: User, candidate: Candidate | None, requested: UUID | None
) -> User:
    if requested is None:
        return user
    assignee = db.get(User, requested)
    if assignee is None or not assignee.is_active:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Исполнитель должен быть активным пользователем.",
        )
    if candidate is not None:
        # The candidate link must stay inside the assignee's access scope.
        can_see = assignee.role != UserRole.HR or candidate.owner_user_id == assignee.id
        if not can_see:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Исполнитель не имеет доступа к связанному кандидату.",
            )
    return assignee


def _resolve_candidate(db: Session, user: User, candidate_id: UUID | None) -> Candidate | None:
    if candidate_id is None:
        return None
    candidate = db.get(Candidate, candidate_id)
    if candidate is None or candidate.deleted_at is not None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Кандидат не найден.",
        )
    if user.role == UserRole.HR and candidate.owner_user_id != user.id:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Кандидат не найден.",
        )
    return candidate


def _resolve_event_link(
    db: Session, user: User, event_id: UUID | None, *, current: Reminder | None = None
) -> Event | None:
    """Validate the optional event link of a reminder.

    The event must exist, be visible to the user and not be taken by another
    reminder (the UNIQUE link is the shared event↔reminder domain contract:
    at most one «Моё напоминание» per calendar event).
    """
    if event_id is None:
        return None
    event = db.get(Event, event_id)
    if event is None or not can_see_event(user, event):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Событие не найдено.",
        )
    if event.candidate is not None and event.candidate.deleted_at is not None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Событие не найдено.",
        )
    linked = linked_reminder(db, event_id)
    if linked is not None and (current is None or linked.id != current.id):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="К этому событию уже привязано напоминание.",
        )
    return event


@router.get("", response_model=ReminderList, summary="List my reminders")
def list_reminders(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    status_filter: str | None = Query(default=None, alias="status"),
    candidate_id: UUID | None = Query(default=None),
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
) -> ReminderList:
    """Owner or assignee view, deterministic order (due_at asc, id asc).

    A reminder is visible to its owner AND its assignee: the owner keeps
    seeing a reminder they created even when it was delegated to another
    user, and the assignee sees reminders handed to them. Everyone else
    gets an empty page (existence never leaks). ``candidate_id`` filters
    the linked-candidate reminders for the candidate card.
    """
    filters = [or_(Reminder.assignee_user_id == user.id, Reminder.owner_user_id == user.id)]
    if candidate_id is not None:
        filters.append(Reminder.candidate_id == candidate_id)
    if status_filter is not None:
        if status_filter not in ("active", "completed", "cancelled"):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="status должен быть active, completed или cancelled.",
            )
        filters.append(Reminder.status == status_filter)
    total = db.execute(select(func.count()).select_from(Reminder).where(*filters)).scalar_one()
    rows = (
        db.execute(
            select(Reminder)
            .where(*filters)
            .order_by(Reminder.due_at.asc(), Reminder.id.asc())
            .limit(limit)
            .offset(offset)
        )
        .scalars()
        .all()
    )
    return ReminderList(
        items=[ReminderOut.model_validate(row) for row in rows],
        total=int(total),
        limit=limit,
        offset=offset,
    )


@router.post("", response_model=ReminderOut, status_code=status.HTTP_201_CREATED)
def create_reminder(
    payload: ReminderCreate,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> ReminderOut:
    """Create a reminder for myself (or delegate to an eligible assignee)."""
    _valid_timezone(payload.timezone)
    candidate = _resolve_candidate(db, user, payload.candidate_id)
    # The event link is validated up front: visible event + no duplicate
    # reminder (the shared event↔reminder domain contract).
    event = _resolve_event_link(db, user, payload.event_id)
    if event is not None and candidate is not None and event.candidate_id != candidate.id:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Кандидат не соответствует связанному событию.",
        )
    assignee = _resolve_assignee(db, user, candidate, payload.assignee_user_id)
    reminder = Reminder(
        owner_user_id=user.id,
        assignee_user_id=assignee.id,
        title=payload.title.strip(),
        note=payload.note,
        candidate_id=candidate.id if candidate is not None else None,
        event_id=event.id if event is not None else None,
        due_at=payload.due_at,
        timezone=payload.timezone,
        importance=payload.importance,
        recurrence=payload.recurrence,
        status=ReminderStatus.ACTIVE,
        created_at=utc_now(),
        updated_at=utc_now(),
    )
    db.add(reminder)
    db.commit()
    db.refresh(reminder)
    return ReminderOut.model_validate(reminder)


@router.get("/{reminder_id}", response_model=ReminderOut, summary="Get a reminder")
def get_reminder(
    reminder_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> ReminderOut:
    reminder = _own_reminder(db, reminder_id, user)
    return ReminderOut.model_validate(reminder)


@router.patch("/{reminder_id}", response_model=ReminderOut, summary="Update a reminder")
def update_reminder(
    reminder_id: str,
    payload: ReminderUpdate,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> ReminderOut:
    """Owner-only edit with optimistic concurrency (expected_version)."""
    reminder = _own_reminder(db, reminder_id, user)
    if reminder.owner_user_id != user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Редактировать напоминание может только владелец.",
        )
    locked = db.execute(
        select(Reminder)
        .where(Reminder.id == reminder.id)
        .with_for_update()
        .execution_options(populate_existing=True)
    ).scalar_one()
    if locked.version != payload.expected_version:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "Напоминание уже изменено (ожидалась версия "
                f"{payload.expected_version}, актуальная — {locked.version})."
            ),
        )
    if locked.status != ReminderStatus.ACTIVE:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Завершённое или отменённое напоминание нельзя изменять.",
        )
    fields_set = payload.model_fields_set
    if payload.title is not None:
        locked.title = payload.title.strip()
    if "note" in fields_set:
        locked.note = payload.note
    if payload.timezone is not None:
        _valid_timezone(payload.timezone)
        locked.timezone = payload.timezone
    if "due_at" in fields_set and payload.due_at is not None:
        locked.due_at = payload.due_at
    if payload.importance is not None:
        locked.importance = ReminderImportance(payload.importance)
    if payload.recurrence is not None:
        locked.recurrence = ReminderRecurrence(payload.recurrence)
    if "candidate_id" in fields_set:
        candidate = _resolve_candidate(db, user, payload.candidate_id)
        locked.candidate_id = candidate.id if candidate is not None else None
        if candidate is not None:
            _resolve_assignee(db, user, candidate, locked.assignee_user_id)
    if "event_id" in fields_set:
        event = _resolve_event_link(db, user, payload.event_id, current=locked)
        if event is not None:
            if (
                locked.candidate_id is not None
                and event.candidate_id != locked.candidate_id
            ):
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail="Кандидат не соответствует связанному событию.",
                )
            if event.candidate_id is not None:
                locked.candidate_id = event.candidate_id
        locked.event_id = event.id if event is not None else None
    if "assignee_user_id" in fields_set:
        candidate = locked.candidate
        locked.assignee_user_id = _resolve_assignee(
            db, user, candidate, payload.assignee_user_id
        ).id
    locked.version += 1
    locked.updated_at = utc_now()
    cancel_pending_for_object(db, object_type="reminder", object_id=locked.id)
    db.commit()
    db.refresh(locked)
    return ReminderOut.model_validate(locked)


@router.post("/{reminder_id}/complete", response_model=ReminderOut, summary="Complete")
def complete_reminder(
    reminder_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> ReminderOut:
    reminder = _own_reminder(db, reminder_id, user)
    locked = db.execute(
        select(Reminder)
        .where(Reminder.id == reminder.id)
        .with_for_update()
        .execution_options(populate_existing=True)
    ).scalar_one()
    if locked.status != ReminderStatus.ACTIVE:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Напоминание уже закрыто.")
    locked.status = ReminderStatus.COMPLETED
    locked.completed_at = utc_now()
    locked.version += 1
    locked.updated_at = utc_now()
    cancel_pending_for_object(db, object_type="reminder", object_id=locked.id)
    db.commit()
    db.refresh(locked)
    return ReminderOut.model_validate(locked)


@router.post("/{reminder_id}/cancel", response_model=ReminderOut, summary="Cancel")
def cancel_reminder(
    reminder_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> ReminderOut:
    reminder = _own_reminder(db, reminder_id, user)
    locked = db.execute(
        select(Reminder)
        .where(Reminder.id == reminder.id)
        .with_for_update()
        .execution_options(populate_existing=True)
    ).scalar_one()
    if locked.status == ReminderStatus.CANCELLED:
        return ReminderOut.model_validate(locked)
    if locked.status == ReminderStatus.COMPLETED:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="Завершённое напоминание нельзя отменить."
        )
    locked.status = ReminderStatus.CANCELLED
    locked.version += 1
    locked.updated_at = utc_now()
    cancel_pending_for_object(db, object_type="reminder", object_id=locked.id)
    db.commit()
    db.refresh(locked)
    return ReminderOut.model_validate(locked)
