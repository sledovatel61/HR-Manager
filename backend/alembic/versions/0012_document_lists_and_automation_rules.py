"""Versioned document lists and personal automation rules (phase 11).

Six new tables on top of revision 0011:

* ``document_lists`` — the stable identity of a checklist (name,
  description, application scope: position and/or stage; both empty =
  a general list). Never physically deleted.
* ``document_list_versions`` — immutable versions with the closed status
  vocabulary ``draft | published | archived``. A partial unique index
  guarantees at most ONE published version per list even under concurrent
  publication; ``(list_id, version_number)`` is unique; a CHECK keeps the
  status/timestamp pairs consistent.
* ``document_list_items`` — ordered items of a version (stable key,
  Russian name, optional safe explanation, required flag).
* ``candidate_document_assignments`` — the exact version applied to a
  candidate (RESTRICT FKs: an applied version can never be deleted). At
  most one current assignment per candidate (partial unique index over
  ``replaced_at IS NULL``); replacing a list closes the previous row.
* ``candidate_document_items`` — the per-candidate snapshot of the items
  with the receipt state ``missing | received``, who/when changed it and
  an optimistic ``version`` counter. No files, numbers or notes.
* ``automation_rules`` — personal rules (owner, closed trigger/action
  enums, validated JSON parameters, enabled flag, optimistic version,
  soft delete).
* ``automation_rule_executions`` — append-only execution history; the
  unique ``(rule_id, dedupe_key)`` pair is the durable idempotency barrier
  of rule evaluation. Rows hold ids/versions/outcome classes only.

One column is added to an existing table: ``notification_outbox.
object_snapshot`` (nullable JSON) — a PII-free snapshot of the document
list id/version, the assignment id and the item keys a document message
lists, re-validated by the worker right before the provider call.

Audit action values are stored as strings (``audit_log.action`` is already
64 characters wide since 0010) — no schema change is needed for the new
phase-11 audit actions. Fully reversible: downgrade drops the column and
the six tables in dependency order.
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

_STAGES = [
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
]
_LIST_STATUSES = ["draft", "published", "archived"]
_DOCUMENT_STATUSES = ["missing", "received"]
_TRIGGERS = ["stage_entered", "documents_missing_due"]
_ACTIONS = ["apply_document_list", "send_document_request", "send_document_reminder"]
_OUTCOMES = ["queued", "applied", "skipped", "failed"]


def _in_list(values: list[str]) -> str:
    return ", ".join(f"'{value}'" for value in values)


def upgrade() -> None:
    # --- Outbox: PII-free snapshot of the rendered business object -----------
    op.add_column(
        "notification_outbox",
        sa.Column("object_snapshot", sa.JSON(), nullable=True),
    )

    # --- Lists ---------------------------------------------------------------
    op.create_table(
        "document_lists",
        sa.Column("id", _UUID, primary_key=True),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("description", sa.Text(), nullable=False, server_default=""),
        sa.Column("scope_position", sa.String(200), nullable=True),
        sa.Column("scope_stage", sa.String(32), nullable=True),
        sa.Column(
            "created_by_user_id",
            _UUID,
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            f"scope_stage IS NULL OR scope_stage IN ({_in_list(_STAGES)})",
            name="ck_document_lists_scope_stage_valid",
        ),
        sa.CheckConstraint("version >= 1", name="ck_document_lists_version_positive"),
    )
    op.create_index("ix_document_lists_created_at", "document_lists", ["created_at"])

    # --- Versions ------------------------------------------------------------
    op.create_table(
        "document_list_versions",
        sa.Column("id", _UUID, primary_key=True),
        sa.Column(
            "list_id",
            _UUID,
            sa.ForeignKey("document_lists.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("version_number", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="draft"),
        sa.Column("row_version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column(
            "created_by_user_id",
            _UUID,
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "published_by_user_id",
            _UUID,
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("list_id", "version_number", name="uq_document_list_versions_number"),
        sa.CheckConstraint(
            f"status IN ({_in_list(_LIST_STATUSES)})",
            name="ck_document_list_versions_status_valid",
        ),
        sa.CheckConstraint("version_number >= 1", name="ck_document_list_versions_number_positive"),
        sa.CheckConstraint(
            "row_version >= 1", name="ck_document_list_versions_row_version_positive"
        ),
        sa.CheckConstraint(
            "(status = 'draft' AND published_at IS NULL AND archived_at IS NULL) "
            "OR (status = 'published' AND published_at IS NOT NULL AND archived_at IS NULL) "
            "OR (status = 'archived' AND archived_at IS NOT NULL)",
            name="ck_document_list_versions_status_timestamps",
        ),
    )
    op.create_index("ix_document_list_versions_list_id", "document_list_versions", ["list_id"])
    # The atomic-publication barrier: at most one published version per list.
    op.create_index(
        "uq_document_list_versions_one_published",
        "document_list_versions",
        ["list_id"],
        unique=True,
        postgresql_where=sa.text("status = 'published'"),
    )

    # --- Items ---------------------------------------------------------------
    op.create_table(
        "document_list_items",
        sa.Column("id", _UUID, primary_key=True),
        sa.Column(
            "version_id",
            _UUID,
            sa.ForeignKey("document_list_versions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("item_key", sa.String(64), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("explanation", sa.String(500), nullable=False, server_default=""),
        sa.Column("is_required", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("sort_order", sa.Integer(), nullable=False),
        sa.UniqueConstraint("version_id", "item_key", name="uq_document_list_items_key"),
        sa.UniqueConstraint("version_id", "sort_order", name="uq_document_list_items_order"),
        sa.CheckConstraint("sort_order >= 0", name="ck_document_list_items_order_nonnegative"),
    )
    op.create_index("ix_document_list_items_version_id", "document_list_items", ["version_id"])

    # --- Rules (before assignments: assignments reference rules) ------------
    op.create_table(
        "automation_rules",
        sa.Column("id", _UUID, primary_key=True),
        sa.Column(
            "owner_user_id",
            _UUID,
            sa.ForeignKey("users.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("is_enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("trigger_type", sa.String(32), nullable=False),
        sa.Column("trigger_params", sa.JSON(), nullable=False),
        sa.Column("conditions", sa.JSON(), nullable=False),
        sa.Column("action_type", sa.String(32), nullable=False),
        sa.Column("action_params", sa.JSON(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            f"trigger_type IN ({_in_list(_TRIGGERS)})",
            name="ck_automation_rules_trigger_valid",
        ),
        sa.CheckConstraint(
            f"action_type IN ({_in_list(_ACTIONS)})",
            name="ck_automation_rules_action_valid",
        ),
        sa.CheckConstraint("version >= 1", name="ck_automation_rules_version_positive"),
    )
    op.create_index("ix_automation_rules_owner_user_id", "automation_rules", ["owner_user_id"])
    op.create_index(
        "ix_automation_rules_active_trigger",
        "automation_rules",
        ["trigger_type", "is_enabled"],
        postgresql_where=sa.text("deleted_at IS NULL"),
    )

    # --- Candidate assignments (exact-version snapshot) ----------------------
    op.create_table(
        "candidate_document_assignments",
        sa.Column("id", _UUID, primary_key=True),
        sa.Column(
            "candidate_id",
            _UUID,
            sa.ForeignKey("candidates.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "list_id",
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
        sa.Column("version_number", sa.Integer(), nullable=False),
        sa.Column("list_name_snapshot", sa.String(200), nullable=False),
        sa.Column(
            "assigned_by_user_id",
            _UUID,
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "assigned_by_rule_id",
            _UUID,
            sa.ForeignKey("automation_rules.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("assigned_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("replaced_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "replaced_by_user_id",
            _UUID,
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.create_index(
        "ix_candidate_document_assignments_candidate_id",
        "candidate_document_assignments",
        ["candidate_id"],
    )
    op.create_index(
        "ix_candidate_document_assignments_version_id",
        "candidate_document_assignments",
        ["version_id"],
    )
    op.create_index(
        "uq_candidate_document_assignments_current",
        "candidate_document_assignments",
        ["candidate_id"],
        unique=True,
        postgresql_where=sa.text("replaced_at IS NULL"),
    )

    op.create_table(
        "candidate_document_items",
        sa.Column("id", _UUID, primary_key=True),
        sa.Column(
            "assignment_id",
            _UUID,
            sa.ForeignKey("candidate_document_assignments.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("item_key", sa.String(64), nullable=False),
        sa.Column("name_snapshot", sa.String(200), nullable=False),
        sa.Column("explanation_snapshot", sa.String(500), nullable=False, server_default=""),
        sa.Column("is_required", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("sort_order", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="missing"),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column(
            "changed_by_user_id",
            _UUID,
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("changed_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("assignment_id", "item_key", name="uq_candidate_document_items_key"),
        sa.CheckConstraint(
            f"status IN ({_in_list(_DOCUMENT_STATUSES)})",
            name="ck_candidate_document_items_status_valid",
        ),
        sa.CheckConstraint("version >= 1", name="ck_candidate_document_items_version_positive"),
        sa.CheckConstraint("sort_order >= 0", name="ck_candidate_document_items_order_nonnegative"),
    )
    op.create_index(
        "ix_candidate_document_items_assignment_id",
        "candidate_document_items",
        ["assignment_id"],
    )

    # --- Immutable execution history ----------------------------------------
    op.create_table(
        "automation_rule_executions",
        sa.Column("id", _UUID, primary_key=True),
        sa.Column(
            "rule_id",
            _UUID,
            sa.ForeignKey("automation_rules.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("rule_version", sa.Integer(), nullable=False),
        sa.Column("trigger_type", sa.String(32), nullable=False),
        sa.Column("trigger_object_type", sa.String(32), nullable=False),
        sa.Column("trigger_object_id", _UUID, nullable=True),
        sa.Column("trigger_object_version", sa.Integer(), nullable=True),
        sa.Column(
            "candidate_id",
            _UUID,
            sa.ForeignKey("candidates.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("action_type", sa.String(32), nullable=False),
        sa.Column("outcome", sa.String(16), nullable=False),
        sa.Column("outcome_class", sa.String(64), nullable=True),
        sa.Column("dedupe_key", sa.String(255), nullable=False),
        sa.Column("outbox_ids", sa.JSON(), nullable=True),
        sa.Column("list_id", _UUID, nullable=True),
        sa.Column("list_version_id", _UUID, nullable=True),
        sa.Column("executed_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("rule_id", "dedupe_key", name="uq_automation_rule_executions_dedupe"),
        sa.CheckConstraint(
            f"outcome IN ({_in_list(_OUTCOMES)})",
            name="ck_automation_rule_executions_outcome_valid",
        ),
        sa.CheckConstraint(
            f"action_type IN ({_in_list(_ACTIONS)})",
            name="ck_automation_rule_executions_action_valid",
        ),
    )
    op.create_index(
        "ix_automation_rule_executions_rule_executed",
        "automation_rule_executions",
        ["rule_id", "executed_at"],
    )
    op.create_index(
        "ix_automation_rule_executions_candidate_id",
        "automation_rule_executions",
        ["candidate_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_automation_rule_executions_candidate_id", table_name="automation_rule_executions"
    )
    op.drop_index(
        "ix_automation_rule_executions_rule_executed", table_name="automation_rule_executions"
    )
    op.drop_table("automation_rule_executions")

    op.drop_index(
        "ix_candidate_document_items_assignment_id", table_name="candidate_document_items"
    )
    op.drop_table("candidate_document_items")

    op.drop_index(
        "uq_candidate_document_assignments_current", table_name="candidate_document_assignments"
    )
    op.drop_index(
        "ix_candidate_document_assignments_version_id", table_name="candidate_document_assignments"
    )
    op.drop_index(
        "ix_candidate_document_assignments_candidate_id",
        table_name="candidate_document_assignments",
    )
    op.drop_table("candidate_document_assignments")

    op.drop_index("ix_automation_rules_active_trigger", table_name="automation_rules")
    op.drop_index("ix_automation_rules_owner_user_id", table_name="automation_rules")
    op.drop_table("automation_rules")

    op.drop_index("ix_document_list_items_version_id", table_name="document_list_items")
    op.drop_table("document_list_items")

    op.drop_index("uq_document_list_versions_one_published", table_name="document_list_versions")
    op.drop_index("ix_document_list_versions_list_id", table_name="document_list_versions")
    op.drop_table("document_list_versions")

    op.drop_index("ix_document_lists_created_at", table_name="document_lists")
    op.drop_table("document_lists")

    op.drop_column("notification_outbox", "object_snapshot")
