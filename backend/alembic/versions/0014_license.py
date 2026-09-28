"""Offline pilot license — installation/server. Revision 0014.

Phase 15: adds ``licenses`` table for closed pilot offline license.

* license_id — UUID of the license file itself (unique)
* client_name — pilot/client display name
* issued_at — UTC datetime of issuance
* expires_at — YYYY-MM-DD string, inclusive until 23:59:59 UTC
* expires_at_end — UTC datetime of end-of-day (for queries)
* max_active_users — integer 1..1000
* signature — 128 hex Ed25519
* is_active — only one active at a time (partial unique index)
* last_seen_at — monotonic clock-rollback protection
* uploaded_by_user_id — admin who uploaded

No raw license file stored, only metadata + signature (PII-safe logs).
"""

import contextlib

import sqlalchemy as sa

from alembic import op

revision = "0014"
down_revision = "0013"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "licenses",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("license_id", sa.String(length=36), nullable=False),
        sa.Column("client_name", sa.String(length=200), nullable=False),
        sa.Column("issued_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.String(length=10), nullable=False),
        sa.Column("expires_at_end", sa.DateTime(timezone=True), nullable=False),
        sa.Column("max_active_users", sa.Integer(), nullable=False),
        sa.Column("signature", sa.String(length=128), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("uploaded_by_user_id", sa.Uuid(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("max_active_users >= 1", name="ck_licenses_max_users_min"),
        sa.CheckConstraint("max_active_users <= 1000", name="ck_licenses_max_users_max"),
        sa.ForeignKeyConstraint(
            ["uploaded_by_user_id"],
            ["users.id"],
            name="fk_licenses_uploaded_by",
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_licenses"),
    )
    op.create_index("ix_licenses_license_id", "licenses", ["license_id"], unique=True)
    op.create_index("ix_licenses_is_active", "licenses", ["is_active"], unique=False)
    op.create_index("ix_licenses_expires_at", "licenses", ["expires_at"], unique=False)
    # Partial unique: only one active license at a time (Postgres + SQLite)
    # Postgres: WHERE is_active = true, SQLite: WHERE is_active
    with contextlib.suppress(Exception):
        op.create_index(
            "uq_licenses_one_active",
            "licenses",
            ["is_active"],
            unique=True,
            postgresql_where=sa.text("is_active = true"),
            sqlite_where=sa.text("is_active"),
        )


def downgrade() -> None:
    with contextlib.suppress(Exception):
        op.drop_index("uq_licenses_one_active", table_name="licenses")
    op.drop_index("ix_licenses_expires_at", table_name="licenses")
    op.drop_index("ix_licenses_is_active", table_name="licenses")
    op.drop_index("ix_licenses_license_id", table_name="licenses")
    op.drop_table("licenses")
