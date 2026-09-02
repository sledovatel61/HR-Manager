"""Конфигурация приложения на основе переменных окружения.

Загрузка настроек — pydantic-settings. Секреты НЕ хранятся в коде:
значения приходят из окружения (в dev — из файла .env, см. .env.example).
"""

from functools import lru_cache
from typing import Literal

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

AppEnv = Literal["development", "test", "production"]

# Заглушка dev-пароля для POSTGRES_PASSWORD. Используется ТОЛЬКО
# docker-compose локально. При APP_ENV=production backend отказывается
# стартовать с таким значением (см. validate_production).
DEV_PASSWORD_PLACEHOLDER = "change-me-local-only"

# Минимальная длина секрета приложения в production.
MIN_SECRET_KEY_LENGTH = 32


class Settings(BaseSettings):
    """Все настройки приложения.

    Параметры имеют префикс отсутствует (имена совпадают с переменными
    окружения) — это документировано в .env.example.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    app_env: AppEnv = "development"
    app_name: str = "HR Manager API"
    app_version: str = "0.1.0"

    database_url: str = Field(
        default="postgresql+psycopg://hr_manager:change-me-local-only@localhost:5432/hr_manager",
        description="URL подключения к PostgreSQL (SQLAlchemy).",
    )
    secret_key: str = Field(
        default="",
        description="Секрет приложения. Обязателен и должен быть длинным в production.",
    )

    def is_production(self) -> bool:
        return self.app_env == "production"

    @model_validator(mode="after")
    def validate_production(self) -> "Settings":
        """Запрещаем запуск production с небезопасными значениями."""
        if not self.is_production():
            return self

        problems: list[str] = []
        if not self.secret_key or len(self.secret_key) < MIN_SECRET_KEY_LENGTH:
            problems.append(
                f"SECRET_KEY должен быть задан и быть не короче "
                f"{MIN_SECRET_KEY_LENGTH} символов (сейчас: "
                f"{len(self.secret_key) if self.secret_key else 0})"
            )
        if DEV_PASSWORD_PLACEHOLDER in self.database_url:
            problems.append(
                "DATABASE_URL не должен содержать локальный dev-пароль "
                f"({DEV_PASSWORD_PLACEHOLDER!r})"
            )
        if self.database_backend != "postgresql":
            problems.append(
                "production-БД может быть только PostgreSQL "
                f"(получен backend: {self.database_backend!r})"
            )
        if problems:
            raise ValueError(
                "Конфигурация production небезопасна: "
                + "; ".join(problems)
            )
        return self

    @property
    def database_backend(self) -> str:
        """Имя backend-а SQLAlchemy ('postgresql') — для проверок и тестов."""
        return self.database_url.split("+", 1)[0].split(":", 1)[0]


@lru_cache
def get_settings() -> Settings:
    """Настройки кэшируются на время жизни процесса."""
    return Settings()
