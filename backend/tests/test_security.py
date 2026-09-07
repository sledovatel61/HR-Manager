"""Unit tests for password hashing, password policy and bootstrap logic."""

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.bootstrap import bootstrap_admin
from app.config import (
    DEVELOPMENT_BOOTSTRAP_ADMIN_PASSWORD,
    DEVELOPMENT_BOOTSTRAP_ADMIN_USERNAME,
    DEVELOPMENT_SECRET_KEY,
    Settings,
)
from app.models import User, UserRole
from app.security import (
    WeakPasswordError,
    generate_csrf_token,
    hash_password,
    needs_rehash,
    validate_password_policy,
    verify_password,
)
from tests.conftest import FIXTURE_PASSWORD


def test_argon2id_hash_roundtrip() -> None:
    hashed = hash_password("Correct-Horse-Battery-1")
    assert hashed.startswith("$argon2id$")
    assert verify_password(hashed, "Correct-Horse-Battery-1")
    assert not verify_password(hashed, "wrong-password")


def test_hash_is_salted_and_unique() -> None:
    first = hash_password(FIXTURE_PASSWORD)
    second = hash_password(FIXTURE_PASSWORD)
    assert first != second
    assert verify_password(first, FIXTURE_PASSWORD)
    assert verify_password(second, FIXTURE_PASSWORD)


def test_verify_password_handles_malformed_hash() -> None:
    assert not verify_password("not-a-hash", FIXTURE_PASSWORD)
    assert not verify_password("", FIXTURE_PASSWORD)


def test_needs_rehash_detects_parameter_change() -> None:
    hashed = hash_password(FIXTURE_PASSWORD)
    assert needs_rehash(hashed) is False
    # A weaker legacy hash should be flagged for rehashing.
    from argon2 import PasswordHasher

    weak = PasswordHasher(time_cost=1, memory_cost=8 * 1024, parallelism=1).hash(FIXTURE_PASSWORD)
    assert needs_rehash(weak) is True


def test_csrf_tokens_are_unique() -> None:
    tokens = {generate_csrf_token() for _ in range(10)}
    assert len(tokens) == 10


@pytest.mark.parametrize(
    "password",
    [
        "short123",  # too short
        "alllettersonly",  # no digit
        "123456789012345",  # no letter
        "",  # empty
    ],
)
def test_weak_passwords_are_rejected(password: str) -> None:
    with pytest.raises(WeakPasswordError):
        validate_password_policy(password)


def test_password_equal_to_username_is_rejected() -> None:
    with pytest.raises(WeakPasswordError):
        validate_password_policy("Alice-Alice-123", username="alice-alice-123")


@pytest.mark.parametrize("password", ["Str0ng-Pass-2026", "Correct Horse 9 Staple", "Aaaaaaaaaaa1"])
def test_strong_passwords_pass(password: str) -> None:
    validate_password_policy(password)  # must not raise


def _settings(**overrides: str) -> Settings:
    # SQLite is only allowed in the test environment (config guard).
    base: dict[str, str] = {
        "APP_ENV": "test",
        "SECRET_KEY": DEVELOPMENT_SECRET_KEY,
        "DATABASE_URL": "sqlite+pysqlite://",
    }
    base.update(overrides)
    return Settings.model_validate(base)


def test_bootstrap_creates_admin_when_table_empty(db_session: Session) -> None:
    settings = _settings()
    created = bootstrap_admin(db_session, settings)
    assert created is not None
    assert created.username == DEVELOPMENT_BOOTSTRAP_ADMIN_USERNAME
    assert created.role == UserRole.ADMIN
    assert created.password_hash.startswith("$argon2id$")
    assert verify_password(created.password_hash, DEVELOPMENT_BOOTSTRAP_ADMIN_PASSWORD)


def test_bootstrap_is_idempotent(db_session: Session) -> None:
    settings = _settings()
    assert bootstrap_admin(db_session, settings) is not None
    # Second run must not create another user.
    assert bootstrap_admin(db_session, settings) is None
    count = db_session.scalar(select(func.count()).select_from(User))
    assert count == 1


def test_bootstrap_uses_configured_credentials(db_session: Session) -> None:
    settings = _settings(
        BOOTSTRAP_ADMIN_USERNAME="root",
        BOOTSTRAP_ADMIN_PASSWORD="Root-Strong-Pass-1",
        BOOTSTRAP_ADMIN_FULL_NAME="Big Boss",
    )
    created = bootstrap_admin(db_session, settings)
    assert created is not None
    assert created.username == "root"
    assert created.full_name == "Big Boss"
    assert verify_password(created.password_hash, "Root-Strong-Pass-1")


def test_production_bootstrap_refuses_default_password(db_session: Session) -> None:
    # A deployment cannot even construct production settings with the default
    # admin password (fail-fast in config, covered in test_config.py). This
    # test additionally pins the in-function guard: if production flags are on
    # and the password is still the development default, no user is created.
    settings = _settings(
        APP_ENV="production",
        SECRET_KEY="x" * 48,
        DATABASE_URL="postgresql+psycopg://u:strongpassword@db:5432/hr",
        BOOTSTRAP_ADMIN_PASSWORD="Strong-Bootstrap-Pass-1",
    )
    object.__setattr__(settings, "bootstrap_admin_password", DEVELOPMENT_BOOTSTRAP_ADMIN_PASSWORD)

    created = bootstrap_admin(db_session, settings)
    assert created is None
    assert db_session.scalar(select(func.count()).select_from(User)) == 0


def test_production_bootstrap_creates_admin_with_configured_password(
    db_session: Session,
) -> None:
    settings = _settings(
        APP_ENV="production",
        SECRET_KEY="x" * 48,
        DATABASE_URL="postgresql+psycopg://u:strongpassword@db:5432/hr",
        BOOTSTRAP_ADMIN_USERNAME="prodadmin",
        BOOTSTRAP_ADMIN_PASSWORD="Strong-Bootstrap-Pass-1",
    )
    created = bootstrap_admin(db_session, settings)
    assert created is not None
    assert created.username == "prodadmin"
    assert verify_password(created.password_hash, "Strong-Bootstrap-Pass-1")
