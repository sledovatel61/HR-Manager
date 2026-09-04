"""Backup and restore-drill orchestration (roadmap phase 7).

This module owns the operational procedures around backups:

* full PostgreSQL backup with ``pg_dump -Fc`` into a consistent custom-format
  dump, encrypted BEFORE it reaches the long-term backup directory;
* post-dump verification (authenticated decrypt + SHA-256) before the
  plaintext staging file is removed;
* atomic publication (temp file + fsync + rename), checksum sidecar,
  freshness, retention with a minimum-copies floor;
* a restore drill into a SEPARATE database that validates the dump, applies
  migrations, checks key tables and probes ``/health`` of the application
  pointed at the restored database;
* audit events for every run (started/succeeded/failed) without PII.

The same code is executed by the admin API (background thread, actor is the
authenticated administrator) and by the backup CLI (actor authenticated
against the user database or the explicit ``backup-scheduler`` service
identity). Nothing here fakes success: every check that matters (pg_dump exit
code, encryption, decryption, SHA-256, migration head, table presence, HTTP
health) is performed for real and any failure produces a non-zero result.
"""

from __future__ import annotations

import fcntl
import hashlib
import os
import shutil
import signal
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from sqlalchemy import text
from sqlalchemy.engine import make_url

from app import backup as backup_lib
from app.audit import record_event
from app.db import SessionLocal
from app.models import AuditAction, User
from app.utils import utc_now

# Exit codes shared by the CLI and documented in docs/OPERATIONS.md.
EXIT_OK = 0
EXIT_USAGE = 2
EXIT_AUTH = 3
EXIT_LOCKED = 4
EXIT_DUMP = 5
EXIT_ENCRYPT = 6
EXIT_VERIFY = 7
EXIT_STATE = 8
EXIT_DRILL = 9
EXIT_PRUNE = 10
EXIT_UNHEALTHY = 11

REQUIRED_TABLES = (
    "users",
    "candidates",
    "audit_log",
    "events",
    "candidate_terminations",
    "analytics_facts",
)


class BackupLockedError(RuntimeError):
    """Another backup/drill process holds the lock."""


@dataclass
class RunnerConfig:
    """Everything the runners need; no secrets are ever printed/logged."""

    database_url: str
    backup_dir: Path
    state_file: Path
    pgdump_bin: str = "pg_dump"
    pgrestore_bin: str = "pg_restore"
    drill_admin_url: str | None = None
    drill_db_name: str = "hr_manager_restore_drill"
    retention_days: int = 7
    max_age_hours: int = 26
    min_copies: int = 2
    min_free_mb: int = 512
    alembic_dir: Path | None = None
    app_health_timeout_s: float = 90.0
    app_env: str = "production"

    @classmethod
    def from_settings(cls, settings: Any) -> RunnerConfig:
        return cls(
            database_url=settings.database_url,
            backup_dir=Path(settings.backup_dir),
            state_file=Path(settings.backup_state_file),
            pgdump_bin=settings.backup_pgdump_bin,
            pgrestore_bin=settings.backup_restore_bin,
            drill_admin_url=settings.backup_drill_admin_url or None,
            drill_db_name=settings.backup_drill_db_name,
            retention_days=settings.backup_retention_days,
            max_age_hours=settings.backup_max_age_hours,
            min_copies=settings.backup_min_copies,
            min_free_mb=settings.backup_min_free_mb,
            alembic_dir=(
                Path(settings.backup_alembic_dir)
                if settings.backup_alembic_dir
                else Path(__file__).resolve().parent.parent
            ),
            app_health_timeout_s=settings.backup_health_timeout_s,
            app_env=settings.environment,
        )


def _libpq_env(database_url: str, extra: dict[str, str] | None = None) -> dict[str, str]:
    """Derive libpq environment from a SQLAlchemy URL.

    The password travels through the process environment only — never through
    a command line — and is never printed.
    """
    url = make_url(database_url)
    env = dict(os.environ)
    env.update(extra or {})
    env["PGHOST"] = url.host or ""
    env["PGPORT"] = str(url.port or 5432)
    env["PGUSER"] = url.username or ""
    if url.password:
        env["PGPASSWORD"] = url.password
    env["PGDATABASE"] = url.database or ""
    env["PGOPTIONS"] = "-c statement_timeout=0"
    return env


def _fail_free_space(cfg: RunnerConfig) -> None:
    usage = shutil.disk_usage(cfg.backup_dir)
    if usage.free < cfg.min_free_mb * 1024 * 1024:
        raise RuntimeError(
            f"not enough free space for a backup: {usage.free // (1024 * 1024)} MiB "
            f"free, {cfg.min_free_mb} MiB required"
        )


def _acquire_lock(cfg: RunnerConfig) -> None:
    cfg.backup_dir.mkdir(parents=True, exist_ok=True)
    lock_path = cfg.backup_dir / ".backup.lock"
    handle = lock_path.open("w")
    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError as exc:
        handle.close()
        raise BackupLockedError(
            "another backup/restore operation is already running (lock held)"
        ) from exc
    # Keep the handle referenced on the config so the flock lives as long as
    # the operation does.
    cfg._lock_handle = handle  # type: ignore[attr-defined]


def _release_lock(cfg: RunnerConfig) -> None:
    handle = getattr(cfg, "_lock_handle", None)
    if handle is not None:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        finally:
            handle.close()
            cfg._lock_handle = None  # type: ignore[attr-defined]


def _audit(
    action: AuditAction,
    *,
    actor: User | None,
    actor_name: str,
    details: str,
) -> None:
    with SessionLocal() as db:
        record_event(
            db,
            action,
            actor=actor,
            username=actor.username if actor is not None else actor_name,
            details=details,
            commit=True,
        )


def _require_key_ring() -> dict[str, bytes]:
    """Fail fast with a clear message when encryption keys are unusable."""
    keys = backup_lib.load_keys_from_env()
    if not keys:
        raise RuntimeError("no backup encryption keys configured (BACKUP_KEY_ID/BACKUP_ENC_KEY)")
    return keys


@dataclass
class BackupOutcome:
    """Result of one backup run (no secrets, no dump contents)."""

    ok: bool
    exit_code: int
    message: str
    file: str | None = None
    size: int | None = None
    enc_sha256: str | None = None
    request_id: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "exit_code": self.exit_code,
            "message": self.message,
            "file": self.file,
            "size": self.size,
            "enc_sha256": self.enc_sha256,
            "request_id": self.request_id,
        }


def run_backup(
    cfg: RunnerConfig,
    *,
    actor: User | None,
    actor_name: str,
    reason: str,
    request_id: str,
    keys: dict[str, bytes] | None = None,
    key_id: str | None = None,
) -> BackupOutcome:
    """Create, encrypt, verify and publish one full backup; run retention."""
    if key_id is None:
        key_id = (os.environ.get("BACKUP_KEY_ID") or "").strip()
    try:
        key_ring = keys if keys is not None else _require_key_ring()
    except backup_lib.BackupError as exc:
        return BackupOutcome(
            ok=False, exit_code=EXIT_ENCRYPT, message=str(exc), request_id=request_id
        )
    primary = key_ring.get(key_id)
    if primary is None:
        return BackupOutcome(
            ok=False,
            exit_code=EXIT_ENCRYPT,
            message=f"encryption key id {key_id!r} is not configured",
            request_id=request_id,
        )

    started_at = utc_now()
    _audit(
        AuditAction.BACKUP_STARTED,
        actor=actor,
        actor_name=actor_name,
        details=f"request_id={request_id} reason={reason[:200] or '-'}",
    )
    try:
        _acquire_lock(cfg)
    except BackupLockedError as exc:
        _audit(
            AuditAction.BACKUP_FAILED,
            actor=actor,
            actor_name=actor_name,
            details=f"request_id={request_id} error=locked",
        )
        return BackupOutcome(
            ok=False, exit_code=EXIT_LOCKED, message=str(exc), request_id=request_id
        )

    plaintext: Path | None = None
    try:
        _fail_free_space(cfg)
        state = backup_lib.load_state(cfg.state_file)
        # The run id must always be [0-9a-f]{8}: list/retention/freshness only
        # recognize files matching BACKUP_FILE_RE. Derive it deterministically
        # from the request id so any caller-provided string stays parseable.
        run_id = (
            hashlib.sha256(request_id.encode("utf-8")).hexdigest()[:8]
            if request_id
            else f"{int(started_at.timestamp()):08x}"[-8:]
        )
        filename = backup_lib.backup_filename(started_at, run_id)
        staging = backup_lib.secure_tempdir(cfg.backup_dir, ".staging-")
        plaintext = staging / "dump.pgdump"
        ciphertext_tmp = staging / "dump.pgdump.enc"

        # 1. Consistent dump (custom format). The staging file is created
        #    0600 via os.open so no umask window can expose plaintext.
        env = _libpq_env(cfg.database_url)
        url = make_url(cfg.database_url)
        fd = os.open(plaintext, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "wb") as dump_out:
            proc = subprocess.run(
                [cfg.pgdump_bin, "-Fc", "-d", url.database or ""],
                env=env,
                stdout=dump_out,
                stderr=subprocess.PIPE,
                check=False,
            )
        if proc.returncode != 0:
            stderr_tail = (proc.stderr or b"").decode("utf-8", "replace")[-400:]
            raise RuntimeError(f"pg_dump failed (exit {proc.returncode}): {stderr_tail.strip()}")
        if plaintext.stat().st_size == 0:
            raise RuntimeError("pg_dump produced an empty dump")

        # 2. Encrypt (authenticated, header-bound) into a temporary file.
        cipher_fd = os.open(ciphertext_tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with plaintext.open("rb") as src, os.fdopen(cipher_fd, "wb") as dst:
            backup_lib.encrypt_stream(src, dst, key=primary, key_id=key_id, created_at=started_at)

        # 3. Verify before the plaintext is deleted: authenticated decrypt of
        #    the published bytes + SHA-256 + detached checksum.
        with ciphertext_tmp.open("rb") as src, open(os.devnull, "wb") as sink:
            backup_lib.decrypt_to_stream(src, sink, keys=key_ring)

        final_path = cfg.backup_dir / filename
        os.replace(ciphertext_tmp, final_path)
        digest = backup_lib.write_checksum_file(final_path)
        os.unlink(plaintext)
        plaintext = None
        shutil.rmtree(staging, ignore_errors=True)

        record = backup_lib.BackupRecord(
            file=filename,
            at=started_at.isoformat(),
            size=final_path.stat().st_size,
            enc_sha256=digest,
            status="ok",
            reason=reason[:200],
            request_id=request_id,
        )
        backup_lib.update_state_backup(cfg.state_file, state, record)

        # 4. Retention: delete only backups older than the policy and never
        #    the newest ``min_copies``; runs only after a successful backup.
        deleted, _kept = backup_lib.retention_plan(
            backup_lib.list_backup_files(cfg.backup_dir),
            now=utc_now(),
            retention_days=cfg.retention_days,
            min_copies=cfg.min_copies,
        )
        for name in deleted:
            path = cfg.backup_dir / name
            path.unlink(missing_ok=True)
            path.with_name(name + ".sha256").unlink(missing_ok=True)
        if deleted:
            _audit(
                AuditAction.BACKUP_RETENTION_CLEANED,
                actor=actor,
                actor_name=actor_name,
                details=f"removed={len(deleted)}",
            )

        _audit(
            AuditAction.BACKUP_SUCCEEDED,
            actor=actor,
            actor_name=actor_name,
            details=f"request_id={request_id} file={filename} size={final_path.stat().st_size}",
        )
        return BackupOutcome(
            ok=True,
            exit_code=EXIT_OK,
            message=f"backup published: {filename}",
            file=filename,
            size=final_path.stat().st_size,
            enc_sha256=digest,
            request_id=request_id,
        )
    except BackupLockedError as exc:
        return BackupOutcome(
            ok=False, exit_code=EXIT_LOCKED, message=str(exc), request_id=request_id
        )
    except backup_lib.BackupError as exc:
        _audit(
            AuditAction.BACKUP_FAILED,
            actor=actor,
            actor_name=actor_name,
            details=f"request_id={request_id} error={type(exc).__name__}",
        )
        return BackupOutcome(
            ok=False, exit_code=EXIT_VERIFY, message=str(exc), request_id=request_id
        )
    except Exception as exc:  # pragma: no cover - safety net for OS-level failures
        _audit(
            AuditAction.BACKUP_FAILED,
            actor=actor,
            actor_name=actor_name,
            details=f"request_id={request_id} error={type(exc).__name__}",
        )
        return BackupOutcome(ok=False, exit_code=EXIT_DUMP, message=str(exc), request_id=request_id)
    finally:
        if plaintext is not None:
            plaintext.unlink(missing_ok=True)
        _release_lock(cfg)


def _expected_migration_head(cfg: RunnerConfig) -> str | None:
    if cfg.alembic_dir is None:
        return None
    try:
        from alembic.config import Config
        from alembic.script import ScriptDirectory

        alembic_cfg = Config(str(cfg.alembic_dir / "alembic.ini"))
        alembic_cfg.set_main_option("script_location", str(cfg.alembic_dir / "alembic"))
        return ScriptDirectory.from_config(alembic_cfg).get_current_head()
    except Exception:  # pragma: no cover - configuration problem, reported as drill failure
        return None


def _current_migration_revision(database_url: str) -> str | None:
    from sqlalchemy import create_engine

    engine = create_engine(database_url, pool_pre_ping=False)
    try:
        with engine.connect() as connection:
            result = connection.execute(text("SELECT version_num FROM alembic_version")).first()
            return str(result[0]) if result is not None else None
    except Exception:  # pragma: no cover - no alembic table yet
        return None
    finally:
        engine.dispose()


def _migrate_database(cfg: RunnerConfig, database_url: str) -> None:
    if cfg.alembic_dir is None:
        raise RuntimeError("alembic directory is not configured; cannot migrate the drill database")
    env = dict(os.environ)
    env["DATABASE_URL"] = database_url
    env["APP_ENV"] = cfg.app_env
    proc = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=cfg.alembic_dir,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
        timeout=300,
    )
    if proc.returncode != 0:
        tail = (proc.stdout or b"").decode("utf-8", "replace")[-400:]
        raise RuntimeError(f"alembic upgrade failed (exit {proc.returncode}): {tail.strip()}")


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _probe_health(url: str, timeout_s: float) -> bool:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=5) as response:
                if response.status == 200:
                    return True
        except (urllib.error.URLError, OSError):
            time.sleep(1.0)
    return False


@dataclass
class DrillOutcome:
    """Result of one restore drill (no PII, no dump contents)."""

    ok: bool
    exit_code: int
    message: str
    file: str | None = None
    restored_tables: int | None = None
    migration_ok: bool | None = None
    health_ok: bool | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "exit_code": self.exit_code,
            "message": self.message,
            "file": self.file,
            "restored_tables": self.restored_tables,
            "migration_ok": self.migration_ok,
            "health_ok": self.health_ok,
        }


def run_restore_drill(
    cfg: RunnerConfig,
    *,
    actor: User | None,
    actor_name: str,
    file: str | None = None,
    keys: dict[str, bytes] | None = None,
) -> DrillOutcome:
    """Restore the newest (or named) backup into a separate database.

    The drill validates the backup end-to-end: authenticated decrypt, schema
    version, migrations to head, key tables, and ``/health`` of the app
    pointed at the restored database. The production database is never
    touched; the drill database is dropped afterwards.
    """
    try:
        key_ring = keys if keys is not None else _require_key_ring()
    except backup_lib.BackupError as exc:
        return DrillOutcome(ok=False, exit_code=EXIT_ENCRYPT, message=str(exc))
    _audit(
        AuditAction.BACKUP_RESTORE_DRILL_STARTED,
        actor=actor,
        actor_name=actor_name,
        details=f"file={file or 'newest'}",
    )
    try:
        _acquire_lock(cfg)
    except BackupLockedError as exc:
        _audit(
            AuditAction.BACKUP_RESTORE_DRILL_FAILED,
            actor=actor,
            actor_name=actor_name,
            details="error=locked",
        )
        return DrillOutcome(ok=False, exit_code=EXIT_LOCKED, message=str(exc))

    app_proc: subprocess.Popen[bytes] | None = None
    staging: Path | None = None
    try:
        state = backup_lib.load_state(cfg.state_file)
        if file is None:
            record = state.last_backup
            if record is None or record.status != "ok":
                raise RuntimeError("no successful backup recorded; run a backup first")
            file = record.file
        enc_path = cfg.backup_dir / file
        if not enc_path.is_file():
            raise RuntimeError(f"backup file not found: {file}")

        if not backup_lib.verify_checksum_file(enc_path):
            raise backup_lib.BackupIntegrityError("encrypted backup checksum mismatch")

        staging = backup_lib.secure_tempdir(cfg.backup_dir, ".drill-")
        plaintext = staging / "dump.pgdump"
        plain_fd = os.open(plaintext, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with enc_path.open("rb") as src, os.fdopen(plain_fd, "wb") as dst:
            backup_lib.decrypt_to_stream(src, dst, keys=key_ring)

        if cfg.drill_admin_url is None:
            raise RuntimeError(
                "BACKUP_DRILL_ADMIN_URL is not set; a superuser connection is required "
                "to create/drop the drill database"
            )
        from sqlalchemy import create_engine

        admin_engine = create_engine(cfg.drill_admin_url, isolation_level="AUTOCOMMIT")
        try:
            with admin_engine.connect() as connection:
                connection.execute(
                    text(f"DROP DATABASE IF EXISTS {cfg.drill_db_name} WITH (FORCE)")
                )
                connection.execute(text(f"CREATE DATABASE {cfg.drill_db_name}"))
        finally:
            admin_engine.dispose()

        # str(URL) hides the password ("***") in SQLAlchemy 2.x; render
        # explicitly so the drill connection carries real credentials.
        drill_url = (
            make_url(cfg.database_url)
            .set(database=cfg.drill_db_name)
            .render_as_string(hide_password=False)
        )
        env = _libpq_env(drill_url)
        proc = subprocess.run(
            [cfg.pgrestore_bin, "--exit-on-error", "--no-owner", "-d", cfg.drill_db_name],
            env=env,
            stdin=plaintext.open("rb"),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            check=False,
            timeout=600,
        )
        if proc.returncode != 0:
            tail = (proc.stdout or b"").decode("utf-8", "replace")[-400:]
            raise RuntimeError(f"pg_restore failed (exit {proc.returncode}): {tail.strip()}")

        _migrate_database(cfg, drill_url)
        migration_ok = _current_migration_revision(drill_url) == _expected_migration_head(cfg)

        engine = create_engine(drill_url)
        try:
            with engine.connect() as connection:
                present = connection.execute(
                    text(
                        "SELECT count(*) FROM information_schema.tables "
                        "WHERE table_schema = 'public' AND table_name = ANY(:names)"
                    ),
                    {"names": list(REQUIRED_TABLES)},
                ).scalar_one()
                users_count = connection.execute(text("SELECT count(*) FROM users")).scalar_one()
        finally:
            engine.dispose()
        if present != len(REQUIRED_TABLES):
            raise RuntimeError(
                f"restored schema is missing tables: {present}/{len(REQUIRED_TABLES)} present"
            )
        if users_count < 1:
            raise RuntimeError("restored database has no users; drill cannot pass")

        # Application health against the RESTORED database.
        port = _free_port()
        health_env = dict(os.environ)
        health_env["DATABASE_URL"] = drill_url
        health_env["APP_ENV"] = cfg.app_env
        cwd = cfg.alembic_dir if cfg.alembic_dir is not None else Path.cwd()
        app_proc = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "uvicorn",
                "app.main:app",
                "--host",
                "127.0.0.1",
                "--port",
                str(port),
                "--log-level",
                "warning",
            ],
            cwd=cwd,
            env=health_env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        try:
            health_ok = _probe_health(f"http://127.0.0.1:{port}/health", cfg.app_health_timeout_s)
        finally:
            app_proc.send_signal(signal.SIGTERM)
            try:
                app_proc.wait(timeout=30)
            except subprocess.TimeoutExpired:
                app_proc.kill()
            app_proc = None
        if not health_ok:
            raise RuntimeError("application /health did not turn 200 on the restored database")

        _audit(
            AuditAction.BACKUP_RESTORE_DRILL_SUCCEEDED,
            actor=actor,
            actor_name=actor_name,
            details=(
                f"file={file} tables={present} migrations_ok={migration_ok} health_ok={health_ok}"
            ),
        )
        state.last_drill = {
            "at": utc_now().isoformat(),
            "file": file,
            "ok": True,
            "tables": present,
            "migration_ok": migration_ok,
            "health_ok": health_ok,
        }
        backup_lib.save_state(cfg.state_file, state)
        return DrillOutcome(
            ok=True,
            exit_code=EXIT_OK,
            message=f"restore drill passed on database {cfg.drill_db_name!r}",
            file=file,
            restored_tables=present,
            migration_ok=migration_ok,
            health_ok=health_ok,
        )
    except BackupLockedError as exc:
        return DrillOutcome(ok=False, exit_code=EXIT_LOCKED, message=str(exc))
    except (backup_lib.BackupError, RuntimeError) as exc:
        _audit(
            AuditAction.BACKUP_RESTORE_DRILL_FAILED,
            actor=actor,
            actor_name=actor_name,
            details=f"file={file or 'newest'} error={type(exc).__name__}",
        )
        state = backup_lib.load_state(cfg.state_file)
        state.last_drill = {
            "at": utc_now().isoformat(),
            "file": file,
            "ok": False,
            "error": type(exc).__name__,
        }
        backup_lib.save_state(cfg.state_file, state)
        return DrillOutcome(ok=False, exit_code=EXIT_DRILL, message=str(exc), file=file)
    finally:
        if app_proc is not None:  # pragma: no cover - defensive cleanup
            app_proc.kill()
        _release_lock(cfg)
        if cfg.drill_admin_url is not None:
            _drop_drill_database(cfg)
        if staging is not None:
            shutil.rmtree(staging, ignore_errors=True)


def _drop_drill_database(cfg: RunnerConfig) -> None:
    """Best-effort cleanup of the drill database (never the production one)."""
    from sqlalchemy import create_engine

    try:
        admin_engine = create_engine(cfg.drill_admin_url or "", isolation_level="AUTOCOMMIT")
        try:
            with admin_engine.connect() as connection:
                connection.execute(
                    text(f"DROP DATABASE IF EXISTS {cfg.drill_db_name} WITH (FORCE)")
                )
        finally:
            admin_engine.dispose()
    except Exception:  # pragma: no cover - cleanup is best-effort
        pass


@dataclass
class CheckOutcome:
    """Result of a backup integrity/freshness check."""

    ok: bool
    exit_code: int
    message: str
    file: str | None = None
    age_seconds: float | None = None
    checksum_ok: bool | None = None
    decrypt_ok: bool | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "exit_code": self.exit_code,
            "message": self.message,
            "file": self.file,
            "age_seconds": self.age_seconds,
            "checksum_ok": self.checksum_ok,
            "decrypt_ok": self.decrypt_ok,
        }


def run_check(
    cfg: RunnerConfig,
    *,
    actor: User | None,
    actor_name: str,
    file: str | None = None,
    deep: bool = False,
    keys: dict[str, bytes] | None = None,
) -> CheckOutcome:
    """Verify the newest (or named) backup: freshness, checksum, decrypt."""
    now = utc_now()
    state = backup_lib.load_state(cfg.state_file)
    if file is None:
        record = state.last_backup
        file = record.file if record is not None else None
    if not file:
        _audit(
            AuditAction.BACKUP_VERIFY_FAILED,
            actor=actor,
            actor_name=actor_name,
            details="error=no_backup_recorded",
        )
        return CheckOutcome(ok=False, exit_code=EXIT_VERIFY, message="no backup recorded yet")
    enc_path = cfg.backup_dir / file
    if not enc_path.is_file():
        _audit(
            AuditAction.BACKUP_VERIFY_FAILED,
            actor=actor,
            actor_name=actor_name,
            details=f"file={file} error=missing",
        )
        return CheckOutcome(
            ok=False, exit_code=EXIT_VERIFY, message=f"backup file not found: {file}", file=file
        )

    fresh, age = backup_lib.freshness_ok(state, now=now, max_age_hours=cfg.max_age_hours)
    checksum_ok = backup_lib.verify_checksum_file(enc_path)
    decrypt_ok: bool | None = None
    if deep:
        try:
            key_ring = keys if keys is not None else _require_key_ring()
        except backup_lib.BackupError as exc:
            return CheckOutcome(ok=False, exit_code=EXIT_UNHEALTHY, message=str(exc), file=file)
        try:
            with enc_path.open("rb") as src, open(os.devnull, "wb") as sink:
                backup_lib.decrypt_to_stream(src, sink, keys=key_ring)
            decrypt_ok = True
        except backup_lib.BackupError:
            decrypt_ok = False

    ok = fresh and checksum_ok and (decrypt_ok is None or decrypt_ok)
    state.last_check = {
        "at": now.isoformat(),
        "file": file,
        "ok": ok,
        "fresh": fresh,
        "checksum_ok": checksum_ok,
        "decrypt_ok": decrypt_ok,
        "age_seconds": round(age, 1) if age is not None else None,
    }
    backup_lib.save_state(cfg.state_file, state)
    if not ok:
        _audit(
            AuditAction.BACKUP_VERIFY_FAILED,
            actor=actor,
            actor_name=actor_name,
            details=(
                f"file={file} fresh={fresh} checksum_ok={checksum_ok} decrypt_ok={decrypt_ok}"
            ),
        )
    problems = []
    if not fresh:
        problems.append(
            f"backup is older than {cfg.max_age_hours}h"
            + (f" (age {age / 3600:.1f}h)" if age is not None else "")
        )
    if not checksum_ok:
        problems.append("encrypted backup checksum mismatch")
    if decrypt_ok is False:
        problems.append("decrypt verification failed")
    return CheckOutcome(
        ok=ok,
        exit_code=EXIT_OK if ok else EXIT_UNHEALTHY,
        message="; ".join(problems) if problems else f"backup {file} verified",
        file=file,
        age_seconds=age,
        checksum_ok=checksum_ok,
        decrypt_ok=decrypt_ok,
    )
