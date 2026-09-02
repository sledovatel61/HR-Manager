"""Конфигурация backend-приложения.

Все настройки читаются из переменных окружения (и файла ``.env`` при его
наличии). В репозитории секретов нет: ``.env.example`` содержит только
примеры значений для локальной разработки.

Принципы:
- в production приложение ОБЯЗАНО получить явную безопасную конфигурацию
  (SECRET_KEY, DATABASE_URL и др.) — без неё оно не стартует;
- SQLite допустим только в изолированных unit-тестах
  (см. ``tests/backend/README.md``) и запрещён как production/development БД.
"""

from enum import StrEnum
from functools import lru_cache

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

#: URL по умолчанию для локальной разработки без пароля: для разработчика
#: поднимается PostgreSQL из docker compose, URL переопределяется через .env.
DEV_DATABASE_URL = "postgresql+psycopg://hr_manager@localhost:5432/hr_manager"

#: Секрет по умолчанию для development/test. Явно помечен как небезопасный,
#: в production такое значение отклоняется валидатором.
DEV_SECRET_KEY = "dev-only-insecure-secret-key-not-for-production"

MIN_SECRET_KEY_LENGTH = 32

#: Типовые значения-заглушки, запрещённые в production.
FORBIDDEN_SECRET_VALUES = frozenset(
    {
        DEV_SECRET_KEY,
        "change-me",
        "changeme",
        "secret",
        "password",
        "please-change-me",
    }
)


class AppEnvironment(StrEnum):
    """Среда запуска приложения."""

    DEVELOPMENT = "development"
    TEST = "test"
    PRODUCTION = "production"


class Settings(BaseSettings):
    """Настройки приложения, читаемые из окружения."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_name: str = "HR Manager"
    app_env: AppEnvironment = AppEnvironment.DEVELOPMENT
    debug: bool = False

    #: Секрет подписи сессий. В production — обязателен и проверяется на стойкость.
    secret_key: str | None = None

    #: Строка подключения SQLAlchemy. Обязательна в production.
    database_url: str | None = None

    #: Таймаут сетевых операций проверки БД в endpoint /health, секунды.
    db_connect_timeout_seconds: int = 3

    db_pool_size: int = 5
    db_max_overflow: int = 10

    @property
    def is_production(self) -> bool:
        return self.app_env == AppEnvironment.PRODUCTION

    @model_validator(mode="after")
    def validate_configuration(self) -> "Settings":
        problems: list[str] = []

        if self.database_url is None:
            if self.is_production:
                problems.append(
                    "DATABASE_URL обязателен в production: "
                    "значений по умолчанию для production не существует"
                )
            else:
                self.database_url = DEV_DATABASE_URL

        if (
            self.database_url is not None
            and self.database_url.startswith("sqlite")
            and self.app_env != AppEnvironment.TEST
        ):
            problems.append(
                "SQLite допустим только при APP_ENV=test для изолированных "
                "unit-тестов; production и development используют PostgreSQL"
            )

        if self.is_production:
            if self.debug:
                problems.append("DEBUG=true запрещён в production")
            if self.secret_key is None:
                problems.append(
                    "SECRET_KEY обязателен в production "
                    "(задайте через переменную окружения или secret storage)"
                )
            elif (
                len(self.secret_key) < MIN_SECRET_KEY_LENGTH
                or self.secret_key.lower() in FORBIDDEN_SECRET_VALUES
            ):
                problems.append(
                    f"SECRET_KEY слишком слабый: минимум {MIN_SECRET_KEY_LENGTH} "
                    "символов, значения-заглушки запрещены"
                )

        if problems:
            raise ValueError("Небезопасная или неполная конфигурация: " + "; ".join(problems))

        if self.secret_key is None:
            self.secret_key = DEV_SECRET_KEY

        return self


@lru_cache
def get_settings() -> Settings:
    """Возвращает кешированные настройки приложения."""
    return Settings()
