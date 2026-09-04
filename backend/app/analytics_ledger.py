"""Append-only analytics fact ledger (Phase 6 analytics and reports).

The ledger is the single source of truth for all analytics metrics:

* one immutable row per business fact;
* ``owner_user_id`` snapshots the responsible HR **at the fact moment**
  (ownership transfers never rewrite history);
* ``source`` snapshots the candidate source at the fact moment;
* writes are idempotent per business row via partial unique indexes;
* facts are written in the SAME transaction as the business operation —
  if the audit or ledger write fails, the whole operation rolls back.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import AnalyticsFact, AnalyticsFactType


def record_fact(
    db: Session,
    *,
    fact_type: AnalyticsFactType,
    candidate_id: uuid.UUID,
    owner_user_id: uuid.UUID,
    fact_at: datetime | None = None,
    fact_subtype: str | None = None,
    stage_from: str | None = None,
    stage_to: str | None = None,
    source: str | None = None,
    interaction_id: uuid.UUID | None = None,
    event_id: uuid.UUID | None = None,
    transfer_id: uuid.UUID | None = None,
    termination_id: uuid.UUID | None = None,
    # Historical backfill may need to override ``created_at`` (import/repair).
    created_at: datetime | None = None,
) -> AnalyticsFact:
    """Insert an analytics fact (no commit — caller's transaction owns it)."""
    fact = AnalyticsFact(
        candidate_id=candidate_id,
        fact_type=fact_type,
        fact_subtype=fact_subtype,
        fact_at=fact_at,
        stage_from=stage_from,
        stage_to=stage_to,
        source=source,
        owner_user_id=owner_user_id,
        interaction_id=interaction_id,
        event_id=event_id,
        transfer_id=transfer_id,
        termination_id=termination_id,
        created_at=created_at,
    )
    db.add(fact)
    return fact


def record_fact_idempotent(
    db: Session,
    *,
    fact_type: AnalyticsFactType,
    candidate_id: uuid.UUID,
    owner_user_id: uuid.UUID,
    **kwargs: Any,
) -> AnalyticsFact | None:
    """Insert a fact unless an equivalent fact already exists.

    Business rows are created exactly once, so the unique partial indexes
    guarantee idempotency; the existence check additionally tolerates a
    backfilled fact whose ``owner_user_id`` changed afterwards (the ledger
    keeps the fact-time owner and does not duplicate the row).
    """
    filters: list[Any] = [AnalyticsFact.fact_type == fact_type]
    if kwargs.get("interaction_id") is not None:
        filters.append(AnalyticsFact.interaction_id == kwargs["interaction_id"])
    elif kwargs.get("event_id") is not None:
        filters.append(AnalyticsFact.event_id == kwargs["event_id"])
    elif kwargs.get("transfer_id") is not None:
        filters.append(AnalyticsFact.transfer_id == kwargs["transfer_id"])
    elif kwargs.get("termination_id") is not None:
        filters.append(AnalyticsFact.termination_id == kwargs["termination_id"])
    elif fact_type == AnalyticsFactType.CANDIDATE_CREATED:
        filters.append(AnalyticsFact.candidate_id == candidate_id)
    else:
        # Stage changes legitimately repeat (back-and-forth moves); a precise
        # duplicate has identical candidate, kind, stage_from/stage_to and
        # fact_at. Backfill wrote exact copies, so this is enough to avoid
        # double rows for the same transition instant.
        filters.extend(
            [
                AnalyticsFact.candidate_id == candidate_id,
                AnalyticsFact.stage_from == kwargs.get("stage_from"),
                AnalyticsFact.stage_to == kwargs.get("stage_to"),
                AnalyticsFact.fact_at == kwargs.get("fact_at"),
            ]
        )
    if db.scalars(select(AnalyticsFact.id).where(*filters).limit(1)).first():
        return None
    return record_fact(
        db, fact_type=fact_type, candidate_id=candidate_id, owner_user_id=owner_user_id, **kwargs
    )


def bulk_record_facts(db: Session, facts: Sequence[AnalyticsFact]) -> None:
    """Bulk-insert pre-built facts (migration backfill)."""
    if facts:
        db.add_all(facts)
