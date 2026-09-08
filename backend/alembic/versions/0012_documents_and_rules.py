"""Versioned document snapshots and personal rules. Revision 0012."""

import sqlalchemy as sa

from alembic import op

revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_constraint("ck_access_grants_scope_valid", "access_grants", type_="check")
    op.create_check_constraint(
        "ck_access_grants_scope_valid",
        "access_grants",
        "scope IN ('pilot_full_access','document_lists_manage','candidate_documents_all')",
    )
    op.create_table(
        "document_lists",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("stage", sa.String(length=32), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("author_id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("version > 0", name="ck_document_lists_version"),
        sa.ForeignKeyConstraint(
            ["author_id"], ["users.id"], name="fk_document_lists_author", ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id", name="pk_document_lists"),
    )
    op.create_table(
        "document_rules",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("owner_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("params", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("version > 0", name="ck_document_rules_version"),
        sa.ForeignKeyConstraint(
            ["owner_id"], ["users.id"], name="fk_document_rules_owner", ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id", name="pk_document_rules"),
    )
    op.create_index("ix_document_rules_owner", "document_rules", ["owner_id"], unique=False)
    op.create_table(
        "document_list_versions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("list_id", sa.Uuid(), nullable=False),
        sa.Column("number", sa.Integer(), nullable=False),
        sa.Column("stage", sa.String(length=32), nullable=False),
        sa.Column("state", sa.String(length=16), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("description", sa.String(length=500), nullable=False),
        sa.Column("items", sa.JSON(), nullable=False),
        sa.Column("author_id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "state IN ('draft','published','archived')", name="ck_document_versions_state"
        ),
        sa.CheckConstraint("number > 0", name="ck_document_versions_number"),
        sa.ForeignKeyConstraint(
            ["author_id"], ["users.id"], name="fk_document_versions_author", ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["list_id"],
            ["document_lists.id"],
            name="fk_document_versions_list",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_document_list_versions"),
        sa.UniqueConstraint("list_id", "number", name="uq_document_versions_number"),
    )
    op.create_index(
        "uq_document_versions_published_scope",
        "document_list_versions",
        ["stage"],
        unique=True,
        postgresql_where=sa.text("state = 'published'"),
        sqlite_where=sa.text("state = 'published'"),
    )
    op.create_table(
        "candidate_document_sets",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("candidate_id", sa.Uuid(), nullable=False),
        sa.Column("list_version_id", sa.Uuid(), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("snapshot", sa.JSON(), nullable=False),
        sa.Column("author_id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["author_id"], ["users.id"], name="fk_document_sets_author", ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["candidate_id"],
            ["candidates.id"],
            name="fk_document_sets_candidate",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["list_version_id"],
            ["document_list_versions.id"],
            name="fk_document_sets_version",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_candidate_document_sets"),
        sa.UniqueConstraint("candidate_id", "revision", name="uq_document_sets_revision"),
    )
    op.create_table(
        "document_rule_executions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("rule_id", sa.Uuid(), nullable=False),
        sa.Column("rule_version", sa.Integer(), nullable=False),
        sa.Column("candidate_id", sa.Uuid(), nullable=False),
        sa.Column("trigger_id", sa.Uuid(), nullable=False),
        sa.Column("trigger_version", sa.Integer(), nullable=False),
        sa.Column("action", sa.String(length=32), nullable=False),
        sa.Column("outcome", sa.String(length=32), nullable=False),
        sa.Column("params", sa.JSON(), nullable=False),
        sa.Column("dedupe_key", sa.String(length=255), nullable=False),
        sa.Column("outbox_id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "action IN ('apply_list','document_request','document_reminder')",
            name="ck_rule_executions_action",
        ),
        sa.CheckConstraint(
            "outcome IN ('applied','queued','skipped','cancelled','failed')",
            name="ck_rule_executions_outcome",
        ),
        sa.ForeignKeyConstraint(
            ["candidate_id"],
            ["candidates.id"],
            name="fk_rule_executions_candidate",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["outbox_id"],
            ["notification_outbox.id"],
            name="fk_rule_executions_outbox",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["rule_id"], ["document_rules.id"], name="fk_rule_executions_rule", ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id", name="pk_document_rule_executions"),
        sa.UniqueConstraint("dedupe_key", name="uq_rule_executions_dedupe"),
    )
    op.create_table(
        "candidate_document_items",
        sa.Column("set_id", sa.Uuid(), nullable=False),
        sa.Column("key", sa.String(length=64), nullable=False),
        sa.Column("state", sa.String(length=16), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("changed_by", sa.Uuid(), nullable=False),
        sa.Column("changed_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("state IN ('missing','received')", name="ck_document_items_state"),
        sa.CheckConstraint("version > 0", name="ck_document_items_version"),
        sa.ForeignKeyConstraint(
            ["changed_by"], ["users.id"], name="fk_document_items_author", ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["set_id"],
            ["candidate_document_sets.id"],
            name="fk_document_items_set",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("set_id", "key", name="pk_candidate_document_items"),
    )
    op.add_column("notification_outbox", sa.Column("document_context", sa.JSON(), nullable=True))

    op.execute("""
        CREATE FUNCTION phase11_immutable() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN RAISE EXCEPTION 'immutable_history' USING ERRCODE = '23514'; END $$
    """)
    op.execute(
        "CREATE TRIGGER document_rules_no_delete BEFORE DELETE ON document_rules "
        "FOR EACH ROW EXECUTE FUNCTION phase11_immutable()"
    )
    op.create_check_constraint(
        "ck_document_rules_trigger",
        "document_rules",
        "params->>'trigger' IN ('stage_transition','scheduled_reminder')",
    )
    op.create_check_constraint(
        "ck_document_rules_action",
        "document_rules",
        "params->>'action' IN ('apply_list','document_request','document_reminder')",
    )
    op.create_check_constraint(
        "ck_document_rules_channel",
        "document_rules",
        "params->>'channel' IS NULL OR params->>'channel' IN ('email','telegram')",
    )
    for table in ("candidate_document_sets", "document_rule_executions"):
        op.execute(
            f"CREATE TRIGGER immutable_{table} BEFORE UPDATE OR DELETE ON {table} "
            "FOR EACH ROW EXECUTE FUNCTION phase11_immutable()"
        )
    op.execute("""
        CREATE FUNCTION phase11_version_guard() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF TG_OP = 'DELETE' THEN
                RAISE EXCEPTION 'immutable_version' USING ERRCODE = '23514';
            END IF;
            IF (to_jsonb(NEW) - 'state' - 'published_at') IS DISTINCT FROM
               (to_jsonb(OLD) - 'state' - 'published_at')
               OR (OLD.state <> 'draft' AND NEW.state <> 'archived')
               OR (OLD.state <> 'draft' AND NEW.published_at IS DISTINCT FROM OLD.published_at)
            THEN RAISE EXCEPTION 'immutable_version' USING ERRCODE = '23514'; END IF;
            RETURN NEW;
        END $$
    """)
    op.execute(
        "CREATE TRIGGER immutable_document_version BEFORE UPDATE OR DELETE "
        "ON document_list_versions FOR EACH ROW EXECUTE FUNCTION phase11_version_guard()"
    )
    op.execute("""
        CREATE FUNCTION phase11_list_guard() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF NEW.stage IS DISTINCT FROM OLD.stage THEN
                RAISE EXCEPTION 'immutable_scope' USING ERRCODE = '23514';
            END IF;
            RETURN NEW;
        END $$
    """)
    op.execute(
        "CREATE TRIGGER immutable_document_scope BEFORE UPDATE ON document_lists "
        "FOR EACH ROW EXECUTE FUNCTION phase11_list_guard()"
    )
    op.execute("""
        CREATE FUNCTION phase11_outbox_guard() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF OLD.document_context IS NOT NULL AND
               (TG_OP = 'DELETE' OR NEW.document_context::jsonb
                   IS DISTINCT FROM OLD.document_context::jsonb
                OR NEW.body IS DISTINCT FROM OLD.body OR NEW.title IS DISTINCT FROM OLD.title
                OR NEW.rule_id IS DISTINCT FROM OLD.rule_id
                OR NEW.initiator_user_id IS DISTINCT FROM OLD.initiator_user_id
                OR NEW.template_version IS DISTINCT FROM OLD.template_version) THEN
                RAISE EXCEPTION 'immutable_message' USING ERRCODE = '23514';
            END IF;
            IF TG_OP = 'DELETE' THEN RETURN OLD; END IF;
            RETURN NEW;
        END $$
    """)
    op.execute(
        "CREATE TRIGGER document_outbox_immutable BEFORE UPDATE OR DELETE ON notification_outbox "
        "FOR EACH ROW EXECUTE FUNCTION phase11_outbox_guard()"
    )
    op.execute(
        "CREATE TRIGGER document_attempt_immutable BEFORE UPDATE OR DELETE "
        "ON notification_delivery_attempts "
        "FOR EACH ROW EXECUTE FUNCTION phase11_immutable()"
    )

    op.create_index(
        "ix_document_rule_outbox_discovery",
        "notification_outbox",
        ["rule_id", "template_version", "object_type", "object_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_document_rule_outbox_discovery", table_name="notification_outbox")
    op.execute("DROP TRIGGER document_outbox_immutable ON notification_outbox")
    op.execute("DROP TRIGGER document_attempt_immutable ON notification_delivery_attempts")
    op.execute("DROP FUNCTION phase11_outbox_guard()")
    op.execute("DROP FUNCTION phase11_immutable() CASCADE")
    op.execute("DROP FUNCTION phase11_version_guard() CASCADE")
    op.execute("DROP FUNCTION phase11_list_guard() CASCADE")
    # Grants introduced in this revision have no meaning in the older schema.
    op.execute("DELETE FROM access_grants WHERE scope <> 'pilot_full_access'")
    op.drop_constraint("ck_access_grants_scope_valid", "access_grants", type_="check")
    op.create_check_constraint(
        "ck_access_grants_scope_valid", "access_grants", "scope = 'pilot_full_access'"
    )
    op.drop_column("notification_outbox", "document_context")
    op.drop_table("candidate_document_items")
    op.drop_table("document_rule_executions")
    op.drop_table("candidate_document_sets")
    op.drop_index(
        "uq_document_versions_published_scope",
        table_name="document_list_versions",
        postgresql_where=sa.text("state = 'published'"),
        sqlite_where=sa.text("state = 'published'"),
    )
    op.drop_table("document_list_versions")
    op.drop_index("ix_document_rules_owner", table_name="document_rules")
    op.drop_table("document_rules")
    op.drop_table("document_lists")
