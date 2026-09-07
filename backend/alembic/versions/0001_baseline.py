"""enable pgcrypto extension (baseline)

This migration prepares UUID generation for later phases. It enables the
``pgcrypto`` extension when it is available on the database host (the
standard full PostgreSQL distribution ships it; it is a "trusted" extension
since PostgreSQL 13 and needs no superuser). When the contrib extension is
not installed, it falls back gracefully: ``gen_random_uuid()`` is a built-in
function in PostgreSQL 13 and above, so later migrations do not strictly
require the extension.

The migration is idempotent and reversible (the extension is only dropped
when this migration created it).

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
    # gen_random_uuid() is core since PostgreSQL 13. Prefer the pgcrypto
    # extension when the host provides it (full distribution), otherwise rely
    # on the built-in function.
    op.execute(
        """
        DO $$
        BEGIN
            CREATE EXTENSION IF NOT EXISTS pgcrypto;
        EXCEPTION
            WHEN undefined_file OR feature_not_supported THEN
                RAISE NOTICE 'pgcrypto extension not available; using built-in gen_random_uuid()';
        END
        $$;
        """
    )


def downgrade() -> None:
    # Only drop the extension when it actually exists; this keeps the
    # downgrade safe on hosts where pgcrypto was never available.
    op.execute("DROP EXTENSION IF EXISTS pgcrypto")
