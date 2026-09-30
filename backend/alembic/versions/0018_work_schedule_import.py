"""Work schedule Excel import — provenance tables + ``excel_import`` source.

The Excel import of the work schedule (built on top of Phase 18) adds:

* ``schedule_imports`` — an aggregate of each confirmed import: file name,
  SHA-256 fingerprint and the counters (created/matched/updated/service/
  skipped/errors) plus the author. File contents and personal data are never
  stored here.
* ``schedule_import_rows`` — one record per applied row key: ``row_key`` is a
  deterministic SHA-256 of the row content (date, normalized name/title,
  time). Re-importing the same file finds the existing keys and does not
  create duplicates. Key uniqueness is enforced within a single import; the
  links to the candidate/service entry are nulled when the target is deleted
  while the import fact itself remains.
* The candidate source vocabulary gains ``excel_import`` (the CHECK
  constraint is narrowed back on downgrade, mirroring revision 0008).

No data migration is required: imports did not exist before this revision.
Downgrade is full — both tables are dropped and the source vocabulary is
restored to its 0016 shape (refused while ``excel_import`` candidates exist).

Revision ID: 0018
Revises: 0017
Create Date: 2026-09-30
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0018"
down_revision: str | None = "0017"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # --- Новый источник кандидатов: импорт графика из Excel ---------------
    op.drop_constraint("ck_candidates_source_valid", "candidates", type_="check")
    op.create_check_constraint(
        "ck_candidates_source_valid",
        "candidates",
        "source IN ('site', 'referral', 'hh_manual', 'university', 'event', "
        "'agency', 'inbound_call', 'excel_import')",
    )

    # --- Агрегат подтверждённого импорта -----------------------------------
    op.create_table(
        "schedule_imports",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("file_name", sa.String(length=255), nullable=False),
        sa.Column("file_sha256", sa.String(length=64), nullable=False),
        sa.Column("sheet_title", sa.String(length=120), nullable=True),
        sa.Column("rows_total", sa.Integer(), nullable=False),
        sa.Column("created_candidates", sa.Integer(), nullable=False),
        sa.Column("matched_candidates", sa.Integer(), nullable=False),
        sa.Column("updated_candidates", sa.Integer(), nullable=False),
        sa.Column("service_entries", sa.Integer(), nullable=False),
        sa.Column("skipped_rows", sa.Integer(), nullable=False),
        sa.Column("error_rows", sa.Integer(), nullable=False),
        sa.Column("created_by_user_id", sa.Uuid(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["created_by_user_id"],
            ["users.id"],
            name="fk_schedule_imports_created_by",
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_schedule_imports"),
    )
    op.create_index(
        "ix_schedule_imports_file_sha256", "schedule_imports", ["file_sha256"], unique=False
    )
    op.create_index(
        "ix_schedule_imports_created_at", "schedule_imports", ["created_at"], unique=False
    )

    # --- Построчные ключи импорта (идемпотентность повторного импорта) -----
    op.create_table(
        "schedule_import_rows",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("import_id", sa.Uuid(), nullable=False),
        sa.Column("row_key", sa.String(length=64), nullable=False),
        sa.Column("sheet_row", sa.Integer(), nullable=False),
        sa.Column("result", sa.String(length=16), nullable=False),
        sa.Column("candidate_id", sa.Uuid(), nullable=True),
        sa.Column("entry_id", sa.Uuid(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["import_id"],
            ["schedule_imports.id"],
            name="fk_schedule_import_rows_import",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["candidate_id"],
            ["candidates.id"],
            name="fk_schedule_import_rows_candidate",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["entry_id"],
            ["schedule_entries.id"],
            name="fk_schedule_import_rows_entry",
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_schedule_import_rows"),
        sa.UniqueConstraint("import_id", "row_key", name="uq_schedule_import_rows_key"),
    )
    op.create_index(
        "ix_schedule_import_rows_row_key", "schedule_import_rows", ["row_key"], unique=False
    )


def downgrade() -> None:
    bind = op.get_bind()
    imported = bind.execute(
        sa.text("SELECT count(*) FROM candidates WHERE source = 'excel_import'")
    ).scalar_one()
    if imported:
        raise RuntimeError(
            "cannot downgrade 0018: candidates with source 'excel_import' exist "
            "(remove or re-source them, or restore from backup-forward instead)"
        )
    op.drop_index("ix_schedule_import_rows_row_key", table_name="schedule_import_rows")
    op.drop_table("schedule_import_rows")
    op.drop_index("ix_schedule_imports_created_at", table_name="schedule_imports")
    op.drop_index("ix_schedule_imports_file_sha256", table_name="schedule_imports")
    op.drop_table("schedule_imports")
    op.drop_constraint("ck_candidates_source_valid", "candidates", type_="check")
    op.create_check_constraint(
        "ck_candidates_source_valid",
        "candidates",
        "source IN ('site', 'referral', 'hh_manual', 'university', 'event', "
        "'agency', 'inbound_call')",
    )
