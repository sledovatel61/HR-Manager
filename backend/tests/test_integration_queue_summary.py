"""PostgreSQL mirrors for the «Должность» directory and the queue summary.

Run with::

    TEST_DATABASE_URL=postgresql+psycopg://user:pass@host:5432/db \\
        pytest -m integration -v backend/tests/test_integration_queue_summary.py

Why these two endpoints need a real PostgreSQL in addition to the SQLite unit
tests in ``tests/test_candidate_positions.py`` / ``tests/test_queue_summary.py``:

* the directory is a ``GROUP BY`` over ``position_normalized`` with
  ``min(position)`` as the representative spelling — on PostgreSQL both depend
  on the database collation, which is exactly what the endpoint must *not*
  rely on (the order is computed in Python, the spelling is only cosmetic);
* the summary aggregates the whole personal scope with ``count()`` and
  ``GROUP BY stage`` over native UUID/timestamptz columns, and the
  «Без движения» cut-off is a timestamptz comparison.

The schema must be applied (``alembic upgrade head``); fixtures truncate the
tables before each test.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import update
from sqlalchemy.orm import Session

from app.models import Candidate, CandidateStage, Event, EventStatus, UserRole
from tests.conftest import FIXTURE_PASSWORD, make_candidate, make_event, make_user

pytestmark = pytest.mark.integration

UTC = ZoneInfo("UTC")


def _login(client: TestClient, username: str) -> None:
    response = client.post("/auth/login", json={"username": username, "password": FIXTURE_PASSWORD})
    assert response.status_code == 200, response.text


def _age(db: Session, candidate: Candidate, **values: object) -> None:
    db.execute(update(Candidate).where(Candidate.id == candidate.id).values(**values))
    db.commit()
    db.refresh(candidate)


def test_position_directory_covers_the_whole_base_on_postgres(
    pg_client: TestClient, pg_db: Session
) -> None:
    """A position that only exists outside the first page must be selectable.

    Collation-dependent ``min()`` is cosmetic here; what matters is that the
    group is present at all.
    """
    hr = make_user(pg_db, username="hr1", role=UserRole.HR)
    far = make_candidate(
        pg_db, owner=hr, full_name="Дальний кандидат", position="Дальняя должность"
    )
    _age(pg_db, far, updated_at=datetime.now(UTC) - timedelta(days=30))
    for index in range(120):
        make_candidate(pg_db, owner=hr, full_name=f"Кандидат {index}", position="Монтажник РЭА")

    _login(pg_client, "hr1")

    directory = pg_client.get("/candidates/positions")
    assert directory.status_code == 200
    positions = [item["position"] for item in directory.json()["items"]]
    assert positions == ["Дальняя должность", "Монтажник РЭА"]
    assert directory.json()["items"][1]["count"] == 120

    # И фильтр по ней работает — на PostgreSQL, а не только на SQLite.
    filtered = pg_client.get("/candidates?position=дальняя ДОЛЖНОСТЬ")
    assert filtered.status_code == 200
    assert filtered.json()["total"] == 1


def test_queue_summary_aggregates_the_whole_personal_scope_on_postgres(
    pg_client: TestClient, pg_db: Session
) -> None:
    """The stale candidate lives in the oldest rows — the summary still sees it."""
    hr = make_user(pg_db, username="hr1", role=UserRole.HR)
    hr2 = make_user(pg_db, username="hr2", role=UserRole.HR)
    now = datetime.now(UTC)

    stale = make_candidate(pg_db, owner=hr, full_name="Забытый кандидат", position="Инженер")
    _age(pg_db, stale, created_at=now - timedelta(days=40), updated_at=now - timedelta(days=30))
    for index in range(120):
        fresh = make_candidate(pg_db, owner=hr, full_name=f"Кандидат {index}", position="Монтажник")
        _age(pg_db, fresh, created_at=now - timedelta(days=5))
    # Чужой кандидат в личную очередь не попадает.
    make_candidate(pg_db, owner=hr2, full_name="Чужой кандидат")

    # Событие завершённое — не «требует действия».
    own = make_candidate(pg_db, owner=hr, full_name="С событием")
    make_event(
        pg_db,
        candidate=own,
        author=hr,
        assignee=hr,
        title="Уже состоялось",
        starts_at=now + timedelta(days=1),
        status=EventStatus.COMPLETED,
    )
    planned = make_event(
        pg_db,
        candidate=own,
        author=hr,
        assignee=hr,
        title="Созвон",
        starts_at=now + timedelta(days=2),
        status=EventStatus.SCHEDULED,
    )
    # Отменённое событие: cancelled_at обязателен по CHECK-ограничению.
    cancelled = make_event(
        pg_db,
        candidate=own,
        author=hr,
        assignee=hr,
        title="Отменено",
        starts_at=now + timedelta(days=3),
    )
    pg_db.execute(
        update(Event)
        .where(Event.id == cancelled.id)
        .values(status=EventStatus.CANCELLED, cancelled_at=now)
    )
    pg_db.commit()

    _login(pg_client, "hr1")

    summary = pg_client.get("/candidates/queue/summary")
    assert summary.status_code == 200
    body = summary.json()
    assert body["total"] == 122
    assert body["in_work"] == 122
    assert body["stuck"] == 1
    assert [item["full_name"] for item in body["stuck_sample"]] == ["Забытый кандидат"]
    assert [item["id"] for item in body["upcoming_events"]] == [str(planned.id)]
    assert body["upcoming_events_total"] == 1


def test_queue_summary_is_personal_for_manager_on_postgres(
    pg_client: TestClient, pg_db: Session
) -> None:
    manager = make_user(pg_db, username="mgr", role=UserRole.MANAGER)
    hr = make_user(pg_db, username="hr1", role=UserRole.HR)
    own = make_candidate(pg_db, owner=manager, full_name="Кандидат руководителя")
    _age(pg_db, own, updated_at=datetime.now(UTC) - timedelta(days=10))
    for index in range(5):
        make_candidate(pg_db, owner=hr, full_name=f"Кандидат HR {index}")

    _login(pg_client, "mgr")

    body = pg_client.get("/candidates/queue/summary").json()
    assert body["owner_id"] == str(manager.id)
    assert body["total"] == 1
    assert body["stuck"] == 1


def test_empty_summary_over_postgres(pg_client: TestClient, pg_db: Session) -> None:
    make_user(pg_db, username="hr1", role=UserRole.HR)

    _login(pg_client, "hr1")

    body = pg_client.get("/candidates/queue/summary").json()
    assert body["total"] == 0
    assert body["by_stage"][0] == {"stage": "new", "count": 0}
    assert len(body["by_stage"]) == 11
    assert body["stuck_sample"] == []
    assert body["upcoming_events"] == []


def test_closed_stages_never_count_as_in_work_on_postgres(
    pg_client: TestClient, pg_db: Session
) -> None:
    hr = make_user(pg_db, username="hr1", role=UserRole.HR)
    for index, stage in enumerate(
        (
            CandidateStage.NEW,
            CandidateStage.CONTACTED,
            CandidateStage.HIRED,
            CandidateStage.STARTED,
            CandidateStage.PROBATION,
            CandidateStage.FIRED,
            CandidateStage.REJECTED,
        )
    ):
        candidate = make_candidate(pg_db, owner=hr, full_name=f"Кандидат {index}", stage=stage)
        _age(pg_db, candidate, updated_at=datetime.now(UTC) - timedelta(days=10))

    _login(pg_client, "hr1")

    body = pg_client.get("/candidates/queue/summary").json()
    assert body["total"] == 7
    assert body["in_work"] == 2
    # Закрытые этапы не считаются застрявшими, даже если по ним давно не было
    # движений.
    assert body["stuck"] == 2
