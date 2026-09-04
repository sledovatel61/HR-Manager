"""Alembic migration environment (synchronous engine, PostgreSQL).

The database URL comes from the application configuration (``DATABASE_URL``
environment variable) so that every tool talks to the same database.

Concurrency guard: before running any migration the process takes a
PostgreSQL transaction-scoped advisory lock (``pg_advisory_xact_lock``) with
a constant key shared by every migration runner. Concurrent ``alembic
upgrade`` invocations therefore serialize: the second runner waits for the
first to commit, then observes the schema is already at head and does
nothing. The lock is released automatically when the migration transaction
ends — there is no stale-lock failure mode. (Only ``upgrade``/``downgrade``
runs migrate; ``alembic current``/``history`` never block because the lock is
taken inside the migration transaction only.)
"""

from logging.config import fileConfig

from sqlalchemy import engine_from_config, pool, text

from alembic import context
from app.config import get_settings
from app.models import Base

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

settings = get_settings()
# set_main_option performs %-interpolation, so escape literal percent signs.
config.set_main_option("sqlalchemy.url", settings.database_url.replace("%", "%%"))

# Migrations remain hand-written (explicit, reviewable DDL); metadata is
# attached so autogenerate is available when needed and so Alembic knows the
# full target schema.
target_metadata = Base.metadata

# Single-flight lock key: stable across deployments, project-specific.
MIGRATION_LOCK_KEY = 767_147_072


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode (emit SQL without a connection)."""
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode (against a live database)."""
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            # Serialize migration runners (see the module docstring).
            connection.execute(
                text("SELECT pg_advisory_xact_lock(:key)"),
                {"key": MIGRATION_LOCK_KEY},
            )
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
