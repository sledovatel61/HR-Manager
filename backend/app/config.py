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
``RELEASE_SHA``            full git SHA of the running release (reported by
                           the ops status endpoint; injected by CI/deploy)
``BACKUP_DIR``             directory holding encrypted backups and state
``BACKUP_STATE_FILE``      JSON state file for backup/monitoring signals
``BACKUP_RETENTION_DAYS``  how long backups are kept (minimum 7)
``BACKUP_MAX_AGE_HOURS``   freshness threshold used by backup check/status
``BACKUP_MIN_COPIES``      newest copies never removed by retention
``BACKUP_PGDUMP_BIN``      pg_dump executable used by backup commands
                           (defaults to ``pg_dump`` from PATH)
``BACKUP_RESTORE_BIN``     pg_restore executable used by restore drills
``BACKUP_KEY_ID``          id of the primary encryption key (stored inside
                           the backup header, used by the runner/CLI)
``BACKUP_ENC_KEY``         base64-encoded 32-byte AES-256-GCM key; production
                           rejects missing/weak/development-only values when
                           the backup contour is enabled (BACKUP_ENABLED)
``BACKUP_LEGACY_KEYS``     JSON object ``{key_id: base64 key}`` of rotated
                           keys kept only to decrypt/verify old backups
``BACKUP_DRILL_ADMIN_URL`` SQLAlchemy URL of a superuser connection able to
                           create/drop the drill database (used by restore
                           drills; without it drills fail with a clear error)
``BACKUP_DRILL_DB_NAME``   name of the temporary drill database
                           (default ``hr_manager_restore_drill``)
``BACKUP_ALEMBIC_DIR``     directory with ``alembic.ini`` used by drill
                           migrations (defaults to the backend source root)
``BACKUP_HEALTH_TIMEOUT_S`` how long the drill waits for ``/health`` to turn
                           200 on the restored database (default 90)
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

# Development-only backup encryption key (base64 of a 32-byte value). The dev
# Compose stack uses it so the backup contour can be exercised locally; a
# production environment must never run with it (see the validator below).
DEVELOPMENT_BACKUP_ENC_KEY = "ZGV2LW9ubHktYmFja3VwLWtleS0wMDAwMDAwMDAwMDA="
# The backup key is exactly 32 bytes (AES-256): 44 base64 characters.
BACKUP_KEY_BYTES = 32
BACKUP_KEY_BASE64_LENGTH = 44

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

    # Ops/release contour (roadmap phase 7).
    release_sha: str = Field(default="", validation_alias="RELEASE_SHA")
    backup_dir: str = Field(default="/var/backups/hr-manager", validation_alias="BACKUP_DIR")
    backup_state_file: str = Field(
        default="/var/backups/hr-manager/state.json", validation_alias="BACKUP_STATE_FILE"
    )
    backup_retention_days: int = Field(default=7, validation_alias="BACKUP_RETENTION_DAYS")
    backup_max_age_hours: int = Field(default=26, validation_alias="BACKUP_MAX_AGE_HOURS")
    backup_min_copies: int = Field(default=2, validation_alias="BACKUP_MIN_COPIES")
    backup_pgdump_bin: str = Field(default="pg_dump", validation_alias="BACKUP_PGDUMP_BIN")
    backup_restore_bin: str = Field(default="pg_restore", validation_alias="BACKUP_RESTORE_BIN")
    backup_min_free_mb: int = Field(default=512, validation_alias="BACKUP_MIN_FREE_MB")
    backup_enc_key: str = Field(default="", validation_alias="BACKUP_ENC_KEY")
    backup_drill_admin_url: str = Field(default="", validation_alias="BACKUP_DRILL_ADMIN_URL")
    backup_drill_db_name: str = Field(
        default="hr_manager_restore_drill", validation_alias="BACKUP_DRILL_DB_NAME"
    )
    backup_alembic_dir: str = Field(default="", validation_alias="BACKUP_ALEMBIC_DIR")
    backup_health_timeout_s: float = Field(default=90.0, validation_alias="BACKUP_HEALTH_TIMEOUT_S")

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
            # Backups are secret assets: when the backup contour is enabled in
            # production the encryption key must be a real, correctly sized,
            # non-development value. The runner re-validates the key on every
            # run; this guard fails fast at startup instead.
            if self.backup_enc_key:
                if self.backup_enc_key == DEVELOPMENT_BACKUP_ENC_KEY:
                    problems.append(
                        "BACKUP_ENC_KEY must not be the development-only backup key in production"
                    )
                elif len(self.backup_enc_key) < BACKUP_KEY_BASE64_LENGTH:
                    problems.append(
                        f"BACKUP_ENC_KEY must decode to {BACKUP_KEY_BYTES} bytes "
                        f"({BACKUP_KEY_BASE64_LENGTH} base64 characters)"
                    )

        if self.backup_retention_days < 7:
            problems.append("BACKUP_RETENTION_DAYS must be at least 7 (backup retention policy)")
        if self.backup_min_copies < 1:
            problems.append("BACKUP_MIN_COPIES must be at least 1")
        if self.backup_max_age_hours < 1:
            problems.append("BACKUP_MAX_AGE_HOURS must be at least 1")

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
