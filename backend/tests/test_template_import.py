"""Block E — importing methodical material into the template base (UX 2026-09-29).

Covers the happy path (a ``.md`` file becomes a draft), the refusals the brief
asks for (wrong extension, oversized, binary, bad encoding, empty) and access
control: the same import must be 403 for an ordinary HR and 201 for admin / a
holder of ``document_lists_manage``.
"""

from __future__ import annotations

import io
import zipfile

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app import document_templates as ops
from app.models import AccessGrant, AccessGrantScope, AuditEvent, User, UserRole
from app.template_import import (
    MAX_UPLOAD_BYTES,
    SUPPORTED_EXTENSIONS,
    SUPPORTED_HINT,
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
) -> httpx.Response:
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


@pytest.mark.parametrize("extension", [e for e in SUPPORTED_EXTENSIONS if e != ".docx"])
def test_every_plain_text_extension_is_accepted(extension: str) -> None:
    imported = read_template_file("текст".encode(), f"note{extension}")
    assert imported.body == "текст"


@pytest.mark.parametrize("filename", ["photo.png", "script.php", "noext", "page.odt", "sheet.xls"])
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
        client, headers, content=b"PNG\x89binary", filename="offer.png", kind="script"
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
    assert entry.details is not None
    assert "import=.md" in entry.details
    # The material itself never lands in the audit trail.
    assert "Паспорт кандидата" not in entry.details


# --------------------------------------------------------------------------
# .docx (block E rework) and the .pdf refusal
# --------------------------------------------------------------------------

_W_NS = 'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"'


def _docx(inner: str, extra: dict[str, bytes] | None = None) -> bytes:
    """Build a minimal but real .docx (a ZIP with word/document.xml)."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", "<Types/>")
        archive.writestr(
            "word/document.xml",
            f"<w:document {_W_NS}><w:body>{inner}</w:body></w:document>",
        )
        for name, data in (extra or {}).items():
            archive.writestr(name, data)
    return buffer.getvalue()


def _zip_with(name: str, data: bytes) -> bytes:
    info = zipfile.ZipInfo(name)
    info.compress_type = zipfile.ZIP_DEFLATED
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        archive.writestr(info, data)
    return buffer.getvalue()


def _para(*runs: str) -> str:
    return "<w:p>" + "".join(f"<w:r>{run}</w:r>" for run in runs) + "</w:p>"


def test_docx_text_is_extracted_with_paragraphs_and_placeholders() -> None:
    imported = read_template_file(
        _docx(
            _para("<w:t>Скрипт звонка</w:t>")
            + _para(
                "<w:t>Здравствуйте, </w:t>",
                "<w:t>{{ candidate.full_name }}</w:t>",
                "<w:tab/><w:t>!</w:t>",
            )
            + _para("<w:t>Новый</w:t><w:br/><w:t>абзац</w:t>"),
        ),
        "Скрипт звонка.docx",
    )
    assert imported.title == "Скрипт звонка"
    assert imported.extension == ".docx"
    # Paragraphs become line breaks, a tab becomes a space, and the allowlisted
    # variable survives into the template body.
    assert imported.body.splitlines() == [
        "Скрипт звонка",
        "Здравствуйте, {{ candidate.full_name }} !",
        "Новый",
        "абзац",
    ]


def test_docx_markup_never_leaks_into_the_body() -> None:
    """Only <w:t> text is read: no tags, attributes, comments or fields."""
    imported = read_template_file(
        _docx(
            _para(
                "<w:t>Проверка</w:t>",
                '<w:t xml:space="preserve"> </w:t>',
                "<w:t><!-- комментарий --></w:t>",
            )
        ),
        "check.docx",
    )
    assert imported.body.strip() == "Проверка"
    assert "<" not in imported.body and "<!--" not in imported.body


def test_docx_placeholders_are_still_validated() -> None:
    with pytest.raises(TemplateContentError):
        read_template_file(_docx(_para("<w:t>{{ candidate.ssn }}</w:t>")), "bad.docx")


def test_docx_without_the_document_part_is_refused() -> None:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("[Content_Types].xml", "<Types/>")
        archive.writestr("word/styles.xml", "<x/>")
    with pytest.raises(TemplateContentError) as excinfo:
        read_template_file(buffer.getvalue(), "a.docx")
    assert "word/document.xml" in str(excinfo.value)


def test_docx_that_is_not_a_zip_is_refused() -> None:
    with pytest.raises(TemplateContentError) as excinfo:
        read_template_file("просто текст, переименованный в .docx".encode(), "fake.docx")
    assert "ZIP" in str(excinfo.value)


def test_docx_oversized_entry_is_refused_before_inflating() -> None:
    """100 MB of 'A' compresses to ~100 KB: the bomb must be refused."""
    payload = _zip_with("word/document.xml", b"A" * (100 * 1024 * 1024))
    assert len(payload) < MAX_UPLOAD_BYTES, "the bomb must be small on the wire"
    with pytest.raises(TemplateContentError) as excinfo:
        read_template_file(payload, "bomb.docx")
    assert "повреждённым" in str(excinfo.value)


def test_docx_with_an_extreme_compression_ratio_is_refused() -> None:
    """A 3 MB entry in 3 KB: below the per-entry cap, caught by the ratio cap."""
    payload = _zip_with("word/document.xml", b"A" * (3 * 1024 * 1024))
    assert len(payload) < MAX_UPLOAD_BYTES
    with pytest.raises(TemplateContentError) as excinfo:
        read_template_file(payload, "ratio.docx")
    assert "сжатие" in str(excinfo.value)


def test_docx_with_too_many_entries_is_refused() -> None:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for index in range(3000):
            archive.writestr(f"word/part{index}.xml", b"x")
        archive.writestr(
            "word/document.xml",
            f"<w:document {_W_NS}><w:body>{_para('<w:t>ok</w:t>')}</w:body></w:document>",
        )
    with pytest.raises(TemplateContentError) as excinfo:
        read_template_file(buffer.getvalue(), "many.docx")
    assert "слишком много частей" in str(excinfo.value)


def test_docx_with_a_doctype_is_refused() -> None:
    """A DTD is the shape of an XXE payload; ElementTree must never see it."""
    payload = _docx(
        "<!DOCTYPE r [<!ENTITY x SYSTEM 'file:///etc/passwd'>]>" + _para("<w:t>&x;</w:t>")
    )
    with pytest.raises(TemplateContentError) as excinfo:
        read_template_file(payload, "xxe.docx")
    assert "сущности" in str(excinfo.value)


def test_docx_whose_member_names_look_like_paths_are_never_followed() -> None:
    """Nothing is written to disk, so a traversal name is simply ignored."""
    imported = read_template_file(
        _docx(_para("<w:t>ok</w:t>"), extra={"../../../../etc/passwd": b"x"}),
        "../../evil.docx",
    )
    assert imported.body.strip() == "ok"
    assert "root:" not in imported.body


def test_docx_without_text_is_refused() -> None:
    with pytest.raises(TemplateContentError) as excinfo:
        read_template_file(_docx("<w:p/>"), "empty.docx")
    assert "нет текста" in str(excinfo.value)


def test_docx_oversized_upload_is_refused_by_the_unchanged_cap() -> None:
    """MAX_UPLOAD_BYTES was not raised to make room for .docx."""
    assert MAX_UPLOAD_BYTES == 512 * 1024
    with pytest.raises(TemplateContentError) as excinfo:
        read_template_file(b"A" * (MAX_UPLOAD_BYTES + 1), "big.docx")
    assert "КБ" in str(excinfo.value)


def test_pdf_is_refused_with_an_actionable_instruction() -> None:
    with pytest.raises(TemplateContentError) as excinfo:
        read_template_file(b"%PDF-1.7\n\x0c binary", "offer.pdf")
    message = str(excinfo.value)
    assert ".docx" in message and ".txt" in message
    assert "сохраните" in message


def test_docx_is_listed_in_the_supported_formats() -> None:
    assert ".docx" in SUPPORTED_EXTENSIONS
    # The hint the UI shows must name the formats the server accepts.
    assert ".docx" in SUPPORTED_HINT


def test_docx_import_creates_a_draft_over_http(client: TestClient, db_session: Session) -> None:
    admin = make_user(db_session, username="imp-docx", role=UserRole.ADMIN)
    response = _upload(
        client,
        _auth(client, admin.username),
        content=_docx(_para("<w:t>Памятка HR</w:t>") + _para("<w:t>Пункт один</w:t>")),
        filename="Памятка HR.docx",
        kind="memo",
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["name"] == "Памятка HR"
    assert body["versions"][0]["state"] == "draft"
    assert body["versions"][0]["body"].splitlines() == ["Памятка HR", "Пункт один"]


def test_pdf_upload_over_http_is_422_and_says_what_to_do(
    client: TestClient, db_session: Session
) -> None:
    admin = make_user(db_session, username="imp-pdf", role=UserRole.ADMIN)
    headers = _auth(client, admin.username)
    response = _upload(
        client, headers, content=b"%PDF-1.7 binary", filename="offer.pdf", kind="offer"
    )
    assert response.status_code == 422
    assert ".docx" in response.json()["detail"]


def test_docx_import_still_audits_format_and_never_content(
    client: TestClient, db_session: Session
) -> None:
    admin = make_user(db_session, username="imp-docx-audit", role=UserRole.ADMIN)
    response = _upload(
        client,
        _auth(client, admin.username),
        content=_docx(_para("<w:t>СЕКРЕТНЫЙ ТЕКСТ КАНДИДАТА</w:t>")),
        filename="Скрипт.docx",
        kind="script",
    )
    template_id = response.json()["id"]
    rows = db_session.query(AuditEvent).filter(AuditEvent.actor_user_id == admin.id).all()
    entry = next(row for row in rows if template_id in (row.details or ""))
    assert "import=.docx" in (entry.details or "")
    assert "СЕКРЕТНЫЙ" not in (entry.details or "")


def test_docx_import_requires_the_manage_right(client: TestClient, db_session: Session) -> None:
    make_user(db_session, username="imp-docx-hr", role=UserRole.HR)
    assert (
        _upload(
            client,
            _auth(client, "imp-docx-hr"),
            content=_docx(_para("<w:t>text</w:t>")),
            filename="a.docx",
            kind="script",
        ).status_code
        == 403
    )
