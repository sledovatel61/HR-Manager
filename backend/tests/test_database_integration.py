"""Интеграционные проверки против настоящего PostgreSQL.

Запускаются отдельно при живом PostgreSQL (в CI — сервисный контейнер):

    pytest -m database        # при APP_ENV=test и валидном DATABASE_URL

В отличие от изолированных unit-тестов (SQLite in-memory, см. conftest),
здесь проверяется реальное поведение: подключение, применение Alembic-
миграций и синхронизация схемы с metadata приложения.
"""

import os
import subprocess
import sys
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext

from hr_manager.db.schema import Base

pytestmark = pytest.mark.database

BACKEND_DIR = Path(__file__).resolve().parents[1]

DATABASE_URL = os.environ.get(
    "DATABASE_URL",
    "postgresql+psycopg://hr_manager:hr_manager@127.0.0.1:5432/hr_manager",
)


def _engine() -> sa.Engine:
    engine = sa.create_engine(DATABASE_URL)
    return engine


def test_dialect_is_postgresql() -> None:
    """Health приложения полагается на PostgreSQL — проверяем диалект."""
    engine = _engine()
    try:
        with engine.connect() as conn:
            assert conn.dialect.name == "postgresql"
            version = conn.execute(sa.text("SELECT version()")).scalar_one()
            assert "PostgreSQL" in version
    finally:
        engine.dispose()


def test_alembic_roundtrip_and_schema_sync() -> None:
    """Миграции применяются, откатываются и схема синхронна с metadata.

    Этап 1 не содержит бизнес-сущностей, поэтому реальных таблиц нет —
    проверяется корректность самой Alembic-цепочки и отсутствие расхождений
    между базой и Base.metadata (на будущих этапах тест поймает дрейф схемы).
    """
    engine = _engine()
    try:
        env = dict(os.environ)
        env["DATABASE_URL"] = DATABASE_URL

        def alembic(*args: str) -> None:
            result = subprocess.run(
                # Текущий интерпретатор гарантированно содержит установленный
                # alembic (не полагаемся на наличие скрипта в PATH).
                [sys.executable, "-m", "alembic", *args],
                cwd=BACKEND_DIR,
                env=env,
                capture_output=True,
                text=True,
                check=False,
            )
            assert (
                result.returncode == 0
            ), f"alembic {' '.join(args)} failed:\n{result.stdout}\n{result.stderr}"

        alembic("upgrade", "head")
        inspector = sa.inspect(engine)
        assert "alembic_version" in inspector.get_table_names()

        # Схема в БД не расходится с metadata приложения.
        with engine.connect() as conn:
            context = MigrationContext.configure(conn)
            diff = compare_metadata(context, Base.metadata)
            assert diff == [], f"Схема расходится с metadata: {diff}"

        # Alembic после downgrade base может оставить пустую таблицу
        # alembic_version — важно отсутствие записанной версии, а не таблицы.
        alembic("downgrade", "base")
        if "alembic_version" in sa.inspect(engine).get_table_names():
            with engine.connect() as conn:
                remaining = conn.execute(
                    sa.text("SELECT count(*) FROM alembic_version")
                ).scalar_one()
                assert remaining == 0, "После downgrade base не должно быть версий"

        alembic("upgrade", "head")
        with engine.connect() as conn:
            assert conn.dialect.has_table(conn, "alembic_version")
            current = conn.execute(
                sa.text("SELECT count(*) FROM alembic_version")
            ).scalar_one()
            assert current == 1, "После upgrade head должна быть одна текущая версия"
    finally:
        engine.dispose()
