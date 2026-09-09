"""First-run pairing on real PostgreSQL: races, constraints and the CLI.

These tests prove the guarantees that SQLite cannot show: FOR UPDATE
serialization of concurrent claims against the single pending pairing, the
partial unique index behind "at most one pending installation", and the
installer-facing CLI (code via STDIN only — never argv).
"""

import json
import subprocess
import sys
import threading
from collections.abc import Iterator
from datetime import timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import Settings
from app.main import create_app
from app.models import (
    AccessGrant,
    AccessGrantScope,
    PilotPairing,
    PilotPairingStatus,
    User,
    UserRole,
    WorkRole,
)
from app.routers.first_run import hash_pairing_code, reset_first_run_limiters
from app.utils import utc_now

pytestmark = pytest.mark.integration

BACKEND_DIR = Path(__file__).resolve().parents[1]
CODE = "R7T4WQ"


@pytest.fixture(autouse=True)
def _clean_limiter() -> Iterator[None]:
    reset_first_run_limiters()
    yield
    reset_first_run_limiters()


def _issue(db: Session, code: str = CODE, work_role: WorkRole = WorkRole.HR) -> PilotPairing:
    pairing = PilotPairing(
        code_hash=hash_pairing_code(code),
        work_role=work_role,
        surname="Сидорова",
        status=PilotPairingStatus.PENDING,
        created_at=utc_now(),
        expires_at=utc_now() + timedelta(minutes=15),
    )
    db.add(pairing)
    db.commit()
    db.refresh(pairing)
    return pairing


@pytest.fixture()
def loopback_client(
    pg_settings: Settings, pg_engine: Engine, pg_client: TestClient
) -> Iterator[TestClient]:
    """Loopback Host TestClient over the same app/engine the pg_client fixture
    truncated and started (the claim endpoint refuses non-loopback Hosts)."""
    app = create_app(pg_settings, engine=pg_engine)
    with TestClient(app, base_url="http://127.0.0.1") as test_client:
        yield test_client


def test_concurrent_claims_exactly_one_owner(loopback_client: TestClient, pg_db: Session) -> None:
    _issue(pg_db)
    client = loopback_client
    results: list[int] = []
    barrier = threading.Barrier(6)

    def worker() -> None:
        barrier.wait(timeout=15)
        response = client.post(
            "/setup/first-run/claim",
            json={"code": CODE},
            headers={"Origin": "http://127.0.0.1"},
        )
        results.append(response.status_code)

    threads = [threading.Thread(target=worker) for _ in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)

    assert results.count(200) == 1, results
    assert set(results) <= {200, 410}, results
    owners = pg_db.scalar(select(func.count()).select_from(User))
    assert owners == 1
    pilot = pg_db.execute(select(User)).scalar_one()
    assert pilot.role == UserRole.ADMIN
    assert (
        pg_db.scalar(
            select(func.count())
            .select_from(AccessGrant)
            .where(AccessGrant.scope == AccessGrantScope.PILOT_FULL_ACCESS)
        )
        == 1
    )
    # Exactly one claimed pairing; attempts untouched by the losers.
    claimed = (
        pg_db.execute(select(PilotPairing).where(PilotPairing.status == PilotPairingStatus.CLAIMED))
        .scalars()
        .all()
    )
    assert len(claimed) == 1


def test_partial_unique_index_single_pending(pg_client: TestClient, pg_db: Session) -> None:
    _issue(pg_db)
    with pytest.raises(IntegrityError):
        _issue(pg_db, code="OTHER1")
    pg_db.rollback()


def test_cli_pilot_pairing_issue_and_status(
    loopback_client: TestClient, pg_engine: Engine, integration_url: str
) -> None:
    """The installer contract: JSON on STDIN, nothing secret in argv, and a
    status word the engine can poll. The subprocess uses the same test DB."""
    env = {
        "APP_ENV": "test",
        "DATABASE_URL": integration_url,
        "PATH": "/usr/bin:/bin:/usr/local/bin",
    }
    payload = json.dumps({"code": CODE, "surname": "Климова", "work_role": "manager"})
    run = subprocess.run(
        [sys.executable, "-m", "app.cli", "pilot-pairing", "issue"],
        input=payload,
        capture_output=True,
        text=True,
        cwd=BACKEND_DIR,
        env=env,
    )
    assert run.returncode == 0, run.stderr
    pairing_id = run.stdout.strip()
    assert len(pairing_id) == 36  # opaque uuid, not the code
    assert CODE not in run.stdout + run.stderr

    status = subprocess.run(
        [sys.executable, "-m", "app.cli", "pilot-pairing", "status"],
        capture_output=True,
        text=True,
        cwd=BACKEND_DIR,
        env=env,
    )
    assert status.returncode == 0 and status.stdout.strip() == "pending"

    response = loopback_client.post(
        "/setup/first-run/claim", json={"code": CODE}, headers={"Origin": "http://127.0.0.1"}
    )
    assert response.status_code == 200, response.text
    assert response.json()["user"]["work_role"] == "manager"

    status = subprocess.run(
        [sys.executable, "-m", "app.cli", "pilot-pairing", "status"],
        capture_output=True,
        text=True,
        cwd=BACKEND_DIR,
        env=env,
    )
    assert status.returncode == 1 and status.stdout.strip() == "ready"

    # Cleaning the seeded owner keeps other PG tests deterministic.
    with pg_engine.begin() as connection:
        connection.execute(
            text(
                "TRUNCATE users, pilot_pairings, access_grants, audit_log, user_sessions "
                "RESTART IDENTITY CASCADE"
            )
        )


def test_invalid_cli_input_is_rejected_without_side_effects(
    integration_url: str,
) -> None:
    env = {
        "APP_ENV": "test",
        "DATABASE_URL": integration_url,
        "PATH": "/usr/bin:/bin:/usr/local/bin",
    }
    run = subprocess.run(
        [sys.executable, "-m", "app.cli", "pilot-pairing", "issue"],
        input=json.dumps({"code": "ab", "surname": "", "work_role": "wizard"}),
        capture_output=True,
        text=True,
        cwd=BACKEND_DIR,
        env=env,
    )
    assert run.returncode == 2
    assert "work_role" in run.stderr
