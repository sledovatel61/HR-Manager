"""Phase 12 first-run unit tests (SQLite, isolated).

Covers the endpoint contracts, validation, loopback gating, rate limiting,
single-pilot invariants and the absence of secrets in responses/audit. Real
PostgreSQL races and trigger guards live in
``test_integration_pilot_first_run.py``.
"""

import secrets
from collections.abc import Iterator
from datetime import timedelta
from typing import Any

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from httpx import Response
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.bootstrap import bootstrap_admin
from app.config import Settings
from app.main import create_app
from app.models import (
    AccessGrant,
    AccessGrantScope,
    AuditAction,
    AuditEvent,
    Base,
    BootstrapExchange,
    BootstrapTicket,
    NotificationPreference,
    User,
)
from app.security import verify_password
from app.setup_owner import (
    build_owner_username,
    hash_claim_value,
    store_pilot_exchange,
    transliterate_surname,
    validate_surname,
)
from app.utils import utc_now

FIXTURE_TOKEN = secrets.token_hex(32)  # 64 hex chars, >= 32 bytes of entropy
LOOPBACK = {"x-real-ip": "127.0.0.1"}
PASSWORD = "Str0ng-Pilot-Pass-2026"

SQLITE_URL = "sqlite+pysqlite://"


@pytest.fixture()
def pilot_settings() -> Settings:
    return Settings.model_validate(
        {
            "APP_ENV": "test",
            "APP_DEBUG": "false",
            "SECRET_KEY": "unit-test-secret-key",
            "DATABASE_URL": SQLITE_URL,
            "PILOT_BOOTSTRAP_EXCHANGE_TOKEN": FIXTURE_TOKEN,
            # High enough to never interfere; the dedicated rate-limit test
            # builds its own app with an explicit limit.
            "PILOT_SETUP_RATE_LIMIT": "10000",
            "PILOT_SETUP_RATE_WINDOW_SECONDS": "300",
        }
    )


@pytest.fixture()
def pilot_client(pilot_settings: Settings) -> Iterator[TestClient]:
    engine = create_engine(
        SQLITE_URL,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        store_pilot_exchange(db, pilot_settings)
    app = create_app(pilot_settings, engine=engine)
    with TestClient(app) as test_client:
        test_client.engine = engine
        yield test_client
    engine.dispose()


def _db_session(client: TestClient) -> Session:
    return Session(client.engine)


def _claim(client: TestClient, token: str = FIXTURE_TOKEN, **overrides: Any) -> Response:
    payload: dict[str, Any] = {
        "exchange_token": token,
        "surname": "Смирнова",
        "working_mode": "manager",
        "timezone": "Europe/Moscow",
    }
    payload.update(overrides)
    return client.post("/setup/owner/claim", json=payload, headers=LOOPBACK)


def _preview(client: TestClient, ticket: str) -> Response:
    return client.post("/setup/owner/preview", json={"ticket": ticket}, headers=LOOPBACK)


def _redeem(client: TestClient, ticket: str, **overrides: Any) -> Response:
    payload: dict[str, Any] = {
        "ticket": ticket,
        "timezone": "Europe/Moscow",
        "workdays": [1, 2, 3, 4, 5],
        "quiet_hours_start": "21:00",
        "quiet_hours_end": "08:00",
        "password": PASSWORD,
    }
    payload.update(overrides)
    return client.post("/setup/owner/redeem", json=payload, headers=LOOPBACK)


# --- claim --------------------------------------------------------------------


def test_claim_returns_one_time_ticket(pilot_client: TestClient) -> None:
    response = _claim(pilot_client)
    assert response.status_code == 201, response.text
    body = response.json()
    ticket = body["ticket"]
    assert len(ticket) == 43
    assert body["expires_at"]
    with _db_session(pilot_client) as db:
        exchange = db.scalar(select(BootstrapExchange))
        assert exchange is not None
        assert exchange.consumed_at is not None
        ticket_row = db.scalar(select(BootstrapTicket))
        assert ticket_row is not None
        assert ticket_row.consumed_at is None
        assert ticket_row.surname == "Смирнова"
        assert ticket_row.working_mode == "manager"
        assert ticket_row.timezone == "Europe/Moscow"
        # Only hashes persist: the raw token/ticket are never stored.
        assert exchange.token_hash == hash_claim_value(FIXTURE_TOKEN)
        assert exchange.token_hash != FIXTURE_TOKEN
        assert ticket_row.ticket_hash == hash_claim_value(ticket)
        assert ticket_row.ticket_hash != ticket


def test_claim_rejects_unknown_and_well_formed_but_wrong_token(
    pilot_client: TestClient,
) -> None:
    assert _claim(pilot_client, token="a" * 43).status_code == 403
    assert _claim(pilot_client, token=secrets.token_hex(32)).status_code == 403
    with _db_session(pilot_client) as db:
        rejections = db.scalars(
            select(AuditEvent).where(AuditEvent.action == AuditAction.PILOT_SETUP_REJECTED)
        ).all()
        assert len(rejections) == 2
        for event in rejections:
            assert "a" * 43 not in (event.details or "")


def test_claim_is_single_use(pilot_client: TestClient) -> None:
    assert _claim(pilot_client).status_code == 201
    second = _claim(pilot_client)
    assert second.status_code == 403
    assert "уже использован" in second.json()["detail"]


def test_claim_rejects_expired_exchange(pilot_client: TestClient) -> None:
    with _db_session(pilot_client) as db:
        row = db.scalar(select(BootstrapExchange))
        assert row is not None
        row.expires_at = utc_now() - timedelta(minutes=1)
        db.commit()
    response = _claim(pilot_client)
    assert response.status_code == 403
    assert "устарел" in response.json()["detail"]


def test_claim_rejects_non_loopback_client(pilot_client: TestClient) -> None:
    response = pilot_client.post(
        "/setup/owner/claim",
        json={
            "exchange_token": FIXTURE_TOKEN,
            "surname": "Смирнова",
            "working_mode": "hr",
        },
        headers={"x-real-ip": "198.51.100.7"},
    )
    assert response.status_code == 404


def test_claim_disabled_without_exchange_token() -> None:
    # Outside the pilot (no exchange token configured) the first-run flow
    # must not exist at all, even for loopback callers.
    settings = Settings.model_validate(
        {
            "APP_ENV": "pilot",
            "APP_DEBUG": "false",
            "SECRET_KEY": "x" * 48,
            "DATABASE_URL": "postgresql+psycopg://p:strong-pass@db:5432/hr_manager",
            "BOOTSTRAP_ADMIN_PASSWORD": "Strong-Bootstrap-Pass-1",
        }
    )
    engine = create_engine(
        SQLITE_URL,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    app = create_app(settings, engine=engine)
    with TestClient(app) as client:
        assert (
            client.post(
                "/setup/owner/claim",
                json={"exchange_token": FIXTURE_TOKEN, "surname": "Тест", "working_mode": "hr"},
                headers=LOOPBACK,
            ).status_code
            == 404
        )
    engine.dispose()


# --- surname / username --------------------------------------------------------


@pytest.mark.parametrize("bad", ["", "Иван1", "Смир\u0007нова", "Смирнова!", "  "])
def test_surname_validation_rejects(bad: str) -> None:
    with pytest.raises(HTTPException):
        validate_surname(bad)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Смирнова", "Смирнова"),
        ("  Иван   Иванов ", "Иван Иванов"),
        ("О'Брайен", "О'Брайен"),
        ("Иванов-Петров", "Иванов-Петров"),
        ("Дёмина", "Дёмина"),
    ],
)
def test_surname_validation_accepts_and_normalizes(raw: str, expected: str) -> None:
    assert validate_surname(raw) == expected


@pytest.mark.parametrize(
    ("surname", "slug"),
    [
        ("Смирнова", "smirnova"),
        ("Щукин", "shchukin"),
        ("Иванов-Петров", "ivanov-petrov"),
        ("Жучкина", "zhuchkina"),
        ("Ырысов", "yrysov"),
        ("Smith", "smith"),
    ],
)
def test_transliteration_is_deterministic(surname: str, slug: str) -> None:
    assert transliterate_surname(surname) == slug
    engine = create_engine(
        SQLITE_URL,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        assert build_owner_username(db, surname) == f"owner.{slug}"
        # Repeated calls stay deterministic while the username is free.
        assert build_owner_username(db, surname) == f"owner.{slug}"
    engine.dispose()


def test_surname_with_only_unsupported_chars_falls_back() -> None:
    assert transliterate_surname("123") == "pilot"


# --- preview -------------------------------------------------------------------


def test_preview_shows_surname_mode_and_readiness_without_consuming(
    pilot_client: TestClient,
) -> None:
    ticket = _claim(pilot_client).json()["ticket"]
    response = _preview(pilot_client, ticket)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["surname"] == "Смирнова"
    assert body["working_mode"] == "manager"
    assert body["full_access"] is True
    assert set(body["readiness"]) == {"backend", "database", "worker", "backup"}
    assert body["readiness"]["backend"] == "ok"
    assert body["readiness"]["database"] == "ok"
    assert set(body["channels"]) == {"telegram", "email"}
    assert body["channels"]["telegram"] == "not_configured"
    # Preview never consumes the ticket: a second preview still works.
    assert _preview(pilot_client, ticket).status_code == 200


def test_preview_rejects_unknown_ticket(pilot_client: TestClient) -> None:
    assert _preview(pilot_client, secrets.token_urlsafe(32)).status_code == 403


# --- redeem --------------------------------------------------------------------


def test_redeem_creates_single_owner_with_full_access(pilot_client: TestClient) -> None:
    ticket = _claim(pilot_client).json()["ticket"]
    response = _redeem(pilot_client, ticket)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["user"]["role"] == "admin"
    assert body["working_mode"] == "manager"
    assert body["user"]["full_name"] == "Смирнова"
    assert body["user"]["username"] == "owner.smirnova"
    assert body["csrf_token"]
    # The chosen password never comes back in the response.
    assert PASSWORD not in response.text
    # Session cookies are attached by the redeem response.
    assert "hrm_session" in response.cookies

    with _db_session(pilot_client) as db:
        users = db.scalars(select(User)).all()
        assert len(users) == 1
        user = users[0]
        assert verify_password(user.password_hash, PASSWORD)
        grants = db.scalars(
            select(AccessGrant).where(AccessGrant.scope == AccessGrantScope.PILOT_FULL_ACCESS)
        ).all()
        assert len(grants) == 1
        assert grants[0].revoked_at is None
        assert grants[0].user_id == user.id
        prefs = db.get(NotificationPreference, user.id)
        assert prefs is not None
        assert prefs.timezone == "Europe/Moscow"
        assert prefs.workdays == [1, 2, 3, 4, 5]
        assert prefs.quiet_hours_start == "21:00"
        assert prefs.quiet_hours_end == "08:00"
        created = db.scalar(
            select(AuditEvent).where(AuditEvent.action == AuditAction.PILOT_OWNER_CREATED)
        )
        assert created is not None
        # Audit never contains the surname or the password.
        assert "Смирнова" not in (created.details or "")
        assert PASSWORD not in (created.details or "")


def test_redeem_user_can_log_in_afterwards(pilot_client: TestClient) -> None:
    ticket = _claim(pilot_client).json()["ticket"]
    assert _redeem(pilot_client, ticket).status_code == 200
    login = pilot_client.post(
        "/auth/login", json={"username": "owner.smirnova", "password": PASSWORD}
    )
    assert login.status_code == 200
    me = pilot_client.get("/auth/me")
    assert me.status_code == 200
    assert me.json()["working_mode"] == "manager"


def test_redeem_ticket_is_single_use_and_never_creates_a_second_pilot(
    pilot_client: TestClient,
) -> None:
    ticket = _claim(pilot_client).json()["ticket"]
    assert _redeem(pilot_client, ticket).status_code == 200
    second = _redeem(pilot_client, ticket)
    assert second.status_code == 409
    with _db_session(pilot_client) as db:
        assert db.scalar(select(func.count()).select_from(User)) == 1


def test_claim_and_redeem_blocked_once_owner_exists(pilot_client: TestClient) -> None:
    ticket = _claim(pilot_client).json()["ticket"]
    assert _redeem(pilot_client, ticket).status_code == 200
    with _db_session(pilot_client) as db:
        # A fresh, valid exchange (as if the token were rotated) still must
        # not allow a second first run.
        now = utc_now()
        db.add(
            BootstrapExchange(
                token_hash=hash_claim_value("f" * 64),
                created_at=now,
                expires_at=now + timedelta(minutes=60),
            )
        )
        db.commit()
    claim = _claim(pilot_client, token="f" * 64)
    assert claim.status_code == 409
    assert "уже завершена" in claim.json()["detail"]


@pytest.mark.parametrize(
    ("overrides", "message_part"),
    [
        ({"password": "short"}, "Пароль"),
        ({"timezone": "Mars/Olympus"}, "часов"),
        ({"workdays": [0]}, "Рабочие дни"),
        ({"workdays": [8]}, "Рабочие дни"),
        ({"workdays": [1, 1, 2]}, "повторяться"),
        ({"quiet_hours_start": "9:000"}, "HH:MM"),
        ({"quiet_hours_end": "24:00"}, "HH:MM"),
    ],
)
def test_redeem_validates_payload(
    pilot_client: TestClient, overrides: dict[str, Any], message_part: str
) -> None:
    ticket = _claim(pilot_client).json()["ticket"]
    response = _redeem(pilot_client, ticket, **overrides)
    assert response.status_code == 422, response.text
    assert message_part in response.json()["detail"]
    with _db_session(pilot_client) as db:
        assert db.scalar(select(func.count()).select_from(User)) == 0


def test_redeem_rejects_expired_ticket(pilot_client: TestClient) -> None:
    ticket = _claim(pilot_client).json()["ticket"]
    with _db_session(pilot_client) as db:
        row = db.scalar(select(BootstrapTicket))
        assert row is not None
        row.expires_at = utc_now() - timedelta(minutes=1)
        db.commit()
    assert _redeem(pilot_client, ticket).status_code == 410


def test_rate_limit_on_first_run_endpoints() -> None:
    settings = Settings.model_validate(
        {
            "APP_ENV": "test",
            "APP_DEBUG": "false",
            "SECRET_KEY": "unit-test-secret-key",
            "DATABASE_URL": SQLITE_URL,
            "PILOT_BOOTSTRAP_EXCHANGE_TOKEN": FIXTURE_TOKEN,
            "PILOT_SETUP_RATE_LIMIT": "3",
            "PILOT_SETUP_RATE_WINDOW_SECONDS": "300",
        }
    )
    engine = create_engine(
        SQLITE_URL,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        store_pilot_exchange(db, settings)
    app = create_app(settings, engine=engine)
    with TestClient(app) as client:
        codes = [
            client.post(
                "/setup/owner/preview",
                json={"ticket": secrets.token_urlsafe(32)},
                headers=LOOPBACK,
            ).status_code
            for _ in range(4)
        ]
    assert codes == [403, 403, 403, 429]
    engine.dispose()


def test_setup_state_reports_working_mode(pilot_client: TestClient) -> None:
    ticket = _claim(pilot_client).json()["ticket"]
    assert _redeem(pilot_client, ticket).status_code == 200
    state = pilot_client.get("/setup/state")
    assert state.status_code == 200
    assert state.json()["working_mode"] == "manager"
    assert state.json()["pilot_exists"] is True


def test_existing_setup_pilot_endpoint_blocks_second_pilot(
    pilot_client: TestClient,
) -> None:
    ticket = _claim(pilot_client).json()["ticket"]
    assert _redeem(pilot_client, ticket).status_code == 200
    # Authenticated as the new owner, the phase-8 wizard must refuse to
    # create another pilot account.
    create = pilot_client.post(
        "/setup/pilot",
        json={"username": "second", "password": "Another-Pass-2026", "full_name": "Второй"},
        headers={**LOOPBACK, "x-csrf-token": pilot_client.cookies.get("hrm_csrf", "")},
    )
    assert create.status_code == 409


# --- startup bootstrap ---------------------------------------------------------


def test_bootstrap_with_exchange_token_creates_no_admin(pilot_settings: Settings) -> None:
    engine = create_engine(
        SQLITE_URL,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        assert bootstrap_admin(db, pilot_settings) is None
        assert db.scalar(select(func.count()).select_from(User)) == 0
        exchange = db.scalar(select(BootstrapExchange))
        assert exchange is not None
        assert exchange.token_hash == hash_claim_value(FIXTURE_TOKEN)
        assert exchange.token_hash != FIXTURE_TOKEN
    engine.dispose()
