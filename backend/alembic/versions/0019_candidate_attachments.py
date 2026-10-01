"""Candidate attachments: DOCX questionnaires and PDF scans on the candidate card.

Revision 0019.

Revision 0018 belongs to the work-schedule import (PR #46), so this one is
0019: two files claiming the same revision id make Alembic warn «Revision
0018 is present more than once» and silently drop one of the two branches
from the chain. ``down_revision`` points at 0017 for as long as 0018 is not
in ``main``; once PR #46 is merged this becomes 0018 and the chain is linear
again (0017 -> 0018 -> 0019).

Adds one table, ``candidate_attachments``: a protected attachment of a
candidate — a questionnaire (``.docx``) or a scan (``.pdf``) uploaded by an HR.
This deliberately widens the phase-16 «documents without files» boundary: the
generated-document contour stays textual, while a file *received from a user*
is now stored. See ``docs/candidate-attachments.md``.

Storage decision: the bytes live in PostgreSQL (``BYTEA``), not on a
filesystem volume. That keeps the existing backup/restore contract intact —
the regular ``pg_dump``/``pg_restore`` backup already covers the whole
database, so attachments survive an update, a rollback and a reinstall without
a new volume, a new retention rule or a second backup path. Nothing is written
to a public frontend directory, and no file is served statically.

Guarantees encoded in the schema:

* ``kind`` is a closed vocabulary (``docx``/``pdf``) and ``size_bytes`` is
  positive, so a row can never describe an arbitrary or empty file;
* ``sha256`` is always a full 64-character digest (audit/deduplication);
* one *active* attachment per (candidate, filename) via a partial unique index
  — a second upload of the same name is a conflict, while a deleted name is
  freed for reuse;
* a trigger freezes the stored bytes and their identity: content, hash, size,
  name, kind, candidate and uploader cannot be edited in place, and rows are
  never deleted — removal is the soft ``deleted_at`` the application sets.

Fully reversible: ``downgrade`` drops the guard and the table. No existing
table or row is touched.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0019"
down_revision: str | None = "0017"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_ATTACHMENT_GUARD = """
CREATE FUNCTION candidate_attachments_guard() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    -- Строки вложений не удаляются: удаление в приложении мягкое (deleted_at).
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'immutable_candidate_attachment' USING ERRCODE = '23514';
    END IF;
    -- Сохранённые байты и их реквизиты неизменяемы: исправление — это новое
    -- вложение. Меняются только отметка удаления и updated_at.
    IF (to_jsonb(NEW) - 'deleted_at' - 'deleted_by_user_id' - 'updated_at')
       IS DISTINCT FROM
       (to_jsonb(OLD) - 'deleted_at' - 'deleted_by_user_id' - 'updated_at') THEN
        RAISE EXCEPTION 'immutable_candidate_attachment' USING ERRCODE = '23514';
    END IF;
    -- Восстановление удалённого вложения не предусмотрено интерфейсом.
    IF OLD.deleted_at IS NOT NULL AND NEW.deleted_at IS DISTINCT FROM OLD.deleted_at THEN
        RAISE EXCEPTION 'immutable_candidate_attachment' USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
END $$
"""


def upgrade() -> None:
    op.create_table(
        "candidate_attachments",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("candidate_id", sa.Uuid(), nullable=False),
        sa.Column("filename", sa.String(length=255), nullable=False),
        sa.Column("kind", sa.String(length=8), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        # Document bytes. Stored in the database rather than on a filesystem
        # volume so the regular pg_dump/pg_restore backup already covers them.
        sa.Column("content", sa.LargeBinary(), nullable=False),
        sa.Column("uploaded_by_user_id", sa.Uuid(), nullable=True),
        sa.Column("uploaded_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("deleted_by_user_id", sa.Uuid(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("kind IN ('docx', 'pdf')", name="ck_candidate_attachments_kind_valid"),
        sa.CheckConstraint("size_bytes > 0", name="ck_candidate_attachments_size_positive"),
        sa.CheckConstraint(
            "length(filename) >= 1", name="ck_candidate_attachments_filename_present"
        ),
        sa.CheckConstraint("length(sha256) = 64", name="ck_candidate_attachments_sha256_length"),
        sa.ForeignKeyConstraint(
            ["candidate_id"],
            ["candidates.id"],
            name="fk_candidate_attachments_candidate",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["uploaded_by_user_id"],
            ["users.id"],
            name="fk_candidate_attachments_uploader",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["deleted_by_user_id"],
            ["users.id"],
            name="fk_candidate_attachments_deleter",
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_candidate_attachments"),
    )
    op.create_index(
        "ix_candidate_attachments_candidate_id",
        "candidate_attachments",
        ["candidate_id"],
        unique=False,
    )
    # One ACTIVE attachment per (candidate, filename); a deleted name is freed
    # so the same file can be uploaded again.
    op.create_index(
        "uq_candidate_attachments_candidate_filename",
        "candidate_attachments",
        ["candidate_id", "filename"],
        unique=True,
        postgresql_where=sa.text("deleted_at IS NULL"),
        sqlite_where=sa.text("deleted_at IS NULL"),
    )
    op.execute(_ATTACHMENT_GUARD)
    op.execute(
        """
        CREATE TRIGGER candidate_attachments_immutable
        BEFORE UPDATE OR DELETE ON candidate_attachments
        FOR EACH ROW EXECUTE FUNCTION candidate_attachments_guard()
        """
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS candidate_attachments_immutable ON candidate_attachments")
    op.execute("DROP FUNCTION IF EXISTS candidate_attachments_guard()")
    op.drop_index("uq_candidate_attachments_candidate_filename", table_name="candidate_attachments")
    op.drop_index("ix_candidate_attachments_candidate_id", table_name="candidate_attachments")
    op.drop_table("candidate_attachments")
