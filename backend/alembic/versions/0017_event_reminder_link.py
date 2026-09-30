"""Optional candidate link on events + the unique event↔reminder link.

Revision 0017 (UX feedback 2026-09-29, блоки A/B «единая связь
событие ↔ напоминание ↔ кандидат»).

Two changes:

* ``events.candidate_id`` becomes OPTIONAL. An event may be created without
  a candidate (личное событие); such events are visible to their author and
  assignee (and to manager/admin). Candidate-linked events keep the
  candidate-ownership visibility rules. Existing rows are untouched — every
  stored event keeps its candidate.
* ``reminders.event_id`` gets a UNIQUE constraint: the shared domain link
  guarantees AT MOST ONE «Моё напоминание» per calendar event no matter
  which entry point created it (calendar, candidate card, reminders page).
  Nullable unique columns allow many unlinked reminders (NULL != NULL).
  No data migration is needed: the FE never sent ``event_id`` before, so
  existing reminder rows are unlinked.

The revision is fully reversible: downgrade drops the unique constraint and
restores NOT NULL on ``events.candidate_id`` (safe because the upgrade never
writes NULL into existing rows).

Revision ID: 0017
Revises: 0016
Create Date: 2026-09-29
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0017"
down_revision: str | None = "0016"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Событие без кандидата: связь необязательна (существующие данные не трогаем).
    op.alter_column("events", "candidate_id", existing_type=sa.Uuid(), nullable=True)

    # Одно напоминание на событие: идемпотентность общей связи.
    op.create_unique_constraint("uq_reminders_event_id", "reminders", ["event_id"])


def downgrade() -> None:
    op.drop_constraint("uq_reminders_event_id", "reminders", type_="unique")
    op.alter_column("events", "candidate_id", existing_type=sa.Uuid(), nullable=False)
