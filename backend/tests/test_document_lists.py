"""Unit tests for versioned document lists and candidate snapshots (phase 11).

Coverage:

* admin CRUD of lists/versions: create → draft → publish → new draft →
  publish archives the previous one; immutability of published versions;
  optimistic concurrency (409) on list header, version items and publish;
  one draft at a time; empty draft cannot be published;
* RBAC: non-admin 403 on the admin API; ``/published`` is visible to any
  authenticated user and exposes published versions only;
* candidate snapshot: apply stores an exact copy, a later publication does
  not change it; replace requires confirmation; item status changes under
  optimistic concurrency; foreign HR 404 / manager allowed / admin without
  grant 403 / deleted candidate 404 / item of another candidate 404;
* manual document messages from the applied list: server-owned text with
  the currently missing items only, snapshot on the row, idempotent
  replay, refusal when nothing is missing, pending duplicate 409;
* audit rows carry ids/keys/counts, never document names.
"""

from collections.abc import Iterator
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings
from app.main import create_app
from app.models import (
    AccessGrant,
    AccessGrantScope,
    AuditAction,
    AuditEvent,
    Candidate,
    CandidateChannelConsent,
    CandidateDocumentAssignment,
    CandidateDocumentItem,
    DeliveryStatus,
    DocumentListVersion,
    NotificationOutbox,
    User,
    UserRole,
)
from tests.conftest import FIXTURE_PASSWORD, make_candidate, make_user

NOW = datetime(2026, 9, 4, 12, 0, 0, tzinfo=UTC)

ITEMS = [
    {
        "item_key": "passport",
        "name": "Паспорт",
        "explanation": "Разворот с фото",
        "is_required": True,
    },
    {"item_key": "snils", "name": "СНИЛС", "is_required": True},
    {"item_key": "diploma", "name": "Диплом", "is_required": False},
]


def _login(client: TestClient, username: str) -> str:
    response = client.post("/auth/login", json={"username": username, "password": FIXTURE_PASSWORD})
    assert response.status_code == 200, response.text
    return response.json()["csrf_token"]


@pytest.fixture()
def admin_user(db_session: Session) -> User:
    return make_user(db_session, username="admin1", role=UserRole.ADMIN)


@pytest.fixture()
def hr_user(db_session: Session) -> User:
    return make_user(db_session, username="hr1", role=UserRole.HR)


@pytest.fixture()
def channels_app(unit_engine: Any) -> Iterator[TestClient]:
    settings = Settings.model_validate(
        {
            "APP_ENV": "test",
            "APP_DEBUG": "false",
            "SECRET_KEY": "unit-test-secret-key-0123456789abcdef",
            "SMTP_ENABLED": "true",
            "SMTP_HOST": "smtp.example.test",
            "SMTP_PORT": "2525",
            "SMTP_ENCRYPTION": "starttls",
            "SMTP_USERNAME": "mailer@example.test",
            "SMTP_PASSWORD": "mailer-secret",
            "SMTP_FROM_ADDRESS": "hr@example.test",
            "CANDIDATE_EMAIL_CONFIRM_BASE_URL": "https://hr.example.test",
        }
    )
    app = create_app(settings, engine=unit_engine)
    with TestClient(app) as test_client:
        yield test_client


def _allow_email(db: Session, candidate: Candidate) -> None:
    db.add(
        CandidateChannelConsent(
            candidate_id=candidate.id,
            channel="email",
            granted=True,
            granted_at=NOW,
            source="email_confirm",
            policy_version="phase10-v1",
            granted_by_user_id=None,
            email_normalized=candidate.email_normalized,
        )
    )
    db.commit()


def _create_list(client: TestClient, csrf: str, *, name: str = "Приём на работу") -> dict:
    response = client.post(
        "/document-lists",
        json={"name": name, "description": "Базовый набор", "items": ITEMS},
        headers={"X-CSRF-Token": csrf},
    )
    assert response.status_code == 201, response.text
    return response.json()


def _publish(client: TestClient, csrf: str, list_id: str, version: dict) -> dict:
    response = client.post(
        f"/document-lists/{list_id}/versions/{version['id']}/publish",
        json={"expected_row_version": version["row_version"]},
        headers={"X-CSRF-Token": csrf},
    )
    assert response.status_code == 200, response.text
    return response.json()


def _published_list(client: TestClient, csrf: str, **kwargs: Any) -> tuple[dict, dict]:
    created = _create_list(client, csrf, **kwargs)
    version = _publish(client, csrf, created["id"], created["versions"][0])
    return created, version


# --- Admin list/version lifecycle ------------------------------------------------


def test_list_lifecycle_publish_archives_previous(
    client: TestClient, db_session: Session, admin_user: User
) -> None:
    csrf = _login(client, "admin1")
    created = _create_list(client, csrf)
    assert created["published_version_id"] is None
    draft = created["versions"][0]
    assert draft["status"] == "draft" and draft["version_number"] == 1
    assert [item["item_key"] for item in draft["items"]] == ["passport", "snils", "diploma"]

    published = _publish(client, csrf, created["id"], draft)
    assert published["status"] == "published"
    assert published["published_at"] is not None
    assert published["row_version"] == draft["row_version"] + 1

    # A published version is immutable.
    response = client.put(
        f"/document-lists/{created['id']}/versions/{published['id']}/items",
        json={"expected_row_version": published["row_version"], "items": ITEMS[:1]},
        headers={"X-CSRF-Token": csrf},
    )
    assert response.status_code == 409
    assert "нельзя менять" in response.json()["detail"]

    # New draft = copy of the published items; editing it leaves v1 intact.
    response = client.post(
        f"/document-lists/{created['id']}/versions", headers={"X-CSRF-Token": csrf}
    )
    assert response.status_code == 201, response.text
    draft2 = response.json()
    assert draft2["version_number"] == 2 and draft2["status"] == "draft"
    assert [item["item_key"] for item in draft2["items"]] == ["passport", "snils", "diploma"]
    # Only one draft at a time.
    response = client.post(
        f"/document-lists/{created['id']}/versions", headers={"X-CSRF-Token": csrf}
    )
    assert response.status_code == 409

    response = client.put(
        f"/document-lists/{created['id']}/versions/{draft2['id']}/items",
        json={"expected_row_version": draft2["row_version"], "items": ITEMS[:2]},
        headers={"X-CSRF-Token": csrf},
    )
    assert response.status_code == 200, response.text
    draft2 = response.json()
    assert [item["item_key"] for item in draft2["items"]] == ["passport", "snils"]

    published2 = _publish(client, csrf, created["id"], draft2)
    assert published2["status"] == "published"
    detail = client.get(f"/document-lists/{created['id']}").json()
    statuses = {v["version_number"]: v["status"] for v in detail["versions"]}
    assert statuses == {1: "archived", 2: "published"}
    assert detail["published_version_number"] == 2
    v1 = next(v for v in detail["versions"] if v["version_number"] == 1)
    assert v1["archived_at"] is not None
    assert len(v1["items"]) == 3  # history untouched

    # Manual archive of the published version.
    response = client.post(
        f"/document-lists/{created['id']}/versions/{published2['id']}/archive",
        json={"expected_row_version": published2["row_version"]},
        headers={"X-CSRF-Token": csrf},
    )
    assert response.status_code == 200
    assert response.json()["status"] == "archived"
    assert client.get("/document-lists/published").json()["items"] == []

    actions = {
        row.action
        for row in db_session.execute(select(AuditEvent)).scalars().all()
        if row.action.value.startswith("document_list")
    }
    assert {
        AuditAction.DOCUMENT_LIST_CREATED,
        AuditAction.DOCUMENT_LIST_VERSION_CREATED,
        AuditAction.DOCUMENT_LIST_VERSION_UPDATED,
        AuditAction.DOCUMENT_LIST_VERSION_PUBLISHED,
        AuditAction.DOCUMENT_LIST_VERSION_ARCHIVED,
    } <= actions
    for row in db_session.execute(select(AuditEvent)).scalars().all():
        assert "Паспорт" not in (row.details or "")


def test_list_header_update_and_optimistic_conflicts(
    client: TestClient, db_session: Session, admin_user: User
) -> None:
    csrf = _login(client, "admin1")
    created = _create_list(client, csrf)
    response = client.patch(
        f"/document-lists/{created['id']}",
        json={"expected_version": 1, "name": "Приём: офис", "scope_stage": "offer"},
        headers={"X-CSRF-Token": csrf},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["name"] == "Приём: офис" and body["scope_stage"] == "offer" and body["version"] == 2
    # Stale editor.
    response = client.patch(
        f"/document-lists/{created['id']}",
        json={"expected_version": 1, "name": "Другое"},
        headers={"X-CSRF-Token": csrf},
    )
    assert response.status_code == 409
    assert "ожидалась версия 1" in response.json()["detail"]
    # Clear scope.
    response = client.patch(
        f"/document-lists/{created['id']}",
        json={"expected_version": 2, "clear_scope_stage": True},
        headers={"X-CSRF-Token": csrf},
    )
    assert response.status_code == 200
    assert response.json()["scope_stage"] is None
    # Stale publish / stale items.
    draft = created["versions"][0]
    response = client.put(
        f"/document-lists/{created['id']}/versions/{draft['id']}/items",
        json={"expected_row_version": 99, "items": ITEMS},
        headers={"X-CSRF-Token": csrf},
    )
    assert response.status_code == 409
    response = client.post(
        f"/document-lists/{created['id']}/versions/{draft['id']}/publish",
        json={"expected_row_version": 99},
        headers={"X-CSRF-Token": csrf},
    )
    assert response.status_code == 409
    # Empty draft cannot be published.
    response = client.put(
        f"/document-lists/{created['id']}/versions/{draft['id']}/items",
        json={"expected_row_version": draft["row_version"], "items": []},
        headers={"X-CSRF-Token": csrf},
    )
    assert response.status_code == 200
    response = client.post(
        f"/document-lists/{created['id']}/versions/{draft['id']}/publish",
        json={"expected_row_version": draft["row_version"] + 1},
        headers={"X-CSRF-Token": csrf},
    )
    assert response.status_code == 409
    assert "пустой" in response.json()["detail"]
    # Unknown ids are 404, not 500.
    assert client.get(f"/document-lists/{uuid4()}").status_code == 404
    assert client.get("/document-lists/not-a-uuid").status_code == 404


def test_item_validation(client: TestClient, db_session: Session, admin_user: User) -> None:
    csrf = _login(client, "admin1")
    bad_cases = [
        {"name": "x", "items": [{"item_key": "Passport", "name": "П"}]},
        {"name": "x", "items": [{"item_key": "a", "name": "A"}, {"item_key": "a", "name": "B"}]},
        {"name": "x", "items": [{"item_key": "a", "name": ""}]},
        {"name": "x", "items": [{"item_key": "a", "name": "A\x00"}]},
        {"name": "", "items": [{"item_key": "a", "name": "A"}]},
        {
            "name": "x",
            "items": [{"item_key": f"k{i}", "name": f"D{i}"} for i in range(21)],
        },
    ]
    for payload in bad_cases:
        response = client.post("/document-lists", json=payload, headers={"X-CSRF-Token": csrf})
        assert response.status_code == 422, payload
    # An empty draft may exist (items are added later) but never publishes.
    response = client.post(
        "/document-lists", json={"name": "Пустой", "items": []}, headers={"X-CSRF-Token": csrf}
    )
    assert response.status_code == 201


def test_admin_api_rbac(client: TestClient, db_session: Session, hr_user: User) -> None:
    make_user(db_session, username="mgr1", role=UserRole.MANAGER)
    assert client.get("/document-lists").status_code == 401
    csrf = _login(client, "hr1")
    assert client.get("/document-lists").status_code == 403
    assert (
        client.post(
            "/document-lists",
            json={"name": "x", "items": ITEMS},
            headers={"X-CSRF-Token": csrf},
        ).status_code
        == 403
    )
    # CSRF is enforced even for admins.
    make_user(db_session, username="admin2", role=UserRole.ADMIN)
    _login(client, "admin2")
    assert client.post("/document-lists", json={"name": "x", "items": ITEMS}).status_code == 403


def test_published_endpoint_shows_published_only(
    client: TestClient, db_session: Session, admin_user: User, hr_user: User
) -> None:
    csrf = _login(client, "admin1")
    created, _ = _published_list(client, csrf)
    _create_list(client, csrf, name="Черновик")  # never published
    client.post("/auth/logout", headers={"X-CSRF-Token": csrf})
    _login(client, "hr1")
    response = client.get("/document-lists/published")
    assert response.status_code == 200
    items = response.json()["items"]
    assert [entry["name"] for entry in items] == ["Приём на работу"]
    assert items[0]["id"] == created["id"] and items[0]["published_version_number"] == 1
    assert [item["item_key"] for item in items[0]["items"]] == ["passport", "snils", "diploma"]


# --- Candidate snapshot -----------------------------------------------------------


def _apply(client: TestClient, csrf: str, candidate: Candidate, list_id: str, **extra: Any) -> Any:
    return client.post(
        f"/candidates/{candidate.id}/documents/apply",
        json={"list_id": list_id, **extra},
        headers={"X-CSRF-Token": csrf},
    )


def test_apply_snapshot_is_immune_to_later_publications(
    client: TestClient, db_session: Session, admin_user: User, hr_user: User
) -> None:
    admin_csrf = _login(client, "admin1")
    created, _ = _published_list(client, admin_csrf)
    client.post("/auth/logout", headers={"X-CSRF-Token": admin_csrf})

    candidate = make_candidate(db_session, owner=hr_user)
    csrf = _login(client, "hr1")
    assert client.get(f"/candidates/{candidate.id}/documents").json() == {
        "current": None,
        "history": [],
    }
    response = _apply(client, csrf, candidate, created["id"])
    assert response.status_code == 201, response.text
    current = response.json()["current"]
    assert current["list_name"] == "Приём на работу" and current["version_number"] == 1
    assert current["assigned_by_username"] == "hr1"
    assert [item["item_key"] for item in current["items"]] == ["passport", "snils", "diploma"]
    assert all(item["status"] == "missing" and item["version"] == 1 for item in current["items"])
    assert current["missing_required_count"] == 2 and current["received_count"] == 0
    # The same version again is a no-op conflict.
    assert _apply(client, csrf, candidate, created["id"]).status_code == 409

    # Admin publishes v2 with fewer items: the candidate snapshot is unchanged.
    client.post("/auth/logout", headers={"X-CSRF-Token": csrf})
    admin_csrf = _login(client, "admin1")
    draft2 = client.post(
        f"/document-lists/{created['id']}/versions", headers={"X-CSRF-Token": admin_csrf}
    ).json()
    draft2 = client.put(
        f"/document-lists/{created['id']}/versions/{draft2['id']}/items",
        json={"expected_row_version": draft2["row_version"], "items": ITEMS[:1]},
        headers={"X-CSRF-Token": admin_csrf},
    ).json()
    _publish(client, admin_csrf, created["id"], draft2)
    client.post("/auth/logout", headers={"X-CSRF-Token": admin_csrf})
    csrf = _login(client, "hr1")
    current = client.get(f"/candidates/{candidate.id}/documents").json()["current"]
    assert current["version_number"] == 1 and len(current["items"]) == 3

    # Replacing requires confirmation; the previous assignment becomes history.
    response = _apply(client, csrf, candidate, created["id"])
    assert response.status_code == 409
    assert "Подтвердите замену" in response.json()["detail"]
    response = _apply(client, csrf, candidate, created["id"], replace=True)
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["current"]["version_number"] == 2 and len(body["current"]["items"]) == 1
    assert len(body["history"]) == 1 and body["history"][0]["version_number"] == 1
    assignments = db_session.execute(select(CandidateDocumentAssignment)).scalars().all()
    assert len(assignments) == 2
    assert sum(1 for a in assignments if a.replaced_at is None) == 1
    # Published-version deletion is impossible while snapshots reference it.
    version = db_session.get(DocumentListVersion, __import__("uuid").UUID(current["version_id"]))
    assert version is not None and version.status.value == "archived"


def test_apply_refuses_unpublished_list_and_missing_endpoint(
    client: TestClient, db_session: Session, admin_user: User, hr_user: User
) -> None:
    admin_csrf = _login(client, "admin1")
    unpublished = _create_list(client, admin_csrf, name="Черновик")
    client.post("/auth/logout", headers={"X-CSRF-Token": admin_csrf})
    candidate = make_candidate(db_session, owner=hr_user)
    csrf = _login(client, "hr1")
    assert _apply(client, csrf, candidate, unpublished["id"]).status_code == 404
    assert _apply(client, csrf, candidate, str(uuid4())).status_code == 404
    response = client.get(f"/candidates/{candidate.id}/documents/missing")
    assert response.status_code == 200
    assert response.json()["items"] == [] and response.json()["list_id"] is None


def test_item_status_optimistic_concurrency(
    client: TestClient, db_session: Session, admin_user: User, hr_user: User
) -> None:
    admin_csrf = _login(client, "admin1")
    created, _ = _published_list(client, admin_csrf)
    client.post("/auth/logout", headers={"X-CSRF-Token": admin_csrf})
    candidate = make_candidate(db_session, owner=hr_user)
    csrf = _login(client, "hr1")
    current = _apply(client, csrf, candidate, created["id"]).json()["current"]
    passport = next(item for item in current["items"] if item["item_key"] == "passport")

    response = client.patch(
        f"/candidates/{candidate.id}/documents/items/{passport['id']}",
        json={"status": "received", "expected_version": 1},
        headers={"X-CSRF-Token": csrf},
    )
    assert response.status_code == 200, response.text
    updated = next(
        item for item in response.json()["current"]["items"] if item["item_key"] == "passport"
    )
    assert updated["status"] == "received" and updated["version"] == 2
    assert updated["changed_by_username"] == "hr1" and updated["changed_at"] is not None
    assert response.json()["current"]["missing_required_count"] == 1
    assert response.json()["current"]["received_count"] == 1

    # Stale editor: 409 with the actual version, nothing overwritten.
    response = client.patch(
        f"/candidates/{candidate.id}/documents/items/{passport['id']}",
        json={"status": "missing", "expected_version": 1},
        headers={"X-CSRF-Token": csrf},
    )
    assert response.status_code == 409
    assert "актуальная — 2" in response.json()["detail"]
    item_row = db_session.get(CandidateDocumentItem, __import__("uuid").UUID(passport["id"]))
    assert item_row is not None
    db_session.refresh(item_row)
    assert item_row.status.value == "received" and item_row.version == 2

    # Same status again is a no-op (200, version unchanged).
    response = client.patch(
        f"/candidates/{candidate.id}/documents/items/{passport['id']}",
        json={"status": "received", "expected_version": 2},
        headers={"X-CSRF-Token": csrf},
    )
    assert response.status_code == 200
    assert (
        next(
            item for item in response.json()["current"]["items"] if item["item_key"] == "passport"
        )["version"]
        == 2
    )

    # Missing endpoint shows the remaining items in list order.
    missing = client.get(f"/candidates/{candidate.id}/documents/missing").json()
    assert [item["item_key"] for item in missing["items"]] == ["snils", "diploma"]
    missing = client.get(
        f"/candidates/{candidate.id}/documents/missing", params={"required_only": "true"}
    ).json()
    assert [item["item_key"] for item in missing["items"]] == ["snils"]

    # Audit: keys/ids only.
    rows = [
        row
        for row in db_session.execute(select(AuditEvent)).scalars().all()
        if row.action == AuditAction.CANDIDATE_DOCUMENT_RECEIVED
    ]
    assert len(rows) == 1 and "item_key=passport" in (rows[0].details or "")
    assert "Паспорт" not in (rows[0].details or "")

    # Unknown / malformed item ids.
    assert (
        client.patch(
            f"/candidates/{candidate.id}/documents/items/{uuid4()}",
            json={"status": "received", "expected_version": 1},
            headers={"X-CSRF-Token": csrf},
        ).status_code
        == 404
    )
    assert (
        client.patch(
            f"/candidates/{candidate.id}/documents/items/nope",
            json={"status": "received", "expected_version": 1},
            headers={"X-CSRF-Token": csrf},
        ).status_code
        == 404
    )


def test_candidate_documents_access_matrix(
    client: TestClient, db_session: Session, admin_user: User, hr_user: User
) -> None:
    other_hr = make_user(db_session, username="hr2", role=UserRole.HR)
    make_user(db_session, username="mgr1", role=UserRole.MANAGER)
    admin_csrf = _login(client, "admin1")
    created, _ = _published_list(client, admin_csrf)
    # Admin without a pilot grant: 403 on the candidate document scope.
    candidate = make_candidate(db_session, owner=hr_user)
    foreign = make_candidate(db_session, owner=other_hr, full_name="Чужой Кандидат")
    deleted = make_candidate(db_session, owner=hr_user, full_name="Удалённый", deleted=True)
    assert client.get(f"/candidates/{candidate.id}/documents").status_code == 403
    assert _apply(client, admin_csrf, candidate, created["id"]).status_code == 403
    # With an active grant the admin (pilot) keeps the powers.
    db_session.add(
        AccessGrant(
            user_id=admin_user.id,
            scope=AccessGrantScope.PILOT_FULL_ACCESS,
            granted_by_user_id=admin_user.id,
        )
    )
    db_session.commit()
    assert client.get(f"/candidates/{candidate.id}/documents").status_code == 200
    client.post("/auth/logout", headers={"X-CSRF-Token": admin_csrf})

    csrf = _login(client, "hr1")
    assert client.get(f"/candidates/{candidate.id}/documents").status_code == 200
    assert client.get(f"/candidates/{foreign.id}/documents").status_code == 404
    assert _apply(client, csrf, foreign, created["id"]).status_code == 404
    assert client.get(f"/candidates/{deleted.id}/documents").status_code == 404
    assert client.get(f"/candidates/{uuid4()}/documents").status_code == 404
    # An item of a foreign candidate cannot be flipped through my card (IDOR).
    current = _apply(client, csrf, candidate, created["id"]).json()["current"]
    client.post("/auth/logout", headers={"X-CSRF-Token": csrf})
    csrf2 = _login(client, "hr2")
    response = client.patch(
        f"/candidates/{foreign.id}/documents/items/{current['items'][0]['id']}",
        json={"status": "received", "expected_version": 1},
        headers={"X-CSRF-Token": csrf2},
    )
    assert response.status_code == 404
    client.post("/auth/logout", headers={"X-CSRF-Token": csrf2})
    # Manager sees everything.
    _login(client, "mgr1")
    assert client.get(f"/candidates/{candidate.id}/documents").status_code == 200
    assert client.get(f"/candidates/{foreign.id}/documents").status_code == 200
    # Unauthenticated.
    client.post("/auth/logout", headers={"X-CSRF-Token": _login(client, "mgr1")})
    assert client.get(f"/candidates/{candidate.id}/documents").status_code == 401


# --- Manual document messages from the applied list --------------------------------


def test_document_message_from_applied_list(
    channels_app: TestClient, db_session: Session, admin_user: User, hr_user: User
) -> None:
    admin_csrf = _login(channels_app, "admin1")
    created, _ = _published_list(channels_app, admin_csrf)
    channels_app.post("/auth/logout", headers={"X-CSRF-Token": admin_csrf})
    candidate = make_candidate(db_session, owner=hr_user, email="doc@example.com")
    _allow_email(db_session, candidate)
    csrf = _login(channels_app, "hr1")
    url = f"/candidates/{candidate.id}/documents/messages"
    # No list applied yet.
    response = channels_app.post(
        url,
        json={"message_type": "document_request", "idempotency_key": "key-0001"},
        headers={"X-CSRF-Token": csrf},
    )
    assert response.status_code == 409
    current = _apply(channels_app, csrf, candidate, created["id"]).json()["current"]
    passport = next(item for item in current["items"] if item["item_key"] == "passport")
    channels_app.patch(
        f"/candidates/{candidate.id}/documents/items/{passport['id']}",
        json={"status": "received", "expected_version": 1},
        headers={"X-CSRF-Token": csrf},
    )
    response = channels_app.post(
        url,
        json={"message_type": "document_request", "idempotency_key": "key-0001"},
        headers={"X-CSRF-Token": csrf},
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["channels"] == ["email"]
    message = body["messages"][0]
    assert message["title"] == "Запрос документов"
    # Only the currently missing items, in list order; no received one.
    assert "— СНИЛС" in message["body"] and "— Диплом" in message["body"]
    assert "Паспорт" not in message["body"]
    assert message["body"].index("СНИЛС") < message["body"].index("Диплом")
    row = db_session.get(NotificationOutbox, __import__("uuid").UUID(message["id"]))
    assert row is not None
    assert row.object_type == "document_assignment" and str(row.object_id) == current["id"]
    assert row.object_snapshot == {
        "assignment_id": current["id"],
        "list_id": created["id"],
        "version_id": current["version_id"],
        "version_number": 1,
        "item_keys": ["snils", "diploma"],
    }
    assert row.source.value == "manual" and row.rule_id is None

    # Idempotent replay: same response, nothing new queued.
    replay = channels_app.post(
        url,
        json={"message_type": "document_request", "idempotency_key": "key-0001"},
        headers={"X-CSRF-Token": csrf},
    )
    assert replay.status_code == 201 and replay.json() == body
    # Same key, different payload → 409.
    response = channels_app.post(
        url,
        json={"message_type": "document_reminder", "idempotency_key": "key-0001"},
        headers={"X-CSRF-Token": csrf},
    )
    assert response.status_code == 409
    # Pending duplicate of the same type → 409.
    response = channels_app.post(
        url,
        json={"message_type": "document_request", "idempotency_key": "key-0002"},
        headers={"X-CSRF-Token": csrf},
    )
    assert response.status_code == 409
    assert "уже ожидает" in response.json()["detail"]
    assert (
        len(
            db_session.execute(
                select(NotificationOutbox).where(
                    NotificationOutbox.recipient_candidate_id == candidate.id
                )
            )
            .scalars()
            .all()
        )
        == 1
    )
    # Body/recipient/documents from the client are refused by the schema.
    response = channels_app.post(
        url,
        json={
            "message_type": "document_request",
            "idempotency_key": "key-0003",
            "documents": ["Что-то"],
        },
        headers={"X-CSRF-Token": csrf},
    )
    assert response.status_code == 422
    # Audit without names.
    rows = [
        r
        for r in db_session.execute(select(AuditEvent)).scalars().all()
        if r.action == AuditAction.CANDIDATE_DOCUMENT_MESSAGE_QUEUED
    ]
    assert len(rows) == 1
    assert "items=2" in (rows[0].details or "") and "СНИЛС" not in (rows[0].details or "")
    # The row shows up in the phase-10 message history of the candidate.
    history = channels_app.get(f"/candidates/{candidate.id}/messages").json()
    assert [m["id"] for m in history["items"]] == [message["id"]]

    # Everything received → nothing to send (cancel the pending one first).
    row.status = DeliveryStatus.CANCELLED
    db_session.commit()
    for key in ("snils", "diploma"):
        item = next(i for i in current["items"] if i["item_key"] == key)
        channels_app.patch(
            f"/candidates/{candidate.id}/documents/items/{item['id']}",
            json={"status": "received", "expected_version": 1},
            headers={"X-CSRF-Token": csrf},
        )
    response = channels_app.post(
        url,
        json={"message_type": "document_reminder", "idempotency_key": "key-0004"},
        headers={"X-CSRF-Token": csrf},
    )
    assert response.status_code == 409
    assert "уже получены" in response.json()["detail"]


def test_document_message_requires_allowed_channel(
    channels_app: TestClient, db_session: Session, admin_user: User, hr_user: User
) -> None:
    admin_csrf = _login(channels_app, "admin1")
    created, _ = _published_list(channels_app, admin_csrf)
    channels_app.post("/auth/logout", headers={"X-CSRF-Token": admin_csrf})
    candidate = make_candidate(db_session, owner=hr_user, email="noconsent@example.com")
    csrf = _login(channels_app, "hr1")
    _apply(channels_app, csrf, candidate, created["id"])
    response = channels_app.post(
        f"/candidates/{candidate.id}/documents/messages",
        json={"message_type": "document_request", "idempotency_key": "key-0009"},
        headers={"X-CSRF-Token": csrf},
    )
    assert response.status_code == 409
    assert "Нет разрешённых каналов" in response.json()["detail"]
    response = channels_app.post(
        f"/candidates/{candidate.id}/documents/messages",
        json={
            "message_type": "document_request",
            "channel": "telegram",
            "idempotency_key": "key-0010",
        },
        headers={"X-CSRF-Token": csrf},
    )
    assert response.status_code == 409
