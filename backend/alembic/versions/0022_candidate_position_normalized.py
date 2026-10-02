"""Candidate position normalized: locale-independent «Должность» filter.

Revision 0022, ``down_revision`` is 0021 — ``0021_schedule_import_sync_and_assignment``
from ``main``, which took the number 0021 while this branch was being written.
Two files with the same revision id make Alembic warn «Revision NNNN is present
more than once» and silently drop one branch from the chain (this already
happened once in the project — see the docstring of ``0020``), so this
migration is renumbered to 0022 and the chain stays linear:
0020 -> 0021 (main, schedule import sync) -> 0022 (this one).

Why a stored column and not ``lower(position)`` in SQL
------------------------------------------------------
The «Должность» filter (customer requirement, see
``prompts/BENTO_ARCTIC_IMPLEMENTATION_PROMPT.md`` §7.1) compares free text
exactly, ignoring case. The first implementation compared
``lower(position) = <casefolded input>`` in SQL, which silently depends on the
database locale:

* SQLite — ``lower()`` is ASCII-only, so «Монтажник РЭА» never matched;
* PostgreSQL with a C/POSIX locale — the same failure, just harder to notice
  because it only shows up on Cyrillic data.

The project already solves this for names, phones and e-mails the same way:
the normalized value is computed in Python (``app.utils``, ``casefold``) and
stored next to the original. This migration adds the fourth such column.

What it changes
---------------
* new column ``candidates.position_normalized``, ``VARCHAR(200) NOT NULL
  DEFAULT ''`` — never NULL, so the index and the equality filter stay simple
  and candidates without a position are simply not matched by any non-empty
  filter value;
* index ``ix_candidates_position_normalized`` — the filter is selective
  (one position out of many), the board is read far more often than written;
* backfill of existing rows in Python, so it is correct on any database and
  any locale, including rows this migration could not lower() in SQL.

The column is not exposed in the API: it is an implementation detail of the
filter and of duplicate-free suggestion lists, exactly like
``full_name_normalized``.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0022"
down_revision = "0021"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "candidates",
        sa.Column(
            "position_normalized",
            sa.String(length=200),
            nullable=False,
            server_default="",
        ),
    )
    op.create_index("ix_candidates_position_normalized", "candidates", ["position_normalized"])

    # Backfill in Python: casefold handles Cyrillic on every backend and every
    # locale, which is the whole point of the column.
    connection = op.get_bind()
    rows = connection.execute(sa.text("SELECT id, position FROM candidates")).fetchall()
    for candidate_id, position in rows:
        normalized = " ".join((position or "").split()).casefold()
        connection.execute(
            sa.text("UPDATE candidates SET position_normalized = :n WHERE id = :id"),
            {"n": normalized, "id": candidate_id},
        )


def downgrade() -> None:
    op.drop_index("ix_candidates_position_normalized", table_name="candidates")
    op.drop_column("candidates", "position_normalized")
