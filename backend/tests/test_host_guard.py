"""Unit tests for the loopback Host guard (phase 12, app/host_guard.py)."""

import pytest

from app.host_guard import is_loopback_host, split_host_header


@pytest.mark.parametrize(
    "host",
    [
        "127.0.0.1",
        "127.0.0.1:8081",
        "localhost",
        "localhost:80",
        "LOCALHOST:8081",
        "127.0.0.1.",  # trailing dot from search domains is normalized
        "::1",
        "[::1]",
        "[::1]:8000",
    ],
)
def test_loopback_names_accepted(host: str) -> None:
    assert is_loopback_host(host)


@pytest.mark.parametrize(
    "host",
    [
        None,
        "",
        "evil.example.com",
        "evil.example.com:8081",
        "192.168.1.10:8081",  # LAN address: explicitly NOT loopback
        "10.0.0.5",
        "0.0.0.0",
        "localhost.evil.example.com",
        "127.0.0.1.evil.example.com",
        "not-a-host:::bad",
    ],
)
def test_non_loopback_rejected(host: str | None) -> None:
    assert is_loopback_host(host) is False


def test_split_host_header_shapes() -> None:
    assert split_host_header("Example.COM:8443") == "example.com"
    assert split_host_header("[::1]:80") == "[::1]"
    assert split_host_header(None) == ""


def test_pilot_middleware_rejects_non_loopback_host() -> None:
    """End-to-end: with HRMGR_PILOT_LOCAL_TRUSTED on, no route answers a
    non-loopback Host — including /health — while loopback Hosts work."""
    from fastapi.testclient import TestClient

    from app.config import Settings
    from app.main import create_app

    settings = Settings.model_validate(
        {
            "APP_ENV": "test",
            "DATABASE_URL": "sqlite+pysqlite://",
            "SECRET_KEY": "unit-test-secret-key",
            "HRMGR_PILOT_LOCAL_TRUSTED": "true",
        }
    )
    from sqlalchemy import create_engine
    from sqlalchemy.pool import StaticPool

    engine = create_engine(
        "sqlite+pysqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    app = create_app(settings, engine=engine)
    with TestClient(app, base_url="http://127.0.0.1") as client:
        ok = client.get("/health")
        assert ok.status_code == 200
        rebinding = client.get("/health", headers={"Host": "rebind.evil.example"})
        assert rebinding.status_code == 403
        assert "127.0.0.1" in rebinding.json()["detail"]


def test_guard_absent_without_pilot_flag() -> None:
    """Without the opt-in the Host guard is NOT installed: server deploys
    behind a real proxy keep their Host header behaviour."""
    from fastapi.testclient import TestClient

    from app.config import Settings
    from app.main import create_app

    settings = Settings.model_validate(
        {
            "APP_ENV": "test",
            "DATABASE_URL": "sqlite+pysqlite://",
            "SECRET_KEY": "unit-test-secret-key",
        }
    )
    assert settings.pilot_local_trusted is False
    from sqlalchemy import create_engine
    from sqlalchemy.pool import StaticPool

    engine = create_engine(
        "sqlite+pysqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    app = create_app(settings, engine=engine)
    with TestClient(app, base_url="http://127.0.0.1") as client:
        assert client.get("/health", headers={"Host": "hr.example.com"}).status_code == 200
