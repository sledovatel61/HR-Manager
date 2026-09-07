"""event status vocabulary: add 'cancelled'

Phase 8 adds the ``cancelled`` event status (a terminal state, like
``completed``) so the notification trigger «событие отменено» can fire
honestly. The phase-5 transitions stay intact: ``scheduled -> completed |
postponed`` and ``postponed -> scheduled | completed`` gain
``... | cancelled`` as a terminal exit.

Backward compatible: existing rows keep their status; only the CHECK
constraint is widened and the ``cancelled_at`` column is added. Old code
(phase 5-7) never writes the new value, so a two-step deploy is not
required — the new value appears only after this migration is deployed.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0008"
down_revision: str | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "events",
        sa.Column("cancelled_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.drop_constraint("ck_events_status_valid", "events", type_="check")
    op.create_check_constraint(
        "ck_events_status_valid",
        "events",
        "status IN ('scheduled', 'completed', 'postponed', 'cancelled')",
    )
    op.create_check_constraint(
        "ck_events_cancelled_at_consistent",
        "events",
        "(status = 'cancelled' AND cancelled_at IS NOT NULL) "
        "OR (status <> 'cancelled' AND cancelled_at IS NULL)",
    )
    # The event_history vocabulary gains the 'cancelled' kind (the phase-5
    # CHECK is widened; old rows are unaffected).
    op.drop_constraint("ck_event_history_kind_valid", "event_history", type_="check")
    op.create_check_constraint(
        "ck_event_history_kind_valid",
        "event_history",
        "kind IN ('created', 'updated', 'rescheduled', 'completed', "
        "'postponed', 'cancelled', 'assignee_changed')",
    )


def downgrade() -> None:
    # Restore the phase-5 vocabulary. Rows with status='cancelled' would
    # violate the old CHECK; the downgrade refuses to run while any exist
    # (schema rollback is a deliberate, safe operation only).
    bind = op.get_bind()
    cancelled = bind.execute(
        sa.text("SELECT count(*) FROM events WHERE status = 'cancelled'")
    ).scalar_one()
    if cancelled:
        raise RuntimeError(
            "cannot downgrade 0008: events with status 'cancelled' exist "
            "(cancel those events or restore from backup-forward instead)"
        )
    op.drop_constraint("ck_events_cancelled_at_consistent", "events", type_="check")
    op.drop_constraint("ck_events_status_valid", "events", type_="check")
    op.create_check_constraint(
        "ck_events_status_valid",
        "events",
        "status IN ('scheduled', 'completed', 'postponed')",
    )
    op.drop_constraint("ck_event_history_kind_valid", "event_history", type_="check")
    op.create_check_constraint(
        "ck_event_history_kind_valid",
        "event_history",
        "kind IN ('created', 'updated', 'rescheduled', 'completed', "
        "'postponed', 'assignee_changed')",
    )
    op.drop_column("events", "cancelled_at")
