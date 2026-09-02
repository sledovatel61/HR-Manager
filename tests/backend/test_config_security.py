"""Тесты защиты конфигурации.

Инвариант: production-развёртывание не может стартовать без обязательно
сконфигурированных секретов. Это защита от классической ошибки «подняли
боевую базу с паролем по умолчанию».
"""

import pytest
from app.config import AppEnvironment, Settings
from pydantic import ValidationError

STRONG_SECRET = "x" * 64
PROD_DB_URL = "postgresql+psycopg://hr_manager:secret@db.internal:5432/hr_manager"


def prod_settings(**overrides: object) -> Settings:
    kwargs: dict[str, object] = {
        "app_env": AppEnvironment.PRODUCTION,
        "secret_key": STRONG_SECRET,
        "database_url": PROD_DB_URL,
        "_env_file": None,
    }
    kwargs.update(overrides)
    return Settings(**kwargs)  # type: ignore[arg-type]


def test_production_starts_with_explicit_secure_configuration() -> None:
    settings = prod_settings()
    assert settings.database_url == PROD_DB_URL
    assert settings.secret_key == STRONG_SECRET


def test_production_refuses_to_start_without_secret_key() -> None:
    with pytest.raises(ValidationError, match="SECRET_KEY"):
        prod_settings(secret_key=None)


def test_production_refuses_weak_and_placeholder_secret_key() -> None:
    with pytest.raises(ValidationError, match="SECRET_KEY"):
        prod_settings(secret_key="change-me")
    with pytest.raises(ValidationError, match="SECRET_KEY"):
        prod_settings(secret_key="short")


def test_production_refuses_to_start_without_database_url() -> None:
    settings = prod_settings()
    assert settings.database_url is not None  # sanity-check фикстуры

    def build_without_url() -> Settings:
        return Settings(
            app_env=AppEnvironment.PRODUCTION,
            secret_key=STRONG_SECRET,
            database_url=None,
            _env_file=None,
        )

    with pytest.raises(ValidationError, match="DATABASE_URL"):
        build_without_url()


def test_production_refuses_debug_mode() -> None:
    with pytest.raises(ValidationError, match="DEBUG"):
        prod_settings(debug=True)


def test_sqlite_is_forbidden_outside_isolated_unit_tests() -> None:
    with pytest.raises(ValidationError, match="SQLite"):
        Settings(
            app_env=AppEnvironment.DEVELOPMENT,
            database_url="sqlite+pysqlite:///./local.db",
            _env_file=None,
        )
    with pytest.raises(ValidationError, match="SQLite"):
        prod_settings(database_url="sqlite+pysqlite:///./local.db")

    # APP_ENV=test — единственная среда, где SQLite разрешён
    # (изолированные unit-тесты, см. tests/backend/README.md).
    settings = Settings(
        app_env=AppEnvironment.TEST,
        database_url="sqlite+pysqlite:///:memory:",
        _env_file=None,
    )
    assert settings.database_url.startswith("sqlite")
