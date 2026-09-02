"""Доступ к PostgreSQL через SQLAlchemy 2.

Используется единый source of truth: hr_manager.db.Base.metadata.
Никакой SQLite — единственная поддерживаемая БД приложения PostgreSQL.
"""

from sqlalchemy import Engine, create_engine, text

from hr_manager.core.config import Settings, get_settings


def create_db_engine(settings: Settings | None = None) -> Engine:
    """Создаёт engine SQLAlchemy из настроек приложения."""
    settings = settings or get_settings()
    return create_engine(settings.database_url, pool_pre_ping=True)


def check_database(engine: Engine | None = None) -> None:
    """Проверяет доступность БД; бросает исключение при проблемах.

    Используется health-endpoint'ом /health. Намеренно не возвращает
    статус-код, чтобы вызывающий код сам решал, как обработать сбой.
    """
    engine = engine or create_db_engine()
    with engine.connect() as connection:
        connection.execute(text("SELECT 1"))
