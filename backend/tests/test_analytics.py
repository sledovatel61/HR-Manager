"""Unit tests for analytics endpoints (in-memory SQLite).

Covers RBAC (401/403), period validation (422), the ten KPIs, funnel
counts and cohort conversions (including null rate, repeated transitions
and transfers), by_source/by_hr slices, terminations (new business entity),
CSV export layout/BOM/escaping/formula-injection neutralization, export
auditing without content, atomic rollback when a ledger write fails, and
the OpenAPI contract. PostgreSQL integration mirrors (real timezone/DST
semantics, backfill, concurrency) live in ``tests/test_integration_analytics.py``.
"""

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import quote
from uuid import UUID, uuid4

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.analytics import _csv_field
from app.analytics_ledger import record_fact
from app.models import (
    AnalyticsFact,
    AnalyticsFactType,
    AuditAction,
    AuditEvent,
    Candidate,
    CandidateSource,
    User,
    UserRole,
)
from app.routers.auth import reset_login_limiter
from app.utils import utc_now
from tests.conftest import FIXTURE_PASSWORD, make_candidate, make_user


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
    """Percent-encode an ISO timestamp for query strings ('+' breaks URLs)."""
    return quote(value, safe="")


def _window() -> tuple[str, str]:
    """A [now-1h, now+1h) window covering API-created facts."""
    now = utc_now()
    return (now - timedelta(hours=1)).isoformat(), (now + timedelta(hours=1)).isoformat()


def _kpi_url(from_: str, to: str, **params: str) -> str:
    base = f"/analytics/kpi?from={_q(from_)}&to={_q(to)}"
    for key, value in params.items():
        base += f"&{key}={_q(value)}"
    return base


def _make_manager_admin(db: Session) -> tuple[User, User]:
    return make_user(db, username="manager", role=UserRole.MANAGER), make_user(
        db, username="admin", role=UserRole.ADMIN
    )


# --- RBAC --------------------------------------------------------------------


def test_analytics_requires_authentication(client: TestClient) -> None:
    frm, to = _window()
    for path in (
        f"/analytics/kpi?from={_q(frm)}&to={_q(to)}",
        f"/analytics/funnel?from={_q(frm)}&to={_q(to)}",
        f"/analytics/export?format=csv&from={_q(frm)}&to={_q(to)}",
    ):
        response = client.get(path)
        assert response.status_code == 401
        assert response.json()["detail"]  # existing error format


def test_hr_gets_403_on_every_analytics_endpoint(client: TestClient, db_session: Session) -> None:
    make_user(db_session, username="hr1", role=UserRole.HR)
    _login(client, "hr1")
    frm, to = _window()
    for path in (
        f"/analytics/kpi?from={_q(frm)}&to={_q(to)}",
        f"/analytics/funnel?from={_q(frm)}&to={_q(to)}",
        f"/analytics/export?format=csv&from={_q(frm)}&to={_q(to)}",
    ):
        response = client.get(path)
        assert response.status_code == 403
        assert "аналитик" in response.json()["detail"]


def test_manager_and_admin_are_allowed(client: TestClient, db_session: Session) -> None:
    _make_manager_admin(db_session)
    frm, to = _window()
    for username in ("manager", "admin"):
        _login(client, username)
        response = client.get(f"/analytics/kpi?from={_q(frm)}&to={_q(to)}")
        assert response.status_code == 200
        assert response.json()["scope"] == "team"


# --- Period/filter validation -------------------------------------------------


def test_period_validation_errors(client: TestClient, db_session: Session) -> None:
    _make_manager_admin(db_session)
    _login(client, "manager")

    base = utc_now().replace(microsecond=0)
    a = base.isoformat()
    b = (base + timedelta(days=30)).isoformat()

    # from >= to
    assert client.get(f"/analytics/kpi?from={_q(b)}&to={_q(a)}").status_code == 422
    # from == to
    assert client.get(f"/analytics/kpi?from={_q(a)}&to={_q(a)}").status_code == 422
    # span > 366 days
    long_span = (base + timedelta(days=367)).isoformat()
    assert client.get(f"/analytics/kpi?from={_q(a)}&to={_q(long_span)}").status_code == 422
    # unknown timezone
    response = client.get(f"/analytics/kpi?from={_q(a)}&to={_q(b)}&timezone=Mars%2FBase")
    assert response.status_code == 422
    assert "таймзона" in response.json()["detail"].lower()
    # hr_id points at a non-HR (manager) user
    manager = (
        db_session.query(__import__("app.models", fromlist=["User"]).User)
        .filter_by(username="manager")
        .one()
    )
    manager = db_session.query(User).filter_by(username="manager").one()
    assert (
        client.get(f"/analytics/kpi?from={_q(a)}&to={_q(b)}&hr_id={manager.id}").status_code == 422
    )
    # hr_id points at a missing user
    assert client.get(f"/analytics/kpi?from={_q(a)}&to={_q(b)}&hr_id={uuid4()}").status_code == 422
    # invalid source
    assert client.get(f"/analytics/kpi?from={_q(a)}&to={_q(b)}&source=tg").status_code == 422
    # export without format
    assert client.get(f"/analytics/export?from={_q(a)}&to={_q(b)}").status_code == 422
    # export with an unsupported format
    response = client.get(f"/analytics/export?format=xlsx&from={_q(a)}&to={_q(b)}")
    assert response.status_code == 422
    assert "csv" in response.json()["detail"]


def test_period_and_filters_are_echoed(client: TestClient, db_session: Session) -> None:
    _make_manager_admin(db_session)
    hr = make_user(db_session, username="hr1", role=UserRole.HR)
    _login(client, "manager")

    start = datetime(2026, 1, 1, 0, 0, tzinfo=UTC)
    end = datetime(2026, 2, 1, 0, 0, tzinfo=UTC)
    response = client.get(
        f"/analytics/kpi?from={_q(start.isoformat())}&to={_q(end.isoformat())}"
        f"&timezone=Europe%2FMoscow&hr_id={hr.id}&source=site"
    )
    assert response.status_code == 200
    body = response.json()
    assert datetime.fromisoformat(body["period"]["from"]) == start
    assert datetime.fromisoformat(body["period"]["to"]) == end
    assert body["period"]["timezone"] == "Europe/Moscow"
    assert body["filters"] == {"hr_id": str(hr.id), "source": "site"}


# --- KPI / funnel / conversions ----------------------------------------------


def _create_team_facts(db: Session) -> tuple[User, User, Candidate, Candidate, datetime]:
    """Seed facts directly for deterministic metrics (times are explicit)."""
    hr1 = make_user(db, username="alice", role=UserRole.HR)
    hr2 = make_user(db, username="bob", role=UserRole.HR)
    c1 = make_candidate(db, owner=hr1, source=CandidateSource.SITE)
    c2 = make_candidate(db, owner=hr2, source=CandidateSource.REFERRAL)
    now = utc_now()

    record_fact(
        db,
        fact_type=AnalyticsFactType.CANDIDATE_CREATED,
        candidate_id=c1.id,
        owner_user_id=hr1.id,
        fact_at=now - timedelta(hours=10),
        source="site",
    )
    record_fact(
        db,
        fact_type=AnalyticsFactType.STAGE_CHANGED,
        candidate_id=c1.id,
        owner_user_id=hr1.id,
        fact_at=now - timedelta(hours=9),
        stage_from="new",
        stage_to="contacted",
        source="site",
    )
    record_fact(
        db,
        fact_type=AnalyticsFactType.INTERACTION_ADDED,
        candidate_id=c1.id,
        owner_user_id=hr1.id,
        fact_at=now - timedelta(hours=8),
        fact_subtype="call",
        source="site",
    )
    record_fact(
        db,
        fact_type=AnalyticsFactType.CANDIDATE_CREATED,
        candidate_id=c2.id,
        owner_user_id=hr2.id,
        fact_at=now - timedelta(hours=7),
        source="referral",
    )
    db.commit()
    return hr1, hr2, c1, c2, now


def test_kpi_metrics_over_seeded_facts(client: TestClient, db_session: Session) -> None:
    _make_manager_admin(db_session)
    hr1, hr2, _, _, now = _create_team_facts(db_session)
    _login(client, "manager")

    frm = (now - timedelta(days=1)).isoformat()
    to = (now + timedelta(days=1)).isoformat()
    body = client.get(f"/analytics/kpi?from={_q(frm)}&to={_q(to)}").json()
    kpis = body["kpis"]

    assert kpis == {
        "created_candidates": 2,
        "processed_candidates": 1,  # only c1 had activity (stage + call)
        "calls": 1,
        "reached": 0,
        "interviews_scheduled": 0,
        "interviews_done": 0,
        "offers": 0,
        "hired": 0,
        "dismissed": 0,
        "terminated": 0,
    }
    assert body["by_source"] == [
        {"source": "referral", "created": 1, "hired": 0, "dismissed": 0, "terminated": 0},
        {"source": "site", "created": 1, "hired": 0, "dismissed": 0, "terminated": 0},
    ]
    assert body["by_hr"] == [
        {
            "hr_id": str(hr1.id),
            "username": "alice",
            "created": 1,
            "processed": 1,
            "hired": 0,
            "dismissed": 0,
            "terminated": 0,
        },
        {
            "hr_id": str(hr2.id),
            "username": "bob",
            "created": 1,
            "processed": 0,
            "hired": 0,
            "dismissed": 0,
            "terminated": 0,
        },
    ]


def test_funnel_and_conversions(client: TestClient, db_session: Session) -> None:
    _make_manager_admin(db_session)
    _create_team_facts(db_session)
    _login(client, "manager")

    now = utc_now()
    frm = (now - timedelta(days=1)).isoformat()
    to = (now + timedelta(days=1)).isoformat()
    body = client.get(f"/analytics/funnel?from={_q(frm)}&to={_q(to)}").json()

    assert [s["stage"] for s in body["stages"]] == [
        "new",
        "contacted",
        "reached",
        "interview_scheduled",
        "interview_done",
        "offer",
        "hired",
        "started",
        "probation",
    ]
    reached = {s["stage"]: s["reached"] for s in body["stages"]}
    assert reached["new"] == 2
    assert reached["contacted"] == 1
    assert reached["offer"] == 0

    conversions = {c["from_stage"]: c for c in body["conversions"]}
    assert list(conversions) == [
        "new",
        "contacted",
        "reached",
        "interview_scheduled",
        "interview_done",
        "offer",
        "hired",
        "started",
    ]
    assert conversions["new"]["numerator"] == 1
    assert conversions["new"]["denominator"] == 2
    assert conversions["new"]["rate"] == 50.0
    assert conversions["contacted"]["numerator"] == 0
    assert conversions["contacted"]["denominator"] == 1
    assert conversions["contacted"]["rate"] == 0.0  # real 0 numerator, not null


def test_zero_denominator_gives_null_rate(client: TestClient, db_session: Session) -> None:
    _make_manager_admin(db_session)
    _login(client, "manager")
    now = utc_now()
    frm = (now - timedelta(days=1)).isoformat()
    to = (now + timedelta(days=1)).isoformat()
    body = client.get(f"/analytics/kpi?from={_q(frm)}&to={_q(to)}").json()
    assert all(c["denominator"] == 0 for c in body["conversions"])
    assert all(c["rate"] is None for c in body["conversions"])


def test_repeated_transitions_do_not_double_count(client: TestClient, db_session: Session) -> None:
    _make_manager_admin(db_session)
    hr = make_user(db_session, username="hr1", role=UserRole.HR)
    candidate = make_candidate(db_session, owner=hr)
    csrf = _csrf(_login(client, "manager"))

    for stage in ("offer", "rejected", "offer"):
        response = client.patch(
            f"/candidates/{candidate.id}",
            json={"stage": stage},
            headers={"X-CSRF-Token": csrf},
        )
        assert response.status_code == 200

    frm, to = _window()
    body = client.get(f"/analytics/kpi?from={_q(frm)}&to={_q(to)}").json()
    assert body["kpis"]["offers"] == 1  # distinct candidate, two offer transitions
    assert body["kpis"]["dismissed"] == 1
    assert body["kpis"]["processed_candidates"] == 1

    funnel = client.get(f"/analytics/funnel?from={_q(frm)}&to={_q(to)}").json()
    reached = {s["stage"]: s["reached"] for s in funnel["stages"]}
    assert reached["offer"] == 1
    conversions = {c["from_stage"]: c for c in funnel["conversions"]}
    assert conversions["offer"]["denominator"] == 1


def test_transfer_attribution_uses_fact_time_owner(client: TestClient, db_session: Session) -> None:
    _make_manager_admin(db_session)
    hr1 = make_user(db_session, username="hr1", role=UserRole.HR)
    hr2 = make_user(db_session, username="hr2", role=UserRole.HR)

    csrf = _csrf(_login(client, "hr1"))
    created = client.post(
        "/candidates",
        json={"full_name": "Передаваемый", "source": "hh_manual", "position": ""},
        headers={"X-CSRF-Token": csrf},
    )
    assert created.status_code == 201
    candidate_id = created.json()["id"]

    manager_csrf = _csrf(_login(client, "manager"))
    transfer = client.post(
        f"/candidates/{candidate_id}/transfer",
        json={"new_owner_user_id": str(hr2.id), "reason": "Перераспределение"},
        headers={"X-CSRF-Token": manager_csrf},
    )
    assert transfer.status_code == 200

    csrf = _csrf(_login(client, "hr2"))
    interaction = client.post(
        f"/candidates/{candidate_id}/interactions",
        json={"type": "call", "comment": "Звонок после передачи"},
        headers={"X-CSRF-Token": csrf},
    )
    assert interaction.status_code == 201

    _login(client, "manager")
    frm, to = _window()
    body = client.get(f"/analytics/kpi?from={_q(frm)}&to={_q(to)}").json()
    by_hr = {row["username"]: row for row in body["by_hr"]}
    # Creation stays with hr1; the post-transfer activity belongs to hr2.
    assert by_hr["hr1"]["created"] == 1
    assert by_hr["hr1"]["processed"] == 0
    assert by_hr["hr2"]["created"] == 0
    assert by_hr["hr2"]["processed"] == 1

    # Filtering by hr1 shows only their creation.
    filtered = client.get(f"/analytics/kpi?from={_q(frm)}&to={_q(to)}&hr_id={hr1.id}").json()
    assert filtered["kpis"]["created_candidates"] == 1
    assert filtered["kpis"]["processed_candidates"] == 0
    assert filtered["kpis"]["calls"] == 0


def test_soft_deleted_candidates_remain_in_facts(client: TestClient, db_session: Session) -> None:
    _make_manager_admin(db_session)
    make_user(db_session, username="hr1", role=UserRole.HR)
    csrf = _csrf(_login(client, "hr1"))
    created = client.post(
        "/candidates",
        json={"full_name": "Удаляемый", "source": "site", "position": ""},
        headers={"X-CSRF-Token": csrf},
    )
    assert created.status_code == 201
    candidate_id = created.json()["id"]

    manager_csrf = _csrf(_login(client, "manager"))
    assert (
        client.delete(
            f"/candidates/{candidate_id}", headers={"X-CSRF-Token": manager_csrf}
        ).status_code
        == 200
    )

    frm, to = _window()
    body = client.get(f"/analytics/kpi?from={_q(frm)}&to={_q(to)}").json()
    assert body["kpis"]["created_candidates"] == 1


def test_hr_and_source_filters(client: TestClient, db_session: Session) -> None:
    _make_manager_admin(db_session)
    _create_team_facts(db_session)
    hr1 = db_session.query(User).filter_by(username="alice").one()
    _login(client, "manager")
    now = utc_now()
    frm = (now - timedelta(days=1)).isoformat()
    to = (now + timedelta(days=1)).isoformat()

    by_hr1 = client.get(f"/analytics/kpi?from={_q(frm)}&to={_q(to)}&hr_id={hr1.id}").json()
    assert by_hr1["kpis"]["created_candidates"] == 1
    assert by_hr1["kpis"]["calls"] == 1
    assert [r["username"] for r in by_hr1["by_hr"]] == ["alice"]

    by_source = client.get(f"/analytics/kpi?from={_q(frm)}&to={_q(to)}&source=referral").json()
    assert by_source["kpis"]["created_candidates"] == 1
    assert [r["source"] for r in by_source["by_source"]] == ["referral"]

    combined = client.get(
        f"/analytics/kpi?from={_q(frm)}&to={_q(to)}&source=referral&hr_id={hr1.id}"
    ).json()
    assert combined["kpis"]["created_candidates"] == 0


# --- Interviews (events) ------------------------------------------------------


def test_interview_metrics_from_events(client: TestClient, db_session: Session) -> None:
    _make_manager_admin(db_session)
    hr = make_user(db_session, username="hr1", role=UserRole.HR)
    candidate = make_candidate(db_session, owner=hr)
    csrf = _csrf(_login(client, "hr1"))
    now = utc_now()

    created = client.post(
        "/events",
        json={
            "candidate_id": str(candidate.id),
            "type": "interview",
            "title": "Собеседование",
            "starts_at": (now + timedelta(hours=2)).isoformat(),
            "ends_at": None,
            "remind_at": None,
            "assignee_user_id": None,
        },
        headers={"X-CSRF-Token": csrf},
    )
    assert created.status_code == 201
    event_id = created.json()["id"]
    version = created.json()["version"]

    completed = client.patch(
        f"/events/{event_id}",
        json={"expected_version": version, "status": "completed"},
        headers={"X-CSRF-Token": csrf},
    )
    assert completed.status_code == 200

    _login(client, "manager")
    frm, to = _window()
    body = client.get(f"/analytics/kpi?from={_q(frm)}&to={_q(to)}").json()
    assert body["kpis"]["interviews_scheduled"] == 1
    assert body["kpis"]["interviews_done"] == 1


# --- Terminations --------------------------------------------------------------


def test_termination_endpoint_and_metric(client: TestClient, db_session: Session) -> None:
    _make_manager_admin(db_session)
    hr = make_user(db_session, username="hr1", role=UserRole.HR)
    candidate = make_candidate(db_session, owner=hr)
    frm, to = _window()
    csrf = _csrf(_login(client, "manager"))
    terminated_at = utc_now().isoformat()  # inside [now-1h, now+1h)

    response = client.post(
        f"/candidates/{candidate.id}/termination",
        json={"terminated_at": terminated_at, "reason": "Переезд в другой город"},
        headers={"X-CSRF-Token": csrf},
    )
    assert response.status_code == 201
    body = response.json()
    assert body["candidate_id"] == str(candidate.id)
    assert datetime.fromisoformat(body["terminated_at"]).astimezone(UTC) == datetime.fromisoformat(
        terminated_at
    )
    assert body["reason"] == "Переезд в другой город"
    assert body["created_by_username"] == "manager"

    listing = client.get(f"/candidates/{candidate.id}/terminations")
    assert listing.status_code == 200
    assert listing.json()["total"] == 1
    assert listing.json()["items"][0]["reason"] == "Переезд в другой город"

    kpis = client.get(f"/analytics/kpi?from={_q(frm)}&to={_q(to)}").json()["kpis"]
    assert kpis["terminated"] == 1
    assert kpis["dismissed"] == 0  # termination is NOT a rejected stage move

    # Audit must not contain the free-text reason.
    audit = db_session.scalars(
        select(AuditEvent).where(AuditEvent.action == AuditAction.CANDIDATE_TERMINATED)
    ).all()
    assert len(audit) == 1
    assert "Переезд" not in (audit[0].details or "")


def test_termination_validation_and_rights(client: TestClient, db_session: Session) -> None:
    _make_manager_admin(db_session)
    hr1 = make_user(db_session, username="hr1", role=UserRole.HR)
    make_user(db_session, username="hr2", role=UserRole.HR)
    candidate = make_candidate(db_session, owner=hr1)

    # HR cannot terminate someone else's candidate: visibility comes first
    # (established no-leak 404), same as for transfers.
    csrf = _csrf(_login(client, "hr2"))
    assert (
        client.post(
            f"/candidates/{candidate.id}/termination",
            json={"terminated_at": utc_now().isoformat(), "reason": "Причина"},
            headers={"X-CSRF-Token": csrf},
        ).status_code
        == 404
    )

    # Blank / missing reason -> 422.
    csrf = _csrf(_login(client, "manager"))
    assert (
        client.post(
            f"/candidates/{candidate.id}/termination",
            json={"terminated_at": utc_now().isoformat(), "reason": "   "},
            headers={"X-CSRF-Token": csrf},
        ).status_code
        == 422
    )
    assert (
        client.post(
            f"/candidates/{candidate.id}/termination",
            json={"terminated_at": utc_now().isoformat()},
            headers={"X-CSRF-Token": csrf},
        ).status_code
        == 422
    )


# --- CSV export ----------------------------------------------------------------


def _seed_csv_facts(db: Session) -> tuple[UUID, str, str]:
    hr = make_user(db, username="alice", role=UserRole.HR)
    c = make_candidate(db, owner=hr, source=CandidateSource.SITE)
    t0 = datetime(2026, 1, 2, 10, 0, tzinfo=UTC)
    record_fact(
        db,
        fact_type=AnalyticsFactType.CANDIDATE_CREATED,
        candidate_id=c.id,
        owner_user_id=hr.id,
        fact_at=t0,
        source="site",
    )
    record_fact(
        db,
        fact_type=AnalyticsFactType.STAGE_CHANGED,
        candidate_id=c.id,
        owner_user_id=hr.id,
        fact_at=t0 + timedelta(days=1),
        stage_from="new",
        stage_to="contacted",
        source="site",
    )
    record_fact(
        db,
        fact_type=AnalyticsFactType.INTERACTION_ADDED,
        candidate_id=c.id,
        owner_user_id=hr.id,
        fact_at=t0 + timedelta(days=1, hours=1),
        fact_subtype="call",
        source="site",
    )
    db.commit()
    return hr.id, "2026-01-01T00:00:00+00:00", "2026-02-01T00:00:00+00:00"


def test_csv_export_exact_layout_bom_and_headers(client: TestClient, db_session: Session) -> None:
    _make_manager_admin(db_session)
    hr_id, frm, to = _seed_csv_facts(db_session)
    _login(client, "manager")

    response = client.get(f"/analytics/export?format=csv&from={_q(frm)}&to={_q(to)}")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/csv")
    assert "charset=utf-8" in response.headers["content-type"]
    assert (
        response.headers["content-disposition"]
        == 'attachment; filename="analytics-2026-01-01-2026-02-01.csv"'
    )

    raw = response.content
    assert raw.startswith(b"\xef\xbb\xbf")  # UTF-8 BOM for Excel

    text = raw.decode("utf-8-sig")
    lines = text.split("\r\n")
    expected = [
        "HR Manager Analytics Report",
        "period_from,2026-01-01T00:00:00+00:00",
        "period_to,2026-02-01T00:00:00+00:00",
        "timezone,UTC",
        "scope,team",
        "hr_id,",
        "source,",
        "",
        "section,kpi",
        "metric,value",
        "created_candidates,1",
        "processed_candidates,1",
        "calls,1",
        "reached,0",
        "interviews_scheduled,0",
        "interviews_done,0",
        "offers,0",
        "hired,0",
        "dismissed,0",
        "terminated,0",
        "",
        "section,conversions",
        "from_stage,to_stage,numerator,denominator,rate",
        "new,contacted,1,1,100.00",
        "contacted,reached,0,1,0.00",
        "reached,interview_scheduled,0,0,",
        "interview_scheduled,interview_done,0,0,",
        "interview_done,offer,0,0,",
        "offer,hired,0,0,",
        "hired,started,0,0,",
        "started,probation,0,0,",
        "",
        "section,funnel",
        "stage,reached",
        "new,1",
        "contacted,1",
        "reached,0",
        "interview_scheduled,0",
        "interview_done,0",
        "offer,0",
        "hired,0",
        "started,0",
        "probation,0",
        "",
        "section,by_source",
        "source,created,hired,dismissed,terminated",
        "site,1,0,0,0",
        "",
        "section,by_hr",
        "hr_id,username,created,processed,hired,dismissed,terminated",
        f"{hr_id},alice,1,1,0,0,0",
        "",
    ]
    assert lines == expected


def test_csv_escaping_and_formula_injection_neutralization() -> None:
    assert _csv_field("=SUM(A1:A9)") == "'=SUM(A1:A9)"
    assert _csv_field("+cmd|'/C calc'!A0") == "'+cmd|'/C calc'!A0"
    assert _csv_field("-2+3") == "'-2+3"
    assert _csv_field("@import") == "'@import"
    assert _csv_field("обычное") == "обычное"
    assert _csv_field("a,b;c") == '"a,b;c"'
    assert _csv_field('сказал "да"') == '"сказал ""да"""'
    assert _csv_field("строка\nперенос") == '"строка\nперенос"'
    assert _csv_field(None) == ""
    assert _csv_field(42) == "42"


def test_csv_export_neutralizes_hostile_username(client: TestClient, db_session: Session) -> None:
    _make_manager_admin(db_session)
    # '-' + letters/digits is a valid username pattern -> a realistic
    # spreadsheet-formula injection vector in the by_hr section.
    hr = make_user(db_session, username="-alice", role=UserRole.HR)
    c = make_candidate(db_session, owner=hr)
    t0 = datetime(2026, 1, 2, 10, 0, tzinfo=UTC)
    record_fact(
        db_session,
        fact_type=AnalyticsFactType.CANDIDATE_CREATED,
        candidate_id=c.id,
        owner_user_id=hr.id,
        fact_at=t0,
        source="site",
    )
    db_session.commit()
    _login(client, "manager")

    response = client.get(
        "/analytics/export?format=csv"
        "&from=2026-01-01T00%3A00%3A00%2B00%3A00&to=2026-02-01T00%3A00%3A00%2B00%3A00"
    )
    assert response.status_code == 200
    text = response.content.decode("utf-8-sig")
    assert "'-alice" in text
    assert "\n-alice" not in text  # never exported raw at a field start


def test_export_audited_without_report_content(client: TestClient, db_session: Session) -> None:
    _make_manager_admin(db_session)
    _seed_csv_facts(db_session)
    _login(client, "manager")
    frm, to = _window()

    response = client.get(f"/analytics/export?format=csv&from={_q(frm)}&to={_q(to)}")
    assert response.status_code == 200

    events = db_session.scalars(
        select(AuditEvent).where(AuditEvent.action == AuditAction.ANALYTICS_EXPORTED)
    ).all()
    assert len(events) == 1
    details = events[0].details or ""
    assert "from=" in details and "to=" in details and "timezone=" in details
    assert "created_candidates" not in details  # parameters only, never content
    assert "alice" not in details  # no user/candidate data


def test_export_errors_return_json_not_partial_files(
    client: TestClient, db_session: Session
) -> None:
    _make_manager_admin(db_session)
    _login(client, "manager")
    response = client.get("/analytics/export?format=csv&from=x&to=y")
    assert response.status_code == 422
    assert "application/json" in response.headers["content-type"]


# --- Ledger guarantees ---------------------------------------------------------


def test_ledger_unique_indexes_block_duplicate_facts(db_session: Session) -> None:
    hr = make_user(db_session, username="hr1", role=UserRole.HR)
    c = make_candidate(db_session, owner=hr)
    t0 = utc_now()
    record_fact(
        db_session,
        fact_type=AnalyticsFactType.CANDIDATE_CREATED,
        candidate_id=c.id,
        owner_user_id=hr.id,
        fact_at=t0,
        source="site",
    )
    db_session.commit()
    with pytest.raises(IntegrityError):  # UNIQUE (partial) constraint on candidate_id
        record_fact(
            db_session,
            fact_type=AnalyticsFactType.CANDIDATE_CREATED,
            candidate_id=c.id,
            owner_user_id=hr.id,
            fact_at=t0,
            source="site",
        )
        db_session.commit()
    db_session.rollback()


def test_mutation_rolls_back_when_ledger_write_fails(
    client: TestClient, db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A failed ledger write must roll back the whole candidate creation."""
    from app.routers import candidates as candidates_router

    make_user(db_session, username="hr1", role=UserRole.HR)
    csrf = _csrf(_login(client, "hr1"))

    def failing_record_fact(db: Session, *args: Any, **kwargs: Any) -> None:
        raise RuntimeError("simulated ledger write failure")

    monkeypatch.setattr(candidates_router, "record_fact", failing_record_fact)

    with pytest.raises(RuntimeError, match="simulated ledger write failure"):
        client.post(
            "/candidates",
            json={"full_name": "Не появится", "source": "site", "position": ""},
            headers={"X-CSRF-Token": csrf},
        )

    db_session.expire_all()
    assert db_session.scalar(select(func.count()).select_from(Candidate)) == 0
    assert db_session.scalar(select(func.count()).select_from(AnalyticsFact)) == 0


# --- OpenAPI --------------------------------------------------------------------


def test_openapi_documents_analytics_contract(client: TestClient) -> None:
    spec = client.get("/openapi.json").json()
    paths = spec["paths"]
    for path in ("/analytics/kpi", "/analytics/funnel", "/analytics/export"):
        assert path in paths
        assert "get" in paths[path]
        parameters = {p["name"]: p for p in paths[path]["get"]["parameters"]}
        assert {"from", "to", "timezone"} <= set(parameters)
    schemas = spec["components"]["schemas"]
    assert "AnalyticsKpiResponse" in schemas
    assert "AnalyticsFunnelResponse" in schemas
    assert "AnalyticsConversionOut" in schemas
    kpi_props = schemas["AnalyticsKpiResponse"]["properties"]
    assert set(kpi_props) == {
        "period",
        "filters",
        "scope",
        "kpis",
        "conversions",
        "by_source",
        "by_hr",
    }
    kpis_props = schemas["AnalyticsKpisOut"]["properties"]
    assert set(kpis_props) == {
        "created_candidates",
        "processed_candidates",
        "calls",
        "reached",
        "interviews_scheduled",
        "interviews_done",
        "offers",
        "hired",
        "dismissed",
        "terminated",
    }
