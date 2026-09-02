"""Database access primitives.

Provides the SQLAlchemy engine, a connectivity probe used by the health
endpoint, and the ORM session factory / request dependency used by the
business routers.
"""

import logging
import time
from collections.abc import Iterator
from dataclasses import dataclass

from sqlalchemy import Engine, create_engine, text
from sqlalchemy.orm import Session, sessionmaker

from app.config import Settings

logger = logging.getLogger(__name__)

# Session factory is bound to the application engine in ``create_app``.
SessionLocal: sessionmaker[Session] = sessionmaker(autoflush=False, autocommit=False, future=True)


def build_engine(settings: Settings) -> Engine:
    """Create the SQLAlchemy engine for the configured database URL."""
    connect_args: dict[str, object] = {}
    if settings.database_url.startswith("postgresql"):
        connect_args["connect_timeout"] = int(settings.db_connect_timeout_seconds)
    return create_engine(settings.database_url, pool_pre_ping=True, connect_args=connect_args)


def bind_session_factory(engine: Engine) -> None:
    """Bind the module-level session factory to ``engine``."""
    SessionLocal.configure(bind=engine)


@dataclass(frozen=True)
class DatabaseProbe:
    """Result of a database connectivity check."""

    ok: bool
    latency_ms: int | None = None


def probe_database(engine: Engine) -> DatabaseProbe:
    """Run ``SELECT 1`` and report whether the database is reachable.

    Never raises: the health endpoint must degrade gracefully instead.
    """
    started = time.perf_counter()
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
    except Exception:  # connectivity probes must never raise; degraded state instead
        logger.warning("database health check failed", exc_info=True)
        return DatabaseProbe(ok=False)
    latency_ms = round((time.perf_counter() - started) * 1000)
    return DatabaseProbe(ok=True, latency_ms=latency_ms)


def get_db() -> Iterator[Session]:
    """FastAPI dependency: yield one ORM session per request."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
