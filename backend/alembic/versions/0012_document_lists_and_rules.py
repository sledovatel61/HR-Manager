"""Document lists, candidate document tracking and automation rules (phase 11).

Revision 0012 on top of 0011.  Adds:

* ``document_lists`` — stable identifiers of document lists (name, description,
  scope string, author).
* ``document_list_versions`` — immutable published snapshots; each version has
  a status (draft / published / archived) and ordered items.
* ``document_list_items`` — rows inside one version (stable key, Russian name,
  required flag, optional safe explanation).
* ``candidate_document_assignments`` — links a candidate to an exact published
  version (snapshot); the candidate's items are initialised from the version.
* ``candidate_document_items`` — per-item state (missing / received), who and
  when changed it, optimistic ``version``.
* ``automation_rules`` — personal rules owned by a user, with closed typed
  trigger/condition/action parameters (JSON), enabled flag, optimistic version.
* ``automation_rule_executions`` — immutable audit of every rule firing
  (rule/version, trigger object/version, candidate, action, outcome, dedupe key;
  no PII, no message text).

All enum/check/unique/FK/index constraints have stable names.  Reversible:
downgrade drops all new tables.
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


def upgrade() -> None:
    # --------------------------------------------------------------------- #
    # Document lists (stable identifiers)                                    #
    # --------------------------------------------------------------------- #
    op.create_table(
        "document_lists",
        sa.Column("id", _UUID, primary_key=True),
        sa.Column("stable_key", sa.String(64), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("description", sa.Text(), nullable=False, server_default=""),
        sa.Column("scope", sa.String(200), nullable=False, server_default=""),
        sa.Column(
            "created_by_user_id",
            _UUID,
            sa.ForeignKey("users.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("stable_key", name="uq_document_lists_stable_key"),
    )
    op.create_index("ix_document_lists_stable_key", "document_lists", ["stable_key"])

    # --------------------------------------------------------------------- #
    # Document list versions                                                  #
    # --------------------------------------------------------------------- #
    op.create_table(
        "document_list_versions",
        sa.Column("id", _UUID, primary_key=True),
        sa.Column(
            "list_id",
            _UUID,
            sa.ForeignKey("document_lists.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("version_number", sa.Integer(), nullable=False),
        sa.Column(
            "status",
            sa.String(16),
            nullable=False,
            server_default="draft",
        ),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "published_by_user_id",
            _UUID,
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "status IN ('draft', 'published', 'archived')",
            name="ck_document_list_versions_status_valid",
        ),
        sa.CheckConstraint("version_number >= 1", name="ck_document_list_versions_number_positive"),
        sa.CheckConstraint(
            "(status = 'published' AND published_at IS NOT NULL) "
            "OR (status = 'draft' AND published_at IS NULL) "
            "OR (status = 'archived')",
            name="ck_document_list_versions_published_consistent",
        ),
        sa.UniqueConstraint(
            "list_id",
            "version_number",
            name="uq_document_list_versions_list_version",
        ),
    )
    op.create_index(
        "ix_document_list_versions_list_id",
        "document_list_versions",
        ["list_id"],
    )
    op.create_index(
        "ix_document_list_versions_status",
        "document_list_versions",
        ["status"],
    )

    # --------------------------------------------------------------------- #
    # Document list items (inside a version)                                  #
    # --------------------------------------------------------------------- #
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
        sa.Column("name", sa.String(300), nullable=False),
        sa.Column("explanation", sa.Text(), nullable=True),
        sa.Column("is_required", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "length(trim(name)) > 0",
            name="ck_document_list_items_name_not_blank",
        ),
        sa.UniqueConstraint(
            "version_id",
            "item_key",
            name="uq_document_list_items_version_key",
        ),
    )
    op.create_index(
        "ix_document_list_items_version_id",
        "document_list_items",
        ["version_id"],
    )

    # --------------------------------------------------------------------- #
    # Candidate → exact published version assignment (snapshot)               #
    # --------------------------------------------------------------------- #
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
            "version_id",
            _UUID,
            sa.ForeignKey("document_list_versions.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "assigned_by_user_id",
            _UUID,
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("assigned_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "replaced_at",
            sa.DateTime(timezone=True),
            nullable=True,
            comment="When a newer version replaced this assignment",
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_cda_candidate_id",
        "candidate_document_assignments",
        ["candidate_id"],
    )
    op.create_index(
        "ix_cda_version_id",
        "candidate_document_assignments",
        ["version_id"],
    )
    # At most one active (non-replaced) assignment per candidate per list.
    # We link through version→list for the constraint.
    # PostgreSQL partial unique index: one active assignment per candidate per list.
    op.execute(
        """
        CREATE UNIQUE INDEX uq_cda_active_per_candidate_list
        ON candidate_document_assignments (candidate_id, version_id)
        WHERE replaced_at IS NULL
        """
    )

    # --------------------------------------------------------------------- #
    # Candidate document items (per-item state)                               #
    # --------------------------------------------------------------------- #
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
        sa.Column("name_snapshot", sa.String(300), nullable=False),
        sa.Column("is_required", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column(
            "status",
            sa.String(16),
            nullable=False,
            server_default="missing",
        ),
        sa.Column(
            "changed_by_user_id",
            _UUID,
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("changed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "version",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("1"),
            comment="Optimistic concurrency for status changes",
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "status IN ('missing', 'received')",
            name="ck_candidate_document_items_status_valid",
        ),
        sa.CheckConstraint(
            "version >= 1",
            name="ck_candidate_document_items_version_positive",
        ),
        sa.UniqueConstraint(
            "assignment_id",
            "item_key",
            name="uq_cdi_assignment_key",
        ),
    )
    op.create_index(
        "ix_cdi_assignment_id",
        "candidate_document_items",
        ["assignment_id"],
    )
    op.create_index(
        "ix_cdi_status",
        "candidate_document_items",
        ["status"],
    )

    # --------------------------------------------------------------------- #
    # Automation rules                                                        #
    # --------------------------------------------------------------------- #
    op.create_table(
        "automation_rules",
        sa.Column("id", _UUID, primary_key=True),
        sa.Column(
            "owner_user_id",
            _UUID,
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("is_enabled", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column(
            "trigger_type",
            sa.String(64),
            nullable=False,
        ),
        sa.Column("trigger_params", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column(
            "condition_type",
            sa.String(64),
            nullable=True,
        ),
        sa.Column("condition_params", sa.JSON(), nullable=True),
        sa.Column(
            "action_type",
            sa.String(64),
            nullable=False,
        ),
        sa.Column("action_params", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column(
            "version",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("1"),
            comment="Optimistic concurrency",
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "trigger_type IN ('stage_transition', 'document_reminder_schedule')",
            name="ck_automation_rules_trigger_type_valid",
        ),
        sa.CheckConstraint(
            "action_type IN ("
            "'apply_document_list', 'send_document_request', 'send_document_reminder'"
            ")",
            name="ck_automation_rules_action_type_valid",
        ),
        sa.CheckConstraint(
            "length(trim(name)) > 0",
            name="ck_automation_rules_name_not_blank",
        ),
        sa.CheckConstraint(
            "version >= 1",
            name="ck_automation_rules_version_positive",
        ),
    )
    op.create_index(
        "ix_automation_rules_owner",
        "automation_rules",
        ["owner_user_id"],
    )
    op.create_index(
        "ix_automation_rules_enabled",
        "automation_rules",
        ["is_enabled"],
    )

    # --------------------------------------------------------------------- #
    # Automation rule executions (immutable history)                          #
    # --------------------------------------------------------------------- #
    op.create_table(
        "automation_rule_executions",
        sa.Column("id", _UUID, primary_key=True),
        sa.Column(
            "rule_id",
            _UUID,
            sa.ForeignKey("automation_rules.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("rule_version", sa.Integer(), nullable=False),
        sa.Column(
            "trigger_object_id",
            _UUID,
            nullable=True,
            comment="ID of the triggering object (event, candidate)",
        ),
        sa.Column(
            "trigger_object_version",
            sa.Integer(),
            nullable=True,
        ),
        sa.Column(
            "candidate_id",
            _UUID,
            sa.ForeignKey("candidates.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("action_type", sa.String(64), nullable=False),
        sa.Column("action_params", sa.JSON(), nullable=True),
        sa.Column("outcome", sa.String(32), nullable=False),
        sa.Column("dedupe_key", sa.String(255), nullable=True),
        sa.Column("executed_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "outcome IN ('success', 'skipped', 'failed', 'cancelled')",
            name="ck_are_outcome_valid",
        ),
    )
    op.create_index(
        "ix_are_rule_id",
        "automation_rule_executions",
        ["rule_id"],
    )
    op.create_index(
        "ix_are_candidate_id",
        "automation_rule_executions",
        ["candidate_id"],
    )
    op.create_index(
        "ix_are_dedupe_key",
        "automation_rule_executions",
        ["dedupe_key"],
    )


def downgrade() -> None:
    op.drop_table("automation_rule_executions")
    op.drop_table("automation_rules")
    op.drop_table("candidate_document_items")
    op.drop_index("uq_cda_active_per_candidate_list", table_name="candidate_document_assignments")
    op.drop_table("candidate_document_assignments")
    op.drop_table("document_list_items")
    op.drop_table("document_list_versions")
    op.drop_table("document_lists")
