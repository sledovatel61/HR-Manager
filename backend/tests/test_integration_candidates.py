"""Integration tests for the candidates database against a real PostgreSQL.

Run with::

    TEST_DATABASE_URL=postgresql+psycopg://user:pass@host:5432/db \
        pytest -m integration -v

The schema must already be applied (``alembic upgrade head``); fixtures
truncate the tables before each test. These mirror the SQLite unit tests in
``tests/test_candidates.py`` for the highest-risk flows and cover
PostgreSQL-specific behaviour: native UUIDs, server-side sort on
``stage_position``, case-insensitive full-name search via the normalized
column, and the ``audit_log.candidate_id`` foreign key.
"""

from collections.abc import Iterator

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import (
    AuditAction,
    AuditEvent,
    Candidate,
    CandidateInteraction,
    CandidateStage,
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


def _payload(**overrides: object) -> dict:
    payload: dict = {
        "full_name": "Сидоров Сидор Сидорович",
        "phone": "+7 911 555-44-33",
        "email": "sidorov@example.com",
        "source": "referral",
        "position": "Аналитик",
        "owner_user_id": None,
    }
    payload.update(overrides)
    return payload


def test_candidate_crud_on_postgres(pg_client: TestClient, pg_db: Session) -> None:
    hr = make_user(pg_db, username="hr1", role=UserRole.HR)
    csrf = _csrf(_login(pg_client, "hr1"))

    created = pg_client.post(
        "/candidates",
        json=_payload(owner_user_id=str(hr.id)),
        headers={"X-CSRF-Token": csrf},
    )
    assert created.status_code == 201
    body = created.json()
    assert body["stage"] == "new"
    assert body["owner_username"] == "hr1"

    fetched = pg_client.get(f"/candidates/{body['id']}")
    assert fetched.status_code == 200
    assert fetched.json()["full_name"] == "Сидоров Сидор Сидорович"

    updated = pg_client.patch(
        f"/candidates/{body['id']}",
        json={"stage": "offer", "position": "Старший аналитик"},
        headers={"X-CSRF-Token": csrf},
    )
    assert updated.status_code == 200
    assert updated.json()["stage"] == "offer"


def test_hr_scope_and_foreign_404_on_postgres(pg_client: TestClient, pg_db: Session) -> None:
    hr1 = make_user(pg_db, username="hr1", role=UserRole.HR)
    hr2 = make_user(pg_db, username="hr2", role=UserRole.HR)
    own = make_candidate(pg_db, owner=hr1, full_name="Свой")
    foreign = make_candidate(pg_db, owner=hr2, full_name="Чужой")

    _login(pg_client, "hr1")
    listing = pg_client.get("/candidates")
    assert listing.status_code == 200
    assert listing.json()["total"] == 1
    assert listing.json()["items"][0]["id"] == str(own.id)

    assert pg_client.get(f"/candidates/{foreign.id}").status_code == 404
    csrf = _csrf(_login(pg_client, "hr1"))
    assert (
        pg_client.patch(
            f"/candidates/{foreign.id}", json={"position": "x"}, headers={"X-CSRF-Token": csrf}
        ).status_code
        == 404
    )


def test_manager_sees_all_and_filters_by_owner_on_postgres(
    pg_client: TestClient, pg_db: Session
) -> None:
    hr1 = make_user(pg_db, username="hr1", role=UserRole.HR)
    hr2 = make_user(pg_db, username="hr2", role=UserRole.HR)
    make_user(pg_db, username="mgr", role=UserRole.MANAGER)
    make_candidate(pg_db, owner=hr1, full_name="Кандидат А")
    make_candidate(pg_db, owner=hr2, full_name="Кандидат Б")

    _login(pg_client, "mgr")
    assert pg_client.get("/candidates").json()["total"] == 2
    assert pg_client.get(f"/candidates?owner_id={hr2.id}").json()["total"] == 1


def test_soft_delete_and_restore_on_postgres(pg_client: TestClient, pg_db: Session) -> None:
    hr = make_user(pg_db, username="hr1", role=UserRole.HR)
    candidate = make_candidate(pg_db, owner=hr, full_name="На удаление")
    csrf = _csrf(_login(pg_client, "hr1"))

    deleted = pg_client.delete(f"/candidates/{candidate.id}", headers={"X-CSRF-Token": csrf})
    assert deleted.status_code == 200
    assert deleted.json()["is_deleted"] is True

    # Excluded from normal listings.
    assert pg_client.get("/candidates").json()["total"] == 0

    # The row survives physically (soft delete; PostgreSQL timestamptz).
    pg_db.expire_all()
    stored = pg_db.get(Candidate, candidate.id)
    assert stored is not None
    assert stored.deleted_at is not None

    restored = pg_client.post(f"/candidates/{candidate.id}/restore", headers={"X-CSRF-Token": csrf})
    assert restored.status_code == 200
    assert restored.json()["is_deleted"] is False
    assert pg_client.get("/candidates").json()["total"] == 1


def test_search_filters_sort_and_pagination_on_postgres(
    pg_client: TestClient, pg_db: Session
) -> None:
    hr = make_user(pg_db, username="hr1", role=UserRole.HR)
    make_candidate(
        pg_db,
        owner=hr,
        full_name="Иванов Иван",
        phone="+7 111 111-11-11",
        email="i.ivanov@example.com",
        stage=CandidateStage.NEW,
    )
    make_candidate(
        pg_db,
        owner=hr,
        full_name="Петров Пётр",
        phone="+7 222 222-22-22",
        email="p.petrov@example.com",
        stage=CandidateStage.HIRED,
    )
    make_candidate(
        pg_db,
        owner=hr,
        full_name="Сидоров Сидор",
        phone="+7 333 333-33-33",
        email="s.sidorov@example.com",
        stage=CandidateStage.INTERVIEW_DONE,
    )
    _login(pg_client, "hr1")

    # Case-insensitive Cyrillic name search (normalized column).
    by_name = pg_client.get("/candidates?query=ПЕТРОВ")
    assert by_name.json()["total"] == 1

    # Funnel-ordered stage sort (server-side via stage_position).
    ascending = pg_client.get("/candidates?sort=stage&direction=asc")
    assert [item["stage"] for item in ascending.json()["items"]] == [
        "new",
        "interview_done",
        "hired",
    ]

    # Server-side pagination.
    page = pg_client.get("/candidates?sort=full_name&direction=asc&limit=2&offset=1")
    assert page.json()["total"] == 3
    assert len(page.json()["items"]) == 2


def test_duplicate_detection_on_postgres(pg_client: TestClient, pg_db: Session) -> None:
    hr = make_user(pg_db, username="hr1", role=UserRole.HR)
    make_candidate(pg_db, owner=hr, phone="+7 900 000-00-00")
    csrf = _csrf(_login(pg_client, "hr1"))

    conflict = pg_client.post(
        "/candidates",
        json=_payload(owner_user_id=str(hr.id), phone="8 900 000 00 00", email=None),
        headers={"X-CSRF-Token": csrf},
    )
    assert conflict.status_code == 409
    assert len(conflict.json()["detail"]["duplicates"]) == 1

    confirmed = pg_client.post(
        "/candidates",
        json=_payload(
            owner_user_id=str(hr.id),
            phone="8 900 000 00 00",
            email=None,
            confirm_duplicate=True,
        ),
        headers={"X-CSRF-Token": csrf},
    )
    assert confirmed.status_code == 201


def test_interactions_on_postgres(pg_client: TestClient, pg_db: Session) -> None:
    hr = make_user(pg_db, username="hr1", role=UserRole.HR)
    candidate = make_candidate(pg_db, owner=hr)
    csrf = _csrf(_login(pg_client, "hr1"))

    created = pg_client.post(
        f"/candidates/{candidate.id}/interactions",
        json={"type": "meeting", "comment": "Провели техническое собеседование"},
        headers={"X-CSRF-Token": csrf},
    )
    assert created.status_code == 201
    assert created.json()["author_username"] == "hr1"

    listing = pg_client.get(f"/candidates/{candidate.id}/interactions")
    assert listing.status_code == 200
    assert listing.json()["total"] == 1

    assert pg_db.scalar(select(func.count()).select_from(CandidateInteraction)) == 1


def test_audit_links_to_candidate_on_postgres(pg_client: TestClient, pg_db: Session) -> None:
    hr = make_user(pg_db, username="hr1", role=UserRole.HR)
    make_user(pg_db, username="adm", role=UserRole.ADMIN)
    csrf = _csrf(_login(pg_client, "hr1"))

    created = pg_client.post(
        "/candidates",
        json=_payload(owner_user_id=str(hr.id)),
        headers={"X-CSRF-Token": csrf},
    )
    candidate_id = created.json()["id"]
    pg_client.patch(
        f"/candidates/{candidate_id}", json={"stage": "hired"}, headers={"X-CSRF-Token": csrf}
    )
    pg_client.delete(f"/candidates/{candidate_id}", headers={"X-CSRF-Token": csrf})
    pg_client.post(f"/candidates/{candidate_id}/restore", headers={"X-CSRF-Token": csrf})

    events = pg_db.scalars(
        select(AuditEvent)
        .where(AuditEvent.candidate_id == candidate_id)
        .order_by(AuditEvent.created_at)
    ).all()
    assert [event.action for event in events] == [
        AuditAction.CANDIDATE_CREATED,
        AuditAction.CANDIDATE_STAGE_CHANGED,
        AuditAction.CANDIDATE_DELETED,
        AuditAction.CANDIDATE_RESTORED,
    ]
    # Audit details must never contain candidate personal data.
    for event in events:
        assert "Сидоров" not in (event.details or "")
        assert "sidorov@" not in (event.details or "")
        assert "911" not in (event.details or "")

    # The admin audit API exposes the candidate link (UUID only, no PII).
    admin_csrf = _csrf(_login(pg_client, "adm"))
    audit_listing = pg_client.get("/admin/audit?limit=200", headers={"X-CSRF-Token": admin_csrf})
    assert audit_listing.status_code == 200
    api_events = sorted(
        (
            item
            for item in audit_listing.json()["items"]
            if item.get("candidate_id") == candidate_id
        ),
        key=lambda item: item["created_at"],
    )
    assert [item["action"] for item in api_events] == [
        "candidate_created",
        "candidate_stage_changed",
        "candidate_deleted",
        "candidate_restored",
    ]
    for item in api_events:
        assert "Сидоров" not in (item["details"] or "")
        assert "sidorov@" not in (item["details"] or "")


def test_stage_vocabulary_on_postgres(pg_client: TestClient, pg_db: Session) -> None:
    """All 11 PRODUCT_SPEC §5 funnel stages are accepted; anything else 422."""
    hr = make_user(pg_db, username="hr1", role=UserRole.HR)
    candidate = make_candidate(pg_db, owner=hr)
    csrf = _csrf(_login(pg_client, "hr1"))

    for stage in (
        "new",
        "contacted",
        "reached",
        "interview_scheduled",
        "interview_done",
        "offer",
        "hired",
        "started",
        "probation",
        "fired",
        "rejected",
    ):
        assert (
            pg_client.patch(
                f"/candidates/{candidate.id}", json={"stage": stage}, headers={"X-CSRF-Token": csrf}
            ).status_code
            == 200
        )

    assert (
        pg_client.patch(
            f"/candidates/{candidate.id}",
            json={"stage": "fantasy_stage"},
            headers={"X-CSRF-Token": csrf},
        ).status_code
        == 422
    )


def test_candidate_survives_owner_deletion_guard_on_postgres(
    pg_client: TestClient, pg_db: Session
) -> None:
    """Deleting the owner user must not orphan candidates (RESTRICT)."""
    hr = make_user(pg_db, username="hr1", role=UserRole.HR)
    make_candidate(pg_db, owner=hr)

    with pytest.raises(IntegrityError):  # RESTRICT FK from candidates.owner_user_id
        pg_db.delete(hr)
        pg_db.commit()
    pg_db.rollback()
