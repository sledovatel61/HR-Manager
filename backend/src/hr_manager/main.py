"""Точка входа FastAPI-приложения HR Manager.

Запуск (development):
    uvicorn hr_manager.main:app --reload
"""

from fastapi import FastAPI

from hr_manager.api.health import router as health_router
from hr_manager.core.config import get_settings
from hr_manager.core.logging import configure_logging

configure_logging()

settings = get_settings()

app = FastAPI(
    title=settings.app_name,
    version=settings.app_version,
    description="API системы подбора персонала HR Manager.",
)

# На Этапе 1 доступен только health-check; бизнес-роутеры появятся позже.
app.include_router(health_router)


@app.get("/", include_in_schema=False)
def root() -> dict[str, str]:
    """Короткая справка для удобного открытия API в браузере."""
    return {
        "service": settings.app_name,
        "health": "/health",
        "docs": "/docs",
    }
