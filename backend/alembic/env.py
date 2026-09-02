"""Окружение Alembic.

URL подключения берётся из переменной окружения ``DATABASE_URL``
(в Docker Compose она задаётся через ``.env``). Хранить URL с паролем
в ``alembic.ini`` нельзя.

ORM-модели и ``target_metadata`` появятся в Этапе 2 (единая база
кандидатов). До тех пор автогенерация миграций отключена осознанно:
baseline-ревизия проверяет сам конвейер миграций.
"""

import os
from logging.config import fileConfig

from sqlalchemy import engine_from_config, pool

from alembic import context

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# ORM metadata появится вместе с моделями в Этапе 2 (см. ROADMAP.md).
target_metadata = None


def get_url() -> str:
    """URL подключения: переменная окружения, затем опция конфига."""
    url = os.environ.get("DATABASE_URL") or config.get_main_option("sqlalchemy.url")
    if not url:
        raise RuntimeError(
            "URL базы данных не задан: установите переменную окружения "
            "DATABASE_URL (см. .env.example)"
        )
    return url


def run_migrations_offline() -> None:
    """Генерация SQL без подключения к базе."""
    context.configure(
        url=get_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Применение миграций через подключение к базе."""
    configuration = config.get_section(config.config_ini_section, default={})
    configuration["sqlalchemy.url"] = get_url()
    connectable = engine_from_config(
        configuration,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
