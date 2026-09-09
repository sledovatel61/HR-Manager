"""Phase 12 integration proofs against real PostgreSQL.

The first-run exchange must create exactly one owner even under concurrency
(the atomic one-shot claim + the case-insensitive unique username index are
the database-level backstops), and the owner-based access model from phases
0–11 must be unaffected by the new pilot columns.
"""

from collections.abc import Iterator
from datetime import timedelta
from threading import Thread

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, func, select
from sqlalchemy.orm import Session

from app.config import Settings
from app.main import create_app
from app.models import AccessGrant, AccessGrantScope, PilotFirstRunClaim, User
from app.routers.setup import reset_first_run_limiter
from app.utils import utc_now

pytestmark = pytest.mark.integration

LOOPBACK = {"X-Forwarded-For": "127.0.0.1"}
TOKEN = "A" * 44


@pytest.fixture(autouse=True)
def _clean_limiter() -> Iterator[None]:
    reset_first_run_limiter()
    yield
    reset_first_run_limiter()


@pytest.fixture()
def pilot_pg_client(pg_engine: Engine, pg_settings: Settings) -> Iterator[TestClient]:
    settings = pg_settings.model_copy(
        update={
            "first_run_token": TOKEN,
            "first_run_expires_at": (utc_now() + timedelta(minutes=10)).isoformat(),
            "pilot_surname": "Петрова",
            "pilot_working_mode": "manager",
        }
    )
    app = create_app(settings, engine=pg_engine)
    with TestClient(app) as test_client:
        yield test_client


def test_first_run_is_single_pilot_under_concurrency(
    pilot_pg_client: TestClient, pg_db: Session
) -> None:
    """Two concurrent claims of the same token: exactly one owner survives."""
    results: list[int] = []

    def claim() -> None:
        response = pilot_pg_client.post(
            "/setup/first-run", json={"exchange_token": TOKEN}, headers=LOOPBACK
        )
        results.append(response.status_code)

    threads = [Thread(target=claim) for _ in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert results.count(200) == 1
    assert all(code in (200, 409, 429) for code in results)

    owners = pg_db.scalars(select(User)).all()
    assert len(owners) == 1
    assert owners[0].working_mode == "manager"
    grants = pg_db.scalars(
        select(AccessGrant).where(AccessGrant.scope == AccessGrantScope.PILOT_FULL_ACCESS)
    ).all()
    assert len(grants) == 1
    claims = pg_db.scalars(select(PilotFirstRunClaim)).all()
    assert len(claims) == 1


def test_first_run_refuses_when_database_is_populated(
    pilot_pg_client: TestClient, pg_db: Session
) -> None:
    from tests.conftest import make_user

    make_user(pg_db, username="existing-user")
    response = pilot_pg_client.post(
        "/setup/first-run", json={"exchange_token": TOKEN}, headers=LOOPBACK
    )
    assert response.status_code == 409
    assert pg_db.scalar(select(func.count()).select_from(User)) == 1
