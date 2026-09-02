"""Тесты безопасной конфигурации.

Гарантируют, что приложение невозможно случайно запустить в production
с дефолтными/пустыми секретами: конфиг обязан валидироваться до старта.
"""

import pytest
from pydantic import ValidationError

from hr_manager.core.config import (
    DEV_PASSWORD_PLACEHOLDER,
    MIN_SECRET_KEY_LENGTH,
    Settings,
)


def test_production_requires_long_secret_key() -> None:
    """production + пустой/короткий SECRET_KEY -> ошибка валидации."""
    with pytest.raises(ValidationError, match="SECRET_KEY"):
        Settings(app_env="production", secret_key="short", database_url="postgresql+psycopg://u:p@db:5432/d")


def test_production_rejects_dev_password_placeholder() -> None:
    """production не должен содержать dev-пароль из docker-compose."""
    url = f"postgresql+psycopg://hr_manager:{DEV_PASSWORD_PLACEHOLDER}@db:5432/hr_manager"
    with pytest.raises(ValidationError, match="небезопасна"):
        Settings(
            app_env="production",
            secret_key="x" * MIN_SECRET_KEY_LENGTH,
            database_url=url,
        )


def test_development_allows_empty_secret() -> None:
    """development может работать без SECRET_KEY (локальный dev)."""
    settings = Settings(app_env="development")
    assert settings.is_production() is False


def test_development_with_placeholder_password_is_allowed() -> None:
    """dev-пароль из compose допустим только вне production."""
    settings = Settings(
        app_env="development",
        database_url=f"postgresql+psycopg://hr_manager:{DEV_PASSWORD_PLACEHOLDER}@localhost:5432/hr_manager",
    )
    assert settings.database_backend == "postgresql"
