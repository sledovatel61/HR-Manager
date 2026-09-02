"""Health endpoint.

``GET /health`` reports liveness of the application and readiness of its
database. It returns HTTP 200 only when the database is reachable; otherwise
it returns HTTP 503 with a ``degraded`` payload. The body never contains
connection details or credentials.
"""

from fastapi import APIRouter, Request, Response

from app import __version__
from app.db import probe_database
from app.schemas import DatabaseHealth, HealthResponse

router = APIRouter(tags=["health"])


@router.get(
    "/health",
    response_model=HealthResponse,
    summary="Service and database health",
    responses={503: {"model": HealthResponse, "description": "Application is up, database is not"}},
)
def get_health(request: Request, response: Response) -> HealthResponse:
    """Check application liveness and database connectivity."""
    settings = request.app.state.settings
    engine = request.app.state.engine

    probe = probe_database(engine)
    database_ok = probe.ok
    if not database_ok:
        response.status_code = 503

    return HealthResponse(
        status="ok" if database_ok else "degraded",
        service=settings.app_name,
        version=__version__,
        environment=settings.environment,
        checks={
            "database": DatabaseHealth(
                status="ok" if database_ok else "error",
                latency_ms=probe.latency_ms,
            )
        },
    )
