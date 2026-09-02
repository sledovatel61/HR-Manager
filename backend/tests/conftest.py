"""Shared pytest fixtures for the HR Manager backend.

Unit tests run against an in-memory SQLite database (allowed ONLY for isolated
unit tests with APP_ENV=test, documented in README and ARCHITECTURE.md). The
ORM schema is created directly from the models metadata. Integration tests
(marker ``integration``) run against a real PostgreSQL via
``TEST_DATABASE_URL`` after the Alembic migration pipeline has been applied —
they never fall back to SQLite.
"""

import os
from collections.abc import Iterator
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, create_engine, text
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.config import Settings
from app.main import create_app
from app.models import Base, User, UserRole
from app.security import hash_password

TEST_SQLITE_URL = "sqlite+pysqlite://"

# A valid strong password used by fixtures (satisfies the password policy).
FIXTURE_PASSWORD = "Str0ng-Pass-2026"


@pytest.fixture()
def unit_engine() -> Iterator[Engine]:
    engine = create_engine(
        TEST_SQLITE_URL,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
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
            # Fast lockout threshold for deterministic unit tests.
            "LOGIN_MAX_FAILURES": "5",
            "LOGIN_LOCK_MINUTES": "15",
        }
    )


@pytest.fixture()
def client(unit_settings: Settings, unit_engine: Engine) -> Iterator[TestClient]:
    """TestClient backed by an in-memory SQLite engine with schema created."""
    app = create_app(unit_settings, engine=unit_engine)
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture()
def db_session(unit_engine: Engine) -> Iterator[Session]:
    """Direct ORM session over the in-memory test database."""
    with Session(unit_engine) as session:
        yield session
        session.rollback()


def make_user(
    db: Session,
    *,
    username: str,
    role: UserRole = UserRole.HR,
    password: str = FIXTURE_PASSWORD,
    full_name: str = "",
    is_active: bool = True,
) -> User:
    """Create and persist a user with an Argon2id password hash."""
    user = User(
        username=username,
        full_name=full_name or username,
        role=role,
        password_hash=hash_password(password),
        is_active=is_active,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def _require_integration_url() -> str:
    """Return TEST_DATABASE_URL when it points at PostgreSQL, else skip."""
    url = os.environ.get("TEST_DATABASE_URL")
    if not url or not url.startswith("postgresql"):
        pytest.skip("TEST_DATABASE_URL with a PostgreSQL URL is required for integration tests")
    return url


@pytest.fixture()
def integration_url() -> str:
    return _require_integration_url()


@pytest.fixture(scope="session")
def pg_engine() -> Iterator[Engine]:
    """Engine for the real PostgreSQL integration database.

    The schema is expected to exist (the integration test job runs
    ``alembic upgrade head`` beforehand; the migration tests manage upgrades
    themselves). Tables are truncated between tests for isolation.
    """
    url = _require_integration_url()
    engine = create_engine(url, pool_pre_ping=True)
    yield engine
    engine.dispose()


@pytest.fixture()
def pg_settings(integration_url: str) -> Settings:
    return Settings.model_validate(
        {
            "APP_ENV": "test",
            "APP_DEBUG": "false",
            "SECRET_KEY": "integration-test-secret-key",
            "DATABASE_URL": integration_url,
        }
    )


@pytest.fixture()
def pg_client(pg_settings: Settings, pg_engine: Engine) -> Iterator[TestClient]:
    """TestClient against PostgreSQL. Tables are truncated for a clean state."""
    with pg_engine.begin() as connection:
        connection.execute(
            text("TRUNCATE TABLE audit_log, user_sessions, users RESTART IDENTITY CASCADE")
        )
    app = create_app(pg_settings, engine=pg_engine)
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture()
def pg_db(pg_engine: Engine) -> Iterator[Session]:
    """Direct ORM session over the PostgreSQL integration database."""
    with Session(pg_engine) as session:
        yield session
        session.rollback()


def user_id(user: User) -> UUID:
    """Typed helper for readability in tests."""
    return user.id
