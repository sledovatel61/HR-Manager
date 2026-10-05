"""«Моя очередь» dashboard against a real PostgreSQL.

Why this file exists: the SQLite unit suite (``tests/test_queue_dashboard.py``)
cannot prove the PostgreSQL-specific parts — native ``timestamptz`` round-trip
(aware vs naive, the reason ``_as_utc`` exists), the ``Date`` column of
``candidate.start_date``, and ``IN (…)`` over native UUIDs with a scope far
larger than one page. SQLite stores everything as text, so a bug where the
aggregator returned an offset-less timestamp would pass there and break the
API contract in production.

Covered here:

* every timestamp in the response carries an explicit UTC offset;
* a candidate far beyond the first page is counted (the page-size regression);
* the week axis is built from local midnights in a real IANA zone;
* HR cannot widen the scope, a manager can switch to a colleague.

Run with::

    TEST_DATABASE_URL=postgresql+psycopg://user:pass@host:5432/db \\
        pytest -m integration tests/test_integration_queue_dashboard.py
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import update
from sqlalchemy.orm import Session

from app.analytics_ledger import record_fact
from app.models import (
    AnalyticsFactType,
    Candidate,
    CandidateSource,
    CandidateStage,
    User,
    UserRole,
)
from app.routers.auth import reset_login_limiter
from tests.conftest import FIXTURE_PASSWORD, make_candidate, make_user

pytestmark = pytest.mark.integration

UTC = ZoneInfo("UTC")
MSK = ZoneInfo("Europe/Moscow")
URL = "/candidates/queue/dashboard"


@pytest.fixture(autouse=True)
def _clean_limiter() -> Iterator[None]:
    reset_login_limiter()
    yield
    reset_login_limiter()


def _login(client: TestClient, username: str) -> httpx.Response:
    return client.post("/auth/login", json={"username": username, "password": FIXTURE_PASSWORD})


def _now() -> datetime:
    return datetime.now(UTC)


def _seed(
    db: Session,
    *,
    owner: User,
    created_at: datetime,
    stage: CandidateStage = CandidateStage.NEW,
    source: CandidateSource = CandidateSource.SITE,
    full_name: str = "Иванов Иван",
    updated_at: datetime | None = None,
) -> Candidate:
    candidate = make_candidate(db, owner=owner, source=source, full_name=full_name)
    db.execute(
        update(Candidate)
        .where(Candidate.id == candidate.id)
        .values(created_at=created_at, updated_at=updated_at or created_at)
    )
    record_fact(
        db,
        fact_type=AnalyticsFactType.CANDIDATE_CREATED,
        candidate_id=candidate.id,
        owner_user_id=owner.id,
        fact_at=created_at,
        source=source.value,
    )
    if stage is not CandidateStage.NEW:
        db.execute(
            update(Candidate)
            .where(Candidate.id == candidate.id)
            .values(stage=stage, updated_at=created_at + timedelta(hours=1))
        )
        record_fact(
            db,
            fact_type=AnalyticsFactType.STAGE_CHANGED,
            candidate_id=candidate.id,
            owner_user_id=owner.id,
            fact_at=created_at + timedelta(hours=1),
            stage_to=stage.value,
            source=source.value,
        )
    db.commit()
    db.refresh(candidate)
    return candidate


def test_every_timestamp_carries_a_utc_offset_on_postgres(
    pg_client: TestClient, pg_db: Session
) -> None:
    """timestamptz round-trips as aware UTC: the response must say so.

    On SQLite a naive value would be indistinguishable; here a missing offset
    would be an API-contract break (the browser would shift the card).
    """
    hr = make_user(pg_db, username="hr1", role=UserRole.HR)
    _seed(pg_db, owner=hr, created_at=_now() - timedelta(hours=2))
    _login(pg_client, "hr1")

    payload = pg_client.get(f"{URL}?period=today").json()

    for key in ("from", "to"):
        assert payload["period"][key].endswith(("+00:00", "Z")), payload["period"][key]
    for bucket in payload["period"]["buckets"]:
        assert bucket["from"].endswith(("+00:00", "Z"))
        assert bucket["to"].endswith(("+00:00", "Z"))
    assert payload["generated_at"].endswith(("+00:00", "Z"))


def test_aggregate_counts_candidates_beyond_the_first_page_on_postgres(
    pg_client: TestClient, pg_db: Session
) -> None:
    """Native UUID scope: the aggregate is not a page, and not a LIMIT."""
    hr = make_user(pg_db, username="hr1", role=UserRole.HR)
    moment = _now()
    _seed(
        pg_db,
        owner=hr,
        created_at=moment - timedelta(days=400),
        updated_at=moment - timedelta(days=400),
        stage=CandidateStage.OFFER,
        source=CandidateSource.UNIVERSITY,
        full_name="Давно ждёт",
    )
    for index in range(119):
        _seed(
            pg_db,
            owner=hr,
            created_at=moment - timedelta(hours=index + 1),
            updated_at=moment - timedelta(days=10),
            full_name=f"Кандидат {index}",
        )
    _login(pg_client, "hr1")

    payload = pg_client.get(f"{URL}?period=all").json()

    assert payload["kpis"]["new_candidates"] == 120
    assert payload["kpis"]["total_candidates"] == 120
    assert {row["source"]: row["count"] for row in payload["sources"]}["university"] == 1
    assert {row["stage"]: row["count"] for row in payload["funnel"]}["offer"] == 1
    assert payload["attention_candidates_total"] == 120
    # Самая давняя строка — первая в выборке «Требуют внимания».
    assert payload["attention_candidates"][0]["full_name"] == "Давно ждёт"
    assert payload["truncated"] is False


def test_start_date_and_week_axis_on_postgres(pg_client: TestClient, pg_db: Session) -> None:
    """``start_date`` is a Date column here (text on SQLite), and the week axis
    is built from local midnights of a real IANA zone."""
    hr = make_user(pg_db, username="hr1", role=UserRole.HR)
    moment = _now()
    candidate = _seed(pg_db, owner=hr, created_at=moment - timedelta(days=10))
    # Дата внутри оси недели: «+2 дня» был бы за пределами периода графика
    # (ось заканчивается завтрашней полуночью), хотя в KPI «выходы на неделе»
    # попадает — там окно смотрит вперёд на 7 дней.
    db_date = moment.date()
    pg_db.execute(update(Candidate).where(Candidate.id == candidate.id).values(start_date=db_date))
    pg_db.commit()
    _login(pg_client, "hr1")

    moscow = pg_client.get(f"{URL}?period=week&timezone=Europe/Moscow").json()
    utc = pg_client.get(f"{URL}?period=week&timezone=UTC").json()

    assert moscow["kpis"]["weekly_exits"] == 1
    assert len(moscow["period"]["buckets"]) == 7
    # Границы суток разные: Москва начинает сутки раньше UTC.
    assert moscow["period"]["from"] != utc["period"]["from"]
    assert moscow["period"]["timezone"] == "Europe/Moscow"
    assert sum(row["exits"] for row in moscow["hiring_dynamics"]) == 1


def test_scope_rules_on_postgres(pg_client: TestClient, pg_db: Session) -> None:
    hr = make_user(pg_db, username="hr1", role=UserRole.HR)
    manager = make_user(pg_db, username="manager1", role=UserRole.MANAGER)
    _seed(pg_db, owner=hr, created_at=_now() - timedelta(days=1), full_name="Свой")
    _seed(pg_db, owner=manager, created_at=_now() - timedelta(days=1), full_name="Чужой")
    _login(pg_client, "hr1")

    own = pg_client.get(f"{URL}?period=all").json()
    assert own["kpis"]["new_candidates"] == 1
    # HR не может расширить область даже вручную передав owner_id.
    forced = pg_client.get(f"{URL}?period=all&owner_id={manager.id}").json()
    assert forced["scope"]["owner_id"] == str(hr.id)

    _login(pg_client, "manager1")
    switched = pg_client.get(f"{URL}?period=all&owner_id={hr.id}").json()
    assert switched["scope"]["owner_id"] == str(hr.id)
    assert switched["scope"]["personal"] is False
    assert switched["kpis"]["new_candidates"] == 1
