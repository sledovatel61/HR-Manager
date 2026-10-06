"""«Моя очередь» summary endpoint (in-memory SQLite).

What this file locks in
-----------------------
The screen used to download the 100 most recently **updated** candidates and
count everything in the browser. By construction such a window holds the
freshest rows, so «Без движения 3+ дня» and «Требуют внимания» systematically
missed exactly the candidates they exist for, and the KPI tiles presented a
page as the whole queue.

``GET /candidates/queue/summary`` returns server-side aggregates over the
caller's whole personal scope plus two bounded samples for the cards. These
tests pin the aggregates, the personality of the scope (HR, manager,
administrator, pilot), the window semantics and the event semantics.

PostgreSQL mirrors live in ``tests/test_integration_candidates.py``.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import update
from sqlalchemy.orm import Session

from app.models import (
    AccessGrant,
    AccessGrantScope,
    Candidate,
    CandidateStage,
    Event,
    EventStatus,
    User,
    UserRole,
)
from tests.conftest import FIXTURE_PASSWORD, make_candidate, make_event, make_user

UTC = ZoneInfo("UTC")
STUCK_DAYS = 3


@pytest.fixture(autouse=True)
def _clean_limiter() -> Iterator[None]:  # pragma: no cover - see test_candidates.py
    from app.routers.auth import reset_login_limiter

    reset_login_limiter()
    yield
    reset_login_limiter()


def _login(client: TestClient, username: str) -> None:
    response = client.post("/auth/login", json={"username": username, "password": FIXTURE_PASSWORD})
    assert response.status_code == 200, response.text


def _now() -> datetime:
    return datetime.now(UTC)


def _set_times(
    db: Session,
    candidate: Candidate,
    *,
    created_at: datetime | None = None,
    updated_at: datetime | None = None,
) -> Candidate:
    """Write timestamps explicitly: ``updated_at`` carries an ``onupdate``
    default, so a plain attribute assignment would be overwritten."""
    values = {}
    if created_at is not None:
        values["created_at"] = created_at
    if updated_at is not None:
        values["updated_at"] = updated_at
    if not values:
        return candidate
    db.execute(update(Candidate).where(Candidate.id == candidate.id).values(**values))
    db.commit()
    db.refresh(candidate)
    return candidate


def _set_stage(db: Session, candidate: Candidate, stage: CandidateStage) -> Candidate:
    from app.models import CANDIDATE_STAGE_POSITION

    db.execute(
        update(Candidate)
        .where(Candidate.id == candidate.id)
        .values(stage=stage, stage_position=CANDIDATE_STAGE_POSITION[stage])
    )
    db.commit()
    db.refresh(candidate)
    return candidate


def _summary(client: TestClient, query: str = "") -> dict:
    response = client.get(f"/candidates/queue/summary{query}")
    assert response.status_code == 200, response.text
    return response.json()


def _grant_pilot(db: Session, user: User) -> None:
    db.add(AccessGrant(user_id=user.id, scope=AccessGrantScope.PILOT_FULL_ACCESS))
    db.commit()


# --- Доступ ------------------------------------------------------------------


def test_unauthenticated_queue_summary_gets_401(client: TestClient, db_session: Session) -> None:
    make_user(db_session, username="hr1", role=UserRole.HR)

    assert client.get("/candidates/queue/summary").status_code == 401


# --- Главная регрессия: очередь шире первой страницы --------------------------


def test_stale_candidate_beyond_the_first_page_is_counted(
    client: TestClient, db_session: Session
) -> None:
    """A candidate that has been waiting for a month is the *oldest* row.

    The screen downloaded the 100 freshest rows and counted in the browser, so
    this candidate was invisible to «Без движения» — the summary counts it.
    """
    hr = make_user(db_session, username="hr1", role=UserRole.HR)
    stale = make_candidate(db_session, owner=hr, full_name="Забытый кандидат", position="Инженер")
    _set_times(
        db_session,
        stale,
        created_at=_now() - timedelta(days=40),
        updated_at=_now() - timedelta(days=30),
    )
    for index in range(120):
        make_candidate(db_session, owner=hr, full_name=f"Кандидат {index}", position="Монтажник")
    # Остальные созданы «давно», иначе они тоже попадут в «Новые за сутки».
    db_session.execute(
        update(Candidate)
        .where(Candidate.id != stale.id)
        .values(created_at=_now() - timedelta(days=5))
    )
    db_session.commit()

    _login(client, "hr1")

    # Доказательство причины: первой страницы из 100 строк недостаточно.
    first_page = client.get("/candidates?sort=updated_at&direction=desc&limit=100&offset=0")
    assert first_page.status_code == 200
    assert first_page.json()["total"] == 121
    assert "Забытый кандидат" not in [item["full_name"] for item in first_page.json()["items"]]

    summary = _summary(client)
    assert summary["total"] == 121
    assert summary["in_work"] == 121
    assert summary["stuck"] == 1
    assert summary["fresh"] == 0
    assert [item["full_name"] for item in summary["stuck_sample"]] == ["Забытый кандидат"]
    assert summary["stuck_sample_truncated"] is False


# --- Точность агрегатов -------------------------------------------------------


def test_kpis_and_funnel_are_exact(client: TestClient, db_session: Session) -> None:
    hr = make_user(db_session, username="hr1", role=UserRole.HR)
    now = _now()

    fresh = make_candidate(db_session, owner=hr, full_name="Новый", position="Инженер")
    _set_times(db_session, fresh, created_at=now - timedelta(hours=2), updated_at=now)

    contacted = make_candidate(db_session, owner=hr, full_name="В работе", position="Инженер")
    _set_times(db_session, contacted, created_at=now - timedelta(days=5), updated_at=now)
    _set_stage(db_session, contacted, CandidateStage.CONTACTED)

    offer = make_candidate(db_session, owner=hr, full_name="Оффер", position="Инженер")
    _set_times(db_session, offer, created_at=now - timedelta(days=5), updated_at=now)
    _set_stage(db_session, offer, CandidateStage.OFFER)

    hired = make_candidate(db_session, owner=hr, full_name="Нанят", position="Инженер")
    _set_times(
        db_session, hired, created_at=now - timedelta(days=9), updated_at=now - timedelta(days=9)
    )
    _set_stage(db_session, hired, CandidateStage.HIRED)

    rejected = make_candidate(db_session, owner=hr, full_name="Отказ", position="Инженер")
    _set_times(
        db_session, rejected, created_at=now - timedelta(days=9), updated_at=now - timedelta(days=9)
    )
    _set_stage(db_session, rejected, CandidateStage.REJECTED)

    # Выходы на неделе: сегодня, +3 и +6 дней попадают, +7 — уже нет.
    for name, offset in (("Выход сегодня", 0), ("Выход +3", 3), ("Выход +6", 6), ("Выход +7", 7)):
        starts = make_candidate(db_session, owner=hr, full_name=name, position="Инженер")
        starts.start_date = now.date() + timedelta(days=offset)
    db_session.commit()
    # «Новые за сутки» — только кандидат, созданный два часа назад.
    db_session.execute(
        update(Candidate).where(Candidate.id != fresh.id).values(created_at=now - timedelta(days=5))
    )
    db_session.commit()

    _login(client, "hr1")

    summary = _summary(client)
    assert summary["total"] == 9
    # В работе = все, кроме закрытых этапов (hired, rejected).
    assert summary["in_work"] == 7
    assert summary["fresh"] == 1
    assert summary["stuck"] == 0
    assert summary["starts"] == 3
    assert summary["closed_stages"] == ["hired", "started", "probation", "fired", "rejected"]

    by_stage = {row["stage"]: row["count"] for row in summary["by_stage"]}
    assert by_stage == {
        "new": 5,  # fresh + три «выхода» + …
        "contacted": 1,
        "reached": 0,
        "interview_scheduled": 0,
        "interview_done": 0,
        "offer": 1,
        "hired": 1,
        "started": 0,
        "probation": 0,
        "fired": 0,
        "rejected": 1,
    }
    # Воронка покрывает всю очередь, а не страницу: сумма = total.
    assert sum(by_stage.values()) == summary["total"]


def test_stuck_window_and_horizon_are_configurable(client: TestClient, db_session: Session) -> None:
    hr = make_user(db_session, username="hr1", role=UserRole.HR)
    now = _now()
    waiting = make_candidate(db_session, owner=hr, full_name="Ждёт 5 дней")
    _set_times(
        db_session,
        waiting,
        created_at=now - timedelta(days=5),
        updated_at=now - timedelta(days=5),
    )
    starts = make_candidate(db_session, owner=hr, full_name="Выход +3")
    starts.start_date = now.date() + timedelta(days=3)
    db_session.commit()

    _login(client, "hr1")

    assert _summary(client)["stuck"] == 1
    assert _summary(client, "?stuck_days=10")["stuck"] == 0
    assert _summary(client, "?stuck_days=1")["stuck"] == 1
    assert _summary(client)["starts"] == 1
    assert _summary(client, "?horizon_days=2")["starts"] == 0
    assert _summary(client, "?horizon_days=7")["starts"] == 1


def test_deleted_candidates_are_ignored(client: TestClient, db_session: Session) -> None:
    hr = make_user(db_session, username="hr1", role=UserRole.HR)
    make_candidate(db_session, owner=hr, full_name="Рабочий")
    make_candidate(db_session, owner=hr, full_name="Удалённый", deleted=True)

    _login(client, "hr1")

    summary = _summary(client)
    assert summary["total"] == 1
    assert summary["in_work"] == 1


# --- Персональность -----------------------------------------------------------


def test_hr_summary_excludes_foreign_candidates(client: TestClient, db_session: Session) -> None:
    hr1 = make_user(db_session, username="hr1", role=UserRole.HR)
    hr2 = make_user(db_session, username="hr2", role=UserRole.HR)
    make_candidate(db_session, owner=hr1, full_name="Свой")
    for index in range(4):
        foreign = make_candidate(db_session, owner=hr2, full_name=f"Чужой {index}")
        _set_times(db_session, foreign, updated_at=_now() - timedelta(days=10))

    _login(client, "hr1")

    summary = _summary(client)
    assert summary["total"] == 1
    assert summary["stuck"] == 0
    assert summary["owner_username"] == "hr1"


def test_manager_summary_is_personal_not_the_shared_base(
    client: TestClient, db_session: Session
) -> None:
    """The heading says «моя очередь» — a manager used to get everybody's."""
    mgr = make_user(db_session, username="mgr", role=UserRole.MANAGER)
    hr = make_user(db_session, username="hr1", role=UserRole.HR)
    own = make_candidate(db_session, owner=mgr, full_name="Кандидат руководителя")
    _set_times(db_session, own, updated_at=_now() - timedelta(days=10))
    for index in range(5):
        make_candidate(db_session, owner=hr, full_name=f"Кандидат HR {index}")

    _login(client, "mgr")

    summary = _summary(client)
    assert summary["owner_id"] == str(mgr.id)
    assert summary["total"] == 1
    assert summary["stuck"] == 1
    assert summary["personal"] is True


def test_admin_and_pilot_summaries_are_personal(client: TestClient, db_session: Session) -> None:
    admin = make_user(db_session, username="adm", role=UserRole.ADMIN)
    pilot = make_user(db_session, username="pilot", role=UserRole.ADMIN)
    _grant_pilot(db_session, pilot)
    hr = make_user(db_session, username="hr1", role=UserRole.HR)

    for index in range(2):
        make_candidate(db_session, owner=admin, full_name=f"Кандидат админа {index}")
    make_candidate(db_session, owner=pilot, full_name="Кандидат пилота")
    for index in range(7):
        make_candidate(db_session, owner=hr, full_name=f"Кандидат HR {index}")

    _login(client, "adm")
    assert _summary(client)["total"] == 2

    # Смена пользователя: logout с CSRF-токеном из ответа логина.
    relogin = client.post("/auth/login", json={"username": "adm", "password": FIXTURE_PASSWORD})
    csrf = relogin.json()["csrf_token"]
    assert client.post("/auth/logout", headers={"X-CSRF-Token": csrf}).status_code == 200
    _login(client, "pilot")
    pilot_summary = _summary(client)
    assert pilot_summary["owner_username"] == "pilot"
    assert pilot_summary["total"] == 1


# --- События ------------------------------------------------------------------


def test_upcoming_events_are_personal_and_never_terminal(
    client: TestClient, db_session: Session
) -> None:
    hr = make_user(db_session, username="hr1", role=UserRole.HR)
    hr2 = make_user(db_session, username="hr2", role=UserRole.HR)
    own = make_candidate(db_session, owner=hr, full_name="Свой кандидат")
    foreign = make_candidate(db_session, owner=hr2, full_name="Чужой кандидат")
    now = _now()

    planned = make_event(
        db_session,
        candidate=own,
        author=hr,
        assignee=hr,
        title="Созвон",
        starts_at=now + timedelta(days=1),
        status=EventStatus.SCHEDULED,
    )
    make_event(
        db_session,
        candidate=own,
        author=hr,
        assignee=hr,
        title="Уже состоялся",
        starts_at=now + timedelta(days=2),
        status=EventStatus.COMPLETED,
    )
    cancelled = make_event(
        db_session,
        candidate=own,
        author=hr,
        assignee=hr,
        title="Отменён",
        starts_at=now + timedelta(days=2, hours=1),
    )
    db_session.execute(
        update(Event)
        .where(Event.id == cancelled.id)
        .values(status=EventStatus.CANCELLED, cancelled_at=now)
    )
    db_session.commit()
    make_event(
        db_session,
        candidate=own,
        author=hr,
        assignee=hr,
        title="За горизонтом",
        starts_at=now + timedelta(days=10),
        status=EventStatus.SCHEDULED,
    )
    make_event(
        db_session,
        candidate=foreign,
        author=hr2,
        assignee=hr2,
        title="Чужое событие",
        starts_at=now + timedelta(days=1),
        status=EventStatus.SCHEDULED,
    )

    _login(client, "hr1")

    summary = _summary(client)
    assert summary["upcoming_events_total"] == 1
    assert [item["id"] for item in summary["upcoming_events"]] == [str(planned.id)]
    assert summary["upcoming_events"][0]["status"] == "scheduled"
    assert summary["upcoming_events"][0]["candidate_full_name"] == "Свой кандидат"
    assert summary["upcoming_events_truncated"] is False


def test_event_sample_is_bounded_and_flagged(client: TestClient, db_session: Session) -> None:
    hr = make_user(db_session, username="hr1", role=UserRole.HR)
    candidate = make_candidate(db_session, owner=hr, full_name="Кандидат")
    now = _now()
    for index in range(8):
        make_event(
            db_session,
            candidate=candidate,
            author=hr,
            assignee=hr,
            title=f"Событие {index}",
            starts_at=now + timedelta(days=1, minutes=index),
            status=EventStatus.SCHEDULED,
        )

    _login(client, "hr1")

    summary = _summary(client, "?sample_limit=3")
    assert summary["upcoming_events_total"] == 8
    assert len(summary["upcoming_events"]) == 3
    assert summary["upcoming_events_truncated"] is True
    # Ближайшие первыми — по starts_at.
    titles = [item["title"] for item in summary["upcoming_events"]]
    assert titles == ["Событие 0", "Событие 1", "Событие 2"]


# --- Выборки (карточки) -------------------------------------------------------


def test_stuck_sample_is_bounded_oldest_first(client: TestClient, db_session: Session) -> None:
    hr = make_user(db_session, username="hr1", role=UserRole.HR)
    now = _now()
    for index in range(10):
        stale = make_candidate(db_session, owner=hr, full_name=f"Ждёт {index}")
        _set_times(db_session, stale, updated_at=now - timedelta(days=4 + index))

    _login(client, "hr1")

    summary = _summary(client, "?sample_limit=3")
    assert summary["stuck"] == 10
    assert summary["stuck_sample_truncated"] is True
    # Самые давние — первыми: они ждут дольше всех.
    assert [item["full_name"] for item in summary["stuck_sample"]] == ["Ждёт 9", "Ждёт 8", "Ждёт 7"]
    assert summary["stuck_sample"][0]["stage"] == "new"


def test_empty_queue_returns_zeroed_aggregates(client: TestClient, db_session: Session) -> None:
    make_user(db_session, username="hr1", role=UserRole.HR)

    _login(client, "hr1")

    summary = _summary(client)
    assert summary["total"] == 0
    assert summary["in_work"] == 0
    assert summary["fresh"] == 0
    assert summary["stuck"] == 0
    assert summary["starts"] == 0
    assert summary["stuck_sample"] == []
    assert summary["upcoming_events"] == []
    assert summary["upcoming_events_total"] == 0
    assert summary["stuck_sample_truncated"] is False
    assert summary["upcoming_events_truncated"] is False
    # Воронка присутствует целиком: пустая очередь — не отсутствующая воронка.
    assert [row["count"] for row in summary["by_stage"]] == [0] * len(summary["by_stage"])
    assert len(summary["by_stage"]) == 11


def test_summary_never_exposes_other_peoples_candidates(
    client: TestClient, db_session: Session
) -> None:
    """Персональные данные чужих кандидатов не должны утечь даже в выборке."""
    hr = make_user(db_session, username="hr1", role=UserRole.HR)
    hr2 = make_user(db_session, username="hr2", role=UserRole.HR)
    make_candidate(db_session, owner=hr, full_name="Мой кандидат", position="Инженер")
    stale = make_candidate(db_session, owner=hr2, full_name="Чужой кандидат", position="Директор")
    _set_times(db_session, stale, updated_at=_now() - timedelta(days=20))

    _login(client, "hr1")

    payload: httpx.Response = client.get("/candidates/queue/summary")
    assert "Чужой кандидат" not in payload.text
    assert "Директор" not in payload.text


def test_unknown_owner_is_not_a_parameter(client: TestClient, db_session: Session) -> None:
    """Сводка персональна: параметра «чужой owner» у неё нет по замыслу."""
    hr = make_user(db_session, username="hr1", role=UserRole.HR)
    make_candidate(db_session, owner=hr, full_name="Мой кандидат")

    _login(client, "hr1")

    with_owner = client.get(f"/candidates/queue/summary?owner_id={uuid.uuid4()}")
    assert with_owner.status_code == 200
    assert with_owner.json()["total"] == 1
    assert with_owner.json()["owner_id"] == str(hr.id)


def test_generated_at_and_windows_are_utc(client: TestClient, db_session: Session) -> None:
    hr = make_user(db_session, username="hr1", role=UserRole.HR)
    make_candidate(db_session, owner=hr, full_name="Кандидат")

    _login(client, "hr1")

    summary = _summary(client)
    generated = datetime.fromisoformat(summary["generated_at"])
    assert generated.tzinfo is not None
    assert summary["stuck_days"] == STUCK_DAYS
    assert summary["horizon_days"] == 7
    assert isinstance(summary["by_stage"], list)
