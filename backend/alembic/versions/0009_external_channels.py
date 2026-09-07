"""External channels: Telegram bindings, email addresses, per-channel consent.

Phase 9 (Telegram and email integrations) extends the phase-8 outbox with
real delivery channels. This revision:

* ``telegram_links`` — one row per user with the numeric ``chat_id``
  (never a username), link/revoke timestamps and PII-free delivery stats;
* ``telegram_link_tokens`` — one-shot expiring linking tokens, stored as
  SHA-256 hashes (the raw value is shown to the owner once at creation);
* ``telegram_start_events`` — observed ``/start <token>`` events from the
  bot inbox, so concurrent confirms never lose each other's events;
* ``telegram_poll_state`` — singleton getUpdates offset;
* ``user_emails`` — verified + pending addresses with a one-shot
  verification token hash and PII-free delivery stats;
* ``notification_preferences`` — explicit per-channel consent
  (``telegram_opt_in`` / ``email_opt_in`` plus timestamp, source and terms
  version). Existing rows keep ``opt_in=false``: external channels are
  never enabled silently.

Reversible: downgrade drops the new tables/columns. No secrets are stored
(bot token and SMTP password arrive only via environment/secret storage).
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0009"
down_revision: str | None = "0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_UUID = postgresql.UUID(as_uuid=True)


def upgrade() -> None:
    op.create_table(
        "telegram_links",
        sa.Column("user_id", _UUID, primary_key=True),
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
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
    )

    op.create_table(
        "telegram_link_tokens",
        sa.Column("id", _UUID, primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("user_id", _UUID, nullable=False),
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
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("token_hash", name="uq_telegram_link_tokens_token_hash"),
    )
    op.create_index("ix_telegram_link_tokens_user_id", "telegram_link_tokens", ["user_id"])
    op.create_index("ix_telegram_link_tokens_expires_at", "telegram_link_tokens", ["expires_at"])

    op.create_table(
        "telegram_start_events",
        sa.Column("token_hash", sa.String(64), primary_key=True),
        sa.Column("chat_id", sa.BigInteger(), nullable=False),
        sa.Column(
            "seen_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )

    op.create_table(
        "telegram_poll_state",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("last_update_id", sa.BigInteger(), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )

    op.create_table(
        "user_emails",
        sa.Column("user_id", _UUID, primary_key=True),
        sa.Column("email", sa.String(254), nullable=True),
        sa.Column("verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("pending_email", sa.String(254), nullable=True),
        sa.Column("verification_token_hash", sa.String(64), nullable=True),
        sa.Column("verification_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("verification_attempts", sa.Integer(), nullable=False, server_default="0"),
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
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.UniqueConstraint(
            "verification_token_hash", name="uq_user_emails_verification_token_hash"
        ),
    )

    # Per-channel consent on the existing preferences table. Server defaults
    # keep every existing user opted OUT (channels are never enabled
    # silently); the ORM also defaults new rows to False.
    op.add_column(
        "notification_preferences",
        sa.Column("telegram_opt_in", sa.Boolean(), nullable=False, server_default="false"),
    )
    op.add_column(
        "notification_preferences",
        sa.Column("telegram_consent_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "notification_preferences",
        sa.Column("telegram_consent_source", sa.String(32), nullable=True),
    )
    op.add_column(
        "notification_preferences",
        sa.Column("telegram_consent_policy_version", sa.String(32), nullable=True),
    )
    op.add_column(
        "notification_preferences",
        sa.Column("email_opt_in", sa.Boolean(), nullable=False, server_default="false"),
    )
    op.add_column(
        "notification_preferences",
        sa.Column("email_consent_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "notification_preferences",
        sa.Column("email_consent_source", sa.String(32), nullable=True),
    )
    op.add_column(
        "notification_preferences",
        sa.Column("email_consent_policy_version", sa.String(32), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("notification_preferences", "email_consent_policy_version")
    op.drop_column("notification_preferences", "email_consent_source")
    op.drop_column("notification_preferences", "email_consent_at")
    op.drop_column("notification_preferences", "email_opt_in")
    op.drop_column("notification_preferences", "telegram_consent_policy_version")
    op.drop_column("notification_preferences", "telegram_consent_source")
    op.drop_column("notification_preferences", "telegram_consent_at")
    op.drop_column("notification_preferences", "telegram_opt_in")
    op.drop_table("user_emails")
    op.drop_table("telegram_poll_state")
    op.drop_table("telegram_start_events")
    op.drop_index("ix_telegram_link_tokens_expires_at", table_name="telegram_link_tokens")
    op.drop_index("ix_telegram_link_tokens_user_id", table_name="telegram_link_tokens")
    op.drop_table("telegram_link_tokens")
    op.drop_table("telegram_links")
