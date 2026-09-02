"""HR Manager backend entry point.

Run locally::

    uvicorn app.main:app --host 0.0.0.0 --port 8000
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from sqlalchemy import Engine

from app import __version__
from app.config import Settings, get_settings
from app.db import build_engine
from app.routers import health


def create_app(settings: Settings | None = None, engine: Engine | None = None) -> FastAPI:
    """Build the FastAPI application.

    ``settings`` construction (including the production safety checks) happens
    before the app object exists, so an unsafe production configuration fails
    fast and the application never starts. Tests may inject a pre-built
    ``engine`` (e.g. in-memory SQLite for isolated unit tests).
    """
    app_settings = settings or get_settings()
    app_engine = engine or build_engine(app_settings)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        yield
        app_engine.dispose()

    app = FastAPI(title="HR Manager API", version=__version__, lifespan=lifespan)
    app.state.settings = app_settings
    app.state.engine = app_engine
    app.include_router(health.router)
    return app


app = create_app()
