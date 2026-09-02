"""Тесты health-check приложения.

Единица тестирования — FastAPI app с TestClient. Реальное подключение к БД
не выполняется: в успешном сценарии заглушается проверка доступности БД,
в сценарии отказа — используется гарантированно недоступный URL.
"""

from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient

from hr_manager.api import health as health_module
from hr_manager.main import app


@pytest.fixture()
def client() -> TestClient:
    """TestClient без внешних зависимостей."""
    return TestClient(app)


def test_health_returns_ok_when_database_available(client: TestClient, monkeypatch) -> None:
    """GET /health -> 200 с пометкой, что БД доступна."""
    monkeypatch.setattr(health_module, "check_database", Mock())  # БД считаем доступной

    response = client.get("/health")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["database"] == "ok"
    assert body["environment"] == "test"
    assert body["version"]


def test_health_returns_503_when_database_unavailable(
    client: TestClient, monkeypatch, database_url_unreachable
) -> None:
    """GET /health -> 503, если подключение к БД не удалось."""
    # Handler обращается к get_settings() через имя, импортированное в модуль
    # health, поэтому подменяем имя именно в пространстве health_module.
    # Исходную функцию сохраняем до подмены, чтобы избежать рекурсии.
    original_get_settings = health_module.get_settings

    def _unreachable_settings():
        return original_get_settings().model_copy(
            update={"database_url": database_url_unreachable}
        )

    monkeypatch.setattr(health_module, "get_settings", _unreachable_settings)

    response = client.get("/health")

    assert response.status_code == 503
    body = response.json()
    assert body["status"] == "error"
    assert body["database"] == "unavailable"


def test_health_endpoint_registered_on_root_docs(client: TestClient) -> None:
    """Health виден в OpenAPI и у корня нет скрытых эндпоинтов."""
    schema = client.get("/openapi.json").json()
    assert "/health" in schema["paths"]
    assert "get" in schema["paths"]["/health"]
