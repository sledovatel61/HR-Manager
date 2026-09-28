"""Document templates with versioned content and immutable generated snapshots.

Revision 0015 (Phase 16, roadmap stage 6 «контент и шаблоны», textual MVP).

Adds three tables:

* ``document_templates`` — stable template entity (``kind`` + ``scope`` +
  editable ``name`` + optimistic ``revision`` counter). Templates are never
  physically deleted (operational phase 11 policy).
* ``document_template_versions`` — immutable content versions: ``title``,
  ``body`` and the sorted list of placeholders used. Only ``state``
  (``draft -> active -> archived``) and ``activated_at`` may change after
  creation; every content change requires a new version.
* ``candidate_document_generations`` — immutable snapshots of a document
  generated for a candidate: the exact rendered text/HTML, the template
  identity/name/title as of generation time and the content hash.

No files, no bytea, no external storage, no PDF/DOCX conversion and no
delivery to candidates — roadmap stage 6 is implemented here as versioned
*text* content with server-side rendering only.

PostgreSQL guards follow the phase 11/12 pattern with phase-16 function
names so every revision stays independently reversible.
"""

import sqlalchemy as sa

from alembic import op

revision = "0015"
down_revision = "0014"
branch_labels = None
depends_on = None

BODY_MAX_LENGTH = 20000

_GENERATION_GUARD = """
CREATE FUNCTION phase16_generation_guard() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'immutable_generation' USING ERRCODE = '23514';
END $$
"""

_VERSION_GUARD = """
CREATE FUNCTION phase16_version_guard() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'immutable_template_version' USING ERRCODE = '23514';
    END IF;
    -- Content columns are frozen: a correction always creates a new version.
    IF (to_jsonb(NEW) - 'state' - 'activated_at') IS DISTINCT FROM
       (to_jsonb(OLD) - 'state' - 'activated_at') THEN
        RAISE EXCEPTION 'immutable_template_version' USING ERRCODE = '23514';
    END IF;
    IF OLD.state = 'archived' AND NEW.state IS DISTINCT FROM 'archived' THEN
        RAISE EXCEPTION 'immutable_template_version' USING ERRCODE = '23514';
    END IF;
    IF NEW.state = 'draft' THEN
        IF OLD.state IS DISTINCT FROM 'draft'
           OR NEW.activated_at IS DISTINCT FROM OLD.activated_at THEN
            RAISE EXCEPTION 'immutable_template_version' USING ERRCODE = '23514';
        END IF;
    ELSIF NEW.state = 'active' THEN
        IF OLD.state <> 'draft' OR NEW.activated_at IS NULL THEN
            RAISE EXCEPTION 'immutable_template_version' USING ERRCODE = '23514';
        END IF;
    ELSIF NEW.state = 'archived' THEN
        IF NEW.activated_at IS DISTINCT FROM OLD.activated_at THEN
            RAISE EXCEPTION 'immutable_template_version' USING ERRCODE = '23514';
        END IF;
    ELSE
        RAISE EXCEPTION 'immutable_template_version' USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
END $$
"""

_TEMPLATE_GUARD = """
CREATE FUNCTION phase16_template_guard() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'immutable_template' USING ERRCODE = '23514';
    END IF;
    -- Identity (kind/scope/author) is frozen; only the display name, the
    -- optimistic counter and the timestamp may change.
    IF NEW.kind IS DISTINCT FROM OLD.kind
       OR NEW.scope IS DISTINCT FROM OLD.scope
       OR NEW.author_id IS DISTINCT FROM OLD.author_id
       OR NEW.created_at IS DISTINCT FROM OLD.created_at THEN
        RAISE EXCEPTION 'immutable_template' USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
END $$
"""


def upgrade() -> None:
    op.create_table(
        "document_templates",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("scope", sa.String(length=32), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("author_id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("revision > 0", name="ck_document_templates_revision"),
        sa.ForeignKeyConstraint(
            ["author_id"],
            ["users.id"],
            name="fk_document_templates_author",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_document_templates"),
    )
    op.create_index(
        "ix_document_templates_scope_kind", "document_templates", ["scope", "kind"], unique=False
    )
    op.create_table(
        "document_template_versions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("template_id", sa.Uuid(), nullable=False),
        sa.Column("number", sa.Integer(), nullable=False),
        sa.Column("state", sa.String(length=16), nullable=False),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("placeholders", sa.JSON(), nullable=False),
        sa.Column("author_id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("activated_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("number > 0", name="ck_template_versions_number"),
        sa.CheckConstraint(
            "state IN ('draft','active','archived')", name="ck_template_versions_state"
        ),
        sa.CheckConstraint(
            f"length(body) >= 1 AND length(body) <= {BODY_MAX_LENGTH}",
            name="ck_template_versions_body_length",
        ),
        sa.ForeignKeyConstraint(
            ["author_id"],
            ["users.id"],
            name="fk_template_versions_author",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["template_id"],
            ["document_templates.id"],
            name="fk_template_versions_template",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_template_versions"),
        sa.UniqueConstraint("template_id", "number", name="uq_template_versions_number"),
    )
    op.create_index(
        "uq_template_versions_active",
        "document_template_versions",
        ["template_id"],
        unique=True,
        postgresql_where=sa.text("state = 'active'"),
        sqlite_where=sa.text("state = 'active'"),
    )
    op.create_table(
        "candidate_document_generations",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("candidate_id", sa.Uuid(), nullable=False),
        sa.Column("template_id", sa.Uuid(), nullable=False),
        sa.Column("template_version_id", sa.Uuid(), nullable=False),
        sa.Column("template_number", sa.Integer(), nullable=False),
        sa.Column("template_name", sa.String(length=120), nullable=False),
        sa.Column("template_title", sa.String(length=200), nullable=False),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("body_text", sa.Text(), nullable=False),
        sa.Column("body_html", sa.Text(), nullable=False),
        sa.Column("content_sha256", sa.String(length=64), nullable=False),
        sa.Column("idempotency_key", sa.String(length=64), nullable=True),
        sa.Column("created_by", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("revision > 0", name="ck_document_generations_revision"),
        sa.CheckConstraint("template_number > 0", name="ck_document_generations_number"),
        sa.ForeignKeyConstraint(
            ["candidate_id"],
            ["candidates.id"],
            name="fk_document_generations_candidate",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["created_by"],
            ["users.id"],
            name="fk_document_generations_author",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["template_id"],
            ["document_templates.id"],
            name="fk_document_generations_template",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["template_version_id"],
            ["document_template_versions.id"],
            name="fk_document_generations_version",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_candidate_document_generations"),
        sa.UniqueConstraint("candidate_id", "revision", name="uq_document_generations_revision"),
    )
    op.create_index(
        "uq_document_generations_idempotency",
        "candidate_document_generations",
        ["created_by", "idempotency_key"],
        unique=True,
        postgresql_where=sa.text("idempotency_key IS NOT NULL"),
        sqlite_where=sa.text("idempotency_key IS NOT NULL"),
    )
    op.create_index(
        "ix_document_generations_candidate",
        "candidate_document_generations",
        ["candidate_id", "revision"],
        unique=False,
    )

    op.execute(_GENERATION_GUARD)
    op.execute(
        "CREATE TRIGGER phase16_generation_immutable BEFORE UPDATE OR DELETE "
        "ON candidate_document_generations FOR EACH ROW "
        "EXECUTE FUNCTION phase16_generation_guard()"
    )
    op.execute(_VERSION_GUARD)
    op.execute(
        "CREATE TRIGGER phase16_version_immutable BEFORE UPDATE OR DELETE "
        "ON document_template_versions FOR EACH ROW "
        "EXECUTE FUNCTION phase16_version_guard()"
    )
    op.execute(_TEMPLATE_GUARD)
    op.execute(
        "CREATE TRIGGER phase16_template_immutable BEFORE UPDATE OR DELETE "
        "ON document_templates FOR EACH ROW EXECUTE FUNCTION phase16_template_guard()"
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER phase16_template_immutable ON document_templates")
    op.execute("DROP TRIGGER phase16_version_immutable ON document_template_versions")
    op.execute("DROP TRIGGER phase16_generation_immutable ON candidate_document_generations")
    op.execute("DROP FUNCTION phase16_template_guard()")
    op.execute("DROP FUNCTION phase16_version_guard()")
    op.execute("DROP FUNCTION phase16_generation_guard()")
    op.drop_index(
        "ix_document_generations_candidate",
        table_name="candidate_document_generations",
    )
    op.drop_index(
        "uq_document_generations_idempotency",
        table_name="candidate_document_generations",
        postgresql_where=sa.text("idempotency_key IS NOT NULL"),
        sqlite_where=sa.text("idempotency_key IS NOT NULL"),
    )
    op.drop_table("candidate_document_generations")
    op.drop_index(
        "uq_template_versions_active",
        table_name="document_template_versions",
        postgresql_where=sa.text("state = 'active'"),
        sqlite_where=sa.text("state = 'active'"),
    )
    op.drop_table("document_template_versions")
    op.drop_index("ix_document_templates_scope_kind", table_name="document_templates")
    op.drop_table("document_templates")
