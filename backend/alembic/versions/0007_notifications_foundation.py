"""notification foundation: preferences, notifications, reminders, outbox

Phase 8 (notifications and pilot mode): the delivery foundation on which
phases 9-11 will add Telegram/SMTP and candidate communications.

Design (see docs/ARCHITECTURE.md, «Уведомления и пилотный режим»):

* ``notification_preferences`` — per-user IANA timezone, quiet hours
  (HH:MM strings; midnight-crossing intervals supported), workdays,
  enabled types/channels. A missing row means system defaults.
* ``notifications`` — logical in-app notifications with an immutable
  title/body snapshot (no candidate PII), a closed type vocabulary, a
  PII-free structured metadata blob and a unique ``dedupe_key`` so a
  repeatedly processed business event cannot produce duplicates.
* ``reminders`` — personal reminders (owner + assignee, optional
  candidate/event link, UTC due + IANA display timezone, closed recurrence
  set, optimistic concurrency via ``version``, monotonic ``occurrence``).
* ``notification_outbox`` — the transactional delivery queue (one row per
  message per channel). State machine ``queued -> sending ->
  accepted/delivered`` plus ``failed``/``cancelled``/``skipped``; bounded
  retries with ``attempts``/``next_attempt_at``; worker lease;
  ``idempotency_key`` unique; consent snapshot without secrets;
  ``scheduled_at`` (original) and ``scheduled_at_effective`` (after
  quiet-hours shifting) kept separately for audit.
* ``notification_delivery_attempts`` — append-only attempt history,
  unique per (outbox, attempt_no); accepted records are never edited.
* ``worker_heartbeat`` — singleton row for the worker healthcheck and the
  /ops/status signal (no PII).
* ``access_grants`` — explicit audited grants; an active
  ``pilot_full_access`` grant per user is unique (partial unique index).

All timestamps are timezone-aware UTC. Reversible: downgrade drops the
tables in dependency order.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_UUID = postgresql.UUID(as_uuid=True)

_NOTIFICATION_TYPES = (
    "event_assigned",
    "event_approaching",
    "event_overdue",
    "event_rescheduled",
    "event_cancelled",
    "candidate_transferred",
    "reminder_due",
    "reminder_overdue",
    "system_alert",
)
_PRIORITIES = ("low", "normal", "high")
_SOURCES = ("system", "rule", "reminder", "manual")
_CHANNELS = ("in_app", "email", "telegram")
_STATUSES = ("queued", "sending", "accepted", "delivered", "failed", "cancelled", "skipped")
_OUTCOMES = ("accepted", "delivered", "failed", "skipped", "cancelled")
_RECURRENCES = ("none", "daily", "workdays", "weekly")
_REMINDER_STATUSES = ("active", "completed", "cancelled")
_IMPORTANCES = ("low", "normal", "high")
_GRANT_SCOPES = ("pilot_full_access",)


def _in_list(values: tuple[str, ...]) -> str:
    return ", ".join(f"'{value}'" for value in values)


def upgrade() -> None:
    op.create_table(
        "notification_preferences",
        sa.Column("user_id", _UUID, primary_key=True),
        sa.Column("timezone", sa.String(64), nullable=False),
        sa.Column("quiet_hours_start", sa.String(5), nullable=False),
        sa.Column("quiet_hours_end", sa.String(5), nullable=False),
        sa.Column("workdays", sa.JSON(), nullable=False),
        sa.Column("enabled_types", sa.JSON(), nullable=False),
        sa.Column("enabled_channels", sa.JSON(), nullable=False),
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
            "length(timezone) BETWEEN 1 AND 64",
            name="ck_notification_preferences_tz_len",
        ),
        sa.CheckConstraint(
            "quiet_hours_start LIKE '__:__'",
            name="ck_notification_preferences_quiet_start_format",
        ),
        sa.CheckConstraint(
            "quiet_hours_end LIKE '__:__'",
            name="ck_notification_preferences_quiet_end_format",
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
    )

    op.create_table(
        "notifications",
        sa.Column("id", _UUID, primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("user_id", _UUID, nullable=False),
        sa.Column("type", sa.String(32), nullable=False),
        sa.Column("title", sa.String(300), nullable=False),
        sa.Column("body", sa.Text(), nullable=True),
        sa.Column("priority", sa.String(16), nullable=False),
        sa.Column("source", sa.String(16), nullable=False),
        sa.Column("object_type", sa.String(32), nullable=True),
        sa.Column("object_id", _UUID, nullable=True),
        sa.Column("metadata", sa.JSON(), nullable=True),
        sa.Column("dedupe_key", sa.String(255), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("read_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("dismissed_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            f"type IN ({_in_list(_NOTIFICATION_TYPES)})",
            name="ck_notifications_type_valid",
        ),
        sa.CheckConstraint(
            f"priority IN ({_in_list(_PRIORITIES)})",
            name="ck_notifications_priority_valid",
        ),
        sa.CheckConstraint(
            f"source IN ({_in_list(_SOURCES)})",
            name="ck_notifications_source_valid",
        ),
        sa.CheckConstraint("length(trim(title)) > 0", name="ck_notifications_title_not_blank"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
    )
    op.create_index("ix_notifications_user_created", "notifications", ["user_id", "created_at"])
    op.create_index("ix_notifications_user_unread", "notifications", ["user_id", "read_at"])
    op.create_index(
        "uq_notifications_dedupe_key",
        "notifications",
        ["dedupe_key"],
        unique=True,
        postgresql_where=sa.text("dedupe_key IS NOT NULL"),
    )

    op.create_table(
        "reminders",
        sa.Column("id", _UUID, primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("owner_user_id", _UUID, nullable=False),
        sa.Column("assignee_user_id", _UUID, nullable=False),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("candidate_id", _UUID, nullable=True),
        sa.Column("event_id", _UUID, nullable=True),
        sa.Column("due_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("timezone", sa.String(64), nullable=False),
        sa.Column("importance", sa.String(16), nullable=False),
        sa.Column("recurrence", sa.String(16), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("occurrence", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
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
            f"recurrence IN ({_in_list(_RECURRENCES)})",
            name="ck_reminders_recurrence_valid",
        ),
        sa.CheckConstraint(
            f"status IN ({_in_list(_REMINDER_STATUSES)})",
            name="ck_reminders_status_valid",
        ),
        sa.CheckConstraint(
            f"importance IN ({_in_list(_IMPORTANCES)})",
            name="ck_reminders_importance_valid",
        ),
        sa.CheckConstraint("length(trim(title)) > 0", name="ck_reminders_title_not_blank"),
        sa.CheckConstraint("length(timezone) BETWEEN 1 AND 64", name="ck_reminders_tz_len"),
        sa.CheckConstraint(
            "(status = 'completed' AND completed_at IS NOT NULL) "
            "OR (status <> 'completed' AND completed_at IS NULL)",
            name="ck_reminders_completed_at_consistent",
        ),
        sa.ForeignKeyConstraint(["owner_user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["assignee_user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["candidate_id"], ["candidates.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["event_id"], ["events.id"], ondelete="SET NULL"),
    )
    op.create_index("ix_reminders_assignee_due", "reminders", ["assignee_user_id", "due_at"])

    op.create_table(
        "notification_outbox",
        sa.Column("id", _UUID, primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("recipient_user_id", _UUID, nullable=True),
        sa.Column("external_recipient", sa.String(500), nullable=True),
        sa.Column("channel", sa.String(16), nullable=False),
        sa.Column("notification_type", sa.String(32), nullable=False),
        sa.Column("source", sa.String(16), nullable=False),
        sa.Column("title", sa.String(300), nullable=False),
        sa.Column("body", sa.Text(), nullable=True),
        sa.Column("template", sa.String(64), nullable=True),
        sa.Column("template_version", sa.Integer(), nullable=True),
        sa.Column("initiator_user_id", _UUID, nullable=True),
        sa.Column("rule_id", _UUID, nullable=True),
        sa.Column("object_type", sa.String(32), nullable=True),
        sa.Column("object_id", _UUID, nullable=True),
        sa.Column("scheduled_at", sa.DateTime(timezone=True), nullable=True),
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
        sa.Column("idempotency_key", sa.String(255), nullable=False),
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
            f"channel IN ({_in_list(_CHANNELS)})",
            name="ck_notification_outbox_channel_valid",
        ),
        sa.CheckConstraint(
            f"notification_type IN ({_in_list(_NOTIFICATION_TYPES)})",
            name="ck_notification_outbox_type_valid",
        ),
        sa.CheckConstraint(
            f"source IN ({_in_list(_SOURCES)})",
            name="ck_notification_outbox_source_valid",
        ),
        sa.CheckConstraint(
            f"status IN ({_in_list(_STATUSES)})",
            name="ck_notification_outbox_status_valid",
        ),
        sa.CheckConstraint("attempts >= 0", name="ck_notification_outbox_attempts_nonnegative"),
        sa.CheckConstraint(
            "(recipient_user_id IS NOT NULL) <> (external_recipient IS NOT NULL)",
            name="ck_notification_outbox_exactly_one_recipient",
        ),
        sa.CheckConstraint(
            "(status = 'sending' AND lease_expires_at IS NOT NULL) "
            "OR (status <> 'sending' AND lease_expires_at IS NULL)",
            name="ck_notification_outbox_lease_consistent",
        ),
        sa.ForeignKeyConstraint(["recipient_user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["initiator_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.UniqueConstraint("idempotency_key", name="uq_notification_outbox_idempotency_key"),
    )
    op.create_index(
        "ix_notification_outbox_queued_due",
        "notification_outbox",
        ["status", "scheduled_at"],
        postgresql_where=sa.text("status = 'queued'"),
    )
    op.create_index(
        "ix_notification_outbox_sending_lease",
        "notification_outbox",
        ["status", "lease_expires_at"],
        postgresql_where=sa.text("status = 'sending'"),
    )

    op.create_table(
        "notification_delivery_attempts",
        sa.Column("id", _UUID, primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("outbox_id", _UUID, nullable=False),
        sa.Column("attempt_no", sa.Integer(), nullable=False),
        sa.Column(
            "started_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "finished_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("outcome", sa.String(16), nullable=False),
        sa.Column("error_code", sa.String(64), nullable=True),
        sa.Column("error_class", sa.String(32), nullable=True),
        sa.Column("provider_message_id", sa.String(255), nullable=True),
        sa.CheckConstraint(
            f"outcome IN ({_in_list(_OUTCOMES)})",
            name="ck_notification_delivery_attempts_outcome_valid",
        ),
        sa.CheckConstraint("attempt_no >= 1", name="ck_notification_delivery_attempts_no_positive"),
        sa.ForeignKeyConstraint(["outbox_id"], ["notification_outbox.id"], ondelete="CASCADE"),
        sa.UniqueConstraint(
            "outbox_id",
            "attempt_no",
            name="uq_notification_delivery_attempts_outbox_attempt",
        ),
    )

    op.create_table(
        "worker_heartbeat",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("worker_id", sa.String(64), nullable=False),
        sa.Column("pid", sa.Integer(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("processed_total", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("failed_total", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("current_lease_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )

    op.create_table(
        "access_grants",
        sa.Column("id", _UUID, primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("user_id", _UUID, nullable=False),
        sa.Column("scope", sa.String(32), nullable=False),
        sa.Column("granted_by_user_id", _UUID, nullable=True),
        sa.Column(
            "granted_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoke_reason", sa.String(500), nullable=True),
        sa.CheckConstraint(
            f"scope IN ({_in_list(_GRANT_SCOPES)})",
            name="ck_access_grants_scope_valid",
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["granted_by_user_id"], ["users.id"], ondelete="SET NULL"),
    )
    op.create_index(
        "uq_access_grants_active",
        "access_grants",
        ["user_id", "scope"],
        unique=True,
        postgresql_where=sa.text("revoked_at IS NULL"),
    )


def downgrade() -> None:
    op.drop_table("access_grants")
    op.drop_table("worker_heartbeat")
    op.drop_table("notification_delivery_attempts")
    op.drop_table("notification_outbox")
    op.drop_table("reminders")
    op.drop_table("notifications")
    op.drop_table("notification_preferences")
