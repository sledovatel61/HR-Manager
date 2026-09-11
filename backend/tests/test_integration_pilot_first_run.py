"""Phase 12 first-run integration tests (real PostgreSQL).

Proves the single-flight claim/redeem under real concurrency, the
append-only trigger guards on the claim tables, expiry enforcement and that
only hashes (never raw tokens/tickets) are persisted. Runs with
``pytest -m integration`` against ``TEST_DATABASE_URL``.
"""

import secrets
import threading
from collections.abc import Iterator
from typing import Any, cast

import pytest
from fastapi.testclient import TestClient
from httpx import Response
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.config import Settings
from app.models import (
    AccessGrant,
    AccessGrantScope,
    AuditAction,
    AuditEvent,
    BootstrapExchange,
    BootstrapTicket,
    User,
)
from app.setup_owner import hash_claim_value, store_pilot_exchange

pytestmark = pytest.mark.integration

LOOPBACK = {"x-real-ip": "127.0.0.1"}
PASSWORD = "Str0ng-Pilot-Pass-2026"

_TRUNCATE_SQL = (
    "TRUNCATE TABLE audit_log, event_history, events, candidate_transfers, "
    "candidate_interactions, candidates, user_sessions, users, "
    "candidate_telegram_links, candidate_telegram_link_tokens, "
    "candidate_channel_consents, notification_delivery_attempts, "
    "notification_outbox, notifications, notification_preferences, "
    "telegram_start_events, telegram_link_tokens, telegram_links, "
    "telegram_poll_state, user_emails, access_grants, bootstrap_tickets, "
    "bootstrap_exchanges RESTART IDENTITY CASCADE"
)


def _claim(client: TestClient, token: str, surname: str = "Смирнова") -> Response:
    return client.post(
        "/setup/owner/claim",
        json={
            "exchange_token": token,
            "surname": surname,
            "working_mode": "hr",
            "timezone": "Europe/Moscow",
        },
        headers=LOOPBACK,
    )


def _redeem(client: TestClient, ticket: str) -> Response:
    return client.post(
        "/setup/owner/redeem",
        json={
            "ticket": ticket,
            "timezone": "Europe/Moscow",
            "workdays": [1, 2, 3, 4, 5],
            "quiet_hours_start": "21:00",
            "quiet_hours_end": "08:00",
            "password": PASSWORD,
        },
        headers=LOOPBACK,
    )


@pytest.fixture()
def pilot_app_client(pg_settings: Any, pg_engine: Any) -> Iterator[TestClient]:
    """Isolated pilot app: truncated tables + a fresh exchange token."""
    from app.main import create_app

    with pg_engine.begin() as connection:
        connection.execute(text(_TRUNCATE_SQL))
    token = secrets.token_hex(32)
    settings = cast(Settings, pg_settings).model_copy(
        update={"pilot_bootstrap_exchange_token": token}
    )
    with Session(pg_engine) as db:
        store_pilot_exchange(db, settings)
    app = create_app(settings, engine=pg_engine)
    with TestClient(app) as client:
        client.raw_token = token
        client.pg_engine = pg_engine
        yield client


def test_concurrent_claims_with_same_token_yield_one_ticket(
    pilot_app_client: TestClient,
) -> None:
    token = pilot_app_client.raw_token
    results: list[Response] = []
    barrier = threading.Barrier(2)

    def run() -> None:
        barrier.wait()
        results.append(_claim(pilot_app_client, token))

    threads = [threading.Thread(target=run) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    codes = sorted(response.status_code for response in results)
    assert codes == [201, 403], [response.text for response in results]
    with Session(pilot_app_client.pg_engine) as db:
        tickets = db.scalars(select(BootstrapTicket)).all()
        assert len(tickets) == 1
        exchange = db.scalar(select(BootstrapExchange))
        assert exchange is not None
        assert exchange.consumed_at is not None


def test_concurrent_redeems_create_exactly_one_owner(
    pilot_app_client: TestClient,
) -> None:
    ticket = _claim(pilot_app_client, pilot_app_client.raw_token).json()["ticket"]
    results: list[Response] = []
    barrier = threading.Barrier(2)

    def run() -> None:
        barrier.wait()
        results.append(_redeem(pilot_app_client, ticket))

    threads = [threading.Thread(target=run) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    codes = sorted(response.status_code for response in results)
    assert codes == [200, 409], [response.text for response in results]
    with Session(pilot_app_client.pg_engine) as db:
        users = db.scalars(select(User)).all()
        assert len(users) == 1
        grants = db.scalars(
            select(AccessGrant).where(AccessGrant.scope == AccessGrantScope.PILOT_FULL_ACCESS)
        ).all()
        assert len(grants) == 1


def test_claim_tables_are_append_only(pilot_app_client: TestClient) -> None:
    """The trigger guards reject every UPDATE except the single consumption
    transition, and any DELETE.

    Each violation runs on a fresh connection: PostgreSQL aborts the
    transaction of a failed statement, so the connection is invalidated
    and closed (the server rolls back on close) instead of attempting a
    ROLLBACK some drivers refuse after a failed statement."""
    engine = pilot_app_client.pg_engine

    def execute_guard_violations(statements: list[str]) -> None:
        for statement in statements:
            conn = engine.connect()
            try:
                with pytest.raises(Exception, match="bootstrap"):
                    conn.exec_driver_sql(statement)
            finally:
                conn.invalidate()
                conn.close()

    ticket = _claim(pilot_app_client, pilot_app_client.raw_token).json()["ticket"]
    # Unconsumed rows cannot be edited or deleted.
    execute_guard_violations(
        [
            "UPDATE bootstrap_tickets SET surname = 'hacked'",
            "DELETE FROM bootstrap_exchanges",
        ]
    )
    assert _redeem(pilot_app_client, ticket).status_code == 200
    # Consumed rows cannot be reverted or deleted.
    execute_guard_violations(
        [
            "UPDATE bootstrap_tickets SET consumed_at = NULL",
            "DELETE FROM bootstrap_tickets",
        ]
    )


def test_exchange_expiry_is_enforced(pilot_app_client: TestClient) -> None:
    # The claim tables are append-only, so the only way to simulate an
    # expired exchange is to insert one that is already expired.
    expired_token = secrets.token_hex(32)
    with pilot_app_client.pg_engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO bootstrap_exchanges "
                "(id, token_hash, created_at, expires_at) "
                "VALUES (gen_random_uuid(), :token_hash, now() - interval '2 hours', "
                "now() - interval '1 hour')"
            ),
            {"token_hash": hash_claim_value(expired_token)},
        )
    response = _claim(pilot_app_client, expired_token)
    assert response.status_code == 403
    assert "устарел" in response.json()["detail"]


def test_audit_has_no_token_or_surname(pilot_app_client: TestClient) -> None:
    token = pilot_app_client.raw_token
    _claim(pilot_app_client, token)
    with Session(pilot_app_client.pg_engine) as db:
        for event in db.scalars(select(AuditEvent)).all():
            details = event.details or ""
            assert token not in details
            assert "Смирнова" not in details
        claimed = db.scalar(
            select(AuditEvent).where(AuditEvent.action == AuditAction.PILOT_OWNER_CLAIMED)
        )
        assert claimed is not None
        assert claimed.details == "working_mode=hr"


def test_database_stores_only_hashes(pilot_app_client: TestClient) -> None:
    token = pilot_app_client.raw_token
    body = _claim(pilot_app_client, token).json()
    with Session(pilot_app_client.pg_engine) as db:
        exchange = db.scalar(select(BootstrapExchange))
        ticket = db.scalar(select(BootstrapTicket))
        assert exchange is not None
        assert ticket is not None
        assert exchange.token_hash == hash_claim_value(token)
        assert exchange.token_hash != token
        assert ticket.ticket_hash == hash_claim_value(body["ticket"])
        assert ticket.ticket_hash != body["ticket"]
