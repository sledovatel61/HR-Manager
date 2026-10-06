"""«Моя очередь» dashboard: server-side aggregates for the analytics cards.

Why a dedicated endpoint
------------------------
The dashboard needs time series (created candidates, interview conversion,
average hiring days, hiring dynamics), a source breakdown, task counters and
the personal blocks of «Моя очередь». Assembling that in the browser would
mean shipping the raw candidate rows — the exact defect the queue summary
fixed, where one page of rows was presented as the whole queue. Every number
below is a SQL aggregate or a Python roll-up over rows **inside the caller's
scope**, never over a page and never over a ``limit``.

Scope (RBAC)
------------
Identical to the «Моя очередь» list (``GET /candidates`` with ``mode=queue``
and ``GET /candidates/queue/summary``):

* the scope owner is the caller for **every** role (HR, manager,
  administrator, pilot) — the section is called «Моя очередь»;
* a manager or an administrator may pass ``owner_id`` to look at a colleague
  (the same filter the list screen offers them);
* an HR can never widen the scope: ``owner_id`` is forced back to self, so
  another HR's data cannot leak even if the parameter is sent;
* soft-deleted candidates never count.

Filters (``position``, ``stage``, ``source``) are applied to the **current**
attributes of the candidate — the same values the list screen filters on — so
the list and the dashboard can never disagree about what «в работе» means.

Periods and timezones
---------------------
``today`` / ``week`` / ``all``. Unlike the manager analytics page (where the
browser computes the boundaries), the dashboard computes them **server-side**
in the requested IANA timezone: the endpoint must be reproducible from API
tests and from the scheduler, and a preset has to mean the same thing no
matter who asks. Buckets: hours for ``today``, days for ``week``, months for
``all`` (capped at :data:`MAX_ALL_MONTHS`).

Never fabricated
----------------
A metric that the data model cannot express is returned as ``None`` with a
human note in ``kpi_notes`` — never as ``0`` and never as a demo number.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from typing import Any
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import HTTPException, status
from sqlalchemy import ColumnElement, func, select
from sqlalchemy.orm import Session

from app.models import (
    CANDIDATE_STAGE_ORDER,
    AnalyticsFact,
    AnalyticsFactType,
    Candidate,
    CandidateSource,
    CandidateStage,
    Event,
    EventStatus,
    EventType,
    Notification,
    Reminder,
    ReminderStatus,
    User,
    UserRole,
)
from app.utils import normalize_position, utc_now

# --- Periods -----------------------------------------------------------------

DASHBOARD_PERIODS: tuple[str, ...] = ("today", "week", "all")
DEFAULT_DASHBOARD_TIMEZONE = "UTC"
#: ``datetime.UTC`` is a ``timezone``, not a ``ZoneInfo``; the helpers below
#: are typed on ``ZoneInfo`` (DST-aware arithmetic), so keep one instance.
UTC_ZONE: ZoneInfo = ZoneInfo("UTC")
#: «Всё» is bucketed by month; keep the chart readable instead of unbounded.
MAX_ALL_MONTHS = 24
#: Safety valve for the Python roll-up (facts of one scope are far below it).
FACT_SCAN_LIMIT = 200_000
#: Business definitions reused by the KPI and the hiring-dynamics chart.
HIRED_STAGES: tuple[CandidateStage, ...] = (CandidateStage.HIRED, CandidateStage.STARTED)
INTERVIEW_STAGES: tuple[CandidateStage, ...] = (
    CandidateStage.INTERVIEW_SCHEDULED,
    CandidateStage.INTERVIEW_DONE,
)
STUCK_DAYS = 3
HORIZON_DAYS = 7
SAMPLE_LIMIT = 6

#: Terminal stages of the funnel. Mirrors ``QUEUE_CLOSED_STAGES`` of the
#: «Моя очередь» screen (``app/routers/candidates.py``): the router imports
#: this module, so importing the constant back would invert the dependency.
#: ``test_queue_dashboard.py::test_closed_stages_match_the_queue_contract``
#: pins the two together.
DASHBOARD_CLOSED_STAGES: tuple[CandidateStage, ...] = (
    CandidateStage.HIRED,
    CandidateStage.STARTED,
    CandidateStage.PROBATION,
    CandidateStage.FIRED,
    CandidateStage.REJECTED,
)

#: Metrics the data model cannot express: shown as «—» with this note.
UNAVAILABLE_NOTES: dict[str, str] = {
    "active_vacancies": (
        "В проекте нет сущности «вакансия»: должность кандидата — свободный текст, "
        "справочника вакансий нет. Показатель появится вместе с этой сущностью."
    ),
}

SOURCE_LABELS: dict[str, str] = {
    CandidateSource.SITE.value: "Сайт компании",
    CandidateSource.REFERRAL.value: "Рекомендация",
    CandidateSource.HH_MANUAL.value: "hh.ru (вручную)",
    CandidateSource.UNIVERSITY.value: "Учебное заведение",
    CandidateSource.EVENT.value: "Мероприятие",
    CandidateSource.AGENCY.value: "Агентство",
    CandidateSource.INBOUND_CALL.value: "Входящий звонок",
    CandidateSource.EXCEL_IMPORT.value: "Импорт Excel",
}


@dataclass(frozen=True)
class DashboardBucket:
    """One point of every series: a half-open interval ``[from, to)``."""

    key: str
    from_dt: datetime
    to_dt: datetime
    label: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "bucket": self.key,
            "from": self.from_dt,
            "to": self.to_dt,
            "label": self.label,
        }


@dataclass(frozen=True)
class DashboardWindow:
    """Resolved period: UTC instants plus the bucket axis of the timezone."""

    key: str
    from_dt: datetime
    to_dt: datetime
    timezone: ZoneInfo
    timezone_name: str
    buckets: tuple[DashboardBucket, ...]
    #: «Всё» axis cut at :data:`MAX_ALL_MONTHS`: the numbers follow the axis,
    #: and the flag lets the UI say «показаны последние 24 месяца».
    capped: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "from": self.from_dt,
            "to": self.to_dt,
            "timezone": self.timezone_name,
            "bucket_size": _bucket_size(self.key),
            "capped": self.capped,
            "buckets": [bucket.as_dict() for bucket in self.buckets],
        }


def _bucket_size(period: str) -> str:
    return {"today": "hour", "week": "day", "all": "month"}[period]


def _as_utc(value: datetime) -> datetime:
    """Normalize a column value to aware UTC.

    ``UTCDateTime`` returns aware UTC on PostgreSQL, but SQLite's DATETIME has
    no timezone and hands back a naive value (the stored string is UTC, so
    attaching ``UTC`` is the documented contract, not a conversion). Without
    this, Python-side comparisons raise and the API would serialize an
    offset-less timestamp on SQLite only.
    """
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def resolve_timezone(name: str) -> tuple[ZoneInfo, str]:
    """Validate an IANA timezone name; 422 with a readable Russian detail."""
    try:
        return ZoneInfo(name), name
    except (ZoneInfoNotFoundError, ValueError, OSError) as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"Неизвестная таймзона: {name}.",
        ) from exc


def _day_start(day: date, tz: ZoneInfo) -> datetime:
    """Midnight of ``day`` in ``tz`` as a UTC instant (DST-safe)."""
    return datetime.combine(day, time(0, 0), tzinfo=tz).astimezone(UTC)


def _month_start(year: int, month: int, tz: ZoneInfo) -> datetime:
    return _day_start(date(year, month, 1), tz)


def _add_month(year: int, month: int) -> tuple[int, int]:
    return (year + 1, 1) if month == 12 else (year, month + 1)


def resolve_window(
    *,
    period: str,
    timezone: str = DEFAULT_DASHBOARD_TIMEZONE,
    now: datetime | None = None,
    earliest: datetime | None = None,
) -> DashboardWindow:
    """Build the half-open window and its bucket axis for a period preset.

    ``today`` — from midnight to midnight of the current day (24 hourly
    buckets, so a fresh day is not an empty chart); ``week`` — seven calendar
    days ending with today; ``all`` — from the first day of the month that
    holds ``earliest`` (or 12 months back when there is no data) up to now,
    bucketed by month and capped at :data:`MAX_ALL_MONTHS` trailing buckets.
    """
    if period not in DASHBOARD_PERIODS:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"Период должен быть одним из: {', '.join(DASHBOARD_PERIODS)}.",
        )
    tz, tz_name = resolve_timezone(timezone)
    moment = (now or utc_now()).astimezone(UTC)
    today_local = moment.astimezone(tz).date()

    buckets: list[DashboardBucket] = []
    if period == "today":
        start = _day_start(today_local, tz)
        end = _day_start(today_local + timedelta(days=1), tz)
        cursor = start
        while cursor < end:
            nxt = cursor + timedelta(hours=1)
            buckets.append(
                DashboardBucket(
                    key=cursor.astimezone(tz).strftime("%Y-%m-%dT%H"),
                    from_dt=cursor,
                    to_dt=nxt,
                    label=cursor.astimezone(tz).strftime("%H:%M"),
                )
            )
            cursor = nxt
        return DashboardWindow("today", start, end, tz, tz_name, tuple(buckets))

    if period == "week":
        # Every boundary is a LOCAL midnight computed from the calendar date:
        # a plain ``+24h`` drifts through a DST transition and would show a
        # 23- or 25-hour day as 24 hours (and a shifted axis).
        first = today_local - timedelta(days=6)
        for offset in range(7):
            day = first + timedelta(days=offset)
            buckets.append(
                DashboardBucket(
                    key=day.strftime("%Y-%m-%d"),
                    from_dt=_day_start(day, tz),
                    to_dt=_day_start(day + timedelta(days=1), tz),
                    label=day.strftime("%d.%m"),
                )
            )
        return DashboardWindow(
            "week", buckets[0].from_dt, buckets[-1].to_dt, tz, tz_name, tuple(buckets)
        )

    # «Всё»: months from the first month with data (or a year back) until now.
    anchor = (earliest or (moment - timedelta(days=365))).astimezone(tz)
    year, month = anchor.year, anchor.month
    end = _day_start(today_local + timedelta(days=1), tz)
    cursor = _month_start(year, month, tz)
    while cursor < end:
        n_year, n_month = _add_month(year, month)
        nxt = _month_start(n_year, n_month, tz)
        local = cursor.astimezone(tz)
        buckets.append(
            DashboardBucket(
                key=local.strftime("%Y-%m"),
                from_dt=cursor,
                to_dt=nxt,
                label=local.strftime("%m.%Y"),
            )
        )
        year, month = n_year, n_month
        cursor = nxt
    capped = len(buckets) > MAX_ALL_MONTHS
    if capped:
        buckets = buckets[-MAX_ALL_MONTHS:]
    return DashboardWindow(
        "all", buckets[0].from_dt, end, tz, tz_name, tuple(buckets), capped=capped
    )


def _bucket_of(instant: datetime, window: DashboardWindow) -> str | None:
    """Bucket key of an instant, or ``None`` when it falls outside the axis."""
    for bucket in window.buckets:
        if bucket.from_dt <= instant < bucket.to_dt:
            return bucket.key
    return None


# --- Scope -------------------------------------------------------------------


def resolve_scope_owner(db: Session, user: User, owner_id: UUID | None) -> User:
    """Whose queue is aggregated.

    HR is always pinned to themselves; a manager/administrator may ask for a
    colleague, and an unknown id is 422 rather than a silent fallback (the
    caller must see that their filter did not apply).
    """
    if user.role == UserRole.HR or owner_id is None or owner_id == user.id:
        return user
    target = db.get(User, owner_id)
    if target is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Указанный ответственный не найден.",
        )
    return target


def _candidate_conditions(
    *,
    owner: User,
    position: str | None = None,
    stage: CandidateStage | None = None,
    source: CandidateSource | None = None,
) -> list[ColumnElement[bool]]:
    """Filters shared by every aggregate (mirrors the list screen)."""
    conditions: list[ColumnElement[bool]] = [
        Candidate.owner_user_id == owner.id,
        Candidate.deleted_at.is_(None),
    ]
    cleaned = normalize_position(position) if position is not None else ""
    if cleaned:
        conditions.append(Candidate.position_normalized == cleaned)
    if stage is not None:
        conditions.append(Candidate.stage == stage)
    if source is not None:
        conditions.append(Candidate.source == source)
    return conditions


def _candidate_ids(
    db: Session,
    *,
    owner: User,
    position: str | None = None,
    stage: CandidateStage | None = None,
    source: CandidateSource | None = None,
) -> set[UUID]:
    """Ids of every candidate in scope — the whole scope, never a page."""
    rows = db.execute(
        select(Candidate.id).where(
            *_candidate_conditions(owner=owner, position=position, stage=stage, source=source)
        )
    ).all()
    return {row[0] for row in rows}


def _facts(
    db: Session, candidate_ids: set[UUID], *, limit: int = FACT_SCAN_LIMIT
) -> list[AnalyticsFact]:
    """Facts of the scoped candidates (facts carry their own timestamps)."""
    if not candidate_ids:
        return []
    rows = (
        db.execute(
            select(AnalyticsFact)
            .where(AnalyticsFact.candidate_id.in_(candidate_ids))
            .order_by(AnalyticsFact.fact_at.asc(), AnalyticsFact.id.asc())
            .limit(limit)
        )
        .scalars()
        .all()
    )
    return list(rows)


# --- Aggregates --------------------------------------------------------------


def _task_rows(
    db: Session,
    *,
    user: User,
    window: DashboardWindow,
    now: datetime,
) -> tuple[int, int, list[Reminder], list[Reminder]]:
    """Tasks = personal reminders assigned to the caller.

    The project has no separate «task» entity: the closest real object is a
    reminder (``Reminder``) with an assignee, a due instant and a status. The
    dashboard counts **active** reminders only; completed and cancelled ones
    are history, not work.
    """
    base = [
        Reminder.assignee_user_id == user.id,
        Reminder.status == ReminderStatus.ACTIVE,
    ]
    my_tasks = int(
        db.scalar(
            select(func.count())
            .select_from(Reminder)
            .where(*base, Reminder.due_at >= window.from_dt, Reminder.due_at < window.to_dt)
        )
        or 0
    )
    overdue_total = int(
        db.scalar(select(func.count()).select_from(Reminder).where(*base, Reminder.due_at < now))
        or 0
    )
    due_rows = (
        db.execute(
            select(Reminder)
            .where(*base, Reminder.due_at >= window.from_dt, Reminder.due_at < window.to_dt)
            .order_by(Reminder.due_at.asc(), Reminder.id.asc())
            .limit(SAMPLE_LIMIT)
        )
        .scalars()
        .all()
    )
    overdue_rows = (
        db.execute(
            select(Reminder)
            .where(*base, Reminder.due_at < now)
            .order_by(Reminder.due_at.asc(), Reminder.id.asc())
            .limit(SAMPLE_LIMIT)
        )
        .scalars()
        .all()
    )
    return my_tasks, overdue_total, list(due_rows), list(overdue_rows)


def _interviews_in_window(db: Session, candidate_ids: set[UUID], window: DashboardWindow) -> int:
    if not candidate_ids:
        return 0
    return int(
        db.scalar(
            select(func.count())
            .select_from(Event)
            .where(
                Event.candidate_id.in_(candidate_ids),
                Event.type == EventType.INTERVIEW,
                Event.starts_at >= window.from_dt,
                Event.starts_at < window.to_dt,
                # Отменённое собеседование не считается проведённым.
                Event.status != EventStatus.CANCELLED,
            )
        )
        or 0
    )


def _starts_in_window(db: Session, candidate_ids: set[UUID], window: DashboardWindow) -> int:
    """Exits (planned start dates) inside the window."""
    if not candidate_ids:
        return 0
    return int(
        db.scalar(
            select(func.count())
            .select_from(Candidate)
            .where(
                Candidate.id.in_(candidate_ids),
                Candidate.start_date >= window.from_dt.date(),
                Candidate.start_date < window.to_dt.date(),
            )
        )
        or 0
    )


def _upcoming_starts(
    db: Session, candidate_ids: set[UUID], now: datetime, days: int = HORIZON_DAYS
) -> int:
    """«Выходы на неделе» — a forward-looking window, not the chart period."""
    if not candidate_ids:
        return 0
    today = now.date()
    return int(
        db.scalar(
            select(func.count())
            .select_from(Candidate)
            .where(
                Candidate.id.in_(candidate_ids),
                Candidate.start_date >= today,
                Candidate.start_date < today + timedelta(days=days),
            )
        )
        or 0
    )


def _conversion_series(
    facts: list[AnalyticsFact],
    candidate_ids: set[UUID],
    created_at: dict[UUID, datetime],
    window: DashboardWindow,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Created→interview cohort conversion, per bucket and for the period.

    Denominator: candidates created in the bucket (``Candidate.created_at``,
    so a row older than the ledger still counts). Numerator: those of them who
    later reached ``interview_scheduled`` or ``interview_done`` — a later fact,
    at any time, because a conversion that happens next month is still a
    conversion of this cohort.
    """
    reached_interview: dict[UUID, datetime] = {}
    for fact in facts:
        if fact.fact_type == AnalyticsFactType.STAGE_CHANGED and fact.stage_to in [
            stage.value for stage in INTERVIEW_STAGES
        ]:
            reached_interview.setdefault(fact.candidate_id, _as_utc(fact.fact_at))

    per_bucket: dict[str, list[int]] = {bucket.key: [0, 0] for bucket in window.buckets}
    created_ids: list[UUID] = []
    for candidate_id, at in created_at.items():
        if candidate_id not in candidate_ids:
            continue
        key = _bucket_of(at, window)
        if key is None:
            continue
        per_bucket[key][1] += 1
        created_ids.append(candidate_id)
        interview = reached_interview.get(candidate_id)
        if interview is not None and interview >= at:
            per_bucket[key][0] += 1

    numerator = sum(1 for cid in created_ids if cid in reached_interview)
    denominator = len(created_ids)
    series = [
        {
            "bucket": bucket.key,
            "numerator": per_bucket[bucket.key][0],
            "denominator": per_bucket[bucket.key][1],
            "rate": (
                round(per_bucket[bucket.key][0] / per_bucket[bucket.key][1] * 100, 1)
                if per_bucket[bucket.key][1]
                else None
            ),
        }
        for bucket in window.buckets
    ]
    total = {
        "numerator": numerator,
        "denominator": denominator,
        "rate": round(numerator / denominator * 100, 1) if denominator else None,
    }
    return series, total


def _hiring_series(
    facts: list[AnalyticsFact],
    candidate_ids: set[UUID],
    created_at: dict[UUID, datetime],
    window: DashboardWindow,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Average hiring time (creation → hired/started) per bucket and overall.

    A candidate with no known creation instant cannot produce a duration and is
    skipped rather than counted as zero — the sample size travels with the
    number so the card can say «по N нанятым».
    """
    hired_at: dict[UUID, datetime] = {}
    for fact in facts:
        if fact.fact_type == AnalyticsFactType.STAGE_CHANGED and fact.stage_to in [
            stage.value for stage in HIRED_STAGES
        ]:
            hired_at.setdefault(fact.candidate_id, _as_utc(fact.fact_at))

    buckets: dict[str, list[float]] = {bucket.key: [] for bucket in window.buckets}
    all_days: list[float] = []
    for candidate_id, at in hired_at.items():
        if candidate_id not in candidate_ids:
            continue
        start = created_at.get(candidate_id)
        if start is None:
            continue
        days = (at - start).total_seconds() / 86_400.0
        all_days.append(days)
        key = _bucket_of(at, window)
        if key is not None:
            buckets[key].append(days)

    series = [
        {
            "bucket": bucket.key,
            "value": (
                round(sum(buckets[bucket.key]) / len(buckets[bucket.key]), 1)
                if buckets[bucket.key]
                else None
            ),
            "sample": len(buckets[bucket.key]),
        }
        for bucket in window.buckets
    ]
    total = {
        "value": round(sum(all_days) / len(all_days), 1) if all_days else None,
        "sample": len(all_days),
    }
    return series, total


def _new_candidates_count(db: Session, candidate_ids: set[UUID], window: DashboardWindow) -> int:
    """Candidates created inside the window.

    A direct COUNT, not a sum over the series: the «Всё» axis is capped at
    :data:`MAX_ALL_MONTHS` buckets, and a KPI must not quietly lose the rows
    that fall outside the drawn axis.
    """
    if not candidate_ids:
        return 0
    return int(
        db.scalar(
            select(func.count())
            .select_from(Candidate)
            .where(
                Candidate.id.in_(candidate_ids),
                Candidate.created_at >= window.from_dt,
                Candidate.created_at < window.to_dt,
            )
        )
        or 0
    )


def _created_series(
    candidates: list[tuple[UUID, datetime]], window: DashboardWindow
) -> list[dict[str, Any]]:
    per_bucket: dict[str, int] = {bucket.key: 0 for bucket in window.buckets}
    for _, created in candidates:
        key = _bucket_of(created, window)
        if key is not None:
            per_bucket[key] += 1
    return [{"bucket": bucket.key, "value": per_bucket[bucket.key]} for bucket in window.buckets]


def _exits_series(
    db: Session, candidate_ids: set[UUID], window: DashboardWindow
) -> list[dict[str, Any]]:
    """Exits per bucket (planned ``start_date`` falls into the bucket)."""
    per_bucket: dict[str, int] = {bucket.key: 0 for bucket in window.buckets}
    if candidate_ids:
        rows = db.execute(
            select(Candidate.start_date).where(
                Candidate.id.in_(candidate_ids),
                Candidate.start_date.is_not(None),
            )
        ).all()
        for (start_date,) in rows:
            if start_date is None:
                continue
            instant = _day_start(start_date, window.timezone)
            key = _bucket_of(instant, window)
            if key is not None:
                per_bucket[key] += 1
    return [{"bucket": bucket.key, "exits": per_bucket[bucket.key]} for bucket in window.buckets]


def _sources_breakdown(
    db: Session, candidate_ids: set[UUID], window: DashboardWindow
) -> list[dict[str, Any]]:
    """New candidates per source in the window (rows only for real data)."""
    if not candidate_ids:
        return []
    rows = db.execute(
        select(Candidate.source, func.count(Candidate.id))
        .where(
            Candidate.id.in_(candidate_ids),
            Candidate.created_at >= window.from_dt,
            Candidate.created_at < window.to_dt,
        )
        .group_by(Candidate.source)
    ).all()
    known = {str(source.value): source.value for source in CandidateSource}
    out: list[dict[str, Any]] = []
    for source, count in rows:
        key = str(source.value) if hasattr(source, "value") else str(source)
        out.append(
            {
                "source": known.get(key, key),
                "label": SOURCE_LABELS.get(known.get(key, key), key),
                "count": int(count),
            }
        )
    out.sort(key=lambda row: (-int(row["count"]), str(row["source"])))
    return out


def _funnel(
    db: Session,
    *,
    owner: User,
    position: str | None = None,
    stage: CandidateStage | None = None,
    source: CandidateSource | None = None,
) -> list[dict[str, Any]]:
    """Current stage distribution: same numbers as the queue mini-funnel."""
    rows = db.execute(
        select(Candidate.stage, func.count(Candidate.id))
        .where(*_candidate_conditions(owner=owner, position=position, stage=stage, source=source))
        .group_by(Candidate.stage)
    ).all()
    counts = {stage: int(count) for stage, count in rows}
    return [
        {"stage": stage.value, "count": counts.get(stage, 0)} for stage in CANDIDATE_STAGE_ORDER
    ]


def _stuck_sample(
    db: Session,
    *,
    owner: User,
    position: str | None,
    stage: CandidateStage | None,
    source: CandidateSource | None,
    now: datetime,
) -> tuple[int, list[Candidate]]:
    conditions = _candidate_conditions(owner=owner, position=position, stage=stage, source=source)
    conditions.append(Candidate.stage.notin_(DASHBOARD_CLOSED_STAGES))
    conditions.append(Candidate.updated_at < now - timedelta(days=STUCK_DAYS))
    total = int(db.scalar(select(func.count()).select_from(Candidate).where(*conditions)) or 0)
    rows = (
        db.execute(
            select(Candidate)
            .where(*conditions)
            .order_by(Candidate.updated_at.asc(), Candidate.id.asc())
            .limit(SAMPLE_LIMIT)
        )
        .scalars()
        .all()
    )
    return total, list(rows)


def _upcoming_events(
    db: Session,
    *,
    owner: User,
    position: str | None,
    stage: CandidateStage | None,
    source: CandidateSource | None,
    now: datetime,
    days: int = HORIZON_DAYS,
) -> tuple[int, list[Event]]:
    # «Ближайшие события» — окно вперёд от сегодняшнего дня в UTC (то же
    # правило, что и у сводки очереди: сервер не угадывает локальное время).
    today = now.astimezone(UTC).date()
    conditions = [
        *_candidate_conditions(owner=owner, position=position, stage=stage, source=source),
        Event.starts_at >= _day_start(today, UTC_ZONE),
        Event.starts_at < _day_start(today + timedelta(days=days), UTC_ZONE),
        # Завершённое или отменённое событие не «требует действия».
        Event.status.notin_([EventStatus.COMPLETED, EventStatus.CANCELLED]),
    ]
    stmt = select(Event).join(Candidate, Event.candidate_id == Candidate.id).where(*conditions)
    total = int(db.scalar(select(func.count()).select_from(stmt.subquery())) or 0)
    rows = (
        db.execute(stmt.order_by(Event.starts_at.asc(), Event.id.asc()).limit(SAMPLE_LIMIT))
        .scalars()
        .all()
    )
    return total, list(rows)


def _unread(db: Session, user: User) -> int:
    return int(
        db.scalar(
            select(func.count())
            .select_from(Notification)
            .where(
                Notification.user_id == user.id,
                Notification.read_at.is_(None),
                Notification.dismissed_at.is_(None),
            )
        )
        or 0
    )


def _reminder_card(row: Reminder) -> dict[str, Any]:
    return {
        "id": row.id,
        "title": row.title,
        "due_at": _as_utc(row.due_at),
        "importance": row.importance.value,
        "status": row.status.value,
        "candidate_id": row.candidate_id,
        "event_id": row.event_id,
    }


def _event_card(row: Event) -> dict[str, Any]:
    return {
        "id": row.id,
        "candidate_id": row.candidate_id,
        "candidate_full_name": row.candidate.full_name if row.candidate is not None else "",
        "type": row.type.value,
        "title": row.title,
        "status": row.status.value,
        "starts_at": _as_utc(row.starts_at),
        "ends_at": _as_utc(row.ends_at) if row.ends_at is not None else None,
    }


def _stuck_card(row: Candidate) -> dict[str, Any]:
    return {
        "id": row.id,
        "full_name": row.full_name,
        "position": row.position,
        "stage": row.stage.value,
        "updated_at": _as_utc(row.updated_at),
    }


# --- Entry point -------------------------------------------------------------


def build_dashboard(
    db: Session,
    *,
    user: User,
    period: str = "week",
    timezone: str = DEFAULT_DASHBOARD_TIMEZONE,
    owner_id: UUID | None = None,
    position: str | None = None,
    stage: CandidateStage | None = None,
    source: CandidateSource | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Every number of the «Моя очередь» dashboard, computed server-side."""
    moment = (now or utc_now()).astimezone(UTC)
    owner = resolve_scope_owner(db, user, owner_id)
    # «Всё» builds its axis from the first candidate, so the window needs the
    # scope first — the resolution order matters, not the other way round.
    scoped_ids = _candidate_ids(db, owner=owner, position=position, stage=stage, source=source)
    earliest: datetime | None = None
    if period == "all" and scoped_ids:
        earliest = db.scalar(
            select(func.min(Candidate.created_at)).where(Candidate.id.in_(scoped_ids))
        )
    window = resolve_window(period=period, timezone=timezone, now=moment, earliest=earliest)

    candidates_rows = (
        db.execute(
            select(Candidate.id, Candidate.created_at).where(Candidate.id.in_(scoped_ids))
        ).all()
        if scoped_ids
        else []
    )
    candidates = [(row[0], _as_utc(row[1])) for row in candidates_rows]
    facts = _facts(db, scoped_ids)
    truncated = len(facts) >= FACT_SCAN_LIMIT

    created_map = {candidate_id: _as_utc(created) for candidate_id, created in candidates}
    for fact in facts:
        if fact.fact_type == AnalyticsFactType.CANDIDATE_CREATED:
            created_map.setdefault(fact.candidate_id, _as_utc(fact.fact_at))
    created_series = _created_series(candidates, window)
    conversion_series, conversion_total = _conversion_series(facts, scoped_ids, created_map, window)
    hiring_series, hiring_total = _hiring_series(facts, scoped_ids, created_map, window)
    exits_series = _exits_series(db, scoped_ids, window)
    hiring_dynamics = [
        {
            "bucket": bucket.key,
            "exits": exits_series[index]["exits"],
            "hired": hiring_series[index]["sample"],
            "avg_hiring_days": hiring_series[index]["value"],
        }
        for index, bucket in enumerate(window.buckets)
    ]

    my_tasks, overdue_tasks, due_rows, overdue_rows = _task_rows(
        db, user=user, window=window, now=moment
    )
    new_candidates = _new_candidates_count(db, scoped_ids, window)
    # Фильтры действуют на весь экран, включая мини-воронку: иначе воронка и
    # KPI показывают разные множества кандидатов.
    funnel = _funnel(db, owner=owner, position=position, stage=stage, source=source)
    closed_stage_values = {stage.value for stage in DASHBOARD_CLOSED_STAGES}
    in_work = sum(row["count"] for row in funnel if row["stage"] not in closed_stage_values)
    stuck_total, stuck_rows = _stuck_sample(
        db, owner=owner, position=position, stage=stage, source=source, now=moment
    )
    events_total, event_rows = _upcoming_events(
        db, owner=owner, position=position, stage=stage, source=source, now=moment
    )

    return {
        "scope": {
            "owner_id": owner.id,
            "owner_username": owner.username,
            "personal": owner.id == user.id,
            "role": user.role.value,
        },
        "period": window.as_dict(),
        "generated_at": moment,
        "filters": {
            "position": position or None,
            "stage": stage.value if stage is not None else None,
            "source": source.value if source is not None else None,
        },
        "kpis": {
            # «В работе» осталось на экране: мини-воронка показывает состав,
            # эти два числа — размер очереди целиком (считает сервер).
            "total_candidates": len(scoped_ids),
            "in_work": in_work,
            "my_tasks": my_tasks,
            "overdue_tasks": overdue_tasks,
            "new_candidates": new_candidates,
            "interviews": _interviews_in_window(db, scoped_ids, window),
            "interview_conversion": conversion_total,
            "average_hiring_days": hiring_total,
            # Нет сущности «вакансия» — честно отдаём null, а не 0 и не демо-число.
            "active_vacancies": None,
            "weekly_exits": _upcoming_starts(db, scoped_ids, moment),
        },
        "kpi_notes": dict(UNAVAILABLE_NOTES),
        "created_candidates_series": created_series,
        "interview_conversion_series": conversion_series,
        "average_hiring_days_series": hiring_series,
        "hiring_dynamics": hiring_dynamics,
        "sources": _sources_breakdown(db, scoped_ids, window),
        "funnel": funnel,
        "attention_candidates": [_stuck_card(row) for row in stuck_rows],
        "attention_candidates_total": stuck_total,
        "attention_truncated": stuck_total > len(stuck_rows),
        "upcoming_events": [_event_card(row) for row in event_rows],
        "upcoming_events_total": events_total,
        "upcoming_events_truncated": events_total > len(event_rows),
        "tasks": {
            "due": [_reminder_card(row) for row in due_rows],
            "overdue": [_reminder_card(row) for row in overdue_rows],
            "total": my_tasks,
            "overdue_total": overdue_tasks,
        },
        "unread_notifications": _unread(db, user),
        "truncated": truncated,
    }
