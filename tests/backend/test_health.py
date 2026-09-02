"""Тесты endpoint GET /health.

Семантика: 200 — только когда доступны и приложение, и БД; 503 — когда
приложение живо, но БД недоступна. Изолированные unit-тесты используют
SQLite in-memory как «доступную БД» — это разрешено и документировано
(см. tests/backend/README.md). Реальная доступность PostgreSQL
проверяется smoke-тестом Docker Compose в CI.
"""

from app.config import AppEnvironment, Settings
from app.main import create_app
from fastapi.testclient import TestClient


def make_client(database_url: str) -> TestClient:
    settings = Settings(
        app_env=AppEnvironment.TEST,
        database_url=database_url,
        _env_file=None,
    )
    return TestClient(create_app(settings))


def test_health_returns_200_when_application_and_database_are_up() -> None:
    # In-memory SQLite используется ТОЛЬКО как изолированный стенд
    # доступной БД для unit-теста (APP_ENV=test), см. README каталога.
    client = make_client("sqlite+pysqlite:///:memory:")
    response = client.get("/health")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["database"] == "up"
    assert body["version"]
    assert body["checked_at"]


def test_health_returns_503_when_database_is_down() -> None:
    # PostgreSQL на порту 1 заведомо недоступен; connect_timeout=1
    # держит тест быстрым, даже если сокет «молчит».
    client = make_client(
        "postgresql+psycopg://hr_manager:hr_manager@127.0.0.1:1/hr_manager"
    )
    response = client.get("/health")

    assert response.status_code == 503
    body = response.json()
    assert body["status"] == "degraded"
    assert body["database"] == "down"


def test_health_does_not_leak_internals_in_response() -> None:
    client = make_client(
        "postgresql+psycopg://hr_manager:hr_manager@127.0.0.1:1/hr_manager"
    )
    response = client.get("/health")

    # Публичный endpoint не должен раскрывать строки подключения,
    # тексты ошибок БД и прочие внутренние детали.
    assert set(response.json()) == {"status", "database", "version", "checked_at"}
