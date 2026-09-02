"""Схема ответа endpoint /health."""

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel


class ComponentStatus(StrEnum):
    """Состояние отдельного компонента."""

    UP = "up"
    DOWN = "down"


class OverallStatus(StrEnum):
    """Общее состояние приложения."""

    OK = "ok"
    DEGRADED = "degraded"


class HealthResponse(BaseModel):
    """Состояние приложения и его критичных зависимостей.

    Поля нарочно минимальны: health endpoint доступен без аутентификации
    и не должен раскрывать внутренние детали (строки подключения,
    текст ошибок БД и т.п.).
    """

    status: OverallStatus
    database: ComponentStatus
    version: str
    checked_at: datetime
