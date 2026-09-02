"""Command-line administration tools.

Usage::

    python -m app.cli create-admin --username admin --password '...' [--full-name '...']
    python -m app.cli list-users

``create-admin`` is idempotent: it exits with a clear message if the username
already exists. It is the supported way to create the first administrator in
production (the startup bootstrap also works when BOOTSTRAP_ADMIN_* settings
are provided).
"""

import argparse
import getpass
import sys

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import SessionLocal, bind_session_factory, build_engine
from app.models import User, UserRole
from app.security import WeakPasswordError, hash_password, validate_password_policy
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
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
