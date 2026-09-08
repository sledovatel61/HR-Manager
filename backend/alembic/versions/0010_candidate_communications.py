"""One-way candidate communications (phase 10).

This revision extends the accepted phase-8/9 outbox with candidate-addressed
rows and adds the candidate channel/consent tables:

* ``notification_outbox.recipient_candidate_id`` — the row is a one-way
  message to a *candidate*; exactly one recipient column must be set
  (the two-way CHECK is recreated as a three-way one). The concrete
  address/chat id is resolved by the worker from the candidate's own
  consented state at send time — never stored from a client payload;
* ``notification_type`` vocabulary gains the six candidate message types
  (CHECKs on ``notifications`` and ``notification_outbox`` recreated,
  column widths raised: 32 -> 48);
* ``audit_log.action`` widened to 64 (new phase-10 audit actions are
  longer than 32 chars; widening a varchar is metadata-only in
  PostgreSQL);
* ``candidate_telegram_links`` — the candidate's voluntary Telegram
  binding (numeric ``chat_id`` only, soft revoke, PII-free stats) with a
  partial unique index over active chats;
* ``candidate_telegram_link_tokens`` — one-shot expiring invitation
  tokens stored as SHA-256 hashes;
* ``candidate_channel_consents`` — the per-channel consent decision
  (fail-closed: no row or ``granted=false`` never sends; email consent is
  pinned to the normalized address it covered).

No secrets are stored (bot token and SMTP credentials remain
environment-only). Reversible: downgrade drops the new tables/columns and
restores the previous constraints and widths.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0010"
down_revision: str | None = "0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_UUID = postgresql.UUID(as_uuid=True)

# Vocabulary after this revision (phase 8 + 9 + the six candidate types).
_NOTIFICATION_TYPES = [
    "event_assigned",
    "event_approaching",
    "event_overdue",
    "event_rescheduled",
    "event_cancelled",
    "candidate_transferred",
    "reminder_due",
    "reminder_overdue",
    "system_alert",
    "candidate_interview_scheduled",
    "candidate_interview_reminder",
    "candidate_interview_rescheduled",
    "candidate_interview_cancelled",
    "candidate_document_request",
    "candidate_document_reminder",
]
_NOTIFICATION_TYPES_PREVIOUS = [
    "event_assigned",
    "event_approaching",
    "event_overdue",
    "event_rescheduled",
    "event_cancelled",
    "candidate_transferred",
    "reminder_due",
    "reminder_overdue",
    "system_alert",
]


def _in_list(values: list[str]) -> str:
    return ", ".join(f"'{value}'" for value in values)


_THREE_WAY_RECIPIENT = (
    "(recipient_user_id IS NOT NULL AND external_recipient IS NULL "
    "AND recipient_candidate_id IS NULL) "
    "OR (recipient_user_id IS NULL AND external_recipient IS NOT NULL "
    "AND recipient_candidate_id IS NULL) "
    "OR (recipient_user_id IS NULL AND external_recipient IS NULL "
    "AND recipient_candidate_id IS NOT NULL)"
)
_TWO_WAY_RECIPIENT = "(recipient_user_id IS NOT NULL) <> (external_recipient IS NOT NULL)"


def upgrade() -> None:
    # --- Outbox: candidate recipient + extended type vocabulary -----------
    op.add_column(
        "notification_outbox",
        sa.Column("recipient_candidate_id", _UUID, nullable=True),
    )
    op.create_foreign_key(
        "fk_notification_outbox_recipient_candidate",
        "notification_outbox",
        "candidates",
        ["recipient_candidate_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_index(
        "ix_notification_outbox_recipient_candidate",
        "notification_outbox",
        ["recipient_candidate_id"],
    )
    op.drop_constraint(
        "ck_notification_outbox_exactly_one_recipient", "notification_outbox", type_="check"
    )
    op.create_check_constraint(
        "ck_notification_outbox_exactly_one_recipient",
        "notification_outbox",
        _THREE_WAY_RECIPIENT,
    )
    op.alter_column(
        "notification_outbox",
        "notification_type",
        existing_type=sa.String(32),
        type_=sa.String(48),
        existing_nullable=False,
    )
    op.drop_constraint("ck_notification_outbox_type_valid", "notification_outbox", type_="check")
    op.create_check_constraint(
        "ck_notification_outbox_type_valid",
        "notification_outbox",
        f"notification_type IN ({_in_list(_NOTIFICATION_TYPES)})",
    )

    # --- Notifications: same vocabulary (candidate types are never written
    #     there, but the model and the schema must not drift) ---------------
    op.alter_column(
        "notifications",
        "type",
        existing_type=sa.String(32),
        type_=sa.String(48),
        existing_nullable=False,
    )
    op.drop_constraint("ck_notifications_type_valid", "notifications", type_="check")
    op.create_check_constraint(
        "ck_notifications_type_valid",
        "notifications",
        f"type IN ({_in_list(_NOTIFICATION_TYPES)})",
    )

    # --- Audit: longer phase-10 action names (metadata-only widening) ------
    op.alter_column(
        "audit_log",
        "action",
        existing_type=sa.String(32),
        type_=sa.String(64),
        existing_nullable=False,
    )

    # --- Candidate Telegram bindings ---------------------------------------
    op.create_table(
        "candidate_telegram_links",
        sa.Column("candidate_id", _UUID, primary_key=True),
        sa.Column("chat_id", sa.BigInteger(), nullable=True),
        sa.Column("linked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoke_reason", sa.String(64), nullable=True),
        sa.Column("last_sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error_class", sa.String(32), nullable=True),
        sa.Column("last_error_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.ForeignKeyConstraint(["candidate_id"], ["candidates.id"], ondelete="CASCADE"),
    )
    op.create_index(
        "uq_candidate_telegram_links_chat_id_active",
        "candidate_telegram_links",
        ["chat_id"],
        unique=True,
        postgresql_where=sa.text("revoked_at IS NULL AND chat_id IS NOT NULL"),
        sqlite_where=sa.text("revoked_at IS NULL AND chat_id IS NOT NULL"),
    )

    op.create_table(
        "candidate_telegram_link_tokens",
        sa.Column("id", _UUID, primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("candidate_id", _UUID, nullable=False),
        sa.Column("token_hash", sa.String(64), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("consume_reason", sa.String(16), nullable=True),
        sa.ForeignKeyConstraint(["candidate_id"], ["candidates.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("token_hash", name="uq_candidate_telegram_link_tokens_token_hash"),
    )
    op.create_index(
        "ix_candidate_telegram_link_tokens_candidate_id",
        "candidate_telegram_link_tokens",
        ["candidate_id"],
    )
    op.create_index(
        "ix_candidate_telegram_link_tokens_expires_at",
        "candidate_telegram_link_tokens",
        ["expires_at"],
    )

    # --- Per-channel candidate consent --------------------------------------
    op.create_table(
        "candidate_channel_consents",
        sa.Column("candidate_id", _UUID, primary_key=True),
        sa.Column("channel", sa.String(16), primary_key=True),
        sa.Column("granted", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("granted_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("granted_by_user_id", _UUID, nullable=True),
        sa.Column("source", sa.String(32), nullable=False),
        sa.Column("policy_version", sa.String(32), nullable=True),
        sa.Column("email_normalized", sa.String(254), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.ForeignKeyConstraint(["candidate_id"], ["candidates.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["granted_by_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.CheckConstraint(
            "channel IN ('email', 'telegram')",
            name="ck_candidate_channel_consents_channel_valid",
        ),
        sa.CheckConstraint(
            "granted IS NOT NULL AND granted_at IS NOT NULL",
            name="ck_candidate_channel_consents_decision_complete",
        ),
        sa.CheckConstraint(
            "source IN ('hr_recorded', 'telegram_start')",
            name="ck_candidate_channel_consents_source_valid",
        ),
    )
    op.create_index(
        "ix_candidate_channel_consents_candidate_id",
        "candidate_channel_consents",
        ["candidate_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_candidate_channel_consents_candidate_id",
        table_name="candidate_channel_consents",
    )
    op.drop_table("candidate_channel_consents")
    op.drop_index(
        "ix_candidate_telegram_link_tokens_expires_at",
        table_name="candidate_telegram_link_tokens",
    )
    op.drop_index(
        "ix_candidate_telegram_link_tokens_candidate_id",
        table_name="candidate_telegram_link_tokens",
    )
    op.drop_table("candidate_telegram_link_tokens")
    op.drop_index(
        "uq_candidate_telegram_links_chat_id_active",
        table_name="candidate_telegram_links",
    )
    op.drop_table("candidate_telegram_links")

    op.alter_column(
        "audit_log",
        "action",
        existing_type=sa.String(64),
        type_=sa.String(32),
        existing_nullable=False,
    )
    op.drop_constraint("ck_notifications_type_valid", "notifications", type_="check")
    op.create_check_constraint(
        "ck_notifications_type_valid",
        "notifications",
        f"type IN ({_in_list(_NOTIFICATION_TYPES_PREVIOUS)})",
    )
    op.alter_column(
        "notifications",
        "type",
        existing_type=sa.String(48),
        type_=sa.String(32),
        existing_nullable=False,
    )
    op.drop_constraint("ck_notification_outbox_type_valid", "notification_outbox", type_="check")
    op.create_check_constraint(
        "ck_notification_outbox_type_valid",
        "notification_outbox",
        f"notification_type IN ({_in_list(_NOTIFICATION_TYPES_PREVIOUS)})",
    )
    op.alter_column(
        "notification_outbox",
        "notification_type",
        existing_type=sa.String(48),
        type_=sa.String(32),
        existing_nullable=False,
    )
    op.drop_constraint(
        "ck_notification_outbox_exactly_one_recipient", "notification_outbox", type_="check"
    )
    op.create_check_constraint(
        "ck_notification_outbox_exactly_one_recipient",
        "notification_outbox",
        _TWO_WAY_RECIPIENT,
    )
    op.drop_index(
        "ix_notification_outbox_recipient_candidate",
        table_name="notification_outbox",
    )
    op.drop_constraint(
        "fk_notification_outbox_recipient_candidate", "notification_outbox", type_="foreignkey"
    )
    op.drop_column("notification_outbox", "recipient_candidate_id")
