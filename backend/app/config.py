"""Application configuration.

All settings are read from environment variables only. There is no implicit
``.env`` file loading on purpose: secrets must be provided by the runtime
environment (Docker Compose, CI, or a process supervisor), never by files
that could be committed accidentally.

Environment variables
---------------------
``APP_ENV``                ``development`` | ``test`` | ``production``
``APP_DEBUG``              ``true``/``false``
``SECRET_KEY``             signing key for upcoming session/security features
``DATABASE_URL``           SQLAlchemy URL (PostgreSQL in dev/prod, SQLite is
                           allowed only for isolated unit tests with APP_ENV=test)
``DB_CONNECT_TIMEOUT_SECONDS``  connection timeout used by the health probe
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

MIN_SECRET_KEY_LENGTH = 32


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

    @property
    def is_production(self) -> bool:
        return self.environment == "production"

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
