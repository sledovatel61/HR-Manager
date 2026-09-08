"""Tests for Phase 11: document lists, candidate documents and automation rules."""

from __future__ import annotations

import uuid

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.models import (
    DocumentList,
    DocumentListItem,
    DocumentListStatus,
    DocumentListVersion,
    User,
    UserRole,
)
from app.utils import utc_now
from tests.conftest import FIXTURE_PASSWORD, make_candidate, make_user


def _login(client: TestClient, username: str) -> None:
    resp = client.post(
        "/auth/login",
        json={"username": username, "password": FIXTURE_PASSWORD},
    )
    assert resp.status_code == 200, f"Login failed: {resp.text}"


def _csrf(client: TestClient) -> dict:
    return {"X-CSRF-Token": client.cookies.get("hrm_csrf", "")}


def _post_json(client: TestClient, url: str, data: dict) -> object:
    return client.post(url, json=data, headers=_csrf(client))


def _make_dl(db: Session, admin: User) -> tuple[DocumentList, DocumentListVersion]:
    now = utc_now()
    dl = DocumentList(
        stable_key=f"t-{uuid.uuid4().hex[:8]}",
        name="Тестовый список",
        scope="",
        created_by_user_id=admin.id,
        created_at=now,
        updated_at=now,
    )
    db.add(dl)
    db.flush()
    ver = DocumentListVersion(
        list_id=dl.id,
        version_number=1,
        status=DocumentListStatus.DRAFT,
        created_at=now,
        updated_at=now,
    )
    db.add(ver)
    db.flush()
    for idx, (key, name, req) in enumerate(
        [
            ("passport", "Паспорт", True),
            ("inn", "ИНН", True),
            ("photo", "Фото", False),
        ]
    ):
        db.add(
            DocumentListItem(
                version_id=ver.id,
                item_key=key,
                name=name,
                is_required=req,
                sort_order=idx,
                created_at=now,
            )
        )
    db.commit()
    db.refresh(dl)
    return dl, ver


def _publish(db: Session, ver: DocumentListVersion) -> None:
    now = utc_now()
    ver.status = DocumentListStatus.PUBLISHED
    ver.published_at = now
    ver.updated_at = now
    db.commit()
    db.refresh(ver)


# ---- Document list lifecycle ------------------------------------------------


def test_create_list(client: TestClient, db_session: Session) -> None:
    make_user(db_session, username="ac1", role=UserRole.ADMIN)
    _login(client, "ac1")
    resp = _post_json(
        client,
        "/document-lists",
        {
            "stable_key": f"onb-{uuid.uuid4().hex[:6]}",
            "name": "Документы",
            "items": [{"item_key": "p", "name": "Паспорт", "is_required": True}],
        },
    )
    assert resp.status_code == 201, resp.text
    assert resp.json()["name"] == "Документы"


def test_duplicate_key_returns_409(client: TestClient, db_session: Session) -> None:
    make_user(db_session, username="ac2", role=UserRole.ADMIN)
    _login(client, "ac2")
    key = f"dup-{uuid.uuid4().hex[:6]}"
    r1 = _post_json(client, "/document-lists", {"stable_key": key, "name": "Первый"})
    assert r1.status_code == 201, r1.text
    r2 = _post_json(client, "/document-lists", {"stable_key": key, "name": "Второй"})
    assert r2.status_code == 409, r2.text


def test_hr_cannot_create_list(client: TestClient, db_session: Session) -> None:
    make_user(db_session, username="hc1", role=UserRole.HR)
    _login(client, "hc1")
    resp = _post_json(client, "/document-lists", {"stable_key": "nope", "name": "X"})
    assert resp.status_code == 403, resp.text


def test_publish_version(client: TestClient, db_session: Session) -> None:
    admin = make_user(db_session, username="ac3", role=UserRole.ADMIN)
    dl, ver = _make_dl(db_session, admin)
    _login(client, "ac3")
    resp = client.post(
        f"/document-lists/{dl.id}/versions/{ver.id}/publish",
        headers=_csrf(client),
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "published"


def test_cannot_edit_published_version(client: TestClient, db_session: Session) -> None:
    admin = make_user(db_session, username="ac4", role=UserRole.ADMIN)
    dl, ver = _make_dl(db_session, admin)
    _publish(db_session, ver)
    _login(client, "ac4")
    resp = client.patch(
        f"/document-lists/{dl.id}/versions/{ver.id}/items/passport",
        json={"name": "Новое"},
        headers=_csrf(client),
    )
    assert resp.status_code == 409, resp.text


def test_archive_published_version(client: TestClient, db_session: Session) -> None:
    admin = make_user(db_session, username="ac5", role=UserRole.ADMIN)
    dl, ver = _make_dl(db_session, admin)
    _publish(db_session, ver)
    _login(client, "ac5")
    resp = client.post(
        f"/document-lists/{dl.id}/versions/{ver.id}/archive",
        headers=_csrf(client),
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "archived"


def test_publish_archives_previous(client: TestClient, db_session: Session) -> None:
    admin = make_user(db_session, username="ac6", role=UserRole.ADMIN)
    dl, ver1 = _make_dl(db_session, admin)
    _publish(db_session, ver1)
    _login(client, "ac6")
    r = client.post(f"/document-lists/{dl.id}/versions", headers=_csrf(client))
    assert r.status_code == 201, r.text
    new_id = r.json()["id"]
    r2 = client.post(
        f"/document-lists/{dl.id}/versions/{new_id}/publish",
        headers=_csrf(client),
    )
    assert r2.status_code == 200, r2.text
    resp = client.get(f"/document-lists/{dl.id}")
    assert resp.status_code == 200
    published = resp.json().get("latest_published_version")
    assert published is not None
    assert published["id"] == new_id
    assert published["status"] == "published"


def test_list_document_lists(client: TestClient, db_session: Session) -> None:
    admin = make_user(db_session, username="ac7", role=UserRole.ADMIN)
    _make_dl(db_session, admin)
    _login(client, "ac7")
    resp = client.get("/document-lists")
    assert resp.status_code == 200, resp.text
    assert resp.json()["total"] >= 1


# ---- Candidate documents ----------------------------------------------------


def test_apply_list_to_candidate(client: TestClient, db_session: Session) -> None:
    admin = make_user(db_session, username="ac8", role=UserRole.ADMIN)
    dl, ver = _make_dl(db_session, admin)
    _publish(db_session, ver)
    cand = make_candidate(db_session, owner=admin, full_name="Тестов Тест")
    _login(client, "ac8")
    resp = _post_json(
        client,
        f"/document-lists/apply/{cand.id}",
        {"list_id": str(dl.id)},
    )
    assert resp.status_code == 201, resp.text
    assert resp.json()["candidate_id"] == str(cand.id)


def test_get_candidate_documents(client: TestClient, db_session: Session) -> None:
    admin = make_user(db_session, username="ac9", role=UserRole.ADMIN)
    dl, ver = _make_dl(db_session, admin)
    _publish(db_session, ver)
    cand = make_candidate(db_session, owner=admin, full_name="Документов Тест")
    _login(client, "ac9")
    _post_json(
        client,
        f"/document-lists/apply/{cand.id}",
        {"list_id": str(dl.id)},
    )
    resp = client.get(f"/document-lists/candidate/{cand.id}")
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert len(data) >= 1
    assert data[0]["missing_required_count"] == 2


def test_update_item_with_concurrency(client: TestClient, db_session: Session) -> None:
    admin = make_user(db_session, username="acA", role=UserRole.ADMIN)
    dl, ver = _make_dl(db_session, admin)
    _publish(db_session, ver)
    cand = make_candidate(db_session, owner=admin, full_name="Конкурентный Тест")
    _login(client, "acA")
    _post_json(
        client,
        f"/document-lists/apply/{cand.id}",
        {"list_id": str(dl.id)},
    )
    resp = client.get(f"/document-lists/candidate/{cand.id}")
    items = resp.json()[0]["items"]
    pp = next(i for i in items if i["item_key"] == "passport")
    resp = client.patch(
        f"/document-lists/candidate/{cand.id}/items/{pp['id']}",
        json={"expected_version": pp["version"], "status": "received"},
        headers=_csrf(client),
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "received"
    assert resp.json()["version"] == 2


def test_optimistic_conflict_returns_409(client: TestClient, db_session: Session) -> None:
    admin = make_user(db_session, username="acB", role=UserRole.ADMIN)
    dl, ver = _make_dl(db_session, admin)
    _publish(db_session, ver)
    cand = make_candidate(db_session, owner=admin, full_name="Конфликтный Тест")
    _login(client, "acB")
    _post_json(
        client,
        f"/document-lists/apply/{cand.id}",
        {"list_id": str(dl.id)},
    )
    resp = client.get(f"/document-lists/candidate/{cand.id}")
    item = resp.json()[0]["items"][0]
    resp = client.patch(
        f"/document-lists/candidate/{cand.id}/items/{item['id']}",
        json={"expected_version": 999, "status": "received"},
        headers=_csrf(client),
    )
    assert resp.status_code == 409, resp.text


def test_missing_required_documents(client: TestClient, db_session: Session) -> None:
    admin = make_user(db_session, username="acC", role=UserRole.ADMIN)
    dl, ver = _make_dl(db_session, admin)
    _publish(db_session, ver)
    cand = make_candidate(db_session, owner=admin, full_name="Недостающий Тест")
    _login(client, "acC")
    _post_json(
        client,
        f"/document-lists/apply/{cand.id}",
        {"list_id": str(dl.id)},
    )
    resp = client.get(f"/document-lists/candidate/{cand.id}/missing")
    assert resp.status_code == 200, resp.text
    missing = resp.json()
    assert len(missing) == 2
    keys = {m["item_key"] for m in missing}
    assert "passport" in keys and "inn" in keys and "photo" not in keys


def test_hr_cannot_see_other_candidate_docs(client: TestClient, db_session: Session) -> None:
    admin = make_user(db_session, username="acD", role=UserRole.ADMIN)
    make_user(db_session, username="hc2", role=UserRole.HR)
    make_candidate(db_session, owner=admin, full_name="Чужой Тест")
    _login(client, "hc2")
    resp = client.get(f"/document-lists/candidate/{admin.id}")
    # HR can't see admin's candidates - expect 404
    assert resp.status_code == 404, resp.text


def test_soft_deleted_candidate_404(client: TestClient, db_session: Session) -> None:
    admin = make_user(db_session, username="acE", role=UserRole.ADMIN)
    cand = make_candidate(db_session, owner=admin, full_name="Удалённый Тест")
    cand.deleted_at = utc_now()
    db_session.commit()
    _login(client, "acE")
    resp = client.get(f"/document-lists/candidate/{cand.id}")
    assert resp.status_code == 404, resp.text


# ---- Automation rules -------------------------------------------------------


def test_create_automation_rule(client: TestClient, db_session: Session) -> None:
    make_user(db_session, username="ar1", role=UserRole.ADMIN)
    _login(client, "ar1")
    resp = _post_json(
        client,
        "/automation-rules",
        {
            "name": "Автоприменение",
            "trigger": {
                "type": "stage_transition",
                "params": {"stage": "interview_scheduled"},
            },
            "action": {
                "type": "apply_document_list",
                "params": {"list_id": str(uuid.uuid4())},
            },
        },
    )
    assert resp.status_code == 201, resp.text
    assert resp.json()["is_enabled"] is True
    assert resp.json()["version"] == 1


def test_create_rule_with_condition(client: TestClient, db_session: Session) -> None:
    make_user(db_session, username="ar2", role=UserRole.ADMIN)
    _login(client, "ar2")
    resp = _post_json(
        client,
        "/automation-rules",
        {
            "name": "С условием",
            "trigger": {"type": "stage_transition", "params": {"stage": "hired"}},
            "condition": {"type": "has_missing_required", "params": {}},
            "action": {"type": "send_document_request", "params": {}},
        },
    )
    assert resp.status_code == 201, resp.text
    assert resp.json()["condition_type"] == "has_missing_required"


def test_invalid_trigger_type_rejected(client: TestClient, db_session: Session) -> None:
    make_user(db_session, username="ar3", role=UserRole.ADMIN)
    _login(client, "ar3")
    resp = _post_json(
        client,
        "/automation-rules",
        {
            "name": "Bad",
            "trigger": {"type": "run_python", "params": {}},
            "action": {"type": "send_document_request", "params": {}},
        },
    )
    assert resp.status_code == 422, resp.text


def test_invalid_action_type_rejected(client: TestClient, db_session: Session) -> None:
    make_user(db_session, username="ar4", role=UserRole.ADMIN)
    _login(client, "ar4")
    resp = _post_json(
        client,
        "/automation-rules",
        {
            "name": "Bad",
            "trigger": {"type": "stage_transition", "params": {"stage": "new"}},
            "action": {"type": "execute_sql", "params": {"q": "DROP TABLE"}},
        },
    )
    assert resp.status_code == 422, resp.text


def test_toggle_rule(client: TestClient, db_session: Session) -> None:
    make_user(db_session, username="ar5", role=UserRole.ADMIN)
    _login(client, "ar5")
    resp = _post_json(
        client,
        "/automation-rules",
        {
            "name": "Toggle",
            "trigger": {"type": "stage_transition", "params": {"stage": "new"}},
            "action": {"type": "send_document_request", "params": {}},
        },
    )
    rule = resp.json()
    resp = client.patch(
        f"/automation-rules/{rule['id']}/toggle",
        json={"expected_version": rule["version"], "is_enabled": False},
        headers=_csrf(client),
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["is_enabled"] is False
    assert resp.json()["version"] == 2


def test_delete_rule(client: TestClient, db_session: Session) -> None:
    make_user(db_session, username="ar6", role=UserRole.ADMIN)
    _login(client, "ar6")
    resp = _post_json(
        client,
        "/automation-rules",
        {
            "name": "Delete",
            "trigger": {"type": "stage_transition", "params": {"stage": "new"}},
            "action": {"type": "send_document_request", "params": {}},
        },
    )
    rid = resp.json()["id"]
    resp = client.delete(f"/automation-rules/{rid}", headers=_csrf(client))
    assert resp.status_code == 204, resp.text
    resp = client.get(f"/automation-rules/{rid}")
    assert resp.status_code == 404, resp.text


def test_user_only_sees_own_rules(client: TestClient, db_session: Session) -> None:
    make_user(db_session, username="ar7a", role=UserRole.ADMIN)
    make_user(db_session, username="ar7h", role=UserRole.HR)
    _login(client, "ar7a")
    _post_json(
        client,
        "/automation-rules",
        {
            "name": "Admin rule",
            "trigger": {"type": "stage_transition", "params": {"stage": "new"}},
            "action": {"type": "send_document_request", "params": {}},
        },
    )
    client.post("/auth/logout")
    _login(client, "ar7h")
    resp = client.get("/automation-rules")
    assert resp.status_code == 200, resp.text
    assert resp.json()["total"] == 0


def test_update_rule_wrong_version_409(client: TestClient, db_session: Session) -> None:
    make_user(db_session, username="ar8", role=UserRole.ADMIN)
    _login(client, "ar8")
    resp = _post_json(
        client,
        "/automation-rules",
        {
            "name": "Ver",
            "trigger": {"type": "stage_transition", "params": {"stage": "new"}},
            "action": {"type": "send_document_request", "params": {}},
        },
    )
    rule = resp.json()
    resp = client.patch(
        f"/automation-rules/{rule['id']}",
        json={"expected_version": 999, "name": "Stale"},
        headers=_csrf(client),
    )
    assert resp.status_code == 409, resp.text


def test_document_reminder_trigger_requires_params(client: TestClient, db_session: Session) -> None:
    make_user(db_session, username="ar9", role=UserRole.ADMIN)
    _login(client, "ar9")
    resp = _post_json(
        client,
        "/automation-rules",
        {
            "name": "No days",
            "trigger": {
                "type": "document_reminder_schedule",
                "params": {},
            },
            "action": {"type": "send_document_reminder", "params": {"days": 7}},
        },
    )
    assert resp.status_code == 422, resp.text
