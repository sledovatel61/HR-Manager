"""Integration-тесты импорта графика выхода из Excel на реальном PostgreSQL.

Зеркало ключевых сценариев ``test_schedule_import.py`` (проверка без записи,
подтверждённый импорт, повтор без дублей, неоднозначное ФИО, атомарный
откат, права и аудит), но на продуктовой схеме: новые таблицы
``schedule_imports``/``schedule_import_rows`` из ревизии ``0018``, расширенный
CHECK источника кандидатов (``excel_import``), native UUID/Date/Time.

Run with::

    TEST_DATABASE_URL=postgresql+psycopg://user:pass@host:5432/db \
        pytest -m integration -v
"""

from __future__ import annotations

import io
import json
import threading
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from datetime import time
from pathlib import Path
from uuid import uuid4

import httpx
import pytest
from fastapi.testclient import TestClient
from openpyxl import load_workbook
from sqlalchemy import Engine, func, select
from sqlalchemy.orm import Session

from app.config import Settings
from app.main import create_app
from app.models import (
    AuditAction,
    AuditEvent,
    Candidate,
    CandidateSource,
    CandidateStage,
    ScheduleEntry,
    ScheduleImport,
    ScheduleImportRow,
    UserRole,
)
from app.routers.auth import reset_login_limiter
from tests.conftest import FIXTURE_PASSWORD, make_candidate, make_user

pytestmark = pytest.mark.integration

FIXTURE_PATH = Path(__file__).parent / "fixtures" / "work_schedule_sample.xlsx"


@pytest.fixture(autouse=True)
def _clean_limiter() -> Iterator[None]:
    reset_login_limiter()
    yield
    reset_login_limiter()


def _fixture_bytes() -> bytes:
    return FIXTURE_PATH.read_bytes()


def _same_rows_different_workbook_bytes() -> bytes:
    """Keep source rows identical while changing the file fingerprint."""

    workbook = load_workbook(io.BytesIO(_fixture_bytes()))
    workbook.properties.title = "Параллельная версия того же графика"
    output = io.BytesIO()
    workbook.save(output)
    workbook.close()
    return output.getvalue()


def _login(client: TestClient, username: str) -> str:
    response = client.post("/auth/login", json={"username": username, "password": FIXTURE_PASSWORD})
    assert response.status_code == 200, response.text
    return response.json()["csrf_token"]


def _upload(client: TestClient, payload: bytes, csrf: str) -> httpx.Response:
    return client.post(
        "/work-schedule/import/preview",
        files={"file": ("график.xlsx", io.BytesIO(payload), "application/octet-stream")},
        headers={"X-CSRF-Token": csrf},
    )


def _confirm(
    client: TestClient, payload: bytes, csrf: str, decisions: list[dict] | None = None
) -> httpx.Response:
    return client.post(
        "/work-schedule/import",
        files={"file": ("график.xlsx", io.BytesIO(payload), "application/octet-stream")},
        data={"decisions": json.dumps({"decisions": decisions or []})},
        headers={"X-CSRF-Token": csrf},
    )


def test_preview_is_read_only_on_postgres(pg_client: TestClient, pg_db: Session) -> None:
    hr = make_user(pg_db, username="hr1", role=UserRole.HR)
    existing = make_candidate(pg_db, owner=hr, full_name="Тестова Анна Ивановна")
    csrf = _login(pg_client, "hr1")

    response = _upload(pg_client, _fixture_bytes(), csrf)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["summary"]["rows_total"] == 29
    testova = next(row for row in body["rows"] if row["full_name"] == "Тестова Анна Ивановна")
    assert testova["suggested_action"] == "match"
    assert testova["match"]["candidate_id"] == str(existing.id)

    # Превью ничего не пишет даже на реальной БД.
    assert pg_db.scalar(select(func.count()).select_from(Candidate)) == 1
    assert pg_db.scalar(select(func.count()).select_from(ScheduleEntry)) == 0
    assert pg_db.scalar(select(func.count()).select_from(ScheduleImport)) == 0


def test_confirmed_import_round_trips_on_postgres(pg_client: TestClient, pg_db: Session) -> None:
    hr = make_user(pg_db, username="hr1", role=UserRole.HR)
    csrf = _login(pg_client, "hr1")

    response = _confirm(pg_client, _fixture_bytes(), csrf)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["created"] >= 20
    assert body["service_created"] == 5

    # Импортёр указан автором импорта, но не назначается владельцем
    # импортированных кандидатов автоматически; дата/время дошли до БД.
    created = list(
        pg_db.scalars(
            select(Candidate).where(Candidate.source == CandidateSource.EXCEL_IMPORT)
        ).all()
    )
    assert len(created) == body["created"]
    assert all(candidate.owner_user_id is None for candidate in created)
    testova = next(
        candidate for candidate in created if candidate.full_name == "Тестова Анна Ивановна"
    )
    assert testova.start_date is not None
    assert testova.start_date.isoformat() == "2026-08-10"
    assert testova.start_time == time(9, 30)

    # Служебные строки видны в графике вместе с кандидатами.
    listing = pg_client.get("/work-schedule?from=2026-08-10&to=2026-08-14").json()
    kinds = {item["kind"] for item in listing["items"]}
    assert kinds == {"candidate", "entry"}
    assert any(item["display_name"] == "Увольнение" for item in listing["items"])

    # Связи импорт → сущности сохранены (провенанс).
    import_record = pg_db.scalar(select(ScheduleImport))
    assert import_record is not None and import_record.created_by_user_id == hr.id
    links = pg_db.scalars(
        select(ScheduleImportRow).where(ScheduleImportRow.import_id == import_record.id)
    ).all()
    assert (
        len(links) == body["created"] + body["service_created"] + body["updated"] + body["matched"]
    )

    # Повтор того же ФИО внутри файла: один кандидат, вторая строка обновляет
    # поля выхода той же карточки (никаких тихих дублей).
    dup_rows = [
        item for item in body["rows"] if item["reason"] and "повтор ФИО в файле" in item["reason"]
    ]
    assert len(dup_rows) == 1
    assert dup_rows[0]["result"] in ("updated", "matched")
    odnofam = pg_db.scalars(
        select(Candidate).where(Candidate.full_name == "Однофамилев Константин")
    ).all()
    assert len(odnofam) == 1
    assert dup_rows[0]["candidate_id"] == str(odnofam[0].id)

    # Агрегат аудита без ФИО/телефонов.
    audit = pg_db.scalar(
        select(AuditEvent).where(AuditEvent.action == AuditAction.WORK_SCHEDULE_IMPORTED)
    )
    assert audit is not None and "created=" in (audit.details or "")
    assert "Тестова" not in (audit.details or "")


def test_reimport_is_idempotent_on_postgres(pg_client: TestClient, pg_db: Session) -> None:
    make_user(pg_db, username="hr1", role=UserRole.HR)
    csrf = _login(pg_client, "hr1")

    first = _confirm(pg_client, _fixture_bytes(), csrf)
    assert first.status_code == 200
    created_first = first.json()["created"]
    service_first = first.json()["service_created"]

    second = _confirm(pg_client, _fixture_bytes(), csrf)
    assert second.status_code == 200
    body = second.json()
    assert body["created"] == 0
    assert body["service_created"] == 0
    assert body["skipped"] >= created_first + service_first

    assert (
        pg_db.scalar(
            select(func.count())
            .select_from(Candidate)
            .where(Candidate.source == CandidateSource.EXCEL_IMPORT)
        )
        == created_first
    )
    assert pg_db.scalar(select(func.count()).select_from(ScheduleEntry)) == service_first


def test_ambiguous_names_and_foreign_candidates_on_postgres(
    pg_client: TestClient, pg_db: Session
) -> None:
    manager = make_user(pg_db, username="boss", role=UserRole.MANAGER)
    make_candidate(pg_db, owner=manager, full_name="Однофамилев Константин")
    make_candidate(pg_db, owner=manager, full_name="Однофамилев Константин")
    csrf = _login(pg_client, "boss")

    # Без явного выбора между тёзками импорт не проходит.
    refused = _confirm(pg_client, _fixture_bytes(), csrf)
    assert refused.status_code == 422

    # Чужой/несуществующий кандидат для сопоставления → 422 и ничего не записано.
    bad = _confirm(
        pg_client,
        _fixture_bytes(),
        csrf,
        [
            {
                "row_index": 1,
                "action": "match",
                "candidate_id": str(uuid4()),
            }
        ],
    )
    assert bad.status_code == 422
    assert pg_db.scalar(select(func.count()).select_from(Candidate)) == 2
    assert pg_db.scalar(select(func.count()).select_from(ScheduleEntry)) == 0
    assert pg_db.scalar(select(func.count()).select_from(ScheduleImport)) == 0


def test_hr_scope_is_respected_on_postgres(pg_client: TestClient, pg_db: Session) -> None:
    make_user(pg_db, username="hr1", role=UserRole.HR)
    hr2 = make_user(pg_db, username="hr2", role=UserRole.HR)
    make_candidate(pg_db, owner=hr2, full_name="Тестова Анна Ивановна")
    csrf = _login(pg_client, "hr1")

    preview = _upload(pg_client, _fixture_bytes(), csrf).json()
    testova = next(row for row in preview["rows"] if row["full_name"] == "Тестова Анна Ивановна")
    # Кандидат второго HR не раскрывается в совпадениях.
    assert testova["suggested_action"] == "create"
    assert testova["match"] is None

    confirmed = _confirm(pg_client, _fixture_bytes(), csrf)
    assert confirmed.status_code == 200
    imported = pg_db.scalar(
        select(Candidate).where(
            Candidate.full_name == "Тестова Анна Ивановна",
            Candidate.source == CandidateSource.EXCEL_IMPORT,
        )
    )
    assert imported is not None
    assert imported.owner_user_id is None
    assert imported.stage == CandidateStage.OFFER


def test_concurrent_confirms_of_same_file_do_not_duplicate(
    pg_client: TestClient,
    pg_db: Session,
    pg_settings: Settings,
    pg_engine: Engine,
) -> None:
    """Два одновременных подтверждения одного файла не создают дублей.

    Без общего advisory-lock оба запроса читают «ещё не импортированные»
    ключи и записывают по своему набору (уникальность ``(import_id, row_key)``
    чужой импорт не останавливает). Проверка:
    после завершения обоих запросов создан ровно один набор кандидатов и
    служебных записей, второй запрос идемпотентно пропускает строки,
    ошибок уникальности и частичных данных нет.
    """
    make_user(pg_db, username="hr1", role=UserRole.HR)
    csrf_first = _login(pg_client, "hr1")

    # Второй независимый клиент — вторая сессия, как при двойном подтверждении.
    second_app = create_app(pg_settings, engine=pg_engine)
    payload = _fixture_bytes()
    barrier = threading.Barrier(2)

    def run_confirm(client: TestClient, csrf: str) -> httpx.Response:
        # Барьер разводит запросы как можно ближе по времени.
        barrier.wait(timeout=30)
        return _confirm(client, payload, csrf)

    with TestClient(second_app) as second_client:
        csrf_second = _login(second_client, "hr1")
        with ThreadPoolExecutor(max_workers=2) as pool:
            first_future = pool.submit(run_confirm, pg_client, csrf_first)
            second_future = pool.submit(run_confirm, second_client, csrf_second)
            responses = [
                first_future.result(timeout=120),
                second_future.result(timeout=120),
            ]

    # Оба запроса завершились без ошибок (ошибка уникальности дала бы 500).
    assert [response.status_code for response in responses] == [200, 200], [
        response.text for response in responses
    ]
    bodies = [response.json() for response in responses]

    # Один импорт записывает весь набор, второй идемпотентно пропускает:
    # «размазывания» строк между запросами нет.
    created = sorted(body["created"] for body in bodies)
    service = sorted(body["service_created"] for body in bodies)
    assert created[0] == 0 and created[1] > 0
    assert service[0] == 0 and service[1] > 0

    pg_db.expire_all()

    # Кандидатов ровно один набор, без дублей по ФИО.
    assert (
        pg_db.scalar(
            select(func.count())
            .select_from(Candidate)
            .where(Candidate.source == CandidateSource.EXCEL_IMPORT)
        )
        == created[1]
    )
    duplicated_names = pg_db.execute(
        select(Candidate.full_name)
        .where(Candidate.source == CandidateSource.EXCEL_IMPORT)
        .group_by(Candidate.full_name)
        .having(func.count() > 1)
    ).all()
    assert duplicated_names == []

    # Служебных записей — по одному разу на строку.
    assert pg_db.scalar(select(func.count()).select_from(ScheduleEntry)) == service[1]

    # Агрегатов импорта два (по одному на запрос), но реально записывал один.
    imports = pg_db.scalars(select(ScheduleImport)).all()
    assert len(imports) == 2
    assert sorted(record.created_candidates for record in imports) == [0, created[1]]


def test_concurrent_confirms_of_different_files_share_master_lock(
    pg_client: TestClient,
    pg_db: Session,
    pg_settings: Settings,
    pg_engine: Engine,
) -> None:
    """Distinct file fingerprints still serialize against the one master set."""

    hr = make_user(pg_db, username="hr-different-files", role=UserRole.HR)
    csrf_first = _login(pg_client, hr.username)
    first_payload = _fixture_bytes()
    second_payload = _same_rows_different_workbook_bytes()
    assert first_payload != second_payload

    second_app = create_app(pg_settings, engine=pg_engine)
    barrier = threading.Barrier(2)

    def run_confirm(client: TestClient, csrf: str, payload: bytes) -> httpx.Response:
        barrier.wait(timeout=30)
        return _confirm(client, payload, csrf)

    with TestClient(second_app) as second_client:
        csrf_second = _login(second_client, hr.username)
        with ThreadPoolExecutor(max_workers=2) as pool:
            first_future = pool.submit(run_confirm, pg_client, csrf_first, first_payload)
            second_future = pool.submit(run_confirm, second_client, csrf_second, second_payload)
            responses = [
                first_future.result(timeout=120),
                second_future.result(timeout=120),
            ]

    assert [response.status_code for response in responses] == [200, 200], [
        response.text for response in responses
    ]
    bodies = [response.json() for response in responses]
    created = sorted(body["created"] for body in bodies)
    services = sorted(body["service_created"] for body in bodies)
    assert created[0] == 0 and created[1] > 0
    assert services[0] == 0 and services[1] > 0

    pg_db.expire_all()
    imports = pg_db.scalars(select(ScheduleImport)).all()
    assert len(imports) == 2
    assert len({record.file_sha256 for record in imports}) == 2
    latest = max(imports, key=lambda item: (item.created_at, str(item.id)))
    active_rows = pg_db.scalars(
        select(ScheduleImportRow).where(ScheduleImportRow.is_active.is_(True))
    ).all()
    assert len(active_rows) == len(bodies[0]["rows"])
    assert {row.import_id for row in active_rows} == {latest.id}
    assert latest.rows_missing == 0

    imported_candidates = pg_db.scalars(
        select(Candidate).where(Candidate.source == CandidateSource.EXCEL_IMPORT)
    ).all()
    assert len(imported_candidates) == created[1]
    assert all(candidate.owner_user_id is None for candidate in imported_candidates)
    assert pg_db.scalar(select(func.count()).select_from(ScheduleEntry)) == services[1]
