"""HR Manager backend entry point.

Run locally::

    uvicorn app.main:app --host 0.0.0.0 --port 8000
"""

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from sqlalchemy import Engine
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from app import __version__
from app.bootstrap import bootstrap_admin
from app.config import Settings, get_settings
from app.db import bind_session_factory, build_engine
from app.routers import audit, auth, health, users

logger = logging.getLogger(__name__)

# Baseline security headers applied to every API response. TLS itself is
# terminated by the reverse proxy (roadmap phase 7); these headers harden the
# browser-side handling of any response.
_SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
    "Cache-Control": "no-store",
}


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Attach baseline security headers to every response."""

    async def dispatch(self, request: Request, call_next: object) -> Response:
        # starlette's BaseHTTPMiddleware expects an awaitable callable.
        response = await call_next(request)  # type: ignore[operator]
        for name, value in _SECURITY_HEADERS.items():
            response.headers.setdefault(name, value)
        return response


def create_app(settings: Settings | None = None, engine: Engine | None = None) -> FastAPI:
    """Build the FastAPI application.

    ``settings`` construction (including the production safety checks) happens
    before the app object exists, so an unsafe production configuration fails
    fast and the application never starts. Tests may inject a pre-built
    ``engine`` (e.g. in-memory SQLite for isolated unit tests).
    """
    app_settings = settings or get_settings()
    app_engine = engine or build_engine(app_settings)
    bind_session_factory(app_engine)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        # Create the initial administrator on a fresh database. Skipped in the
        # test environment: tests build their own deterministic fixtures.
        if app_settings.environment != "test":
            from app.db import SessionLocal

            with SessionLocal() as db:
                try:
                    bootstrap_admin(db, app_settings)
                except Exception:  # never let bootstrap crash the API process
                    logger.exception("administrator bootstrap failed")
        yield
        app_engine.dispose()

    app = FastAPI(title="HR Manager API", version=__version__, lifespan=lifespan)
    app.state.settings = app_settings
    app.state.engine = app_engine

    app.add_middleware(SecurityHeadersMiddleware)

    app.include_router(health.router)
    app.include_router(auth.router)
    app.include_router(users.router)
    app.include_router(audit.router)
    return app


app = create_app()
