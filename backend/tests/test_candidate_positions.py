"""Distinct-position directory for the «Должность» filter (in-memory SQLite).

What this file locks in
-----------------------
The «Должность» filter on the «Кандидаты» and «Канбан» screens is a
``<select>`` with no free text input, and its options used to be collected
from the **first page** of ``GET /candidates`` (100 rows). A position that
only exists further down the base was therefore impossible to select, even
though the server supported the filter perfectly well.

``GET /candidates/positions`` builds the option list in SQL over the whole
visible scope instead. These tests pin the scope rules (HR/owner/deleted),
the deduplication rules (the same ``normalize_position`` the filter uses) and
the fact that no page boundary can hide an option.

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

from app.models import Candidate, UserRole
from tests.conftest import FIXTURE_PASSWORD, make_candidate, make_user


@pytest.fixture(autouse=True)
def _clean_limiter() -> Iterator[None]:  # pragma: no cover - see test_candidates.py
    from app.routers.auth import reset_login_limiter

    reset_login_limiter()
    yield
    reset_login_limiter()


def _login(client: TestClient, username: str) -> None:
    response = client.post("/auth/login", json={"username": username, "password": FIXTURE_PASSWORD})
    assert response.status_code == 200, response.text


def _options(response: httpx.Response) -> list[str]:
    return [item["position"] for item in response.json()["items"]]


def _age(db: Session, candidate_id: uuid.UUID, *, updated_at: datetime) -> None:
    """Move ``updated_at`` into the past (``onupdate`` would overwrite a plain
    attribute assignment, so the value is written with an explicit UPDATE)."""
    db.execute(update(Candidate).where(Candidate.id == candidate_id).values(updated_at=updated_at))
    db.commit()


def test_unauthenticated_gets_401(client: TestClient, db_session: Session) -> None:
    make_user(db_session, username="hr1", role=UserRole.HR)

    assert client.get("/candidates/positions").status_code == 401


def test_position_beyond_the_first_list_page_is_offered(
    client: TestClient, db_session: Session
) -> None:
    """The regression this endpoint exists for.

    130 candidates: the only «Дальняя должность» sits in the last row. The old
    client-side scan of one page of 100 never saw it, so the value could not
    be chosen; the directory must offer it — and the filter must then match it.
    """
    hr = make_user(db_session, username="hr1", role=UserRole.HR)
    far = make_candidate(
        db_session, owner=hr, full_name="Дальний кандидат", position="Дальняя должность"
    )
    # Самый «старый» по updated_at: при sort=updated_at desc он окажется
    # за пределами первой страницы — именно там его не видел старый код.
    _age(db_session, far.id, updated_at=datetime.now(ZoneInfo("UTC")) - timedelta(days=30))
    for index in range(129):
        make_candidate(
            db_session, owner=hr, full_name=f"Кандидат {index}", position="Монтажник РЭА"
        )

    _login(client, "hr1")

    directory = client.get("/candidates/positions")
    assert directory.status_code == 200
    assert _options(directory) == ["Дальняя должность", "Монтажник РЭА"]

    # The page the old implementation looked at really does not contain it …
    first_page = client.get("/candidates?sort=updated_at&direction=desc&limit=100&offset=0")
    assert first_page.status_code == 200
    assert "Дальняя должность" not in [item["position"] for item in first_page.json()["items"]]

    # … but the server-side filter has always worked, so the option is usable.
    filtered = client.get("/candidates?position=Дальняя должность")
    assert filtered.status_code == 200
    assert filtered.json()["total"] == 1
    assert filtered.json()["items"][0]["full_name"] == "Дальний кандидат"


def test_case_and_whitespace_duplicates_collapse_into_one_option(
    client: TestClient, db_session: Session
) -> None:
    hr = make_user(db_session, username="hr1", role=UserRole.HR)
    make_candidate(db_session, owner=hr, full_name="А", position="Монтажник РЭА")
    make_candidate(db_session, owner=hr, full_name="Б", position="  монтажник рэа  ")
    make_candidate(db_session, owner=hr, full_name="В", position="МОНТАЖНИК РЭА")

    _login(client, "hr1")

    directory = client.get("/candidates/positions")
    assert directory.status_code == 200
    items = directory.json()["items"]
    # Одна опция на все написания — иначе в селекте появятся три одинаковых.
    assert len(items) == 1
    assert items[0]["count"] == 3
    assert directory.json()["total"] == 1

    # Любое из написаний фильтрует те же три строки: опция и фильтр согласованы.
    for spelling in ("Монтажник РЭА", "монтажник рэа", "  МОНТАЖНИК РЭА "):
        listing = client.get(f"/candidates?position={spelling}")
        assert listing.status_code == 200
        assert listing.json()["total"] == 3


def test_hr_never_receives_positions_of_other_users(
    client: TestClient, db_session: Session
) -> None:
    hr1 = make_user(db_session, username="hr1", role=UserRole.HR)
    hr2 = make_user(db_session, username="hr2", role=UserRole.HR)
    make_candidate(db_session, owner=hr1, position="Своя должность")
    make_candidate(db_session, owner=hr2, position="Чужая должность")

    _login(client, "hr1")

    directory = client.get("/candidates/positions")
    assert _options(directory) == ["Своя должность"]

    # Явный owner_id чужого пользователя не расширяет область для HR.
    narrowed = client.get(f"/candidates/positions?owner_id={hr2.id}")
    assert _options(narrowed) == ["Своя должность"]


def test_manager_scope_and_owner_filter(client: TestClient, db_session: Session) -> None:
    hr1 = make_user(db_session, username="hr1", role=UserRole.HR)
    hr2 = make_user(db_session, username="hr2", role=UserRole.HR)
    make_user(db_session, username="mgr", role=UserRole.MANAGER)
    make_candidate(db_session, owner=hr1, position="Должность А")
    make_candidate(db_session, owner=hr2, position="Должность Б")

    _login(client, "mgr")

    everything = client.get("/candidates/positions")
    assert _options(everything) == ["Должность А", "Должность Б"]

    narrowed = client.get(f"/candidates/positions?owner_id={hr1.id}")
    assert _options(narrowed) == ["Должность А"]

    unknown = client.get(f"/candidates/positions?owner_id={uuid.uuid4()}")
    assert unknown.status_code == 200
    assert unknown.json()["items"] == []
    assert unknown.json()["total"] == 0


def test_deleted_view_scope(client: TestClient, db_session: Session) -> None:
    hr = make_user(db_session, username="hr1", role=UserRole.HR)
    make_candidate(db_session, owner=hr, position="Рабочая должность")
    make_candidate(db_session, owner=hr, position="Удалённая должность", deleted=True)

    _login(client, "hr1")

    alive = client.get("/candidates/positions")
    assert _options(alive) == ["Рабочая должность"]

    deleted = client.get("/candidates/positions?include_deleted=true")
    assert _options(deleted) == ["Удалённая должность"]


def test_empty_positions_are_not_offered(client: TestClient, db_session: Session) -> None:
    hr = make_user(db_session, username="hr1", role=UserRole.HR)
    make_candidate(db_session, owner=hr, position="")
    make_candidate(db_session, owner=hr, position="   ")
    make_candidate(db_session, owner=hr, position="Инженер")

    _login(client, "hr1")

    directory = client.get("/candidates/positions")
    assert _options(directory) == ["Инженер"]


def test_limit_truncates_the_payload_but_total_counts_every_value(
    client: TestClient, db_session: Session
) -> None:
    hr = make_user(db_session, username="hr1", role=UserRole.HR)
    for name in ("Должность А", "Должность Б", "Должность В", "Должность Г", "Должность Д"):
        make_candidate(db_session, owner=hr, position=name)

    _login(client, "hr1")

    limited = client.get("/candidates/positions?limit=2")
    assert limited.status_code == 200
    assert len(limited.json()["items"]) == 2
    # Клиент видит, что список обрезан, и не выдаёт его за полный.
    assert limited.json()["total"] == 5
    assert limited.json()["truncated"] is True

    full = client.get("/candidates/positions?limit=100")
    assert len(full.json()["items"]) == 5
    assert full.json()["truncated"] is False


def test_order_is_deterministic_and_independent_of_case(
    client: TestClient, db_session: Session
) -> None:
    """Сортировка — по нормализованному значению в Python, а не по колляции БД.

    Кириллица в SQLite и PostgreSQL сортируется по-разному; порядок в селекте
    обязан совпадать. Регистр не влияет на порядок (нормализация — casefold).
    """
    hr = make_user(db_session, username="hr1", role=UserRole.HR)
    for name in ("электрик", "Монтажник РЭА", "аналитик", "Бухгалтер"):
        make_candidate(db_session, owner=hr, position=name)

    _login(client, "hr1")

    directory = client.get("/candidates/positions")
    assert _options(directory) == ["аналитик", "Бухгалтер", "Монтажник РЭА", "электрик"]


def test_positions_do_not_leak_personal_data_of_candidates(
    client: TestClient, db_session: Session
) -> None:
    """В справочник уходят только должности и счётчики — не имена и не телефоны."""
    hr = make_user(db_session, username="hr1", role=UserRole.HR)
    make_candidate(db_session, owner=hr, full_name="Петров Пётр", position="Инженер")

    _login(client, "hr1")

    payload = client.get("/candidates/positions").text
    assert "Петров" not in payload
    assert client.get("/candidates/positions").json()["items"][0] == {
        "position": "Инженер",
        "count": 1,
    }
