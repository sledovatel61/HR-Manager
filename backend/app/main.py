"""Точка входа FastAPI-приложения HR Manager."""

import logging

from fastapi import FastAPI

from app import __version__
from app.api.routes import health
from app.config import AppEnvironment, Settings, get_settings
from app.db.session import build_engine, session_factory

logger = logging.getLogger(__name__)


def create_app(settings: Settings | None = None) -> FastAPI:
    """Фабрика приложения.

    Параметр ``settings`` позволяет тестам и Alembic создавать приложение
    с изолированной конфигурацией, не трогая переменные окружения.
    """
    resolved = settings if settings is not None else get_settings()

    is_production = resolved.app_env == AppEnvironment.PRODUCTION
    app = FastAPI(
        title=resolved.app_name,
        version=__version__,
        # Интерактивные схемы API полезны локально, но не должны быть
        # опубликованы в production.
        docs_url=None if is_production else "/docs",
        redoc_url=None if is_production else "/redoc",
        openapi_url=None if is_production else "/openapi.json",
    )

    engine = build_engine(resolved)
    app.state.engine = engine
    app.state.session_factory = session_factory(engine)
    app.state.settings = resolved

    app.include_router(health.router)
    return app


logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
app = create_app()
