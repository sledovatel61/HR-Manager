"""Health-check приложения и базы данных.

GET /health — публичный эндпоинт без бизнес-данных. Возвращает 200
только когда приложение живо И база данных доступна; в противном случае
возвращает 503 с описанием причины.
"""

import logging
from typing import Any

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from sqlalchemy.exc import SQLAlchemyError

from hr_manager.core.config import get_settings
from hr_manager.core.db import check_database, create_db_engine

logger = logging.getLogger(__name__)

router = APIRouter(tags=["system"])


@router.get(
    "/health",
    summary="Проверка состояния приложения и БД",
    responses={
        200: {"description": "Приложение и БД доступны."},
        503: {"description": "Приложение или БД недоступны."},
    },
)
def health() -> JSONResponse:
    """Возвращает 200, только если PostgreSQL доступен, иначе 503."""
    settings = get_settings()
    payload: dict[str, Any] = {
        "status": "ok",
        "app": settings.app_name,
        "version": settings.app_version,
        "environment": settings.app_env,
    }

    try:
        engine = create_db_engine(settings)
        check_database(engine)
    except (SQLAlchemyError, OSError) as exc:
        logger.error("Database health check failed: %s", exc)
        payload["status"] = "error"
        payload["database"] = "unavailable"
        return JSONResponse(status_code=503, content=payload)

    payload["database"] = "ok"
    return JSONResponse(status_code=200, content=payload)
