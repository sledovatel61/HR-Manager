"""Block E — importing methodical material into the template base (UX 2026-09-29).

Covers the happy path (a ``.md`` file becomes a draft), the refusals the brief
asks for (wrong extension, oversized, binary, bad encoding, empty) and access
control: the same import must be 403 for an ordinary HR and 201 for admin / a
holder of ``document_lists_manage``.
"""

from __future__ import annotations

import io

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app import document_templates as ops
from app.models import AccessGrant, AccessGrantScope, AuditEvent, User, UserRole
from app.template_import import (
    MAX_UPLOAD_BYTES,
    SUPPORTED_EXTENSIONS,
    extension_of,
    read_template_file,
    safe_display_name,
)
from app.template_render import TemplateContentError
from tests.conftest import make_user
from tests.test_candidate_messages import _login


def _auth(client: TestClient, username: str) -> dict[str, str]:
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


def _upload(
    client: TestClient,
    headers: dict[str, str],
    *,
    content: bytes,
    filename: str,
    kind: str = "checklist",
    name: str = "",
    scope: str = "",
):
    return client.post(
        "/document-templates/import",
        data={"kind": kind, "name": name, "scope": scope},
        files={"file": (filename, io.BytesIO(content), "text/markdown")},
        headers=headers,
    )


# --------------------------------------------------------------------------
# Pure helpers
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Памятка HR.md", "Памятка HR"),
        ("C:\\Users\\User\\Documents\\скрипт.txt", "скрипт"),
        ("../../etc/passwd.md", "passwd"),
        ("  ..\\..\\windows\\offer.txt  ", "offer"),
        ("no-extension", "no-extension"),
        ("", ""),
    ],
)
def test_safe_display_name_never_keeps_a_path(raw: str, expected: str) -> None:
    assert safe_display_name(raw) == expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Памятка.MD", ".md"),
        ("a/b/c.TXT", ".txt"),
        ("noext", ""),
        (None, ""),
    ],
)
def test_extension_of(raw: str | None, expected: str) -> None:
    assert extension_of(raw) == expected


def test_read_template_file_derives_title_from_the_first_heading() -> None:
    imported = read_template_file("# Чек-лист\n\n- пункт 1".encode(), "list.md")
    assert imported.title == "Чек-лист"
    assert imported.body.startswith("# Чек-лист")
    assert imported.extension == ".md"


def test_read_template_file_derives_title_from_a_csv_header() -> None:
    imported = read_template_file("Вопрос,Ответ\nСколько лет?,—\n".encode(), "q.csv")
    assert imported.title.startswith("Вопрос")


@pytest.mark.parametrize("extension", SUPPORTED_EXTENSIONS)
def test_every_supported_extension_is_accepted(extension: str) -> None:
    imported = read_template_file("текст".encode(), f"note{extension}")
    assert imported.body == "текст"


@pytest.mark.parametrize("filename", ["offer.docx", "scan.pdf", "photo.png", "script.php", "noext"])
def test_unsupported_extension_is_refused_by_name(filename: str) -> None:
    with pytest.raises(TemplateContentError) as excinfo:
        read_template_file("текст".encode(), filename)
    assert "не поддерживается" in str(excinfo.value)


def test_oversized_file_is_refused() -> None:
    with pytest.raises(TemplateContentError) as excinfo:
        read_template_file(b"a" * (MAX_UPLOAD_BYTES + 1), "big.txt")
    assert "КБ" in str(excinfo.value)


def test_binary_content_is_refused_even_with_a_text_extension() -> None:
    with pytest.raises(TemplateContentError) as excinfo:
        read_template_file(b"%PDF-1.7\x00\x00binary", "scan.txt")
    assert "не похож на текст" in str(excinfo.value)


def test_non_utf8_file_is_refused() -> None:
    with pytest.raises(TemplateContentError) as excinfo:
        read_template_file("Привет windows-1251".encode("cp1251"), "note.txt")
    assert "UTF-8" in str(excinfo.value)


def test_empty_file_is_refused() -> None:
    with pytest.raises(TemplateContentError) as excinfo:
        read_template_file(b"   \n  ", "blank.txt")
    assert "не содержит текста" in str(excinfo.value)


def test_placeholders_in_an_imported_file_are_still_validated() -> None:
    imported = read_template_file("Здравствуйте, {{ candidate.full_name }}!".encode(), "greet.txt")
    assert "{{ candidate.full_name }}" in imported.body
    with pytest.raises(TemplateContentError):
        read_template_file(b"{{ candidate.ssn }}", "bad.txt")


# --------------------------------------------------------------------------
# HTTP: happy path, refusals, rights, audit
# --------------------------------------------------------------------------


def test_import_creates_a_draft_template(client: TestClient, db_session: Session) -> None:
    admin = make_user(db_session, username="imp-admin", role=UserRole.ADMIN)
    response = _upload(
        client,
        _auth(client, admin.username),
        content="Скрипт звонка\n\nЗдравствуйте, {{ candidate.full_name }}!".encode(),
        filename="Скрипт звонка.md",
        kind="script",
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["kind"] == "script"
    # The safe name is the template name when the HR did not give one.
    assert body["name"] == "Скрипт звонка"
    assert body["versions"][0]["state"] == "draft"
    assert body["versions"][0]["title"] == "Скрипт звонка"
    assert body["versions"][0]["placeholders"] == ["candidate.full_name"]


def test_explicit_name_and_scope_win_over_the_file_name(
    client: TestClient, db_session: Session
) -> None:
    admin = make_user(db_session, username="imp-scope", role=UserRole.ADMIN)
    response = _upload(
        client,
        _auth(client, admin.username),
        content=b"text",
        filename="file-name.md",
        kind="checklist",
        name="Скрипт для оффера",
        scope="interview_scheduled",
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["name"] == "Скрипт для оффера"
    assert body["scope"] == "interview_scheduled"


def test_import_of_a_bad_file_is_422_and_creates_nothing(
    client: TestClient, db_session: Session
) -> None:
    admin = make_user(db_session, username="imp-bad", role=UserRole.ADMIN)
    headers = _auth(client, admin.username)
    before = len(client.get("/document-templates", headers=headers).json()["items"])
    response = _upload(
        client, headers, content=b"%PDF\x00binary", filename="offer.pdf", kind="script"
    )
    assert response.status_code == 422
    assert "не поддерживается" in response.json()["detail"]
    after = len(client.get("/document-templates", headers=headers).json()["items"])
    assert after == before


def test_import_rejects_an_unknown_stage(client: TestClient, db_session: Session) -> None:
    admin = make_user(db_session, username="imp-stage", role=UserRole.ADMIN)
    response = _upload(
        client, _auth(client, admin.username), content=b"text", filename="a.md", scope="nope"
    )
    assert response.status_code == 422


def test_ordinary_hr_cannot_import(client: TestClient, db_session: Session) -> None:
    make_user(db_session, username="imp-hr", role=UserRole.HR)
    response = _upload(
        client, _auth(client, "imp-hr"), content=b"text", filename="a.md", kind="script"
    )
    assert response.status_code == 403


def test_manager_with_grant_can_import(client: TestClient, db_session: Session) -> None:
    manager = make_user(db_session, username="imp-mgr", role=UserRole.MANAGER)
    _manage_grant(db_session, manager)
    response = _upload(
        client, _auth(client, manager.username), content=b"text", filename="a.md", kind="script"
    )
    assert response.status_code == 201, response.text


def test_import_is_audited_without_the_file_contents(
    client: TestClient, db_session: Session
) -> None:
    admin = make_user(db_session, username="imp-audit", role=UserRole.ADMIN)
    response = _upload(
        client,
        _auth(client, admin.username),
        content="Паспорт кандидата".encode(),
        filename="Памятка HR.md",
        kind="memo",
    )
    template_id = response.json()["id"]
    rows = db_session.query(AuditEvent).filter(AuditEvent.actor_user_id == admin.id).all()
    entry = next(row for row in rows if template_id in (row.details or ""))
    assert entry.action == "document_template_created"
    assert "import=.md" in entry.details
    # The material itself never lands in the audit trail.
    assert "Паспорт кандидата" not in entry.details
