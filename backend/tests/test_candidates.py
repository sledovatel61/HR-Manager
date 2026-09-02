"""Unit tests for the candidates database (in-memory SQLite, APP_ENV=test).

Authorization matrix covered here:

* unauthenticated -> 401 on every candidate endpoint;
* HR sees and mutates only their own candidates (foreign -> 404);
* manager/admin see all candidates and may filter by owner;
* CSRF enforced on mutating methods (via the existing session dependency).

Also covered: funnel stage validation, soft delete/restore, server-side
search/filters/sort/pagination, duplicate protection with explicit
confirmation, interaction history, and candidate audit events.

Integration mirrors run against real PostgreSQL in
``tests/test_integration_candidates.py``.
"""

from collections.abc import Iterator

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import (
    AuditAction,
    AuditEvent,
    Candidate,
    CandidateInteraction,
    CandidateSource,
    CandidateStage,
    User,
    UserRole,
)
from app.routers.auth import reset_login_limiter
from tests.conftest import FIXTURE_PASSWORD, make_candidate, make_user


@pytest.fixture(autouse=True)
def _clean_limiter() -> Iterator[None]:
    # The login rate limiter is process-global; reset it so unrelated tests
    # never consume each other's login budget.
    reset_login_limiter()
    yield
    reset_login_limiter()


def _login(client: TestClient, username: str, password: str = FIXTURE_PASSWORD) -> httpx.Response:
    return client.post("/auth/login", json={"username": username, "password": password})


def _csrf(response: httpx.Response) -> str:
    return response.json()["csrf_token"]


def _candidate_payload(**overrides: object) -> dict:
    payload: dict = {
        "full_name": "Петров Пётр Петрович",
        "phone": "+7 900 123-45-67",
        "email": "petrov@example.com",
        "source": "site",
        "position": "Инженер",
        "owner_user_id": None,
    }
    payload.update(overrides)
    return payload


# --- Authentication and authorization ---------------------------------------


def test_unauthenticated_gets_401(client: TestClient, db_session: Session) -> None:
    hr = make_user(db_session, username="hr1", role=UserRole.HR)
    candidate = make_candidate(db_session, owner=hr)

    assert client.get("/candidates").status_code == 401
    assert client.get(f"/candidates/{candidate.id}").status_code == 401
    assert client.post("/candidates", json={}).status_code == 401
    assert client.patch(f"/candidates/{candidate.id}", json={}).status_code == 401
    assert client.delete(f"/candidates/{candidate.id}").status_code == 401
    assert client.post(f"/candidates/{candidate.id}/restore").status_code == 401
    assert client.get(f"/candidates/{candidate.id}/interactions").status_code == 401


def test_hr_sees_only_own_candidates(client: TestClient, db_session: Session) -> None:
    hr1 = make_user(db_session, username="hr1", role=UserRole.HR)
    hr2 = make_user(db_session, username="hr2", role=UserRole.HR)
    own = make_candidate(db_session, owner=hr1, full_name="Свой кандидат")
    foreign = make_candidate(db_session, owner=hr2, full_name="Чужой кандидат")

    client.post("/auth/login", json={"username": "hr1", "password": FIXTURE_PASSWORD})

    listing = client.get("/candidates")
    assert listing.status_code == 200
    names = [item["full_name"] for item in listing.json()["items"]]
    assert names == ["Свой кандидат"]
    assert listing.json()["total"] == 1

    assert client.get(f"/candidates/{own.id}").status_code == 200
    # Foreign candidates are hidden with 404 (existence is not leaked).
    assert client.get(f"/candidates/{foreign.id}").status_code == 404


def test_manager_and_admin_see_all_and_filter_by_owner(
    client: TestClient, db_session: Session
) -> None:
    hr1 = make_user(db_session, username="hr1", role=UserRole.HR)
    hr2 = make_user(db_session, username="hr2", role=UserRole.HR)
    make_user(db_session, username="mgr", role=UserRole.MANAGER)
    make_user(db_session, username="adm", role=UserRole.ADMIN)
    make_candidate(db_session, owner=hr1, full_name="Кандидат А")
    make_candidate(db_session, owner=hr2, full_name="Кандидат Б")

    client.post("/auth/login", json={"username": "mgr", "password": FIXTURE_PASSWORD})
    listing = client.get("/candidates")
    assert listing.status_code == 200
    assert listing.json()["total"] == 2

    filtered = client.get(f"/candidates?owner_id={hr1.id}")
    assert filtered.status_code == 200
    assert filtered.json()["total"] == 1
    assert filtered.json()["items"][0]["owner_user_id"] == str(hr1.id)

    client.post(
        "/auth/logout",
        headers={
            "X-CSRF-Token": _csrf(
                client.post("/auth/login", json={"username": "mgr", "password": FIXTURE_PASSWORD})
            )
        },
    )
    client.post("/auth/login", json={"username": "adm", "password": FIXTURE_PASSWORD})
    assert client.get("/candidates").json()["total"] == 2


def test_hr_cannot_mutate_foreign_candidate(client: TestClient, db_session: Session) -> None:
    make_user(db_session, username="hr1", role=UserRole.HR)
    hr2 = make_user(db_session, username="hr2", role=UserRole.HR)
    foreign = make_candidate(db_session, owner=hr2, full_name="Чужой")

    login = client.post("/auth/login", json={"username": "hr1", "password": FIXTURE_PASSWORD})
    csrf = _csrf(login)

    patch_foreign = client.patch(
        f"/candidates/{foreign.id}", json={"position": "x"}, headers={"X-CSRF-Token": csrf}
    )
    assert patch_foreign.status_code == 404
    delete_foreign = client.delete(f"/candidates/{foreign.id}", headers={"X-CSRF-Token": csrf})
    assert delete_foreign.status_code == 404
    interact_foreign = client.post(
        f"/candidates/{foreign.id}/interactions",
        json={"type": "note", "comment": "hi"},
        headers={"X-CSRF-Token": csrf},
    )
    assert interact_foreign.status_code == 404


def test_csrf_required_for_candidate_mutations(client: TestClient, db_session: Session) -> None:
    hr = make_user(db_session, username="hr1", role=UserRole.HR)
    candidate = make_candidate(db_session, owner=hr)

    login = client.post("/auth/login", json={"username": "hr1", "password": FIXTURE_PASSWORD})
    assert login.status_code == 200

    # No CSRF header -> 403 even with a valid session.
    assert client.patch(f"/candidates/{candidate.id}", json={"position": "x"}).status_code == 403
    assert client.delete(f"/candidates/{candidate.id}").status_code == 403


# --- CRUD --------------------------------------------------------------------


def test_create_read_update_candidate(client: TestClient, db_session: Session) -> None:
    hr = make_user(db_session, username="hr1", role=UserRole.HR)
    login = client.post("/auth/login", json={"username": "hr1", "password": FIXTURE_PASSWORD})
    csrf = _csrf(login)

    created = client.post(
        "/candidates",
        json=_candidate_payload(owner_user_id=str(hr.id)),
        headers={"X-CSRF-Token": csrf},
    )
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["full_name"] == "Петров Пётр Петрович"
    assert body["stage"] == "new"
    assert body["owner_user_id"] == str(hr.id)
    assert body["owner_username"] == "hr1"

    fetched = client.get(f"/candidates/{body['id']}")
    assert fetched.status_code == 200
    assert fetched.json()["email"] == "petrov@example.com"

    updated = client.patch(
        f"/candidates/{body['id']}",
        json={"position": "Старший инженер", "stage": "interview_scheduled"},
        headers={"X-CSRF-Token": csrf},
    )
    assert updated.status_code == 200
    assert updated.json()["position"] == "Старший инженер"
    assert updated.json()["stage"] == "interview_scheduled"


def test_create_without_owner_defaults_to_creator(client: TestClient, db_session: Session) -> None:
    make_user(db_session, username="hr1", role=UserRole.HR)
    csrf = _csrf(_login(client, "hr1"))

    created = client.post(
        "/candidates",
        json=_candidate_payload(owner_user_id=None),
        headers={"X-CSRF-Token": csrf},
    )
    assert created.status_code == 201
    assert created.json()["owner_username"] == "hr1"


def test_hr_cannot_create_for_another_owner(client: TestClient, db_session: Session) -> None:
    make_user(db_session, username="hr1", role=UserRole.HR)
    hr2 = make_user(db_session, username="hr2", role=UserRole.HR)
    login = client.post("/auth/login", json={"username": "hr1", "password": FIXTURE_PASSWORD})
    csrf = _csrf(login)

    response = client.post(
        "/candidates",
        json=_candidate_payload(owner_user_id=str(hr2.id)),
        headers={"X-CSRF-Token": csrf},
    )
    assert response.status_code == 403


def test_manager_can_assign_any_owner(client: TestClient, db_session: Session) -> None:
    make_user(db_session, username="mgr", role=UserRole.MANAGER)
    hr = make_user(db_session, username="hr1", role=UserRole.HR)
    login = client.post("/auth/login", json={"username": "mgr", "password": FIXTURE_PASSWORD})
    csrf = _csrf(login)

    created = client.post(
        "/candidates",
        json=_candidate_payload(owner_user_id=str(hr.id)),
        headers={"X-CSRF-Token": csrf},
    )
    assert created.status_code == 201
    assert created.json()["owner_user_id"] == str(hr.id)


def test_stage_validation_uses_shared_vocabulary(client: TestClient, db_session: Session) -> None:
    hr = make_user(db_session, username="hr1", role=UserRole.HR)
    candidate = make_candidate(db_session, owner=hr)
    login = client.post("/auth/login", json={"username": "hr1", "password": FIXTURE_PASSWORD})
    csrf = _csrf(login)

    invalid = client.patch(
        f"/candidates/{candidate.id}",
        json={"stage": "not_a_stage"},
        headers={"X-CSRF-Token": csrf},
    )
    assert invalid.status_code == 422

    # All eleven PRODUCT_SPEC §5 stages are accepted.
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
            client.patch(
                f"/candidates/{candidate.id}",
                json={"stage": stage},
                headers={"X-CSRF-Token": csrf},
            ).status_code
            == 200
        )


def test_invalid_source_rejected(client: TestClient, db_session: Session) -> None:
    hr = make_user(db_session, username="hr1", role=UserRole.HR)
    login = client.post("/auth/login", json={"username": "hr1", "password": FIXTURE_PASSWORD})
    csrf = _csrf(login)

    response = client.post(
        "/candidates",
        json=_candidate_payload(owner_user_id=str(hr.id), source="radio"),
        headers={"X-CSRF-Token": csrf},
    )
    assert response.status_code == 422


# --- Soft delete and restore -------------------------------------------------


def test_soft_delete_excludes_and_restore_brings_back(
    client: TestClient, db_session: Session
) -> None:
    hr = make_user(db_session, username="hr1", role=UserRole.HR)
    candidate = make_candidate(db_session, owner=hr)
    login = client.post("/auth/login", json={"username": "hr1", "password": FIXTURE_PASSWORD})
    csrf = _csrf(login)

    deleted = client.delete(f"/candidates/{candidate.id}", headers={"X-CSRF-Token": csrf})
    assert deleted.status_code == 200
    assert deleted.json()["is_deleted"] is True
    assert deleted.json()["deleted_at"] is not None

    # Excluded from regular lists and direct GET.
    assert client.get("/candidates").json()["total"] == 0
    assert client.get(f"/candidates/{candidate.id}").status_code == 404

    # Physical delete does not exist: the row is still there (ORM check).
    assert db_session.get(Candidate, candidate.id) is not None

    restored = client.post(f"/candidates/{candidate.id}/restore", headers={"X-CSRF-Token": csrf})
    assert restored.status_code == 200
    assert restored.json()["is_deleted"] is False
    assert client.get("/candidates").json()["total"] == 1

    # Restoring a live candidate is an error.
    again = client.post(f"/candidates/{candidate.id}/restore", headers={"X-CSRF-Token": csrf})
    assert again.status_code == 400


def test_deleting_an_already_deleted_candidate_is_404(
    client: TestClient, db_session: Session
) -> None:
    hr = make_user(db_session, username="hr1", role=UserRole.HR)
    candidate = make_candidate(db_session, owner=hr, deleted=True)
    login = client.post("/auth/login", json={"username": "hr1", "password": FIXTURE_PASSWORD})
    csrf = _csrf(login)
    deleted_again = client.delete(f"/candidates/{candidate.id}", headers={"X-CSRF-Token": csrf})
    assert deleted_again.status_code == 404


# --- Search, filters, sorting, pagination ------------------------------------


def _seed_candidates(db_session: Session, hr: User) -> None:
    make_candidate(
        db_session,
        owner=hr,
        full_name="Абрамов Алексей",
        phone="+7 111 111-11-11",
        email="a.abramov@example.com",
        source=CandidateSource.SITE,
        position="dev",
        stage=CandidateStage.NEW,
    )
    make_candidate(
        db_session,
        owner=hr,
        full_name="Борисова Анна",
        phone="+7 222 222-22-22",
        email="a.borisova@example.com",
        source=CandidateSource.REFERRAL,
        position="qa",
        stage=CandidateStage.INTERVIEW_SCHEDULED,
    )
    make_candidate(
        db_session,
        owner=hr,
        full_name="Волков Сергей",
        phone="+7 333 333-33-33",
        email="s.volkov@example.com",
        source=CandidateSource.EVENT,
        position="dev",
        stage=CandidateStage.OFFER,
    )


def test_search_by_name_phone_and_email(client: TestClient, db_session: Session) -> None:
    hr = make_user(db_session, username="hr1", role=UserRole.HR)
    _seed_candidates(db_session, hr)
    client.post("/auth/login", json={"username": "hr1", "password": FIXTURE_PASSWORD})

    by_name = client.get("/candidates?query=борисов")
    assert by_name.json()["total"] == 1
    assert by_name.json()["items"][0]["full_name"] == "Борисова Анна"

    by_phone = client.get("/candidates?query=222-22-22")
    assert by_phone.json()["total"] == 1
    assert by_phone.json()["items"][0]["full_name"] == "Борисова Анна"

    by_email = client.get("/candidates?query=S.VOLKOV@example.com")
    assert by_email.json()["total"] == 1
    assert by_email.json()["items"][0]["full_name"] == "Волков Сергей"


def test_filter_by_stage_and_source(client: TestClient, db_session: Session) -> None:
    hr = make_user(db_session, username="hr1", role=UserRole.HR)
    _seed_candidates(db_session, hr)
    client.post("/auth/login", json={"username": "hr1", "password": FIXTURE_PASSWORD})

    stage_filter = client.get("/candidates?stage=offer")
    assert stage_filter.json()["total"] == 1
    assert stage_filter.json()["items"][0]["stage"] == "offer"

    source_filter = client.get("/candidates?source=referral")
    assert source_filter.json()["total"] == 1
    assert source_filter.json()["items"][0]["source"] == "referral"

    combined = client.get("/candidates?source=referral&stage=offer")
    assert combined.json()["total"] == 0


def test_sorting_by_stage_follows_funnel_order(client: TestClient, db_session: Session) -> None:
    hr = make_user(db_session, username="hr1", role=UserRole.HR)
    _seed_candidates(db_session, hr)
    client.post("/auth/login", json={"username": "hr1", "password": FIXTURE_PASSWORD})

    ascending = client.get("/candidates?sort=stage&direction=asc")
    stages = [item["stage"] for item in ascending.json()["items"]]
    assert stages == ["new", "interview_scheduled", "offer"]

    descending = client.get("/candidates?sort=stage&direction=desc")
    stages = [item["stage"] for item in descending.json()["items"]]
    assert stages == ["offer", "interview_scheduled", "new"]


def test_pagination_is_server_side(client: TestClient, db_session: Session) -> None:
    hr = make_user(db_session, username="hr1", role=UserRole.HR)
    _seed_candidates(db_session, hr)
    client.post("/auth/login", json={"username": "hr1", "password": FIXTURE_PASSWORD})

    page1 = client.get("/candidates?sort=full_name&direction=asc&limit=2&offset=0")
    assert page1.json()["total"] == 3
    assert len(page1.json()["items"]) == 2
    page2 = client.get("/candidates?sort=full_name&direction=asc&limit=2&offset=2")
    assert len(page2.json()["items"]) == 1

    # Bad sort field falls back to the default (created_at desc) — no error.
    assert client.get("/candidates?sort=evil").status_code == 200
    # Bad direction is rejected by the query pattern.
    assert client.get("/candidates?direction=sideways").status_code == 422


# --- Duplicate protection ----------------------------------------------------


def test_duplicate_phone_requires_confirmation(client: TestClient, db_session: Session) -> None:
    hr = make_user(db_session, username="hr1", role=UserRole.HR)
    make_candidate(db_session, owner=hr, phone="+7 900 000-00-00")
    login = client.post("/auth/login", json={"username": "hr1", "password": FIXTURE_PASSWORD})
    csrf = _csrf(login)

    # Same phone in a different format -> still a duplicate, 409 with matches.
    conflict = client.post(
        "/candidates",
        json=_candidate_payload(owner_user_id=str(hr.id), phone="8 900 000 00 00", email=None),
        headers={"X-CSRF-Token": csrf},
    )
    assert conflict.status_code == 409
    detail = conflict.json()["detail"]
    assert len(detail["duplicates"]) == 1
    assert "confirm_duplicate=true" in detail["message"]

    # Confirmed duplicate creation succeeds and is audited as such.
    created = client.post(
        "/candidates",
        json=_candidate_payload(
            owner_user_id=str(hr.id),
            phone="8 900 000 00 00",
            email=None,
            confirm_duplicate=True,
        ),
        headers={"X-CSRF-Token": csrf},
    )
    assert created.status_code == 201
    assert (
        db_session.scalar(
            select(func.count())
            .select_from(AuditEvent)
            .where(AuditEvent.action == AuditAction.DUPLICATE_CANDIDATE_CREATED)
        )
        == 1
    )


def test_duplicate_email_blocked_on_update(client: TestClient, db_session: Session) -> None:
    hr = make_user(db_session, username="hr1", role=UserRole.HR)
    make_candidate(db_session, owner=hr, email="first@example.com")
    second = make_candidate(db_session, owner=hr, email="second@example.com")
    login = client.post("/auth/login", json={"username": "hr1", "password": FIXTURE_PASSWORD})
    csrf = _csrf(login)

    conflict = client.patch(
        f"/candidates/{second.id}",
        json={"email": "FIRST@example.com"},
        headers={"X-CSRF-Token": csrf},
    )
    assert conflict.status_code == 409
    assert len(conflict.json()["detail"]["duplicates"]) == 1

    confirmed = client.patch(
        f"/candidates/{second.id}",
        json={"email": "FIRST@example.com", "confirm_duplicate": True},
        headers={"X-CSRF-Token": csrf},
    )
    assert confirmed.status_code == 200
    assert confirmed.json()["email"] == "FIRST@example.com"


def test_duplicate_check_never_leaks_foreign_candidates(
    client: TestClient, db_session: Session
) -> None:
    """HRs must not learn about a colleague's candidate via duplicate matches."""
    make_user(db_session, username="hr1", role=UserRole.HR)
    make_user(db_session, username="hr2", role=UserRole.HR)
    make_user(db_session, username="mgr", role=UserRole.MANAGER)

    hr1_csrf = _csrf(_login(client, "hr1"))
    created = client.post(
        "/candidates",
        json=_candidate_payload(phone="+7 900 555-66-77", email=None),
        headers={"X-CSRF-Token": hr1_csrf},
    )
    assert created.status_code == 201

    # HR2 creates the same phone: no 409 (and no PII leak), just a new card.
    hr2_csrf = _csrf(_login(client, "hr2"))
    second = client.post(
        "/candidates",
        json=_candidate_payload(phone="89005556677", email=None, full_name="Другой кандидат"),
        headers={"X-CSRF-Token": hr2_csrf},
    )
    assert second.status_code == 201

    # A manager creating the same phone sees the duplicates and gets 409.
    mgr_csrf = _csrf(_login(client, "mgr"))
    conflict = client.post(
        "/candidates",
        json=_candidate_payload(phone="+7 (900) 555-66-77", email=None, full_name="Третий"),
        headers={"X-CSRF-Token": mgr_csrf},
    )
    assert conflict.status_code == 409
    assert len(conflict.json()["detail"]["duplicates"]) == 2


def test_no_false_duplicates_for_self_update(client: TestClient, db_session: Session) -> None:
    """Updating a candidate without changing phone/email must never 409."""
    hr = make_user(db_session, username="hr1", role=UserRole.HR)
    candidate = make_candidate(
        db_session, owner=hr, phone="+7 900 111-22-33", email="self@example.com"
    )
    login = client.post("/auth/login", json={"username": "hr1", "password": FIXTURE_PASSWORD})
    csrf = _csrf(login)

    updated = client.patch(
        f"/candidates/{candidate.id}",
        json={"position": "Инженер-тестировщик"},
        headers={"X-CSRF-Token": csrf},
    )
    assert updated.status_code == 200


# --- Interactions ------------------------------------------------------------


def test_interactions_are_recorded_and_listed(client: TestClient, db_session: Session) -> None:
    hr = make_user(db_session, username="hr1", role=UserRole.HR)
    candidate = make_candidate(db_session, owner=hr)
    login = client.post("/auth/login", json={"username": "hr1", "password": FIXTURE_PASSWORD})
    csrf = _csrf(login)

    created = client.post(
        f"/candidates/{candidate.id}/interactions",
        json={"type": "call", "comment": "Дозвонились, договорились о встрече"},
        headers={"X-CSRF-Token": csrf},
    )
    assert created.status_code == 201
    assert created.json()["author_username"] == "hr1"
    assert created.json()["type"] == "call"

    listing = client.get(f"/candidates/{candidate.id}/interactions")
    assert listing.status_code == 200
    assert listing.json()["total"] == 1
    assert listing.json()["items"][0]["comment"] == "Дозвонились, договорились о встрече"

    assert db_session.scalar(select(func.count()).select_from(CandidateInteraction)) == 1


def test_interaction_comment_is_required(client: TestClient, db_session: Session) -> None:
    hr = make_user(db_session, username="hr1", role=UserRole.HR)
    candidate = make_candidate(db_session, owner=hr)
    login = client.post("/auth/login", json={"username": "hr1", "password": FIXTURE_PASSWORD})
    csrf = _csrf(login)

    response = client.post(
        f"/candidates/{candidate.id}/interactions",
        json={"type": "note", "comment": "   "},
        headers={"X-CSRF-Token": csrf},
    )
    assert response.status_code == 422


# --- Audit -------------------------------------------------------------------


def test_candidate_lifecycle_is_audited(client: TestClient, db_session: Session) -> None:
    hr = make_user(db_session, username="hr1", role=UserRole.HR)
    login = client.post("/auth/login", json={"username": "hr1", "password": FIXTURE_PASSWORD})
    csrf = _csrf(login)

    created = client.post(
        "/candidates",
        json=_candidate_payload(owner_user_id=str(hr.id)),
        headers={"X-CSRF-Token": csrf},
    )
    candidate_id = created.json()["id"]
    client.patch(
        f"/candidates/{candidate_id}",
        json={"stage": "offer"},
        headers={"X-CSRF-Token": csrf},
    )
    client.delete(f"/candidates/{candidate_id}", headers={"X-CSRF-Token": csrf})
    client.post(f"/candidates/{candidate_id}/restore", headers={"X-CSRF-Token": csrf})

    events = db_session.scalars(
        select(AuditEvent)
        .where(AuditEvent.candidate_id.is_not(None))
        .order_by(AuditEvent.created_at)
    ).all()
    actions = [event.action for event in events]
    assert actions == [
        AuditAction.CANDIDATE_CREATED,
        AuditAction.CANDIDATE_STAGE_CHANGED,
        AuditAction.CANDIDATE_DELETED,
        AuditAction.CANDIDATE_RESTORED,
    ]
    # No personal data leaks into audit details.
    for event in events:
        assert "Петров" not in (event.details or "")
        assert "petrov@" not in (event.details or "")
        assert "900" not in (event.details or "")
