"""Synchronize source schedule rows without losing people or assignments.

Adds source-row snapshots to the existing schedule import tables instead of
introducing a second import subsystem. ``schedule_import_rows`` becomes the
versioned representation of person/service/skip/error rows; only the rows in
the latest import are active. Candidates created by the importer may remain
unassigned until an authorized user allocates an HR owner.

Revision ID: 0021
Revises: 0020
Create Date: 2026-10-02
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0021"
down_revision: str | None = "0020"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Imported candidates are deliberately unassigned until a manager chooses
    # an HR owner. Existing candidates keep their current owner values.
    op.alter_column(
        "candidates",
        "owner_user_id",
        existing_type=sa.Uuid(),
        nullable=True,
    )

    op.add_column(
        "schedule_entries",
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
    )

    op.add_column("schedule_imports", sa.Column("header_row", sa.Integer(), nullable=False, server_default="1"))
    op.add_column("schedule_imports", sa.Column("source_headers", sa.JSON(), nullable=True))
    op.add_column("schedule_imports", sa.Column("source_columns", sa.JSON(), nullable=True))
    for name in ("rows_added", "rows_updated", "rows_unchanged", "rows_missing", "active_people"):
        op.add_column(
            "schedule_imports",
            sa.Column(name, sa.Integer(), nullable=False, server_default="0"),
        )
    op.execute("UPDATE schedule_imports SET source_headers = '[]', source_columns = '{}' WHERE source_headers IS NULL")
    op.alter_column("schedule_imports", "source_headers", nullable=False)
    op.alter_column("schedule_imports", "source_columns", nullable=False)

    additions = (
        sa.Column("source_identity", sa.String(length=64), nullable=True),
        sa.Column("row_order", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("row_type", sa.String(length=16), nullable=False, server_default="skip"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("sync_status", sa.String(length=16), nullable=False, server_default="added"),
        sa.Column("decision", sa.String(length=16), nullable=False, server_default="skip"),
        sa.Column("missing_in_import_id", sa.Uuid(), nullable=True),
        sa.Column("source_values", sa.JSON(), nullable=True),
        sa.Column("full_name", sa.String(length=200), nullable=True),
        sa.Column("name_normalized", sa.String(length=200), nullable=False, server_default=""),
        sa.Column("entry_date", sa.Date(), nullable=True),
        sa.Column("time_from", sa.Time(), nullable=True),
        sa.Column("time_to", sa.Time(), nullable=True),
        sa.Column("organization", sa.String(length=120), nullable=True),
        sa.Column("department", sa.String(length=120), nullable=True),
        sa.Column("position", sa.String(length=200), nullable=True),
        sa.Column("shift", sa.String(length=32), nullable=True),
        sa.Column("comment", sa.String(length=300), nullable=True),
        sa.Column("phone", sa.String(length=32), nullable=True),
        sa.Column("phone_normalized", sa.String(length=32), nullable=True),
        sa.Column("schedule_ready", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("parse_error", sa.Text(), nullable=True),
        sa.Column("warnings", sa.JSON(), nullable=True),
        sa.Column("owner_user_id", sa.Uuid(), nullable=True),
    )
    for column in additions:
        op.add_column("schedule_import_rows", column)

    # Preserve existing provenance rows as active snapshots where possible.
    # Their content is reconstructed from linked entities on the next import;
    # the original xlsx is intentionally not retained by this feature.
    op.execute(
        """
        UPDATE schedule_import_rows
        SET source_identity = row_key,
            row_order = sheet_row,
            row_type = CASE
                WHEN candidate_id IS NOT NULL THEN 'person'
                WHEN entry_id IS NOT NULL THEN 'service'
                ELSE 'skip'
            END,
            warnings = '[]'
        """
    )
    # Only rows belonging to the latest confirmed import are current. Older
    # provenance snapshots remain queryable but cannot leak into active export.
    op.execute(
        """
        UPDATE schedule_import_rows
        SET is_active = (import_id = (
            SELECT id FROM schedule_imports ORDER BY created_at DESC, id DESC LIMIT 1
        ))
        """
    )
    op.execute(
        """
        UPDATE schedule_import_rows AS source_row
        SET full_name = candidate.full_name,
            name_normalized = candidate.full_name_normalized,
            entry_date = candidate.start_date,
            time_from = candidate.start_time,
            organization = candidate.start_organization,
            department = candidate.start_department,
            position = candidate.position,
            shift = candidate.shift,
            comment = candidate.start_comment,
            phone = candidate.phone,
            phone_normalized = candidate.phone_normalized,
            owner_user_id = candidate.owner_user_id,
            schedule_ready = (candidate.start_date IS NOT NULL)
        FROM candidates AS candidate
        WHERE source_row.candidate_id = candidate.id
        """
    )
    op.execute(
        """
        UPDATE schedule_import_rows AS source_row
        SET full_name = entry.title,
            entry_date = entry.entry_date,
            time_from = entry.time_from,
            time_to = entry.time_to,
            organization = entry.organization,
            department = entry.department,
            comment = entry.comment,
            schedule_ready = TRUE
        FROM schedule_entries AS entry
        WHERE source_row.entry_id = entry.id
          AND source_row.candidate_id IS NULL
        """
    )
    op.execute(
        """
        UPDATE schedule_imports
        SET active_people = (
            SELECT count(*) FROM schedule_import_rows
            WHERE schedule_import_rows.import_id = schedule_imports.id
              AND schedule_import_rows.is_active = TRUE
              AND schedule_import_rows.row_type = 'person'
        )
        """
    )
    op.alter_column("schedule_import_rows", "source_identity", nullable=False)
    op.alter_column("schedule_import_rows", "warnings", nullable=False)

    op.create_foreign_key(
        "fk_schedule_import_rows_missing_import",
        "schedule_import_rows",
        "schedule_imports",
        ["missing_in_import_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_foreign_key(
        "fk_schedule_import_rows_owner",
        "schedule_import_rows",
        "users",
        ["owner_user_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_check_constraint(
        "ck_schedule_import_rows_type_valid",
        "schedule_import_rows",
        "row_type IN ('person', 'service', 'skip', 'error')",
    )
    op.create_index(
        "ix_schedule_import_rows_active_order",
        "schedule_import_rows",
        ["is_active", "row_order"],
        unique=False,
    )
    op.create_index(
        "ix_schedule_import_rows_identity",
        "schedule_import_rows",
        ["source_identity"],
        unique=False,
    )


def downgrade() -> None:
    bind = op.get_bind()
    unassigned = bind.execute(
        sa.text("SELECT count(*) FROM candidates WHERE owner_user_id IS NULL")
    ).scalar_one()
    if unassigned:
        raise RuntimeError(
            "cannot downgrade 0021: unassigned candidates exist; assign them before downgrading"
        )

    op.drop_index("ix_schedule_import_rows_identity", table_name="schedule_import_rows")
    op.drop_index("ix_schedule_import_rows_active_order", table_name="schedule_import_rows")
    op.drop_constraint("ck_schedule_import_rows_type_valid", "schedule_import_rows", type_="check")
    op.drop_constraint(
        "fk_schedule_import_rows_owner", "schedule_import_rows", type_="foreignkey"
    )
    op.drop_constraint(
        "fk_schedule_import_rows_missing_import", "schedule_import_rows", type_="foreignkey"
    )
    for name in (
        "owner_user_id",
        "warnings",
        "parse_error",
        "schedule_ready",
        "phone_normalized",
        "phone",
        "comment",
        "shift",
        "position",
        "department",
        "organization",
        "time_to",
        "time_from",
        "entry_date",
        "name_normalized",
        "full_name",
        "source_values",
        "missing_in_import_id",
        "decision",
        "sync_status",
        "is_active",
        "row_type",
        "row_order",
        "source_identity",
    ):
        op.drop_column("schedule_import_rows", name)
    for name in ("active_people", "rows_missing", "rows_unchanged", "rows_updated", "rows_added"):
        op.drop_column("schedule_imports", name)
    op.drop_column("schedule_imports", "source_columns")
    op.drop_column("schedule_imports", "source_headers")
    op.drop_column("schedule_imports", "header_row")
    op.drop_column("schedule_entries", "is_active")
    op.alter_column(
        "candidates",
        "owner_user_id",
        existing_type=sa.Uuid(),
        nullable=False,
    )
