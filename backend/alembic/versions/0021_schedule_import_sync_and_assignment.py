"""Synchronize source schedule rows without losing people or assignments.

Adds source-row snapshots to the existing schedule import tables instead of
introducing a second import subsystem. ``schedule_import_rows`` becomes the
versioned representation of person/service/skip/error rows; only the rows in
the latest import are active. Candidates created by the importer may remain
unassigned until an authorized user allocates an HR owner.

This revision depends on the candidate-attachments revision ``0020`` (PR #48)
and must be merged after it. Keep ``down_revision`` at ``0020`` so the
migration chain remains linear in the agreed merge order: #46 → #47 → #48 → this branch.

Revision ID: 0021
Revises: 0020
Create Date: 2026-10-02
"""

from collections.abc import Sequence
from typing import Any

import sqlalchemy as sa

from alembic import op

revision: str = "0021"
down_revision: str | None = "0020"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _uses_sqlite() -> bool:
    return op.get_bind().dialect.name == "sqlite"


def _alter_nullable(
    table: str,
    column: str,
    *,
    existing_type: sa.types.TypeEngine,
    nullable: bool,
) -> None:
    """Use SQLite table recreation where ALTER COLUMN is unsupported."""

    if _uses_sqlite():
        with op.batch_alter_table(table) as batch:
            batch.alter_column(column, existing_type=existing_type, nullable=nullable)
    else:
        op.alter_column(
            table,
            column,
            existing_type=existing_type,
            nullable=nullable,
        )


def _backfill_existing_rows() -> None:
    """Populate new snapshot columns using portable correlated UPDATEs.

    Scalar subqueries and SQLAlchemy Core expressions compile for both
    PostgreSQL and SQLite; no dialect-specific ``UPDATE ... FROM`` is needed.
    The old workbook bytes were not retained, so source cell arrays are
    initialized to an empty JSON array until a row is next imported.
    """

    imports = sa.table(
        "schedule_imports",
        sa.column("id", sa.Uuid()),
        sa.column("created_at", sa.DateTime(timezone=True)),
        sa.column("source_headers", sa.JSON()),
        sa.column("source_columns", sa.JSON()),
        sa.column("active_people", sa.Integer()),
    )
    rows = sa.table(
        "schedule_import_rows",
        sa.column("id", sa.Uuid()),
        sa.column("import_id", sa.Uuid()),
        sa.column("row_key", sa.String(length=64)),
        sa.column("sheet_row", sa.Integer()),
        sa.column("candidate_id", sa.Uuid()),
        sa.column("entry_id", sa.Uuid()),
        sa.column("source_identity", sa.String(length=64)),
        sa.column("row_order", sa.Integer()),
        sa.column("row_type", sa.String(length=16)),
        sa.column("is_active", sa.Boolean()),
        sa.column("source_values", sa.JSON()),
        sa.column("full_name", sa.String(length=200)),
        sa.column("name_normalized", sa.String(length=200)),
        sa.column("entry_date", sa.Date()),
        sa.column("time_from", sa.Time()),
        sa.column("time_to", sa.Time()),
        sa.column("organization", sa.String(length=120)),
        sa.column("department", sa.String(length=120)),
        sa.column("position", sa.String(length=200)),
        sa.column("shift", sa.String(length=32)),
        sa.column("comment", sa.String(length=300)),
        sa.column("phone", sa.String(length=32)),
        sa.column("phone_normalized", sa.String(length=32)),
        sa.column("schedule_ready", sa.Boolean()),
        sa.column("parse_error", sa.Text()),
        sa.column("warnings", sa.JSON()),
        sa.column("owner_user_id", sa.Uuid()),
    )
    candidates = sa.table(
        "candidates",
        sa.column("id", sa.Uuid()),
        sa.column("full_name", sa.String(length=200)),
        sa.column("full_name_normalized", sa.String(length=200)),
        sa.column("start_date", sa.Date()),
        sa.column("start_time", sa.Time()),
        sa.column("start_organization", sa.String(length=120)),
        sa.column("start_department", sa.String(length=120)),
        sa.column("position", sa.String(length=200)),
        sa.column("shift", sa.String(length=32)),
        sa.column("start_comment", sa.String(length=300)),
        sa.column("phone", sa.String(length=32)),
        sa.column("phone_normalized", sa.String(length=32)),
        sa.column("owner_user_id", sa.Uuid()),
    )
    entries = sa.table(
        "schedule_entries",
        sa.column("id", sa.Uuid()),
        sa.column("title", sa.String(length=200)),
        sa.column("entry_date", sa.Date()),
        sa.column("time_from", sa.Time()),
        sa.column("time_to", sa.Time()),
        sa.column("organization", sa.String(length=120)),
        sa.column("department", sa.String(length=120)),
        sa.column("comment", sa.String(length=300)),
    )

    op.execute(
        sa.update(imports)
        .where(imports.c.source_headers.is_(None))
        .values(
            source_headers=sa.literal_column("'[]'"),
            source_columns=sa.literal_column("'{}'"),
        )
    )

    op.execute(
        sa.update(rows).values(
            source_identity=rows.c.row_key,
            row_order=rows.c.sheet_row,
            row_type=sa.case(
                (rows.c.candidate_id.is_not(None), "person"),
                (rows.c.entry_id.is_not(None), "service"),
                else_="skip",
            ),
            source_values=sa.literal_column("'[]'"),
            warnings=sa.literal_column("'[]'"),
        )
    )

    def candidate_value(column: str) -> sa.ScalarSelect:
        return (
            sa.select(candidates.c[column])
            .where(candidates.c.id == rows.c.candidate_id)
            .scalar_subquery()
        )

    op.execute(
        sa.update(rows)
        .where(rows.c.candidate_id.is_not(None))
        .values(
            full_name=candidate_value("full_name"),
            name_normalized=candidate_value("full_name_normalized"),
            entry_date=candidate_value("start_date"),
            time_from=candidate_value("start_time"),
            organization=candidate_value("start_organization"),
            department=candidate_value("start_department"),
            position=candidate_value("position"),
            shift=candidate_value("shift"),
            comment=candidate_value("start_comment"),
            phone=candidate_value("phone"),
            phone_normalized=candidate_value("phone_normalized"),
            owner_user_id=candidate_value("owner_user_id"),
            schedule_ready=sa.case(
                (candidate_value("start_date").is_not(None), sa.true()),
                else_=sa.false(),
            ),
        )
    )

    def entry_value(column: str) -> sa.ScalarSelect:
        return sa.select(entries.c[column]).where(entries.c.id == rows.c.entry_id).scalar_subquery()

    op.execute(
        sa.update(rows)
        .where(rows.c.entry_id.is_not(None), rows.c.candidate_id.is_(None))
        .values(
            full_name=entry_value("title"),
            entry_date=entry_value("entry_date"),
            time_from=entry_value("time_from"),
            time_to=entry_value("time_to"),
            organization=entry_value("organization"),
            department=entry_value("department"),
            comment=entry_value("comment"),
            schedule_ready=sa.true(),
        )
    )

    latest_import_id = (
        sa.select(imports.c.id)
        .order_by(imports.c.created_at.desc(), imports.c.id.desc())
        .limit(1)
        .scalar_subquery()
    )
    op.execute(sa.update(rows).values(is_active=rows.c.import_id == latest_import_id))

    active_people = (
        sa.select(sa.func.count(rows.c.id))
        .where(
            rows.c.import_id == imports.c.id,
            rows.c.is_active.is_(True),
            rows.c.row_type == "person",
        )
        .correlate(imports)
        .scalar_subquery()
    )
    op.execute(sa.update(imports).values(active_people=active_people))


def upgrade() -> None:
    # Imported candidates are deliberately unassigned until a manager chooses
    # an HR owner. Existing candidates keep their current owner values.
    _alter_nullable(
        "candidates",
        "owner_user_id",
        existing_type=sa.Uuid(),
        nullable=True,
    )

    op.add_column(
        "schedule_entries",
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
    )

    op.add_column(
        "schedule_imports",
        sa.Column("header_row", sa.Integer(), nullable=False, server_default="1"),
    )
    op.add_column("schedule_imports", sa.Column("source_headers", sa.JSON(), nullable=True))
    op.add_column("schedule_imports", sa.Column("source_columns", sa.JSON(), nullable=True))
    for name in ("rows_added", "rows_updated", "rows_unchanged", "rows_missing", "active_people"):
        op.add_column(
            "schedule_imports",
            sa.Column(name, sa.Integer(), nullable=False, server_default="0"),
        )

    additions: tuple[sa.Column[Any], ...] = (
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

    _backfill_existing_rows()

    if _uses_sqlite():
        with op.batch_alter_table("schedule_imports") as batch:
            batch.alter_column("source_headers", existing_type=sa.JSON(), nullable=False)
            batch.alter_column("source_columns", existing_type=sa.JSON(), nullable=False)
        with op.batch_alter_table("schedule_import_rows") as batch:
            batch.alter_column(
                "source_identity", existing_type=sa.String(length=64), nullable=False
            )
            batch.alter_column("warnings", existing_type=sa.JSON(), nullable=False)
            batch.create_foreign_key(
                "fk_schedule_import_rows_missing_import",
                "schedule_imports",
                ["missing_in_import_id"],
                ["id"],
                ondelete="SET NULL",
            )
            batch.create_foreign_key(
                "fk_schedule_import_rows_owner",
                "users",
                ["owner_user_id"],
                ["id"],
                ondelete="SET NULL",
            )
            batch.create_check_constraint(
                "ck_schedule_import_rows_type_valid",
                "row_type IN ('person', 'service', 'skip', 'error')",
            )
    else:
        op.alter_column(
            "schedule_imports", "source_headers", existing_type=sa.JSON(), nullable=False
        )
        op.alter_column(
            "schedule_imports", "source_columns", existing_type=sa.JSON(), nullable=False
        )
        op.alter_column(
            "schedule_import_rows",
            "source_identity",
            existing_type=sa.String(length=64),
            nullable=False,
        )
        op.alter_column("schedule_import_rows", "warnings", existing_type=sa.JSON(), nullable=False)
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
    candidates = sa.table(
        "candidates",
        sa.column("owner_user_id", sa.Uuid()),
    )
    unassigned = bind.execute(
        sa.select(sa.func.count())
        .select_from(candidates)
        .where(candidates.c.owner_user_id.is_(None))
    ).scalar_one()
    if unassigned:
        raise RuntimeError(
            "cannot downgrade 0021: unassigned candidates exist; assign them before downgrading"
        )

    op.drop_index("ix_schedule_import_rows_identity", table_name="schedule_import_rows")
    op.drop_index("ix_schedule_import_rows_active_order", table_name="schedule_import_rows")
    if _uses_sqlite():
        with op.batch_alter_table("schedule_import_rows") as batch:
            batch.drop_constraint("ck_schedule_import_rows_type_valid", type_="check")
            batch.drop_constraint("fk_schedule_import_rows_owner", type_="foreignkey")
            batch.drop_constraint("fk_schedule_import_rows_missing_import", type_="foreignkey")
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
                batch.drop_column(name)
        with op.batch_alter_table("schedule_imports") as batch:
            for name in (
                "active_people",
                "rows_missing",
                "rows_unchanged",
                "rows_updated",
                "rows_added",
                "source_columns",
                "source_headers",
                "header_row",
            ):
                batch.drop_column(name)
        with op.batch_alter_table("schedule_entries") as batch:
            batch.drop_column("is_active")
    else:
        op.drop_constraint(
            "ck_schedule_import_rows_type_valid", "schedule_import_rows", type_="check"
        )
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
        for name in (
            "active_people",
            "rows_missing",
            "rows_unchanged",
            "rows_updated",
            "rows_added",
        ):
            op.drop_column("schedule_imports", name)
        op.drop_column("schedule_imports", "source_columns")
        op.drop_column("schedule_imports", "source_headers")
        op.drop_column("schedule_imports", "header_row")
        op.drop_column("schedule_entries", "is_active")

    _alter_nullable(
        "candidates",
        "owner_user_id",
        existing_type=sa.Uuid(),
        nullable=False,
    )
