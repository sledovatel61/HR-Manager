"""Candidate communications: channels, consent tokens, message history.

Phase 10 (one-way Russian operational messages to candidates) adds
candidate-scoped delivery infrastructure on top of the accepted phase-8/9
outbox/worker contour. Candidates are not internal users, so bindings and
consent live on the candidate and messages never materialize into the
internal in-app notification center.

* ``events.location`` — optional single-line venue of a scheduled event,
  used by interview messages (content never copied into history/audit);
* ``event_history.location_changed`` — phase-5 style changed-flag for the
  new content field;
* ``candidate_contact_channels`` — one row per (candidate, channel): the
  current server-side recipient snapshot (consented email address or
  voluntarily bound Telegram ``chat_id``), the audited consent grant
  (``consent_granted`` + timestamp + source + terms version) and the
  opt-out marker (``revoked_at``). A partial unique index keeps one active
  ``chat_id`` bound to at most one candidate;
* ``candidate_channel_tokens`` — one-shot expiring tokens (SHA-256 hashes
  only) for email double-opt-in links, email unsubscribe links and
  Telegram ``/start`` linking; new tokens supersede old unconsumed ones;
* ``candidate_messages`` — a candidate-facing message. Content columns are
  written once at scheduling time (transactional outbox); the delivery
  lifecycle (status/attempts/timestamps/provider id/safe error) follows the
  phase-8/9 worker discipline. This row is the immutable delivery history;
* ``candidate_message_attempts`` — append-only attempt history
  (UNIQUE(message_id, attempt_no)).

No secrets are stored; the bot token and SMTP credentials stay in the
environment. Reversible: downgrade drops the new tables/columns.
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

_MESSAGE_TYPES = (
    "interview_scheduled",
    "interview_reminder",
    "interview_rescheduled",
    "interview_cancelled",
    "documents_request",
    "documents_reminder",
    "consent_invite",
)
_SOURCES = ("manual", "event", "rule", "system")
_STATUSES = ("queued", "sending", "accepted", "delivered", "failed", "cancelled", "skipped")
_PURPOSES = ("email_consent", "telegram_link")


def _in_list(values: tuple[str, ...]) -> str:
    return ", ".join(f"'{value}'" for value in values)


def upgrade() -> None:
    # --- Events: optional single-line venue ---------------------------------
    op.add_column(
        "events",
        sa.Column("location", sa.String(200), nullable=True),
    )
    op.create_check_constraint(
        "ck_events_location_len",
        "events",
        "location IS NULL OR length(location) BETWEEN 1 AND 200",
    )
    op.add_column(
        "event_history",
        sa.Column(
            "location_changed",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )

    # --- Candidate contact channels -----------------------------------------
    op.create_table(
        "candidate_contact_channels",
        sa.Column("candidate_id", _UUID, primary_key=True),
        sa.Column("channel", sa.String(16), primary_key=True),
        sa.Column("email_address", sa.String(254), nullable=True),
        sa.Column("chat_id", sa.BigInteger(), nullable=True),
        sa.Column("linked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("consent_granted", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("consent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("consent_source", sa.String(32), nullable=True),
        sa.Column("consent_policy_version", sa.String(32), nullable=True),
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
        sa.CheckConstraint(
            "channel IN ('email', 'telegram')",
            name="ck_candidate_contact_channels_channel_valid",
        ),
        sa.CheckConstraint(
            "(channel = 'email' AND email_address IS NOT NULL AND chat_id IS NULL) "
            "OR (channel = 'telegram' AND chat_id IS NOT NULL AND email_address IS NULL) "
            "OR (channel = 'telegram' AND chat_id IS NULL AND email_address IS NULL)",
            name="ck_candidate_contact_channels_binding_shape",
        ),
        sa.CheckConstraint(
            "(consent_granted = false AND consent_at IS NULL) "
            "OR (consent_granted = true AND consent_at IS NOT NULL)",
            name="ck_candidate_contact_channels_consent_consistent",
        ),
        sa.ForeignKeyConstraint(["candidate_id"], ["candidates.id"], ondelete="CASCADE"),
    )
    # One active Telegram chat can serve at most one candidate.
    op.create_index(
        "uq_candidate_contact_channels_chat_id_active",
        "candidate_contact_channels",
        ["chat_id"],
        unique=True,
        postgresql_where=sa.text("chat_id IS NOT NULL"),
        sqlite_where=sa.text("chat_id IS NOT NULL"),
    )

    # --- Candidate channel tokens -------------------------------------------
    op.create_table(
        "candidate_channel_tokens",
        sa.Column("id", _UUID, primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("candidate_id", _UUID, nullable=False),
        sa.Column("channel", sa.String(16), nullable=False),
        sa.Column("purpose", sa.String(32), nullable=False),
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
        sa.Column("consume_chat_id", sa.BigInteger(), nullable=True),
        sa.Column("email_address", sa.String(254), nullable=True),
        sa.CheckConstraint(
            f"purpose IN ({_in_list(_PURPOSES)})",
            name="ck_candidate_channel_tokens_purpose_valid",
        ),
        sa.ForeignKeyConstraint(["candidate_id"], ["candidates.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("token_hash", name="uq_candidate_channel_tokens_token_hash"),
    )
    op.create_index(
        "ix_candidate_channel_tokens_lookup",
        "candidate_channel_tokens",
        ["candidate_id", "channel", "purpose"],
    )
    op.create_index(
        "ix_candidate_channel_tokens_expires_at",
        "candidate_channel_tokens",
        ["expires_at"],
    )

    # --- Candidate messages (immutable content + delivery lifecycle) --------
    op.create_table(
        "candidate_messages",
        sa.Column("id", _UUID, primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("candidate_id", _UUID, nullable=False),
        sa.Column("channel", sa.String(16), nullable=False),
        sa.Column("message_type", sa.String(32), nullable=False),
        sa.Column("source", sa.String(16), nullable=False),
        sa.Column("recipient_email", sa.String(254), nullable=True),
        sa.Column("recipient_chat_id", sa.BigInteger(), nullable=True),
        sa.Column("title", sa.String(300), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("template_version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("initiator_user_id", _UUID, nullable=True),
        sa.Column("event_id", _UUID, nullable=True),
        sa.Column("event_version", sa.Integer(), nullable=True),
        sa.Column("scheduled_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("scheduled_at_effective", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "queued_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("accepted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("delivered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("failed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cancelled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.String(16), nullable=False, server_default="queued"),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error_code", sa.String(64), nullable=True),
        sa.Column("error_class", sa.String(32), nullable=True),
        sa.Column("provider_message_id", sa.String(255), nullable=True),
        sa.Column("idempotency_key", sa.String(255), nullable=True),
        sa.Column("consent_snapshot", sa.JSON(), nullable=True),
        sa.Column("quiet_hours_bypassed", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
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
        sa.CheckConstraint(
            "channel IN ('email', 'telegram')",
            name="ck_candidate_messages_channel_valid",
        ),
        sa.CheckConstraint(
            f"message_type IN ({_in_list(_MESSAGE_TYPES)})",
            name="ck_candidate_messages_type_valid",
        ),
        sa.CheckConstraint(
            f"source IN ({_in_list(_SOURCES)})",
            name="ck_candidate_messages_source_valid",
        ),
        sa.CheckConstraint(
            f"status IN ({_in_list(_STATUSES)})",
            name="ck_candidate_messages_status_valid",
        ),
        sa.CheckConstraint(
            "length(trim(title)) > 0",
            name="ck_candidate_messages_title_not_blank",
        ),
        sa.CheckConstraint(
            "length(trim(body)) > 0",
            name="ck_candidate_messages_body_not_blank",
        ),
        sa.CheckConstraint(
            "attempts >= 0",
            name="ck_candidate_messages_attempts_nonnegative",
        ),
        sa.CheckConstraint(
            "(channel = 'email' AND recipient_email IS NOT NULL AND recipient_chat_id IS NULL) "
            "OR (channel = 'telegram' AND recipient_chat_id IS NOT NULL "
            "AND recipient_email IS NULL)",
            name="ck_candidate_messages_recipient_shape",
        ),
        sa.CheckConstraint(
            "(status = 'sending' AND lease_expires_at IS NOT NULL) "
            "OR (status <> 'sending' AND lease_expires_at IS NULL)",
            name="ck_candidate_messages_lease_consistent",
        ),
        sa.ForeignKeyConstraint(["candidate_id"], ["candidates.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["event_id"], ["events.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["initiator_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.UniqueConstraint("idempotency_key", name="uq_candidate_messages_idempotency_key"),
    )
    op.create_index(
        "ix_candidate_messages_candidate_created",
        "candidate_messages",
        ["candidate_id", "created_at"],
    )
    op.create_index(
        "ix_candidate_messages_queued_due",
        "candidate_messages",
        ["status", "scheduled_at"],
        postgresql_where=sa.text("status = 'queued'"),
    )
    op.create_index(
        "ix_candidate_messages_sending_lease",
        "candidate_messages",
        ["status", "lease_expires_at"],
        postgresql_where=sa.text("status = 'sending'"),
    )

    # --- Append-only attempt history ----------------------------------------
    op.create_table(
        "candidate_message_attempts",
        sa.Column("id", _UUID, primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("message_id", _UUID, nullable=False),
        sa.Column("attempt_no", sa.Integer(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("outcome", sa.String(16), nullable=False),
        sa.Column("error_code", sa.String(64), nullable=True),
        sa.Column("error_class", sa.String(32), nullable=True),
        sa.Column("provider_message_id", sa.String(255), nullable=True),
        sa.CheckConstraint(
            "attempt_no >= 1",
            name="ck_candidate_message_attempts_no_positive",
        ),
        sa.ForeignKeyConstraint(["message_id"], ["candidate_messages.id"], ondelete="CASCADE"),
        sa.UniqueConstraint(
            "message_id",
            "attempt_no",
            name="uq_candidate_message_attempts_message_attempt",
        ),
    )


def downgrade() -> None:
    op.drop_table("candidate_message_attempts")
    op.drop_index("ix_candidate_messages_sending_lease", table_name="candidate_messages")
    op.drop_index("ix_candidate_messages_queued_due", table_name="candidate_messages")
    op.drop_index(
        "ix_candidate_messages_candidate_created",
        table_name="candidate_messages",
    )
    op.drop_table("candidate_messages")
    op.drop_index(
        "ix_candidate_channel_tokens_expires_at",
        table_name="candidate_channel_tokens",
    )
    op.drop_index(
        "ix_candidate_channel_tokens_lookup",
        table_name="candidate_channel_tokens",
    )
    op.drop_table("candidate_channel_tokens")
    op.drop_index(
        "uq_candidate_contact_channels_chat_id_active",
        table_name="candidate_contact_channels",
    )
    op.drop_table("candidate_contact_channels")
    op.drop_column("event_history", "location_changed")
    op.drop_constraint("ck_events_location_len", "events", type_="check")
    op.drop_column("events", "location")
