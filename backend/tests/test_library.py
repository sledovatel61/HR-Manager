"""«Библиотека HR» unit tests (SQLite): seed catalog, cards, detail, downloads.

The library reuses the phase 16 template contract, so these tests focus on
what the rework adds: the idempotent built-in catalog, the read-only screens
available to a plain HR, draft invisibility, safe downloads with the phase 16
header/audit discipline, and the import duplicate question.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.library import (
    CATEGORY_LABELS,
    LIBRARY_CATEGORIES,
    SUMMARY_MAX_LENGTH,
)
from app.library_seed import seed_count_missing, seed_library, seed_materials, seed_template_count
from app.models import (
    AuditAction,
    AuditEvent,
    DocumentTemplate,
    DocumentTemplateVersion,
    User,
    UserRole,
)
from app.template_render import BODY_MAX_LENGTH
from tests.conftest import make_user
from tests.test_candidate_messages import _login


def _headers(client: TestClient, username: str) -> dict[str, str]:
    return {"X-CSRF-Token": _login(client, username)}


def _seed(db: Session) -> User:
    """A user plus the built-in catalog (the same call the startup performs)."""
    admin = make_user(db, username="seed-admin", role=UserRole.ADMIN)
    seed_library(db)
    return admin


# --- Built-in catalog ---------------------------------------------------------


def test_seed_creates_published_catalog(client: TestClient, db_session: Session) -> None:
    _seed(db_session)
    assert seed_count_missing(db_session) == 0
    templates = db_session.scalars(select(DocumentTemplate)).all()
    assert len(templates) == len(seed_materials())
    for template in templates:
        assert template.seed_key
        versions = db_session.scalars(
            select(DocumentTemplateVersion).where(
                DocumentTemplateVersion.template_id == template.id
            )
        ).all()
        assert len(versions) == 1
        assert versions[0].state == "active"
        assert versions[0].activated_at is not None
        assert template.category in LIBRARY_CATEGORIES
        assert template.summary.strip()
        # Printable, closed-form content: validated by the same pipeline as
        # user input and inside the body limit.
        assert 1 <= len(versions[0].body) <= BODY_MAX_LENGTH


@pytest.mark.parametrize("material", seed_materials(), ids=lambda item: item.key)
def test_seed_material_content_rules(material: Any) -> None:
    """No personal data, no local paths, no raw tokens in the built-in texts."""
    assert material.name.strip() and material.title.strip()
    assert len(material.summary) <= SUMMARY_MAX_LENGTH
    body = material.body
    assert "{{" not in body and "}}" not in body
    assert "C:\\" not in body and "C:/" not in body
    for forbidden in ("C:\\Users", ".docx", ".xlsx"):
        assert forbidden not in body


def test_seed_is_idempotent(client: TestClient, db_session: Session) -> None:
    _seed(db_session)
    before = seed_template_count(db_session)
    assert seed_library(db_session) == 0
    assert seed_template_count(db_session) == before
    assert seed_count_missing(db_session) == 0


def test_seed_never_touches_owner_changes(db_session: Session) -> None:
    """An archived/renamed seed material stays as the owner left it."""
    make_user(db_session, username="seed-owner", role=UserRole.ADMIN)
    seed_library(db_session)
    template = db_session.scalar(
        select(DocumentTemplate).where(DocumentTemplate.seed_key == "call_screening_checklist")
    )
    assert template is not None
    template.name = "Мой утверждённый чек-лист"
    version = db_session.scalar(
        select(DocumentTemplateVersion).where(
            DocumentTemplateVersion.template_id == template.id,
        )
    )
    assert version is not None
    version.state = "archived"
    db_session.commit()

    seed_library(db_session)

    db_session.refresh(template)
    assert template.name == "Мой утверждённый чек-лист"
    db_session.refresh(version)
    assert version.state == "archived"
    assert seed_template_count(db_session) == len(seed_materials())


def test_category_dictionary_is_closed_and_labelled() -> None:
    assert set(CATEGORY_LABELS) == set(LIBRARY_CATEGORIES)
    assert all(label.strip() for label in CATEGORY_LABELS.values())


# --- Library screens ----------------------------------------------------------


def _make_material(
    client: TestClient,
    headers: dict[str, str],
    *,
    name: str = "Памятка HR",
    category: str = "memos",
    summary: str = "Короткое назначение материала.",
    body: str = "- пункт один\n- пункт два",
    kind: str = "memo",
    publish: bool = True,
) -> dict:
    response = client.post(
        "/document-templates",
        json={
            "kind": kind,
            "name": name,
            "category": category,
            "summary": summary,
            "title": f"Заголовок: {name}",
            "body": body,
        },
        headers=headers,
    )
    assert response.status_code == 201, response.text
    template = response.json()
    assert template["category"] == category
    assert template["summary"] == summary
    if publish:
        activate = client.post(
            f"/document-templates/{template['id']}/versions/{template['versions'][0]['id']}/activate",
            json={"expected_revision": template["revision"]},
            headers=headers,
        )
        assert activate.status_code == 200, activate.text
    return template


def test_library_lists_published_materials_for_plain_hr(
    client: TestClient, db_session: Session
) -> None:
    admin = make_user(db_session, username="lib-admin", role=UserRole.ADMIN)
    headers = _headers(client, admin.username)
    published = _make_material(client, headers, name="Опубликовано")
    _make_material(client, headers, name="Только черновик", publish=False)

    hr = make_user(db_session, username="lib-hr", role=UserRole.HR)
    hr_headers = _headers(client, hr.username)
    response = client.get("/library/materials", headers=hr_headers)
    assert response.status_code == 200, response.text
    data = response.json()
    names = [item["name"] for item in data["items"]]
    assert "Опубликовано" in names
    assert "Только черновик" not in names
    assert {item["key"] for item in data["categories"]} == set(LIBRARY_CATEGORIES)
    assert data["can_manage"] is False
    card = next(item for item in data["items"] if item["id"] == published["id"])
    assert card["category"] == "memos"
    assert card["version_number"] == 1
    assert card["published_at"] is not None
    assert "body" not in card  # cards carry no content


def test_library_detail_renders_demo_values_not_raw_tokens(
    client: TestClient, db_session: Session
) -> None:
    admin = make_user(db_session, username="lib-admin2", role=UserRole.ADMIN)
    headers = _headers(client, admin.username)
    _make_material(
        client,
        headers,
        name="Оффер с подстановками",
        category="interview",
        body="Здравствуйте, {{ candidate.full_name }}!\n- Должность: {{ candidate.position }}",
    )
    hr = make_user(db_session, username="lib-hr2", role=UserRole.HR)
    hr_headers = _headers(client, hr.username)
    listing = client.get("/library/materials", headers=hr_headers).json()
    material_id = next(
        item["id"] for item in listing["items"] if item["name"] == "Оффер с подстановками"
    )
    detail = client.get(f"/library/materials/{material_id}", headers=hr_headers)
    assert detail.status_code == 200, detail.text
    data = detail.json()
    assert data["has_placeholders"] is True
    # The regular HR never sees raw `{{ … }}` tokens: hints and demo values instead.
    assert "{{" not in data["body_text"]
    assert "Иван Тестовый" in data["body_text"]
    assert data["placeholder_hints"][0]["token"] == "candidate.full_name"
    assert data["placeholder_hints"][0]["hint"].strip()
    assert "<p>" in data["body_html"]


def test_library_draft_material_answers_404_even_for_manager(
    client: TestClient, db_session: Session
) -> None:
    admin = make_user(db_session, username="lib-admin3", role=UserRole.ADMIN)
    headers = _headers(client, admin.username)
    draft = _make_material(client, headers, name="Черновик", publish=False)

    for path in (
        f"/library/materials/{draft['id']}",
        f"/library/materials/{draft['id']}/download",
        f"/library/materials/{draft['id']}/view",
    ):
        response = client.get(path, headers=headers)
        assert response.status_code == 404, path

    missing = client.get("/library/materials/00000000-0000-0000-0000-000000000000", headers=headers)
    assert missing.status_code == 404


def test_library_download_headers_and_redacted_audit(
    client: TestClient, db_session: Session
) -> None:
    admin = make_user(db_session, username="lib-admin4", role=UserRole.ADMIN)
    headers = _headers(client, admin.username)
    material = _make_material(client, headers, name="Чек-лист звонка", category="calls")

    response = client.get(f"/library/materials/{material['id']}/download", headers=headers)
    assert response.status_code == 200, response.text
    assert response.headers["content-disposition"].startswith('attachment; filename="material-memo')
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert "sandbox" in response.headers["content-security-policy"]
    assert "пункт один" in response.text  # the content itself is the material

    txt = client.get(f"/library/materials/{material['id']}/download?format=txt", headers=headers)
    assert txt.status_code == 200
    assert "text/plain" in txt.headers["content-type"]

    bad = client.get(f"/library/materials/{material['id']}/download?format=pdf", headers=headers)
    assert bad.status_code == 422

    events = db_session.scalars(
        select(AuditEvent).where(AuditEvent.action == AuditAction.LIBRARY_MATERIAL_DOWNLOADED)
    ).all()
    assert len(events) == 2
    for event in events:
        assert "material=" in (event.details or "")
        assert "format=" in (event.details or "")
        # No material text and no personal data in the audit row.
        assert "пункт" not in (event.details or "")


def test_library_view_serves_inline_sandboxed_html(client: TestClient, db_session: Session) -> None:
    admin = make_user(db_session, username="lib-admin5", role=UserRole.ADMIN)
    headers = _headers(client, admin.username)
    material = _make_material(client, headers)

    response = client.get(f"/library/materials/{material['id']}/view", headers=headers)
    assert response.status_code == 200, response.text
    assert response.headers["content-disposition"].startswith("inline;")
    assert response.headers["cache-control"] == "no-store"
    assert "sandbox" in response.headers["content-security-policy"]
    events = db_session.scalars(
        select(AuditEvent).where(AuditEvent.action == AuditAction.LIBRARY_MATERIAL_OPENED)
    ).all()
    assert len(events) == 1


# --- Import: categories and the duplicate question ----------------------------


def _import_file(
    client: TestClient,
    headers: dict[str, str],
    *,
    filename: str,
    content: bytes,
    **form: str,
) -> Any:
    data: dict[str, str] = {key: value for key, value in form.items()}
    return client.post(
        "/document-templates/import",
        files={"file": (filename, content, "text/plain")},
        data=data,
        headers=headers,
    )


def test_import_accepts_category_and_summary(client: TestClient, db_session: Session) -> None:
    admin = make_user(db_session, username="lib-admin6", role=UserRole.ADMIN)
    headers = _headers(client, admin.username)
    response = _import_file(
        client,
        headers,
        filename="material.txt",
        content="Скрипт разговора\n- шаг один".encode(),
        kind="script",
        name="Скрипт первого звонка",
        category="calls",
        summary="Позвонить и назначить встречу.",
    )
    assert response.status_code == 201, response.text
    data = response.json()
    assert data["category"] == "calls"
    assert data["summary"] == "Позвонить и назначить встречу."


def test_import_rejects_unknown_category(client: TestClient, db_session: Session) -> None:
    admin = make_user(db_session, username="lib-admin7", role=UserRole.ADMIN)
    headers = _headers(client, admin.username)
    response = _import_file(
        client,
        headers,
        filename="material.txt",
        content="Текст\n- строка".encode(),
        kind="memo",
        name="Памятка",
        category="unknown_category",
    )
    assert response.status_code == 422, response.text


def test_reimport_asks_about_duplicate_instead_of_creating_it(
    client: TestClient, db_session: Session
) -> None:
    admin = make_user(db_session, username="lib-admin8", role=UserRole.ADMIN)
    headers = _headers(client, admin.username)
    first = _import_file(
        client,
        headers,
        filename="one.txt",
        content="Скрипт приглашения\n- шаг один".encode(),
        kind="script",
        name="Скрипт приглашения",
        category="calls",
    )
    assert first.status_code == 201, first.text
    existing_id = first.json()["id"]

    # Same kind + name, different content: an explicit question, not a silent twin.
    second = _import_file(
        client,
        headers,
        filename="two.txt",
        content="Скрипт приглашения\n- другой текст".encode(),
        kind="script",
        name="Скрипт приглашения",
        category="calls",
    )
    assert second.status_code == 409, second.text
    detail = second.json()["detail"]
    assert detail["existing"]["id"] == existing_id
    assert "уже существует" in detail["message"]

    # The explicit "new version" path adds version 2 as a draft.
    listed = client.get("/document-templates", headers=headers).json()
    template = next(item for item in listed["items"] if item["id"] == existing_id)
    revision = template["revision"]
    new_version = _import_file(
        client,
        headers,
        filename="two.txt",
        content="Скрипт приглашения\n- другой текст".encode(),
        kind="script",
        name="Скрипт приглашения",
    )
    del new_version  # unused: the version goes through import-version below
    version_response = client.post(
        f"/document-templates/{existing_id}/import-version",
        files={
            "file": (
                "two.txt",
                "Скрипт приглашения\n- другой текст".encode(),
                "text/plain",
            )
        },
        data={"expected_revision": str(revision), "title": ""},
        headers=headers,
    )
    assert version_response.status_code == 201, version_response.text
    updated = version_response.json()
    assert len(updated["versions"]) == 2
    assert updated["versions"][0]["state"] == "draft"

    # The explicit "separate material" path needs force_new=1.
    separate = _import_file(
        client,
        headers,
        filename="three.txt",
        content="Скрипт приглашения\n- третий вариант".encode(),
        kind="script",
        name="Скрипт приглашения",
        category="calls",
        force_new="1",
    )
    assert separate.status_code == 201, separate.text
    assert separate.json()["id"] != existing_id


def test_import_version_without_manage_grant_is_refused(
    client: TestClient, db_session: Session
) -> None:
    admin = make_user(db_session, username="lib-admin9", role=UserRole.ADMIN)
    headers = _headers(client, admin.username)
    material = _make_material(client, headers)

    hr = make_user(db_session, username="lib-hr3", role=UserRole.HR)
    hr_headers = _headers(client, hr.username)
    response = client.post(
        f"/document-templates/{material['id']}/import-version",
        files={"file": ("x.txt", "Текст\n- строка".encode(), "text/plain")},
        data={"expected_revision": "1"},
        headers=hr_headers,
    )
    assert response.status_code == 403, response.text
