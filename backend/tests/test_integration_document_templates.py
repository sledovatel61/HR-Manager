"""Phase 16 on real PostgreSQL: immutability guards, unique scopes and races.

SQLite cannot prove any of this: the guards are plpgsql triggers, the single
active version per template is a partial unique index, and the revision
allocation races are advisory-lock serialized. These tests never fall back to
SQLite (``pg_client``/``pg_db`` fixtures).
"""

from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from typing import Any
from uuid import UUID

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import Engine, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.document_templates import activate_version, generate
from app.models import (
    AuditEvent,
    Candidate,
    CandidateDocumentGeneration,
    CandidateStage,
    DocumentTemplateVersion,
    User,
    UserRole,
)
from app.template_schemas import GenerateRequest
from tests.conftest import make_candidate, make_user
from tests.test_candidate_messages import _login

pytestmark = pytest.mark.integration

CONTENT: dict[str, Any] = {
    "kind": "offer",
    "name": "Оффер (PG)",
    "title": "Оффер для кандидата",
    "body": "Здравствуйте, {{candidate.full_name}}!\nДата: {{system.date}}",
}


def parallel(action: Callable[[int], int]) -> list[int]:
    barrier = Barrier(2)

    def run(i: int) -> int:
        barrier.wait(timeout=10)
        return action(i)

    with ThreadPoolExecutor(max_workers=2) as pool:
        return list(pool.map(run, [0, 1]))


def _auth(client: TestClient, username: str) -> dict[str, str]:
    return {"X-CSRF-Token": _login(client, username)}


def _published_template(client: TestClient, db: Session) -> tuple[User, dict, dict[str, str]]:
    admin = make_user(db, username="pg-tpl-admin", role=UserRole.ADMIN)
    template = client.post(
        "/document-templates", json=CONTENT, headers=_auth(client, admin.username)
    ).json()
    version_id = template["versions"][0]["id"]
    activated = client.post(
        f"/document-templates/{template['id']}/versions/{version_id}/activate",
        json={"expected_revision": template["revision"]},
        headers=_auth(client, admin.username),
    )
    assert activated.status_code == 200, activated.text
    return admin, activated.json(), _auth(client, admin.username)


def _expect_integrity_error(db: Session, statement: str, **params: Any) -> str:
    with pytest.raises(IntegrityError) as excinfo:
        db.execute(text(statement), params or None)
        db.flush()
    db.rollback()
    assert excinfo.value.orig is not None
    return str(excinfo.value.orig)


def test_pg_immutability_triggers(pg_client: TestClient, pg_db: Session) -> None:
    _admin, template, _headers = _published_template(pg_client, pg_db)
    version_id = template["versions"][0]["id"]
    hr = make_user(pg_db, username="pg-guard-hr", full_name="Петрова Мария")
    candidate = make_candidate(pg_db, owner=hr, full_name="Иванов Иван")
    generation = pg_client.post(
        f"/candidates/{candidate.id}/generated-documents",
        json={"template_version_id": version_id, "idempotency_key": "pg-guard-0001"},
        headers=_auth(pg_client, hr.username),
    ).json()

    # Version content is frozen: only state/activated_at may move forward.
    assert "immutable_template_version" in _expect_integrity_error(
        pg_db,
        "UPDATE document_template_versions SET body = 'hacked' WHERE id = :id",
        id=version_id,
    )
    assert "immutable_template_version" in _expect_integrity_error(
        pg_db,
        "UPDATE document_template_versions SET title = 'Другое' WHERE id = :id",
        id=version_id,
    )
    assert "immutable_template_version" in _expect_integrity_error(
        pg_db,
        "UPDATE document_template_versions SET placeholders = '[]'::json WHERE id = :id",
        id=version_id,
    )
    assert "immutable_template_version" in _expect_integrity_error(
        pg_db, "DELETE FROM document_template_versions WHERE id = :id", id=version_id
    )
    # An archived version cannot be resurrected.
    assert "immutable_template_version" in _expect_integrity_error(
        pg_db,
        "UPDATE document_template_versions SET state = 'active' WHERE id = :id",
        id=version_id,
    )
    # active -> archived is the legal terminal transition.
    pg_db.execute(
        text("UPDATE document_template_versions SET state = 'archived' WHERE id = :id"),
        {"id": version_id},
    )
    pg_db.commit()
    archived_row = pg_db.get(DocumentTemplateVersion, UUID(version_id))
    assert archived_row is not None and archived_row.state == "archived"

    # Template identity is frozen, the display name is not, deletion is never.
    assert "immutable_template" in _expect_integrity_error(
        pg_db,
        "UPDATE document_templates SET scope = 'hired' WHERE id = :id",
        id=template["id"],
    )
    assert "immutable_template" in _expect_integrity_error(
        pg_db,
        "UPDATE document_templates SET kind = 'dogovor' WHERE id = :id",
        id=template["id"],
    )
    assert "immutable_template" in _expect_integrity_error(
        pg_db, "DELETE FROM document_templates WHERE id = :id", id=template["id"]
    )
    pg_db.execute(
        text("UPDATE document_templates SET name = 'Оффер (новое имя)' WHERE id = :id"),
        {"id": template["id"]},
    )
    pg_db.commit()

    # Generated snapshots are append-only.
    assert "immutable_generation" in _expect_integrity_error(
        pg_db,
        "UPDATE candidate_document_generations SET body_text = 'hacked' WHERE id = :id",
        id=generation["id"],
    )
    assert "immutable_generation" in _expect_integrity_error(
        pg_db,
        "DELETE FROM candidate_document_generations WHERE id = :id",
        id=generation["id"],
    )
    # The snapshot kept the template name as of generation time.
    row = pg_db.get(CandidateDocumentGeneration, UUID(generation["id"]))
    assert row is not None
    pg_db.refresh(row)
    assert row.template_name == CONTENT["name"]
    assert row.template_name != "Оффер (новое имя)"


def test_pg_single_active_version_per_template(pg_client: TestClient, pg_db: Session) -> None:
    _admin, template, headers = _published_template(pg_client, pg_db)
    second = pg_client.post(
        f"/document-templates/{template['id']}/versions",
        json={
            "title": "Вторая версия",
            "body": "Текст второй версии",
            "expected_revision": template["revision"],
        },
        headers=headers,
    )
    assert second.status_code == 201, second.text
    draft = next(v for v in second.json()["versions"] if v["state"] == "draft")

    # Two active versions of one template are impossible at the database level.
    error = _expect_integrity_error(
        pg_db,
        "UPDATE document_template_versions SET state = 'active', activated_at = now() "
        "WHERE id = :id",
        id=draft["id"],
    )
    assert "uq_template_versions_active" in error or "duplicate key" in error

    # The API path archives the previous active version first and succeeds.
    activated = pg_client.post(
        f"/document-templates/{template['id']}/versions/{draft['id']}/activate",
        json={"expected_revision": second.json()["revision"]},
        headers=headers,
    )
    assert activated.status_code == 200, activated.text
    states = {v["number"]: v["state"] for v in activated.json()["versions"]}
    assert states == {1: "archived", 2: "active"}


def test_pg_concurrent_activation_serializes(
    pg_client: TestClient, pg_db: Session, pg_engine: Engine
) -> None:
    admin, template, headers = _published_template(pg_client, pg_db)
    first = pg_client.post(
        f"/document-templates/{template['id']}/versions",
        json={"title": "Черновик", "body": "Текст", "expected_revision": template["revision"]},
        headers=headers,
    ).json()
    draft_id = next(v["id"] for v in first["versions"] if v["state"] == "draft")
    version_id = UUID(draft_id)
    template_id = UUID(template["id"])
    admin_id = admin.id
    expected = first["revision"]
    pg_db.rollback()

    def action(_: int) -> int:
        with Session(pg_engine) as db:
            user = db.get(User, admin_id)
            assert user
            try:
                activate_version(db, user, template_id, version_id, expected)
                return 200
            except HTTPException as exc:
                db.rollback()
                return exc.status_code

    assert sorted(parallel(action)) == [200, 409]
    with Session(pg_engine) as db:
        active = db.scalars(
            select(DocumentTemplateVersion).where(
                DocumentTemplateVersion.template_id == template_id,
                DocumentTemplateVersion.state == "active",
            )
        ).all()
        assert len(active) == 1
        assert active[0].id == version_id


def test_pg_concurrent_generation_allocates_distinct_revisions(
    pg_client: TestClient, pg_db: Session, pg_engine: Engine
) -> None:
    _admin, template, _headers = _published_template(pg_client, pg_db)
    hr = make_user(pg_db, username="pg-gen-hr", full_name="Петрова Мария")
    candidate = make_candidate(pg_db, owner=hr, full_name="Иванов Иван")
    version_id = UUID(template["versions"][0]["id"])
    candidate_id = candidate.id
    hr_id = hr.id
    hr_name = hr.username
    pg_db.rollback()
    _auth(pg_client, hr_name)

    def action(i: int) -> int:
        with Session(pg_engine) as db:
            user = db.get(User, hr_id)
            cand = db.get(Candidate, candidate_id)
            assert user and cand
            request = GenerateRequest(
                template_version_id=version_id, idempotency_key=f"pg-race-key-{i:04d}"
            )
            try:
                _row, created = generate(
                    db,
                    user=user,
                    candidate=cand,
                    payload=request,
                    default_timezone="Europe/Moscow",
                )
                return 201 if created else 200
            except HTTPException as exc:
                db.rollback()
                return exc.status_code

    assert sorted(parallel(action)) == [201, 201]
    with Session(pg_engine) as db:
        revisions = db.scalars(
            select(CandidateDocumentGeneration.revision).where(
                CandidateDocumentGeneration.candidate_id == candidate_id
            )
        ).all()
        assert sorted(revisions) == [1, 2]

    # The same idempotency key in parallel yields exactly one stored document.
    def same_key(_: int) -> int:
        with Session(pg_engine) as db:
            user = db.get(User, hr_id)
            cand = db.get(Candidate, candidate_id)
            assert user and cand
            request = GenerateRequest(
                template_version_id=version_id, idempotency_key="pg-same-key-0001"
            )
            try:
                _row, created = generate(
                    db,
                    user=user,
                    candidate=cand,
                    payload=request,
                    default_timezone="Europe/Moscow",
                )
                return 201 if created else 200
            except HTTPException as exc:
                db.rollback()
                return exc.status_code

    assert sorted(parallel(same_key)) == [200, 201]
    with Session(pg_engine) as db:
        keys = db.scalars(
            select(CandidateDocumentGeneration.idempotency_key).where(
                CandidateDocumentGeneration.candidate_id == candidate_id
            )
        ).all()
        assert keys.count("pg-same-key-0001") == 1


def test_pg_http_flow_matches_units(pg_client: TestClient, pg_db: Session) -> None:
    """One end-to-end HTTP pass on PostgreSQL: publish, generate, download."""
    _admin, template, _headers = _published_template(pg_client, pg_db)
    hr = make_user(pg_db, username="pg-flow-hr", full_name="Петрова Мария")
    candidate = make_candidate(
        pg_db, owner=hr, full_name="Иванов Иван", position="Инженер", email="ivanov@example.com"
    )
    hr_headers = _auth(pg_client, hr.username)
    version_id = template["versions"][0]["id"]

    preview = pg_client.post(
        f"/candidates/{candidate.id}/generated-documents/preview",
        json={"template_version_id": version_id},
        headers=hr_headers,
    )
    assert preview.status_code == 200, preview.text
    assert "Иванов Иван" in preview.json()["body_text"]

    created = pg_client.post(
        f"/candidates/{candidate.id}/generated-documents",
        json={"template_version_id": version_id, "idempotency_key": "pg-flow-00001"},
        headers=hr_headers,
    )
    assert created.status_code == 201, created.text
    generation = created.json()

    download = pg_client.get(
        f"/candidates/{candidate.id}/generated-documents/{generation['id']}/download",
        headers=hr_headers,
    )
    assert download.status_code == 200
    assert download.headers["content-disposition"].endswith('.html"')
    assert "Иванов Иван" in download.text

    audit = pg_db.execute(
        text(
            "SELECT action, candidate_id FROM audit_log WHERE action IN "
            "('document_downloaded','document_generated') ORDER BY created_at"
        )
    ).all()
    assert [row[0] for row in audit] == ["document_generated", "document_downloaded"]
    assert all(row[1] == candidate.id for row in audit)


def test_pg_scope_gate_blocks_before_insert(
    pg_client: TestClient, pg_db: Session, pg_engine: Engine
) -> None:
    """The stage scope is enforced on PostgreSQL exactly as in the unit suite.

    ``scope`` is part of a template's frozen identity (the trigger forbids
    changing it), so the scoped and all-stages templates are created through the
    API. A mismatch answers 422 from preview and from generate, leaves no row and
    no audit record, while a matching generation keeps its snapshot and its
    idempotent replay after the candidate leaves the scope.
    """
    admin, scoped_template, _headers = _published_template(pg_client, pg_db)
    hr = make_user(pg_db, username="pg-scope-hr", full_name="Петрова Мария")
    candidate = make_candidate(pg_db, owner=hr, full_name="Иванов Иван")
    scoped_version = scoped_template["versions"][0]["id"]

    # A scope can only be set at creation time; the API validates the value.
    scoped = pg_client.post(
        "/document-templates",
        json={**CONTENT, "kind": "anketa", "name": "Анкета (оффер)", "scope": "offer"},
        headers=_auth(pg_client, admin.username),
    )
    assert scoped.status_code == 201, scoped.text
    open_template = pg_client.post(
        "/document-templates",
        json={**CONTENT, "kind": "dogovor", "name": "Договор (все этапы)"},
        headers=_auth(pg_client, admin.username),
    )
    assert open_template.status_code == 201, open_template.text
    assert open_template.json()["scope"] == ""
    scoped_version_id = scoped.json()["versions"][0]["id"]
    open_version_id = open_template.json()["versions"][0]["id"]
    # A version must be published before it can render anything.
    for template, version_id in (
        (scoped.json(), scoped_version_id),
        (open_template.json(), open_version_id),
    ):
        activated = pg_client.post(
            f"/document-templates/{template['id']}/versions/{version_id}/activate",
            json={"expected_revision": template["revision"]},
            headers=_auth(pg_client, admin.username),
        )
        assert activated.status_code == 200, activated.text

    preview = pg_client.post(
        f"/candidates/{candidate.id}/generated-documents/preview",
        json={"template_version_id": scoped_version_id},
        headers=_auth(pg_client, hr.username),
    )
    assert preview.status_code == 422, preview.text
    assert "Оффер" in preview.json()["detail"]
    generate = pg_client.post(
        f"/candidates/{candidate.id}/generated-documents",
        json={"template_version_id": scoped_version_id, "idempotency_key": "pg-scope-0001"},
        headers=_auth(pg_client, hr.username),
    )
    assert generate.status_code == 422, generate.text

    # The all-stages template works for the same candidate right away.
    open_preview = pg_client.post(
        f"/candidates/{candidate.id}/generated-documents/preview",
        json={"template_version_id": open_version_id},
        headers=_auth(pg_client, hr.username),
    )
    assert open_preview.status_code == 200, open_preview.text

    with Session(pg_engine) as db:
        assert db.scalars(select(CandidateDocumentGeneration)).all() == []
        actions = db.scalars(
            select(AuditEvent.action).where(AuditEvent.action == "document_generated")
        ).all()
        assert actions == []

    # Reaching the scoped stage opens the gate on the very same key.
    candidate.stage = CandidateStage.OFFER
    pg_db.commit()
    created = pg_client.post(
        f"/candidates/{candidate.id}/generated-documents",
        json={"template_version_id": scoped_version_id, "idempotency_key": "pg-scope-0001"},
        headers=_auth(pg_client, hr.username),
    )
    assert created.status_code == 201, created.text

    # Leaving the scope keeps the snapshot and the idempotent replay, but blocks
    # a new document.
    candidate.stage = CandidateStage.HIRED
    pg_db.commit()
    replay = pg_client.post(
        f"/candidates/{candidate.id}/generated-documents",
        json={"template_version_id": scoped_version_id, "idempotency_key": "pg-scope-0001"},
        headers=_auth(pg_client, hr.username),
    )
    assert replay.status_code == 200
    assert replay.json()["id"] == created.json()["id"]
    blocked = pg_client.post(
        f"/candidates/{candidate.id}/generated-documents",
        json={"template_version_id": scoped_version_id, "idempotency_key": "pg-scope-0002"},
        headers=_auth(pg_client, hr.username),
    )
    assert blocked.status_code == 422

    # The unmatched template stays untouched by all of this.
    assert scoped_version != scoped_version_id
    with Session(pg_engine) as db:
        assert len(db.scalars(select(CandidateDocumentGeneration)).all()) == 1
