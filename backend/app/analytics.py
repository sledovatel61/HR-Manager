"""Analytics engine (Phase 6): SQL aggregations over the facts ledger.

The single source of truth for every metric is the append-only
``analytics_facts`` ledger (see models.AnalyticsFact). All queries aggregate
in SQL — the whole candidates table is never loaded into Python.

Period semantics: ``from <= fact_at < to`` (half-open interval, both
boundaries compared as UTC instants — never machine-local time). The
``timezone`` parameter is validated (IANA database) and echoed back; the
client uses it to compute preset boundaries, the server never converts fact
timestamps with it.

Funnel: fixed order from ``CANDIDATE_STAGE_ORDER`` excluding the terminal
``fired`` / ``rejected`` stages (those are measured by ``dismissed`` /
``terminated`` instead). Conversions are cohort conversions: numerator =
unique candidates who reached B in the period AFTER reaching A; denominator =
unique candidates who reached A in the period. Rate is ``null`` (never 0)
when the denominator is 0, otherwise 0..100 rounded to at most 2 decimals.
"""

from __future__ import annotations

import itertools
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import HTTPException, status
from sqlalchemy import and_, func, select
from sqlalchemy.orm import Session, aliased

from .models import (
    CANDIDATE_STAGE_ORDER,
    AnalyticsFact,
    AnalyticsFactType,
    CandidateSource,
    CandidateStage,
    User,
    UserRole,
)

MAX_PERIOD_DAYS = 366

# The hiring funnel: all enum stages except the terminal fired/rejected.
FUNNEL_STAGES: tuple[CandidateStage, ...] = tuple(
    stage
    for stage in CANDIDATE_STAGE_ORDER
    if stage not in (CandidateStage.FIRED, CandidateStage.REJECTED)
)

# Business activity that counts towards ``processed_candidates`` (prompt
# definition: interaction, stage change, transfer, event created/completed).
_PROCESSED_TYPES: tuple[AnalyticsFactType, ...] = (
    AnalyticsFactType.INTERACTION_ADDED,
    AnalyticsFactType.STAGE_CHANGED,
    AnalyticsFactType.TRANSFER,
    AnalyticsFactType.EVENT_CREATED,
    AnalyticsFactType.EVENT_COMPLETED,
)

_HIRED_STAGES = (CandidateStage.HIRED.value, CandidateStage.STARTED.value)


@dataclass(frozen=True)
class PeriodQuery:
    """Normalized, validated analytics query parameters."""

    from_dt: datetime  # aware UTC instant
    to_dt: datetime  # aware UTC instant
    timezone: ZoneInfo
    hr_id: uuid.UUID | None
    source: CandidateSource | None


def _as_utc(value: datetime) -> datetime:
    """Canonical UTC instant of an incoming timestamp (naive = UTC)."""
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def resolve_period_query(
    db: Session,
    *,
    from_dt: datetime,
    to_dt: datetime,
    timezone: str,
    hr_id: uuid.UUID | None,
    source: CandidateSource | None,
) -> PeriodQuery:
    """Validate and normalize query parameters; 422 with a clear Russian
    detail on any violation (existing error format)."""
    start = _as_utc(from_dt)
    end = _as_utc(to_dt)
    if start >= end:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Начало периода должно быть раньше окончания.",
        )
    if end - start > timedelta(days=MAX_PERIOD_DAYS):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Период не может превышать {MAX_PERIOD_DAYS} дней.",
        )
    try:
        tz = ZoneInfo(timezone)
    except ZoneInfoNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Неизвестная таймзона: {timezone}.",
        ) from exc

    if hr_id is not None:
        hr = db.get(User, hr_id)
        if hr is None or hr.role != UserRole.HR:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Фильтр по ответственному должен указывать на пользователя с ролью HR.",
            )

    return PeriodQuery(from_dt=start, to_dt=end, timezone=tz, hr_id=hr_id, source=source)


def _base_conditions(entity: Any, pq: PeriodQuery) -> list[Any]:
    """Period + filter conditions for a facts query (``entity`` is either
    ``AnalyticsFact`` or an alias of it)."""
    conditions = [
        entity.fact_at >= pq.from_dt,
        entity.fact_at < pq.to_dt,
    ]
    if pq.hr_id is not None:
        conditions.append(entity.owner_user_id == pq.hr_id)
    if pq.source is not None:
        conditions.append(entity.source == pq.source.value)
    return conditions


def _stage_reach(entity: Any, stage: CandidateStage) -> Any:
    """Condition "the candidate reached ``stage``".

    Reaching ``new`` = the creation fact; every other stage = a recorded
    ``stage_changed`` transition to it. Current-stage snapshots are never
    used — only facts that happened inside the period count.
    """
    if stage == CandidateStage.NEW:
        return entity.fact_type == AnalyticsFactType.CANDIDATE_CREATED
    return and_(
        entity.fact_type == AnalyticsFactType.STAGE_CHANGED,
        entity.stage_to == stage.value,
    )


def _count(db: Session, pq: PeriodQuery, *extra: Any) -> int:
    """``COUNT(*)`` over facts matching period/filters plus ``extra``."""
    stmt = select(func.count()).where(*_base_conditions(AnalyticsFact, pq), *extra)
    return int(db.execute(stmt).scalar_one())


def _count_candidates(db: Session, pq: PeriodQuery, *extra: Any) -> int:
    """``COUNT(DISTINCT candidate_id)`` over matching facts."""
    stmt = select(func.count(func.distinct(AnalyticsFact.candidate_id))).where(
        *_base_conditions(AnalyticsFact, pq), *extra
    )
    return int(db.execute(stmt).scalar_one())


def _count_events(db: Session, pq: PeriodQuery, fact_type: AnalyticsFactType) -> int:
    """``COUNT(DISTINCT event_id)`` for interview events created/completed."""
    stmt = select(func.count(func.distinct(AnalyticsFact.event_id))).where(
        *_base_conditions(AnalyticsFact, pq),
        AnalyticsFact.fact_type == fact_type,
        AnalyticsFact.fact_subtype == "interview",
        AnalyticsFact.event_id.is_not(None),
    )
    return int(db.execute(stmt).scalar_one())


def _kpi_numbers(db: Session, pq: PeriodQuery) -> dict[str, int]:
    """The ten KPI numbers, each a single SQL aggregation."""
    hired_extra = (
        AnalyticsFact.fact_type == AnalyticsFactType.STAGE_CHANGED,
        AnalyticsFact.stage_to.in_(_HIRED_STAGES),
    )
    return {
        "created_candidates": _count_candidates(
            db, pq, AnalyticsFact.fact_type == AnalyticsFactType.CANDIDATE_CREATED
        ),
        "processed_candidates": _count_candidates(
            db, pq, AnalyticsFact.fact_type.in_(_PROCESSED_TYPES)
        ),
        "calls": _count(
            db,
            pq,
            AnalyticsFact.fact_type == AnalyticsFactType.INTERACTION_ADDED,
            AnalyticsFact.fact_subtype == "call",
        ),
        "reached": _count_candidates(db, pq, _stage_reach(AnalyticsFact, CandidateStage.REACHED)),
        "interviews_scheduled": _count_events(db, pq, AnalyticsFactType.EVENT_CREATED),
        "interviews_done": _count_events(db, pq, AnalyticsFactType.EVENT_COMPLETED),
        "offers": _count_candidates(db, pq, _stage_reach(AnalyticsFact, CandidateStage.OFFER)),
        "hired": _count_candidates(db, pq, *hired_extra),
        "dismissed": _count_candidates(
            db, pq, _stage_reach(AnalyticsFact, CandidateStage.REJECTED)
        ),
        "terminated": _count_candidates(
            db, pq, AnalyticsFact.fact_type == AnalyticsFactType.TERMINATED
        ),
    }


def _conversion(
    db: Session, pq: PeriodQuery, from_stage: CandidateStage, to_stage: CandidateStage
) -> dict:
    """Cohort conversion ``from_stage -> to_stage``.

    Denominator: unique candidates who reached ``from_stage`` in the period.
    Numerator: those who also reached ``to_stage`` in the period AFTER
    reaching ``from_stage`` (a later fact in the same period). Repeated
    back-and-forth transitions never double-count (DISTINCT candidates).
    """
    reached_a = _count_candidates(db, pq, _stage_reach(AnalyticsFact, from_stage))

    if reached_a == 0:
        numerator = 0
    else:
        fa = aliased(AnalyticsFact)
        fb = aliased(AnalyticsFact)
        stmt = (
            select(func.count(func.distinct(fa.candidate_id)))
            .select_from(fa)
            .join(fb, fb.candidate_id == fa.candidate_id)
            .where(
                *_base_conditions(fa, pq),
                _stage_reach(fa, from_stage),
                *_base_conditions(fb, pq),
                _stage_reach(fb, to_stage),
                fb.fact_at > fa.fact_at,
            )
        )
        numerator = int(db.execute(stmt).scalar_one())

    rate = round(numerator / reached_a * 100, 2) if reached_a else None
    return {
        "from_stage": from_stage.value,
        "to_stage": to_stage.value,
        "numerator": numerator,
        "denominator": reached_a,
        "rate": rate,
    }


def _all_conversions(db: Session, pq: PeriodQuery) -> list[dict[str, Any]]:
    """Conversions for every consecutive funnel pair, in funnel order."""
    return [
        _conversion(db, pq, from_stage, to_stage)
        for from_stage, to_stage in itertools.pairwise(FUNNEL_STAGES)
    ]


def _funnel_counts(db: Session, pq: PeriodQuery) -> list[dict[str, Any]]:
    """Unique candidates reaching each funnel stage in the period."""
    return [
        {
            "stage": stage.value,
            "reached": _count_candidates(db, pq, _stage_reach(AnalyticsFact, stage)),
        }
        for stage in FUNNEL_STAGES
    ]


def _by_source(db: Session, pq: PeriodQuery) -> list[dict[str, Any]]:
    """Per-source breakdown; rows exist only for sources with facts in the
    period (never fabricated). ``source`` is the fact-time snapshot."""
    stmt = (
        select(
            AnalyticsFact.source,
            func.count(func.distinct(AnalyticsFact.candidate_id))
            .filter(AnalyticsFact.fact_type == AnalyticsFactType.CANDIDATE_CREATED)
            .label("created"),
            func.count(func.distinct(AnalyticsFact.candidate_id))
            .filter(
                AnalyticsFact.fact_type == AnalyticsFactType.STAGE_CHANGED,
                AnalyticsFact.stage_to.in_(_HIRED_STAGES),
            )
            .label("hired"),
            func.count(func.distinct(AnalyticsFact.candidate_id))
            .filter(
                AnalyticsFact.fact_type == AnalyticsFactType.STAGE_CHANGED,
                AnalyticsFact.stage_to == CandidateStage.REJECTED.value,
            )
            .label("dismissed"),
            func.count(func.distinct(AnalyticsFact.candidate_id))
            .filter(AnalyticsFact.fact_type == AnalyticsFactType.TERMINATED)
            .label("terminated"),
        )
        .where(*_base_conditions(AnalyticsFact, pq), AnalyticsFact.source.is_not(None))
        .group_by(AnalyticsFact.source)
        .order_by(AnalyticsFact.source)
    )
    rows = db.execute(stmt).all()
    return [
        {
            "source": row.source,
            "created": int(row.created),
            "hired": int(row.hired),
            "dismissed": int(row.dismissed),
            "terminated": int(row.terminated),
        }
        for row in rows
    ]


def _by_hr(db: Session, pq: PeriodQuery) -> list[dict[str, Any]]:
    """Per-HR breakdown. The owner is the responsible HR AT FACT TIME, so a
    transferred candidate's old facts stay attributed to the previous owner.
    Rows exist only for HRs with facts in the period (never fabricated)."""
    stmt = (
        select(
            AnalyticsFact.owner_user_id.label("hr_id"),
            User.username,
            func.count(func.distinct(AnalyticsFact.candidate_id))
            .filter(AnalyticsFact.fact_type == AnalyticsFactType.CANDIDATE_CREATED)
            .label("created"),
            func.count(func.distinct(AnalyticsFact.candidate_id))
            .filter(AnalyticsFact.fact_type.in_(_PROCESSED_TYPES))
            .label("processed"),
            func.count(func.distinct(AnalyticsFact.candidate_id))
            .filter(
                AnalyticsFact.fact_type == AnalyticsFactType.STAGE_CHANGED,
                AnalyticsFact.stage_to.in_(_HIRED_STAGES),
            )
            .label("hired"),
            func.count(func.distinct(AnalyticsFact.candidate_id))
            .filter(
                AnalyticsFact.fact_type == AnalyticsFactType.STAGE_CHANGED,
                AnalyticsFact.stage_to == CandidateStage.REJECTED.value,
            )
            .label("dismissed"),
            func.count(func.distinct(AnalyticsFact.candidate_id))
            .filter(AnalyticsFact.fact_type == AnalyticsFactType.TERMINATED)
            .label("terminated"),
        )
        .select_from(AnalyticsFact)
        .join(User, User.id == AnalyticsFact.owner_user_id)
        .where(*_base_conditions(AnalyticsFact, pq), User.role == UserRole.HR)
        .group_by(AnalyticsFact.owner_user_id, User.username)
        .order_by(func.lower(User.username), AnalyticsFact.owner_user_id)
    )
    rows = db.execute(stmt).all()
    return [
        {
            "hr_id": str(row.hr_id),
            "username": row.username,
            "created": int(row.created),
            "processed": int(row.processed),
            "hired": int(row.hired),
            "dismissed": int(row.dismissed),
            "terminated": int(row.terminated),
        }
        for row in rows
    ]


def _period_block(pq: PeriodQuery) -> dict[str, Any]:
    return {
        "from": pq.from_dt,
        "to": pq.to_dt,
        "timezone": pq.timezone.key,
    }


def _filters_block(pq: PeriodQuery) -> dict[str, Any]:
    return {
        "hr_id": str(pq.hr_id) if pq.hr_id is not None else None,
        "source": pq.source.value if pq.source is not None else None,
    }


def build_kpi_report(db: Session, pq: PeriodQuery) -> dict[str, Any]:
    """Full KPI report body (``GET /analytics/kpi``)."""
    return {
        "period": _period_block(pq),
        "filters": _filters_block(pq),
        "scope": "team",
        "kpis": _kpi_numbers(db, pq),
        "conversions": _all_conversions(db, pq),
        "by_source": _by_source(db, pq),
        "by_hr": _by_hr(db, pq),
    }


def build_funnel_report(db: Session, pq: PeriodQuery) -> dict[str, Any]:
    """Full funnel report body (``GET /analytics/funnel``)."""
    return {
        "period": _period_block(pq),
        "filters": _filters_block(pq),
        "stages": _funnel_counts(db, pq),
        "conversions": _all_conversions(db, pq),
    }


# --- CSV export --------------------------------------------------------------


def _csv_field(value: object) -> str:
    """One CSV cell: formula-injection neutralization + RFC 4180 escaping
    (commas, semicolons, quotes, newlines)."""
    if value is None:
        return ""
    text = str(value)
    # Neutralize spreadsheet formula injection (=, +, -, @ at the start).
    if text[:1] in ("=", "+", "-", "@"):
        text = "'" + text
    if any(ch in text for ch in (",", ";", '"', "\n", "\r")):
        text = '"' + text.replace('"', '""') + '"'
    return text


def _csv_row(fields: Any) -> str:
    if isinstance(fields, (list, tuple)):
        return ",".join(_csv_field(field) for field in fields)
    return _csv_field(fields)


def build_csv(db: Session, pq: PeriodQuery) -> tuple[str, str]:
    """Build the CSV export (rows joined with CRLF, UTF-8 BOM added by the
    caller) and the attachment filename.

    Layout (locked by tests):

    - report header + period/timezone/filters as ``key,value`` rows;
    - ``section,kpi`` → ``metric,value`` rows (10 KPIs, fixed order);
    - ``section,conversions`` → ``from_stage,to_stage,numerator,denominator,rate``;
    - ``section,funnel`` → ``stage,reached``;
    - ``section,by_source`` → ``source,created,hired,dismissed,terminated``;
    - ``section,by_hr`` → ``hr_id,username,created,processed,hired,dismissed,terminated``.
    """
    report = build_kpi_report(db, pq)
    kpis = report["kpis"]
    period = pq.from_dt.isoformat(), pq.to_dt.isoformat()

    rows: list[str] = [
        _csv_row("HR Manager Analytics Report"),
        _csv_row(("period_from", period[0])),
        _csv_row(("period_to", period[1])),
        _csv_row(("timezone", pq.timezone.key)),
        _csv_row(("scope", "team")),
        _csv_row(("hr_id", report["filters"]["hr_id"])),
        _csv_row(("source", report["filters"]["source"])),
        "",
        _csv_row(("section", "kpi")),
        _csv_row(("metric", "value")),
        _csv_row(("created_candidates", kpis["created_candidates"])),
        _csv_row(("processed_candidates", kpis["processed_candidates"])),
        _csv_row(("calls", kpis["calls"])),
        _csv_row(("reached", kpis["reached"])),
        _csv_row(("interviews_scheduled", kpis["interviews_scheduled"])),
        _csv_row(("interviews_done", kpis["interviews_done"])),
        _csv_row(("offers", kpis["offers"])),
        _csv_row(("hired", kpis["hired"])),
        _csv_row(("dismissed", kpis["dismissed"])),
        _csv_row(("terminated", kpis["terminated"])),
        "",
        _csv_row(("section", "conversions")),
        _csv_row(("from_stage", "to_stage", "numerator", "denominator", "rate")),
    ]
    for conversion in report["conversions"]:
        rate = None if conversion["rate"] is None else f"{conversion['rate']:.2f}"
        rows.append(
            _csv_row(
                (
                    conversion["from_stage"],
                    conversion["to_stage"],
                    conversion["numerator"],
                    conversion["denominator"],
                    rate,
                )
            )
        )
    rows.append("")
    rows.append(_csv_row(("section", "funnel")))
    rows.append(_csv_row(("stage", "reached")))
    for stage in build_funnel_report(db, pq)["stages"]:
        rows.append(_csv_row((stage["stage"], stage["reached"])))
    rows.append("")
    rows.append(_csv_row(("section", "by_source")))
    rows.append(_csv_row(("source", "created", "hired", "dismissed", "terminated")))
    for row in report["by_source"]:
        rows.append(
            _csv_row(
                (
                    row["source"],
                    row["created"],
                    row["hired"],
                    row["dismissed"],
                    row["terminated"],
                )
            )
        )
    rows.append("")
    rows.append(_csv_row(("section", "by_hr")))
    rows.append(
        _csv_row(("hr_id", "username", "created", "processed", "hired", "dismissed", "terminated"))
    )
    for row in report["by_hr"]:
        rows.append(
            _csv_row(
                (
                    row["hr_id"],
                    row["username"],
                    row["created"],
                    row["processed"],
                    row["hired"],
                    row["dismissed"],
                    row["terminated"],
                )
            )
        )

    from_day = pq.from_dt.astimezone(pq.timezone).strftime("%Y-%m-%d")
    to_day = pq.to_dt.astimezone(pq.timezone).strftime("%Y-%m-%d")
    filename = f"analytics-{from_day}-{to_day}.csv"
    return "\r\n".join(rows) + "\r\n", filename
