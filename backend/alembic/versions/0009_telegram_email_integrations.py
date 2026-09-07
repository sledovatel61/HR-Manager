"""telegram and email integrations: link tokens, channel preferences, consent

Phase 9 (Telegram and email integrations):
* Table ``telegram_link_tokens`` — single-use, expiring tokens for secure Telegram account linking.
  Stores only SHA-256 hash of random tokens.
* Columns added to ``notification_preferences``:
  - ``telegram_chat_id`` (BigInteger)
  - ``telegram_username`` (VARCHAR(64))
  - ``telegram_linked_at`` (TIMESTAMPTZ)
  - ``telegram_opt_in`` (BOOLEAN DEFAULT false)
  - ``telegram_consent_at`` (TIMESTAMPTZ)
  - ``telegram_consent_source`` (VARCHAR(64))
  - ``telegram_consent_policy_version`` (VARCHAR(32))
  - ``email_address`` (VARCHAR(255))
  - ``email_opt_in`` (BOOLEAN DEFAULT false)
  - ``email_consent_at`` (TIMESTAMPTZ)
  - ``email_consent_source`` (VARCHAR(64))
  - ``email_consent_policy_version`` (VARCHAR(32))
  - ``channel_health`` (JSON)

All timestamps are timezone-aware UTC. Reversible.
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
    # 1. telegram_link_tokens table
    op.create_table(
        "telegram_link_tokens",
        sa.Column(
            "id",
            _UUID,
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "user_id",
            _UUID,
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
    )
    op.create_index(
        "ix_telegram_link_tokens_hash",
        "telegram_link_tokens",
        ["token_hash"],
    )
    op.create_index(
        "ix_telegram_link_tokens_user_created",
        "telegram_link_tokens",
        ["user_id", "created_at"],
    )

    # 2. notification_preferences columns
    op.add_column(
        "notification_preferences",
        sa.Column("telegram_chat_id", sa.BigInteger(), nullable=True),
    )
    op.add_column(
        "notification_preferences",
        sa.Column("telegram_username", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "notification_preferences",
        sa.Column("telegram_linked_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "notification_preferences",
        sa.Column(
            "telegram_opt_in",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
    )
    op.add_column(
        "notification_preferences",
        sa.Column("telegram_consent_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "notification_preferences",
        sa.Column("telegram_consent_source", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "notification_preferences",
        sa.Column("telegram_consent_policy_version", sa.String(length=32), nullable=True),
    )
    op.add_column(
        "notification_preferences",
        sa.Column("email_address", sa.String(length=255), nullable=True),
    )
    op.add_column(
        "notification_preferences",
        sa.Column(
            "email_opt_in",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
    )
    op.add_column(
        "notification_preferences",
        sa.Column("email_consent_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "notification_preferences",
        sa.Column("email_consent_source", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "notification_preferences",
        sa.Column("email_consent_policy_version", sa.String(length=32), nullable=True),
    )
    op.add_column(
        "notification_preferences",
        sa.Column("channel_health", sa.JSON(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("notification_preferences", "channel_health")
    op.drop_column("notification_preferences", "email_consent_policy_version")
    op.drop_column("notification_preferences", "email_consent_source")
    op.drop_column("notification_preferences", "email_consent_at")
    op.drop_column("notification_preferences", "email_opt_in")
    op.drop_column("notification_preferences", "email_address")
    op.drop_column("notification_preferences", "telegram_consent_policy_version")
    op.drop_column("notification_preferences", "telegram_consent_source")
    op.drop_column("notification_preferences", "telegram_consent_at")
    op.drop_column("notification_preferences", "telegram_opt_in")
    op.drop_column("notification_preferences", "telegram_linked_at")
    op.drop_column("notification_preferences", "telegram_username")
    op.drop_column("notification_preferences", "telegram_chat_id")

    op.drop_index("ix_telegram_link_tokens_user_created", table_name="telegram_link_tokens")
    op.drop_index("ix_telegram_link_tokens_hash", table_name="telegram_link_tokens")
    op.drop_table("telegram_link_tokens")
