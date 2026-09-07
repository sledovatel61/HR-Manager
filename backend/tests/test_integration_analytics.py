"""Integration tests for analytics against a real PostgreSQL.

Run with::

    TEST_DATABASE_URL=postgresql+psycopg://user:pass@host:5432/db \\
        pytest -m integration -v

Covers what SQLite cannot: timestamptz normalization (UTC / Europe-Moscow /
DST boundaries), the half-open [from, to) period semantics on real
timestamptz columns, the migration backfill into the facts ledger, the
single-transaction rollback contract, and duplicate-fact protection under
real unique indexes and concurrency.
"""

import os
import subprocess
import sys
import threading
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import quote
from uuid import uuid4

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.analytics import build_kpi_report, resolve_period_query
from app.analytics_ledger import record_fact, record_fact_idempotent
from app.models import (
    AnalyticsFact,
    AnalyticsFactType,
    AuditAction,
    AuditEvent,
    Candidate,
    CandidateInteraction,
    CandidateSource,
    CandidateTransfer,
    Event,
    EventStatus,
    EventType,
    UserRole,
)
from app.routers.auth import reset_login_limiter
from app.utils import utc_now
from tests.conftest import FIXTURE_PASSWORD, make_candidate, make_user

pytestmark = pytest.mark.integration

BACKEND_DIR = Path(__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def _clean_limiter() -> Iterator[None]:
    reset_login_limiter()
    yield
    reset_login_limiter()


def _login(client: TestClient, username: str, password: str = FIXTURE_PASSWORD) -> httpx.Response:
    return client.post("/auth/login", json={"username": username, "password": password})


def _csrf(response: httpx.Response) -> str:
    return response.json()["csrf_token"]


def _q(value: str) -> str:
    return quote(value, safe="")


def _run_alembic(*args: str, url: str) -> None:
    subprocess.run(
        [sys.executable, "-m", "alembic", *args],
        cwd=BACKEND_DIR,
        env={**os.environ, "DATABASE_URL": url, "APP_ENV": "test"},
        check=True,
        capture_output=True,
        text=True,
    )


def _kpi(pg_client: TestClient, from_: str, to: str, **params: str) -> dict:
    url = f"/analytics/kpi?from={_q(from_)}&to={_q(to)}"
    for key, value in params.items():
        url += f"&{key}={_q(value)}"
    response = pg_client.get(url)
    assert response.status_code == 200, response.text
    return response.json()


# --- Period/timezone semantics on PostgreSQL ----------------------------------


def test_half_open_boundaries_on_timestamptz(pg_client: TestClient, pg_db: Session) -> None:
    """A fact exactly at ``from`` counts; a fact exactly at ``to`` does not."""
    make_user(pg_db, username="manager", role=UserRole.MANAGER)
    hr = make_user(pg_db, username="hr1", role=UserRole.HR)
    c1 = make_candidate(pg_db, owner=hr)
    c2 = make_candidate(pg_db, owner=hr)
    boundary = datetime(2026, 1, 15, 12, 0, tzinfo=UTC)
    record_fact(
        pg_db,
        fact_type=AnalyticsFactType.CANDIDATE_CREATED,
        candidate_id=c1.id,
        owner_user_id=hr.id,
        fact_at=boundary,
        source="site",
    )
    record_fact(
        pg_db,
        fact_type=AnalyticsFactType.CANDIDATE_CREATED,
        candidate_id=c2.id,
        owner_user_id=hr.id,
        fact_at=boundary + timedelta(days=1),
        source="site",
    )
    pg_db.commit()
    _login(pg_client, "manager")

    frm = "2026-01-15T12:00:00+00:00"
    to = "2026-01-16T12:00:00+00:00"
    body = _kpi(pg_client, frm, to)
    assert body["kpis"]["created_candidates"] == 1  # only the exact-from fact

    # A microsecond after ``from`` counts; a microsecond before ``to`` counts.
    to2 = "2026-01-16T12:00:00.000001+00:00"
    body = _kpi(pg_client, frm, to2)
    assert body["kpis"]["created_candidates"] == 2


def test_moscow_offset_normalization(pg_client: TestClient, pg_db: Session) -> None:
    """Facts stored as UTC instants; +03:00 boundaries normalize correctly."""
    make_user(pg_db, username="manager", role=UserRole.MANAGER)
    hr = make_user(pg_db, username="hr1", role=UserRole.HR)
    c = make_candidate(pg_db, owner=hr)
    # 2026-01-10 12:00 UTC == 15:00 Europe/Moscow (fixed UTC+3, no DST).
    record_fact(
        pg_db,
        fact_type=AnalyticsFactType.CANDIDATE_CREATED,
        candidate_id=c.id,
        owner_user_id=hr.id,
        fact_at=datetime(2026, 1, 10, 12, 0, tzinfo=UTC),
        source="site",
    )
    pg_db.commit()
    _login(pg_client, "manager")

    body = _kpi(
        pg_client,
        "2026-01-10T00:00:00+03:00",
        "2026-01-11T00:00:00+03:00",
        timezone="Europe/Moscow",
    )
    assert body["kpis"]["created_candidates"] == 1
    assert body["period"]["timezone"] == "Europe/Moscow"
    assert datetime.fromisoformat(body["period"]["from"]).astimezone(UTC) == datetime(
        2026, 1, 9, 21, 0, tzinfo=UTC
    )


def test_dst_transition_boundaries_berlin(pg_client: TestClient, pg_db: Session) -> None:
    """Offset-bearing boundaries around a DST change normalize to UTC.

    Europe/Berlin spring-forward 2026-03-29: 02:00 CET -> 03:00 CEST.
    """
    make_user(pg_db, username="manager", role=UserRole.MANAGER)
    hr = make_user(pg_db, username="hr1", role=UserRole.HR)
    c = make_candidate(pg_db, owner=hr)
    record_fact(
        pg_db,
        fact_type=AnalyticsFactType.CANDIDATE_CREATED,
        candidate_id=c.id,
        owner_user_id=hr.id,
        fact_at=datetime(2026, 3, 29, 1, 0, tzinfo=UTC),
        source="site",
    )
    pg_db.commit()
    _login(pg_client, "manager")

    # 2026-03-29T02:30+02:00 == 00:30Z; 03:30+02:00 == 01:30Z -> 01:00Z inside.
    body = _kpi(
        pg_client,
        "2026-03-29T02:30:00+02:00",
        "2026-03-29T03:30:00+02:00",
        timezone="Europe/Berlin",
    )
    assert body["kpis"]["created_candidates"] == 1

    # A window starting right after the fact excludes it.
    body = _kpi(
        pg_client,
        "2026-03-29T03:30:00+02:00",
        "2026-03-29T04:30:00+02:00",
        timezone="Europe/Berlin",
    )
    assert body["kpis"]["created_candidates"] == 0


def test_period_query_rejects_naive_machine_local_assumptions(pg_db: Session) -> None:
    """Naive timestamps are interpreted as UTC (documented contract) — never
    silently as machine-local time."""
    make_user(pg_db, username=f"hr1-{uuid4().hex[:8]}", role=UserRole.HR)
    pq = resolve_period_query(
        pg_db,
        from_dt=datetime(2026, 1, 1, 0, 0),
        to_dt=datetime(2026, 1, 2, 0, 0),
        timezone="UTC",
        hr_id=None,
        source=None,
    )
    assert pq.from_dt == datetime(2026, 1, 1, 0, 0, tzinfo=UTC)


# --- Backfill ------------------------------------------------------------------


def test_migration_backfills_facts_from_history(
    integration_url: str, pg_db: Session, pg_engine: Engine
) -> None:
    """Downgrade to 0005, plant real business history, upgrade: the ledger
    must reconstruct every fact without fabricating anything."""
    url = integration_url
    try:
        _run_alembic("downgrade", "0005", url=url)

        with pg_engine.begin() as connection:
            connection.execute(
                text(
                    "TRUNCATE TABLE audit_log, event_history, events, "
                    "candidate_transfers, candidate_interactions, candidates, "
                    "user_sessions, users RESTART IDENTITY CASCADE"
                )
            )

        t0 = datetime(2026, 2, 1, 9, 0, tzinfo=UTC)
        hr1 = make_user(pg_db, username="anna", role=UserRole.HR)
        hr2 = make_user(pg_db, username="bob", role=UserRole.HR)
        manager = make_user(pg_db, username="mgr", role=UserRole.MANAGER)

        candidate = make_candidate(pg_db, owner=hr1, source=CandidateSource.REFERRAL)
        pg_db.execute(
            text("UPDATE candidates SET created_at = :t WHERE id = :id"),
            {"t": t0, "id": candidate.id},
        )

        interaction = CandidateInteraction(
            candidate_id=candidate.id,
            author_user_id=hr1.id,
            type="call",
            comment="звонок",
            created_at=t0 + timedelta(hours=1),
        )
        pg_db.add(interaction)

        transfer = CandidateTransfer(
            candidate_id=candidate.id,
            initiator_user_id=manager.id,
            from_user_id=hr1.id,
            to_user_id=hr2.id,
            reason="нагрузка",
            created_at=t0 + timedelta(hours=2),
        )
        pg_db.add(transfer)

        event = Event(
            candidate_id=candidate.id,
            author_user_id=hr1.id,
            assignee_user_id=hr2.id,
            type=EventType.INTERVIEW,
            title="Интервью",
            status=EventStatus.COMPLETED,
            starts_at=t0 + timedelta(hours=3),
            ends_at=None,
            remind_at=None,
            completed_at=t0 + timedelta(hours=4),
            created_at=t0 + timedelta(hours=2, minutes=30),
            version=1,
        )
        pg_db.add(event)

        # Historical stage transitions live only in the audit log.
        pg_db.add(
            AuditEvent(
                action=AuditAction.CANDIDATE_STAGE_CHANGED,
                actor_user_id=hr1.id,
                candidate_id=candidate.id,
                details="new -> contacted",
                created_at=t0 + timedelta(minutes=30),
            )
        )
        pg_db.add(
            AuditEvent(
                action=AuditAction.CANDIDATE_STAGE_CHANGED,
                actor_user_id=hr2.id,
                candidate_id=candidate.id,
                details="contacted -> offer",
                created_at=t0 + timedelta(hours=5),
            )
        )
        pg_db.commit()
        pg_db.expire_all()

        _run_alembic("upgrade", "head", url=url)
        pg_db.expire_all()

        facts = pg_db.scalars(select(AnalyticsFact).order_by(AnalyticsFact.fact_at)).all()
        kinds = [(f.fact_type.value, f.fact_at) for f in facts]
        assert [k for k, _ in kinds] == [
            "candidate_created",  # 09:00
            "stage_changed",  # 09:30
            "interaction_added",  # 10:00
            "transfer",  # 11:00
            "event_created",  # 11:30
            "event_completed",  # 13:00
            "stage_changed",  # 14:00
        ]
        created = next(f for f in facts if f.fact_type == AnalyticsFactType.CANDIDATE_CREATED)
        assert created.fact_at == t0
        assert created.source == "referral"
        assert created.owner_user_id == hr1.id

        # The transfer fact belongs to the NEW owner (fact-time owner).
        transfer_fact = next(f for f in facts if f.fact_type == AnalyticsFactType.TRANSFER)
        assert transfer_fact.owner_user_id == hr2.id

        # The post-transfer offer fact is attributed to hr2 (owner at the time).
        offer_fact = next(
            f
            for f in facts
            if f.fact_type == AnalyticsFactType.STAGE_CHANGED and f.stage_to == "offer"
        )
        assert offer_fact.owner_user_id == hr2.id

        # Metrics over the backfilled history.
        pq = resolve_period_query(
            pg_db,
            from_dt=t0 - timedelta(days=1),
            to_dt=t0 + timedelta(days=1),
            timezone="UTC",
            hr_id=None,
            source=None,
        )
        report = build_kpi_report(pg_db, pq)
        assert report["kpis"]["created_candidates"] == 1
        assert report["kpis"]["calls"] == 1
        assert report["kpis"]["offers"] == 1
        assert report["kpis"]["interviews_scheduled"] == 1
        assert report["kpis"]["interviews_done"] == 1
        assert report["kpis"]["processed_candidates"] == 1
        by_hr = {row["username"]: row for row in report["by_hr"]}
        assert by_hr["anna"]["created"] == 1
        assert by_hr["anna"]["processed"] == 1  # pre-transfer facts
        assert by_hr["bob"]["processed"] == 1  # post-transfer facts
        assert by_hr["bob"]["created"] == 0
    finally:
        _run_alembic("upgrade", "head", url=url)


# --- Transactional atomicity and idempotency on PostgreSQL ---------------------


def test_create_rolls_back_when_audit_fails_on_postgres(
    pg_client: TestClient, pg_db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.audit import record_event as real_record_event
    from app.routers import candidates as candidates_router

    make_user(pg_db, username="hr1", role=UserRole.HR)
    csrf = _csrf(_login(pg_client, "hr1"))

    def failing_record_event(db: Session, *args: Any, **kwargs: Any) -> None:
        real_record_event(db, *args, **kwargs)
        db.flush()
        raise RuntimeError("simulated audit write failure")

    monkeypatch.setattr(candidates_router, "record_event", failing_record_event)

    with pytest.raises(RuntimeError, match="simulated audit write failure"):
        pg_client.post(
            "/candidates",
            json={"full_name": "Не появится", "source": "site", "position": ""},
            headers={"X-CSRF-Token": csrf},
        )

    pg_db.expire_all()
    assert pg_db.scalar(select(func.count()).select_from(Candidate)) == 0
    assert pg_db.scalar(select(func.count()).select_from(AnalyticsFact)) == 0
    assert (
        pg_db.scalar(
            select(func.count())
            .select_from(AuditEvent)
            .where(AuditEvent.action == AuditAction.CANDIDATE_CREATED)
        )
        == 0
    )


def test_duplicate_fact_protection_under_concurrency(pg_engine: Engine, pg_db: Session) -> None:
    """Two threads inserting the SAME fact: exactly one row survives."""
    hr = make_user(pg_db, username=f"hr1-{uuid4().hex[:8]}", role=UserRole.HR)
    candidate = make_candidate(pg_db, owner=hr)
    t0 = datetime(2026, 3, 1, 12, 0, tzinfo=UTC)

    outcomes: list[str] = []
    barrier = threading.Barrier(2)

    def insert_fact() -> None:
        with Session(pg_engine) as session:
            barrier.wait()
            try:
                record_fact(
                    session,
                    fact_type=AnalyticsFactType.CANDIDATE_CREATED,
                    candidate_id=candidate.id,
                    owner_user_id=hr.id,
                    fact_at=t0,
                    source="site",
                )
                session.commit()
                outcomes.append("ok")
            except IntegrityError:
                session.rollback()
                outcomes.append("integrity_error")
            except Exception as exc:
                session.rollback()
                outcomes.append(f"error:{exc}")

    threads = [threading.Thread(target=insert_fact) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert sorted(outcomes) == ["integrity_error", "ok"]
    pg_db.expire_all()
    count = pg_db.scalar(
        select(func.count())
        .select_from(AnalyticsFact)
        .where(
            AnalyticsFact.fact_type == AnalyticsFactType.CANDIDATE_CREATED,
            AnalyticsFact.candidate_id == candidate.id,
        )
    )
    assert count == 1


def test_record_fact_idempotent_keeps_single_row(pg_db: Session) -> None:
    hr = make_user(pg_db, username=f"hr1-{uuid4().hex[:8]}", role=UserRole.HR)
    candidate = make_candidate(pg_db, owner=hr)
    t0 = utc_now()

    first = record_fact_idempotent(
        pg_db,
        fact_type=AnalyticsFactType.CANDIDATE_CREATED,
        candidate_id=candidate.id,
        owner_user_id=hr.id,
        fact_at=t0,
        source="site",
    )
    second = record_fact_idempotent(
        pg_db,
        fact_type=AnalyticsFactType.CANDIDATE_CREATED,
        candidate_id=candidate.id,
        owner_user_id=hr.id,
        fact_at=t0,
        source="site",
    )
    pg_db.commit()
    assert first is not None
    assert second is None
    count = pg_db.scalar(
        select(func.count())
        .select_from(AnalyticsFact)
        .where(
            AnalyticsFact.fact_type == AnalyticsFactType.CANDIDATE_CREATED,
            AnalyticsFact.candidate_id == candidate.id,
        )
    )
    assert count == 1


# --- End-to-end report through the API on PostgreSQL ----------------------------


def test_full_lifecycle_kpi_via_api(pg_client: TestClient, pg_db: Session) -> None:
    """HR creates -> manager advances through the funnel -> termination;
    the report aggregates everything with correct attribution."""
    make_user(pg_db, username="manager", role=UserRole.MANAGER)
    make_user(pg_db, username="hr1", role=UserRole.HR)

    csrf = _csrf(_login(pg_client, "hr1"))
    created = pg_client.post(
        "/candidates",
        json={"full_name": "Жизненный цикл", "source": "site", "position": "Dev"},
        headers={"X-CSRF-Token": csrf},
    )
    assert created.status_code == 201
    candidate_id = created.json()["id"]

    csrf = _csrf(_login(pg_client, "manager"))
    for stage in (
        "contacted",
        "reached",
        "interview_scheduled",
        "interview_done",
        "offer",
        "hired",
    ):
        assert (
            pg_client.patch(
                f"/candidates/{candidate_id}",
                json={"stage": stage},
                headers={"X-CSRF-Token": csrf},
            ).status_code
            == 200
        )

    terminated = pg_client.post(
        f"/candidates/{candidate_id}/termination",
        json={"terminated_at": utc_now().isoformat(), "reason": "Ушёл к конкуренту"},
        headers={"X-CSRF-Token": csrf},
    )
    assert terminated.status_code == 201

    frm = (utc_now() - timedelta(hours=2)).isoformat()
    to = (utc_now() + timedelta(hours=1)).isoformat()
    body = _kpi(pg_client, frm, to)
    kpis = body["kpis"]
    assert kpis["created_candidates"] == 1
    assert kpis["reached"] == 1
    assert kpis["offers"] == 1
    assert kpis["hired"] == 1
    assert kpis["terminated"] == 1
    assert kpis["dismissed"] == 0
    assert kpis["processed_candidates"] == 1

    funnel = pg_client.get(f"/analytics/funnel?from={_q(frm)}&to={_q(to)}").json()
    reached = {s["stage"]: s["reached"] for s in funnel["stages"]}
    assert reached == {
        "new": 1,
        "contacted": 1,
        "reached": 1,
        "interview_scheduled": 1,
        "interview_done": 1,
        "offer": 1,
        "hired": 1,
        "started": 0,
        "probation": 0,
    }
    conversions = {c["from_stage"]: c for c in funnel["conversions"]}
    assert conversions["new"]["rate"] == 100.0
    assert conversions["hired"]["numerator"] == 0
    assert conversions["hired"]["rate"] == 0.0

    by_hr = {row["username"]: row for row in body["by_hr"]}
    assert by_hr["hr1"]["hired"] == 1
    assert by_hr["hr1"]["terminated"] == 1


def test_export_filename_uses_requested_timezone(pg_client: TestClient, pg_db: Session) -> None:
    make_user(pg_db, username="manager", role=UserRole.MANAGER)
    _login(pg_client, "manager")
    # UTC midnight on Jan 1 == 03:00 MSK on Jan 1; the *day* in Moscow for the
    # ``to`` instant differs from UTC when the boundary is near midnight.
    response = pg_client.get(
        "/analytics/export?format=csv"
        "&from=2026-01-01T00%3A00%3A00%2B00%3A00"
        "&to=2026-02-01T00%3A00%3A00%2B00%3A00"
        "&timezone=Europe%2FMoscow"
    )
    assert response.status_code == 200
    assert response.headers["content-disposition"] == (
        'attachment; filename="analytics-2026-01-01-2026-02-01.csv"'
    )
    # 23:00 UTC Feb 1 -> Feb 2 in Moscow: the filename must use Moscow days.
    response = pg_client.get(
        "/analytics/export?format=csv"
        "&from=2026-01-01T00%3A00%3A00%2B00%3A00"
        "&to=2026-02-01T23%3A00%3A00%2B00%3A00"
        "&timezone=Europe%2FMoscow"
    )
    assert response.status_code == 200
    assert response.headers["content-disposition"] == (
        'attachment; filename="analytics-2026-01-01-2026-02-02.csv"'
    )
