"""analytics facts ledger and candidate terminations

Phase 6 (analytics and reports): an append-only analytics facts ledger and a
separate candidate-termination business entity.

The ledger is the single source of truth for every analytics metric:

* ``fact_at`` is the UTC instant of the fact (``from <= fact_at < to``
  period semantics);
* ``owner_user_id`` snapshots the responsible HR **at the fact moment**
  (later transfers never rewrite history);
* ``source`` snapshots the candidate source at the fact moment;
* partial unique indexes make writes idempotent per business row;
* rows are written in the same transaction as the business operation.

``candidate_terminations`` is a business entity (terminated_at + non-empty
safe reason) — the ``fired`` candidate stage alone cannot prove when or why a
termination happened, so the metric is derived from these records only.

Backfill: existing production data is imported into the ledger at upgrade
time so historical periods are reportable. It never fabricates facts and
never uses the *current* candidate stage as a transition:
* candidate_created        — from ``candidates.created_at``;
* interaction_added        — from ``candidate_interactions``;
* transfer                 — from ``candidate_transfers``;
* event_created/completed  — from ``events`` (created_at / completed_at);
* stage_changed            — replayed from ``audit_log``
  (action ``candidate_stage_changed``, details ``old -> new``), which is
  the only place where historical transitions are recorded.
Known limitation (documented in docs/ARCHITECTURE.md): facts backfilled
before this migration use the candidate's CURRENT source (historical source
edits are not reconstructible), and the fact-time owner is approximated as
the last transfer before the fact (falling back to the current owner);
pre-existing data older than the audit trail (e.g. truncated in tests) is
not reconstructible — a candidate whose created_at predates its earliest
available history keeps its created fact.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_UUID = postgresql.UUID(as_uuid=True)


def upgrade() -> None:
    op.create_table(
        "candidate_terminations",
        sa.Column("id", _UUID, primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column(
            "candidate_id",
            _UUID,
            sa.ForeignKey("candidates.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("terminated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("reason", sa.String(length=500), nullable=False),
        sa.Column(
            "created_by_user_id",
            _UUID,
            sa.ForeignKey("users.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "length(trim(reason)) > 0",
            name="ck_candidate_terminations_reason_not_blank",
        ),
    )
    op.create_index(
        "ix_candidate_terminations_candidate_id",
        "candidate_terminations",
        ["candidate_id"],
    )
    op.create_index(
        "ix_candidate_terminations_terminated_at",
        "candidate_terminations",
        ["terminated_at"],
    )

    op.create_table(
        "analytics_facts",
        sa.Column("id", _UUID, primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column(
            "candidate_id",
            _UUID,
            sa.ForeignKey("candidates.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("fact_type", sa.String(length=32), nullable=False),
        sa.Column("fact_subtype", sa.String(length=32), nullable=True),
        sa.Column("fact_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("stage_from", sa.String(length=32), nullable=True),
        sa.Column("stage_to", sa.String(length=32), nullable=True),
        sa.Column("source", sa.String(length=32), nullable=True),
        sa.Column(
            "owner_user_id",
            _UUID,
            sa.ForeignKey("users.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "interaction_id",
            _UUID,
            sa.ForeignKey("candidate_interactions.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column(
            "event_id",
            _UUID,
            sa.ForeignKey("events.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column(
            "transfer_id",
            _UUID,
            sa.ForeignKey("candidate_transfers.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column(
            "termination_id",
            _UUID,
            sa.ForeignKey("candidate_terminations.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "fact_type IN ('candidate_created', 'interaction_added', "
            "'stage_changed', 'transfer', 'event_created', 'event_completed', "
            "'terminated')",
            name="ck_analytics_facts_type_valid",
        ),
    )
    op.create_index("ix_analytics_facts_fact_at", "analytics_facts", ["fact_at"])
    op.create_index(
        "ix_analytics_facts_fact_at_owner",
        "analytics_facts",
        ["fact_at", "owner_user_id"],
    )
    op.create_index(
        "ix_analytics_facts_fact_at_source",
        "analytics_facts",
        ["fact_at", "source"],
    )
    op.create_index(
        "ix_analytics_facts_candidate_id",
        "analytics_facts",
        ["candidate_id"],
    )
    op.create_index("ix_analytics_facts_type", "analytics_facts", ["fact_type"])
    op.create_index(
        "uq_analytics_facts_created_candidate",
        "analytics_facts",
        ["candidate_id"],
        unique=True,
        postgresql_where=sa.text("fact_type = 'candidate_created'"),
        sqlite_where=sa.text("fact_type = 'candidate_created'"),
    )
    op.create_index(
        "uq_analytics_facts_interaction",
        "analytics_facts",
        ["interaction_id"],
        unique=True,
        postgresql_where=sa.text("interaction_id IS NOT NULL"),
        sqlite_where=sa.text("interaction_id IS NOT NULL"),
    )
    op.create_index(
        "uq_analytics_facts_event",
        "analytics_facts",
        ["event_id", "fact_type", "fact_at"],
        unique=True,
        postgresql_where=sa.text("event_id IS NOT NULL"),
        sqlite_where=sa.text("event_id IS NOT NULL"),
    )
    op.create_index(
        "uq_analytics_facts_transfer",
        "analytics_facts",
        ["transfer_id"],
        unique=True,
        postgresql_where=sa.text("transfer_id IS NOT NULL"),
        sqlite_where=sa.text("transfer_id IS NOT NULL"),
    )
    op.create_index(
        "uq_analytics_facts_termination",
        "analytics_facts",
        ["termination_id"],
        unique=True,
        postgresql_where=sa.text("termination_id IS NOT NULL"),
        sqlite_where=sa.text("termination_id IS NOT NULL"),
    )

    # --- Backfill (append-only import of existing history) -------------------

    # candidate_created (source and owner as recorded at creation time).
    op.execute(
        sa.text(
            """
            INSERT INTO analytics_facts
                (candidate_id, fact_type, fact_at, source, owner_user_id)
            SELECT c.id, 'candidate_created', c.created_at, c.source,
COALESCE(
    (
        SELECT t.to_user_id
        FROM candidate_transfers t
        WHERE t.candidate_id = c.id
          AND t.created_at <= c.created_at
        ORDER BY t.created_at DESC, t.id DESC
        LIMIT 1
    ),
    c.owner_user_id
)

            FROM candidates c
            WHERE NOT EXISTS (
                SELECT 1 FROM analytics_facts af
                WHERE af.candidate_id = c.id AND af.fact_type = 'candidate_created'
            )
            """
        )
    )

    # interaction_added: one fact per interaction row, at its creation time.
    op.execute(
        sa.text(
            """
            INSERT INTO analytics_facts
                (candidate_id, fact_type, fact_subtype, fact_at, source,
                 owner_user_id, interaction_id)
            SELECT i.candidate_id, 'interaction_added', i.type, i.created_at,
                   c.source,
COALESCE(
    (
        SELECT t.to_user_id
        FROM candidate_transfers t
        WHERE t.candidate_id = i.candidate_id
          AND t.created_at <= i.created_at
        ORDER BY t.created_at DESC, t.id DESC
        LIMIT 1
    ),
    c.owner_user_id
)
, i.id
            FROM candidate_interactions i
            JOIN candidates c ON c.id = i.candidate_id
            WHERE NOT EXISTS (
                SELECT 1 FROM analytics_facts af WHERE af.interaction_id = i.id
            )
            """
        )
    )

    # transfer: one fact per transfer row; the new owner is the fact-time owner.
    op.execute(
        sa.text(
            """
            INSERT INTO analytics_facts
                (candidate_id, fact_type, fact_at, source, owner_user_id,
                 transfer_id)
            SELECT t.candidate_id, 'transfer', t.created_at, c.source,
                   t.to_user_id, t.id
            FROM candidate_transfers t
            JOIN candidates c ON c.id = t.candidate_id
            WHERE NOT EXISTS (
                SELECT 1 FROM analytics_facts af WHERE af.transfer_id = t.id
            )
            """
        )
    )

    # event_created / event_completed: per event row; the event assignee is a
    # manager/admin, so the responsible HR is the candidate owner at the time.
    op.execute(
        sa.text(
            """
            INSERT INTO analytics_facts
                (candidate_id, fact_type, fact_subtype, fact_at, source,
                 owner_user_id, event_id)
            SELECT e.candidate_id, 'event_created', e.type, e.created_at,
                   c.source,
COALESCE(
    (
        SELECT t.to_user_id
        FROM candidate_transfers t
        WHERE t.candidate_id = e.candidate_id
          AND t.created_at <= e.created_at
        ORDER BY t.created_at DESC, t.id DESC
        LIMIT 1
    ),
    c.owner_user_id
)
, e.id
            FROM events e
            JOIN candidates c ON c.id = e.candidate_id
            WHERE NOT EXISTS (
                SELECT 1 FROM analytics_facts af WHERE af.event_id = e.id
            )
            """
        )
    )
    op.execute(
        sa.text(
            """
            INSERT INTO analytics_facts
                (candidate_id, fact_type, fact_subtype, fact_at, source,
                 owner_user_id, event_id)
            SELECT e.candidate_id, 'event_completed', e.type,
                   e.completed_at, c.source,
COALESCE(
    (
        SELECT t.to_user_id
        FROM candidate_transfers t
        WHERE t.candidate_id = e.candidate_id
          AND t.created_at <= e.completed_at
        ORDER BY t.created_at DESC, t.id DESC
        LIMIT 1
    ),
    c.owner_user_id
)
, e.id
            FROM events e
            JOIN candidates c ON c.id = e.candidate_id
            WHERE e.completed_at IS NOT NULL
              AND NOT EXISTS (
                  SELECT 1 FROM analytics_facts af
                  WHERE af.event_id = e.id AND af.fact_type = 'event_completed'
              )
            """
        )
    )

    # stage_changed: replayed from the audit log — the only historical record
    # of transitions. The audit actor is the fact-time owner; if the actor was
    # deleted the candidate's current owner is the best available approximation.
    op.execute(
        sa.text(
            """
            INSERT INTO analytics_facts
                (candidate_id, fact_type, fact_at, stage_from, stage_to,
                 source, owner_user_id)
            SELECT a.candidate_id, 'stage_changed', a.created_at,
                   split_part(a.details, ' -> ', 1),
                   split_part(a.details, ' -> ', 2),
                   c.source,

COALESCE(
    (
        SELECT t.to_user_id
        FROM candidate_transfers t
        WHERE t.candidate_id = a.candidate_id
          AND t.created_at <= a.created_at
        ORDER BY t.created_at DESC, t.id DESC
        LIMIT 1
    ),
    c.owner_user_id
)

            FROM audit_log a
            JOIN candidates c ON c.id = a.candidate_id
            WHERE a.action = 'candidate_stage_changed'
              AND a.details LIKE '% -> %'
              AND split_part(a.details, ' -> ', 2) IN (
                    'new', 'contacted', 'reached', 'interview_scheduled',
                    'interview_done', 'offer', 'hired', 'started',
                    'probation', 'fired', 'rejected'
              )
              AND NOT EXISTS (
                  SELECT 1 FROM analytics_facts af
                  WHERE af.candidate_id = a.candidate_id
                    AND af.fact_type = 'stage_changed'
                    AND af.stage_from = split_part(a.details, ' -> ', 1)
                    AND af.stage_to = split_part(a.details, ' -> ', 2)
                    AND af.fact_at = a.created_at
              )
            """
        )
    )


def downgrade() -> None:
    op.drop_table("analytics_facts")
    op.drop_table("candidate_terminations")
