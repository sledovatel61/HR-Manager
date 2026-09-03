"""Integration tests for candidate transfer against a real PostgreSQL.

Run with::

    TEST_DATABASE_URL=postgresql+psycopg://user:pass@host:5432/db \
        pytest -m integration -v

The schema must already be applied (``alembic upgrade head``). Covers the
atomicity/concurrency guarantees of ``POST /candidates/{id}/transfer`` that
SQLite unit tests cannot: the candidate row lock (``SELECT ... FOR UPDATE``)
and the invariant that a concurrent double transfer leaves exactly one
consistent business-history record.
"""

import threading
from collections.abc import Iterator
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, func, select
from sqlalchemy.orm import Session

from app.config import Settings
from app.models import (
    AuditAction,
    AuditEvent,
    Candidate,
    CandidateTransfer,
    User,
    UserRole,
)
from app.routers.auth import reset_login_limiter
from tests.conftest import FIXTURE_PASSWORD, make_candidate, make_user

pytestmark = pytest.mark.integration


@pytest.fixture(autouse=True)
def _clean_limiter() -> Iterator[None]:
    reset_login_limiter()
    yield
    reset_login_limiter()


def _login(client: TestClient, username: str, password: str = FIXTURE_PASSWORD) -> httpx.Response:
    return client.post("/auth/login", json={"username": username, "password": password})


def _csrf(response: httpx.Response) -> str:
    return response.json()["csrf_token"]


def test_transfer_roundtrip_on_postgres(pg_client: TestClient, pg_db: Session) -> None:
    hr1 = make_user(pg_db, username="hr1", role=UserRole.HR)
    hr2 = make_user(pg_db, username="hr2", role=UserRole.HR)
    candidate = make_candidate(pg_db, owner=hr1, full_name="Интеграционный кандидат")
    csrf = _csrf(_login(pg_client, "hr1"))

    response = pg_client.post(
        f"/candidates/{candidate.id}/transfer",
        json={"new_owner_user_id": str(hr2.id), "reason": "Интеграционная причина"},
        headers={"X-CSRF-Token": csrf},
    )
    assert response.status_code == 200, response.text
    assert response.json()["candidate"]["owner_username"] == "hr2"
    assert response.json()["transfer"]["reason"] == "Интеграционная причина"

    # History is visible to the new owner and paginated.
    _login(pg_client, "hr2")
    history = pg_client.get(f"/candidates/{candidate.id}/transfers")
    assert history.status_code == 200
    assert history.json()["total"] == 1

    # Audit: transferred event, candidate-linked, PII-free details.
    event = pg_db.scalar(
        select(AuditEvent).where(
            AuditEvent.candidate_id == candidate.id,
            AuditEvent.action == AuditAction.CANDIDATE_TRANSFERRED,
        )
    )
    assert event is not None
    assert "Интеграционная причина" not in (event.details or "")
    assert "Интеграционный" not in (event.details or "")


def test_foreign_transfer_404_and_same_owner_400_on_postgres(
    pg_client: TestClient, pg_db: Session
) -> None:
    make_user(pg_db, username="hr1", role=UserRole.HR)
    hr2 = make_user(pg_db, username="hr2", role=UserRole.HR)
    hr3 = make_user(pg_db, username="hr3", role=UserRole.HR)
    foreign = make_candidate(pg_db, owner=hr2)
    csrf = _csrf(_login(pg_client, "hr1"))

    blocked = pg_client.post(
        f"/candidates/{foreign.id}/transfer",
        json={"new_owner_user_id": str(hr3.id), "reason": "Не моё"},
        headers={"X-CSRF-Token": csrf},
    )
    assert blocked.status_code == 404

    own = make_candidate(pg_db, owner=hr2)
    _login(pg_client, "hr2")
    hr2_csrf = _csrf(_login(pg_client, "hr2"))
    same = pg_client.post(
        f"/candidates/{own.id}/transfer",
        json={"new_owner_user_id": str(hr2.id), "reason": "Себе же"},
        headers={"X-CSRF-Token": hr2_csrf},
    )
    assert same.status_code == 400


def test_transfer_history_survives_candidate_soft_delete_on_postgres(
    pg_client: TestClient, pg_db: Session
) -> None:
    """Soft delete keeps the transfer history; visibility follows the card."""
    hr1 = make_user(pg_db, username="hr1", role=UserRole.HR)
    hr2 = make_user(pg_db, username="hr2", role=UserRole.HR)
    candidate = make_candidate(pg_db, owner=hr1)
    csrf = _csrf(_login(pg_client, "hr1"))
    assert (
        pg_client.post(
            f"/candidates/{candidate.id}/transfer",
            json={"new_owner_user_id": str(hr2.id), "reason": "История"},
            headers={"X-CSRF-Token": csrf},
        ).status_code
        == 200
    )

    _login(pg_client, "hr2")
    hr2_csrf = _csrf(_login(pg_client, "hr2"))
    assert (
        pg_client.delete(
            f"/candidates/{candidate.id}", headers={"X-CSRF-Token": hr2_csrf}
        ).status_code
        == 200
    )
    # Deleted card hides its history too; the rows themselves persist.
    assert pg_client.get(f"/candidates/{candidate.id}/transfers").status_code == 404
    assert pg_db.scalar(select(func.count()).select_from(CandidateTransfer)) == 1


def test_concurrent_transfer_leaves_one_consistent_record(
    pg_client: TestClient, pg_db: Session, pg_settings: Settings, pg_engine: Engine
) -> None:
    """Two simultaneous transfers of the same candidate must not interleave.

    The candidate row is locked FOR UPDATE inside the transaction, so exactly
    one request wins; the loser receives 409 and the business history stays
    consistent with the final owner.
    """
    from app.main import create_app

    hr1 = make_user(pg_db, username="hr1", role=UserRole.HR)
    hr2 = make_user(pg_db, username="hr2", role=UserRole.HR)
    hr3 = make_user(pg_db, username="hr3", role=UserRole.HR)
    candidate = make_candidate(pg_db, owner=hr1, full_name="Гоночный кандидат")

    # Two independent clients (separate sessions, same engine).
    app = create_app(pg_settings, engine=pg_engine)
    client_a = TestClient(app)
    client_b = TestClient(app)
    try:
        csrf_a = _csrf(_login(client_a, "hr1"))
        csrf_b = _csrf(_login(client_b, "hr1"))

        barrier = threading.Barrier(2)
        results: dict[str, int] = {}

        def do_transfer(name: str, client: TestClient, csrf: str, target: User) -> None:
            barrier.wait()
            response = client.post(
                f"/candidates/{candidate.id}/transfer",
                json={"new_owner_user_id": str(target.id), "reason": f"Забег {name}"},
                headers={"X-CSRF-Token": csrf},
            )
            results[name] = response.status_code

        threads = [
            threading.Thread(target=do_transfer, args=("a", client_a, csrf_a, hr2)),
            threading.Thread(target=do_transfer, args=("b", client_b, csrf_b, hr3)),
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
    finally:
        client_a.close()
        client_b.close()

    statuses = sorted(results.values())
    # Exactly one request wins. The loser either detects the concurrent
    # ownership change on the locked row (409) or, if its visibility check
    # ran after the commit, sees an already-foreign candidate (404 — the
    # established no-leak semantics). Both are correct.
    assert statuses[0] == 200 and statuses[1] in (404, 409), results

    pg_db.expire_all()
    stored = pg_db.get(Candidate, candidate.id)
    assert stored is not None
    final_owner = stored.owner_user_id
    records = pg_db.scalars(
        select(CandidateTransfer).where(CandidateTransfer.candidate_id == candidate.id)
    ).all()
    # Exactly one immutable history record, matching the final owner.
    assert len(records) == 1
    assert records[0].to_user_id == final_owner


def test_transfer_is_atomic_when_audit_write_fails_on_postgres(
    pg_client: TestClient, pg_db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Same atomicity contract as the SQLite unit test, on PostgreSQL.

    If the audit write fails inside the transaction, the rollback must keep
    the original owner and leave no transfer-history and no audit rows —
    the ownership change must never persist without its audit event.
    """
    from app.audit import record_event as real_record_event
    from app.routers import candidates as candidates_router

    hr1 = make_user(pg_db, username="hr1", role=UserRole.HR)
    hr2 = make_user(pg_db, username="hr2", role=UserRole.HR)
    candidate = make_candidate(pg_db, owner=hr1)
    csrf = _csrf(_login(pg_client, "hr1"))

    def failing_record_event(db: Session, *args: Any, **kwargs: Any) -> None:
        real_record_event(db, *args, **kwargs)
        db.flush()
        raise RuntimeError("simulated audit write failure")

    monkeypatch.setattr(candidates_router, "record_event", failing_record_event)

    with pytest.raises(RuntimeError, match="simulated audit write failure"):
        pg_client.post(
            f"/candidates/{candidate.id}/transfer",
            json={"new_owner_user_id": str(hr2.id), "reason": "Не должно сохраниться"},
            headers={"X-CSRF-Token": csrf},
        )

    pg_db.expire_all()
    stored = pg_db.get(Candidate, candidate.id)
    assert stored is not None
    assert stored.owner_user_id == hr1.id
    assert pg_db.scalar(select(func.count()).select_from(CandidateTransfer)) == 0
    assert (
        pg_db.scalar(
            select(func.count())
            .select_from(AuditEvent)
            .where(AuditEvent.action == AuditAction.CANDIDATE_TRANSFERRED)
        )
        == 0
    )
