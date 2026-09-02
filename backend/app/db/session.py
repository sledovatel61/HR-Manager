"""Фабрика engine и зависимости сессий SQLAlchemy 2."""

from collections.abc import Iterator

from fastapi import Request
from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.config import Settings


def build_engine(settings: Settings) -> Engine:
    """Создаёт engine SQLAlchemy с безопасными параметрами по умолчанию.

    - ``pool_pre_ping`` — отбрасывает «мёртвые» соединения перед использованием;
    - для PostgreSQL задаются таймауты подключения и выполнения запроса,
      чтобы health-check не зависал при недоступной БД.
    """
    url = settings.database_url
    if url is None:  # гарантированно исключено валидатором Settings
        raise RuntimeError("Settings.database_url не сконфигурирован")

    kwargs: dict = {"pool_pre_ping": True}
    if url.startswith("postgresql"):
        timeout = settings.db_connect_timeout_seconds
        kwargs.update(
            pool_size=settings.db_pool_size,
            max_overflow=settings.db_max_overflow,
            connect_args={
                "connect_timeout": timeout,
                "options": f"-c statement_timeout={timeout * 1000}",
            },
        )
    return create_engine(url, **kwargs)


def session_factory(engine: Engine) -> sessionmaker[Session]:
    """Возвращает фабрику сессий для переданного engine."""
    return sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def get_db_session(request: Request) -> Iterator[Session]:
    """Выдаёт сессию БД на время запроса.

    Engine и фабрика сессий создаются один раз в ``create_app`` и читаются
    из ``app.state`` — это позволяет тестам подменять их, конструируя
    приложение со своими настройками.
    """
    factory: sessionmaker[Session] = request.app.state.session_factory
    with factory() as session:
        yield session
