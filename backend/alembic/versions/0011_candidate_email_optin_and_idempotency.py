"""Candidate email double opt-in and manual-send idempotency (phase 10 rework).

PR #14 rework. Two additions on top of revision 0010:

* ``candidate_email_confirm_tokens`` — one-shot expiring tokens of the
  candidate's email double opt-in. The raw token is an HMAC of the
  server secret and the row id: it is re-derived in memory by the worker
  right before the provider call and never persisted — only its SHA-256
  hash is stored, and the queued letter body carries a placeholder
  instead of the URL. A partial unique index enforces at most one
  unconsumed (active) token per candidate. The token is bound to the
  normalized card address it was issued for. The email consent may from
  now on be granted only by the candidate's own click
  (``source='email_confirm'``) — the
  ``ck_candidate_channel_consents_source_valid`` CHECK is recreated to
  allow that value (no data change: previously granted
  ``hr_recorded`` email consents are no longer honoured by the code,
  which requires the confirmed source and address pin).
* ``candidate_message_requests`` — idempotency records of manual
  candidate-message HTTP requests: the client-generated key is bound to
  the acting user, the candidate and a SHA-256 of the exact payload; the
  stored response is replayed verbatim on retries with the same payload.
* ``notification_outbox.object_version`` — snapshot of the business
  object's optimistic version at queue time (the interview's ``version``)
  so the worker can refuse to send a message whose event mutated after
  rendering (reschedule/cancel/complete all bump the version).
* ``notification_type`` vocabulary gains ``candidate_email_confirm`` —
  the double opt-in letter itself (CHECKs on ``notifications`` and
  ``notification_outbox`` recreated).

No secrets are stored. Reversible: downgrade drops the new tables and
column and restores the previous CHECK constraints.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0011"
down_revision: str | None = "0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_UUID = postgresql.UUID(as_uuid=True)

# Vocabulary after this revision (phase 8 + 9 + candidate types + the
# double opt-in letter).
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
    "candidate_email_confirm",
]
_NOTIFICATION_TYPES_PREVIOUS = [
    value for value in _NOTIFICATION_TYPES if value != "candidate_email_confirm"
]


def _in_list(values: list[str]) -> str:
    return ", ".join(f"'{value}'" for value in values)


def upgrade() -> None:
    # --- The double opt-in letter joins the notification vocabulary -----
    op.drop_constraint("ck_notification_outbox_type_valid", "notification_outbox", type_="check")
    op.create_check_constraint(
        "ck_notification_outbox_type_valid",
        "notification_outbox",
        f"notification_type IN ({_in_list(_NOTIFICATION_TYPES)})",
    )
    op.drop_constraint("ck_notifications_type_valid", "notifications", type_="check")
    op.create_check_constraint(
        "ck_notifications_type_valid",
        "notifications",
        f"type IN ({_in_list(_NOTIFICATION_TYPES)})",
    )

    # --- Email consent source: the candidate's own confirmation click ----
    op.drop_constraint(
        "ck_candidate_channel_consents_source_valid", "candidate_channel_consents", type_="check"
    )
    op.create_check_constraint(
        "ck_candidate_channel_consents_source_valid",
        "candidate_channel_consents",
        "source IN ('hr_recorded', 'telegram_start', 'email_confirm')",
    )

    # --- Outbox: the business-object version snapshot --------------------
    op.add_column(
        "notification_outbox",
        sa.Column("object_version", sa.Integer(), nullable=True),
    )

    # --- Double opt-in tokens -------------------------------------------
    op.create_table(
        "candidate_email_confirm_tokens",
        sa.Column("id", _UUID, primary_key=True),
        sa.Column(
            "candidate_id",
            _UUID,
            sa.ForeignKey("candidates.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("token_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("email_normalized", sa.String(254), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("consume_reason", sa.String(16), nullable=True),
    )
    op.create_index(
        "ix_candidate_email_confirm_tokens_candidate_id",
        "candidate_email_confirm_tokens",
        ["candidate_id"],
    )
    op.create_index(
        "ix_candidate_email_confirm_tokens_expires_at",
        "candidate_email_confirm_tokens",
        ["expires_at"],
    )
    # At most one unconsumed (active) token per candidate: the DB-level
    # backstop of the initiation serialization.
    op.create_index(
        "uq_candidate_email_confirm_tokens_one_active",
        "candidate_email_confirm_tokens",
        ["candidate_id"],
        unique=True,
        postgresql_where=sa.text("consumed_at IS NULL"),
    )

    # --- Manual-send idempotency records ---------------------------------
    op.create_table(
        "candidate_message_requests",
        sa.Column("id", _UUID, primary_key=True),
        sa.Column("idempotency_key", sa.String(255), nullable=False, unique=True),
        sa.Column(
            "user_id",
            _UUID,
            sa.ForeignKey("users.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "candidate_id",
            _UUID,
            sa.ForeignKey("candidates.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("message_type", sa.String(48), nullable=False),
        sa.Column("payload_hash", sa.String(64), nullable=False),
        sa.Column("response", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_candidate_message_requests_user_id",
        "candidate_message_requests",
        ["user_id"],
    )
    op.create_index(
        "ix_candidate_message_requests_candidate_id",
        "candidate_message_requests",
        ["candidate_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_candidate_message_requests_candidate_id", table_name="candidate_message_requests"
    )
    op.drop_index("ix_candidate_message_requests_user_id", table_name="candidate_message_requests")
    op.drop_table("candidate_message_requests")

    op.drop_index(
        "ix_candidate_email_confirm_tokens_expires_at",
        table_name="candidate_email_confirm_tokens",
    )
    op.drop_index(
        "uq_candidate_email_confirm_tokens_one_active",
        table_name="candidate_email_confirm_tokens",
    )
    op.drop_index(
        "ix_candidate_email_confirm_tokens_candidate_id",
        table_name="candidate_email_confirm_tokens",
    )
    op.drop_table("candidate_email_confirm_tokens")

    op.drop_column("notification_outbox", "object_version")

    # Rows of the removed type would violate the restored CHECKs — remove
    # them (downgrade is a schema rollback; the double opt-in flow is gone).
    op.execute(
        "DELETE FROM notification_outbox WHERE notification_type = 'candidate_email_confirm'"
    )
    op.execute("DELETE FROM notifications WHERE type = 'candidate_email_confirm'")

    op.drop_constraint(
        "ck_candidate_channel_consents_source_valid", "candidate_channel_consents", type_="check"
    )
    op.create_check_constraint(
        "ck_candidate_channel_consents_source_valid",
        "candidate_channel_consents",
        "source IN ('hr_recorded', 'telegram_start')",
    )

    op.drop_constraint("ck_notifications_type_valid", "notifications", type_="check")
    op.create_check_constraint(
        "ck_notifications_type_valid",
        "notifications",
        f"type IN ({_in_list(_NOTIFICATION_TYPES_PREVIOUS)})",
    )
    op.drop_constraint("ck_notification_outbox_type_valid", "notification_outbox", type_="check")
    op.create_check_constraint(
        "ck_notification_outbox_type_valid",
        "notification_outbox",
        f"notification_type IN ({_in_list(_NOTIFICATION_TYPES_PREVIOUS)})",
    )
