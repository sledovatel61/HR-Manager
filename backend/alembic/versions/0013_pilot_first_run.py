"""Local pilot first-run exchange and working mode. Revision 0013.

Phase 12 (Windows pilot): adds

* ``users.working_mode`` — the working role selected in the installer by the
  pilot owner (starter interface only, never RBAC);
* ``bootstrap_exchanges`` — one-time local exchange between the installer
  engine and the loopback backend (only the SHA-256 hash of the token is
  stored; startup bootstrap inserts a row when
  ``PILOT_BOOTSTRAP_EXCHANGE_TOKEN`` is configured and the user table is
  empty);
* ``bootstrap_tickets`` — short-lived one-time tickets handed to the browser
  after a successful claim; redemption creates the single pilot owner.

Both claim tables are append-only: triggers reject every UPDATE except the
single NULL->timestamp consumption and reject any DELETE. The raw token /
ticket values never touch the database.
"""

import sqlalchemy as sa

from alembic import op

revision = "0013"
down_revision = "0012"
branch_labels = None
depends_on = None

_EXCHANGE_GUARD = """
CREATE FUNCTION phase12_exchange_guard() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF TG_OP = 'DELETE' THEN
    RAISE EXCEPTION 'bootstrap_exchange_immutable' USING ERRCODE = '23514';
  END IF;
  IF OLD.consumed_at IS NOT NULL THEN
    RAISE EXCEPTION 'bootstrap_exchange_immutable' USING ERRCODE = '23514';
  END IF;
  IF NEW.consumed_at IS NOT NULL THEN
    IF NEW.id IS DISTINCT FROM OLD.id
       OR NEW.token_hash IS DISTINCT FROM OLD.token_hash
       OR NEW.created_at IS DISTINCT FROM OLD.created_at
       OR NEW.expires_at IS DISTINCT FROM OLD.expires_at
       OR NEW.client_ip IS DISTINCT FROM OLD.client_ip THEN
      RAISE EXCEPTION 'bootstrap_exchange_immutable' USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
  END IF;
  RAISE EXCEPTION 'bootstrap_exchange_immutable' USING ERRCODE = '23514';
END $$
"""

_TICKET_GUARD = """
CREATE FUNCTION phase12_ticket_guard() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF TG_OP = 'DELETE' THEN
    RAISE EXCEPTION 'bootstrap_ticket_immutable' USING ERRCODE = '23514';
  END IF;
  IF OLD.consumed_at IS NOT NULL THEN
    RAISE EXCEPTION 'bootstrap_ticket_immutable' USING ERRCODE = '23514';
  END IF;
  IF NEW.consumed_at IS NOT NULL THEN
    IF NEW.id IS DISTINCT FROM OLD.id
       OR NEW.ticket_hash IS DISTINCT FROM OLD.ticket_hash
       OR NEW.surname IS DISTINCT FROM OLD.surname
       OR NEW.working_mode IS DISTINCT FROM OLD.working_mode
       OR NEW.timezone IS DISTINCT FROM OLD.timezone
       OR NEW.created_at IS DISTINCT FROM OLD.created_at
       OR NEW.expires_at IS DISTINCT FROM OLD.expires_at
       OR NEW.client_ip IS DISTINCT FROM OLD.client_ip THEN
      RAISE EXCEPTION 'bootstrap_ticket_immutable' USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
  END IF;
  RAISE EXCEPTION 'bootstrap_ticket_immutable' USING ERRCODE = '23514';
END $$
"""


def upgrade() -> None:
    op.add_column("users", sa.Column("working_mode", sa.String(length=16), nullable=True))
    op.create_check_constraint(
        "ck_users_working_mode_valid",
        "users",
        "working_mode IS NULL OR working_mode IN ('hr', 'manager', 'admin')",
    )

    op.create_table(
        "bootstrap_exchanges",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("consumed_ticket_id", sa.Uuid(), nullable=True),
        sa.Column("client_ip", sa.String(length=64), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_bootstrap_exchanges"),
    )
    op.create_index(
        "ix_bootstrap_exchanges_token_hash", "bootstrap_exchanges", ["token_hash"], unique=True
    )
    op.create_index(
        "ix_bootstrap_exchanges_consumed", "bootstrap_exchanges", ["consumed_at"], unique=False
    )

    op.create_table(
        "bootstrap_tickets",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("ticket_hash", sa.String(length=64), nullable=False),
        sa.Column("surname", sa.String(length=60), nullable=False),
        sa.Column("working_mode", sa.String(length=16), nullable=False),
        sa.Column("timezone", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("redeemed_user_id", sa.Uuid(), nullable=True),
        sa.Column("client_ip", sa.String(length=64), nullable=True),
        sa.CheckConstraint(
            "working_mode IN ('hr', 'manager', 'admin')",
            name="ck_bootstrap_tickets_working_mode_valid",
        ),
        sa.ForeignKeyConstraint(
            ["redeemed_user_id"],
            ["users.id"],
            name="fk_bootstrap_tickets_redeemed_user",
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_bootstrap_tickets"),
    )
    op.create_index(
        "ix_bootstrap_tickets_token_hash", "bootstrap_tickets", ["ticket_hash"], unique=True
    )
    op.create_index(
        "ix_bootstrap_tickets_consumed", "bootstrap_tickets", ["consumed_at"], unique=False
    )

    op.execute(_EXCHANGE_GUARD)
    op.execute(
        "CREATE TRIGGER bootstrap_exchanges_guard BEFORE UPDATE OR DELETE "
        "ON bootstrap_exchanges FOR EACH ROW EXECUTE FUNCTION phase12_exchange_guard()"
    )
    op.execute(_TICKET_GUARD)
    op.execute(
        "CREATE TRIGGER bootstrap_tickets_guard BEFORE UPDATE OR DELETE "
        "ON bootstrap_tickets FOR EACH ROW EXECUTE FUNCTION phase12_ticket_guard()"
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER bootstrap_tickets_guard ON bootstrap_tickets")
    op.execute("DROP TRIGGER bootstrap_exchanges_guard ON bootstrap_exchanges")
    op.execute("DROP FUNCTION phase12_ticket_guard()")
    op.execute("DROP FUNCTION phase12_exchange_guard()")
    op.drop_index("ix_bootstrap_tickets_consumed", table_name="bootstrap_tickets")
    op.drop_index("ix_bootstrap_tickets_token_hash", table_name="bootstrap_tickets")
    op.drop_table("bootstrap_tickets")
    op.drop_index("ix_bootstrap_exchanges_consumed", table_name="bootstrap_exchanges")
    op.drop_index("ix_bootstrap_exchanges_token_hash", table_name="bootstrap_exchanges")
    op.drop_table("bootstrap_exchanges")
    op.drop_constraint("ck_users_working_mode_valid", "users", type_="check")
    op.drop_column("users", "working_mode")
