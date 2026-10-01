"""«Библиотека HR»: category, summary and seed key for document templates.

Revision 0018 (library rework of the phase 16 templates section).

Adds three columns to ``document_templates`` — no new tables, no parallel
entity:

* ``category`` — closed-dictionary key for the library screen filters
  (``''`` = uncategorized/legacy), guarded by a CHECK constraint;
* ``summary`` — the one-sentence purpose shown on a library card;
* ``seed_key`` — marks a template provisioned by the built-in starter
  catalog (:mod:`app.library_seed`). A partial unique index lets the seed run
  stay idempotent while keeping ``NULL`` for every material the HR created.

None of the columns is part of a version's immutable content, and the phase 16
immutability triggers do not reference them, so existing rows, versions,
generated snapshots, backup/restore and the upgrade/downgrade path are
unaffected. The downgrade removes exactly what the upgrade added.
"""

import sqlalchemy as sa

from alembic import op

revision = "0018"
down_revision = "0017"
branch_labels = None
depends_on = None

_CATEGORY_VALUES = (
    "interview",
    "candidate_docs",
    "calls",
    "onboarding",
    "memos",
    "position",
)


def upgrade() -> None:
    op.add_column(
        "document_templates",
        sa.Column("category", sa.String(length=32), nullable=False, server_default=""),
    )
    op.add_column(
        "document_templates",
        sa.Column("summary", sa.String(length=300), nullable=False, server_default=""),
    )
    op.add_column(
        "document_templates",
        sa.Column("seed_key", sa.String(length=64), nullable=True),
    )
    op.create_check_constraint(
        "ck_document_templates_category",
        "document_templates",
        "category IN (''" + "".join(f", '{value}'" for value in _CATEGORY_VALUES) + ")",
    )
    op.create_index(
        "uq_document_templates_seed_key",
        "document_templates",
        ["seed_key"],
        unique=True,
        postgresql_where=sa.text("seed_key IS NOT NULL"),
        sqlite_where=sa.text("seed_key IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index(
        "uq_document_templates_seed_key",
        table_name="document_templates",
        postgresql_where=sa.text("seed_key IS NOT NULL"),
        sqlite_where=sa.text("seed_key IS NOT NULL"),
    )
    op.drop_constraint("ck_document_templates_category", "document_templates", type_="check")
    op.drop_column("document_templates", "seed_key")
    op.drop_column("document_templates", "summary")
    op.drop_column("document_templates", "category")
