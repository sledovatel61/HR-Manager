"""event/reminder link: one linked reminder per event

UX feedback 2026-09-29 (block B): a calendar event and its reminder were two
independent objects with nothing connecting them, so an event created in the
calendar never appeared in «Напоминания» and a hand-made reminder had no way
back to its event.  ``app/event_reminders.py`` now derives the reminder from
the event in the same transaction.

The database guarantees the idempotence this code assumes: at most **one**
reminder may reference a given event, so a retried request or two concurrent
writers cannot produce duplicates.  A *partial* unique index is used (the
column is nullable) so the many personal reminders without an event keep
working, and existing rows are normalised first: when a deployment already
contains several reminders for one event, only the oldest survives as the
link — the others keep their data and simply lose the ``event_id`` they were
never displayed by.

Reversible: ``downgrade`` drops just the index; no column or row is removed.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0017"
down_revision: str | None = "0016"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    # Keep the OLDEST reminder linked to each event; detach the rest.
    #
    # `DISTINCT ON` is used rather than `MIN(id)`: `min(uuid)`/`max(uuid)` were
    # only added in PostgreSQL 20, and CI runs PostgreSQL 16, where the
    # aggregate simply does not exist and the whole migration aborts. Ordering
    # by ``created_at, id`` also makes «oldest» mean what the comment says —
    # with random v4 UUIDs, ``MIN(id)`` picked an arbitrary row.
    bind.execute(
        sa.text(
            """
            UPDATE reminders
               SET event_id = NULL
             WHERE event_id IS NOT NULL
               AND id NOT IN (
                   SELECT id
                     FROM (
                         SELECT DISTINCT ON (event_id) id, created_at
                           FROM reminders
                          WHERE event_id IS NOT NULL
                          ORDER BY event_id, created_at, id
                     ) AS keep_one_per_event
               )
            """
        )
    )
    op.create_index(
        "uq_reminders_event_id",
        "reminders",
        ["event_id"],
        unique=True,
        postgresql_where=sa.text("event_id IS NOT NULL"),
        sqlite_where=sa.text("event_id IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("uq_reminders_event_id", table_name="reminders")
