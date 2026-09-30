"""One domain mechanism that keeps a calendar event and its reminder in sync.

UX feedback 2026-09-29 (block B) reported the two objects drifting apart: an
event created in the calendar was never visible in «Напоминания», while a
reminder created by hand had no way back to the event or the candidate.

This module is the single place that derives a :class:`Reminder` row from an
:class:`Event`, so all three entry points (calendar, candidate card,
«Напоминания») converge on identical rules:

* **moment** — ``remind_at`` when set, otherwise ``starts_at`` for
  ``type=reminder`` events (the phase-5 contract: a reminder event *is* its
  own reminder moment).  Calls/interviews without ``remind_at`` have no
  reminder moment and therefore no linked reminder.
* **idempotence** — at most one linked reminder per event, enforced by a
  unique partial index on ``reminders.event_id``; ``sync_event_reminder``
  looks the row up under the caller's row lock, so creating the same event
  twice — or a retried request — can never produce a duplicate.
* **state sync** — completing, cancelling or rescheduling the event cancels
  the pending deliveries of the linked reminder and re-points it at the new
  moment, so «Напоминания» can never show a moment the calendar has moved.
* **ownership** — the reminder is owned by the event author and assigned to
  the event assignee, i.e. it surfaces in the «Напоминания» list of exactly
  the people who see the event.

Every write joins the caller's transaction: the event, its business history,
its audit row and the reminder commit or roll back together.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings
from app.models import (
    Event,
    EventStatus,
    EventType,
    Reminder,
    ReminderRecurrence,
    ReminderStatus,
    User,
)
from app.notification_service import cancel_pending_for_object, preference_for
from app.utils import utc_now

#: Reminder titles are capped at 200 characters by the DB constraint.
_MAX_TITLE = 200
_MAX_NOTE = 2000


def reminder_moment(event: Event) -> datetime | None:
    """The instant an event should remind at, or ``None`` if it has none."""
    if event.remind_at is not None:
        return event.remind_at
    if event.type == EventType.REMINDER:
        return event.starts_at
    return None


def _linked_reminder(db: Session, event: Event, *, for_update: bool = False) -> Reminder | None:
    stmt = select(Reminder).where(Reminder.event_id == event.id)
    if for_update:
        stmt = stmt.with_for_update()
    return db.scalars(stmt).first()


def _title_for(event: Event) -> str:
    label = "Напоминание" if event.type == EventType.REMINDER else "Событие"
    text = f"{label}: {event.title}"
    return text[:_MAX_TITLE]


def sync_event_reminder(
    db: Session,
    event: Event,
    *,
    author: User,
    assignee: User,
    settings: Settings,
) -> Reminder | None:
    """Create, move, or close the reminder linked to ``event``.

    Returns the linked reminder, or ``None`` when the event has no reminder
    moment and none is linked. Safe to call repeatedly for the same event:
    the lookup is unique per ``event_id`` and runs under a row lock.
    """
    existing = _linked_reminder(db, event, for_update=True)
    moment = reminder_moment(event)
    timezone = preference_for(db, assignee.id, settings.notification_default_timezone).timezone
    now = utc_now()

    # Completed/cancelled events stop reminding; the linked row is closed but
    # kept, so the history of «Напоминания» stays consistent with the calendar.
    terminal = event.status in (EventStatus.COMPLETED, EventStatus.CANCELLED)
    if terminal or moment is None:
        if existing is not None and existing.status == ReminderStatus.ACTIVE:
            cancel_pending_for_object(db, object_type="reminder", object_id=existing.id)
            if terminal:
                existing.status = ReminderStatus.CANCELLED
            else:
                # No moment any more: drop the link, the reminder stands alone
                # as a plain personal reminder.
                existing.event_id = None
            existing.version += 1
            existing.updated_at = now
        return existing

    if existing is None:
        reminder = Reminder(
            owner_user_id=author.id,
            assignee_user_id=assignee.id,
            title=_title_for(event),
            note=event.note[:_MAX_NOTE] if event.note else None,
            candidate_id=event.candidate_id,
            event_id=event.id,
            due_at=moment,
            timezone=timezone,
            recurrence=ReminderRecurrence.NONE,
            status=ReminderStatus.ACTIVE,
        )
        db.add(reminder)
        return reminder

    # Existing link: re-point it at the current moment and ownership.
    moved = (
        existing.due_at != moment
        or existing.assignee_user_id != assignee.id
        or existing.title != _title_for(event)
    )
    existing.assignee_user_id = assignee.id
    existing.title = _title_for(event)
    existing.due_at = moment
    existing.timezone = timezone
    if existing.status != ReminderStatus.ACTIVE:
        existing.status = ReminderStatus.ACTIVE
        moved = True
    if moved:
        # A re-pointed reminder must not deliver the stale moment.
        cancel_pending_for_object(db, object_type="reminder", object_id=existing.id)
        existing.version += 1
        existing.updated_at = now
    return existing
