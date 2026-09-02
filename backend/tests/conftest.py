"""Общие фикстуры backend-тестов.

Принципы:
  * Тесты НИКОГДА не ходят в настоящую production-БД или прод-окружение:
    APP_ENV принудительно выставляется в "test" до импорта приложения.
  * Для изолированных unit-тестов разрешён SQLite (in-memory) — это явно
    документированное исключение из правила «PostgreSQL everywhere»
    (см. agents.md и README). Интеграционные проверки с настоящим
    PostgreSQL выполняются только в CI с сервисным контейнером.
"""

import logging
import os

import pytest

logger = logging.getLogger(__name__)

# 1) Окружение теста выставляется ДО любого импорта приложения.
os.environ.setdefault("APP_ENV", "test")
os.environ.setdefault(
    "DATABASE_URL",
    # PostgreSQL по умолчанию. Если PostgreSQL недоступен, применяйте
    # явную пометку pytest.mark.database и запускайте только при живом PG.
    "postgresql+psycopg://test:test@127.0.0.1:1/hr_manager_test",
)

# 2) Блокируем случайное обращение к настоящим окружениям: любой тест,
#    импортирующий приложение при APP_ENV=production, падает сразу.
if os.environ.get("APP_ENV") == "production":
    raise RuntimeError(
        "Запрещено запускать тесты с APP_ENV=production: "
        "тесты могут задеть прод-ресурсы."
    )


@pytest.fixture()
def database_url_unreachable() -> str:
    """URL гарантированно недоступной БД — для проверки отказа health.

    Порт 1 вряд ли слушает PostgreSQL; соединение отклоняется быстро.
    """
    return "postgresql+psycopg://nobody:nothing@127.0.0.1:1/none"


@pytest.fixture()
def db_engine(sqlite_memory_engine):
    """Тестовая БД (по умолчанию изолированный SQLite in-memory).

    Единственное место, где используется SQLite: изолированные unit-тесты,
    которым не нужен настоящий PostgreSQL. Интеграционные тесты БД должны
    переопределять эту фикстуру реальным engine PostgreSQL (CI).
    """
    return sqlite_memory_engine


@pytest.fixture()
def sqlite_memory_engine():
    """Отдельный in-memory SQLite engine для unit-тестов схемы."""
    from sqlalchemy import create_engine

    from hr_manager.db.schema import Base

    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    yield engine
    Base.metadata.drop_all(engine)
    engine.dispose()
