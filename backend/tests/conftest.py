"""Shared pytest fixtures for the HR Manager backend.

Unit tests run against an in-memory SQLite database (allowed ONLY for isolated
unit tests with APP_ENV=test, documented in README and ARCHITECTURE.md). The
ORM schema is created directly from the models metadata. Integration tests
(marker ``integration``) run against a real PostgreSQL via
``TEST_DATABASE_URL`` after the Alembic migration pipeline has been applied —
they never fall back to SQLite.
"""

import os
from collections.abc import Iterator
from datetime import datetime, timedelta
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, create_engine, text
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.config import Settings
from app.main import create_app
from app.models import (
    Base,
    Candidate,
    CandidateSource,
    CandidateStage,
    CandidateTransfer,
    Event,
    EventStatus,
    EventType,
    User,
    UserRole,
)
from app.security import hash_password
from app.utils import normalize_email, normalize_full_name, normalize_phone, utc_now

TEST_SQLITE_URL = "sqlite+pysqlite://"


def _install_pg8000_error_translation() -> None:
    """Local-harness aid: pg8000 maps only 23505 to ``IntegrityError``.

    The integration tests are written against psycopg semantics (any class-23
    integrity violation raises ``sqlalchemy.exc.IntegrityError``). pg8000's
    legacy layer translates every other server error to ``ProgrammingError``,
    so FK (23001), CHECK (23514) and trigger (P0001) violations would fail
    those assertions on the local PGlite harness. When the integration URL
    uses the pg8000 driver, re-translate class-23 errors at the DBAPI layer.
    This patch is a no-op for psycopg/postgres:16 in CI.
    """

    if "pg8000" not in os.environ.get("TEST_DATABASE_URL", ""):
        return
    try:
        # Безбрюкий ignore: в CI модуль отсутствует (import-not-found),
        # в локальном PGlite-окружении установлен без stubs (import-untyped).
        import pg8000.dbapi  # type: ignore
        import pg8000.legacy  # type: ignore
    except ImportError:  # pragma: no cover - pg8000 is a dev-only dependency
        return

    original_execute = pg8000.legacy.Cursor.execute

    def execute(self, operation, args=(), stream=None):
        try:
            return original_execute(self, operation, args, stream)
        except pg8000.dbapi.ProgrammingError as exc:
            message = exc.args[0] if exc.args else ""
            code = message.get("C", "") if isinstance(message, dict) else ""
            if code.startswith("23"):
                raise pg8000.dbapi.IntegrityError(message) from exc
            raise

    if pg8000.legacy.Cursor.execute is not execute:
        pg8000.legacy.Cursor.execute = execute

    # PGlite quirk: a repeated ROLLBACK over the socket returns a stray
    # DataRow; pg8000's row-less rollback context has ``rows=None`` and
    # crashes with ``'NoneType' object has no attribute 'append'``. Tolerate
    # stray rows in row-less contexts (they are discarded by the caller).
    import pg8000.core as _pg8000_core  # type: ignore

    def handle_DATA_ROW(self, data, context):
        if context.rows is None:
            context.rows = []
        original_data_row(self, data, context)

    original_data_row = _pg8000_core.CoreConnection.handle_DATA_ROW
    if _pg8000_core.CoreConnection.handle_DATA_ROW is not handle_DATA_ROW:
        _pg8000_core.CoreConnection.handle_DATA_ROW = handle_DATA_ROW


_install_pg8000_error_translation()

# A valid strong password used by fixtures (satisfies the password policy).
FIXTURE_PASSWORD = "Str0ng-Pass-2026"


@pytest.fixture()
def unit_engine() -> Iterator[Engine]:
    engine = create_engine(
        TEST_SQLITE_URL,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    yield engine
    engine.dispose()


@pytest.fixture(autouse=True)
def _clean_login_limiter() -> Iterator[None]:
    """Reset the process-global login rate limiter around every test.

    The limiter is a module-level singleton in ``app.routers.auth``; without
    a per-test reset, any suite that logs in more than LOGIN_RATE_LIMIT
    times in total trips 429 for unrelated tests (all requests share one
    client IP). This mirrors the fixture that used to live only in
    test_auth.py and applies the same isolation to the whole suite.
    """
    from app.routers.auth import reset_login_limiter
    from app.routers.candidate_messages import reset_candidate_message_limiters
    from app.routers.integrations import reset_integration_limiters
    from app.routers.setup import reset_first_run_limiter

    reset_login_limiter()
    reset_integration_limiters()
    reset_candidate_message_limiters()
    reset_first_run_limiter()
    yield
    reset_login_limiter()
    reset_integration_limiters()
    reset_candidate_message_limiters()
    reset_first_run_limiter()


@pytest.fixture()
def unit_settings() -> Settings:
    # model_validate mirrors how real environment variables map into the
    # settings (validation aliases), without touching the process env.
    return Settings.model_validate(
        {
            "APP_ENV": "test",
            "APP_DEBUG": "false",
            "SECRET_KEY": "unit-test-secret-key",
            "DATABASE_URL": TEST_SQLITE_URL,
            # Fast lockout threshold for deterministic unit tests.
            "LOGIN_MAX_FAILURES": "5",
            "LOGIN_LOCK_MINUTES": "15",
        }
    )


@pytest.fixture()
def client(unit_settings: Settings, unit_engine: Engine) -> Iterator[TestClient]:
    """TestClient backed by an in-memory SQLite engine with schema created."""
    app = create_app(unit_settings, engine=unit_engine)
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture()
def db_session(unit_engine: Engine) -> Iterator[Session]:
    """Direct ORM session over the in-memory test database."""
    with Session(unit_engine) as session:
        yield session
        session.rollback()


def make_user(
    db: Session,
    *,
    username: str,
    role: UserRole = UserRole.HR,
    password: str = FIXTURE_PASSWORD,
    full_name: str = "",
    is_active: bool = True,
) -> User:
    """Create and persist a user with an Argon2id password hash."""
    user = User(
        username=username,
        full_name=full_name or username,
        role=role,
        password_hash=hash_password(password),
        is_active=is_active,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def make_transfer(
    db: Session,
    *,
    candidate: Candidate,
    initiator: User,
    from_user: User,
    to_user: User,
    reason: str = "Перераспределение нагрузки",
) -> CandidateTransfer:
    """Create and persist an immutable ownership-transfer record."""
    transfer = CandidateTransfer(
        candidate_id=candidate.id,
        initiator_user_id=initiator.id,
        from_user_id=from_user.id,
        to_user_id=to_user.id,
        reason=reason,
    )
    db.add(transfer)
    db.commit()
    db.refresh(transfer)
    return transfer


def make_candidate(
    db: Session,
    *,
    owner: User,
    full_name: str = "Иванов Иван Иванович",
    phone: str | None = None,
    email: str | None = None,
    source: CandidateSource = CandidateSource.SITE,
    position: str = "",
    stage: CandidateStage = CandidateStage.NEW,
    deleted: bool = False,
) -> Candidate:
    """Create and persist a candidate owned by ``owner``."""
    from app.models import CANDIDATE_STAGE_POSITION

    candidate = Candidate(
        full_name=full_name,
        full_name_normalized=normalize_full_name(full_name),
        phone=phone,
        phone_normalized=normalize_phone(phone),
        email=email,
        email_normalized=normalize_email(email),
        source=source,
        position=position,
        owner_user_id=owner.id,
        stage=stage,
        stage_position=CANDIDATE_STAGE_POSITION[stage],
        deleted_at=utc_now() if deleted else None,
    )
    db.add(candidate)
    db.commit()
    db.refresh(candidate)
    return candidate


def make_event(
    db: Session,
    *,
    candidate: Candidate,
    author: User,
    assignee: User,
    type_: EventType = EventType.CALL,
    title: str = "Созвон",
    note: str | None = None,
    status: EventStatus = EventStatus.SCHEDULED,
    starts_at: datetime | None = None,
    ends_at: datetime | None = None,
    remind_at: datetime | None = None,
    completed_at: datetime | None = None,
    version: int = 1,
) -> Event:
    """Create and persist a calendar event (times default around a fixed
    near-future moment so status/consistency checks hold)."""
    starts_at = starts_at or utc_now() + timedelta(hours=2)
    if status == EventStatus.COMPLETED:
        completed_at = completed_at or utc_now()
    event = Event(
        candidate_id=candidate.id,
        author_user_id=author.id,
        assignee_user_id=assignee.id,
        type=type_,
        title=title,
        note=note,
        status=status,
        starts_at=starts_at,
        ends_at=ends_at,
        remind_at=remind_at,
        completed_at=completed_at,
        version=version,
    )
    db.add(event)
    db.commit()
    db.refresh(event)
    return event


def _require_integration_url() -> str:
    """Return TEST_DATABASE_URL when it points at PostgreSQL, else skip."""
    url = os.environ.get("TEST_DATABASE_URL")
    if not url or not url.startswith("postgresql"):
        pytest.skip("TEST_DATABASE_URL with a PostgreSQL URL is required for integration tests")
    return url


@pytest.fixture()
def integration_url() -> str:
    return _require_integration_url()


@pytest.fixture(scope="session")
def pg_engine() -> Iterator[Engine]:
    """Engine for the real PostgreSQL integration database.

    The schema is expected to exist (the integration test job runs
    ``alembic upgrade head`` beforehand; the migration tests manage upgrades
    themselves). Tables are truncated between tests for isolation.
    """
    url = _require_integration_url()
    engine = create_engine(url, pool_pre_ping=True)
    yield engine
    engine.dispose()


@pytest.fixture()
def pg_settings(integration_url: str) -> Settings:
    return Settings.model_validate(
        {
            "APP_ENV": "test",
            "APP_DEBUG": "false",
            "SECRET_KEY": "integration-test-secret-key",
            "DATABASE_URL": integration_url,
        }
    )


@pytest.fixture()
def pg_client(pg_settings: Settings, pg_engine: Engine) -> Iterator[TestClient]:
    """TestClient against PostgreSQL. Tables are truncated for a clean state."""
    with pg_engine.begin() as connection:
        connection.execute(
            text(
                "TRUNCATE TABLE audit_log, event_history, events, candidate_transfers, "
                "candidate_interactions, candidates, user_sessions, users, "
                "candidate_telegram_links, candidate_telegram_link_tokens, "
                "candidate_channel_consents, notification_delivery_attempts, "
                "notification_outbox, notifications, notification_preferences, "
                "telegram_start_events, telegram_link_tokens, telegram_links, "
                "telegram_poll_state, user_emails, access_grants, "
                "bootstrap_tickets, bootstrap_exchanges "
                "RESTART IDENTITY CASCADE"
            )
        )
    app = create_app(pg_settings, engine=pg_engine)
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture()
def pg_db(pg_engine: Engine) -> Iterator[Session]:
    """Direct ORM session over the PostgreSQL integration database."""
    with Session(pg_engine) as session:
        yield session
        session.rollback()


def user_id(user: User) -> UUID:
    """Typed helper for readability in tests."""
    return user.id


def make_document_set(db: Session, candidate: Candidate, names: list[str]) -> str:
    """Phase 11 fixture: persist server-owned content before sending a request."""
    from sqlalchemy import select

    from app.document_schemas import VersionOut
    from app.documents import current_set
    from app.models import (
        CandidateDocumentItem,
        CandidateDocumentSet,
        DocumentList,
        DocumentListVersion,
    )

    current = current_set(db, candidate.id)
    if current and [i["name"] for i in current.snapshot["items"]] == names:
        return str(current.id)
    for old in db.scalars(
        select(DocumentListVersion).where(
            DocumentListVersion.state == "published", DocumentListVersion.stage == ""
        )
    ):
        old.state = "archived"
    db.flush()
    parent = DocumentList(author_id=candidate.owner_user_id, stage="")
    db.add(parent)
    db.flush()
    version = DocumentListVersion(
        list_id=parent.id,
        author_id=candidate.owner_user_id,
        stage="",
        number=1,
        name="Документы",
        description="",
        state="published",
        published_at=utc_now(),
        items=[
            {"key": f"item_{i}", "name": name, "explanation": "", "required": True}
            for i, name in enumerate(names)
        ],
    )
    db.add(version)
    db.flush()
    snapshot = CandidateDocumentSet(
        candidate_id=candidate.id,
        list_version_id=version.id,
        revision=current.revision + 1 if current else 1,
        snapshot=VersionOut.model_validate(version).model_dump(mode="json"),
        author_id=candidate.owner_user_id,
    )
    db.add(snapshot)
    db.flush()
    for item in version.items:
        db.add(
            CandidateDocumentItem(
                set_id=snapshot.id, key=item["key"], changed_by=candidate.owner_user_id
            )
        )
    db.commit()
    return str(snapshot.id)
