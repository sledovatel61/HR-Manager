"""Local pilot first-run: owner working mode, forced password change and the
one-shot first-run claim table. Revision 0013.

Phase 12 turns the Windows pilot into a one-click install. The owner is
created once by the installer's first-run exchange and keeps full pilot
access (server-side role ``admin`` + explicit ``pilot_full_access`` grant);
the chosen HR/manager/admin working mode is stored as a display/start-mode
profile field and never replaces RBAC. The initial owner password is a random
value the user never saw, so ``password_change_required`` forces them to set
their own password in the UI after the first login.
"""

import sqlalchemy as sa

from alembic import op

revision = "0013"
down_revision = "0012"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("working_mode", sa.String(length=16), nullable=True),
    )
    op.create_check_constraint(
        "ck_users_working_mode_valid",
        "users",
        "working_mode IS NULL OR working_mode IN ('hr', 'manager', 'admin')",
    )
    op.add_column(
        "users",
        sa.Column("password_change_required", sa.Boolean(), nullable=False, server_default="false"),
    )
    op.create_table(
        "pilot_first_run_claims",
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("claimed_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name="fk_pilot_first_run_claims_user", ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("token_hash", name="pk_pilot_first_run_claims"),
    )


def downgrade() -> None:
    op.drop_table("pilot_first_run_claims")
    op.drop_column("users", "password_change_required")
    op.drop_constraint("ck_users_working_mode_valid", "users", type_="check")
    op.drop_column("users", "working_mode")
