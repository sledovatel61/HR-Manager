"""The single domain mechanism linking calendar events to «Мои напоминания».

UX feedback 2026-09-29, блок B: «Напоминание создаётся из календаря,
карточки кандидата и раздела „Напоминания“ через общий доменный механизм …
не допускай дублей и рассинхронизации статусов».

Rules (enforced in the same transaction as the event mutation):

* every event WITH a reminder moment owns at most ONE linked ``Reminder``
  row (``reminders.event_id`` is UNIQUE) — created from any entry point the
  reminder is the same object everywhere, so «Мои напоминания» always shows
  what the calendar promises;
* the reminder moment is ``starts_at`` for ``type=reminder`` events and
  ``remind_at`` for calls/interviews; without a moment no reminder exists;
* rescheduling an event moves the linked reminder (and re-activates a
  reminder closed for the *previous* moment — the moment changed, so it is
  fresh work);
* completing/cancelling an event completes/cancels the linked reminder;
  clearing the reminder moment cancels it;
* a reminder the user closed manually for the CURRENT moment stays closed —
  closing means «handled», and the unique link means no duplicate can
  appear instead.

The link is a plain FK: ``reminders.candidate_id`` mirrors the event's
candidate so the candidate card can show related reminders through the
regular reminders API.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import (
    Event,
    EventStatus,
    EventType,
    NotificationPreference,
    Reminder,
    ReminderImportance,
    ReminderRecurrence,
    ReminderStatus,
    User,
    UserRole,
)
from app.utils import utc_now


def reminder_moment(event: Event) -> datetime | None:
    """The UTC moment the linked reminder should fire, or None."""
    if event.status in (EventStatus.COMPLETED, EventStatus.CANCELLED):
        return None
    if event.type == EventType.REMINDER:
        return event.starts_at
    return event.remind_at


def can_see_event(user: User, event: Event) -> bool:
    """Visibility shared by the events and reminders routers.

    Candidate-linked events follow candidate ownership (HR: own candidates;
    manager/admin: all). Events without a candidate are visible to their
    author and assignee (and to manager/admin).
    """
    if user.role != UserRole.HR:
        return True
    if event.candidate is not None:
        return event.candidate.owner_user_id == user.id
    return user.id in (event.author_user_id, event.assignee_user_id)


def _display_timezone(db: Session, event: Event, fallback: str) -> str:
    """IANA display timezone for the linked reminder: the assignee's
    notification preference when saved, otherwise the configured default."""
    pref = db.get(NotificationPreference, event.assignee_user_id)
    if pref is not None and pref.timezone:
        return pref.timezone
    return fallback


def linked_reminder(db: Session, event_id: uuid.UUID) -> Reminder | None:
    """The reminder currently linked to the event (at most one)."""
    return db.scalar(select(Reminder).where(Reminder.event_id == event_id))


def sync_event_reminder(
    db: Session, event: Event, *, fallback_timezone: str = "Europe/Moscow"
) -> Reminder | None:
    """Create/update/close the event's linked reminder (same transaction).

    Idempotent: calling it twice in a row never creates a second reminder
    and never bumps versions without a real change. The caller commits.
    """
    moment = reminder_moment(event)
    linked = linked_reminder(db, event.id)

    if moment is None:
        # The event no longer wants a reminder: close the linked one.
        if linked is not None and linked.status == ReminderStatus.ACTIVE:
            if event.status == EventStatus.COMPLETED:
                linked.status = ReminderStatus.COMPLETED
                linked.completed_at = utc_now()
            else:
                linked.status = ReminderStatus.CANCELLED
            linked.version += 1
            linked.updated_at = utc_now()
        return linked

    if linked is None:
        reminder = Reminder(
            owner_user_id=event.author_user_id,
            assignee_user_id=event.assignee_user_id,
            title=event.title,
            note=None,
            candidate_id=event.candidate_id,
            event_id=event.id,
            due_at=moment,
            timezone=_display_timezone(db, event, fallback_timezone),
            importance=ReminderImportance.NORMAL,
            recurrence=ReminderRecurrence.NONE,
            status=ReminderStatus.ACTIVE,
            created_at=utc_now(),
            updated_at=utc_now(),
        )
        db.add(reminder)
        db.flush()
        return reminder

    # A closed reminder for a DIFFERENT moment is re-activated: the event
    # moved, so the old moment is no longer the one the user handled.
    closed_for_previous_moment = linked.status != ReminderStatus.ACTIVE and (
        linked.due_at != moment
    )
    if linked.status != ReminderStatus.ACTIVE and not closed_for_previous_moment:
        return linked

    changed = False
    if closed_for_previous_moment:
        linked.status = ReminderStatus.ACTIVE
        linked.completed_at = None
        changed = True
    if linked.due_at != moment:
        linked.due_at = moment
        changed = True
    if linked.title != event.title:
        linked.title = event.title
        changed = True
    if linked.candidate_id != event.candidate_id:
        linked.candidate_id = event.candidate_id
        changed = True
    if linked.assignee_user_id != event.assignee_user_id:
        linked.assignee_user_id = event.assignee_user_id
        changed = True
    if changed:
        linked.version += 1
        linked.updated_at = utc_now()
    return linked
