"""Integration tests for calendar events against a real PostgreSQL.

Run with::

    TEST_DATABASE_URL=postgresql+psycopg://user:pass@host:5432/db \\
        pytest -m integration -v

The schema must already be applied (``alembic upgrade head``). Covers what
SQLite unit tests cannot: timezone-aware round-trips, the optimistic
concurrency guarantee under a real row lock, the single-transaction rollback
contract and real FK/CHECK constraints.
"""

import threading
from collections.abc import Iterator
from datetime import timedelta
from typing import Any
from urllib.parse import quote

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import Settings
from app.main import create_app
from app.models import (
    AuditAction,
    AuditEvent,
    Event,
    EventHistory,
    UserRole,
)
from app.routers.auth import reset_login_limiter
from app.utils import utc_now
from tests.conftest import FIXTURE_PASSWORD, make_candidate, make_event, make_user

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


def _in(minutes: int) -> str:
    return (utc_now() + timedelta(minutes=minutes)).isoformat()


def _payload(candidate_id: str, **overrides: object) -> dict:
    payload: dict = {
        "candidate_id": candidate_id,
        "type": "call",
        "title": "Интеграционный созвон",
        "starts_at": _in(60),
        "ends_at": None,
        "remind_at": None,
        "assignee_user_id": None,
    }
    payload.update(overrides)
    return payload


def test_event_roundtrip_keeps_timezone_on_postgres(pg_client: TestClient, pg_db: Session) -> None:
    hr1 = make_user(pg_db, username="hr1", role=UserRole.HR)
    candidate = make_candidate(pg_db, owner=hr1)
    csrf = _csrf(_login(pg_client, "hr1"))

    created = pg_client.post(
        "/events", json=_payload(str(candidate.id)), headers={"X-CSRF-Token": csrf}
    )
    assert created.status_code == 201, created.text
    starts_at = created.json()["starts_at"]
    assert starts_at.endswith("+00:00") or starts_at.endswith("Z")
    assert created.json()["version"] == 1

    fetched = pg_client.get(f"/events/{created.json()['id']}")
    assert fetched.status_code == 200
    assert fetched.json()["starts_at"] == created.json()["starts_at"]

    # The stored value is timezone-aware.
    stored = pg_db.get(Event, created.json()["id"])
    assert stored is not None and stored.starts_at.tzinfo is not None


def test_period_filters_and_pagination_on_postgres(pg_client: TestClient, pg_db: Session) -> None:
    hr1 = make_user(pg_db, username="hr1", role=UserRole.HR)
    candidate = make_candidate(pg_db, owner=hr1)
    _login(pg_client, "hr1")

    for hours in (1, 2, 72):
        make_event(
            pg_db,
            candidate=candidate,
            author=hr1,
            assignee=hr1,
            starts_at=utc_now() + timedelta(hours=hours),
        )

    window_from = quote((utc_now() + timedelta(minutes=30)).isoformat(), safe="")
    window_to = quote((utc_now() + timedelta(hours=3)).isoformat(), safe="")
    page = pg_client.get(f"/events?from={window_from}&to={window_to}&limit=1&offset=1")
    assert page.status_code == 200
    assert page.json()["total"] == 2
    assert len(page.json()["items"]) == 1


def test_soft_deleted_candidate_events_hidden_on_postgres(
    pg_client: TestClient, pg_db: Session
) -> None:
    hr1 = make_user(pg_db, username="hr1", role=UserRole.HR)
    make_user(pg_db, username="adm", role=UserRole.ADMIN)
    candidate = make_candidate(pg_db, owner=hr1)
    event = make_event(pg_db, candidate=candidate, author=hr1, assignee=hr1)

    csrf = _csrf(_login(pg_client, "hr1"))
    deleted = pg_client.delete(f"/candidates/{candidate.id}", headers={"X-CSRF-Token": csrf})
    assert deleted.status_code == 200
    assert pg_client.get("/events").json()["total"] == 0
    assert pg_client.get(f"/events/{event.id}").status_code == 404

    _login(pg_client, "adm")
    assert pg_client.get("/events").json()["total"] == 0
    assert pg_client.get(f"/events/{event.id}").status_code == 404
    # The rows still exist (no physical deletion anywhere).
    assert pg_db.scalar(select(func.count()).select_from(Event)) == 1


def test_patch_rollback_when_audit_fails_on_postgres(
    pg_client: TestClient, pg_db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.audit import record_event as real_record_event
    from app.routers import events as events_router

    hr1 = make_user(pg_db, username="hr1", role=UserRole.HR)
    candidate = make_candidate(pg_db, owner=hr1)
    event = make_event(pg_db, candidate=candidate, author=hr1, assignee=hr1)
    csrf = _csrf(_login(pg_client, "hr1"))

    def failing_record_event(db: Session, *args: Any, **kwargs: Any) -> None:
        real_record_event(db, *args, **kwargs)
        db.flush()
        raise RuntimeError("simulated audit write failure")

    monkeypatch.setattr(events_router, "record_event", failing_record_event)

    with pytest.raises(RuntimeError, match="simulated audit write failure"):
        pg_client.patch(
            f"/events/{event.id}",
            json={"expected_version": 1, "title": "Не должно сохраниться"},
            headers={"X-CSRF-Token": csrf},
        )

    pg_db.expire_all()
    stored = pg_db.get(Event, event.id)
    assert stored is not None
    assert stored.title == "Созвон"
    assert stored.version == 1
    assert pg_db.scalar(select(func.count()).select_from(EventHistory)) == 0
    assert (
        pg_db.scalar(
            select(func.count())
            .select_from(AuditEvent)
            .where(AuditEvent.action == AuditAction.EVENT_UPDATED)
        )
        == 0
    )


def test_concurrent_patch_cannot_lose_updates(
    pg_client: TestClient, pg_db: Session, pg_settings: Settings, pg_engine: Engine
) -> None:
    """Two simultaneous PATCHes with the same expected_version: exactly one
    wins; the loser gets 409 and the event holds the winner's version."""
    hr1 = make_user(pg_db, username="hr1", role=UserRole.HR)
    candidate = make_candidate(pg_db, owner=hr1)
    event = make_event(pg_db, candidate=candidate, author=hr1, assignee=hr1)

    app = create_app(pg_settings, engine=pg_engine)
    client_a = TestClient(app)
    client_b = TestClient(app)
    try:
        csrf_a = _csrf(_login(client_a, "hr1"))
        csrf_b = _csrf(_login(client_b, "hr1"))

        barrier = threading.Barrier(2)
        results: dict[str, int] = {}

        def do_patch(name: str, client: TestClient, csrf: str, title: str) -> None:
            barrier.wait()
            response = client.patch(
                f"/events/{event.id}",
                json={"expected_version": 1, "title": title},
                headers={"X-CSRF-Token": csrf},
            )
            results[name] = response.status_code

        threads = [
            threading.Thread(target=do_patch, args=("a", client_a, csrf_a, "Победитель A")),
            threading.Thread(target=do_patch, args=("b", client_b, csrf_b, "Победитель B")),
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
    finally:
        client_a.close()
        client_b.close()

    assert sorted(results.values()) == [200, 409], results

    pg_db.expire_all()
    stored = pg_db.get(Event, event.id)
    assert stored is not None
    assert stored.version == 2
    assert stored.title in {"Победитель A", "Победитель B"}
    # Exactly one history entry beyond the fixture state (fixture writes none).
    assert pg_db.scalar(select(func.count()).select_from(EventHistory)) == 1


def test_real_check_constraints_on_postgres(pg_client: TestClient, pg_db: Session) -> None:
    """The migration's CHECK constraints hold at the database level."""
    hr1 = make_user(pg_db, username="hr1", role=UserRole.HR)
    candidate = make_candidate(pg_db, owner=hr1)
    _login(pg_client, "hr1")

    # Blank title → ck_events_title_not_blank.
    with pytest.raises(IntegrityError):
        make_event(pg_db, candidate=candidate, author=hr1, assignee=hr1, title="   ")
    pg_db.rollback()
    # ends_at <= starts_at → ck_events_ends_after_starts.
    start = utc_now() + timedelta(hours=1)
    with pytest.raises(IntegrityError):
        make_event(
            pg_db,
            candidate=candidate,
            author=hr1,
            assignee=hr1,
            starts_at=start,
            ends_at=start,
        )
    pg_db.rollback()

    # Invalid status values are rejected by the vocabulary check.
    with pytest.raises(IntegrityError):
        pg_db.execute(
            text(
                "INSERT INTO events (candidate_id, author_user_id, assignee_user_id, "
                "type, title, status, starts_at) VALUES "
                "(:candidate_id, :author, :assignee, 'call', 'x', 'done', now())"
            ),
            {
                "candidate_id": candidate.id,
                "author": hr1.id,
                "assignee": hr1.id,
            },
        )
    pg_db.rollback()


def test_history_is_paginated_and_visible_to_manager_on_postgres(
    pg_client: TestClient, pg_db: Session
) -> None:
    hr1 = make_user(pg_db, username="hr1", role=UserRole.HR)
    make_user(pg_db, username="mgr", role=UserRole.MANAGER)
    candidate = make_candidate(pg_db, owner=hr1)
    csrf = _csrf(_login(pg_client, "hr1"))

    created = pg_client.post(
        "/events", json=_payload(str(candidate.id)), headers={"X-CSRF-Token": csrf}
    )
    assert created.status_code == 201
    event_id = created.json()["id"]
    pg_client.patch(
        f"/events/{event_id}",
        json={"expected_version": 1, "status": "completed"},
        headers={"X-CSRF-Token": csrf},
    )

    _login(pg_client, "mgr")
    history = pg_client.get(f"/events/{event_id}/history?limit=1&offset=0")
    assert history.status_code == 200
    assert history.json()["total"] == 2
    assert len(history.json()["items"]) == 1
    assert history.json()["items"][0]["kind"] == "created"
    page2 = pg_client.get(f"/events/{event_id}/history?limit=1&offset=1")
    assert page2.json()["items"][0]["kind"] == "completed"
    assert page2.json()["items"][0]["status_new"] == "completed"


# --- Regression tests (orchestrator review of PR #7) ------------------------


def test_manager_assignee_required_on_postgres(pg_client: TestClient, pg_db: Session) -> None:
    """manager/admin must explicitly pick an active HR assignee.

    Regression: _resolve_assignee fell back to the current user, so a
    non-HR assignee could be created on PostgreSQL too.
    """
    hr1 = make_user(pg_db, username="hr1", role=UserRole.HR)
    make_user(pg_db, username="mgr", role=UserRole.MANAGER)
    make_user(pg_db, username="adm", role=UserRole.ADMIN)
    candidate = make_candidate(pg_db, owner=hr1)

    mgr_csrf = _csrf(_login(pg_client, "mgr"))
    missing = pg_client.post(
        "/events", json=_payload(str(candidate.id)), headers={"X-CSRF-Token": mgr_csrf}
    )
    assert missing.status_code == 422

    _login(pg_client, "adm")
    adm_csrf = _csrf(_login(pg_client, "adm"))
    missing_admin = pg_client.post(
        "/events", json=_payload(str(candidate.id)), headers={"X-CSRF-Token": adm_csrf}
    )
    assert missing_admin.status_code == 422

    mgr_csrf = _csrf(_login(pg_client, "mgr"))
    ok = pg_client.post(
        "/events",
        json=_payload(str(candidate.id), assignee_user_id=str(hr1.id)),
        headers={"X-CSRF-Token": mgr_csrf},
    )
    assert ok.status_code == 201
    assert ok.json()["assignee_user_id"] == str(hr1.id)
    # The stored row really points at the HR, not at the manager.
    stored = pg_db.get(Event, ok.json()["id"])
    assert stored is not None and stored.assignee_user_id == hr1.id


def test_patch_clears_nullable_fields_on_postgres(pg_client: TestClient, pg_db: Session) -> None:
    """Explicit null clears note/ends_at/remind_at with correct history and
    PII-free audit details (PostgreSQL)."""
    hr1 = make_user(pg_db, username="hr1", role=UserRole.HR)
    candidate = make_candidate(pg_db, owner=hr1)
    csrf = _csrf(_login(pg_client, "hr1"))

    created = pg_client.post(
        "/events",
        json=_payload(
            str(candidate.id),
            note="Заметка для очистки",
            ends_at=_in(90),
            remind_at=_in(30),
        ),
        headers={"X-CSRF-Token": csrf},
    )
    assert created.status_code == 201
    event_id = created.json()["id"]

    cleared = pg_client.patch(
        f"/events/{event_id}",
        json={"expected_version": 1, "note": None, "ends_at": None, "remind_at": None},
        headers={"X-CSRF-Token": csrf},
    )
    assert cleared.status_code == 200, cleared.text
    body = cleared.json()
    assert body["note"] is None and body["ends_at"] is None and body["remind_at"] is None
    assert body["version"] == 2

    pg_db.expire_all()
    stored = pg_db.get(Event, event_id)
    assert stored is not None
    assert stored.note is None and stored.ends_at is None and stored.remind_at is None

    entries = pg_db.scalars(
        select(EventHistory)
        .where(EventHistory.event_id == event_id)
        .order_by(EventHistory.created_at)
    ).all()
    assert [entry.kind for entry in entries] == ["created", "rescheduled"]
    change = entries[1]
    assert change.note_changed is True
    assert change.ends_at_old is not None and change.ends_at_new is None
    assert change.remind_at_old is not None and change.remind_at_new is None

    audit_entry = pg_db.scalars(
        select(AuditEvent).where(
            AuditEvent.candidate_id == candidate.id,
            AuditEvent.action == AuditAction.EVENT_RESCHEDULED,
        )
    ).one()
    details = audit_entry.details or ""
    for name in ("ends_at", "remind_at", "note"):
        assert name in details
    assert "Заметка для очистки" not in details
