"""Окружение Alembic.

URL БД берётся из настроек приложения (hr_manager.core.config), т.е. из
переменной окружения DATABASE_URL, а не из alembic.ini. Значение в
alembic.ini — только резерв по умолчанию для локального запуска вне docker.
"""

import os
from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

# Регистрируем модели приложения в Base.metadata (на Этапе 1 моделей нет).
from hr_manager.db.schema import Base  # noqa: E402
from hr_manager.core.config import Settings  # noqa: E402

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def _database_url() -> str:
    """Приоритет: DATABASE_URL из окружения -> alembic.ini."""
    return os.environ.get("DATABASE_URL", "").strip() or config.get_main_option(
        "sqlalchemy.url"
    )


def run_migrations_offline() -> None:
    """Офлайн-режим: формируем SQL без подключения к БД."""
    url = _database_url()
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Онлайн-режим: применяем миграции к реальной БД."""
    # get_settings() дополнительно валидирует production-конфигурацию,
    # поэтому неприменимые по безопасности значения не пройдут и сюда.
    Settings(database_url=_database_url())

    configuration = config.get_section(config.config_ini_section, {})
    configuration["sqlalchemy.url"] = _database_url()
    connectable = engine_from_config(
        configuration,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
