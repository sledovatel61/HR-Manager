"""Integration tests: real encrypted backup, restore drill and migration lock.

These tests run against a real PostgreSQL instance (see ``TEST_DATABASE_URL``
and the dedicated ``hr_manager_backup_test`` database) using the REAL
pg_dump/pg_restore binaries (``BACKUP_PGDUMP_BIN``/``BACKUP_RESTORE_BIN``,
defaulting to ``/tmp/pginst/bin`` in the agent sandbox). Nothing here is
mocked: dumps are created, encrypted, decrypted, restored into a separate
database, migrated and health-probed for real. Corruption, weak keys,
unavailable databases and lock contention must all produce safe failures.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

from app.backup import list_backup_files, load_state
from app.backup_runner import (
    EXIT_LOCKED,
    EXIT_OK,
    EXIT_UNHEALTHY,
    BackupOutcome,
    RunnerConfig,
    run_backup,
    run_check,
    run_restore_drill,
)
from app.db import bind_session_factory
from app.models import AuditAction, User, UserRole
from app.security import hash_password
from app.utils import utc_now

pytestmark = pytest.mark.integration


def _resolve_pg_tool(env_var: str, default_path: str, name: str) -> str | None:
    """Locate a PostgreSQL client tool for the tests.

    Priority: explicit env override, the agent-sandbox self-built binary,
    then the tool on PATH (the GitHub runner ships PG 16 client tools).
    """
    explicit = os.environ.get(env_var)
    if explicit:
        return explicit
    if Path(default_path).exists():
        return default_path
    return shutil.which(name)


PGDUMP_BIN = _resolve_pg_tool("BACKUP_PGDUMP_BIN", "/tmp/pginst/bin/pg_dump", "pg_dump")
PGRESTORE_BIN = _resolve_pg_tool("BACKUP_RESTORE_BIN", "/tmp/pginst/bin/pg_restore", "pg_restore")
BACKEND_ROOT = Path(__file__).resolve().parent.parent


def _pgdump() -> str:
    assert PGDUMP_BIN is not None  # guaranteed by the autouse skip fixture
    return PGDUMP_BIN


def _pgrestore() -> str:
    assert PGRESTORE_BIN is not None  # guaranteed by the autouse skip fixture
    return PGRESTORE_BIN


def _require_test_db_url() -> str:
    """The integration database URL, or skip when it is not configured."""
    url = os.environ.get("TEST_DATABASE_URL")
    if not url or not url.startswith("postgresql"):
        pytest.skip("TEST_DATABASE_URL with a PostgreSQL URL is required for integration tests")
    return url


def _backup_test_db_url(source_url: str) -> str:
    return source_url.replace("/hr_manager_test", "/hr_manager_backup_test")


def _admin_url() -> str:
    """Superuser URL able to create/drop databases.

    BACKUP_DRILL_ADMIN_URL wins; otherwise it is derived from
    TEST_DATABASE_URL by pointing at the ``postgres`` maintenance database
    with the same credentials (the Compose/CI PostgreSQL user is superuser).
    """
    explicit = os.environ.get("BACKUP_DRILL_ADMIN_URL")
    if explicit:
        return explicit
    source = os.environ.get("TEST_DATABASE_URL", "")
    if source.startswith("postgresql"):
        # str(URL) masks the password as "***" in SQLAlchemy 2.x — render
        # explicitly without hiding so the admin engine gets real credentials.
        url = make_url(source).set(database="postgres")
        return url.render_as_string(hide_password=False)
    return "postgresql+psycopg://postgres@localhost:5432/postgres"


def _ensure_backup_test_db(admin_url: str, name: str) -> None:
    engine = create_engine(admin_url, isolation_level="AUTOCOMMIT")
    try:
        with engine.connect() as connection:
            exists = connection.execute(
                text("SELECT 1 FROM pg_database WHERE datname = :name"), {"name": name}
            ).first()
            if exists is None:
                # The name is a fixed internal constant, never user input.
                connection.execute(text(f"CREATE DATABASE {name}"))
    finally:
        engine.dispose()


@pytest.fixture(scope="module", autouse=True)
def _require_dump_tools() -> None:
    """Skip when no pg_dump/pg_restore is available.

    The agent sandbox ships self-built tools in /tmp/pginst; the GitHub
    runner image has the PostgreSQL client tools on PATH. Without either,
    the suite skips instead of failing.
    """
    if not (PGDUMP_BIN and PGRESTORE_BIN):
        pytest.skip(
            "pg_dump/pg_restore are not installed "
            "(BACKUP_PGDUMP_BIN/BACKUP_RESTORE_BIN or PATH required "
            "for the backup integration tests)"
        )


@pytest.fixture(scope="module")
def migrated_source() -> str:
    """Apply the Alembic pipeline to the dedicated backup-test database."""
    backup_test_url = _backup_test_db_url(_require_test_db_url())
    _ensure_backup_test_db(_admin_url(), "hr_manager_backup_test")
    env = dict(os.environ)
    env["DATABASE_URL"] = backup_test_url
    env["APP_ENV"] = "test"
    proc = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=BACKEND_ROOT,
        env=env,
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    return backup_test_url


@pytest.fixture()
def cfg(migrated_source: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> RunnerConfig:
    monkeypatch.setenv("LD_LIBRARY_PATH", "/tmp/pginst/lib")
    bind_session_factory(create_engine(migrated_source))
    return RunnerConfig(
        database_url=migrated_source,
        backup_dir=tmp_path / "backups",
        state_file=tmp_path / "state.json",
        pgdump_bin=_pgdump(),
        pgrestore_bin=_pgrestore(),
        drill_admin_url=_admin_url(),
        drill_db_name="hr_manager_drill_test",
        retention_days=7,
        max_age_hours=26,
        min_copies=2,
        min_free_mb=1,
        alembic_dir=BACKEND_ROOT,
        app_health_timeout_s=120,
        app_env="test",
    )


@pytest.fixture()
def keys(monkeypatch: pytest.MonkeyPatch) -> tuple[dict[str, bytes], str]:
    import base64

    key = os.urandom(32)
    key_id = "test-key-1"
    monkeypatch.setenv("BACKUP_KEY_ID", key_id)
    monkeypatch.setenv("BACKUP_ENC_KEY", base64.b64encode(key).decode("ascii"))
    return {key_id: key}, key_id


@pytest.fixture()
def seeded_user(migrated_source: str) -> None:
    """A real user row so the restored database is non-empty and healthable."""
    with Session(create_engine(migrated_source)) as db:
        row = db.execute(
            text("SELECT count(*) FROM users WHERE username = 'backup-seed-admin'")
        ).scalar_one()
        if row == 0:
            db.add(
                User(
                    username="backup-seed-admin",
                    full_name="Backup Seed Admin",
                    role=UserRole.ADMIN,
                    password_hash=hash_password("Str0ng-Pass-2026"),
                    is_active=True,
                    created_at=utc_now(),
                    updated_at=utc_now(),
                )
            )
            db.commit()


def _make_backup(
    cfg: RunnerConfig, keys: tuple[dict[str, bytes], str], request_id: str = "itest-0001"
) -> BackupOutcome:
    key_ring, key_id = keys
    outcome = run_backup(
        cfg,
        actor=None,
        actor_name="test-runner",
        reason="интеграционный тест",
        request_id=request_id,
        keys=key_ring,
        key_id=key_id,
    )
    assert outcome.ok, outcome.message
    return outcome


def _audit_actions(cfg: RunnerConfig) -> list[tuple[str, str]]:
    engine = create_engine(cfg.database_url)
    try:
        with engine.connect() as connection:
            rows = connection.execute(
                text("SELECT action, username FROM audit_log ORDER BY created_at")
            ).all()
            return [(row[0], row[1] or "") for row in rows]
    finally:
        engine.dispose()


# --- real backup ---------------------------------------------------------------


def test_full_backup_encrypts_publishes_and_verifies(
    cfg: RunnerConfig, keys: tuple[dict[str, bytes], str], seeded_user: None
) -> None:
    outcome = _make_backup(cfg, keys)
    assert outcome.exit_code == EXIT_OK
    assert outcome.file is not None
    backup_path = cfg.backup_dir / outcome.file
    assert backup_path.is_file()
    assert (cfg.backup_dir / (outcome.file + ".sha256")).is_file()
    # The published file starts with our encrypted format magic — never a
    # plaintext pg_dump header ("PGDMP").
    with backup_path.open("rb") as handle:
        assert handle.read(8) == b"HRMBCK1\n"
    # No plaintext staging leftovers anywhere in the backup directory.
    leftovers = [
        entry.name
        for entry in cfg.backup_dir.iterdir()
        if entry.name.endswith(".pgdump") and not entry.name.endswith(".enc")
    ]
    assert leftovers == []
    state = load_state(cfg.state_file)
    assert state.last_backup is not None
    assert state.last_backup.status == "ok"
    actions = _audit_actions(cfg)
    assert ("backup_started", "test-runner") in actions
    assert ("backup_succeeded", "test-runner") in actions
    # The audit trail never contains the reason's free text if it had PII —
    # here it is a neutral reason, but no connection string or key leaks.
    with Session(create_engine(cfg.database_url)) as db:
        from sqlalchemy import select

        from app.models import AuditEvent

        details = db.scalars(
            select(AuditEvent.details).where(AuditEvent.action == AuditAction.BACKUP_SUCCEEDED)
        ).all()
    joined = "\n".join(detail for detail in details if detail is not None)
    assert "postgresql+psycopg" not in joined
    assert "BACKUP_ENC_KEY" not in joined


def test_backup_check_deep_passes_on_valid_backup(
    cfg: RunnerConfig, keys: tuple[dict[str, bytes], str], seeded_user: None
) -> None:
    _make_backup(cfg, keys)
    outcome = run_check(cfg, actor=None, actor_name="test-runner", deep=True)
    assert outcome.ok, outcome.message
    assert outcome.exit_code == EXIT_OK
    assert outcome.checksum_ok is True
    assert outcome.decrypt_ok is True


def test_tampered_ciphertext_fails_check_and_drill(
    cfg: RunnerConfig, keys: tuple[dict[str, bytes], str], seeded_user: None
) -> None:
    outcome = _make_backup(cfg, keys)
    assert outcome.file is not None
    backup_path = cfg.backup_dir / outcome.file
    blob = bytearray(backup_path.read_bytes())
    blob[len(blob) // 2] ^= 0xFF
    backup_path.write_bytes(bytes(blob))

    check = run_check(cfg, actor=None, actor_name="test-runner", deep=True)
    assert not check.ok
    assert check.exit_code == EXIT_UNHEALTHY
    assert check.checksum_ok is False

    drill = run_restore_drill(cfg, actor=None, actor_name="test-runner")
    assert not drill.ok
    state = load_state(cfg.state_file)
    assert state.last_drill is not None and state.last_drill["ok"] is False
    actions = [action for action, _ in _audit_actions(cfg)]
    assert "backup_restore_drill_failed" in actions


def test_retention_deletes_only_old_and_keeps_minimum(
    cfg: RunnerConfig, keys: tuple[dict[str, bytes], str], seeded_user: None
) -> None:
    import os as os_module
    from datetime import timedelta

    # Plant three old backups with old mtimes.
    now = utc_now()
    cfg.backup_dir.mkdir(parents=True, exist_ok=True)
    for index in range(3):
        stamp_time = now - timedelta(days=20)
        name = f"hr-manager-{stamp_time.strftime('%Y%m%dT%H%M%SZ')}-{index:08x}.pgdump.enc"
        path = cfg.backup_dir / name
        path.write_bytes(b"fake-old-backup")
        path.with_name(name + ".sha256").write_text("f" * 64 + "  " + name + "\n")
        stamp = (now - timedelta(days=20)).timestamp()
        os_module.utime(path, (stamp, stamp))

    outcome = _make_backup(cfg, keys, request_id="itest-0002")
    remaining = [name for name, _ in list_backup_files(cfg.backup_dir)]
    # 3 planted stale files + the new backup; retention deletes the two
    # oldest stale files and keeps the newest two (min_copies floor).
    assert outcome.file in remaining
    assert len(remaining) == 2
    assert len([n for n in remaining if n != outcome.file]) == 1
    actions = [action for action, _ in _audit_actions(cfg)]
    assert "backup_retention_cleaned" in actions


def test_lock_contention_fails_cleanly(
    cfg: RunnerConfig, keys: tuple[dict[str, bytes], str], seeded_user: None
) -> None:
    lock_path = cfg.backup_dir / ".backup.lock"
    holder = subprocess.Popen(
        [
            sys.executable,
            "-c",
            (
                "import fcntl,sys,time,pathlib;"
                "p=pathlib.Path(sys.argv[1]);p.parent.mkdir(parents=True,exist_ok=True);"
                "f=p.open('w');fcntl.flock(f.fileno(),fcntl.LOCK_EX);"
                "print('locked',flush=True);time.sleep(8)"
            ),
            str(lock_path),
        ],
        stdout=subprocess.PIPE,
        text=True,
    )
    assert holder.stdout is not None and holder.stdout.readline().strip() == "locked"
    time.sleep(0.3)
    outcome = run_backup(
        cfg,
        actor=None,
        actor_name="test-runner",
        reason="lock test",
        request_id="itest-lock",
        keys=keys[0],
        key_id=keys[1],
    )
    assert not outcome.ok
    assert outcome.exit_code == EXIT_LOCKED
    holder.wait(timeout=15)
    actions = [action for action, _ in _audit_actions(cfg)]
    assert "backup_failed" in actions


def test_weak_key_fails_before_dump(
    cfg: RunnerConfig, seeded_user: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    import base64

    monkeypatch.setenv("BACKUP_KEY_ID", "k-weak")
    monkeypatch.setenv("BACKUP_ENC_KEY", base64.b64encode(b"short").decode("ascii"))
    before = (
        {entry.name for entry in cfg.backup_dir.iterdir()} if cfg.backup_dir.exists() else set()
    )
    outcome = run_backup(
        cfg,
        actor=None,
        actor_name="test-runner",
        reason="weak key",
        request_id="itest-weak",
    )
    assert not outcome.ok
    assert outcome.exit_code == 6
    assert "32 bytes" in outcome.message
    after = {entry.name for entry in cfg.backup_dir.iterdir()} if cfg.backup_dir.exists() else set()
    assert after == before  # nothing was dumped or published


def test_unreachable_database_fails_cleanly(
    cfg: RunnerConfig, keys: tuple[dict[str, bytes], str]
) -> None:
    broken = RunnerConfig(
        database_url="postgresql+psycopg://hr_manager:wrong@localhost:5499/nope",
        backup_dir=cfg.backup_dir,
        state_file=cfg.state_file,
        pgdump_bin=_pgdump(),
        pgrestore_bin=_pgrestore(),
        min_free_mb=1,
    )
    outcome = run_backup(
        broken,
        actor=None,
        actor_name="test-runner",
        reason="broken db",
        request_id="itest-db",
        keys=keys[0],
        key_id=keys[1],
    )
    assert not outcome.ok
    assert "pg_dump failed" in outcome.message or "could not" in outcome.message.lower()


# --- restore drill -------------------------------------------------------------


def test_restore_drill_full_cycle(
    cfg: RunnerConfig, keys: tuple[dict[str, bytes], str], seeded_user: None
) -> None:
    """Real decrypt → pg_restore → migrations → table checks → /health → drop."""
    _make_backup(cfg, keys, request_id="itest-0003")
    drill = run_restore_drill(cfg, actor=None, actor_name="test-runner", keys=keys[0])
    assert drill.ok, drill.message
    assert drill.exit_code == EXIT_OK
    assert drill.restored_tables == 6
    assert drill.migration_ok is True
    assert drill.health_ok is True

    state = load_state(cfg.state_file)
    assert state.last_drill is not None and state.last_drill["ok"] is True
    assert state.last_drill["health_ok"] is True

    # The drill database is dropped afterwards — production data untouched.
    admin_engine = create_engine(_admin_url())
    try:
        with admin_engine.connect() as connection:
            found = connection.execute(
                text("SELECT 1 FROM pg_database WHERE datname = 'hr_manager_drill_test'")
            ).first()
            assert found is None
            prod_users = connection.execute(
                text("SELECT count(*) FROM pg_database WHERE datname = 'hr_manager_test'")
            ).scalar_one()
            assert prod_users == 1
    finally:
        admin_engine.dispose()
    actions = [action for action, _ in _audit_actions(cfg)]
    assert "backup_restore_drill_succeeded" in actions


def test_incomplete_file_is_not_a_backup(
    cfg: RunnerConfig, keys: tuple[dict[str, bytes], str], seeded_user: None
) -> None:
    partial = cfg.backup_dir / "hr-manager-20260904T120000Z-deadbeef.pgdump.enc"
    cfg.backup_dir.mkdir(parents=True, exist_ok=True)
    partial.write_bytes(b"HRMBCK1\n" + b"\x00" * 64)  # magic + garbage header
    outcome = run_backup(
        cfg,
        actor=None,
        actor_name="test-runner",
        reason="partial",
        request_id="itest-part",
        keys=keys[0],
        key_id=keys[1],
    )
    assert outcome.ok
    # The partial file is NOT the newest backup (state points to the real one).
    state = load_state(cfg.state_file)
    assert state.last_backup is not None
    assert state.last_backup.file != "hr-manager-20260904T120000Z-deadbeef.pgdump.enc"
    # And the newest-real-backup check does not blow up on the foreign garbage.
    check = run_check(cfg, actor=None, actor_name="test-runner", deep=False)
    assert check.ok


# --- migration single-flight lock ---------------------------------------------


def test_migration_advisory_lock_serializes_runners(
    cfg: RunnerConfig, monkeypatch: pytest.MonkeyPatch
) -> None:
    import psycopg

    monkeypatch.setenv("LD_LIBRARY_PATH", "/tmp/pginst/lib")
    # The migration guard locks per database: the holder must take the lock
    # in the same database the second alembic runner targets.
    raw_source_url = cfg.database_url.replace("postgresql+psycopg://", "postgresql://")
    holder = psycopg.connect(raw_source_url, autocommit=False)
    try:
        holder.execute("SELECT pg_advisory_xact_lock(767147072)")
        env = dict(os.environ)
        env["DATABASE_URL"] = cfg.database_url
        env["APP_ENV"] = "test"
        proc = subprocess.Popen(
            [sys.executable, "-m", "alembic", "upgrade", "head"],
            cwd=BACKEND_ROOT,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
        try:
            # The second migration runner must BLOCK on the advisory lock.
            time.sleep(2.0)
            assert proc.poll() is None, "alembic did not wait for the advisory lock"
        finally:
            holder.rollback()  # release the lock
        proc.wait(timeout=60)
        assert proc.returncode == 0, proc.stdout
    finally:
        holder.close()


# --- CLI end-to-end ------------------------------------------------------------


def test_cli_backup_now_and_check_end_to_end(
    cfg: RunnerConfig, seeded_user: None, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    import base64

    key = os.urandom(32)
    env = dict(os.environ)
    env.update(
        {
            "APP_ENV": "test",
            "DATABASE_URL": cfg.database_url,
            "BACKUP_DIR": str(tmp_path / "cli-backups"),
            "BACKUP_STATE_FILE": str(tmp_path / "cli-state.json"),
            "BACKUP_KEY_ID": "cli-key",
            "BACKUP_ENC_KEY": base64.b64encode(key).decode("ascii"),
            "BACKUP_ADMIN_PASSWORD": "Str0ng-Pass-2026",
            "BACKUP_PGDUMP_BIN": _pgdump(),
            "BACKUP_RESTORE_BIN": _pgrestore(),
            "LD_LIBRARY_PATH": "/tmp/pginst/lib",
        }
    )

    run = subprocess.run(
        [
            sys.executable,
            "-m",
            "app.cli",
            "backup-now",
            "--reason",
            "cli smoke",
            "--actor",
            "backup-seed-admin",
        ],
        cwd=BACKEND_ROOT,
        env=env,
        capture_output=True,
        text=True,
    )
    assert run.returncode == 0, run.stdout + run.stderr
    assert "backup published" in run.stdout

    check = subprocess.run(
        [sys.executable, "-m", "app.cli", "backup-check", "--deep", "--actor", "backup-seed-admin"],
        cwd=BACKEND_ROOT,
        env=env,
        capture_output=True,
        text=True,
    )
    assert check.returncode == 0, check.stdout + check.stderr
    assert "verified" in check.stdout

    # The CLI audited both operations under the authenticated administrator.
    with Session(create_engine(cfg.database_url)) as db:
        from sqlalchemy import select

        from app.models import AuditEvent

        rows = db.scalars(
            select(AuditEvent.username).where(AuditEvent.action.in_([AuditAction.BACKUP_SUCCEEDED]))
        ).all()
    assert "backup-seed-admin" in rows
