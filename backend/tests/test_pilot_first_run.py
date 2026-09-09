"""Phase 12: the local pilot one-shot first-run exchange.

The Windows installer arms a single-use exchange token via the container
environment. The browser (or the installer over loopback) claims it exactly
once to create the single pilot owner — role ``admin`` + explicit
``pilot_full_access`` grant + the chosen working mode — and to receive a real
session. These tests prove the boundaries: loopback-only, constant-time token
check, TTL, atomic single-shot consumption, single-pilot race, no
unauthenticated backdoor, CSRF on the follow-up password step, and the
working-mode display contract.
"""

from collections.abc import Iterator
from datetime import timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import Engine, func, select
from sqlalchemy.orm import Session

from app.config import Settings
from app.models import (
    AccessGrant,
    AccessGrantScope,
    AuditAction,
    PilotFirstRunClaim,
    User,
)
from app.utils import utc_now
from tests.conftest import make_user

LOOPBACK_HEADERS = {"X-Forwarded-For": "127.0.0.1"}
EXTERNAL_HEADERS = {"X-Forwarded-For": "203.0.113.7"}

FIRST_RUN_TOKEN = "a" * 43 + "="  # 43 url-safe chars, padded — one-shot nonce


def make_pilot_settings(**overrides: object) -> Settings:
    env: dict[str, object] = {
        "APP_ENV": "test",
        "APP_DEBUG": "false",
        "SECRET_KEY": "unit-test-secret-key",
        "DATABASE_URL": "sqlite+pysqlite://",
        "FIRST_RUN_TOKEN": FIRST_RUN_TOKEN,
        "FIRST_RUN_EXPIRES_AT": (utc_now() + timedelta(minutes=10)).isoformat(),
        "PILOT_SURNAME": "Иванова",
        "PILOT_WORKING_MODE": "hr",
    }
    env.update(overrides)
    return Settings.model_validate(env)


@pytest.fixture()
def pilot_client(unit_engine: Engine) -> Iterator[TestClient]:
    from app.main import create_app

    settings = make_pilot_settings()
    app = create_app(settings, engine=unit_engine)
    with TestClient(app) as test_client:
        yield test_client


def test_status_not_pending_without_token(client: TestClient) -> None:
    response = client.get("/setup/first-run/status")
    assert response.status_code == 200
    assert response.json() == {"pending": False, "needs_password": False}


def test_status_pending_before_first_run(pilot_client: TestClient) -> None:
    response = pilot_client.get("/setup/first-run/status")
    assert response.status_code == 200
    assert response.json() == {"pending": True, "needs_password": False}


def test_claim_creates_single_owner_and_logs_in(
    pilot_client: TestClient, db_session: Session
) -> None:
    response = pilot_client.post(
        "/setup/first-run",
        json={"exchange_token": FIRST_RUN_TOKEN},
        headers=LOOPBACK_HEADERS,
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["user"]["role"] == "admin"
    assert body["user"]["full_name"] == "Иванова"
    assert body["user"]["working_mode"] == "hr"
    assert body["user"]["password_change_required"] is True
    assert body["user"]["username"].startswith("pilot-")
    assert body["csrf_token"]
    # A real session cookie was set (automatic first login).
    assert "hrm_session" in response.cookies
    assert "hrm_csrf" in response.cookies

    users = db_session.scalars(select(User)).all()
    assert len(users) == 1
    owner = users[0]
    grant = db_session.scalar(select(AccessGrant).where(AccessGrant.user_id == owner.id))
    assert grant is not None
    assert grant.scope == AccessGrantScope.PILOT_FULL_ACCESS
    assert grant.revoked_at is None
    claim = db_session.scalar(select(PilotFirstRunClaim))
    assert claim is not None and claim.user_id == owner.id
    # The raw token is never stored — only its SHA-256.
    assert claim.token_hash != FIRST_RUN_TOKEN
    assert len(claim.token_hash) == 64


def test_claim_derives_username_deterministically(
    pilot_client: TestClient, db_session: Session
) -> None:
    pilot_client.post(
        "/setup/first-run",
        json={"exchange_token": FIRST_RUN_TOKEN},
        headers=LOOPBACK_HEADERS,
    )
    owner = db_session.scalar(select(User))
    assert owner is not None
    assert owner.username == "pilot-ivanova"


def test_claim_rejected_for_wrong_token(pilot_client: TestClient, db_session: Session) -> None:
    response = pilot_client.post(
        "/setup/first-run",
        json={"exchange_token": "z" * 44},
        headers=LOOPBACK_HEADERS,
    )
    assert response.status_code == 403
    assert db_session.scalar(select(User)) is None


def test_claim_rejected_for_non_loopback_client(
    pilot_client: TestClient, db_session: Session
) -> None:
    response = pilot_client.post(
        "/setup/first-run",
        json={"exchange_token": FIRST_RUN_TOKEN},
        headers=EXTERNAL_HEADERS,
    )
    assert response.status_code == 403
    assert db_session.scalar(select(User)) is None


def test_claim_rejected_after_expiry(unit_engine: Engine) -> None:
    from app.main import create_app

    expired = make_pilot_settings(
        FIRST_RUN_EXPIRES_AT=(utc_now() - timedelta(seconds=1)).isoformat()
    )
    app = create_app(expired, engine=unit_engine)
    with TestClient(app) as client:
        response = client.post(
            "/setup/first-run",
            json={"exchange_token": FIRST_RUN_TOKEN},
            headers=LOOPBACK_HEADERS,
        )
    assert response.status_code == 403


def test_claim_is_single_shot(pilot_client: TestClient, db_session: Session) -> None:
    first = pilot_client.post(
        "/setup/first-run",
        json={"exchange_token": FIRST_RUN_TOKEN},
        headers=LOOPBACK_HEADERS,
    )
    assert first.status_code == 200
    second = pilot_client.post(
        "/setup/first-run",
        json={"exchange_token": FIRST_RUN_TOKEN},
        headers=LOOPBACK_HEADERS,
    )
    assert second.status_code == 409
    assert len(db_session.scalars(select(User)).all()) == 1


def test_claim_refused_when_users_already_exist(
    pilot_client: TestClient, db_session: Session
) -> None:
    make_user(db_session, username="existing", full_name="Существующий")
    response = pilot_client.post(
        "/setup/first-run",
        json={"exchange_token": FIRST_RUN_TOKEN},
        headers=LOOPBACK_HEADERS,
    )
    assert response.status_code == 409


def test_first_run_rate_limited(pilot_client: TestClient) -> None:
    # The wrong token still consumes a rate-limit slot; after the window limit
    # the client is throttled regardless of token correctness.
    statuses = []
    for _ in range(11):
        response = pilot_client.post(
            "/setup/first-run",
            json={"exchange_token": "z" * 44},
            headers=LOOPBACK_HEADERS,
        )
        statuses.append(response.status_code)
    assert 429 in statuses


def test_password_set_requires_csrf(pilot_client: TestClient) -> None:
    pilot_client.post(
        "/setup/first-run",
        json={"exchange_token": FIRST_RUN_TOKEN},
        headers=LOOPBACK_HEADERS,
    )
    response = pilot_client.post("/setup/password", json={"password": "New-Strong-Pass-1"})
    assert response.status_code == 403


def test_password_set_flow_and_subsequent_login(
    pilot_client: TestClient, db_session: Session
) -> None:
    first = pilot_client.post(
        "/setup/first-run",
        json={"exchange_token": FIRST_RUN_TOKEN},
        headers=LOOPBACK_HEADERS,
    )
    assert first.status_code == 200
    csrf = first.json()["csrf_token"]

    weak = pilot_client.post(
        "/setup/password",
        json={"password": "short"},
        headers=LOOPBACK_HEADERS | {"X-CSRF-Token": csrf},
    )
    assert weak.status_code == 422

    owner = db_session.scalar(select(User))
    assert owner is not None and owner.password_change_required is True

    new_password = "New-Strong-Pass-2026"
    response = pilot_client.post(
        "/setup/password",
        json={"password": new_password},
        headers=LOOPBACK_HEADERS | {"X-CSRF-Token": csrf},
    )
    assert response.status_code == 200
    assert response.json()["password_change_required"] is False

    db_session.expire_all()
    owner = db_session.scalar(select(User))
    assert owner is not None and owner.password_change_required is False

    login = pilot_client.post(
        "/auth/login",
        json={"username": owner.username, "password": new_password},
        headers=LOOPBACK_HEADERS,
    )
    assert login.status_code == 200


def test_status_needs_password_after_claim(pilot_client: TestClient) -> None:
    claim = pilot_client.post(
        "/setup/first-run",
        json={"exchange_token": FIRST_RUN_TOKEN},
        headers=LOOPBACK_HEADERS,
    )
    assert claim.status_code == 200
    status = pilot_client.get("/setup/first-run/status")
    assert status.json() == {"pending": False, "needs_password": True}


def _file_backed_engine(tmp_path: Path) -> Engine:
    """A SQLite file engine that survives `create_app` disposal between two
    sequential TestClient lifecycles (in-memory DBs are destroyed on
    ``Engine.dispose()``)."""
    from sqlalchemy import create_engine
    from sqlalchemy.pool import StaticPool

    from app.models import Base

    engine = create_engine(
        f"sqlite+pysqlite:///{tmp_path / 'pilot.db'}",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    return engine


def test_recovery_resumes_without_second_owner(tmp_path: Path) -> None:
    from app.main import create_app

    engine = _file_backed_engine(tmp_path)
    settings_one = make_pilot_settings()
    app_one = create_app(settings_one, engine=engine)
    with TestClient(app_one) as first:
        created = first.post(
            "/setup/first-run",
            json={"exchange_token": FIRST_RUN_TOKEN},
            headers=LOOPBACK_HEADERS,
        )
        assert created.status_code == 200

    # The owner lost the session before choosing a password. The installer
    # re-arms a FRESH token and reopens the first-run page.
    new_token = "B" * 44
    settings_two = make_pilot_settings(FIRST_RUN_TOKEN=new_token)
    app_two = create_app(settings_two, engine=engine)
    with TestClient(app_two) as second:
        status = second.get("/setup/first-run/status")
        assert status.json() == {"pending": False, "needs_password": True}
        resumed = second.post(
            "/setup/first-run",
            json={"exchange_token": new_token},
            headers=LOOPBACK_HEADERS,
        )
        assert resumed.status_code == 200

    with Session(engine) as db:
        users = db.scalars(select(User)).all()
        assert len(users) == 1
        assert users[0].password_change_required is True
        claims = db.scalars(select(PilotFirstRunClaim)).all()
        assert len(claims) == 2
    engine.dispose()


def test_recovery_refused_after_owner_sets_password(tmp_path: Path) -> None:
    from app.main import create_app

    engine = _file_backed_engine(tmp_path)
    settings_one = make_pilot_settings()
    app_one = create_app(settings_one, engine=engine)
    with TestClient(app_one) as first:
        created = first.post(
            "/setup/first-run",
            json={"exchange_token": FIRST_RUN_TOKEN},
            headers=LOOPBACK_HEADERS,
        )
        csrf = created.json()["csrf_token"]
        password = first.post(
            "/setup/password",
            json={"password": "Owner-Chosen-Pass-1"},
            headers=LOOPBACK_HEADERS | {"X-CSRF-Token": csrf},
        )
        assert password.status_code == 200

    new_token = "C" * 44
    settings_two = make_pilot_settings(FIRST_RUN_TOKEN=new_token)
    app_two = create_app(settings_two, engine=engine)
    with TestClient(app_two) as second:
        status = second.get("/setup/first-run/status")
        assert status.json() == {"pending": False, "needs_password": False}
        refused = second.post(
            "/setup/first-run",
            json={"exchange_token": new_token},
            headers=LOOPBACK_HEADERS,
        )
        assert refused.status_code == 409
    engine.dispose()


def test_claim_audited(pilot_client: TestClient, db_session: Session) -> None:
    from app.models import AuditEvent

    pilot_client.post(
        "/setup/first-run",
        json={"exchange_token": FIRST_RUN_TOKEN},
        headers=LOOPBACK_HEADERS,
    )
    actions = {event.action for event in db_session.scalars(select(AuditEvent)).all()}
    assert AuditAction.FIRST_RUN_CLAIMED in actions
    assert AuditAction.FIRST_RUN_COMPLETED in actions
    assert AuditAction.PILOT_USER_CREATED in actions
    assert AuditAction.PILOT_ACCESS_GRANTED in actions


# --- pilot environment configuration ------------------------------------------


def test_pilot_env_defaults_to_non_secure_cookies_and_hardened_secrets() -> None:
    env = {
        "APP_ENV": "pilot",
        "APP_DEBUG": "false",
        "SECRET_KEY": "x" * 48,
        "DATABASE_URL": "postgresql+psycopg://app:strong-pass@db:5432/hr_manager",
    }
    settings = Settings.model_validate(env)
    assert settings.is_pilot
    assert settings.is_hardened
    assert settings.session_cookie_is_secure is False


def test_pilot_env_rejects_development_secrets() -> None:
    from app.config import DEVELOPMENT_SECRET_KEY

    env = {
        "APP_ENV": "pilot",
        "APP_DEBUG": "false",
        "SECRET_KEY": DEVELOPMENT_SECRET_KEY,
        "DATABASE_URL": "postgresql+psycopg://app:strong-pass@db:5432/hr_manager",
    }
    with pytest.raises(ValidationError):
        Settings.model_validate(env)


def test_pilot_env_rejects_debug() -> None:
    env = {
        "APP_ENV": "pilot",
        "APP_DEBUG": "true",
        "SECRET_KEY": "x" * 48,
        "DATABASE_URL": "postgresql+psycopg://app:strong-pass@db:5432/hr_manager",
    }
    with pytest.raises(ValidationError):
        Settings.model_validate(env)


def test_pilot_env_requires_expiry_when_token_set() -> None:
    env = {
        "APP_ENV": "pilot",
        "APP_DEBUG": "false",
        "SECRET_KEY": "x" * 48,
        "DATABASE_URL": "postgresql+psycopg://app:strong-pass@db:5432/hr_manager",
        "FIRST_RUN_TOKEN": FIRST_RUN_TOKEN,
    }
    with pytest.raises(ValidationError):
        Settings.model_validate(env)


def test_pilot_bootstrap_never_creates_admin(unit_engine: Engine) -> None:
    from app.bootstrap import bootstrap_admin

    settings = Settings.model_validate(
        {
            "APP_ENV": "pilot",
            "APP_DEBUG": "false",
            "SECRET_KEY": "x" * 48,
            "DATABASE_URL": "postgresql+psycopg://app:strong-pass@db:5432/hr_manager",
        }
    )
    with Session(unit_engine) as db:
        created = bootstrap_admin(db, settings)
        assert created is None
        assert db.scalar(select(func.count()).select_from(User)) == 0
