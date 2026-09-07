"""Tests for GET /health."""

import os
from typing import cast

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool

RUN_INTEGRATION = os.environ.get("TEST_DATABASE_URL") is not None


def test_health_ok_returns_200(client: TestClient) -> None:
    response = client.get("/health")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["service"] == "hr-manager"
    assert body["version"]
    assert body["checks"]["database"]["status"] == "ok"
    assert isinstance(body["checks"]["database"]["latency_ms"], int)


def test_health_degrades_when_database_is_unavailable(client: TestClient) -> None:
    """Health must report 503 (not 500 or a crash) when the DB is unreachable."""
    broken_engine = create_engine(
        # Directory does not exist, so every connect attempt fails.
        "sqlite+pysqlite:////nonexistent_hr_manager_dir/test.db",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    app = cast(FastAPI, client.app)
    app.state.engine = broken_engine

    try:
        response = client.get("/health")
    finally:
        broken_engine.dispose()

    assert response.status_code == 503
    body = response.json()
    assert body["status"] == "degraded"
    assert body["checks"]["database"]["status"] == "error"
    assert body["checks"]["database"]["latency_ms"] is None


@pytest.mark.integration
@pytest.mark.skipif(not RUN_INTEGRATION, reason="TEST_DATABASE_URL is not set")
def test_health_against_real_postgresql() -> None:
    """Verify the full request path against a real PostgreSQL instance."""
    from app.config import Settings
    from app.main import create_app

    settings = Settings.model_validate(
        {
            "APP_ENV": "test",
            "APP_DEBUG": "false",
            "SECRET_KEY": "integration-test-secret-key",
            "DATABASE_URL": os.environ["TEST_DATABASE_URL"],
        }
    )
    with TestClient(create_app(settings)) as test_client:
        response = test_client.get("/health")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["checks"]["database"]["status"] == "ok"


@pytest.mark.integration
@pytest.mark.skipif(not RUN_INTEGRATION, reason="TEST_DATABASE_URL is not set")
def test_health_degrades_against_stopped_postgresql() -> None:
    """Health must report 503 (not 500/crash) when PostgreSQL is unreachable."""
    # Point at a port where nothing listens (replace any host:port/ segment,
    # e.g. localhost:5432 or 127.0.0.1:55432, with a closed port).
    import re

    from app.config import Settings
    from app.main import create_app

    stopped_url = re.sub(
        r"(@[^/:]+:)\d+(/|$)",
        r"\g<1>5599\g<2>",
        os.environ["TEST_DATABASE_URL"],
        count=1,
    )
    settings = Settings.model_validate(
        {
            "APP_ENV": "test",
            "APP_DEBUG": "false",
            "SECRET_KEY": "integration-test-secret-key",
            "DATABASE_URL": stopped_url,
            "DB_CONNECT_TIMEOUT_SECONDS": "0.5",
        }
    )
    with TestClient(create_app(settings)) as test_client:
        response = test_client.get("/health")

    assert response.status_code == 503
    body = response.json()
    assert body["status"] == "degraded"
    assert body["checks"]["database"]["status"] == "error"
