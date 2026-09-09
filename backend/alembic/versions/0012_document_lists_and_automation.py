"""Document lists, candidate documents and automation rules (Phase 11).

Adds six new tables on top of revision 0011:

* ``document_lists`` — stable logical document-list containers with title,
  description, optional scope (JSON) and author.
* ``document_list_versions`` — immutable versioned snapshots of items
  (draft → published → archived). Items are stored as a JSON array; one
  version per list may be ``published`` at a time (enforced by a partial
  unique index).
* ``candidate_document_lists`` — exact version binding from a candidate to
  a published version; future publications never rewrite it.
* ``candidate_document_items`` — per-item status (missing/received) with
  optimistic concurrency version for conflict detection.
* ``automation_rules`` — personal typed rules owned by a user with
  trigger/conditions/action, enabled flag and optimistic version.
* ``automation_rule_executions`` — immutable execution history with
  dedupe key, outcome and error class; never stores PII or message text.

Also adds the Phase 11 audit actions to the ``audit_log.action`` CHECK.

Reversible: downgrade drops all new tables and restores the previous
audit CHECK.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0012"
down_revision: str | None = "0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_UUID = postgresql.UUID(as_uuid=True)

# New audit actions added in phase 11.
_PHASE_11_AUDIT_ACTIONS = [
    "document_list_created",
    "document_list_version_created",
    "document_list_published",
    "document_list_archived",
    "document_list_applied",
    "document_item_received",
    "document_item_reverted",
    "automation_rule_created",
    "automation_rule_updated",
    "automation_rule_toggled",
    "automation_rule_deleted",
]


def _full_audit_actions() -> str:
    """Build the full CHECK list including all phases."""
    from app.models import AuditAction

    all_values = [member.value for member in AuditAction]
    return ", ".join(f"'{v}'" for v in all_values)


def upgrade() -> None:
    # --- document_lists -------------------------------------------------------
    op.create_table(
        "document_lists",
        sa.Column("id", _UUID, primary_key=True),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("description", sa.Text, nullable=True),
        sa.Column("list_scope", postgresql.JSON, nullable=True),
        sa.Column(
            "author_user_id",
            _UUID,
            sa.ForeignKey("users.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "length(trim(title)) > 0", name="ck_document_lists_title_not_blank"
        ),
    )
    op.create_index(
        "ix_document_lists_author_user_id", "document_lists", ["author_user_id"]
    )

    # --- document_list_versions -----------------------------------------------
    op.create_table(
        "document_list_versions",
        sa.Column("id", _UUID, primary_key=True),
        sa.Column(
            "document_list_id",
            _UUID,
            sa.ForeignKey("document_lists.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("version_number", sa.Integer, nullable=False),
        sa.Column(
            "status",
            sa.String(16),
            nullable=False,
            server_default="draft",
        ),
        sa.Column("items", postgresql.JSON, nullable=False, server_default="[]"),
        sa.Column(
            "created_by_user_id",
            _UUID,
            sa.ForeignKey("users.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "published_by_user_id",
            _UUID,
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "status IN ('draft', 'published', 'archived')",
            name="ck_document_list_versions_status_valid",
        ),
        sa.CheckConstraint(
            "version_number >= 1",
            name="ck_document_list_versions_number_positive",
        ),
        sa.UniqueConstraint(
            "document_list_id",
            "version_number",
            name="uq_document_list_versions_list_version",
        ),
    )
    op.create_index(
        "ix_document_list_versions_list_id",
        "document_list_versions",
        ["document_list_id"],
    )
    op.create_index(
        "ix_document_list_versions_status",
        "document_list_versions",
        ["status"],
    )
    # Only one PUBLISHED version per list (partial unique index).
    op.create_index(
        "uq_document_list_versions_one_published",
        "document_list_versions",
        ["document_list_id"],
        unique=True,
        postgresql_where=sa.text("status = 'published'"),
        sqlite_where=sa.text("status = 'published'"),
    )

    # --- candidate_document_lists ---------------------------------------------
    op.create_table(
        "candidate_document_lists",
        sa.Column(
            "candidate_id",
            _UUID,
            sa.ForeignKey("candidates.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "document_list_id",
            _UUID,
            sa.ForeignKey("document_lists.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "version_id",
            _UUID,
            sa.ForeignKey("document_list_versions.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("version_number", sa.Integer, nullable=False),
        sa.Column(
            "applied_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "applied_by_user_id",
            _UUID,
            sa.ForeignKey("users.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("candidate_id", "document_list_id"),
    )
    op.create_index(
        "ix_candidate_document_lists_candidate_id",
        "candidate_document_lists",
        ["candidate_id"],
    )
    op.create_index(
        "ix_candidate_document_lists_list_id",
        "candidate_document_lists",
        ["document_list_id"],
    )

    # --- candidate_document_items ---------------------------------------------
    op.create_table(
        "candidate_document_items",
        sa.Column(
            "candidate_id",
            _UUID,
            sa.ForeignKey("candidates.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "document_list_id",
            _UUID,
            sa.ForeignKey("document_lists.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("item_key", sa.String(64), nullable=False),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("mandatory", sa.Boolean, nullable=False, server_default=sa.text("true")),
        sa.Column(
            "status",
            sa.String(16),
            nullable=False,
            server_default="missing",
        ),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "received_by_user_id",
            _UUID,
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("version", sa.Integer, nullable=False, server_default=sa.text("1")),
        sa.PrimaryKeyConstraint("candidate_id", "document_list_id", "item_key"),
        sa.CheckConstraint(
            "status IN ('missing', 'received')",
            name="ck_candidate_document_items_status_valid",
        ),
    )
    op.create_index(
        "ix_candidate_document_items_candidate_list",
        "candidate_document_items",
        ["candidate_id", "document_list_id"],
    )

    # --- automation_rules -----------------------------------------------------
    op.create_table(
        "automation_rules",
        sa.Column("id", _UUID, primary_key=True),
        sa.Column(
            "owner_user_id",
            _UUID,
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("enabled", sa.Boolean, nullable=False, server_default=sa.text("true")),
        sa.Column(
            "trigger_type",
            sa.String(32),
            nullable=False,
        ),
        sa.Column("trigger_params", postgresql.JSON, nullable=False, server_default="{}"),
        sa.Column("conditions", postgresql.JSON, nullable=True),
        sa.Column(
            "action_type",
            sa.String(32),
            nullable=False,
        ),
        sa.Column("action_params", postgresql.JSON, nullable=False, server_default="{}"),
        sa.Column("version", sa.Integer, nullable=False, server_default=sa.text("1")),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "trigger_type IN ('stage_transition', 'scheduled_reminder')",
            name="ck_automation_rules_trigger_type_valid",
        ),
        sa.CheckConstraint(
            "action_type IN ('apply_list', 'document_request', 'document_reminder')",
            name="ck_automation_rules_action_type_valid",
        ),
        sa.CheckConstraint(
            "length(trim(title)) > 0", name="ck_automation_rules_title_not_blank"
        ),
    )
    op.create_index(
        "ix_automation_rules_owner_user_id", "automation_rules", ["owner_user_id"]
    )
    op.create_index("ix_automation_rules_enabled", "automation_rules", ["enabled"])

    # --- automation_rule_executions -------------------------------------------
    op.create_table(
        "automation_rule_executions",
        sa.Column("id", _UUID, primary_key=True),
        sa.Column(
            "rule_id",
            _UUID,
            sa.ForeignKey("automation_rules.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("rule_version", sa.Integer, nullable=False),
        sa.Column("trigger_object_type", sa.String(32), nullable=True),
        sa.Column("trigger_object_id", _UUID, nullable=True),
        sa.Column(
            "candidate_id",
            _UUID,
            sa.ForeignKey("candidates.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("action_type", sa.String(32), nullable=False),
        sa.Column(
            "outcome",
            sa.String(16),
            nullable=False,
        ),
        sa.Column("error_class", sa.String(64), nullable=True),
        sa.Column("dedupe_key", sa.String(255), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "outcome IN ('success', 'skipped', 'failed')",
            name="ck_automation_rule_executions_outcome_valid",
        ),
    )
    op.create_index(
        "ix_automation_rule_executions_rule_id",
        "automation_rule_executions",
        ["rule_id"],
    )
    op.create_index(
        "ix_automation_rule_executions_candidate_id",
        "automation_rule_executions",
        ["candidate_id"],
    )
    op.create_index(
        "ix_automation_rule_executions_created_at",
        "automation_rule_executions",
        ["created_at"],
    )
    op.create_index(
        "uq_automation_rule_executions_dedupe",
        "automation_rule_executions",
        ["dedupe_key"],
        unique=True,
        postgresql_where=sa.text("dedupe_key IS NOT NULL"),
        sqlite_where=sa.text("dedupe_key IS NOT NULL"),
    )

    # --- Update audit_log CHECK (add phase 11 actions) -----------------------
    op.drop_constraint("ck_audit_log_action_valid", "audit_log", type_="check")
    op.create_check_constraint(
        "ck_audit_log_action_valid",
        "audit_log",
        f"action IN ({_full_audit_actions()})",
    )


def downgrade() -> None:
    op.drop_constraint("ck_audit_log_action_valid", "audit_log", type_="check")
    # Restore pre-phase-11 audit actions CHECK (from migration 0011).
    from app.models import AuditAction

    pre_actions = [
        a.value
        for a in AuditAction
        if a.value not in set(_PHASE_11_AUDIT_ACTIONS)
    ]
    pre_list = ", ".join(f"'{v}'" for v in pre_actions)
    op.create_check_constraint(
        "ck_audit_log_action_valid", "audit_log", f"action IN ({pre_list})"
    )

    op.drop_table("automation_rule_executions")
    op.drop_table("automation_rules")
    op.drop_table("candidate_document_items")
    op.drop_table("candidate_document_lists")
    op.drop_table("document_list_versions")
    op.drop_table("document_lists")
