"""Production configuration safety guards.

The application must refuse to start in production without explicitly provided
secrets, so that an accidental deployment with defaults fails fast and loudly.
"""

import pytest
from pydantic import ValidationError

from app.config import (
    DEVELOPMENT_BOOTSTRAP_ADMIN_PASSWORD,
    DEVELOPMENT_SECRET_KEY,
    Settings,
)

BASE_PRODUCTION_ENV = {
    "APP_ENV": "production",
    "APP_DEBUG": "false",
    "SECRET_KEY": "x" * 48,
    "DATABASE_URL": "postgresql+psycopg://app:strong-pass@db:5432/hr_manager",
    "BOOTSTRAP_ADMIN_PASSWORD": "Strong-Bootstrap-Pass-1",
}


def test_production_accepts_fully_configured_secrets() -> None:
    settings = Settings.model_validate(BASE_PRODUCTION_ENV)
    assert settings.environment == "production"
    assert settings.is_production
    # Cookies are Secure by default in production.
    assert settings.session_cookie_is_secure is True


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("SECRET_KEY", DEVELOPMENT_SECRET_KEY),
        ("SECRET_KEY", ""),
        ("SECRET_KEY", "too-short"),
        ("DATABASE_URL", "postgresql+psycopg://app@db:5432/hr_manager"),  # no password
        (
            "DATABASE_URL",
            "postgresql+psycopg://hr_manager:hr_manager_dev_password@localhost:5432/hr_manager",
        ),
        ("APP_DEBUG", "true"),
        ("BOOTSTRAP_ADMIN_PASSWORD", DEVELOPMENT_BOOTSTRAP_ADMIN_PASSWORD),
        ("BOOTSTRAP_ADMIN_PASSWORD", "short"),
        ("SESSION_COOKIE_SECURE", "false"),
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


def test_development_cookies_are_not_secure_by_default() -> None:
    settings = Settings.model_validate(
        {
            "APP_ENV": "development",
            "SECRET_KEY": DEVELOPMENT_SECRET_KEY,
            "DATABASE_URL": "postgresql+psycopg://hr_manager:hr_manager_dev_password@localhost:5432/hr_manager",
        }
    )
    assert settings.session_cookie_is_secure is False


def test_session_cookie_secure_can_be_forced_in_development() -> None:
    settings = Settings.model_validate(
        {
            "APP_ENV": "development",
            "SECRET_KEY": DEVELOPMENT_SECRET_KEY,
            "DATABASE_URL": "postgresql+psycopg://hr_manager:hr_manager_dev_password@localhost:5432/hr_manager",
            "SESSION_COOKIE_SECURE": "true",
        }
    )
    assert settings.session_cookie_is_secure is True


# --- phase 7: backup contour configuration ------------------------------------


def test_backup_settings_defaults_and_aliases() -> None:
    settings = Settings.model_validate(BASE_PRODUCTION_ENV)
    assert settings.backup_dir == "/var/backups/hr-manager"
    assert settings.backup_state_file == "/var/backups/hr-manager/state.json"
    assert settings.backup_retention_days == 7
    assert settings.backup_min_copies == 2
    assert settings.backup_max_age_hours == 26
    assert settings.backup_drill_db_name == "hr_manager_restore_drill"
    assert settings.backup_health_timeout_s == 90.0
    assert settings.backup_min_free_mb == 512


def test_production_rejects_development_backup_key() -> None:
    from app.config import DEVELOPMENT_BACKUP_ENC_KEY

    env = dict(BASE_PRODUCTION_ENV)
    env["BACKUP_ENC_KEY"] = DEVELOPMENT_BACKUP_ENC_KEY
    with pytest.raises(ValidationError, match="BACKUP_ENC_KEY"):
        Settings.model_validate(env)


def test_production_rejects_short_backup_key() -> None:
    env = dict(BASE_PRODUCTION_ENV)
    env["BACKUP_ENC_KEY"] = "c2hvcnQ="  # base64 of "short"
    with pytest.raises(ValidationError, match="BACKUP_ENC_KEY"):
        Settings.model_validate(env)


def test_production_accepts_real_backup_key_and_drill_settings() -> None:
    import base64

    env = dict(BASE_PRODUCTION_ENV)
    env["BACKUP_ENC_KEY"] = base64.b64encode(b"k" * 32).decode()
    env["BACKUP_DRILL_ADMIN_URL"] = "postgresql+psycopg://app:strong-pass@db:5432/postgres"
    env["BACKUP_DRILL_DB_NAME"] = "hr_manager_drill_test"
    settings = Settings.model_validate(env)
    assert (
        settings.backup_drill_admin_url == "postgresql+psycopg://app:strong-pass@db:5432/postgres"
    )
    assert settings.backup_drill_db_name == "hr_manager_drill_test"


def test_retention_below_seven_days_is_rejected() -> None:
    env = dict(BASE_PRODUCTION_ENV)
    env["BACKUP_RETENTION_DAYS"] = "6"
    with pytest.raises(ValidationError, match="BACKUP_RETENTION_DAYS"):
        Settings.model_validate(env)
