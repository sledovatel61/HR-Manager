"""Production configuration safety guards.

The application must refuse to start in production without explicitly provided
secrets, so that an accidental deployment with defaults fails fast and loudly.
"""

import pytest
from pydantic import ValidationError

from app.config import Settings

BASE_PRODUCTION_ENV = {
    "APP_ENV": "production",
    "APP_DEBUG": "false",
    "SECRET_KEY": "x" * 48,
    "DATABASE_URL": "postgresql+psycopg://app:strong-pass@db:5432/hr_manager",
}


def test_production_accepts_fully_configured_secrets() -> None:
    settings = Settings.model_validate(BASE_PRODUCTION_ENV)
    assert settings.environment == "production"
    assert settings.is_production


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("SECRET_KEY", "dev-only-secret-key-not-for-production"),
        ("SECRET_KEY", ""),
        ("SECRET_KEY", "too-short"),
        ("DATABASE_URL", "postgresql+psycopg://app@db:5432/hr_manager"),  # no password
        (
            "DATABASE_URL",
            "postgresql+psycopg://hr_manager:hr_manager_dev_password@localhost:5432/hr_manager",
        ),
        ("APP_DEBUG", "true"),
    ],
)
def test_production_rejects_insecure_configuration(field: str, value: str) -> None:
    env = dict(BASE_PRODUCTION_ENV)
    env[field] = value
    with pytest.raises(ValidationError):
        Settings.model_validate(env)


def test_sqlite_rejected_outside_isolated_tests() -> None:
    env = dict(BASE_PRODUCTION_ENV)
    env["DATABASE_URL"] = "sqlite:///./hr_manager.db"
    with pytest.raises(ValidationError, match="SQLite"):
        Settings.model_validate(env)
