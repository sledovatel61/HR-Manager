"""Shared pytest fixtures for the HR Manager backend."""

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, create_engine
from sqlalchemy.pool import StaticPool

from app.config import Settings
from app.main import create_app

# In-memory SQLite is allowed ONLY for isolated unit tests (documented in
# README and docs/ARCHITECTURE.md). Development and production always use
# PostgreSQL; integration tests use a real PostgreSQL via TEST_DATABASE_URL.
# StaticPool + check_same_thread=False make the single shared connection safe
# to use across the threads spawned by FastAPI's TestClient.
TEST_SQLITE_URL = "sqlite+pysqlite://"


@pytest.fixture()
def unit_engine() -> Iterator[Engine]:
    engine = create_engine(
        TEST_SQLITE_URL,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    yield engine
    engine.dispose()


@pytest.fixture()
def unit_settings() -> Settings:
    # model_validate mirrors how real environment variables map into the
    # settings (validation aliases), without touching the process env.
    return Settings.model_validate(
        {
            "APP_ENV": "test",
            "APP_DEBUG": "false",
            "SECRET_KEY": "unit-test-secret-key",
            "DATABASE_URL": TEST_SQLITE_URL,
        }
    )


@pytest.fixture()
def client(unit_settings: Settings, unit_engine: Engine) -> Iterator[TestClient]:
    """TestClient backed by an in-memory SQLite engine.

    Unit tests only exercise behaviour (e.g. the degraded branch of /health);
    real database connectivity is covered by the integration tests.
    """
    app = create_app(unit_settings, engine=unit_engine)
    with TestClient(app) as test_client:
        yield test_client
