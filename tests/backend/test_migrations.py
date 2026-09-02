"""Тест конвейера миграций Alembic.

Проверяет, что baseline-ревизия применяется и откатывается на чистой
базе. База — временный файл SQLite: это изолированный unit-тест, что
явно разрешено регламентом (см. tests/backend/README.md). На реальном
PostgreSQL тот же путь запускает smoke-тест Docker Compose в CI.
"""

from pathlib import Path

import sqlalchemy
from alembic import command
from alembic.config import Config

REPO_ROOT = Path(__file__).resolve().parents[2]
ALEMBIC_INI = REPO_ROOT / "backend" / "alembic.ini"


def alembic_config(database_url: str) -> Config:
    config = Config(str(ALEMBIC_INI))
    config.set_main_option("sqlalchemy.url", database_url)
    return config


def test_baseline_migration_upgrades_and_downgrades_cleanly(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    url = f"sqlite+pysqlite:///{tmp_path}/migration-test.db"
    config = alembic_config(url)

    command.upgrade(config, "head")

    engine = sqlalchemy.create_engine(url)
    with engine.connect() as connection:
        version = connection.execute(
            sqlalchemy.text("SELECT version_num FROM alembic_version")
        ).scalar_one()
    assert version == "0001_baseline"

    command.downgrade(config, "base")
    with engine.connect() as connection:
        rows = connection.execute(
            sqlalchemy.text("SELECT COUNT(*) FROM alembic_version")
        ).scalar_one()
    assert rows == 0
