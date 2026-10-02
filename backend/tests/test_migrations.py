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
import warnings
from pathlib import Path

import pytest
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, text

BACKEND_DIR = Path(__file__).resolve().parents[1]
RUN_INTEGRATION = os.environ.get("TEST_DATABASE_URL") is not None
# The head of the migration chain. Must equal `alembic heads`; a dedicated
# non-integration test below fails the moment a migration is added without
# bumping this, so the drift is caught in the fast job rather than only in
# «Backend integration tests (PostgreSQL)».
HEAD_REVISION = "0021"
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
    "candidate_attachments",
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
        capture_output=True,
        text=True,
    )
    # Not check=True: a broken chain must fail with Alembic's own reason
    # («Can't locate revision identified by '0018'» while PR #46 is unmerged)
    # instead of a bare CalledProcessError.
    assert result.returncode == 0, (
        "`alembic heads` could not read the chain "
        f"(exit {result.returncode}).\nstdout: {result.stdout.strip()}\n"
        f"stderr: {result.stderr.strip()}"
    )
    head_lines = [
        line for line in result.stdout.splitlines() if line.strip() and not line.startswith(" ")
    ]
    heads = [line.split()[0] for line in head_lines]
    # Exactly one head, counted by LINES and not folded into a set: two files
    # claiming the same revision id print the same name twice, and a set-based
    # check happily reduced that to {'0018'} == {'0018'} while `upgrade head`
    # silently applied only one of the two migrations.
    assert len(head_lines) == 1, (
        f"`alembic heads` returned {len(head_lines)} lines ({heads}): either the chain "
        "branches or two migration files declare the same revision id."
    )
    assert heads == [HEAD_REVISION], (
        f"HEAD_REVISION={HEAD_REVISION!r} does not match `alembic heads`={heads}. "
        "Bump HEAD_REVISION in tests/test_migrations.py."
    )

    _assert_chain_is_linear_and_complete()


def _assert_chain_is_linear_and_complete() -> None:
    """One file per revision, one parent per revision, no duplicate ids.

    Two parallel PRs both adding revision «0018» merge cleanly in git and
    ``mergeable: clean`` in the UI, yet Alembic then warns «Revision 0018 is
    present more than once» and drops one branch from the chain — the missing
    table only surfaces at runtime. Every condition below fails on that state.
    """
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        script = ScriptDirectory.from_config(Config(str(BACKEND_DIR / "alembic.ini")))
        walked = list(script.walk_revisions("base", "heads"))
    duplicates = [
        str(item.message) for item in caught if "present more than once" in str(item.message)
    ]
    assert not duplicates, f"Duplicate revision id in alembic/versions: {duplicates}"

    files = sorted(
        path
        for path in (BACKEND_DIR / "alembic" / "versions").glob("*.py")
        if path.name != "__init__.py"
    )
    revisions = [item.revision for item in walked]
    assert len(revisions) == len(files), (
        f"{len(files)} migration files but {len(revisions)} revisions in the chain "
        f"({sorted(revisions)}): a duplicated revision id drops one file from the walk."
    )
    assert len(set(revisions)) == len(revisions), f"Duplicate revision ids: {revisions}"

    parents = [item.down_revision for item in walked if item.down_revision is not None]
    assert sum(1 for item in walked if item.down_revision is None) == 1, (
        "Exactly one base revision (down_revision is None) is expected."
    )
    assert len(parents) == len(set(parents)), (
        f"The chain branches, a revision is the parent of two others: {sorted(parents)}"
    )
    for item in walked:
        if item.down_revision is not None:
            assert item.down_revision in revisions, (
                f"Revision {item.revision} points at down_revision={item.down_revision!r}, "
                "which is not in the chain."
            )
    # The head is the only revision nobody points at.
    assert set(revisions) - set(parents) == {HEAD_REVISION}
