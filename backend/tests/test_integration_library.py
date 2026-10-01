"""«Библиотека HR» on real PostgreSQL: seed idempotency, guards, visibility.

SQLite cannot prove the PostgreSQL-specific parts: the partial unique index
on ``seed_key`` (two concurrent seed runs may not duplicate materials), the
category CHECK constraint, and the phase 16 immutability triggers interacting
with the seed inserts. These tests never fall back to SQLite.
"""

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select, text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.library_seed import seed_count_missing, seed_library, seed_materials
from app.models import DocumentTemplate, DocumentTemplateVersion, UserRole
from tests.conftest import make_user
from tests.test_candidate_messages import _login

pytestmark = pytest.mark.integration


def _headers(client: TestClient, username: str) -> dict[str, str]:
    return {"X-CSRF-Token": _login(client, username)}


def test_pg_seed_idempotent_and_concurrent_safe(
    pg_client: TestClient, pg_engine: Engine, pg_db: Session
) -> None:
    """Idempotency plus a real two-session race, not a pretend one.

    Two sessions run ``seed_library()`` simultaneously over an empty table.
    Depending on interleaving, the loser either blocks on the partial unique
    index on ``seed_key`` and rolls back with IntegrityError (startup logs
    this and retries on the next start), or sees the winner's committed keys
    and skips them. Either way the table must end up with exactly the built-in
    catalog — never duplicates — and a follow-up run must be a no-op.
    """
    admin = make_user(pg_db, username="pg-lib-admin", role=UserRole.ADMIN)
    barrier = Barrier(2)

    def run_seed() -> tuple[str, int]:
        barrier.wait()  # maximize the overlap of the two transactions
        with Session(pg_engine) as session:
            try:
                return ("created", seed_library(session))
            except IntegrityError:
                session.rollback()
                return ("raced", 0)

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(lambda _: run_seed(), range(2)))

    # Whatever the interleaving, exactly one run provisions the catalog and
    # the other either raced away or became a no-op — the index is the hard
    # guarantee.
    assert len(outcomes) == 2
    assert sum(1 for kind, created in outcomes if created == len(seed_materials())) == 1
    assert seed_count_missing(pg_db) == 0

    # A second run — and a manual collision with an existing seed key — must
    # not duplicate anything either.
    assert seed_library(pg_db) == 0
    with pytest.raises(IntegrityError):
        pg_db.execute(
            text(
                "INSERT INTO document_templates (id, kind, scope, name, category,"
                " summary, seed_key, revision, author_id, created_at, updated_at)"
                " VALUES (:id, 'memo', '', 'Дубль', 'memos', '', :key, 1, :author,"
                " now(), now())"
            ),
            {"id": uuid4(), "key": "call_screening_checklist", "author": admin.id},
        )
        pg_db.flush()
    pg_db.rollback()

    count = pg_db.scalar(select(func.count()).select_from(DocumentTemplate))
    assert count == len(seed_materials())


def test_pg_seed_material_visible_to_plain_hr(pg_client: TestClient, pg_db: Session) -> None:
    make_user(pg_db, username="pg-lib-seeder", role=UserRole.ADMIN)
    assert seed_library(pg_db) > 0
    hr = make_user(pg_db, username="pg-lib-hr", role=UserRole.HR)
    response = pg_client.get("/library/materials", headers=_headers(pg_client, hr.username))
    assert response.status_code == 200, response.text
    data = response.json()
    names = [item["name"] for item in data["items"]]
    assert len(names) >= len(seed_materials())
    assert "Вопросник общего собеседования" in names
    assert {item["key"] for item in data["categories"]}


def test_pg_category_check_constraint(pg_client: TestClient, pg_db: Session) -> None:
    admin = make_user(pg_db, username="pg-lib-check", role=UserRole.ADMIN)
    with pytest.raises(IntegrityError):
        pg_db.add(
            DocumentTemplate(
                kind="memo",
                scope="",
                name="Неверная категория",
                category="не_категория",
                summary="",
                author_id=admin.id,
            )
        )
        pg_db.flush()
    pg_db.rollback()


def test_pg_seed_insert_passes_immutability_triggers(pg_client: TestClient, pg_db: Session) -> None:
    """The phase 16 triggers guard UPDATE/DELETE — seed INSERTs must go through."""
    make_user(pg_db, username="pg-lib-triggers", role=UserRole.ADMIN)
    seed_library(pg_db)
    seed_library(pg_db)  # second run touches nothing, still fine
    version = pg_db.scalar(
        select(DocumentTemplateVersion).where(DocumentTemplateVersion.state == "active")
    )
    assert version is not None
    assert version.body.strip()
