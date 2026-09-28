"""Phase 16 API contract (SQLite units): templates, versions, generation.

Covers the accepted MVP boundaries: textual templates only (no files, no
PDF/DOCX, no candidate delivery), 404 for foreign candidates, 403 without the
`document_lists_manage` grant, immutable version content, immutable generated
snapshots, optimistic 409 and audit rows without document text or personal data.
"""

import logging
from typing import Any
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import document_templates as ops
from app.document_templates import scope_applies
from app.models import (
    AccessGrant,
    AccessGrantScope,
    AuditEvent,
    CandidateDocumentGeneration,
    CandidateSource,
    CandidateStage,
    DocumentTemplate,
    DocumentTemplateVersion,
    User,
    UserRole,
)
from tests.conftest import make_candidate, make_user
from tests.test_candidate_messages import _login

CONTENT: dict[str, Any] = {
    "kind": "offer",
    "name": "Оффер (базовый)",
    "title": "Оффер для кандидата",
    "body": (
        "Здравствуйте, {{candidate.full_name}}!\n\n"
        "Предлагаем позицию: {{candidate.position}}\n"
        "Дата: {{system.date}}\n\n"
        "- Этап: {{candidate.stage}}\n"
        "- Ответственный HR: {{hr.full_name}}"
    ),
}


def _auth(client: TestClient, username: str) -> dict[str, str]:
    """Log in and return CSRF headers (the cookie follows the last login)."""
    return {"X-CSRF-Token": _login(client, username)}


def _manage_grant(db: Session, user: User) -> None:
    db.add(
        AccessGrant(
            user_id=user.id,
            scope=AccessGrantScope.DOCUMENT_LISTS_MANAGE,
            granted_at=ops.utc_now(),
        )
    )
    db.commit()


def _create_template(client: TestClient, headers: dict[str, str], **overrides: Any) -> dict:
    payload = {**CONTENT, **overrides}
    response = client.post("/document-templates", json=payload, headers=headers)
    assert response.status_code == 201, response.text
    return response.json()


def _activate(client: TestClient, headers: dict[str, str], template: dict, version_id: str) -> dict:
    response = client.post(
        f"/document-templates/{template['id']}/versions/{version_id}/activate",
        json={"expected_revision": template["revision"]},
        headers=headers,
    )
    assert response.status_code == 200, response.text
    return response.json()


def _published_template(client: TestClient, db: Session) -> tuple[User, dict, dict[str, str]]:
    admin = make_user(db, username="tpl-admin", role=UserRole.ADMIN)
    headers = {"X-CSRF-Token": _login(client, admin.username)}
    template = _create_template(client, headers)
    version = template["versions"][0]
    template = _activate(client, headers, template, version["id"])
    return admin, template, headers


# --- Dictionaries -------------------------------------------------------------


def test_russian_labels_cover_every_enum_member() -> None:
    """A new stage/source may not silently render as a raw key."""
    assert set(ops.STAGE_LABELS) == set(CandidateStage)
    assert set(ops.SOURCE_LABELS) == set(CandidateSource)
    assert all(label.strip() for label in ops.STAGE_LABELS.values())
    assert all(label.strip() for label in ops.SOURCE_LABELS.values())


# --- Lifecycle ----------------------------------------------------------------


def test_template_lifecycle_and_version_immutability(
    client: TestClient, db_session: Session
) -> None:
    admin = make_user(db_session, username="life-admin", role=UserRole.ADMIN)
    headers = {"X-CSRF-Token": _login(client, admin.username)}

    template = _create_template(client, headers)
    first = template["versions"][0]
    assert first["state"] == "draft"
    assert first["placeholders"] == [
        "candidate.full_name",
        "candidate.position",
        "candidate.stage",
        "hr.full_name",
        "system.date",
    ]
    # Publishing the first version makes it active; a draft sibling stays draft.
    template = _activate(client, headers, template, first["id"])
    assert {v["number"]: v["state"] for v in template["versions"]} == {1: "active"}

    # A new version number follows, previous content is untouched.
    created = client.post(
        f"/document-templates/{template['id']}/versions",
        json={
            "title": "Оффер v2",
            "body": "Привет, {{candidate.full_name}}",
            "expected_revision": template["revision"],
        },
        headers=headers,
    )
    assert created.status_code == 201, created.text
    template = created.json()
    second = next(v for v in template["versions"] if v["number"] == 2)
    assert {v["number"] for v in template["versions"]} == {1, 2}

    # Optimistic concurrency: a stale counter is a 409 without data loss.
    stale = client.post(
        f"/document-templates/{template['id']}/versions",
        json={"title": "Черновик", "body": "текст", "expected_revision": 1},
        headers=headers,
    )
    assert stale.status_code == 409

    # Activating the second version archives the first one.
    template = _activate(client, headers, template, second["id"])
    states = {v["number"]: v["state"] for v in template["versions"]}
    assert states == {1: "archived", 2: "active"}
    assert next(v for v in template["versions"] if v["number"] == 2)["activated_at"]

    # Archived versions cannot be activated again; double archive is a 409.
    archived = client.post(
        f"/document-templates/{template['id']}/versions/{second['id']}/activate",
        json={"expected_revision": template["revision"]},
        headers=headers,
    )
    assert archived.status_code == 409

    # Archiving the active version is allowed: the template simply has no
    # published version afterwards and disappears from non-manager lists.
    template = client.post(
        f"/document-templates/{template['id']}/versions/{second['id']}/archive",
        json={"expected_revision": template["revision"]},
        headers=headers,
    ).json()
    assert {v["number"]: v["state"] for v in template["versions"]} == {1: "archived", 2: "archived"}
    hr = make_user(db_session, username="life-hr")
    hr_headers = {"X-CSRF-Token": _login(client, hr.username)}
    assert client.get("/document-templates").json()["items"] == []
    assert client.get("/document-templates", headers=hr_headers).status_code == 200


def test_rename_is_audited_and_does_not_touch_version_content(
    client: TestClient, db_session: Session
) -> None:
    admin, template, headers = _published_template(client, db_session)
    version_before = template["versions"][0]
    renamed = client.patch(
        f"/document-templates/{template['id']}",
        json={"name": "Оффер (2026)", "expected_revision": template["revision"]},
        headers=headers,
    )
    assert renamed.status_code == 200, renamed.text
    body = renamed.json()
    assert body["name"] == "Оффер (2026)"
    assert body["revision"] == template["revision"] + 1
    version_after = body["versions"][0]
    assert version_after["body"] == version_before["body"]
    assert version_after["title"] == version_before["title"]
    assert version_after["number"] == version_before["number"]
    assert version_after["state"] == "active"

    # Same-name and stale-counter renames are conflicts, not silent no-ops.
    again = client.patch(
        f"/document-templates/{template['id']}",
        json={"name": "Оффер (2026)", "expected_revision": body["revision"]},
        headers=headers,
    )
    assert again.status_code == 409
    assert (
        client.patch(
            f"/document-templates/{template['id']}",
            json={"name": "Другое", "expected_revision": 1},
            headers=headers,
        ).status_code
        == 409
    )

    events = db_session.scalars(
        select(AuditEvent).where(AuditEvent.action == "document_template_renamed")
    ).all()
    assert len(events) == 1
    assert "Оффер (2026)" not in (events[0].details or "")
    assert admin.id == events[0].actor_user_id


def test_version_limit_is_enforced(
    client: TestClient, db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(ops, "MAX_VERSIONS_PER_TEMPLATE", 2)
    admin = make_user(db_session, username="limit-admin", role=UserRole.ADMIN)
    headers = {"X-CSRF-Token": _login(client, admin.username)}
    template = _create_template(client, headers)
    second = client.post(
        f"/document-templates/{template['id']}/versions",
        json={"title": "Вторая", "body": "текст", "expected_revision": template["revision"]},
        headers=headers,
    )
    assert second.status_code == 201
    blocked = client.post(
        f"/document-templates/{template['id']}/versions",
        json={
            "title": "Третья",
            "body": "текст",
            "expected_revision": second.json()["revision"],
        },
        headers=headers,
    )
    assert blocked.status_code == 409
    assert "предел" in blocked.json()["detail"]


# --- Access control -----------------------------------------------------------


def test_manage_requires_admin_or_grant(client: TestClient, db_session: Session) -> None:
    hr = make_user(db_session, username="acl-hr")
    manager = make_user(db_session, username="acl-manager", role=UserRole.MANAGER)
    admin = make_user(db_session, username="acl-admin", role=UserRole.ADMIN)

    for user in (hr, manager):
        headers = {"X-CSRF-Token": _login(client, user.username)}
        denied = client.post("/document-templates", json=CONTENT, headers=headers)
        assert denied.status_code == 403, user.username
        detail = denied.json()["detail"]
        assert "document_lists_manage" in detail or "права" in detail

    headers = {"X-CSRF-Token": _login(client, admin.username)}
    template = _create_template(client, headers)
    version = template["versions"][0]
    template = _activate(client, headers, template, version["id"])

    # The granted HR manages content but still only sees what the policy allows.
    granted = make_user(db_session, username="acl-granted")
    _manage_grant(db_session, granted)
    granted_headers = {"X-CSRF-Token": _login(client, granted.username)}
    anketa = client.post(
        "/document-templates",
        json={**CONTENT, "kind": "anketa", "name": "Анкета"},
        headers=granted_headers,
    )
    assert anketa.status_code == 201
    renamed = client.patch(
        f"/document-templates/{template['id']}",
        json={"name": "Оффер (проверено)", "expected_revision": template["revision"]},
        headers=granted_headers,
    )
    assert renamed.status_code == 200

    # A plain HR without the grant cannot even rename, but reads published text.
    hr_headers = {"X-CSRF-Token": _login(client, hr.username)}
    assert (
        client.patch(
            f"/document-templates/{template['id']}",
            json={"name": "Взлом", "expected_revision": renamed.json()["revision"]},
            headers=hr_headers,
        ).status_code
        == 403
    )
    listed = client.get("/document-templates", headers=hr_headers)
    assert listed.status_code == 200
    assert listed.json()["can_manage"] is False
    visible = [item for item in listed.json()["items"] if item["id"] == template["id"]]
    assert len(visible) == 1
    assert {v["state"] for v in visible[0]["versions"]} == {"active"}
    # The draft of the granted HR is invisible to a plain HR.
    assert all(
        version["state"] == "active"
        for item in listed.json()["items"]
        for version in item["versions"]
    )


def test_mutations_require_csrf(client: TestClient, db_session: Session) -> None:
    admin = make_user(db_session, username="csrf-admin", role=UserRole.ADMIN)
    _login(client, admin.username)
    assert client.post("/document-templates", json=CONTENT).status_code == 403


# --- Validation ---------------------------------------------------------------


def test_content_validation_rejects_unknown_placeholders_and_html_braces(
    client: TestClient, db_session: Session
) -> None:
    admin = make_user(db_session, username="val-admin", role=UserRole.ADMIN)
    headers = {"X-CSRF-Token": _login(client, admin.username)}
    cases = [
        {"body": "{{candidate.salary}}"},
        {"body": "{{user.password_hash}}"},
        {"body": "Оплата 50% (нет данных)"},
        {"kind": "Offer"},
        {"kind": "оффер"},
        {"title": "   "},
        {"body": "а" * 20001},
    ]
    for overrides in cases:
        # Percent signs are legal text: only that case must be accepted.
        response = client.post(
            "/document-templates", json={**CONTENT, **overrides}, headers=headers
        )
        if overrides == {"body": "Оплата 50% (нет данных)"}:
            assert response.status_code == 201, overrides
            continue
        assert response.status_code == 422, (overrides, response.text)

    bad_scope = client.post(
        "/document-templates", json={**CONTENT, "scope": "unknown_stage"}, headers=headers
    )
    assert bad_scope.status_code == 422


def test_placeholders_catalog_requires_authentication(
    client: TestClient, db_session: Session
) -> None:
    assert client.get("/document-templates/placeholders").status_code == 401
    hr = make_user(db_session, username="cat-hr")
    _login(client, hr.username)
    body = client.get("/document-templates/placeholders").json()
    tokens = [item["token"] for item in body["items"]]
    assert "candidate.full_name" in tokens
    assert "system.date" in tokens
    assert not any(token.startswith("event.") for token in tokens)


# --- Preview ------------------------------------------------------------------


def test_preview_requires_published_version_access_and_persists_nothing(
    client: TestClient, db_session: Session
) -> None:
    admin, template, _headers = _published_template(client, db_session)
    hr = make_user(db_session, username="prev-hr")
    candidate = make_candidate(
        db_session,
        owner=hr,
        full_name="Смирнова Анна",
        position="Аналитик",
        email="anna@example.com",
        stage=CandidateStage.OFFER,
    )
    hr_headers = {"X-CSRF-Token": _login(client, hr.username)}
    active = template["versions"][0]
    preview = client.post(
        f"/candidates/{candidate.id}/generated-documents/preview",
        json={"template_version_id": active["id"]},
        headers=hr_headers,
    )
    assert preview.status_code == 200, preview.text
    body = preview.json()
    assert "Смирнова Анна" in body["body_text"]
    assert "Аналитик" in body["body_text"]
    assert "Оффер" in body["body_text"]  # stage label, not the raw enum value
    assert body["template_number"] == active["number"]
    assert body["kind"] == "offer"
    assert db_session.scalars(select(CandidateDocumentGeneration)).all() == []

    # A draft version cannot be rendered; a foreign candidate is a 404.
    manager = make_user(db_session, username="prev-manager", role=UserRole.MANAGER)
    assert (
        client.post(
            f"/candidates/{candidate.id}/generated-documents/preview",
            json={"template_version_id": active["id"]},
            headers=_auth(client, manager.username),
        ).status_code
        == 404
    )
    new_draft = client.post(
        f"/document-templates/{template['id']}/versions",
        json={
            "title": "Черновик",
            "body": "текст {{system.date}}",
            "expected_revision": template["revision"],
        },
        headers=_auth(client, admin.username),
    )
    assert new_draft.status_code == 201, new_draft.text
    draft_id = next(v["id"] for v in new_draft.json()["versions"] if v["state"] == "draft")
    assert (
        client.post(
            f"/candidates/{candidate.id}/generated-documents/preview",
            json={"template_version_id": draft_id},
            headers=_auth(client, hr.username),
        ).status_code
        == 422
    )
    assert (
        client.post(
            f"/candidates/{candidate.id}/generated-documents/preview",
            json={"template_version_id": str(uuid4())},
            headers=_auth(client, hr.username),
        ).status_code
        == 404
    )


def test_deleted_candidate_is_not_found(client: TestClient, db_session: Session) -> None:
    _admin, template, _headers = _published_template(client, db_session)
    hr = make_user(db_session, username="del-hr")
    candidate = make_candidate(db_session, owner=hr, deleted=True)
    hr_headers = {"X-CSRF-Token": _login(client, hr.username)}
    assert (
        client.post(
            f"/candidates/{candidate.id}/generated-documents",
            json={
                "template_version_id": template["versions"][0]["id"],
                "idempotency_key": "deleted-0001",
            },
            headers=hr_headers,
        ).status_code
        == 404
    )


# --- Generation ---------------------------------------------------------------


def test_generation_snapshot_survives_candidate_and_template_changes(
    client: TestClient, db_session: Session
) -> None:
    admin, template, _admin_headers = _published_template(client, db_session)
    hr = make_user(db_session, username="gen-hr", full_name="Петрова Мария")
    candidate = make_candidate(
        db_session,
        owner=hr,
        full_name="Иванов Иван",
        position="Инженер",
        stage=CandidateStage.NEW,
    )
    hr_headers = {"X-CSRF-Token": _login(client, hr.username)}
    active = template["versions"][0]
    created = client.post(
        f"/candidates/{candidate.id}/generated-documents",
        json={"template_version_id": active["id"], "idempotency_key": "gen-key-0001"},
        headers=hr_headers,
    )
    assert created.status_code == 201, created.text
    generation = created.json()
    assert generation["revision"] == 1
    assert generation["template_name"] == template["name"]
    assert generation["body_text"].count("Петрова Мария") == 1
    original_text = generation["body_text"]

    # The candidate and the template move on; the snapshot must not.
    candidate.full_name = "Иванов Иван (переименован)"
    candidate.position = "Ведущий инженер"
    candidate.stage = CandidateStage.HIRED
    db_session.commit()
    template = client.patch(
        f"/document-templates/{template['id']}",
        json={"name": "Оффер (переименован)", "expected_revision": template["revision"]},
        headers=_auth(client, admin.username),
    ).json()
    second = client.post(
        f"/document-templates/{template['id']}/versions",
        json={
            "title": "Новая редакция",
            "body": "Совсем другой текст",
            "expected_revision": template["revision"],
        },
        headers=_auth(client, admin.username),
    ).json()
    new_version = next(v for v in second["versions"] if v["state"] == "draft")
    _activate(client, _auth(client, admin.username), second, new_version["id"])

    stored = client.get(
        f"/candidates/{candidate.id}/generated-documents/{generation['id']}",
        headers=_auth(client, hr.username),
    ).json()
    assert stored["body_text"] == original_text
    assert stored["template_name"] == "Оффер (базовый)"
    assert stored["template_title"] == "Оффер для кандидата"
    assert stored["content_sha256"] == generation["content_sha256"]

    # A second generation uses the new version and gets the next revision.
    later = client.post(
        f"/candidates/{candidate.id}/generated-documents",
        json={"template_version_id": new_version["id"], "idempotency_key": "gen-key-0002"},
        headers=_auth(client, hr.username),
    )
    assert later.status_code == 201, later.text
    assert later.json()["revision"] == 2
    assert "Иванов Иван (переименован)" not in later.json()["body_text"]
    assert "Совсем другой текст" in later.json()["body_text"]

    page = client.get(
        f"/candidates/{candidate.id}/generated-documents", headers=_auth(client, hr.username)
    ).json()
    assert page["total"] == 2
    assert [item["revision"] for item in page["items"]] == [2, 1]
    assert page["limit"] == 20 and page["offset"] == 0


def test_generation_idempotency(client: TestClient, db_session: Session) -> None:
    admin, template, _admin_headers = _published_template(client, db_session)
    hr = make_user(db_session, username="idem-hr")
    candidate = make_candidate(db_session, owner=hr)
    hr_headers = {"X-CSRF-Token": _login(client, hr.username)}
    active = template["versions"][0]
    payload = {"template_version_id": active["id"], "idempotency_key": "same-key-0001"}

    first = client.post(
        f"/candidates/{candidate.id}/generated-documents", json=payload, headers=hr_headers
    )
    assert first.status_code == 201
    replay = client.post(
        f"/candidates/{candidate.id}/generated-documents", json=payload, headers=hr_headers
    )
    assert replay.status_code == 200
    assert replay.json()["id"] == first.json()["id"]
    assert db_session.scalars(select(CandidateDocumentGeneration)).all().__len__() == 1

    # The same key bound to a different template version is a conflict.
    other = client.post(
        f"/document-templates/{template['id']}/versions",
        json={"title": "Другая", "body": "Другой текст", "expected_revision": template["revision"]},
        headers=_auth(client, admin.username),
    )
    assert other.status_code == 201, other.text
    other_id = next(v["id"] for v in other.json()["versions"] if v["state"] == "draft")
    conflict = client.post(
        f"/candidates/{candidate.id}/generated-documents",
        json={"template_version_id": other_id, "idempotency_key": "same-key-0001"},
        headers=_auth(client, hr.username),
    )
    assert conflict.status_code == 409


def test_generation_requires_candidate_access(client: TestClient, db_session: Session) -> None:
    _admin, template, _headers = _published_template(client, db_session)
    hr = make_user(db_session, username="own-hr")
    other = make_user(db_session, username="other-hr")
    candidate = make_candidate(db_session, owner=hr)
    other_headers = {"X-CSRF-Token": _login(client, other.username)}
    version_id = template["versions"][0]["id"]
    assert (
        client.post(
            f"/candidates/{candidate.id}/generated-documents/preview",
            json={"template_version_id": version_id},
            headers=other_headers,
        ).status_code
        == 404
    )
    assert (
        client.post(
            f"/candidates/{candidate.id}/generated-documents",
            json={"template_version_id": version_id, "idempotency_key": "other-key-01"},
            headers=other_headers,
        ).status_code
        == 404
    )
    owner_headers = {"X-CSRF-Token": _login(client, hr.username)}
    assert client.get(
        f"/candidates/{candidate.id}/generated-documents", headers=owner_headers
    ).json() == {"items": [], "total": 0, "limit": 20, "offset": 0}


# --- Stage scope --------------------------------------------------------------


def test_scope_applies_helper_matches_stored_stage_values() -> None:
    """The gate compares stored values, not display labels."""
    assert scope_applies("", CandidateStage.NEW) is True
    assert scope_applies(None, CandidateStage.FIRED) is True
    assert scope_applies("all", CandidateStage.REACHED) is True
    # Case/whitespace of a stored scope must not turn a valid template off.
    assert scope_applies(" Offer ", CandidateStage.OFFER) is True
    # A label is not a value: «Оффер» is not the stored form of offer.
    assert scope_applies("Оффер", CandidateStage.OFFER) is False
    # Only a matching value (and an unknown value) behave as documented.
    assert scope_applies("hired", CandidateStage.HIRED) is True
    assert scope_applies("hired", CandidateStage.OFFER) is False
    assert scope_applies("", CandidateStage.NEW) is True


def _scoped_template(client: TestClient, db: Session, *, scope: str | None) -> dict:
    admin = make_user(db, username=f"scoped-admin-{scope or 'all'}", role=UserRole.ADMIN)
    headers = {"X-CSRF-Token": _login(client, admin.username)}
    template = _create_template(client, headers, scope=scope)
    version = template["versions"][0]
    template = _activate(client, headers, template, version["id"])
    return {"template": template, "version": template["versions"][0], "headers": headers}


def test_scope_for_all_stages_renders_every_stage(client: TestClient, db_session: Session) -> None:
    fixture = _scoped_template(client, db_session, scope=None)
    assert fixture["template"]["scope"] == ""
    hr = make_user(db_session, username="scope-all-hr")
    headers = {"X-CSRF-Token": _login(client, hr.username)}
    for index, stage in enumerate((CandidateStage.NEW, CandidateStage.HIRED), start=1):
        candidate = make_candidate(db_session, owner=hr, stage=stage)
        preview = client.post(
            f"/candidates/{candidate.id}/generated-documents/preview",
            json={"template_version_id": fixture["version"]["id"]},
            headers=headers,
        )
        assert preview.status_code == 200, (stage, preview.text)
        created = client.post(
            f"/candidates/{candidate.id}/generated-documents",
            json={
                "template_version_id": fixture["version"]["id"],
                "idempotency_key": f"scope-all-key-{index:02d}",
            },
            headers=headers,
        )
        assert created.status_code == 201, (stage, created.text)
        assert created.json()["revision"] == 1


def test_scope_matching_stage_allows_preview_and_generate(
    client: TestClient, db_session: Session
) -> None:
    fixture = _scoped_template(client, db_session, scope="offer")
    hr = make_user(db_session, username="scope-match-hr")
    candidate = make_candidate(db_session, owner=hr, stage=CandidateStage.OFFER)
    headers = {"X-CSRF-Token": _login(client, hr.username)}
    preview = client.post(
        f"/candidates/{candidate.id}/generated-documents/preview",
        json={"template_version_id": fixture["version"]["id"]},
        headers=headers,
    )
    assert preview.status_code == 200, preview.text
    created = client.post(
        f"/candidates/{candidate.id}/generated-documents",
        json={"template_version_id": fixture["version"]["id"], "idempotency_key": "scope-ok-00001"},
        headers=headers,
    )
    assert created.status_code == 201, created.text


def test_scope_mismatch_returns_422_for_preview_and_generate(
    client: TestClient, db_session: Session
) -> None:
    fixture = _scoped_template(client, db_session, scope="offer")
    hr = make_user(db_session, username="scope-miss-hr")
    candidate = make_candidate(db_session, owner=hr, stage=CandidateStage.NEW)
    headers = {"X-CSRF-Token": _login(client, hr.username)}
    version_id = fixture["version"]["id"]

    preview = client.post(
        f"/candidates/{candidate.id}/generated-documents/preview",
        json={"template_version_id": version_id},
        headers=headers,
    )
    assert preview.status_code == 422, preview.text
    detail = preview.json()["detail"]
    assert "Оффер" in detail and "Новый" in detail
    # The message carries no candidate data.
    assert candidate.full_name not in detail and (candidate.email or "—") not in detail

    created = client.post(
        f"/candidates/{candidate.id}/generated-documents",
        json={"template_version_id": version_id, "idempotency_key": "scope-bad-00001"},
        headers=headers,
    )
    assert created.status_code == 422, created.text
    assert created.json()["detail"] == detail
    # Nothing was persisted and the revision counter did not move.
    assert db_session.scalars(select(CandidateDocumentGeneration)).all() == []
    assert (
        client.get(f"/candidates/{candidate.id}/generated-documents", headers=headers).json()[
            "total"
        ]
        == 0
    )

    # After the candidate reaches the scoped stage the same call succeeds, and
    # a failed attempt left no idempotency trace behind.
    candidate.stage = CandidateStage.OFFER
    db_session.commit()
    retry = client.post(
        f"/candidates/{candidate.id}/generated-documents",
        json={"template_version_id": version_id, "idempotency_key": "scope-bad-00001"},
        headers=headers,
    )
    assert retry.status_code == 201, retry.text
    assert retry.json()["revision"] == 1


def test_scope_check_keeps_snapshot_and_idempotency_intact(
    client: TestClient, db_session: Session
) -> None:
    """A snapshot created in scope survives a later stage change of the candidate."""
    fixture = _scoped_template(client, db_session, scope="offer")
    hr = make_user(db_session, username="scope-snap-hr")
    candidate = make_candidate(db_session, owner=hr, stage=CandidateStage.OFFER)
    headers = {"X-CSRF-Token": _login(client, hr.username)}
    version_id = fixture["version"]["id"]
    payload = {"template_version_id": version_id, "idempotency_key": "scope-snap-0001"}
    created = client.post(
        f"/candidates/{candidate.id}/generated-documents", json=payload, headers=headers
    )
    assert created.status_code == 201, created.text

    # The candidate moves out of scope: history stays readable, the document
    # does not change, and the idempotent replay still returns the same row.
    candidate.stage = CandidateStage.HIRED
    db_session.commit()
    replay = client.post(
        f"/candidates/{candidate.id}/generated-documents", json=payload, headers=headers
    )
    assert replay.status_code == 200, replay.text
    assert replay.json()["id"] == created.json()["id"]
    assert replay.json()["body_text"] == created.json()["body_text"]
    stored = client.get(
        f"/candidates/{candidate.id}/generated-documents/{created.json()['id']}",
        headers=headers,
    )
    assert stored.status_code == 200
    assert stored.json()["content_sha256"] == created.json()["content_sha256"]
    # A new document for the same candidate now fails the scope gate.
    fresh = client.post(
        f"/candidates/{candidate.id}/generated-documents",
        json={"template_version_id": version_id, "idempotency_key": "scope-snap-0002"},
        headers=headers,
    )
    assert fresh.status_code == 422


def test_scope_mismatch_does_not_leak_foreign_candidates(
    client: TestClient, db_session: Session
) -> None:
    """RBAC wins over the scope gate: a foreign candidate is a 404, never a 422."""
    fixture = _scoped_template(client, db_session, scope="offer")
    owner = make_user(db_session, username="scope-owner")
    stranger = make_user(db_session, username="scope-stranger")
    candidate = make_candidate(db_session, owner=owner, stage=CandidateStage.NEW)
    headers = {"X-CSRF-Token": _login(client, stranger.username)}
    version_id = fixture["version"]["id"]
    assert (
        client.post(
            f"/candidates/{candidate.id}/generated-documents/preview",
            json={"template_version_id": version_id},
            headers=headers,
        ).status_code
        == 404
    )
    assert (
        client.post(
            f"/candidates/{candidate.id}/generated-documents",
            json={"template_version_id": version_id, "idempotency_key": "scope-idor-0001"},
            headers=headers,
        ).status_code
        == 404
    )


def test_scope_literal_all_written_outside_the_api_stays_applicable(
    client: TestClient, db_session: Session
) -> None:
    """The API stores the empty string for "all stages"; a row written directly
    into the database with the literal "all" must not become unusable."""
    admin = make_user(db_session, username="legacy-admin", role=UserRole.ADMIN)
    {"X-CSRF-Token": _login(client, admin.username)}
    template = DocumentTemplate(kind="offer", scope="all", name="Легаси-шаблон", author_id=admin.id)
    db_session.add(template)
    db_session.flush()
    version = DocumentTemplateVersion(
        template_id=template.id,
        number=1,
        state="active",
        title="Оффер",
        body="Привет, {{candidate.full_name}}",
        placeholders=["candidate.full_name"],
        author_id=admin.id,
        activated_at=ops.utc_now(),
    )
    db_session.add(version)
    db_session.commit()

    hr = make_user(db_session, username="legacy-hr")
    candidate = make_candidate(db_session, owner=hr, stage=CandidateStage.REJECTED)
    preview = client.post(
        f"/candidates/{candidate.id}/generated-documents/preview",
        json={"template_version_id": str(version.id)},
        headers={"X-CSRF-Token": _login(client, hr.username)},
    )
    assert preview.status_code == 200, preview.text


# --- Download -----------------------------------------------------------------


def test_download_returns_artifacts_and_is_audited_without_content(
    client: TestClient,
    db_session: Session,
    caplog: pytest.LogCaptureFixture,
) -> None:
    _admin, template, _admin_headers = _published_template(client, db_session)
    hr = make_user(db_session, username="dl-hr", full_name="Петрова Мария")
    candidate = make_candidate(
        db_session, owner=hr, full_name="Иванов Иван", email="ivanov@example.com"
    )
    hr_headers = {"X-CSRF-Token": _login(client, hr.username)}
    generation = client.post(
        f"/candidates/{candidate.id}/generated-documents",
        json={
            "template_version_id": template["versions"][0]["id"],
            "idempotency_key": "dl-key-000001",
        },
        headers=hr_headers,
    ).json()

    with caplog.at_level(logging.DEBUG):
        html = client.get(
            f"/candidates/{candidate.id}/generated-documents/{generation['id']}/download?format=html",
            headers=hr_headers,
        )
        text = client.get(
            f"/candidates/{candidate.id}/generated-documents/{generation['id']}/download?format=txt",
            headers=hr_headers,
        )
    assert html.status_code == 200 and text.status_code == 200
    assert html.headers["content-type"].startswith("text/html")
    assert html.headers["content-disposition"] == 'attachment; filename="document-offer-rev1.html"'
    assert html.headers["cache-control"] == "no-store"
    assert html.headers["x-content-type-options"] == "nosniff"
    csp = html.headers["content-security-policy"]
    assert csp == "default-src 'none'; style-src 'unsafe-inline'"
    assert text.headers["content-type"].startswith("text/plain")
    assert text.headers["content-disposition"] == 'attachment; filename="document-offer-rev1.txt"'
    assert "Иванов Иван" in text.text
    assert "<!DOCTYPE html>" in html.text

    unsupported = client.get(
        f"/candidates/{candidate.id}/generated-documents/{generation['id']}/download?format=docx",
        headers=hr_headers,
    )
    assert unsupported.status_code == 422
    assert (
        client.get(
            f"/candidates/{candidate.id}/generated-documents/{uuid4()}/download",
            headers=hr_headers,
        ).status_code
        == 404
    )
    other = make_user(db_session, username="dl-other")
    other_headers = {"X-CSRF-Token": _login(client, other.username)}
    assert (
        client.get(
            f"/candidates/{candidate.id}/generated-documents/{generation['id']}/download",
            headers=other_headers,
        ).status_code
        == 404
    )

    events = db_session.scalars(
        select(AuditEvent).where(AuditEvent.action == "document_downloaded")
    ).all()
    assert len(events) == 2
    details = " ".join(event.details or "" for event in events)
    assert "format=html" in details and "format=txt" in details
    assert "Иванов Иван" not in details and "ivanov@example.com" not in details
    assert generation["content_sha256"][:16] in details

    # No document text and no personal data in the logs either.
    logged = " ".join(record.getMessage() for record in caplog.records)
    assert "Иванов Иван" not in logged
    assert "Здравствуйте" not in logged


def test_generated_document_is_immutable_at_the_orm_level(
    client: TestClient, db_session: Session
) -> None:
    """SQLite units cannot exercise the PG triggers; the ORM layer still must
    never update or delete a snapshot (verified for PostgreSQL separately)."""
    _admin, template, _headers = _published_template(client, db_session)
    hr = make_user(db_session, username="orm-hr")
    candidate = make_candidate(db_session, owner=hr)
    hr_headers = {"X-CSRF-Token": _login(client, hr.username)}
    generation = client.post(
        f"/candidates/{candidate.id}/generated-documents",
        json={
            "template_version_id": template["versions"][0]["id"],
            "idempotency_key": "orm-key-00001",
        },
        headers=hr_headers,
    ).json()
    assert not hasattr(ops, "update_generation")
    assert not hasattr(ops, "delete_generation")
    row = db_session.get(CandidateDocumentGeneration, UUID(generation["id"]))
    assert row is not None and row.body_text == generation["body_text"]
    version_row = db_session.get(DocumentTemplateVersion, UUID(template["versions"][0]["id"]))
    assert version_row is not None and version_row.state == "active"
    template_row = db_session.get(DocumentTemplate, UUID(template["id"]))
    assert template_row is not None and template_row.kind == "offer"
