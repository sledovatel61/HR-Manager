"""events and their immutable business history

Adds ``events`` (calendar events bound to candidates: calls, interviews,
reminders) and ``event_history`` (one immutable row per event mutation with
typed old/new values for safe fields — the business history; the audit log
remains the security trail).

Timestamps are ``TIMESTAMP WITH TIME ZONE`` and stored in UTC. PostgreSQL-
native UUID primary keys use the pgcrypto extension from revision 0001.
Physical event deletion does not exist; rows cascade with their candidate.

Revision ID: 0005
Revises: 0004
Create Date: 2026-09-03
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_UUID = postgresql.UUID(as_uuid=True)
_UUID_SERVER_DEFAULT = sa.text("gen_random_uuid()")


def upgrade() -> None:
    op.create_table(
        "events",
        sa.Column("id", _UUID, nullable=False, server_default=_UUID_SERVER_DEFAULT),
        sa.Column(
            "candidate_id",
            _UUID,
            sa.ForeignKey("candidates.id", ondelete="CASCADE", name="fk_events_candidate_id"),
            nullable=False,
        ),
        sa.Column(
            "author_user_id",
            _UUID,
            sa.ForeignKey("users.id", ondelete="RESTRICT", name="fk_events_author_user_id"),
            nullable=False,
        ),
        sa.Column(
            "assignee_user_id",
            _UUID,
            sa.ForeignKey("users.id", ondelete="RESTRICT", name="fk_events_assignee_user_id"),
            nullable=False,
        ),
        sa.Column("type", sa.String(length=32), nullable=False),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("starts_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ends_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("remind_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False, server_default=sa.text("1")),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "type IN ('call', 'interview', 'reminder')",
            name="ck_events_type_valid",
        ),
        sa.CheckConstraint(
            "status IN ('scheduled', 'completed', 'postponed')",
            name="ck_events_status_valid",
        ),
        sa.CheckConstraint("length(trim(title)) > 0", name="ck_events_title_not_blank"),
        sa.CheckConstraint(
            "ends_at IS NULL OR ends_at > starts_at",
            name="ck_events_ends_after_starts",
        ),
        sa.CheckConstraint(
            "remind_at IS NULL OR remind_at <= starts_at",
            name="ck_events_remind_before_start",
        ),
        sa.CheckConstraint(
            "(status = 'completed' AND completed_at IS NOT NULL) "
            "OR (status <> 'completed' AND completed_at IS NULL)",
            name="ck_events_completed_at_consistent",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_events_candidate_id", "events", ["candidate_id"])
    op.create_index("ix_events_assignee_user_id", "events", ["assignee_user_id"])
    op.create_index("ix_events_starts_at", "events", ["starts_at"])
    op.create_index("ix_events_status", "events", ["status"])

    op.create_table(
        "event_history",
        sa.Column("id", _UUID, nullable=False, server_default=_UUID_SERVER_DEFAULT),
        sa.Column(
            "event_id",
            _UUID,
            sa.ForeignKey("events.id", ondelete="CASCADE", name="fk_event_history_event_id"),
            nullable=False,
        ),
        sa.Column(
            "changed_by_user_id",
            _UUID,
            sa.ForeignKey("users.id", ondelete="RESTRICT", name="fk_event_history_changed_by"),
            nullable=False,
        ),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("status_old", sa.String(length=32), nullable=True),
        sa.Column("status_new", sa.String(length=32), nullable=True),
        sa.Column("starts_at_old", sa.DateTime(timezone=True), nullable=True),
        sa.Column("starts_at_new", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ends_at_old", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ends_at_new", sa.DateTime(timezone=True), nullable=True),
        sa.Column("remind_at_old", sa.DateTime(timezone=True), nullable=True),
        sa.Column("remind_at_new", sa.DateTime(timezone=True), nullable=True),
        sa.Column("assignee_user_id_old", _UUID, nullable=True),
        sa.Column("assignee_user_id_new", _UUID, nullable=True),
        sa.Column("title_changed", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("note_changed", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "kind IN ('created', 'updated', 'rescheduled', 'completed', "
            "'postponed', 'assignee_changed')",
            name="ck_event_history_kind_valid",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_event_history_event_id", "event_history", ["event_id"])
    op.create_index("ix_event_history_created_at", "event_history", ["created_at"])


def downgrade() -> None:
    op.drop_index("ix_event_history_created_at", table_name="event_history")
    op.drop_index("ix_event_history_event_id", table_name="event_history")
    op.drop_table("event_history")
    op.drop_index("ix_events_status", table_name="events")
    op.drop_index("ix_events_starts_at", table_name="events")
    op.drop_index("ix_events_assignee_user_id", table_name="events")
    op.drop_index("ix_events_candidate_id", table_name="events")
    op.drop_table("events")
