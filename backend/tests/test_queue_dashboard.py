"""«Моя очередь» dashboard endpoint (in-memory SQLite).

What this file locks in
-----------------------
The dashboard cannot be a browser-side roll-up: a chart drawn from one page of
candidates presents a window as the whole queue — the defect the queue summary
already fixed. So ``GET /candidates/queue/dashboard`` computes every KPI and
every series server-side over the caller's **whole** scope, and this suite
exists to keep that promise true:

* **the page-size regression** — candidates far beyond the first page (and
  beyond the 100-row window the old client used) are counted;
* **scope** — personal for HR/manager/administrator/pilot; a manager or an
  administrator may switch to a colleague, an HR cannot widen the scope even
  by hand-crafting ``owner_id``;
* **periods and timezones** — ``today``/``week``/``all``, day, week and month
  boundaries, a midnight crossing, an empty period, a future-dated period;
* **honesty** — a metric the data model cannot express is ``null`` with a note
  (there is no vacancy entity in the project), never a fabricated zero;
* **consistency** — the funnel and the blocks agree with
  ``GET /candidates/queue/summary``, which the screen already trusts.

PostgreSQL mirrors live in ``tests/test_integration_candidates.py``.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import update
from sqlalchemy.orm import Session

from app import queue_dashboard as dashboard
from app.analytics_ledger import record_fact
from app.models import (
    CANDIDATE_STAGE_ORDER,
    CANDIDATE_STAGE_POSITION,
    AccessGrant,
    AccessGrantScope,
    AnalyticsFactType,
    Candidate,
    CandidateSource,
    CandidateStage,
    Event,
    EventStatus,
    EventType,
    Notification,
    NotificationPriority,
    NotificationSource,
    NotificationType,
    Reminder,
    ReminderImportance,
    ReminderStatus,
    User,
    UserRole,
)
from app.routers import candidates as candidates_router
from app.routers.candidates import QUEUE_CLOSED_STAGES
from tests.conftest import FIXTURE_PASSWORD, make_candidate, make_event, make_user

UTC = ZoneInfo("UTC")
MSK = ZoneInfo("Europe/Moscow")
URL = "/candidates/queue/dashboard"


@pytest.fixture(autouse=True)
def _clean_limiter() -> Iterator[None]:  # pragma: no cover - see test_candidates.py
    from app.routers.auth import reset_login_limiter

    reset_login_limiter()
    yield
    reset_login_limiter()


@pytest.fixture(autouse=True)
def _frozen_dashboard_clock(monkeypatch: pytest.MonkeyPatch) -> None:
    """Сводка считает «сейчас» по замороженным часам.

    Патчатся только часы самой сводки и соседнего ``/queue/summary`` — их
    тест сверяет между собой, и разные часы дали бы разные ответы на один
    вопрос. Часы сессий (``app.deps``, ``app.routers.auth``) остаются
    настоящими, иначе сессия, выданная в марте, выглядела бы истёкшей сегодня.
    Данные в тестах сеются через ``_now()``, то есть по той же точке, —
    окно и данные согласованы.
    """
    monkeypatch.setattr(dashboard, "utc_now", lambda: FROZEN_NOW)
    monkeypatch.setattr(candidates_router, "utc_now", lambda: FROZEN_NOW)


# --- Помощники ----------------------------------------------------------------


def _login(client: TestClient, username: str) -> None:
    response = client.post("/auth/login", json={"username": username, "password": FIXTURE_PASSWORD})
    assert response.status_code == 200, response.text


#: Замороженное «сейчас» для всего файла. Число бакетов на оси «Всё» —
#: месяцы, и оно зависит от календарной даты прогона: 400 дней назад от
#: 2026-10-05 — это 15 месяцев, от 2026-10-06 — уже 14. Тест с константой
#: `15` был зелёным ровно один день и стал красным 2026-10-06 (ревью раунда 8).
#: Поэтому часы сводки заморожены, а ожидания пересчитаны от этой даты.
FROZEN_NOW = datetime(2026, 3, 12, 10, 0, tzinfo=UTC)


def _now() -> datetime:
    """«Сейчас» в тестах: та же замороженная точка, что и у сервера сводки."""
    return FROZEN_NOW


def _parse(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _as_utc(value: datetime) -> datetime:
    """SQLite returns naive UTC; the tests compare against aware instants."""
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _set_times(
    db: Session,
    candidate: Candidate,
    *,
    created_at: datetime | None = None,
    updated_at: datetime | None = None,
) -> Candidate:
    """Write timestamps explicitly: ``updated_at`` carries an ``onupdate``
    default, so a plain attribute assignment would be overwritten."""
    values: dict[str, Any] = {}
    if created_at is not None:
        values["created_at"] = created_at
    if updated_at is not None:
        values["updated_at"] = updated_at
    if values:
        db.execute(update(Candidate).where(Candidate.id == candidate.id).values(**values))
        db.commit()
        db.refresh(candidate)
    return candidate


def _move(db: Session, candidate: Candidate, stage: CandidateStage, at: datetime) -> Candidate:
    """Set the current stage and record the ledger fact of the transition."""
    db.execute(
        update(Candidate)
        .where(Candidate.id == candidate.id)
        .values(stage=stage, stage_position=CANDIDATE_STAGE_POSITION[stage], updated_at=at)
    )
    owner_id = candidate.owner_user_id
    assert owner_id is not None
    record_fact(
        db,
        fact_type=AnalyticsFactType.STAGE_CHANGED,
        candidate_id=candidate.id,
        owner_user_id=owner_id,
        fact_at=at,
        stage_to=stage.value,
        source=candidate.source.value if candidate.source is not None else None,
    )
    db.commit()
    db.refresh(candidate)
    return candidate


def _seed(
    db: Session,
    *,
    owner: User,
    created_at: datetime,
    stage: CandidateStage = CandidateStage.NEW,
    source: CandidateSource = CandidateSource.SITE,
    position: str = "Монтажник РЭА",
    full_name: str = "Иванов Иван",
    updated_at: datetime | None = None,
) -> Candidate:
    candidate = make_candidate(
        db, owner=owner, source=source, position=position, full_name=full_name
    )
    _set_times(db, candidate, created_at=created_at, updated_at=updated_at or created_at)
    record_fact(
        db,
        fact_type=AnalyticsFactType.CANDIDATE_CREATED,
        candidate_id=candidate.id,
        owner_user_id=owner.id,
        fact_at=created_at,
        source=source.value,
    )
    if stage is not CandidateStage.NEW:
        _move(db, candidate, stage, created_at + timedelta(hours=1))
    else:
        db.commit()
    return candidate


def _reminder(
    db: Session,
    *,
    owner: User,
    assignee: User | None = None,
    due_at: datetime,
    status: ReminderStatus = ReminderStatus.ACTIVE,
    title: str = "Позвонить кандидату",
) -> Reminder:
    row = Reminder(
        owner_user_id=owner.id,
        assignee_user_id=(assignee or owner).id,
        title=title,
        due_at=due_at,
        timezone="Europe/Moscow",
        importance=ReminderImportance.NORMAL,
        status=status,
        completed_at=due_at if status == ReminderStatus.COMPLETED else None,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def _notify(
    db: Session, user: User, *, read: bool = False, dismissed: bool = False
) -> Notification:
    row = Notification(
        user_id=user.id,
        type=NotificationType.EVENT_ASSIGNED,
        title="Назначено событие",
        priority=NotificationPriority.NORMAL,
        source=NotificationSource.SYSTEM,
        read_at=_now() if read else None,
        dismissed_at=_now() if dismissed else None,
    )
    db.add(row)
    db.commit()
    return row


def _dash(client: TestClient, query: str = "") -> dict[str, Any]:
    response = client.get(f"{URL}{query}")
    assert response.status_code == 200, response.text
    return response.json()


def _bucket_of(instant: datetime, buckets: list[dict[str, Any]]) -> str | None:
    moment = _as_utc(instant)
    for bucket in buckets:
        if _parse(bucket["from"]) <= moment < _parse(bucket["to"]):
            return bucket["bucket"]
    return None


def _series_map(payload: dict[str, Any], key: str = "created_candidates_series") -> dict[str, Any]:
    return {row["bucket"]: row for row in payload[key]}


# --- Доступ и область видимости -----------------------------------------------


def test_unauthenticated_dashboard_gets_401(client: TestClient, db_session: Session) -> None:
    make_user(db_session, username="hr1", role=UserRole.HR)

    assert client.get(URL).status_code == 401


def test_hr_scope_excludes_foreign_candidates(client: TestClient, db_session: Session) -> None:
    hr = make_user(db_session, username="hr1", role=UserRole.HR)
    other = make_user(db_session, username="hr2", role=UserRole.HR)
    _seed(db_session, owner=hr, created_at=_now() - timedelta(days=1), full_name="Свой")
    _seed(
        db_session,
        owner=other,
        created_at=_now() - timedelta(days=1),
        full_name="Чужой",
        source=CandidateSource.REFERRAL,
    )
    _login(client, "hr1")

    payload = _dash(client)

    assert payload["scope"]["personal"] is True
    assert payload["kpis"]["new_candidates"] == 1
    assert [row["source"] for row in payload["sources"]] == ["site"]


def test_hr_cannot_widen_scope_with_owner_id(client: TestClient, db_session: Session) -> None:
    hr = make_user(db_session, username="hr1", role=UserRole.HR)
    other = make_user(db_session, username="hr2", role=UserRole.HR)
    _seed(db_session, owner=hr, created_at=_now() - timedelta(days=1), full_name="Свой")
    _seed(db_session, owner=other, created_at=_now() - timedelta(days=1), full_name="Чужой")
    _login(client, "hr1")

    payload = _dash(client, f"?owner_id={other.id}")

    # Игнорировать параметр молча — нельзя; HR остаётся в своей области.
    assert payload["scope"]["owner_id"] == str(hr.id)
    assert payload["kpis"]["new_candidates"] == 1


def test_manager_scope_is_personal_by_default(client: TestClient, db_session: Session) -> None:
    manager = make_user(db_session, username="manager1", role=UserRole.MANAGER)
    hr = make_user(db_session, username="hr1", role=UserRole.HR)
    _seed(db_session, owner=manager, created_at=_now() - timedelta(days=1), full_name="Свой")
    _seed(db_session, owner=hr, created_at=_now() - timedelta(days=1), full_name="Чужой")
    _login(client, "manager1")

    payload = _dash(client)

    assert payload["scope"]["owner_id"] == str(manager.id)
    assert payload["scope"]["personal"] is True
    assert payload["kpis"]["new_candidates"] == 1


def test_manager_may_switch_to_a_colleague(client: TestClient, db_session: Session) -> None:
    make_user(db_session, username="manager1", role=UserRole.MANAGER)
    hr = make_user(db_session, username="hr1", role=UserRole.HR)
    _seed(db_session, owner=hr, created_at=_now() - timedelta(days=1), full_name="Коллега")
    _login(client, "manager1")

    payload = _dash(client, f"?owner_id={hr.id}")

    assert payload["scope"]["owner_id"] == str(hr.id)
    assert payload["scope"]["personal"] is False
    assert payload["kpis"]["new_candidates"] == 1


def test_unknown_owner_id_is_422(client: TestClient, db_session: Session) -> None:
    make_user(db_session, username="manager1", role=UserRole.MANAGER)
    _login(client, "manager1")

    response = client.get(f"{URL}?owner_id={uuid.uuid4()}")

    assert response.status_code == 422


def test_administrator_and_pilot_share_the_personal_rule(
    client: TestClient, db_session: Session
) -> None:
    admin = make_user(db_session, username="admin1", role=UserRole.ADMIN)
    pilot = make_user(db_session, username="pilot1", role=UserRole.HR)
    db_session.add(AccessGrant(user_id=pilot.id, scope=AccessGrantScope.PILOT_FULL_ACCESS))
    db_session.commit()
    for owner in (admin, pilot):
        _seed(db_session, owner=owner, created_at=_now() - timedelta(days=1))
    _login(client, "admin1")

    assert _dash(client)["scope"]["personal"] is True

    _login(client, "pilot1")
    payload = _dash(client)
    assert payload["scope"]["personal"] is True
    # Полный доступ пилота не превращает «Мою очередь» в общую базу:
    # сводка считает только своих кандидатов.
    assert payload["kpis"]["new_candidates"] == 1


# --- Регрессия: данные дальше первой страницы ---------------------------------


def test_aggregate_counts_candidates_far_beyond_the_first_page(
    client: TestClient, db_session: Session
) -> None:
    """The whole point of the endpoint.

    The old client counted what it had downloaded: a page of 20 (or the 100
    most recently updated rows). The 120 candidates below include one created
    400 days ago — the oldest in the scope, i.e. the very row a window sorted
    by ``updated_at desc`` drops first, and the only candidate in the
    ``offer`` stage with the ``university`` source. If the aggregate misses
    it, the dashboard lies about the queue.
    """
    hr = make_user(db_session, username="hr1", role=UserRole.HR)
    moment = _now()
    ancient = _seed(
        db_session,
        owner=hr,
        created_at=moment - timedelta(days=400),
        updated_at=moment - timedelta(days=400),
        stage=CandidateStage.OFFER,
        source=CandidateSource.UNIVERSITY,
    )
    for index in range(119):
        _seed(
            db_session,
            owner=hr,
            created_at=moment - timedelta(hours=index + 1),
            # Все «давние» по обновлению: иначе блок внимания считает не их.
            updated_at=moment - timedelta(days=10),
        )
    _login(client, "hr1")

    payload = _dash(client, "?period=all")

    assert payload["kpis"]["new_candidates"] == 120
    assert {row["source"]: row["count"] for row in payload["sources"]}["university"] == 1
    stages = {row["stage"]: row["count"] for row in payload["funnel"]}
    assert stages["offer"] == 1
    assert stages["new"] == 119
    # И «Требуют внимания» находит именно старую строку, а не свежие 100.
    assert payload["attention_candidates_total"] == 120
    assert payload["attention_candidates"][0]["id"] == str(ancient.id)
    assert payload["truncated"] is False


def test_funnel_matches_the_queue_summary(client: TestClient, db_session: Session) -> None:
    """Two endpoints the same screen trusts must never disagree."""
    hr = make_user(db_session, username="hr1", role=UserRole.HR)
    moment = _now()
    _seed(db_session, owner=hr, created_at=moment - timedelta(days=1))
    _seed(
        db_session,
        owner=hr,
        created_at=moment - timedelta(days=2),
        stage=CandidateStage.INTERVIEW_DONE,
    )
    _seed(db_session, owner=hr, created_at=moment - timedelta(days=3), stage=CandidateStage.HIRED)
    _login(client, "hr1")

    payload = _dash(client, "?period=all")
    summary = client.get("/candidates/queue/summary").json()

    assert {row["stage"]: row["count"] for row in payload["funnel"]} == {
        row["stage"]: row["count"] for row in summary["by_stage"]
    }
    assert payload["attention_candidates_total"] == summary["stuck"]
    assert payload["upcoming_events_total"] == summary["upcoming_events_total"]
    assert [row["id"] for row in payload["attention_candidates"]] == [
        row["id"] for row in summary["stuck_sample"]
    ]


def test_closed_stages_match_the_queue_contract() -> None:
    """The aggregator duplicates ``QUEUE_CLOSED_STAGES`` on purpose (the router
    imports the aggregator, not vice versa) — pin the two together."""
    assert dashboard.DASHBOARD_CLOSED_STAGES == QUEUE_CLOSED_STAGES


# --- Периоды, границы суток, таймзоны -----------------------------------------


def test_today_counts_only_today_local(client: TestClient, db_session: Session) -> None:
    hr = make_user(db_session, username="hr1", role=UserRole.HR)
    moment = _now()
    _seed(db_session, owner=hr, created_at=moment)
    _seed(db_session, owner=hr, created_at=moment - timedelta(days=1))
    _login(client, "hr1")

    today = _dash(client, "?period=today")
    week = _dash(client, "?period=week")

    assert today["kpis"]["new_candidates"] == 1
    assert week["kpis"]["new_candidates"] == 2
    assert today["period"]["bucket_size"] == "hour"
    assert len(today["created_candidates_series"]) == 24
    assert week["period"]["bucket_size"] == "day"
    assert len(week["created_candidates_series"]) == 7


def test_all_period_is_bucketed_by_months(client: TestClient, db_session: Session) -> None:
    hr = make_user(db_session, username="hr1", role=UserRole.HR)
    moment = _now()
    _seed(db_session, owner=hr, created_at=moment - timedelta(days=400))
    _seed(db_session, owner=hr, created_at=moment - timedelta(days=40))
    _seed(db_session, owner=hr, created_at=moment)
    _login(client, "hr1")

    payload = _dash(client, "?period=all")

    assert payload["period"]["bucket_size"] == "month"
    # Ожидание — арифметика от замороженного «сейчас» (2026-03-12), а не
    # сегодняшняя дата: самая старая строка создана 2025-02-05, значит ось идёт
    # от февраля 2025 до марта 2026 включительно — 14 месяцев.
    labels = [bucket["label"] for bucket in payload["period"]["buckets"]]
    assert labels[0] == "02.2025"
    assert labels[-1] == "03.2026"
    assert len(payload["created_candidates_series"]) == 14
    assert payload["kpis"]["new_candidates"] == 3
    assert sum(row["value"] for row in payload["created_candidates_series"]) == 3


def test_all_period_bucket_count_follows_the_calendar_not_the_run_day() -> None:
    """Граница, из-за которой тест был зелёным ровно один день.

    400 дней — это 13,15 месяца, поэтому число месяцев на оси зависит от того,
    на какое число календаря попадает «сейчас»: 05.10.2026 даёт 15 бакетов,
    06.10.2026 — уже 14. Оба случая проверяются здесь с явным ``now``, чтобы
    арифметика оси была зафиксирована независимо от дня прогона: CI прошёл
    05.10.2026 и «доказал» 15, а на следующий день набор тестов стал красным.
    """
    for day, expected in (("2026-10-05", 15), ("2026-10-06", 14)):
        moment = datetime.fromisoformat(day).replace(hour=10, tzinfo=UTC)
        window = dashboard.resolve_window(
            period="all",
            timezone="Europe/Moscow",
            now=moment,
            earliest=moment - timedelta(days=400),
        )
        assert len(window.buckets) == expected, day


def test_all_period_is_capped_at_two_years(client: TestClient, db_session: Session) -> None:
    """«Всё» — это ось, а не бесконечность: она ограничена 24 месяцами.

    Числа обязаны следовать за осью (KPI не может показывать строку, которой
    нет на графике), а флаг ``capped`` позволяет интерфейсу сказать об этом
    вслух: «показаны последние 24 месяца».
    """
    hr = make_user(db_session, username="hr1", role=UserRole.HR)
    _seed(db_session, owner=hr, created_at=_now() - timedelta(days=365 * 12))
    _seed(db_session, owner=hr, created_at=_now() - timedelta(days=30))
    _login(client, "hr1")

    payload = _dash(client, "?period=all")

    assert payload["period"]["capped"] is True
    assert len(payload["created_candidates_series"]) == dashboard.MAX_ALL_MONTHS
    assert payload["kpis"]["new_candidates"] == 1


def test_midnight_crossing_lands_in_the_right_day_bucket(
    client: TestClient, db_session: Session
) -> None:
    """A minute before and a minute after local midnight are different days.

    The expected bucket is located from the response itself, so the test stays
    valid even if the wall clock crosses midnight between seeding and request.
    """
    hr = make_user(db_session, username="hr1", role=UserRole.HR)
    _login(client, "hr1")
    buckets = _dash(client, "?period=week&timezone=UTC")["period"]["buckets"]
    midnight = _parse(buckets[-1]["from"])
    just_before = _seed(db_session, owner=hr, created_at=midnight - timedelta(minutes=1))
    just_after = _seed(db_session, owner=hr, created_at=midnight + timedelta(minutes=1))

    payload = _dash(client, "?period=week&timezone=UTC")

    created = _series_map(payload)
    before_bucket = _bucket_of(just_before.created_at, payload["period"]["buckets"])
    after_bucket = _bucket_of(just_after.created_at, payload["period"]["buckets"])
    assert before_bucket is not None
    assert after_bucket is not None
    assert before_bucket != after_bucket
    assert created[before_bucket]["value"] == 1
    assert created[after_bucket]["value"] == 1


def test_timezone_moves_the_day_boundary(client: TestClient, db_session: Session) -> None:
    """23:30 UTC is already tomorrow in Moscow: the bucket must follow the tz."""
    hr = make_user(db_session, username="hr1", role=UserRole.HR)
    _login(client, "hr1")
    buckets = _dash(client, "?period=week&timezone=Europe/Moscow")["period"]["buckets"]
    msk_midnight = _parse(buckets[-1]["from"])  # 21:00 UTC предыдущих суток
    assert msk_midnight.astimezone(MSK).hour == 0
    late_utc = _seed(
        db_session, owner=hr, created_at=msk_midnight - timedelta(minutes=30)
    )  # 20:30 UTC = 23:30 МСК — вчера по Москве, сегодня по UTC

    payload = _dash(client, "?period=week&timezone=Europe/Moscow")
    msk_bucket = _bucket_of(late_utc.created_at, payload["period"]["buckets"])
    created_msk = _series_map(payload)

    payload_utc = _dash(client, "?period=week&timezone=UTC")
    created_utc = _series_map(
        payload_utc,
    )
    utc_bucket = _bucket_of(late_utc.created_at, payload_utc["period"]["buckets"])

    assert msk_bucket is not None and utc_bucket is not None
    assert created_msk[msk_bucket]["value"] == 1
    # По UTC кандидат попадает в тот же день, но это другой бакет оси недели:
    # значение обязано совпасть, а границы суток — нет.
    assert created_utc[utc_bucket]["value"] == 1
    assert _parse(payload["period"]["from"]) < _parse(payload_utc["period"]["from"])


def test_resolve_window_moscow_day_boundary() -> None:
    """Unit level: 21:30 UTC on the 28th is already the 29th in Moscow."""
    moment = datetime(2026, 9, 28, 21, 30, tzinfo=UTC)
    window = dashboard.resolve_window(period="today", timezone="Europe/Moscow", now=moment)

    assert window.from_dt == datetime(2026, 9, 28, 21, 0, tzinfo=UTC)
    assert window.to_dt == datetime(2026, 9, 29, 21, 0, tzinfo=UTC)
    assert len(window.buckets) == 24
    assert window.buckets[0].label == "00:00"
    assert window.buckets[0].from_dt.astimezone(MSK).day == 29


def test_resolve_window_week_has_seven_days() -> None:
    moment = datetime(2026, 9, 30, 10, 0, tzinfo=UTC)
    window = dashboard.resolve_window(period="week", timezone="Europe/Moscow", now=moment)

    assert len(window.buckets) == 7
    assert window.buckets[-1].to_dt - window.buckets[0].from_dt == timedelta(days=7)
    assert [bucket.key for bucket in window.buckets] == [
        "2026-09-24",
        "2026-09-25",
        "2026-09-26",
        "2026-09-27",
        "2026-09-28",
        "2026-09-29",
        "2026-09-30",
    ]


def test_resolve_window_survives_a_dst_transition() -> None:
    """Berlin moves to summer time on 2026-03-29: the day is 23 hours long.

    Arithmetic on the UTC instant keeps the buckets contiguous — a naive
    ``+24h`` would drift and silently show a wrong axis.
    """
    moment = datetime(2026, 3, 30, 10, 0, tzinfo=UTC)
    window = dashboard.resolve_window(period="week", timezone="Europe/Berlin", now=moment)

    assert len(window.buckets) == 7
    assert window.buckets[5].to_dt - window.buckets[5].from_dt == timedelta(hours=23)
    for previous, current in zip(window.buckets, window.buckets[1:], strict=False):
        assert previous.to_dt == current.from_dt


def test_unknown_timezone_and_period_are_422(client: TestClient, db_session: Session) -> None:
    make_user(db_session, username="hr1", role=UserRole.HR)
    _login(client, "hr1")

    assert client.get(f"{URL}?timezone=Mars/Olympus").status_code == 422
    assert client.get(f"{URL}?period=month").status_code == 422


# --- Пустые и будущие периоды --------------------------------------------------


def test_empty_period_returns_a_full_axis_of_zeroes(
    client: TestClient, db_session: Session
) -> None:
    """No data is not «no chart»: the axis exists, the values are zero, the
    rates are ``null`` and the notes explain the metric that has no source."""
    make_user(db_session, username="hr1", role=UserRole.HR)
    _login(client, "hr1")

    payload = _dash(client, "?period=week")

    assert payload["kpis"]["new_candidates"] == 0
    assert len(payload["created_candidates_series"]) == 7
    assert all(row["value"] == 0 for row in payload["created_candidates_series"])
    assert payload["kpis"]["interview_conversion"] == {
        "numerator": 0,
        "denominator": 0,
        "rate": None,
    }
    assert payload["kpis"]["average_hiring_days"] == {"value": None, "sample": 0}
    assert payload["sources"] == []
    assert payload["funnel"] == [
        {"stage": stage.value, "count": 0} for stage in CANDIDATE_STAGE_ORDER
    ]
    assert payload["upcoming_events"] == []
    assert payload["attention_candidates"] == []
    assert payload["unread_notifications"] == 0
    # Нет сущности «вакансия» — честно «—» с пояснением, а не ноль.
    assert payload["kpis"]["active_vacancies"] is None
    assert payload["kpi_notes"]["active_vacancies"]


def test_future_dated_period_is_empty(client: TestClient, db_session: Session) -> None:
    """A start date in the future is work planned, not an exit that happened."""
    hr = make_user(db_session, username="hr1", role=UserRole.HR)
    moment = _now()
    candidate = _seed(db_session, owner=hr, created_at=moment - timedelta(days=10))
    db_session.execute(
        update(Candidate).where(Candidate.id == candidate.id).values(start_date=moment.date())
    )
    db_session.commit()
    _login(client, "hr1")

    payload = _dash(client, "?period=today")

    exits = {row["bucket"]: row["exits"] for row in payload["hiring_dynamics"]}
    assert sum(exits.values()) == 1
    assert payload["kpis"]["weekly_exits"] == 1


def test_weekly_exits_look_forward_seven_days(client: TestClient, db_session: Session) -> None:
    hr = make_user(db_session, username="hr1", role=UserRole.HR)
    today = _now().date()
    for offset in (-1, 0, 2, 6, 7, 20):
        candidate = _seed(db_session, owner=hr, created_at=_now() - timedelta(days=30))
        db_session.execute(
            update(Candidate)
            .where(Candidate.id == candidate.id)
            .values(start_date=today + timedelta(days=offset))
        )
    db_session.commit()
    _login(client, "hr1")

    payload = _dash(client, "?period=today")

    # Окно [сегодня, сегодня + 7 дней): −1 день (вчера), +7 и +20 — вне окна.
    assert payload["kpis"]["weekly_exits"] == 3


# --- KPI: метрики --------------------------------------------------------------


def test_new_candidates_and_sources(client: TestClient, db_session: Session) -> None:
    hr = make_user(db_session, username="hr1", role=UserRole.HR)
    moment = _now()
    _seed(db_session, owner=hr, created_at=moment - timedelta(days=1), source=CandidateSource.SITE)
    _seed(db_session, owner=hr, created_at=moment - timedelta(days=1), source=CandidateSource.SITE)
    _seed(
        db_session,
        owner=hr,
        created_at=moment - timedelta(days=1),
        source=CandidateSource.REFERRAL,
    )
    _seed(
        db_session,
        owner=hr,
        created_at=moment - timedelta(days=40),
        source=CandidateSource.HH_MANUAL,
    )
    _login(client, "hr1")

    payload = _dash(client, "?period=week")

    assert payload["kpis"]["new_candidates"] == 3
    assert payload["sources"] == [
        {"source": "site", "label": "Сайт компании", "count": 2},
        {"source": "referral", "label": "Рекомендация", "count": 1},
    ]


def test_interviews_count_planned_events_and_ignore_cancelled(
    client: TestClient, db_session: Session
) -> None:
    hr = make_user(db_session, username="hr1", role=UserRole.HR)
    moment = _now()
    candidate = _seed(db_session, owner=hr, created_at=moment - timedelta(days=10))
    make_event(
        db_session,
        candidate=candidate,
        author=hr,
        assignee=hr,
        type_=EventType.INTERVIEW,
        starts_at=moment + timedelta(hours=2),
        ends_at=moment + timedelta(hours=3),
    )
    cancelled = make_event(
        db_session,
        candidate=candidate,
        author=hr,
        assignee=hr,
        type_=EventType.INTERVIEW,
        title="Отменённое",
        starts_at=moment + timedelta(hours=4),
        ends_at=moment + timedelta(hours=5),
    )
    db_session.execute(
        update(Event)
        .where(Event.id == cancelled.id)
        .values(status=EventStatus.CANCELLED, cancelled_at=moment)
    )
    db_session.commit()
    make_event(
        db_session,
        candidate=candidate,
        author=hr,
        assignee=hr,
        type_=EventType.CALL,
        title="Звонок",
        starts_at=moment + timedelta(hours=6),
        ends_at=moment + timedelta(hours=7),
    )
    _login(client, "hr1")

    today = _dash(client, "?period=today")
    assert today["kpis"]["interviews"] == 1
    # И событие попадает в «Ближайшие события».
    assert today["upcoming_events_total"] == 2


def test_interview_conversion_is_a_cohort(client: TestClient, db_session: Session) -> None:
    """4 candidates created in the period, one of them reached an interview —
    even though the interview itself happened later (a conversion of this
    cohort, not of the day it happened)."""
    hr = make_user(db_session, username="hr1", role=UserRole.HR)
    moment = _now()
    reached = _seed(db_session, owner=hr, created_at=moment - timedelta(days=3))
    _move(db_session, reached, CandidateStage.INTERVIEW_SCHEDULED, moment - timedelta(days=1))
    for _ in range(3):
        _seed(db_session, owner=hr, created_at=moment - timedelta(days=3))
    _login(client, "hr1")

    payload = _dash(client, "?period=week")

    assert payload["kpis"]["interview_conversion"] == {
        "numerator": 1,
        "denominator": 4,
        "rate": 25.0,
    }
    series = _series_map(payload, "interview_conversion_series")
    assert sum(row["numerator"] for row in series.values()) == 1
    assert sum(row["denominator"] for row in series.values()) == 4


def test_average_hiring_days_carries_the_sample(client: TestClient, db_session: Session) -> None:
    hr = make_user(db_session, username="hr1", role=UserRole.HR)
    moment = _now()
    fast = _seed(db_session, owner=hr, created_at=moment - timedelta(days=20))
    _move(db_session, fast, CandidateStage.HIRED, moment - timedelta(days=10))
    slow = _seed(db_session, owner=hr, created_at=moment - timedelta(days=60))
    _move(db_session, slow, CandidateStage.STARTED, moment - timedelta(days=20))
    _seed(db_session, owner=hr, created_at=moment - timedelta(days=5))
    _login(client, "hr1")

    payload = _dash(client, "?period=all")

    assert payload["kpis"]["average_hiring_days"]["sample"] == 2
    assert payload["kpis"]["average_hiring_days"]["value"] == 25.0
    assert payload["kpis"]["new_candidates"] == 3


def test_hiring_dynamics_combines_exits_and_speed(client: TestClient, db_session: Session) -> None:
    hr = make_user(db_session, username="hr1", role=UserRole.HR)
    moment = _now()
    candidate = _seed(db_session, owner=hr, created_at=moment - timedelta(days=12))
    _move(db_session, candidate, CandidateStage.HIRED, moment - timedelta(days=2))
    db_session.execute(
        update(Candidate)
        .where(Candidate.id == candidate.id)
        .values(start_date=moment.date())  # завтрашний выход вне окна недели
    )
    db_session.commit()
    _login(client, "hr1")

    payload = _dash(client, "?period=week")
    rows = {row["bucket"]: row for row in payload["hiring_dynamics"]}
    hired_row = next(row for row in rows.values() if row["hired"] == 1)

    assert hired_row["avg_hiring_days"] == 10.0
    assert sum(row["exits"] for row in rows.values()) == 1
    assert sum(row["hired"] for row in rows.values()) == 1


def test_tasks_are_active_reminders_assigned_to_the_caller(
    client: TestClient, db_session: Session
) -> None:
    """There is no «task» entity in the project: a task is an active reminder
    assigned to me. Completed and cancelled ones are history, not work."""
    hr = make_user(db_session, username="hr1", role=UserRole.HR)
    colleague = make_user(db_session, username="hr2", role=UserRole.HR)
    _login(client, "hr1")
    # Границы суток берём у сервера: иначе тест зависит от времени запуска.
    day_start = _parse(_dash(client, "?period=today")["period"]["from"])
    # Задача «на сегодня»: гарантированно в будущем (иначе она уже
    # просрочена) и гарантированно внутри окна суток.
    due_today = min(
        max(_now() + timedelta(minutes=30), day_start),
        day_start + timedelta(hours=23, minutes=59),
    )
    _reminder(db_session, owner=hr, due_at=due_today, title="Сегодня")
    _reminder(db_session, owner=hr, due_at=day_start - timedelta(hours=2), title="Просрочено")
    _reminder(
        db_session,
        owner=hr,
        due_at=day_start - timedelta(days=5),
        title="Завершено",
        status=ReminderStatus.COMPLETED,
    )
    _reminder(db_session, owner=colleague, due_at=day_start - timedelta(hours=1), title="Чужое")

    payload = _dash(client, "?period=today")

    assert payload["kpis"]["my_tasks"] == 1
    assert payload["kpis"]["overdue_tasks"] == 1
    assert [row["title"] for row in payload["tasks_due"]] == ["Сегодня"]
    assert [row["title"] for row in payload["tasks_overdue"]] == ["Просрочено"]


def test_unread_notifications_ignore_read_and_dismissed(
    client: TestClient, db_session: Session
) -> None:
    hr = make_user(db_session, username="hr1", role=UserRole.HR)
    _notify(db_session, hr)
    _notify(db_session, hr)
    _notify(db_session, hr, read=True)
    _notify(db_session, hr, dismissed=True)
    _login(client, "hr1")

    assert _dash(client)["unread_notifications"] == 2


def test_deleted_candidates_never_count(client: TestClient, db_session: Session) -> None:
    hr = make_user(db_session, username="hr1", role=UserRole.HR)
    moment = _now()
    alive = _seed(
        db_session,
        owner=hr,
        created_at=moment - timedelta(days=1),
        updated_at=moment - timedelta(days=5),
    )
    make_candidate(db_session, owner=hr, deleted=True, full_name="Удалённый")
    _login(client, "hr1")

    payload = _dash(client, "?period=all")

    assert payload["kpis"]["new_candidates"] == 1
    assert payload["attention_candidates"][0]["id"] == str(alive.id)


# --- Фильтры -------------------------------------------------------------------


def test_filters_reach_every_widget(client: TestClient, db_session: Session) -> None:
    hr = make_user(db_session, username="hr1", role=UserRole.HR)
    moment = _now()
    _seed(
        db_session,
        owner=hr,
        created_at=moment - timedelta(days=1),
        position="Монтажник РЭА",
        source=CandidateSource.SITE,
    )
    _seed(
        db_session,
        owner=hr,
        created_at=moment - timedelta(days=1),
        position="Инженер",
        source=CandidateSource.REFERRAL,
    )
    _seed(
        db_session,
        owner=hr,
        created_at=moment - timedelta(days=1),
        position="Монтажник РЭА",
        source=CandidateSource.REFERRAL,
        stage=CandidateStage.INTERVIEW_DONE,
    )
    _login(client, "hr1")

    payload = _dash(client, "?period=week&position=Монтажник РЭА")

    assert payload["kpis"]["new_candidates"] == 2
    assert {row["source"] for row in payload["sources"]} == {"site", "referral"}
    assert {row["stage"] for row in payload["funnel"] if row["count"]} == {"new", "interview_done"}

    only_referral = _dash(client, "?period=week&position=Монтажник РЭА&source=referral")
    assert only_referral["kpis"]["new_candidates"] == 1
    assert [row["source"] for row in only_referral["sources"]] == ["referral"]

    only_stage = _dash(client, f"?period=week&stage={CandidateStage.INTERVIEW_DONE.value}")
    assert only_stage["kpis"]["new_candidates"] == 1
    assert {row["stage"] for row in only_stage["funnel"] if row["count"]} == {"interview_done"}

    nothing = _dash(client, "?period=week&position=Директор")
    assert nothing["kpis"]["new_candidates"] == 0
    assert nothing["sources"] == []


def test_position_filter_is_normalized(client: TestClient, db_session: Session) -> None:
    hr = make_user(db_session, username="hr1", role=UserRole.HR)
    _seed(db_session, owner=hr, created_at=_now() - timedelta(days=1), position="Монтажник  РЭА")
    _login(client, "hr1")

    assert (
        _dash(client, "?period=week&position=%20монтажник%20рэа%20")["kpis"]["new_candidates"] == 1
    )
