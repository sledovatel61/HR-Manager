"""Exercise migration 0021 against real SQLite DDL and optional PostgreSQL."""

from __future__ import annotations

import importlib.util
import os
from datetime import UTC, date, datetime, time
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy.engine import Connection

BACKEND_DIR = Path(__file__).resolve().parents[1]
MIGRATION_PATH = (
    BACKEND_DIR / "alembic" / "versions" / "0021_schedule_import_sync_and_assignment.py"
)


def _uuid_key(value: object) -> str:
    return str(value).replace("-", "").lower()


def _migration_module() -> Any:
    spec = importlib.util.spec_from_file_location("schedule_sync_migration_0021", MIGRATION_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _exercise_upgrade_and_downgrade(connection: Connection) -> None:
    """Apply the migration to a minimal, populated 0020-shaped schema."""

    metadata = sa.MetaData()
    users = sa.Table(
        "users",
        metadata,
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("username", sa.String(64), nullable=False),
    )
    candidates = sa.Table(
        "candidates",
        metadata,
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("full_name", sa.String(200), nullable=False),
        sa.Column("full_name_normalized", sa.String(200), nullable=False),
        sa.Column("owner_user_id", sa.Uuid(), nullable=False),
        sa.Column("start_date", sa.Date(), nullable=True),
        sa.Column("start_time", sa.Time(), nullable=True),
        sa.Column("start_organization", sa.String(120), nullable=True),
        sa.Column("start_department", sa.String(120), nullable=True),
        sa.Column("position", sa.String(200), nullable=False),
        sa.Column("shift", sa.String(32), nullable=True),
        sa.Column("start_comment", sa.String(300), nullable=True),
        sa.Column("phone", sa.String(32), nullable=True),
        sa.Column("phone_normalized", sa.String(32), nullable=True),
        sa.ForeignKeyConstraint(
            ["owner_user_id"], ["users.id"], name="fk_candidates_owner", ondelete="RESTRICT"
        ),
    )
    schedule_entries = sa.Table(
        "schedule_entries",
        metadata,
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("entry_date", sa.Date(), nullable=False),
        sa.Column("time_from", sa.Time(), nullable=True),
        sa.Column("time_to", sa.Time(), nullable=True),
        sa.Column("organization", sa.String(120), nullable=True),
        sa.Column("department", sa.String(120), nullable=True),
        sa.Column("comment", sa.String(300), nullable=True),
    )
    imports = sa.Table(
        "schedule_imports",
        metadata,
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("file_name", sa.String(255), nullable=False),
        sa.Column("file_sha256", sa.String(64), nullable=False),
        sa.Column("sheet_title", sa.String(120), nullable=True),
        sa.Column("rows_total", sa.Integer(), nullable=False),
        sa.Column("created_candidates", sa.Integer(), nullable=False),
        sa.Column("matched_candidates", sa.Integer(), nullable=False),
        sa.Column("updated_candidates", sa.Integer(), nullable=False),
        sa.Column("service_entries", sa.Integer(), nullable=False),
        sa.Column("skipped_rows", sa.Integer(), nullable=False),
        sa.Column("error_rows", sa.Integer(), nullable=False),
        sa.Column("created_by_user_id", sa.Uuid(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["created_by_user_id"], ["users.id"], name="fk_schedule_imports_created_by"
        ),
    )
    import_rows = sa.Table(
        "schedule_import_rows",
        metadata,
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("import_id", sa.Uuid(), nullable=False),
        sa.Column("row_key", sa.String(64), nullable=False),
        sa.Column("sheet_row", sa.Integer(), nullable=False),
        sa.Column("result", sa.String(16), nullable=False),
        sa.Column("candidate_id", sa.Uuid(), nullable=True),
        sa.Column("entry_id", sa.Uuid(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["import_id"], ["schedule_imports.id"], name="fk_schedule_import_rows_import"
        ),
        sa.ForeignKeyConstraint(
            ["candidate_id"], ["candidates.id"], name="fk_schedule_import_rows_candidate"
        ),
        sa.ForeignKeyConstraint(
            ["entry_id"], ["schedule_entries.id"], name="fk_schedule_import_rows_entry"
        ),
        sa.UniqueConstraint("import_id", "row_key", name="uq_schedule_import_rows_key"),
    )
    metadata.create_all(connection)

    user_id = uuid4()
    candidate_id = uuid4()
    entry_id = uuid4()
    old_import_id = uuid4()
    latest_import_id = uuid4()
    old_row_id = uuid4()
    person_row_id = uuid4()
    service_row_id = uuid4()
    first_time = datetime(2026, 9, 30, 8, 0, tzinfo=UTC)
    latest_time = datetime(2026, 10, 1, 8, 0, tzinfo=UTC)
    connection.execute(users.insert(), {"id": user_id, "username": "hr"})
    connection.execute(
        candidates.insert(),
        {
            "id": candidate_id,
            "full_name": "Иванова Мария",
            "full_name_normalized": "иванова мария",
            "owner_user_id": user_id,
            "start_date": date(2026, 10, 3),
            "start_time": time(9, 30),
            "start_organization": "ООО Ромашка",
            "start_department": "Склад",
            "position": "Кладовщик",
            "shift": "1 смена",
            "start_comment": "Позвонить утром",
            "phone": "+79000000000",
            "phone_normalized": "+79000000000",
        },
    )
    connection.execute(
        schedule_entries.insert(),
        {
            "id": entry_id,
            "title": "Служебная запись",
            "entry_date": date(2026, 10, 3),
            "time_from": time(14, 0),
            "time_to": time(15, 0),
            "organization": "ООО Ромашка",
            "department": "Склад",
            "comment": "Проверка помещения",
        },
    )
    connection.execute(
        imports.insert(),
        [
            {
                "id": old_import_id,
                "file_name": "old.xlsx",
                "file_sha256": "a" * 64,
                "sheet_title": "График",
                "rows_total": 1,
                "created_candidates": 1,
                "matched_candidates": 0,
                "updated_candidates": 0,
                "service_entries": 0,
                "skipped_rows": 0,
                "error_rows": 0,
                "created_by_user_id": user_id,
                "created_at": first_time,
            },
            {
                "id": latest_import_id,
                "file_name": "latest.xlsx",
                "file_sha256": "b" * 64,
                "sheet_title": "График",
                "rows_total": 2,
                "created_candidates": 1,
                "matched_candidates": 0,
                "updated_candidates": 0,
                "service_entries": 1,
                "skipped_rows": 0,
                "error_rows": 0,
                "created_by_user_id": user_id,
                "created_at": latest_time,
            },
        ],
    )
    connection.execute(
        import_rows.insert(),
        [
            {
                "id": old_row_id,
                "import_id": old_import_id,
                "row_key": "old-person-row",
                "sheet_row": 5,
                "result": "created",
                "candidate_id": candidate_id,
                "entry_id": None,
                "created_at": first_time,
            },
            {
                "id": person_row_id,
                "import_id": latest_import_id,
                "row_key": "latest-person-row",
                "sheet_row": 6,
                "result": "matched",
                "candidate_id": candidate_id,
                "entry_id": None,
                "created_at": latest_time,
            },
            {
                "id": service_row_id,
                "import_id": latest_import_id,
                "row_key": "latest-service-row",
                "sheet_row": 7,
                "result": "service",
                "candidate_id": None,
                "entry_id": entry_id,
                "created_at": latest_time,
            },
        ],
    )

    migration = _migration_module()
    context = MigrationContext.configure(connection)
    with Operations.context(context):
        migration.upgrade()

        candidate_columns = {
            column["name"]: column for column in sa.inspect(connection).get_columns("candidates")
        }
        assert candidate_columns["owner_user_id"]["nullable"] is True

        upgraded_rows = sa.Table("schedule_import_rows", sa.MetaData(), autoload_with=connection)
        required_columns = {
            "source_identity",
            "row_order",
            "row_type",
            "is_active",
            "source_values",
            "full_name",
            "entry_date",
            "time_from",
            "time_to",
            "owner_user_id",
            "warnings",
        }
        assert required_columns <= set(upgraded_rows.c.keys())
        row_values = {
            _uuid_key(row["id"]): row
            for row in connection.execute(sa.select(upgraded_rows)).mappings().all()
        }
        old_person = row_values[_uuid_key(old_row_id)]
        current_person = row_values[_uuid_key(person_row_id)]
        current_service = row_values[_uuid_key(service_row_id)]
        assert old_person["is_active"] is False
        assert current_person["is_active"] is True
        assert current_service["is_active"] is True
        assert current_person["source_identity"] == "latest-person-row"
        assert current_person["row_order"] == 6
        assert current_person["row_type"] == "person"
        assert current_person["full_name"] == "Иванова Мария"
        assert current_person["entry_date"] == date(2026, 10, 3)
        assert current_person["time_from"] == time(9, 30)
        assert _uuid_key(current_person["owner_user_id"]) == _uuid_key(user_id)
        assert current_person["source_values"] == []
        assert current_person["warnings"] == []
        assert current_service["row_type"] == "service"
        assert current_service["full_name"] == "Служебная запись"
        assert current_service["time_to"] == time(15, 0)

        upgraded_imports = sa.Table("schedule_imports", sa.MetaData(), autoload_with=connection)
        import_values = {
            _uuid_key(row["id"]): row
            for row in connection.execute(sa.select(upgraded_imports)).mappings().all()
        }
        assert import_values[_uuid_key(old_import_id)]["source_headers"] == []
        assert import_values[_uuid_key(old_import_id)]["source_columns"] == {}
        assert import_values[_uuid_key(old_import_id)]["active_people"] == 0
        assert import_values[_uuid_key(latest_import_id)]["active_people"] == 1

        # The downgrade guard must be understandable and must not partially
        # remove the new schema when any candidate remains unassigned.
        connection.execute(
            sa.update(candidates).where(candidates.c.id == candidate_id).values(owner_user_id=None)
        )
        with pytest.raises(RuntimeError, match="unassigned candidates exist"):
            migration.downgrade()
        assert "source_identity" in {
            column["name"] for column in sa.inspect(connection).get_columns("schedule_import_rows")
        }
        connection.execute(
            sa.update(candidates)
            .where(candidates.c.id == candidate_id)
            .values(owner_user_id=user_id)
        )
        migration.downgrade()

    downgraded_candidate_columns = {
        column["name"]: column for column in sa.inspect(connection).get_columns("candidates")
    }
    assert downgraded_candidate_columns["owner_user_id"]["nullable"] is False
    assert "source_identity" not in {
        column["name"] for column in sa.inspect(connection).get_columns("schedule_import_rows")
    }
    assert "source_headers" not in {
        column["name"] for column in sa.inspect(connection).get_columns("schedule_imports")
    }
    assert "is_active" not in {
        column["name"] for column in sa.inspect(connection).get_columns("schedule_entries")
    }


def test_schedule_sync_migration_upgrade_and_downgrade_on_sqlite() -> None:
    engine = sa.create_engine("sqlite+pysqlite://")
    try:
        with engine.begin() as connection:
            _exercise_upgrade_and_downgrade(connection)
    finally:
        engine.dispose()


@pytest.mark.integration
@pytest.mark.skipif(
    not os.environ.get("TEST_DATABASE_URL", "").startswith("postgresql"),
    reason="TEST_DATABASE_URL with PostgreSQL is not set",
)
def test_schedule_sync_migration_upgrade_and_downgrade_on_postgres() -> None:
    """Run the same migration/data-backfill assertions in an isolated schema."""

    url = os.environ["TEST_DATABASE_URL"]
    engine = sa.create_engine(url)
    schema = f"migration_0021_{uuid4().hex}"
    try:
        with engine.connect() as connection:
            connection.execute(sa.text(f'CREATE SCHEMA "{schema}"'))
            connection.commit()
            connection.exec_driver_sql(f'SET search_path TO "{schema}"')
            connection.commit()
            try:
                with connection.begin():
                    _exercise_upgrade_and_downgrade(connection)
            finally:
                connection.exec_driver_sql(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
                connection.commit()
    finally:
        engine.dispose()
