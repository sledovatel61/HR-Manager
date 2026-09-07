"""candidates database: candidates, interactions, audit link

Adds the candidates phase schema:

* ``candidates``             — personal data (raw + normalized phone/email),
                               source, position, owner, funnel stage with a
                               materialized ``stage_position``, timestamps and
                               soft-delete columns;
* ``candidate_interactions`` — immutable interaction history entries;
* ``audit_log.candidate_id`` — nullable FK linking candidate lifecycle audit
                               events to their candidate.

PostgreSQL-native types are used (UUID primary keys defaulting to
``gen_random_uuid()`` via the pgcrypto extension from revision 0001,
``TIMESTAMP WITH TIME ZONE``). The migration is fully reversible.

Revision ID: 0003
Revises: 0002
Create Date: 2026-09-02
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_UUID = postgresql.UUID(as_uuid=True)
_UUID_SERVER_DEFAULT = sa.text("gen_random_uuid()")

_CANDIDATE_STAGES = (
    "new",
    "contacted",
    "reached",
    "interview_scheduled",
    "interview_done",
    "offer",
    "hired",
    "started",
    "probation",
    "fired",
    "rejected",
)

_CANDIDATE_SOURCES = (
    "site",
    "referral",
    "hh_manual",
    "university",
    "event",
    "agency",
    "inbound_call",
)

_INTERACTION_TYPES = ("call", "email", "meeting", "note", "status_change")


def upgrade() -> None:
    op.create_table(
        "candidates",
        sa.Column("id", _UUID, nullable=False, server_default=_UUID_SERVER_DEFAULT),
        sa.Column("full_name", sa.String(length=200), nullable=False),
        # Unicode casefold for search (normalized in Python, see models.py).
        sa.Column("full_name_normalized", sa.String(length=200), nullable=False),
        sa.Column("phone", sa.String(length=32), nullable=True),
        sa.Column("phone_normalized", sa.String(length=32), nullable=True),
        sa.Column("email", sa.String(length=254), nullable=True),
        sa.Column("email_normalized", sa.String(length=254), nullable=True),
        sa.Column("source", sa.String(length=32), nullable=False),
        sa.Column("position", sa.String(length=200), nullable=False, server_default=""),
        sa.Column(
            "owner_user_id",
            _UUID,
            sa.ForeignKey("users.id", ondelete="RESTRICT", name="fk_candidates_owner_user_id"),
            nullable=False,
        ),
        sa.Column("stage", sa.String(length=32), nullable=False, server_default="new"),
        sa.Column("stage_position", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "deleted_by_user_id",
            _UUID,
            sa.ForeignKey("users.id", ondelete="SET NULL", name="fk_candidates_deleted_by_user_id"),
            nullable=True,
        ),
        sa.CheckConstraint(
            "stage IN ('" + "', '".join(_CANDIDATE_STAGES) + "')",
            name="ck_candidates_stage_valid",
        ),
        sa.CheckConstraint(
            "source IN ('" + "', '".join(_CANDIDATE_SOURCES) + "')",
            name="ck_candidates_source_valid",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_candidates"),
    )
    op.create_index("ix_candidates_owner_user_id", "candidates", ["owner_user_id"])
    op.create_index("ix_candidates_stage", "candidates", ["stage"])
    op.create_index("ix_candidates_full_name_normalized", "candidates", ["full_name_normalized"])
    op.create_index("ix_candidates_phone_normalized", "candidates", ["phone_normalized"])
    op.create_index("ix_candidates_email_normalized", "candidates", ["email_normalized"])
    op.create_index("ix_candidates_deleted_at", "candidates", ["deleted_at"])
    op.create_index("ix_candidates_updated_at", "candidates", ["updated_at"])

    op.create_table(
        "candidate_interactions",
        sa.Column("id", _UUID, nullable=False, server_default=_UUID_SERVER_DEFAULT),
        sa.Column(
            "candidate_id",
            _UUID,
            sa.ForeignKey(
                "candidates.id",
                ondelete="CASCADE",
                name="fk_candidate_interactions_candidate_id",
            ),
            nullable=False,
        ),
        sa.Column(
            "author_user_id",
            _UUID,
            sa.ForeignKey(
                "users.id", ondelete="RESTRICT", name="fk_candidate_interactions_author_user_id"
            ),
            nullable=False,
        ),
        sa.Column("type", sa.String(length=32), nullable=False),
        sa.Column("comment", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "type IN ('" + "', '".join(_INTERACTION_TYPES) + "')",
            name="ck_candidate_interactions_type_valid",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_candidate_interactions"),
    )
    op.create_index(
        "ix_candidate_interactions_candidate_id", "candidate_interactions", ["candidate_id"]
    )
    op.create_index(
        "ix_candidate_interactions_author_user_id", "candidate_interactions", ["author_user_id"]
    )

    op.add_column(
        "audit_log",
        sa.Column(
            "candidate_id",
            _UUID,
            sa.ForeignKey("candidates.id", ondelete="SET NULL", name="fk_audit_log_candidate_id"),
            nullable=True,
        ),
    )
    op.create_index("ix_audit_log_candidate_id", "audit_log", ["candidate_id"])


def downgrade() -> None:
    op.drop_index("ix_audit_log_candidate_id", table_name="audit_log")
    op.drop_constraint("fk_audit_log_candidate_id", "audit_log", type_="foreignkey")
    op.drop_column("audit_log", "candidate_id")

    op.drop_index("ix_candidate_interactions_author_user_id", table_name="candidate_interactions")
    op.drop_index("ix_candidate_interactions_candidate_id", table_name="candidate_interactions")
    op.drop_table("candidate_interactions")

    op.drop_index("ix_candidates_updated_at", table_name="candidates")
    op.drop_index("ix_candidates_deleted_at", table_name="candidates")
    op.drop_index("ix_candidates_email_normalized", table_name="candidates")
    op.drop_index("ix_candidates_phone_normalized", table_name="candidates")
    op.drop_index("ix_candidates_stage", table_name="candidates")
    op.drop_index("ix_candidates_full_name_normalized", table_name="candidates")
    op.drop_index("ix_candidates_owner_user_id", table_name="candidates")
    op.drop_table("candidates")
