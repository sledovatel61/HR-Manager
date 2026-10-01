"""Migration pipeline tests (integration, real PostgreSQL).

The migration chain must be repeatable and reversible: ``alembic upgrade head``
followed by ``alembic downgrade base`` and a final ``upgrade head`` must leave
the database in the expected state (alembic_version at the head revision and
the identity/security tables present). These tests never touch the
SQLite-only unit environment.
"""

import os
import subprocess
import sys
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text

BACKEND_DIR = Path(__file__).resolve().parents[1]
RUN_INTEGRATION = os.environ.get("TEST_DATABASE_URL") is not None
# The head of the migration chain. Must equal `alembic heads`; a dedicated
# non-integration test below fails the moment a migration is added without
# bumping this, so the drift is caught in the fast job rather than only in
# «Backend integration tests (PostgreSQL)».
HEAD_REVISION = "0018"
EXPECTED_TABLES = {
    "users",
    "user_sessions",
    "audit_log",
    "candidates",
    "candidate_interactions",
    "candidate_transfers",
    "events",
    "event_history",
    "analytics_facts",
    "candidate_terminations",
    "notifications",
    "reminders",
    "notification_preferences",
    "notification_outbox",
    "notification_delivery_attempts",
    "worker_heartbeat",
    "access_grants",
    "telegram_links",
    "telegram_link_tokens",
    "telegram_start_events",
    "telegram_poll_state",
    "user_emails",
    "candidate_telegram_links",
    "candidate_telegram_link_tokens",
    "candidate_channel_consents",
    "candidate_email_confirm_tokens",
    "candidate_message_requests",
    "bootstrap_exchanges",
    "bootstrap_tickets",
    "document_templates",
    "document_template_versions",
    "candidate_document_generations",
    "schedule_entries",
    "schedule_imports",
    "schedule_import_rows",
}


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
            assert version == HEAD_REVISION

            tables = {
                row[0]
                for row in connection.execute(
                    text(
                        "SELECT table_name FROM information_schema.tables "
                        "WHERE table_schema = 'public'"
                    )
                )
            }
            assert tables >= EXPECTED_TABLES

            # The candidates migration must link audit events to candidates.
            audit_columns = {
                row[0]
                for row in connection.execute(
                    text(
                        "SELECT column_name FROM information_schema.columns "
                        "WHERE table_name = 'audit_log'"
                    )
                )
            }
            assert "candidate_id" in audit_columns

            # gen_random_uuid() must work (via pgcrypto or the PG13+ built-in).
            generated = connection.execute(text("SELECT gen_random_uuid()")).scalar_one()
            assert generated is not None
    finally:
        engine.dispose()


@pytest.mark.integration
@pytest.mark.skipif(not RUN_INTEGRATION, reason="TEST_DATABASE_URL is not set")
def test_migrations_are_idempotent() -> None:
    """Applying the migrations twice in a row must succeed (IF NOT EXISTS)."""
    url = os.environ["TEST_DATABASE_URL"]

    _run_alembic("upgrade", "head", url=url)
    _run_alembic("upgrade", "head", url=url)

    # Downgrading step by step and re-upgrading must also succeed.
    _run_alembic("downgrade", "-1", url=url)
    _run_alembic("upgrade", "head", url=url)

    engine = create_engine(url)
    try:
        with engine.connect() as connection:
            version = connection.execute(
                text("SELECT version_num FROM alembic_version")
            ).scalar_one()
            assert version == HEAD_REVISION
    finally:
        engine.dispose()


def test_head_revision_matches_the_migration_chain() -> None:
    """``HEAD_REVISION`` must equal ``alembic heads`` — in the *fast* job too.

    Without this, adding a migration without bumping the constant only fails
    in «Backend integration tests (PostgreSQL)», which most contributors never
    run locally; the chain and the test then disagree silently until release.
    Reading the chain directly needs no database, so this runs everywhere.
    """
    result = subprocess.run(
        [sys.executable, "-m", "alembic", "heads"],
        cwd=BACKEND_DIR,
        env={**os.environ, "DATABASE_URL": "sqlite://", "APP_ENV": "test"},
        check=True,
        capture_output=True,
        text=True,
    )
    heads = {
        line.split()[0]
        for line in result.stdout.splitlines()
        if line.strip() and not line.startswith(" ")
    }
    # Exactly one head: a branched chain would make «upgrade head» ambiguous.
    assert heads == {HEAD_REVISION}, (
        f"HEAD_REVISION={HEAD_REVISION!r} does not match `alembic heads`={sorted(heads)}. "
        "Bump HEAD_REVISION in tests/test_migrations.py."
    )
