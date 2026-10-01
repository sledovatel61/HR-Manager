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
HEAD_REVISION = "0019"
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

    The guard also catches the collision two set-based checks used to miss:
    two migration files claiming the *same* revision id. Git merges such PRs
    cleanly (different file names) and ``alembic`` silently resolves the id to
    one file, so the other migrations never run — every table they would have
    created is then missing at runtime. Proven regression: three open PRs each
    carried ``revision = "0018"`` and two of them dropped out of the chain.
    Hence the three structural checks below, each of which fails on that exact
    state:

    * ``alembic heads`` prints exactly ONE line (counted, not folded into a
      set — ``{'0018'} == {'0018'}`` is how the old guard let duplicates by);
    * the walked base→heads chain contains as many revisions as there are
      migration files (a duplicated id collapses to one walk entry);
    * every revision's ``down_revision`` is present in the chain and every
      revision id resolves to a real file.
    """
    result = subprocess.run(
        [sys.executable, "-m", "alembic", "heads"],
        cwd=BACKEND_DIR,
        env={**os.environ, "DATABASE_URL": "sqlite://", "APP_ENV": "test"},
        check=True,
        capture_output=True,
        text=True,
    )
    head_lines = [line.strip() for line in result.stdout.splitlines() if line.strip()]
    assert len(head_lines) == 1, (
        "The migration chain must have exactly one head, found "
        f"{len(head_lines)}: {head_lines}. Two migrations claim the same "
        "revision id — renumber the newer one; do not merge until unique."
    )
    assert head_lines[0].split()[0] == HEAD_REVISION, (
        f"HEAD_REVISION={HEAD_REVISION!r} does not match `alembic heads`="
        f"{head_lines[0]!r}. Bump HEAD_REVISION in tests/test_migrations.py."
    )

    from alembic.config import Config
    from alembic.script import ScriptDirectory

    script = ScriptDirectory.from_config(Config("alembic.ini"))
    walked_ids = [rev.revision for rev in script.walk_revisions("base", "heads")]

    version_files = [
        path.name
        for path in (BACKEND_DIR / "alembic" / "versions").glob("*.py")
        if path.name != "__init__.py"
    ]
    assert len(walked_ids) == len(version_files), (
        f"The base→heads chain walks {len(walked_ids)} revisions, but "
        f"alembic/versions/ holds {len(version_files)} migration files. "
        "Some revision id is duplicated and its file will never run — "
        "renumber the newer migration."
    )

    id_set = set(walked_ids)
    detached = [
        rev.revision
        for rev in script.walk_revisions("base", "heads")
        if rev.down_revision is not None and rev.down_revision not in id_set
    ]
    assert not detached, (
        f"Revisions whose down_revision is missing from the chain: {detached}. "
        "The chain is forked or points at a revision that does not exist; "
        "`alembic upgrade head` cannot reach every file."
    )

    for revision_id in walked_ids:
        entry = script.get_revision(revision_id)
        assert Path(entry.path).is_file(), (
            f"Revision {revision_id!r} does not resolve to a migration file "
            f"on disk: {entry.path!r}."
        )
