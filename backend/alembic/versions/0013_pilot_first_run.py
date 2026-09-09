"""Windows pilot: working mode, bootstrap-password flag and first-run pairing.

Revision ID: 0013
Revises: 0012
Create Date: 2026-09-09

Phase 12 additive migration:

* ``users.work_role`` — the installer's working-mode choice (UX label, never
  RBAC); nullable so no existing user is touched;
* ``users.password_is_bootstrap`` — the first-run owner may replace the
  never-shown generated password exactly once via the browser UI;
* ``pilot_pairings`` — one-shot loopback pairing codes (SHA-256 digest only),
  with a partial unique index guaranteeing at most one PENDING pairing.

Backward compatible: phase 0-11 code ignores the new columns/table; the
head-revision check in /ops/status stays green before and after.
"""

import sqlalchemy as sa

from alembic import op

revision = "0013"
down_revision = "0012"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("users", sa.Column("work_role", sa.String(length=16), nullable=True))
    op.create_check_constraint(
        "ck_users_work_role_valid",
        "users",
        "work_role IS NULL OR work_role IN ('hr', 'manager', 'admin')",
    )
    op.add_column(
        "users",
        sa.Column(
            "password_is_bootstrap",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )

    op.create_table(
        "pilot_pairings",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("code_hash", sa.String(length=64), nullable=False),
        sa.Column("work_role", sa.String(length=16), nullable=False),
        sa.Column("surname", sa.String(length=120), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("claimed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("claimed_user_id", sa.Uuid(), nullable=True),
        sa.CheckConstraint(
            "status IN ('pending', 'claimed', 'cancelled')",
            name="ck_pilot_pairings_status",
        ),
        sa.CheckConstraint(
            "work_role IN ('hr', 'manager', 'admin')",
            name="ck_pilot_pairings_work_role",
        ),
        sa.CheckConstraint("attempts >= 0", name="ck_pilot_pairings_attempts_non_negative"),
        sa.ForeignKeyConstraint(
            ["claimed_user_id"], ["users.id"], name="fk_pilot_pairings_claimed", ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id", name="pk_pilot_pairings"),
        sa.UniqueConstraint("code_hash", name="uq_pilot_pairings_code_hash"),
    )
    op.create_index(
        "uq_pilot_pairings_pending",
        "pilot_pairings",
        ["status"],
        unique=True,
        postgresql_where=sa.text("status = 'pending'"),
        sqlite_where=sa.text("status = 'pending'"),
    )


def downgrade() -> None:
    op.drop_index("uq_pilot_pairings_pending", table_name="pilot_pairings")
    op.drop_table("pilot_pairings")
    op.drop_column("users", "password_is_bootstrap")
    op.drop_constraint("ck_users_work_role_valid", "users", type_="check")
    op.drop_column("users", "work_role")
