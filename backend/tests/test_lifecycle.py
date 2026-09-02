"""Application lifecycle: resources must be released on shutdown."""

from fastapi.testclient import TestClient
from sqlalchemy import Engine

from app.config import Settings
from app.main import create_app


def test_engine_is_disposed_on_application_shutdown(
    unit_settings: Settings, unit_engine: Engine
) -> None:
    """The lifespan must dispose the SQLAlchemy engine when the app stops.

    Guards against leaking database connections: FastAPI's lifespan shutdown
    must call ``engine.dispose()`` exactly once when the application ends
    (TestClient context exit). The engine instance is instrumented with a
    spy that records dispose calls and forwards to the real implementation.
    """
    dispose_calls: list[bool] = []
    real_dispose = unit_engine.dispose

    def spy_dispose() -> None:
        dispose_calls.append(True)
        real_dispose()

    unit_engine.dispose = spy_dispose  # type: ignore[assignment, method-assign]

    app = create_app(unit_settings, engine=unit_engine)

    with TestClient(app) as test_client:
        assert test_client.get("/health").status_code == 200
        assert dispose_calls == []  # nothing is disposed while the app is running

    # TestClient context exit ran the lifespan shutdown, which calls dispose().
    assert dispose_calls == [True]
