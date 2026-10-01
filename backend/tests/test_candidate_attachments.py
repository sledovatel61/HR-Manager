"""Вложения кандидата (.docx/.pdf): контракт API, права, безопасность, аудит.

Юнит-слой на in-memory SQLite (APP_ENV=test). Проверяются: разрешённые
форматы, отказ по сигнатуре и типу, макросы и Zip Slip в DOCX-контейнере,
очистка имени и path traversal, лимиты размера и квоты, права разных ролей,
скачивание без утечки внутреннего хранения, мягкое удаление, аудит без имени
файла и без текста документа, а также отсутствие записи на диск.

Реальные анкеты и персональные данные в тестах не используются: DOCX/PDF
собираются обезличенными фикстурами в ``tests/attachment_fixtures.py``.
"""

from __future__ import annotations

import ast
import asyncio
import io
import tempfile
from collections.abc import Iterator
from pathlib import Path
from typing import Any
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.candidate_attachments import PDF_FORBIDDEN_MARKERS, sanitize_filename
from app.config import Settings
from app.main import create_app
from app.models import AuditAction, AuditEvent, Candidate, CandidateAttachment, User, UserRole
from app.utils import utc_now
from tests.attachment_fixtures import (
    OLE2_PAYLOAD,
    build_docx,
    build_docx_of_size,
    build_docx_with_content_types,
    build_docx_with_entries,
    build_fake_docx,
    build_pdf,
    build_pdf_with,
    build_pdf_with_broken_deflate,
    build_pdf_with_deflate,
    build_pdf_with_name,
    pdf_name_with_hex_escape,
    pdf_name_with_space,
)
from tests.conftest import FIXTURE_PASSWORD, make_candidate, make_user

DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
PDF_MIME = "application/pdf"


def _login(client: TestClient, username: str) -> str:
    response = client.post("/auth/login", json={"username": username, "password": FIXTURE_PASSWORD})
    assert response.status_code == 200, response.text
    return response.json()["csrf_token"]


def _auth(client: TestClient, username: str) -> dict[str, str]:
    """Войти и вернуть CSRF-заголовок (cookie клиента переключается на вход)."""
    return {"X-CSRF-Token": _login(client, username)}


def _upload(
    client: TestClient,
    candidate: Candidate,
    *,
    filename: str | None,
    payload: bytes,
    content_type: str | None = DOCX_MIME,
    headers: dict[str, str] | None = None,
) -> Any:
    files: dict[str, tuple[str | None, bytes, str | None]] = {
        "file": (filename, payload, content_type)
    }
    return client.post(f"/candidates/{candidate.id}/attachments", files=files, headers=headers)


def _settings(**overrides: str) -> Settings:
    return Settings.model_validate(
        {
            "APP_ENV": "test",
            "APP_DEBUG": "false",
            "SECRET_KEY": "unit-test-secret-key",
            "DATABASE_URL": "sqlite+pysqlite://",
            **overrides,
        }
    )


def _app_client(unit_engine: Any, settings: Settings) -> Iterator[TestClient]:
    app = create_app(settings, engine=unit_engine)
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture()
def limited_client(unit_engine: Any) -> Iterator[TestClient]:
    """Клиент с тесными лимитами: 64 КБ на файл, 2 вложения, 100 КБ всего."""
    yield from _app_client(
        unit_engine,
        _settings(
            ATTACHMENTS_MAX_FILE_BYTES=str(64 * 1024),
            ATTACHMENTS_MAX_COUNT="2",
            ATTACHMENTS_MAX_TOTAL_BYTES=str(100 * 1024),
        ),
    )


@pytest.fixture()
def quota_client(unit_engine: Any) -> Iterator[TestClient]:
    """Клиент, где упирается только общий объём (по числу вложений запаса много)."""
    yield from _app_client(
        unit_engine,
        _settings(
            ATTACHMENTS_MAX_FILE_BYTES=str(64 * 1024),
            ATTACHMENTS_MAX_COUNT="10",
            ATTACHMENTS_MAX_TOTAL_BYTES=str(96 * 1024),
        ),
    )


@pytest.fixture()
def disabled_client(unit_engine: Any) -> Iterator[TestClient]:
    """Клиент с выключенными вложениями (ATTACHMENTS_ENABLED=false)."""
    yield from _app_client(unit_engine, _settings(ATTACHMENTS_ENABLED="false"))


def _audit(db: Session, action: AuditAction) -> list[AuditEvent]:
    return list(db.scalars(select(AuditEvent).where(AuditEvent.action == action)))


# --- Разрешённые форматы и базовый сценарий ----------------------------------


def test_docx_upload_list_download_and_audit(client: TestClient, db_session: Session) -> None:
    hr = make_user(db_session, username="hr1", role=UserRole.HR, full_name="Мария Иванова")
    candidate = make_candidate(db_session, owner=hr)
    headers = _auth(client, "hr1")
    payload = build_docx()

    created = _upload(
        client, candidate, filename="Анкета кандидата.docx", payload=payload, headers=headers
    )
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["kind"] == "docx"
    assert body["filename"] == "Анкета кандидата.docx"
    assert body["size_bytes"] == len(payload)
    assert len(body["sha256"]) == 64
    assert body["uploaded_by_username"] == "hr1"
    assert "content" not in body

    listing = client.get(f"/candidates/{candidate.id}/attachments")
    assert listing.status_code == 200
    data = listing.json()
    assert data["total"] == 1
    assert data["total_bytes"] == len(payload)
    assert data["limits"]["max_file_bytes"] > 0
    assert data["items"][0]["filename"] == "Анкета кандидата.docx"
    assert data["items"][0]["uploaded_by_username"] == "hr1"

    download = client.get(f"/candidates/{candidate.id}/attachments/{body['id']}/download")
    assert download.status_code == 200
    assert download.content == payload
    assert download.headers["content-type"].startswith(DOCX_MIME)
    disposition = download.headers["content-disposition"]
    assert disposition.startswith("attachment;")
    # RFC 5987: кириллическое имя передаётся в filename*, а не ломается в ASCII.
    assert "filename*=UTF-8''" in disposition
    assert "%D0%90%D0%BD%D0%BA%D0%B5%D1%82%D0%B0" in disposition

    uploads = _audit(db_session, AuditAction.CANDIDATE_ATTACHMENT_UPLOADED)
    downloads = _audit(db_session, AuditAction.CANDIDATE_ATTACHMENT_DOWNLOADED)
    assert len(uploads) == 1 and len(downloads) == 1
    for event in uploads + downloads:
        assert event.candidate_id == candidate.id
        # Ни имени файла (в нём бывает ФИО), ни текста документа.
        assert "Анкета" not in (event.details or "")
        assert "attachment=" in (event.details or "")


def test_pdf_upload_and_download(client: TestClient, db_session: Session) -> None:
    hr = make_user(db_session, username="hr1", role=UserRole.HR)
    candidate = make_candidate(db_session, owner=hr)
    payload = build_pdf()

    created = _upload(
        client,
        candidate,
        filename="Скан направления.pdf",
        payload=payload,
        content_type=PDF_MIME,
        headers=_auth(client, "hr1"),
    )
    assert created.status_code == 201, created.text
    assert created.json()["kind"] == "pdf"

    download = client.get(f"/candidates/{candidate.id}/attachments/{created.json()['id']}/download")
    assert download.status_code == 200
    assert download.content == payload
    assert download.headers["content-type"].startswith("application/pdf")


# --- Отказы по формату и содержимому -----------------------------------------


def test_unsupported_extension_rejected(client: TestClient, db_session: Session) -> None:
    hr = make_user(db_session, username="hr1", role=UserRole.HR)
    candidate = make_candidate(db_session, owner=hr)

    response = _upload(
        client,
        candidate,
        filename="archive.zip",
        payload=build_docx(),
        content_type="application/zip",
        headers=_auth(client, "hr1"),
    )
    assert response.status_code == 415
    assert ".docx" in response.json()["detail"]


def test_signature_mismatch_rejected(client: TestClient, db_session: Session) -> None:
    hr = make_user(db_session, username="hr1", role=UserRole.HR)
    candidate = make_candidate(db_session, owner=hr)
    headers = _auth(client, "hr1")

    pdf_named_docx = _upload(
        client, candidate, filename="document.docx", payload=build_pdf(), headers=headers
    )
    assert pdf_named_docx.status_code == 415

    garbage_named_pdf = _upload(
        client,
        candidate,
        filename="scan.pdf",
        payload=b"just some bytes, not a pdf",
        content_type=PDF_MIME,
        headers=headers,
    )
    assert garbage_named_pdf.status_code == 415

    zip_that_is_not_docx = _upload(
        client, candidate, filename="empty.docx", payload=build_fake_docx(), headers=headers
    )
    assert zip_that_is_not_docx.status_code == 415


def test_ole2_legacy_doc_rejected(client: TestClient, db_session: Session) -> None:
    hr = make_user(db_session, username="hr1", role=UserRole.HR)
    candidate = make_candidate(db_session, owner=hr)

    response = _upload(
        client, candidate, filename="old.docx", payload=OLE2_PAYLOAD, headers=_auth(client, "hr1")
    )
    assert response.status_code == 415
    assert "макрос" in response.json()["detail"].lower()


def test_dangerous_content_type_rejected(client: TestClient, db_session: Session) -> None:
    hr = make_user(db_session, username="hr1", role=UserRole.HR)
    candidate = make_candidate(db_session, owner=hr)

    response = _upload(
        client,
        candidate,
        filename="page.docx",
        payload=build_docx(),
        content_type="text/html",
        headers=_auth(client, "hr1"),
    )
    assert response.status_code == 415


def test_docx_with_macros_rejected(client: TestClient, db_session: Session) -> None:
    hr = make_user(db_session, username="hr1", role=UserRole.HR)
    candidate = make_candidate(db_session, owner=hr)
    headers = _auth(client, "hr1")

    with_macro_part = _upload(
        client,
        candidate,
        filename="macro.docx",
        payload=build_docx_with_entries({"word/vbaProject.bin": "fake binary macro"}),
        headers=headers,
    )
    assert with_macro_part.status_code == 415
    assert "макрос" in with_macro_part.json()["detail"].lower()

    with_macro_type = _upload(
        client,
        candidate,
        filename="macro2.docx",
        payload=build_docx_with_content_types(
            '<?xml version="1.0"?>\n<Types xmlns="http://schemas.openxmlformats.org/package/'
            '2006/content-types"><Override PartName="/word/document.xml" ContentType='
            '"application/vnd.ms-word.document.macroEnabled.main+xml"/></Types>'
        ),
        headers=headers,
    )
    assert with_macro_type.status_code == 415


def test_docx_zip_slip_entry_rejected(client: TestClient, db_session: Session) -> None:
    hr = make_user(db_session, username="hr1", role=UserRole.HR)
    candidate = make_candidate(db_session, owner=hr)

    response = _upload(
        client,
        candidate,
        filename="slip.docx",
        payload=build_docx_with_entries({"../../evil.xml": "<x/>"}),
        headers=_auth(client, "hr1"),
    )
    assert response.status_code == 415


def test_docx_absolute_path_entry_rejected(client: TestClient, db_session: Session) -> None:
    hr = make_user(db_session, username="hr1", role=UserRole.HR)
    candidate = make_candidate(db_session, owner=hr)

    response = _upload(
        client,
        candidate,
        filename="abs.docx",
        payload=build_docx_with_entries({"/etc/evil.xml": "<x/>"}),
        headers=_auth(client, "hr1"),
    )
    assert response.status_code == 415


def test_pdf_with_javascript_rejected(client: TestClient, db_session: Session) -> None:
    hr = make_user(db_session, username="hr1", role=UserRole.HR)
    candidate = make_candidate(db_session, owner=hr)

    response = _upload(
        client,
        candidate,
        filename="scripted.pdf",
        payload=build_pdf_with("/JavaScript"),
        content_type=PDF_MIME,
        headers=_auth(client, "hr1"),
    )
    assert response.status_code == 415


@pytest.mark.parametrize("marker", [m.decode() for m in PDF_FORBIDDEN_MARKERS])
@pytest.mark.parametrize("obfuscate", ["space", "hex"])
def test_pdf_forbidden_name_survives_obfuscation(
    client: TestClient, db_session: Session, marker: str, obfuscate: str
) -> None:
    """Запрещённое имя ловится и с пробелом внутри, и в записи ``#xx``.

    PDF допускает white-space внутри name-токена и ``#xx``-эскейпы, поэтому
    ``/Java Script`` и ``/Ja#76aScript`` — нормативная запись того же имени
    ``/JavaScript``. Поиск буквальной подстроки такие файлы пропускал.
    """
    hr = make_user(db_session, username="hr1", role=UserRole.HR)
    candidate = make_candidate(db_session, owner=hr)
    name = pdf_name_with_space(marker) if obfuscate == "space" else pdf_name_with_hex_escape(marker)

    response = _upload(
        client,
        candidate,
        filename="obfuscated.pdf",
        payload=build_pdf_with_name(name),
        content_type=PDF_MIME,
        headers=_auth(client, "hr1"),
    )
    assert response.status_code == 415


def test_pdf_forbidden_name_inside_flate_stream_rejected(
    client: TestClient, db_session: Session
) -> None:
    """Конструкция внутри сжатого потока FlateDecode тоже отклоняется.

    В PDF 1.5+ объекты лежат в object streams, сжатых FlateDecode: в сырых
    байтах файла маркера нет, поэтому поток распаковывается перед проверкой.
    """
    hr = make_user(db_session, username="hr1", role=UserRole.HR)
    candidate = make_candidate(db_session, owner=hr)
    inner = b"1 0 obj<</S/JavaScript/JS(app.launchURL('c:/x.exe'))>>endobj\n"

    response = _upload(
        client,
        candidate,
        filename="packed.pdf",
        payload=build_pdf_with_deflate(inner),
        content_type=PDF_MIME,
        headers=_auth(client, "hr1"),
    )
    assert response.status_code == 415


def test_plain_pdf_still_accepted_after_name_normalisation(
    client: TestClient, db_session: Session
) -> None:
    """Контроль ложных срабатываний: обычный корректный PDF загружается.

    Нормализация имён не должна превращаться в отказ всех нормальных файлов.
    """
    hr = make_user(db_session, username="hr1", role=UserRole.HR)
    candidate = make_candidate(db_session, owner=hr)

    response = _upload(
        client,
        candidate,
        filename="Обычный скан.pdf",
        payload=build_pdf(),
        content_type=PDF_MIME,
        headers=_auth(client, "hr1"),
    )
    assert response.status_code == 201


def test_broken_deflate_stream_does_not_break_upload(
    client: TestClient, db_session: Session
) -> None:
    """Битый или обрезанный поток не роняет загрузку в 5xx."""
    hr = make_user(db_session, username="hr1", role=UserRole.HR)
    candidate = make_candidate(db_session, owner=hr)

    broken = _upload(
        client,
        candidate,
        filename="broken-stream.pdf",
        payload=build_pdf_with_broken_deflate(),
        content_type=PDF_MIME,
        headers=_auth(client, "hr1"),
    )
    assert broken.status_code < 500

    truncated = _upload(
        client,
        candidate,
        filename="truncated.pdf",
        payload=build_pdf().replace(b"endstream", b"XXXXXXXXX"),
        content_type=PDF_MIME,
        headers=_auth(client, "hr1"),
    )
    assert truncated.status_code < 500


def test_empty_file_rejected(client: TestClient, db_session: Session) -> None:
    hr = make_user(db_session, username="hr1", role=UserRole.HR)
    candidate = make_candidate(db_session, owner=hr)

    response = _upload(
        client, candidate, filename="empty.docx", payload=b"", headers=_auth(client, "hr1")
    )
    assert response.status_code == 422


# --- Имя файла ---------------------------------------------------------------


def test_filename_is_sanitized_and_cannot_set_a_path(
    client: TestClient, db_session: Session
) -> None:
    hr = make_user(db_session, username="hr1", role=UserRole.HR)
    candidate = make_candidate(db_session, owner=hr)
    headers = _auth(client, "hr1")

    traversal = _upload(
        client,
        candidate,
        filename="../../../../etc/passwd.docx",
        payload=build_docx(),
        headers=headers,
    )
    assert traversal.status_code == 201, traversal.text
    assert traversal.json()["filename"] == "passwd.docx"

    windows_path = _upload(
        client,
        candidate,
        filename="C:\\Users\\hr\\Documents\\Анкета.pdf",
        payload=build_pdf(),
        content_type=PDF_MIME,
        headers=headers,
    )
    assert windows_path.status_code == 201
    assert windows_path.json()["filename"] == "Анкета.pdf"

    # Расширение всегда проставляет сервер по распознанному типу; само имя
    # сохраняется без потерь, поэтому «report.exe» не теряет хвост.
    assert sanitize_filename("что-то странное.exe", "docx") == "что-то странное.exe.docx"
    assert sanitize_filename("   ", "pdf") == "вложение.pdf"
    assert sanitize_filename("CON.docx", "docx") == "вложение.docx"
    assert sanitize_filename(None, "docx") == "вложение.docx"
    assert sanitize_filename("../../x.docx", "docx") == "x.docx"
    assert "/" not in sanitize_filename("a/b/c.docx", "docx")
    assert "\\" not in sanitize_filename("a\\b\\c.docx", "docx")
    assert len(sanitize_filename("а" * 400, "docx")) <= 120


def test_download_does_not_leak_internal_storage(client: TestClient, db_session: Session) -> None:
    hr = make_user(db_session, username="hr1", role=UserRole.HR)
    candidate = make_candidate(db_session, owner=hr)
    # Payload строится один раз: build_docx() недетерминированна — zipfile
    # пишет в локальный заголовок DOS-время с разрешением 2 секунды, поэтому
    # два вызова через границу секунды дают разные байты.
    payload = build_docx()
    created = _upload(
        client,
        candidate,
        filename="Анкета.docx",
        payload=payload,
        headers=_auth(client, "hr1"),
    )
    attachment_id = created.json()["id"]

    response = client.get(f"/candidates/{candidate.id}/attachments/{attachment_id}/download")
    assert response.status_code == 200
    disposition = response.headers.get("content-disposition", "")
    for secret in ("/var/", "/home/", "postgresql", "sqlite", "candidate_attachments", "BYTEA"):
        assert secret not in response.text
        assert secret not in disposition
    # Тело — это ровно байты документа, без обёрток и префиксов.
    assert response.content == payload


# --- Лимиты и квоты ----------------------------------------------------------


def test_oversized_file_rejected(limited_client: TestClient, db_session: Session) -> None:
    hr = make_user(db_session, username="hr1", role=UserRole.HR)
    candidate = make_candidate(db_session, owner=hr)

    response = _upload(
        limited_client,
        candidate,
        filename="big.docx",
        payload=build_docx_of_size(70 * 1024),
        headers=_auth(limited_client, "hr1"),
    )
    assert response.status_code == 413
    assert "64 КБ" not in response.json()["detail"] or "больше" in response.json()["detail"]


def test_count_quota_rejected(limited_client: TestClient, db_session: Session) -> None:
    hr = make_user(db_session, username="hr1", role=UserRole.HR)
    candidate = make_candidate(db_session, owner=hr)
    headers = _auth(limited_client, "hr1")
    payload = build_docx("а" * 2000)

    first = _upload(limited_client, candidate, filename="1.docx", payload=payload, headers=headers)
    second = _upload(limited_client, candidate, filename="2.docx", payload=payload, headers=headers)
    assert first.status_code == 201 and second.status_code == 201

    third = _upload(limited_client, candidate, filename="3.docx", payload=payload, headers=headers)
    assert third.status_code == 413
    assert "предел" in third.json()["detail"].lower()

    listing = limited_client.get(f"/candidates/{candidate.id}/attachments").json()
    assert listing["total"] == 2
    assert listing["limits"]["max_count"] == 2


def test_total_bytes_quota_rejected(quota_client: TestClient, db_session: Session) -> None:
    hr = make_user(db_session, username="hr1", role=UserRole.HR)
    candidate = make_candidate(db_session, owner=hr)
    headers = _auth(quota_client, "hr1")
    # Каждый файл проходит по лимиту файла, но вдвоём не проходят по объёму.
    payload = build_docx_of_size(55 * 1024)

    first = _upload(quota_client, candidate, filename="a.docx", payload=payload, headers=headers)
    assert first.status_code == 201, first.text
    second = _upload(quota_client, candidate, filename="b.docx", payload=payload, headers=headers)
    assert second.status_code == 413
    assert "объём" in second.json()["detail"]


def test_duplicate_name_is_a_conflict(client: TestClient, db_session: Session) -> None:
    hr = make_user(db_session, username="hr1", role=UserRole.HR)
    candidate = make_candidate(db_session, owner=hr)
    headers = _auth(client, "hr1")

    first = _upload(
        client, candidate, filename="Анкета.docx", payload=build_docx(), headers=headers
    )
    assert first.status_code == 201
    second = _upload(
        client,
        candidate,
        filename="Анкета.docx",
        payload=build_docx("другой текст"),
        headers=headers,
    )
    assert second.status_code == 409
    assert "уже загружен" in second.json()["detail"]

    # После удаления имя освобождается.
    removed = client.delete(
        f"/candidates/{candidate.id}/attachments/{first.json()['id']}", headers=headers
    )
    assert removed.status_code == 200
    third = _upload(
        client, candidate, filename="Анкета.docx", payload=build_docx(), headers=headers
    )
    assert third.status_code == 201


def test_attachments_disabled_by_config(disabled_client: TestClient, db_session: Session) -> None:
    hr = make_user(db_session, username="hr1", role=UserRole.HR)
    candidate = make_candidate(db_session, owner=hr)

    response = _upload(
        disabled_client,
        candidate,
        filename="Анкета.docx",
        payload=build_docx(),
        headers=_auth(disabled_client, "hr1"),
    )
    assert response.status_code == 403
    assert "отключены" in response.json()["detail"]


# --- Права доступа -----------------------------------------------------------


def test_foreign_candidate_is_hidden_from_other_hr(client: TestClient, db_session: Session) -> None:
    owner = make_user(db_session, username="hr1", role=UserRole.HR)
    make_user(db_session, username="hr2", role=UserRole.HR)
    candidate = make_candidate(db_session, owner=owner)
    created = _upload(
        client,
        candidate,
        filename="Анкета.docx",
        payload=build_docx(),
        headers=_auth(client, "hr1"),
    )
    attachment_id = created.json()["id"]
    headers = _auth(client, "hr2")

    assert client.get(f"/candidates/{candidate.id}/attachments").status_code == 404
    assert (
        client.get(f"/candidates/{candidate.id}/attachments/{attachment_id}/download").status_code
        == 404
    )
    assert (
        _upload(
            client, candidate, filename="Чужое.docx", payload=build_docx(), headers=headers
        ).status_code
        == 404
    )
    assert (
        client.delete(
            f"/candidates/{candidate.id}/attachments/{attachment_id}", headers=headers
        ).status_code
        == 404
    )


def test_manager_and_admin_see_the_attachments(client: TestClient, db_session: Session) -> None:
    owner = make_user(db_session, username="hr1", role=UserRole.HR)
    make_user(db_session, username="mgr", role=UserRole.MANAGER)
    make_user(db_session, username="adm", role=UserRole.ADMIN)
    candidate = make_candidate(db_session, owner=owner)

    manager_headers = _auth(client, "mgr")
    uploaded = _upload(
        client, candidate, filename="Анкета.docx", payload=build_docx(), headers=manager_headers
    )
    assert uploaded.status_code == 201, uploaded.text
    attachment_id = uploaded.json()["id"]
    assert client.get(f"/candidates/{candidate.id}/attachments").status_code == 200

    # Вход администратора переключает cookie клиента, поэтому проверка идёт
    # отдельным запросом после _auth.
    _auth(client, "adm")
    admin_download = client.get(f"/candidates/{candidate.id}/attachments/{attachment_id}/download")
    assert admin_download.status_code == 200

    manager_headers = _auth(client, "mgr")
    removed = client.delete(
        f"/candidates/{candidate.id}/attachments/{attachment_id}", headers=manager_headers
    )
    assert removed.status_code == 200


def test_unauthenticated_and_csrf(client: TestClient, db_session: Session) -> None:
    hr = make_user(db_session, username="hr1", role=UserRole.HR)
    candidate = make_candidate(db_session, owner=hr)
    created = _upload(
        client,
        candidate,
        filename="Анкета.docx",
        payload=build_docx(),
        headers=_auth(client, "hr1"),
    )
    attachment_id = created.json()["id"]

    # Без сессии (cookie очищены) список и скачивание недоступны.
    client.cookies.clear()
    assert client.get(f"/candidates/{candidate.id}/attachments").status_code == 401
    assert (
        client.get(f"/candidates/{candidate.id}/attachments/{attachment_id}/download").status_code
        == 401
    )
    # Загрузка без CSRF-заголовка запрещена, даже с валидной сессией.
    _login(client, "hr1")
    without_csrf = _upload(client, candidate, filename="Без токена.docx", payload=build_docx())
    assert without_csrf.status_code == 403


def test_inactive_user_loses_access(client: TestClient, db_session: Session) -> None:
    owner = make_user(db_session, username="hr1", role=UserRole.HR)
    candidate = make_candidate(db_session, owner=owner)
    created = _upload(
        client,
        candidate,
        filename="Анкета.docx",
        payload=build_docx(),
        headers=_auth(client, "hr1"),
    )
    attachment_id = created.json()["id"]

    make_user(db_session, username="mgr", role=UserRole.MANAGER)
    _auth(client, "mgr")
    assert client.get(f"/candidates/{candidate.id}/attachments").status_code == 200

    manager = db_session.scalar(select(User).where(User.username == "mgr"))
    assert manager is not None
    manager.is_active = False
    db_session.commit()

    assert client.get(f"/candidates/{candidate.id}/attachments").status_code == 401
    assert (
        client.get(f"/candidates/{candidate.id}/attachments/{attachment_id}/download").status_code
        == 401
    )


# --- Удаление ----------------------------------------------------------------


def test_delete_is_soft_audited_and_hides_the_file(client: TestClient, db_session: Session) -> None:
    hr = make_user(db_session, username="hr1", role=UserRole.HR)
    candidate = make_candidate(db_session, owner=hr)
    headers = _auth(client, "hr1")
    created = _upload(
        client, candidate, filename="Анкета.docx", payload=build_docx(), headers=headers
    )
    attachment_id = created.json()["id"]

    removed = client.delete(
        f"/candidates/{candidate.id}/attachments/{attachment_id}", headers=headers
    )
    assert removed.status_code == 200
    assert removed.json() == {"deleted": True}

    assert client.get(f"/candidates/{candidate.id}/attachments").json()["total"] == 0
    assert (
        client.get(f"/candidates/{candidate.id}/attachments/{attachment_id}/download").status_code
        == 404
    )
    # Повторное удаление не проходит: вложение уже скрыто.
    again = client.delete(
        f"/candidates/{candidate.id}/attachments/{attachment_id}", headers=headers
    )
    assert again.status_code == 404

    row = db_session.get(CandidateAttachment, UUID(attachment_id))
    assert row is not None
    assert row.deleted_at is not None
    assert row.deleted_by_user_id == hr.id
    # Байты не уничтожаются молча — политика проекта (soft delete).
    assert row.size_bytes == len(build_docx())

    events = _audit(db_session, AuditAction.CANDIDATE_ATTACHMENT_DELETED)
    assert len(events) == 1
    assert events[0].candidate_id == candidate.id
    assert "Анкета" not in (events[0].details or "")


def test_concurrent_delete_is_an_explicit_conflict(db_session: Session) -> None:
    """Доменный слой: второе удаление той же строки — 409, а не молчаливый успех."""
    import pytest as _pytest

    from app.candidate_attachments import AttachmentRejected, delete_attachment

    owner = make_user(db_session, username="hr1", role=UserRole.HR)
    other = make_user(db_session, username="hr2", role=UserRole.HR)
    candidate = make_candidate(db_session, owner=owner)
    attachment = CandidateAttachment(
        candidate_id=candidate.id,
        filename="Анкета.docx",
        kind="docx",
        size_bytes=4,
        sha256="0" * 64,
        content=b"data",
        uploaded_by_user_id=owner.id,
        uploaded_at=utc_now(),
    )
    db_session.add(attachment)
    db_session.commit()

    delete_attachment(db_session, candidate=candidate, attachment=attachment, actor=owner)
    with _pytest.raises(AttachmentRejected) as raised:
        delete_attachment(db_session, candidate=candidate, attachment=attachment, actor=other)
    assert raised.value.status_code == 409


def test_deleted_candidate_hides_attachments(client: TestClient, db_session: Session) -> None:
    hr = make_user(db_session, username="hr1", role=UserRole.HR)
    candidate = make_candidate(db_session, owner=hr)
    headers = _auth(client, "hr1")
    created = _upload(
        client, candidate, filename="Анкета.docx", payload=build_docx(), headers=headers
    )

    candidate.deleted_at = utc_now()
    db_session.commit()

    assert client.get(f"/candidates/{candidate.id}/attachments").status_code == 404
    assert (
        client.get(
            f"/candidates/{candidate.id}/attachments/{created.json()['id']}/download"
        ).status_code
        == 404
    )


# --- Хранение и потоковое чтение ---------------------------------------------


def test_upload_never_writes_to_the_filesystem(
    client: TestClient,
    db_session: Session,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Байты уходят в БД: ни в рабочем каталоге, ни в temp не появляется файлов."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))

    hr = make_user(db_session, username="hr1", role=UserRole.HR)
    candidate = make_candidate(db_session, owner=hr)
    # Один вызов фикстуры на тест: см. комментарий в
    # test_download_does_not_leak_internal_storage.
    payload = build_docx()
    created = _upload(
        client,
        candidate,
        filename="Анкета.docx",
        payload=payload,
        headers=_auth(client, "hr1"),
    )
    assert created.status_code == 201

    assert list(tmp_path.iterdir()) == []
    row = db_session.get(CandidateAttachment, UUID(created.json()["id"]))
    assert row is not None
    assert bytes(row.content) == payload


def test_read_upload_limited_stops_reading_at_the_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Чтение потоковое: превышение лимита обрывает его, файл не буферизуется."""
    import app.candidate_attachments as module

    reads: list[int] = []

    class FakeUpload:
        filename = "big.docx"
        content_type = DOCX_MIME

        def __init__(self, payload: bytes) -> None:
            self._stream = io.BytesIO(payload)

        async def read(self, size: int = -1) -> bytes:
            chunk = self._stream.read(size)
            reads.append(len(chunk))
            return chunk

    monkeypatch.setattr(module, "_READ_CHUNK", 16 * 1024)
    payload = build_docx_of_size(256 * 1024)
    with pytest.raises(module.AttachmentRejected) as raised:
        asyncio.run(module.read_upload_limited(FakeUpload(payload), 64 * 1024))  # type: ignore[arg-type]
    assert raised.value.status_code == 413
    # Файл не читается целиком: чтение оборвалось сразу после превышения.
    assert 0 < sum(reads) < len(payload)


def test_config_limits_are_validated() -> None:
    with pytest.raises(ValueError, match="ATTACHMENTS_MAX_FILE_BYTES"):
        _settings(ATTACHMENTS_MAX_FILE_BYTES="1024")
    with pytest.raises(ValueError, match="ATTACHMENTS_MAX_FILE_BYTES"):
        _settings(ATTACHMENTS_MAX_FILE_BYTES=str(128 * 1024 * 1024))
    with pytest.raises(ValueError, match="ATTACHMENTS_MAX_COUNT"):
        _settings(ATTACHMENTS_MAX_COUNT="0")
    with pytest.raises(ValueError, match="ATTACHMENTS_MAX_TOTAL_BYTES"):
        _settings(
            ATTACHMENTS_MAX_FILE_BYTES=str(1024 * 1024),
            ATTACHMENTS_MAX_TOTAL_BYTES=str(1024),
        )


# --- Страж недетерминированной фикстуры --------------------------------------

#: Фикстуры, которые собирают контейнер заново при каждом вызове: ``zipfile``
#: пишет в локальный заголовок DOS-время с разрешением 2 секунды, поэтому два
#: вызова через границу секунды дают разные байты (расхождение на индексе 10).
_NONDETERMINISTIC_FIXTURES = frozenset(
    {
        "build_docx",
        "build_docx_of_size",
        "build_docx_with_entries",
        "build_docx_with_content_types",
        "build_fake_docx",
    }
)


def _fresh_fixture_calls() -> list[tuple[int, str]]:
    """Сравнения, где один из операндов — свежий вызов недетерминированной фикстуры."""
    source = Path(__file__).read_text(encoding="utf-8")
    found: list[tuple[int, str]] = []
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.Compare):
            continue
        operands = [node.left, *node.comparators]
        for operand in operands:
            if (
                isinstance(operand, ast.Call)
                and isinstance(operand.func, ast.Name)
                and operand.func.id in _NONDETERMINISTIC_FIXTURES
            ):
                found.append((node.lineno, operand.func.id))
    return found


def test_no_test_compares_bytes_against_a_fresh_fixture_call() -> None:
    """Сверка байтов обязана идти с payload, построенным один раз.

    ``build_docx()`` недетерминированна, поэтому ``assert response.content ==
    build_docx()`` падает примерно в каждом третьем полном прогоне — когда два
    вызова фикстуры попадают по разные стороны границы DOS-секунды. В одиночном
    прогоне файла тест успевает внутри одной секунды и флак не виден.
    """
    offenders = _fresh_fixture_calls()
    assert not offenders, (
        "Сравнение со свежим вызовом недетерминированной фикстуры "
        f"(строка, фикстура): {offenders}. Сохраните payload в переменную и "
        "сверяйте с ней."
    )
