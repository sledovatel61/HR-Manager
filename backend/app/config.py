"""Application configuration.

All settings are read from environment variables only. There is no implicit
``.env`` file loading on purpose: secrets must be provided by the runtime
environment (Docker Compose, CI, or a process supervisor), never by files
that could be committed accidentally.

Environment variables
---------------------
``APP_ENV``                ``development`` | ``test`` | ``production``
``APP_DEBUG``              ``true``/``false``
``SECRET_KEY``             signing key for sessions/CSRF tokens
``DATABASE_URL``           SQLAlchemy URL (PostgreSQL in dev/prod, SQLite is
                           allowed only for isolated unit tests with APP_ENV=test)
``DB_CONNECT_TIMEOUT_SECONDS``  connection timeout used by the health probe
``SESSION_TTL_MINUTES``    idle lifetime of a user session (sliding expiration)
``SESSION_COOKIE_SECURE``  force the Secure flag on session/CSRF cookies
                           (defaults to true in production automatically)
``LOGIN_RATE_LIMIT``       max login attempts per IP per window
``LOGIN_RATE_WINDOW_SECONDS``  sliding window length for the login limiter
``LOGIN_MAX_FAILURES``     consecutive failed logins before an account is locked
``LOGIN_LOCK_MINUTES``     account lock duration after too many failures
``BOOTSTRAP_ADMIN_USERNAME`` / ``BOOTSTRAP_ADMIN_PASSWORD`` /
``BOOTSTRAP_ADMIN_FULL_NAME``  initial administrator (created once, when the
                           user table is empty; safe development default in
                           non-production, never used implicitly in production)
"""

from functools import lru_cache
from typing import Literal

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import make_url

# Development-only values. They are safe defaults for a local machine, but a
# production environment must never run with them (see the validator below).
DEVELOPMENT_SECRET_KEY = "dev-only-secret-key-not-for-production"
DEVELOPMENT_DATABASE_URL = (
    "postgresql+psycopg://hr_manager:hr_manager_dev_password@localhost:5432/hr_manager"
)
# Development-only bootstrap administrator credentials (documented and printed
# on startup in non-production environments). Production refuses to bootstrap
# a weak/implicit password.
DEVELOPMENT_BOOTSTRAP_ADMIN_USERNAME = "admin"
DEVELOPMENT_BOOTSTRAP_ADMIN_PASSWORD = "AdminAdmin123"

MIN_SECRET_KEY_LENGTH = 32

# Password policy (also enforced in app/security.py with a dedicated message).
MIN_PASSWORD_LENGTH = 12


class Settings(BaseSettings):
    """Runtime settings, validated depending on the environment."""

    model_config = SettingsConfigDict(extra="ignore", case_sensitive=False, env_file=None)

    app_name: str = Field(default="hr-manager", validation_alias="APP_NAME")
    environment: Literal["development", "test", "production"] = Field(
        default="development", validation_alias="APP_ENV"
    )
    debug: bool = Field(default=False, validation_alias="APP_DEBUG")
    secret_key: str = Field(default=DEVELOPMENT_SECRET_KEY, validation_alias="SECRET_KEY")
    database_url: str = Field(default=DEVELOPMENT_DATABASE_URL, validation_alias="DATABASE_URL")
    db_connect_timeout_seconds: float = Field(
        default=3.0, validation_alias="DB_CONNECT_TIMEOUT_SECONDS"
    )

    # Sessions.
    session_ttl_minutes: int = Field(default=30, validation_alias="SESSION_TTL_MINUTES")
    # ``None`` means "derive from environment" (true in production, false in
    # dev/test so plain-HTTP local stacks work). May be forced explicitly.
    session_cookie_secure: bool | None = Field(
        default=None, validation_alias="SESSION_COOKIE_SECURE"
    )

    # Login brute-force protection.
    login_rate_limit: int = Field(default=20, validation_alias="LOGIN_RATE_LIMIT")
    login_rate_window_seconds: int = Field(
        default=300, validation_alias="LOGIN_RATE_WINDOW_SECONDS"
    )
    login_max_failures: int = Field(default=5, validation_alias="LOGIN_MAX_FAILURES")
    login_lock_minutes: int = Field(default=15, validation_alias="LOGIN_LOCK_MINUTES")

    # Bootstrap administrator (used only when the user table is empty).
    bootstrap_admin_username: str = Field(
        default=DEVELOPMENT_BOOTSTRAP_ADMIN_USERNAME,
        validation_alias="BOOTSTRAP_ADMIN_USERNAME",
    )
    bootstrap_admin_password: str = Field(
        default=DEVELOPMENT_BOOTSTRAP_ADMIN_PASSWORD,
        validation_alias="BOOTSTRAP_ADMIN_PASSWORD",
    )
    bootstrap_admin_full_name: str = Field(
        default="Администратор системы",
        validation_alias="BOOTSTRAP_ADMIN_FULL_NAME",
    )

    @property
    def is_production(self) -> bool:
        return self.environment == "production"

    @property
    def session_cookie_is_secure(self) -> bool:
        """Effective Secure flag for session/CSRF cookies."""
        return (
            self.session_cookie_secure
            if self.session_cookie_secure is not None
            else (self.is_production)
        )

    @model_validator(mode="after")
    def _enforce_environment_rules(self) -> "Settings":
        """Reject configurations that are unsafe for the given environment."""
        problems: list[str] = []
        url = make_url(self.database_url)

        # SQLite is allowed ONLY for isolated unit tests (APP_ENV=test).
        # Development and production always use PostgreSQL.
        if self.environment != "test" and url.get_backend_name() == "sqlite":
            problems.append(
                "SQLite is not supported outside of isolated unit tests; use PostgreSQL"
            )

        if self.is_production:
            if not self.secret_key or self.secret_key == DEVELOPMENT_SECRET_KEY:
                problems.append("SECRET_KEY must be set to a non-default value in production")
            elif len(self.secret_key) < MIN_SECRET_KEY_LENGTH:
                problems.append(
                    f"SECRET_KEY must be at least {MIN_SECRET_KEY_LENGTH} characters in production"
                )
            if self.database_url == DEVELOPMENT_DATABASE_URL:
                problems.append("DATABASE_URL must not use development credentials in production")
            if not url.password:
                problems.append("DATABASE_URL must include a password in production")
            if self.debug:
                problems.append("APP_DEBUG must be false in production")
            if self.session_cookie_secure is False:
                problems.append("SESSION_COOKIE_SECURE must not be disabled in production")
            # The bootstrap administrator must never get an implicit weak
            # password in production.
            if self.bootstrap_admin_password == DEVELOPMENT_BOOTSTRAP_ADMIN_PASSWORD:
                problems.append(
                    "BOOTSTRAP_ADMIN_PASSWORD must be set to a strong value in production "
                    "(or create the administrator with 'python -m app.cli create-admin')"
                )
            if len(self.bootstrap_admin_password) < MIN_PASSWORD_LENGTH:
                problems.append(
                    f"BOOTSTRAP_ADMIN_PASSWORD must be at least {MIN_PASSWORD_LENGTH} characters "
                    "in production"
                )

        if problems:
            raise ValueError(
                f"invalid configuration for environment '{self.environment}': "
                + "; ".join(problems)
            )
        return self


@lru_cache
def get_settings() -> Settings:
    """Return the process-wide settings, loaded once from the environment."""
    return Settings()
