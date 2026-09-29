"""Work schedule («График выхода на работу») — candidate start fields + service rows.

Revision 0016 (Phase 18, решение владельца 2026-09-29: обязательный этап
пилота).

Two additions:

* ``candidates`` gains six nullable columns describing the planned/actual
  start at work: ``start_date`` (date), ``start_time`` (time, optional),
  ``start_organization``, ``start_department``, ``shift`` and
  ``start_comment``. The position column is REUSED (``position``) — the
  schedule does not duplicate it. An index on ``start_date`` serves the
  day-grouped listing.
* ``schedule_entries`` — service rows without a candidate («Увольнение
  13:00-14:00», «перевод», «отработка грузчик», «медосмотр»): date, optional
  time or interval (``time_from``/``time_to``), short text, optional
  organization/department/comment and the author. Authorship keeps the row
  alive when a user is deactivated (``ON DELETE SET NULL``).

No data migration is required: existing candidates simply have no start date
and therefore do not appear in the schedule. The revision is fully
reversible — downgrade drops the table and the six columns.

Revision ID: 0016
Revises: 0015
Create Date: 2026-09-29
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0016"
down_revision: str | None = "0015"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # --- Кандидат: дата выхода и реквизиты рабочего места -----------------
    op.add_column("candidates", sa.Column("start_date", sa.Date(), nullable=True))
    op.add_column("candidates", sa.Column("start_time", sa.Time(), nullable=True))
    op.add_column(
        "candidates", sa.Column("start_organization", sa.String(length=120), nullable=True)
    )
    op.add_column("candidates", sa.Column("start_department", sa.String(length=120), nullable=True))
    op.add_column("candidates", sa.Column("shift", sa.String(length=32), nullable=True))
    op.add_column("candidates", sa.Column("start_comment", sa.String(length=300), nullable=True))
    op.create_index("ix_candidates_start_date", "candidates", ["start_date"], unique=False)

    # --- Служебные строки графика (без кандидата) -------------------------
    op.create_table(
        "schedule_entries",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("entry_date", sa.Date(), nullable=False),
        sa.Column("time_from", sa.Time(), nullable=True),
        sa.Column("time_to", sa.Time(), nullable=True),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("organization", sa.String(length=120), nullable=True),
        sa.Column("department", sa.String(length=120), nullable=True),
        sa.Column("comment", sa.String(length=300), nullable=True),
        sa.Column("author_user_id", sa.Uuid(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "length(title) >= 1",
            name="ck_schedule_entries_title_present",
        ),
        sa.CheckConstraint(
            "time_to IS NULL OR time_from IS NULL OR time_to >= time_from",
            name="ck_schedule_entries_time_order",
        ),
        sa.ForeignKeyConstraint(
            ["author_user_id"],
            ["users.id"],
            name="fk_schedule_entries_author",
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_schedule_entries"),
    )
    op.create_index(
        "ix_schedule_entries_entry_date", "schedule_entries", ["entry_date"], unique=False
    )


def downgrade() -> None:
    op.drop_index("ix_schedule_entries_entry_date", table_name="schedule_entries")
    op.drop_table("schedule_entries")
    op.drop_index("ix_candidates_start_date", table_name="candidates")
    op.drop_column("candidates", "start_comment")
    op.drop_column("candidates", "shift")
    op.drop_column("candidates", "start_department")
    op.drop_column("candidates", "start_organization")
    op.drop_column("candidates", "start_time")
    op.drop_column("candidates", "start_date")
