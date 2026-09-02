"""Migration pipeline tests (integration, real PostgreSQL).

The migration chain must be repeatable and reversible: `alembic upgrade head`
followed by `alembic downgrade base` and a final `upgrade head` must leave the
database in the expected state (alembic_version at the baseline revision,
pgcrypto extension present). These tests never touch the SQLite-only unit
environment.
"""

import os
import subprocess
import sys
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text

BACKEND_DIR = Path(__file__).resolve().parents[1]
RUN_INTEGRATION = os.environ.get("TEST_DATABASE_URL") is not None


def _run_alembic(*args: str, url: str) -> None:
    """Run the alembic CLI in a subprocess against the given database."""
    subprocess.run(
        [sys.executable, "-m", "alembic", *args],
        cwd=BACKEND_DIR,
        env={**os.environ, "DATABASE_URL": url, "APP_ENV": "test"},
        check=True,
        capture_output=True,
        text=True,
    )


@pytest.mark.integration
@pytest.mark.skipif(not RUN_INTEGRATION, reason="TEST_DATABASE_URL is not set")
def test_alembic_upgrade_downgrade_upgrade_cycle() -> None:
    url = os.environ["TEST_DATABASE_URL"]

    _run_alembic("upgrade", "head", url=url)
    _run_alembic("downgrade", "base", url=url)
    _run_alembic("upgrade", "head", url=url)

    engine = create_engine(url)
    try:
        with engine.connect() as connection:
            version = connection.execute(
                text("SELECT version_num FROM alembic_version")
            ).scalar_one()
            assert version == "0001"

            has_pgcrypto = connection.execute(
                text("SELECT count(*) FROM pg_extension WHERE extname = 'pgcrypto'")
            ).scalar_one()
            assert has_pgcrypto == 1
    finally:
        engine.dispose()


@pytest.mark.integration
@pytest.mark.skipif(not RUN_INTEGRATION, reason="TEST_DATABASE_URL is not set")
def test_baseline_migration_is_idempotent() -> None:
    """Applying the baseline twice in a row must succeed (IF NOT EXISTS)."""
    url = os.environ["TEST_DATABASE_URL"]

    _run_alembic("upgrade", "head", url=url)
    _run_alembic("upgrade", "head", url=url)
