"""candidate transfer history: immutable business record

Adds ``candidate_transfers`` — the immutable ownership-transfer history for
candidates (initiator, previous owner, new owner, reason, timestamp). The
audit log stays an audit trail; this table is the business history of the
candidate.

PostgreSQL-native types are used (UUID primary keys defaulting to
``gen_random_uuid()`` via the pgcrypto extension from revision 0001,
``TIMESTAMP WITH TIME ZONE``). The migration is fully reversible.

Revision ID: 0004
Revises: 0003
Create Date: 2026-09-03
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_UUID = postgresql.UUID(as_uuid=True)
_UUID_SERVER_DEFAULT = sa.text("gen_random_uuid()")


def upgrade() -> None:
    op.create_table(
        "candidate_transfers",
        sa.Column("id", _UUID, nullable=False, server_default=_UUID_SERVER_DEFAULT),
        sa.Column(
            "candidate_id",
            _UUID,
            sa.ForeignKey("candidates.id", ondelete="CASCADE", name="fk_transfers_candidate_id"),
            nullable=False,
        ),
        sa.Column(
            "initiator_user_id",
            _UUID,
            sa.ForeignKey("users.id", ondelete="RESTRICT", name="fk_transfers_initiator_user_id"),
            nullable=False,
        ),
        sa.Column(
            "from_user_id",
            _UUID,
            sa.ForeignKey("users.id", ondelete="RESTRICT", name="fk_transfers_from_user_id"),
            nullable=False,
        ),
        sa.Column(
            "to_user_id",
            _UUID,
            sa.ForeignKey("users.id", ondelete="RESTRICT", name="fk_transfers_to_user_id"),
            nullable=False,
        ),
        sa.Column("reason", sa.String(length=500), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "length(trim(reason)) > 0",
            name="ck_candidate_transfers_reason_not_blank",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_candidate_transfers_candidate_id", "candidate_transfers", ["candidate_id"])
    op.create_index(
        "ix_candidate_transfers_initiator_user_id",
        "candidate_transfers",
        ["initiator_user_id"],
    )
    op.create_index("ix_candidate_transfers_created_at", "candidate_transfers", ["created_at"])


def downgrade() -> None:
    op.drop_index("ix_candidate_transfers_created_at", table_name="candidate_transfers")
    op.drop_index("ix_candidate_transfers_initiator_user_id", table_name="candidate_transfers")
    op.drop_index("ix_candidate_transfers_candidate_id", table_name="candidate_transfers")
    op.drop_table("candidate_transfers")
