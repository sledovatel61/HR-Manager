"""Command-line administration tools.

Usage::

    python -m app.cli create-admin --username admin --password '...' [--full-name '...']
    python -m app.cli list-users

Backup/ops commands (roadmap phase 7)::

    python -m app.cli backup-now --reason "перед обновлением" --actor admin
    python -m app.cli backup-check [--file NAME] [--deep] --actor admin
    python -m app.cli backup-drill [--file NAME] --actor admin
    python -m app.cli backup-list
    python -m app.cli backup-prune --yes --actor admin

``create-admin`` is idempotent: it exits with a clear message if the username
already exists. It is the supported way to create the first administrator in
production (the startup bootstrap also works when BOOTSTRAP_ADMIN_* settings
are provided).

Backup commands require an authenticated administrator (``--actor`` with the
password from ``BACKUP_ADMIN_PASSWORD`` or an interactive prompt, verified
against the user database) or the explicit service identity
``--as-scheduler`` for unattended cron runs. Every invocation is audited
without PII; exit codes are documented in ``docs/OPERATIONS.md``.
"""

import argparse
import getpass
import os
import sys
import uuid
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import backup as backup_lib
from app.backup_runner import (
    EXIT_AUTH,
    EXIT_DRILL,
    EXIT_ENCRYPT,
    EXIT_OK,
    EXIT_PRUNE,
    EXIT_UNHEALTHY,
    EXIT_VERIFY,
    RunnerConfig,
    run_backup,
    run_check,
    run_restore_drill,
)
from app.config import get_settings
from app.db import SessionLocal, bind_session_factory, build_engine
from app.models import AuditAction, User, UserRole
from app.security import (
    WeakPasswordError,
    hash_password,
    validate_password_policy,
    verify_password,
)
from app.utils import utc_now


def _session() -> Session:
    settings = get_settings()
    engine = build_engine(settings)
    bind_session_factory(engine)
    return SessionLocal()


def create_admin(args: argparse.Namespace) -> int:
    password = args.password or getpass.getpass("Administrator password: ")
    username = args.username.strip()
    try:
        validate_password_policy(password, username=username)
    except WeakPasswordError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    with _session() as db:
        existing = db.scalar(select(User).where(func.lower(User.username) == username.lower()))
        if existing is not None:
            print(f"error: user {username!r} already exists", file=sys.stderr)
            return 1

        admin = User(
            username=username,
            full_name=(args.full_name or "").strip(),
            role=UserRole.ADMIN,
            password_hash=hash_password(password),
            is_active=True,
            created_at=utc_now(),
            updated_at=utc_now(),
        )
        db.add(admin)
        db.commit()
        db.refresh(admin)
        print(f"created administrator {admin.username!r} (id={admin.id})")
    return 0


def list_users(_args: argparse.Namespace) -> int:
    with _session() as db:
        users = db.scalars(select(User).order_by(User.username)).all()
        if not users:
            print("(no users)")
            return 0
        for user in users:
            status_flag = "active" if user.is_active else "disabled"
            print(f"{user.username:<32} {user.role.value:<8} {status_flag:<8} {user.id}")
    return 0


# --- Backup/ops commands (phase 7) ------------------------------------------


def _bind() -> None:
    """Bind the global session factory to the configured database."""
    settings = get_settings()
    engine = build_engine(settings)
    bind_session_factory(engine)


def _runner_config() -> RunnerConfig:
    settings = get_settings()
    cfg = RunnerConfig.from_settings(settings)
    cfg.alembic_dir = Path(__file__).resolve().parent.parent
    drill_admin_url = os.environ.get("BACKUP_DRILL_ADMIN_URL") or ""
    cfg.drill_admin_url = drill_admin_url or None
    return cfg


def _authenticate_actor(args: argparse.Namespace) -> tuple[User | None, str]:
    """Resolve the audit actor: an authenticated admin or the scheduler.

    The admin password travels through the environment or the interactive
    prompt only and is verified against the password hash in the database
    (server-side authorization); it is never printed or stored.
    """
    if args.as_scheduler:
        if args.actor:
            print("error: --actor and --as-scheduler are mutually exclusive", file=sys.stderr)
            raise SystemExit(EXIT_AUTH)
        return None, "backup-scheduler"
    username = (args.actor or "").strip()
    if not username:
        print(
            "error: run requires --actor <admin-username> or the explicit --as-scheduler flag",
            file=sys.stderr,
        )
        raise SystemExit(EXIT_AUTH)
    password = os.environ.get("BACKUP_ADMIN_PASSWORD") or getpass.getpass(
        f"Password for administrator {username!r}: "
    )
    with SessionLocal() as db:
        user = db.scalar(select(User).where(func.lower(User.username) == username.lower()))
        if (
            user is None
            or not user.is_active
            or user.role != UserRole.ADMIN
            or not verify_password(user.password_hash, password)
        ):
            print("error: administrator authentication failed", file=sys.stderr)
            raise SystemExit(EXIT_AUTH)
    return user, user.username


def backup_now(args: argparse.Namespace) -> int:
    """Create, encrypt, verify and publish a full backup."""
    _bind()
    actor, actor_name = _authenticate_actor(args)
    cfg = _runner_config()
    request_id = args.request_id or uuid.uuid4().hex[:16]
    try:
        outcome = run_backup(
            cfg,
            actor=actor,
            actor_name=actor_name,
            reason=args.reason,
            request_id=request_id,
        )
    except backup_lib.BackupError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_ENCRYPT
    print(outcome.message)
    return outcome.exit_code


def backup_check(args: argparse.Namespace) -> int:
    """Verify freshness/checksum (and, with --deep, authenticated decrypt)."""
    _bind()
    actor, actor_name = _authenticate_actor(args)
    cfg = _runner_config()
    try:
        outcome = run_check(cfg, actor=actor, actor_name=actor_name, file=args.file, deep=args.deep)
    except backup_lib.BackupError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_VERIFY
    print(outcome.message)
    if not outcome.ok:
        return EXIT_UNHEALTHY
    return EXIT_OK


def backup_drill(args: argparse.Namespace) -> int:
    """Restore the newest (or named) backup into a separate database."""
    _bind()
    actor, actor_name = _authenticate_actor(args)
    cfg = _runner_config()
    try:
        outcome = run_restore_drill(cfg, actor=actor, actor_name=actor_name, file=args.file)
    except backup_lib.BackupError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_DRILL
    print(outcome.message)
    return outcome.exit_code


def backup_list(args: argparse.Namespace) -> int:
    """List recent backups from the state file."""
    cfg = _runner_config()
    state = backup_lib.load_state(cfg.state_file)
    records = state.recent[: args.limit]
    if not records:
        print("(no backups recorded)")
        return EXIT_OK
    for record in records:
        print(
            f"{record.file:<48} {record.size:>10} bytes  {record.at}  "
            f"{record.status}  reason={record.reason or '-'}"
        )
    return EXIT_OK


def backup_prune(args: argparse.Namespace) -> int:
    """Run retention cleanup (never below BACKUP_MIN_COPIES)."""
    if not args.yes:
        print("error: backup-prune needs --yes to confirm", file=sys.stderr)
        return EXIT_PRUNE
    _bind()
    actor, actor_name = _authenticate_actor(args)
    cfg = _runner_config()
    deleted, kept = backup_lib.retention_plan(
        backup_lib.list_backup_files(cfg.backup_dir),
        now=utc_now(),
        retention_days=cfg.retention_days,
        min_copies=cfg.min_copies,
    )
    for name in deleted:
        path = cfg.backup_dir / name
        path.unlink(missing_ok=True)
        path.with_name(name + ".sha256").unlink(missing_ok=True)
    with SessionLocal() as db:
        from app.audit import record_event

        record_event(
            db,
            AuditAction.BACKUP_RETENTION_CLEANED,
            actor=actor,
            username=actor.username if actor else actor_name,
            details=f"removed={len(deleted)} kept={len(kept)}",
            commit=True,
        )
    print(f"retention: removed {len(deleted)}, kept {len(kept)}")
    return EXIT_OK


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m app.cli", description="HR Manager admin tools")
    sub = parser.add_subparsers(dest="command", required=True)

    create = sub.add_parser("create-admin", help="create an administrator user")
    create.add_argument("--username", required=True)
    create.add_argument("--password", help="prompted interactively if omitted")
    create.add_argument("--full-name", default="")
    create.set_defaults(func=create_admin)

    listing = sub.add_parser("list-users", help="list all users")
    listing.set_defaults(func=list_users)

    def add_actor_arguments(command: argparse.ArgumentParser) -> None:
        command.add_argument(
            "--actor",
            help="administrator username (password via BACKUP_ADMIN_PASSWORD env or prompt)",
        )
        command.add_argument(
            "--as-scheduler",
            action="store_true",
            help="audit the run as the backup-scheduler service identity",
        )

    backup = sub.add_parser("backup-now", help="create an encrypted backup now")
    backup.add_argument(
        "--reason", required=True, help="why the backup is taken (no personal data)"
    )
    backup.add_argument("--request-id", default="", help="optional correlation id")
    add_actor_arguments(backup)
    backup.set_defaults(func=backup_now)

    check = sub.add_parser("backup-check", help="verify the newest backup (freshness/checksum)")
    check.add_argument("--file", default=None, help="verify this backup instead of the newest")
    check.add_argument(
        "--deep",
        action="store_true",
        help="additionally decrypt-verify the backup (needs the keys)",
    )
    add_actor_arguments(check)
    check.set_defaults(func=backup_check)

    drill = sub.add_parser("backup-drill", help="restore the newest backup into a separate DB")
    drill.add_argument("--file", default=None, help="drill this backup instead of the newest")
    add_actor_arguments(drill)
    drill.set_defaults(func=backup_drill)

    backups = sub.add_parser("backup-list", help="list recent backups from the state file")
    backups.add_argument("--limit", type=int, default=20)
    backups.set_defaults(func=backup_list)

    prune = sub.add_parser("backup-prune", help="run retention cleanup manually")
    prune.add_argument("--yes", action="store_true", help="confirm the cleanup")
    add_actor_arguments(prune)
    prune.set_defaults(func=backup_prune)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
