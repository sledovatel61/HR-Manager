"""Endpoint проверки работоспособности приложения и БД."""

import logging
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, status
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app import __version__
from app.db.session import get_db_session
from app.schemas.health import ComponentStatus, HealthResponse, OverallStatus

logger = logging.getLogger(__name__)

router = APIRouter(tags=["health"])


@router.get(
    "/health",
    response_model=HealthResponse,
    responses={
        status.HTTP_200_OK: {"description": "Приложение и база данных доступны"},
        status.HTTP_503_SERVICE_UNAVAILABLE: {
            "description": "Приложение работает, но база данных недоступна",
            "model": HealthResponse,
        },
    },
)
def health(session: Session = Depends(get_db_session)) -> HealthResponse | JSONResponse:
    """Возвращает 200 только когда приложение И база данных доступны.

    При недоступной БД возвращается 503, чтобы health-check оркестраторов
    (Docker, reverse proxy, мониторинг) не считал экземпляр живым.
    Текст ошибки наружу не возвращается: endpoint публичный.
    """
    _ = session  # сессия берётся для корректного управления соединением
    try:
        session.execute(text("SELECT 1"))
        database = ComponentStatus.UP
    except SQLAlchemyError:
        logger.warning("health: база данных недоступна")
        database = ComponentStatus.DOWN

    payload = HealthResponse(
        status=OverallStatus.OK if database is ComponentStatus.UP else OverallStatus.DEGRADED,
        database=database,
        version=__version__,
        checked_at=datetime.now(UTC),
    )
    if database is ComponentStatus.DOWN:
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content=payload.model_dump(mode="json"),
        )
    return payload
