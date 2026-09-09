"""HR Manager backend entry point.

Run locally::

    uvicorn app.main:app --host 0.0.0.0 --port 8000
"""

import logging
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from sqlalchemy import Engine
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.types import ASGIApp, Receive, Scope, Send

from app import __version__, metrics
from app.bootstrap import bootstrap_admin
from app.config import Settings, get_settings
from app.db import bind_session_factory, build_engine
from app.host_guard import REJECTION_DETAIL, is_loopback_host
from app.routers import (
    analytics,
    audit,
    auth,
    candidate_messages,
    candidates,
    document_rules,
    documents,
    events,
    first_run,
    health,
    integrations,
    notifications,
    ops,
    preferences,
    reminders,
    setup,
    users,
)

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


class MetricsMiddleware(BaseHTTPMiddleware):
    """Record aggregate request metrics (route template + status class only)."""

    async def dispatch(self, request: Request, call_next: object) -> Response:
        started = time.monotonic()
        try:
            response = await call_next(request)  # type: ignore[operator]
        except Exception:
            metrics.observe_request(request, _exception_response(), time.monotonic() - started)
            raise
        metrics.observe_request(request, response, time.monotonic() - started)
        return response


def _exception_response() -> Response:
    return Response(status_code=500)


class PilotHostGuard:
    """Pure-ASGI guard for the loopback pilot profile (phase 12).

    Installed ONLY when ``HRMGR_PILOT_LOCAL_TRUSTED`` is on. It rejects any
    request whose ``Host`` header is not a loopback name, so the plain-HTTP
    pilot cannot be reached via DNS rebinding or a non-loopback hostname.
    Requests that pass are forwarded unchanged. The guard never logs the
    header value.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        headers = {
            key.decode("latin-1").lower(): value.decode("latin-1")
            for key, value in scope.get("headers", [])
        }
        if is_loopback_host(headers.get("host")):
            await self.app(scope, receive, send)
            return
        response = JSONResponse(status_code=403, content={"detail": REJECTION_DETAIL})
        await response(scope, receive, send)


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

    app.add_middleware(MetricsMiddleware)
    app.add_middleware(SecurityHeadersMiddleware)
    if app_settings.pilot_local_trusted:
        # Added last on purpose: Starlette runs middleware in reverse
        # registration order, so the guard evaluates before everything else
        # and a non-loopback Host never reaches a route, the metrics or the
        # session layer.
        app.add_middleware(PilotHostGuard)

    app.include_router(health.router)
    app.include_router(ops.router)
    app.include_router(auth.router)
    app.include_router(users.router)
    app.include_router(audit.router)
    app.include_router(candidates.router)
    app.include_router(documents.router)
    app.include_router(document_rules.router)
    app.include_router(events.router)
    app.include_router(candidate_messages.router)
    app.include_router(candidate_messages.public_router)
    app.include_router(analytics.router)
    app.include_router(notifications.router)
    app.include_router(integrations.router)
    app.include_router(reminders.router)
    app.include_router(preferences.router)
    app.include_router(setup.router)
    app.include_router(first_run.router)
    return app


app = create_app()
