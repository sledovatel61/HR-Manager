"""License fail-closed — pilot/production без ключа -> ошибка, без fallback из git.

Ключ приходит только через env LICENSE_PUBLIC_KEY (runtime).
Файл infra/license/public_key.b64 не используется backend'ом как fallback.
"""

import base64
import secrets

import pytest

from app.config import Settings

SQLITE_URL = "sqlite+pysqlite://"
PG_URL = "postgresql+psycopg://user:pass@localhost/db"


def _valid_key() -> str:
    return base64.b64encode(secrets.token_bytes(32)).decode()


def test_pilot_without_key_fails() -> None:
    with pytest.raises(ValueError, match="LICENSE_PUBLIC_KEY must be set in pilot"):
        Settings.model_validate(
            {
                "APP_ENV": "pilot",
                "SECRET_KEY": "strong-secret-key-1234567890abcdef1234567890",
                "DATABASE_URL": PG_URL,
                "LICENSE_PUBLIC_KEY": "",
                "PILOT_BOOTSTRAP_EXCHANGE_TOKEN": "a" * 64,
            }
        )


def test_pilot_with_whitespace_key_fails() -> None:
    with pytest.raises(ValueError, match="LICENSE_PUBLIC_KEY must be set in pilot"):
        Settings.model_validate(
            {
                "APP_ENV": "pilot",
                "SECRET_KEY": "strong-secret-key-1234567890abcdef1234567890",
                "DATABASE_URL": PG_URL,
                "LICENSE_PUBLIC_KEY": "   ",
                "PILOT_BOOTSTRAP_EXCHANGE_TOKEN": "a" * 64,
            }
        )


def test_pilot_with_valid_key_succeeds() -> None:
    key = _valid_key()
    s = Settings.model_validate(
        {
            "APP_ENV": "pilot",
            "SECRET_KEY": "strong-secret-key-1234567890abcdef1234567890",
            "DATABASE_URL": PG_URL,
            "LICENSE_PUBLIC_KEY": key,
            "PILOT_BOOTSTRAP_EXCHANGE_TOKEN": "a" * 64,
        }
    )
    assert s.license_public_key == key
    assert s.is_pilot is True


def test_pilot_with_invalid_base64_fails() -> None:
    with pytest.raises(ValueError, match="LICENSE_PUBLIC_KEY must be valid base64"):
        Settings.model_validate(
            {
                "APP_ENV": "pilot",
                "SECRET_KEY": "strong-secret-key-1234567890abcdef1234567890",
                "DATABASE_URL": PG_URL,
                "LICENSE_PUBLIC_KEY": "not-a-base64!!!",
                "PILOT_BOOTSTRAP_EXCHANGE_TOKEN": "a" * 64,
            }
        )


def test_pilot_with_wrong_length_fails() -> None:
    # 16 bytes instead of 32 -> 24 base64 chars, not 44
    short = base64.b64encode(secrets.token_bytes(16)).decode()
    assert len(short) != 44
    with pytest.raises(ValueError, match="LICENSE_PUBLIC_KEY must decode to 32 bytes"):
        Settings.model_validate(
            {
                "APP_ENV": "pilot",
                "SECRET_KEY": "strong-secret-key-1234567890abcdef1234567890",
                "DATABASE_URL": PG_URL,
                "LICENSE_PUBLIC_KEY": short,
                "PILOT_BOOTSTRAP_EXCHANGE_TOKEN": "a" * 64,
            }
        )


def test_production_without_key_fails() -> None:
    with pytest.raises(ValueError, match="LICENSE_PUBLIC_KEY must be set in production"):
        Settings.model_validate(
            {
                "APP_ENV": "production",
                "SECRET_KEY": "strong-secret-key-1234567890abcdef1234567890",
                "DATABASE_URL": PG_URL,
                "LICENSE_PUBLIC_KEY": "",
                "BOOTSTRAP_ADMIN_PASSWORD": "StrongPass123!",
            }
        )


def test_production_with_valid_key_succeeds() -> None:
    key = _valid_key()
    s = Settings.model_validate(
        {
            "APP_ENV": "production",
            "SECRET_KEY": "strong-secret-key-1234567890abcdef1234567890",
            "DATABASE_URL": PG_URL,
            "LICENSE_PUBLIC_KEY": key,
            "BOOTSTRAP_ADMIN_PASSWORD": "StrongPass123!",
        }
    )
    assert s.license_public_key == key
    assert s.is_production is True


def test_production_with_invalid_key_fails() -> None:
    with pytest.raises(ValueError, match="LICENSE_PUBLIC_KEY must be valid base64"):
        Settings.model_validate(
            {
                "APP_ENV": "production",
                "SECRET_KEY": "strong-secret-key-1234567890abcdef1234567890",
                "DATABASE_URL": PG_URL,
                "LICENSE_PUBLIC_KEY": "!!!invalid!!!",
                "BOOTSTRAP_ADMIN_PASSWORD": "StrongPass123!",
            }
        )


def test_development_without_key_succeeds() -> None:
    s = Settings.model_validate(
        {
            "APP_ENV": "development",
            "SECRET_KEY": "dev-only-secret-key-not-for-production",
            "DATABASE_URL": "postgresql+psycopg://hr_manager:hr_manager_dev_password@localhost:5432/hr_manager",
            "LICENSE_PUBLIC_KEY": "",
        }
    )
    assert s.license_public_key == ""
    assert s.environment == "development"


def test_test_without_key_succeeds() -> None:
    s = Settings.model_validate(
        {
            "APP_ENV": "test",
            "SECRET_KEY": "test-secret",
            "DATABASE_URL": SQLITE_URL,
            "LICENSE_PUBLIC_KEY": "",
        }
    )
    assert s.license_public_key == ""
    assert s.environment == "test"


def test_test_with_valid_key_succeeds() -> None:
    key = _valid_key()
    s = Settings.model_validate(
        {
            "APP_ENV": "test",
            "SECRET_KEY": "test-secret",
            "DATABASE_URL": SQLITE_URL,
            "LICENSE_PUBLIC_KEY": key,
        }
    )
    assert s.license_public_key == key


def test_test_with_invalid_key_fails_even_in_test() -> None:
    # In test env, invalid key should still be rejected if provided
    with pytest.raises(ValueError, match="LICENSE_PUBLIC_KEY must be valid base64"):
        Settings.model_validate(
            {
                "APP_ENV": "test",
                "SECRET_KEY": "test-secret",
                "DATABASE_URL": SQLITE_URL,
                "LICENSE_PUBLIC_KEY": "not-base64!",
            }
        )
