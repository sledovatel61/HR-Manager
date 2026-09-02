"""Защита от случайного подключения тестов к production-окружению.

Если переменная окружения APP_ENV=production, импорт пакета приложения
должен быть невозможен внутри тестового прогона — conftest уже бросает
исключение до импорта, а этот тест проверяет сам механизм защиты конфига.
"""

import pytest
from pydantic import ValidationError

from hr_manager.core.config import Settings

SECRET_64 = "s" * 64
SAFE_URL = "postgresql+psycopg://hr:strongpass@prod-db.internal:5432/hr_manager"


def test_settings_never_default_to_production(monkeypatch) -> None:
    """Значение APP_ENV по умолчанию не должно быть production."""
    monkeypatch.delenv("APP_ENV", raising=False)  # conftest выставляет test
    assert Settings().app_env == "development"


def test_explicit_production_needs_full_secrets() -> None:
    """Явный production без секретов отклоняется, с секретами — проходит."""
    with pytest.raises(ValidationError):
        Settings(app_env="production")

    settings = Settings(app_env="production", secret_key=SECRET_64, database_url=SAFE_URL)
    assert settings.is_production()
    assert settings.secret_key == SECRET_64


def test_production_rejects_non_postgres_backend() -> None:
    """Проверка на PostgreSQL-стек: другие диалекты в prod невозможны."""
    url = "sqlite:///./prod.db"
    with pytest.raises(Exception):
        # sqlite-URL не должен попасть в валидную production-конфигурацию:
        # если код ниже не бросит исключение, это повод пересмотреть правила.
        Settings(app_env="production", secret_key=SECRET_64, database_url=url)
