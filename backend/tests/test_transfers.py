"""Unit tests for candidate ownership transfer (in-memory SQLite).

Covers: the atomic hand-over (owner change + immutable business history +
PII-free audit), authorization (HR own-only, manager/admin any visible
candidate), validation (new owner must be a different active HR, non-blank
reason), the 404 semantics for foreign/deleted candidates, transfer history
visibility after the hand-over, and the deleted-candidates list used by the
workspace UI. Integration mirrors run on PostgreSQL in
``tests/test_integration_transfers.py`` (including concurrency).
"""

from collections.abc import Iterator
from uuid import uuid4

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import (
    AuditAction,
    AuditEvent,
    CandidateTransfer,
    UserRole,
)
from app.routers.auth import reset_login_limiter
from tests.conftest import FIXTURE_PASSWORD, make_candidate, make_transfer, make_user


@pytest.fixture(autouse=True)
def _clean_limiter() -> Iterator[None]:
    reset_login_limiter()
    yield
    reset_login_limiter()


def _login(client: TestClient, username: str, password: str = FIXTURE_PASSWORD) -> httpx.Response:
    return client.post("/auth/login", json={"username": username, "password": password})


def _csrf(response: httpx.Response) -> str:
    return response.json()["csrf_token"]


def _payload(**overrides: object) -> dict:
    payload: dict = {
        "full_name": "Петров Пётр Петрович",
        "source": "site",
        "position": "Инженер",
        "phone": None,
        "email": None,
        "owner_user_id": None,
    }
    payload.update(overrides)
    return payload


# --- Happy paths -------------------------------------------------------------


def test_hr_transfers_own_candidate(client: TestClient, db_session: Session) -> None:
    hr1 = make_user(db_session, username="hr1", role=UserRole.HR)
    hr2 = make_user(db_session, username="hr2", role=UserRole.HR)
    candidate = make_candidate(db_session, owner=hr1, full_name="Кандидат А")
    csrf = _csrf(_login(client, "hr1"))

    response = client.post(
        f"/candidates/{candidate.id}/transfer",
        json={"new_owner_user_id": str(hr2.id), "reason": "Передаю из-за нагрузки"},
        headers={"X-CSRF-Token": csrf},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    # Typed result: the UI can update itself without refetching.
    assert body["candidate"]["owner_user_id"] == str(hr2.id)
    assert body["candidate"]["owner_username"] == "hr2"
    transfer = body["transfer"]
    assert transfer["initiator_user_id"] == str(hr1.id)
    assert transfer["from_user_id"] == str(hr1.id)
    assert transfer["to_user_id"] == str(hr2.id)
    assert transfer["reason"] == "Передаю из-за нагрузки"

    # Business history row exists with full context.
    stored = db_session.scalars(select(CandidateTransfer)).all()
    assert len(stored) == 1
    assert stored[0].from_username == "hr1"
    assert stored[0].to_username == "hr2"

    # Audit event exists with ids only — never PII or the reason text.
    event = db_session.scalar(
        select(AuditEvent).where(AuditEvent.action == AuditAction.CANDIDATE_TRANSFERRED)
    )
    assert event is not None
    assert event.candidate_id == candidate.id
    assert "Передаю" not in (event.details or "")
    assert "Петров" not in (event.details or "")


def test_manager_and_admin_transfer_any_visible_candidate(
    client: TestClient, db_session: Session
) -> None:
    hr1 = make_user(db_session, username="hr1", role=UserRole.HR)
    hr2 = make_user(db_session, username="hr2", role=UserRole.HR)
    make_user(db_session, username="mgr", role=UserRole.MANAGER)
    make_user(db_session, username="adm", role=UserRole.ADMIN)
    candidate = make_candidate(db_session, owner=hr1)

    mgr_csrf = _csrf(_login(client, "mgr"))
    response = client.post(
        f"/candidates/{candidate.id}/transfer",
        json={"new_owner_user_id": str(hr2.id), "reason": "Перераспределение"},
        headers={"X-CSRF-Token": mgr_csrf},
    )
    assert response.status_code == 200
    assert response.json()["transfer"]["initiator_username"] == "mgr"
    assert response.json()["candidate"]["owner_username"] == "hr2"

    # Transfer back as admin.
    adm_csrf = _csrf(_login(client, "adm"))
    back = client.post(
        f"/candidates/{candidate.id}/transfer",
        json={"new_owner_user_id": str(hr1.id), "reason": "Возврат"},
        headers={"X-CSRF-Token": adm_csrf},
    )
    assert back.status_code == 200
    assert back.json()["candidate"]["owner_username"] == "hr1"


def test_reason_is_trimmed_and_kept(client: TestClient, db_session: Session) -> None:
    hr1 = make_user(db_session, username="hr1", role=UserRole.HR)
    hr2 = make_user(db_session, username="hr2", role=UserRole.HR)
    candidate = make_candidate(db_session, owner=hr1)
    csrf = _csrf(_login(client, "hr1"))

    response = client.post(
        f"/candidates/{candidate.id}/transfer",
        json={"new_owner_user_id": str(hr2.id), "reason": "  Нагрузка  "},
        headers={"X-CSRF-Token": csrf},
    )
    assert response.status_code == 200
    assert response.json()["transfer"]["reason"] == "Нагрузка"


# --- Authorization and 404 semantics ----------------------------------------


def test_hr_cannot_transfer_foreign_candidate_404(client: TestClient, db_session: Session) -> None:
    make_user(db_session, username="hr1", role=UserRole.HR)
    hr2 = make_user(db_session, username="hr2", role=UserRole.HR)
    hr3 = make_user(db_session, username="hr3", role=UserRole.HR)
    foreign = make_candidate(db_session, owner=hr2)
    csrf = _csrf(_login(client, "hr1"))

    response = client.post(
        f"/candidates/{foreign.id}/transfer",
        json={"new_owner_user_id": str(hr3.id), "reason": "Захват"},
        headers={"X-CSRF-Token": csrf},
    )
    assert response.status_code == 404


def test_transfer_requires_csrf(client: TestClient, db_session: Session) -> None:
    hr1 = make_user(db_session, username="hr1", role=UserRole.HR)
    hr2 = make_user(db_session, username="hr2", role=UserRole.HR)
    candidate = make_candidate(db_session, owner=hr1)
    _login(client, "hr1")

    response = client.post(
        f"/candidates/{candidate.id}/transfer",
        json={"new_owner_user_id": str(hr2.id), "reason": "Без CSRF"},
    )
    assert response.status_code == 403


# --- Validation --------------------------------------------------------------


def test_transfer_rejects_same_owner(client: TestClient, db_session: Session) -> None:
    hr1 = make_user(db_session, username="hr1", role=UserRole.HR)
    candidate = make_candidate(db_session, owner=hr1)
    csrf = _csrf(_login(client, "hr1"))

    response = client.post(
        f"/candidates/{candidate.id}/transfer",
        json={"new_owner_user_id": str(hr1.id), "reason": "Себе же"},
        headers={"X-CSRF-Token": csrf},
    )
    assert response.status_code == 400
    assert db_session.scalar(select(func.count()).select_from(CandidateTransfer)) == 0


def test_transfer_rejects_blank_reason(client: TestClient, db_session: Session) -> None:
    hr1 = make_user(db_session, username="hr1", role=UserRole.HR)
    hr2 = make_user(db_session, username="hr2", role=UserRole.HR)
    candidate = make_candidate(db_session, owner=hr1)
    csrf = _csrf(_login(client, "hr1"))

    for reason in ("", "   ", None):
        response = client.post(
            f"/candidates/{candidate.id}/transfer",
            json={"new_owner_user_id": str(hr2.id), "reason": reason},
            headers={"X-CSRF-Token": csrf},
        )
        assert response.status_code == 422


def test_transfer_rejects_inactive_manager_and_admin_as_owner(
    client: TestClient, db_session: Session
) -> None:
    hr1 = make_user(db_session, username="hr1", role=UserRole.HR)
    candidate = make_candidate(db_session, owner=hr1)
    inactive = make_user(db_session, username="hr_off", role=UserRole.HR, is_active=False)
    manager = make_user(db_session, username="mgr", role=UserRole.MANAGER)
    admin = make_user(db_session, username="adm", role=UserRole.ADMIN)
    csrf = _csrf(_login(client, "hr1"))

    for target in (inactive, manager, admin):
        response = client.post(
            f"/candidates/{candidate.id}/transfer",
            json={"new_owner_user_id": str(target.id), "reason": "Кому попало"},
            headers={"X-CSRF-Token": csrf},
        )
        assert response.status_code == 422

    ghost = client.post(
        f"/candidates/{candidate.id}/transfer",
        json={"new_owner_user_id": str(uuid4()), "reason": "Призрак"},
        headers={"X-CSRF-Token": csrf},
    )
    assert ghost.status_code == 422


def test_transfer_rejects_deleted_candidate(client: TestClient, db_session: Session) -> None:
    hr1 = make_user(db_session, username="hr1", role=UserRole.HR)
    hr2 = make_user(db_session, username="hr2", role=UserRole.HR)
    candidate = make_candidate(db_session, owner=hr1, deleted=True)
    csrf = _csrf(_login(client, "hr1"))

    response = client.post(
        f"/candidates/{candidate.id}/transfer",
        json={"new_owner_user_id": str(hr2.id), "reason": "Воскрешение"},
        headers={"X-CSRF-Token": csrf},
    )
    assert response.status_code == 404


# --- History visibility and pagination ---------------------------------------


def test_former_owner_loses_history_access_after_transfer(
    client: TestClient, db_session: Session
) -> None:
    hr1 = make_user(db_session, username="hr1", role=UserRole.HR)
    hr2 = make_user(db_session, username="hr2", role=UserRole.HR)
    candidate = make_candidate(db_session, owner=hr1)
    csrf = _csrf(_login(client, "hr1"))

    transferred = client.post(
        f"/candidates/{candidate.id}/transfer",
        json={"new_owner_user_id": str(hr2.id), "reason": "Передача"},
        headers={"X-CSRF-Token": csrf},
    )
    assert transferred.status_code == 200

    # The former owner can no longer read the card or the history.
    assert client.get(f"/candidates/{candidate.id}").status_code == 404
    assert client.get(f"/candidates/{candidate.id}/transfers").status_code == 404

    # The new owner, a manager and an admin see the history.
    _login(client, "hr2")
    history = client.get(f"/candidates/{candidate.id}/transfers")
    assert history.status_code == 200
    assert history.json()["total"] == 1
    assert history.json()["items"][0]["reason"] == "Передача"

    make_user(db_session, username="mgr", role=UserRole.MANAGER)
    _login(client, "mgr")
    assert client.get(f"/candidates/{candidate.id}/transfers").json()["total"] == 1


def test_transfer_history_pagination(client: TestClient, db_session: Session) -> None:
    hr1 = make_user(db_session, username="hr1", role=UserRole.HR)
    hr2 = make_user(db_session, username="hr2", role=UserRole.HR)
    hr3 = make_user(db_session, username="hr3", role=UserRole.HR)
    candidate = make_candidate(db_session, owner=hr1)
    make_transfer(db_session, candidate=candidate, initiator=hr1, from_user=hr1, to_user=hr2)
    make_transfer(db_session, candidate=candidate, initiator=hr2, from_user=hr2, to_user=hr3)
    # Re-point ownership for the test to reflect the last record.
    candidate.owner_user_id = hr3.id
    db_session.commit()

    make_user(db_session, username="adm", role=UserRole.ADMIN)
    _login(client, "adm")

    page1 = client.get(f"/candidates/{candidate.id}/transfers?limit=1&offset=0")
    assert page1.json()["total"] == 2
    assert len(page1.json()["items"]) == 1
    assert page1.json()["items"][0]["to_username"] == "hr2"
    page2 = client.get(f"/candidates/{candidate.id}/transfers?limit=1&offset=1")
    assert page2.json()["items"][0]["to_username"] == "hr3"


# --- Deleted candidates list (workspace view) --------------------------------


def test_include_deleted_lists_only_soft_deleted(client: TestClient, db_session: Session) -> None:
    hr1 = make_user(db_session, username="hr1", role=UserRole.HR)
    make_candidate(db_session, owner=hr1, full_name="Живой")
    make_candidate(db_session, owner=hr1, full_name="Удалённый", deleted=True)
    _login(client, "hr1")

    regular = client.get("/candidates")
    assert regular.json()["total"] == 1

    deleted = client.get("/candidates?include_deleted=true")
    assert deleted.json()["total"] == 1
    assert deleted.json()["items"][0]["full_name"] == "Удалённый"
    assert deleted.json()["items"][0]["is_deleted"] is True

    # Deleted candidates of other HRs stay invisible (no leak).
    hr2 = make_user(db_session, username="hr2", role=UserRole.HR)
    make_candidate(db_session, owner=hr2, full_name="Чужой удалённый", deleted=True)
    still_one = client.get("/candidates?include_deleted=true")
    assert still_one.json()["total"] == 1


def test_hr_directory_is_safe(client: TestClient, db_session: Session) -> None:
    hr1 = make_user(db_session, username="hr1", role=UserRole.HR)
    make_user(db_session, username="hr2", role=UserRole.HR)
    make_user(db_session, username="hr_off", role=UserRole.HR, is_active=False)
    make_user(db_session, username="mgr", role=UserRole.MANAGER)
    _login(client, "hr1")

    directory = client.get("/admin/users/hr")
    assert directory.status_code == 200
    assert directory.json()["total"] == 2
    usernames = {item["username"] for item in directory.json()["items"]}
    assert usernames == {"hr1", "hr2"}
    # Minimal safe fields only — no admin data.
    assert set(directory.json()["items"][0].keys()) == {
        "id",
        "username",
        "full_name",
        "role",
        "is_active",
    }
    assert hr1.id is not None
