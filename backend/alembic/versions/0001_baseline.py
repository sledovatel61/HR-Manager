"""enable pgcrypto extension (baseline)

Phase 1 ships no business tables yet, so this migration is a safe
infrastructure baseline: it enables the ``pgcrypto`` extension that the user
and candidate schemas of the next phases will use for UUID generation.

``pgcrypto`` is a "trusted" extension since PostgreSQL 13, so it does not
require superuser rights. The migration is idempotent and reversible.

Revision ID: 0001
Revises:
Create Date: 2026-09-02
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto")


def downgrade() -> None:
    op.execute("DROP EXTENSION IF EXISTS pgcrypto")
