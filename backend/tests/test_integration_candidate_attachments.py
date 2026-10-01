"""Интеграционные тесты вложений кандидата на реальном PostgreSQL.

Запуск::

    TEST_DATABASE_URL=postgresql+psycopg://user:pass@host:5432/db \
        pytest -m integration -v

Проверяется то, что существует только в PostgreSQL: схема из миграции
``0019`` (BYTEA-колонка, частичный уникальный индекс, CHECK-ограничения,
триггер неизменяемости), round-trip бинарного DOCX/PDF через API, конфликт
имени на уровне БД и контракт backup/restore — вложения лежат внутри базы,
поэтому попадают в штатный ``pg_dump``.

Порядок действий внутри теста намеренно такой: сначала вся работа с БД, затем
``_release()``, затем обращения к API, и в конце — проверки в БД. Это нужно
локальной PGlite-гарнитуре (один WASM-инстанс PostgreSQL, соединения
обслуживаются последовательно); на реальном PostgreSQL в CI порядок не имеет
значения.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from uuid import UUID

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import Settings
from app.models import AuditAction, AuditEvent, UserRole
from app.routers.auth import reset_login_limiter
from tests.attachment_fixtures import build_docx, build_pdf
from tests.conftest import FIXTURE_PASSWORD, make_candidate, make_user

pytestmark = pytest.mark.integration

DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
PDF_MIME = "application/pdf"


@pytest.fixture(autouse=True)
def _clean_limiter() -> object:
    reset_login_limiter()
    yield
    reset_login_limiter()


def _release(db: Session) -> None:
    """Отдать соединение в пул до обращений к API.

    Для CI (psycopg + PostgreSQL 16) это обычный ``close()`` сессии. Локальная
    PGlite-гарнитура обслуживает соединения последовательно, поэтому открытая
    сессия заблокировала бы handshake соединения, которое нужно API.
    """
    db.close()


#: Локальная гарнитура: PGlite (WASM PostgreSQL) за сокет-мостом и драйвер
#: ``pg8000``. В CI используется ``postgres:16`` + psycopg.
_PGLITE_HARNESS = "pg8000" in os.environ.get("TEST_DATABASE_URL", "")


def _require_sync_db_errors() -> None:
    """Пропустить проверку отказов на уровне БД в локальной PGlite-гарнитуре.

    PGlite отдаёт ``ErrorResponse`` асинхронно: нарушение ограничения
    прилетает не на своём запросе, а на следующем, поэтому ``pytest.raises``
    вокруг одного statement здесь ненадёжен. В CI эти же тесты выполняются на
    реальном PostgreSQL 16 через psycopg, где ошибка синхронна.
    """
    if _PGLITE_HARNESS:
        pytest.skip(
            "PGlite delivers constraint errors asynchronously; "
            "database-level refusals are verified on real PostgreSQL in CI"
        )


def _login(client: TestClient, username: str) -> str:
    response = client.post("/auth/login", json={"username": username, "password": FIXTURE_PASSWORD})
    assert response.status_code == 200, response.text
    return response.json()["csrf_token"]


def _upload(
    client: TestClient, candidate_id: UUID, filename: str, payload: bytes, csrf: str
) -> httpx.Response:
    return client.post(
        f"/candidates/{candidate_id}/attachments",
        files={"file": (filename, payload, DOCX_MIME)},
        headers={"X-CSRF-Token": csrf},
    )


# --- Схема миграции 0019 -----------------------------------------------------


def test_migration_creates_the_attachments_schema(pg_engine: Engine) -> None:
    with pg_engine.connect() as connection:
        columns = {
            row[0]: row[1]
            for row in connection.execute(
                text(
                    "SELECT column_name, data_type FROM information_schema.columns "
                    "WHERE table_name = 'candidate_attachments'"
                )
            )
        }
    assert columns["content"] == "bytea", columns
    assert columns["kind"] == "character varying"
    assert columns["size_bytes"] == "bigint"
    assert columns["uploaded_at"] == "timestamp with time zone"

    with pg_engine.connect() as connection:
        indexes = {
            row[0]: row[1]
            for row in connection.execute(
                text(
                    "SELECT indexname, indexdef FROM pg_indexes "
                    "WHERE tablename = 'candidate_attachments'"
                )
            )
        }
    unique = indexes["uq_candidate_attachments_candidate_filename"]
    assert "UNIQUE" in unique
    assert "deleted_at IS NULL" in unique, unique

    with pg_engine.connect() as connection:
        checks = {
            row[0]
            for row in connection.execute(
                text(
                    "SELECT conname FROM pg_constraint "
                    "WHERE conrelid = 'candidate_attachments'::regclass AND contype = 'c'"
                )
            )
        }
    assert {
        "ck_candidate_attachments_kind_valid",
        "ck_candidate_attachments_size_positive",
        "ck_candidate_attachments_filename_present",
        "ck_candidate_attachments_sha256_length",
    } <= checks, checks

    with pg_engine.connect() as connection:
        triggers = {
            row[0]
            for row in connection.execute(
                text(
                    "SELECT tgname FROM pg_trigger "
                    "WHERE tgrelid = 'candidate_attachments'::regclass AND NOT tgisinternal"
                )
            )
        }
    assert "candidate_attachments_immutable" in triggers


def test_stored_bytes_are_immutable_in_the_database(pg_client: TestClient, pg_db: Session) -> None:
    """Триггер миграции не даёт подменить байты или удалить строку в обход API."""
    _require_sync_db_errors()
    owner = make_user(pg_db, username="hr1", role=UserRole.HR)
    candidate = make_candidate(pg_db, owner=owner)
    payload = build_docx()
    pg_db.execute(
        text(
            "INSERT INTO candidate_attachments (id, candidate_id, filename, kind, size_bytes, "
            "sha256, content, uploaded_by_user_id, uploaded_at, created_at, updated_at) "
            "VALUES (gen_random_uuid(), :candidate, 'Анкета.docx', 'docx', :size, :sha, :content, "
            ":user, now(), now(), now())"
        ),
        {
            "candidate": candidate.id,
            "size": len(payload),
            "sha": "a" * 64,
            "content": payload,
            "user": owner.id,
        },
    )
    pg_db.commit()

    with pytest.raises(IntegrityError):
        pg_db.execute(
            text("UPDATE candidate_attachments SET size_bytes = 1 WHERE filename = 'Анкета.docx'")
        )
    pg_db.rollback()

    with pytest.raises(IntegrityError):
        pg_db.execute(text("DELETE FROM candidate_attachments WHERE filename = 'Анкета.docx'"))
    pg_db.rollback()

    # Мягкое удаление разрешено: это единственный предусмотренный путь.
    pg_db.execute(
        text(
            "UPDATE candidate_attachments SET deleted_at = now(), deleted_by_user_id = :user "
            "WHERE filename = 'Анкета.docx'"
        ),
        {"user": owner.id},
    )
    pg_db.commit()
    assert pg_db.execute(text("SELECT count(*) FROM candidate_attachments")).scalar_one() == 1


def test_check_constraints_reject_bad_rows(pg_client: TestClient, pg_db: Session) -> None:
    _require_sync_db_errors()
    owner = make_user(pg_db, username="hr1", role=UserRole.HR)
    candidate = make_candidate(pg_db, owner=owner)
    candidate_id = candidate.id

    # Запрещённый тип файла.
    with pytest.raises(IntegrityError):
        pg_db.execute(
            text(
                "INSERT INTO candidate_attachments (id, candidate_id, filename, kind, size_bytes, "
                "sha256, content, uploaded_at, created_at, updated_at) "
                "VALUES (gen_random_uuid(), :candidate, 'x.exe', 'exe', 1, :sha, :content, "
                "now(), now(), now())"
            ),
            {"candidate": candidate_id, "sha": "b" * 64, "content": b"x"},
        )
    pg_db.rollback()

    # Нулевой размер.
    with pytest.raises(IntegrityError):
        pg_db.execute(
            text(
                "INSERT INTO candidate_attachments (id, candidate_id, filename, kind, size_bytes, "
                "sha256, content, uploaded_at, created_at, updated_at) "
                "VALUES (gen_random_uuid(), :candidate, 'x.docx', 'docx', 0, :sha, :content, "
                "now(), now(), now())"
            ),
            {"candidate": candidate_id, "sha": "c" * 64, "content": b"x"},
        )
    pg_db.rollback()
    assert pg_db.execute(text("SELECT count(*) FROM candidate_attachments")).scalar_one() == 0


# --- Round-trip через API ----------------------------------------------------


def test_docx_round_trip_on_postgres(pg_client: TestClient, pg_db: Session) -> None:
    hr = make_user(pg_db, username="hr1", role=UserRole.HR, full_name="Мария Иванова")
    candidate = make_candidate(pg_db, owner=hr)
    candidate_id = candidate.id
    payload = build_docx()
    _release(pg_db)

    csrf = _login(pg_client, "hr1")
    created = _upload(pg_client, candidate_id, "Анкета кандидата.docx", payload, csrf)
    assert created.status_code == 201, created.text
    attachment_id = created.json()["id"]

    download = pg_client.get(f"/candidates/{candidate_id}/attachments/{attachment_id}/download")
    assert download.status_code == 200
    assert download.content == payload
    disposition = download.headers["content-disposition"]
    assert disposition.startswith("attachment;")
    assert "filename*=UTF-8''" in disposition
    assert download.headers["content-type"].startswith(DOCX_MIME)

    # Байты в базе — это ровно то, что загрузили (BYTEA без искажений).
    stored = pg_db.execute(
        text("SELECT content FROM candidate_attachments WHERE id = :id"), {"id": attachment_id}
    ).scalar_one()
    assert bytes(stored) == payload

    events = list(
        pg_db.scalars(
            select(AuditEvent).where(
                AuditEvent.action.in_(
                    [
                        AuditAction.CANDIDATE_ATTACHMENT_UPLOADED,
                        AuditAction.CANDIDATE_ATTACHMENT_DOWNLOADED,
                    ]
                )
            )
        )
    )
    assert len(events) == 2
    for event in events:
        assert event.candidate_id == candidate_id
        # Ни имени файла (в нём бывает ФИО), ни текста документа.
        assert "Анкета" not in (event.details or "")


def test_pdf_round_trip_on_postgres(pg_client: TestClient, pg_db: Session) -> None:
    hr = make_user(pg_db, username="hr1", role=UserRole.HR)
    candidate = make_candidate(pg_db, owner=hr)
    candidate_id = candidate.id
    payload = build_pdf()
    _release(pg_db)

    csrf = _login(pg_client, "hr1")
    response = pg_client.post(
        f"/candidates/{candidate_id}/attachments",
        files={"file": ("Скан.pdf", payload, PDF_MIME)},
        headers={"X-CSRF-Token": csrf},
    )
    assert response.status_code == 201, response.text

    download = pg_client.get(
        f"/candidates/{candidate_id}/attachments/{response.json()['id']}/download"
    )
    assert download.status_code == 200
    assert download.content == payload
    assert download.headers["content-type"].startswith(PDF_MIME)


def test_delete_hides_the_attachment_on_postgres(pg_client: TestClient, pg_db: Session) -> None:
    hr = make_user(pg_db, username="hr1", role=UserRole.HR)
    candidate = make_candidate(pg_db, owner=hr)
    candidate_id = candidate.id
    _release(pg_db)

    csrf = _login(pg_client, "hr1")
    created = _upload(pg_client, candidate_id, "Анкета.docx", build_docx(), csrf)
    assert created.status_code == 201
    attachment_id = created.json()["id"]

    removed = pg_client.delete(
        f"/candidates/{candidate_id}/attachments/{attachment_id}", headers={"X-CSRF-Token": csrf}
    )
    assert removed.status_code == 200
    assert pg_client.get(f"/candidates/{candidate_id}/attachments").json()["total"] == 0
    assert (
        pg_client.get(
            f"/candidates/{candidate_id}/attachments/{attachment_id}/download"
        ).status_code
        == 404
    )

    row = pg_db.execute(
        text(
            "SELECT deleted_at IS NOT NULL, deleted_by_user_id IS NOT NULL, octet_length(content) "
            "FROM candidate_attachments WHERE id = :id"
        ),
        {"id": attachment_id},
    ).one()
    # Строка и байты остались (мягкое удаление), отметки удаления проставлены.
    assert row[0] is True and row[1] is True and row[2] > 0
    deleted_events = list(
        pg_db.scalars(
            select(AuditEvent).where(AuditEvent.action == AuditAction.CANDIDATE_ATTACHMENT_DELETED)
        )
    )
    assert len(deleted_events) == 1


def test_duplicate_name_conflict_is_enforced_by_the_index(
    pg_client: TestClient, pg_db: Session
) -> None:
    hr = make_user(pg_db, username="hr1", role=UserRole.HR)
    candidate = make_candidate(pg_db, owner=hr)
    candidate_id = candidate.id
    _release(pg_db)

    csrf = _login(pg_client, "hr1")
    first = _upload(pg_client, candidate_id, "Анкета.docx", build_docx(), csrf)
    assert first.status_code == 201
    second = _upload(pg_client, candidate_id, "Анкета.docx", build_docx(), csrf)
    assert second.status_code == 409

    # Прямая вставка дубля в БД тоже запрещена частичным уникальным индексом
    # (на реальном PostgreSQL; см. _require_sync_db_errors).
    if _PGLITE_HARNESS:
        return
    with pytest.raises(IntegrityError):
        pg_db.execute(
            text(
                "INSERT INTO candidate_attachments (id, candidate_id, filename, kind, size_bytes, "
                "sha256, content, uploaded_at, created_at, updated_at) "
                "VALUES (gen_random_uuid(), :candidate, 'Анкета.docx', 'docx', 1, :sha, "
                ":content, now(), now(), now())"
            ),
            {"candidate": candidate_id, "sha": "d" * 64, "content": b"x"},
        )
    pg_db.rollback()


def test_foreign_candidate_returns_404_on_postgres(pg_client: TestClient, pg_db: Session) -> None:
    owner = make_user(pg_db, username="hr1", role=UserRole.HR)
    make_user(pg_db, username="hr2", role=UserRole.HR)
    candidate = make_candidate(pg_db, owner=owner)
    candidate_id = candidate.id
    _release(pg_db)

    created = _upload(
        pg_client, candidate_id, "Анкета.docx", build_docx(), _login(pg_client, "hr1")
    )
    assert created.status_code == 201
    attachment_id = created.json()["id"]

    _login(pg_client, "hr2")
    assert pg_client.get(f"/candidates/{candidate_id}/attachments").status_code == 404
    download = pg_client.get(f"/candidates/{candidate_id}/attachments/{attachment_id}/download")
    assert download.status_code == 404


# --- Контракт backup/restore -------------------------------------------------


def test_attachments_live_inside_the_database(pg_client: TestClient, pg_db: Session) -> None:
    """Вложения — обычные строки БД, а не файлы в отдельном каталоге.

    Именно это делает штатный ``pg_dump``/``pg_restore`` достаточным для
    резервного копирования вложений: отдельного volume, отдельного retention
    и отдельного пути восстановления не существует.
    """
    # В конфигурации нет и не должно быть каталога хранения вложений.
    names = [name for name in Settings.model_fields if "attachment" in name]
    assert names, "ожидаются настройки лимитов вложений"
    assert not any("dir" in name or "path" in name for name in names), names

    row = pg_db.execute(
        text("SELECT relpersistence FROM pg_class WHERE relname = 'candidate_attachments'")
    ).one()
    # Обычная постоянная таблица: не unlogged (иначе WAL, а значит и backup её
    # не содержат) и не временная.
    assert row.relpersistence == "p", row


def test_attachments_survive_pg_dump(pg_client: TestClient, pg_db: Session) -> None:
    """Реальный ``pg_dump``: байты вложения попадают в дамп целиком.

    Требуются клиентские утилиты PostgreSQL (в CI они ставятся шагом
    «Install PostgreSQL client tools»); без них тест честно пропускается.
    """
    pg_dump = os.environ.get("BACKUP_PGDUMP_BIN") or shutil.which("pg_dump")
    if not pg_dump or not Path(pg_dump).exists():
        pytest.skip("pg_dump is not available in this environment")

    hr = make_user(pg_db, username="hr1", role=UserRole.HR)
    candidate = make_candidate(pg_db, owner=hr)
    candidate_id = candidate.id
    payload = build_docx("уникальный маркер содержимого 8f3a1c")
    _release(pg_db)

    created = _upload(pg_client, candidate_id, "Анкета.docx", payload, _login(pg_client, "hr1"))
    assert created.status_code == 201

    url = make_url(os.environ["TEST_DATABASE_URL"])
    # pg_dump понимает только «чистый» URL без имени драйвера SQLAlchemy.
    plain = url.set(drivername="postgresql")
    with tempfile.TemporaryDirectory() as workdir:
        dump = Path(workdir) / "dump.sql"
        subprocess.run(
            [
                pg_dump,
                "--no-owner",
                "--no-privileges",
                "--table=candidate_attachments",
                "--file",
                str(dump),
                plain.render_as_string(hide_password=False),
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        content = dump.read_text(encoding="utf-8", errors="replace")

    assert "candidate_attachments" in content
    # BYTEA в текстовом дампе — это \\x<hex>: ищем отпечаток наших байтов.
    assert payload.hex() in content.replace("\\x", "")
