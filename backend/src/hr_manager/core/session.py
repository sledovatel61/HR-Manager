"""Сессии БД для FastAPI-зависимостей.

Каждый запрос получает собственную сессию (scope='session') и
гарантированно закрывает её. На этапах с бизнес-логикой здесь же
появится управление транзакциями (commit/rollback на границе запроса).
"""

from collections.abc import Generator

from sqlalchemy.orm import Session, sessionmaker

from hr_manager.core.config import get_settings
from hr_manager.core.db import create_db_engine

_engine = create_db_engine(get_settings())
_session_factory = sessionmaker(bind=_engine, autoflush=False, expire_on_commit=False)


def get_db_session() -> Generator[Session, None, None]:
    """FastAPI-зависимость: выдаёт сессию на запрос и всегда её закрывает."""
    session = _session_factory()
    try:
        yield session
    finally:
        session.close()
