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
``HRMGR_PILOT_LOCAL_TRUSTED``  phase 12 loopback-pilot profile: production
                           checks stay enforced, but the session cookies are
                           allowed to be non-Secure over ``http://127.0.0.1``
                           and the API rejects every request whose Host is
                           not a loopback name (see app/host_guard.py). Only
                           valid with APP_ENV=production (or the test fixtures)
                           and APP_DEBUG=false; the Compose profile must
                           publish ONLY on 127.0.0.1.
``LOGIN_RATE_LIMIT``       max login attempts per IP per window
``LOGIN_RATE_WINDOW_SECONDS``  sliding window length for the login limiter
``LOGIN_MAX_FAILURES``     consecutive failed logins before an account is locked
``LOGIN_LOCK_MINUTES``     account lock duration after too many failures
``BOOTSTRAP_ADMIN_USERNAME`` / ``BOOTSTRAP_ADMIN_PASSWORD`` /
``BOOTSTRAP_ADMIN_FULL_NAME``  initial administrator (created once, when the
                           user table is empty; safe development default in
                           non-production, never used implicitly in production;
                           an empty BOOTSTRAP_ADMIN_PASSWORD explicitly disables
                           bootstrap — phase 12 creates the single pilot owner
                           via the authenticated first-run pairing instead)
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
``TELEGRAM_ENABLED``       enable the Telegram Bot API channel (default false)
``TELEGRAM_BOT_TOKEN``     bot token, secret, required when enabled
``TELEGRAM_BOT_USERNAME``  public bot username for t.me deep links
``TELEGRAM_API_BASE_URL``  Bot API base URL (default https://api.telegram.org)
``TELEGRAM_TIMEOUT_S``     HTTP timeout for Bot API calls (default 10)
``TELEGRAM_LINK_TTL_MINUTES``  linking-token lifetime (default 15)
``SMTP_ENABLED``           enable the SMTP channel (default false)
``SMTP_HOST``/``SMTP_PORT``/``SMTP_ENCRYPTION`` (none|starttls|tls)
``SMTP_USERNAME``/``SMTP_PASSWORD``  auth pair (password is a secret)
``SMTP_FROM_ADDRESS``/``SMTP_FROM_NAME``  envelope sender identity
``SMTP_TIMEOUT_S``         socket timeout for SMTP (default 10)
``SMTP_MAX_MESSAGE_BYTES`` rendered message size cap (default 524288)
``EMAIL_VERIFICATION_TTL_HOURS``  address-confirmation lifetime (default 24)
``INTEGRATION_RATE_LIMIT``/``INTEGRATION_RATE_WINDOW_S``  anti-spam
``CANDIDATE_EMAIL_CONFIRM_TTL_MINUTES``  candidate double-opt-in token
                           lifetime (default 1440 = 24h)
``CANDIDATE_EMAIL_CONFIRM_BASE_URL``  absolute base of the public
                           confirmation page mailed to candidates; empty
                           disables the flow (fail-closed 503)
``PUBLIC_CONFIRM_RATE_LIMIT``/``PUBLIC_CONFIRM_RATE_WINDOW_S``  per-IP
                           anti-abuse of the public confirmation endpoint
"""

from functools import lru_cache
from typing import Literal
from zoneinfo import ZoneInfo

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

# Phase 12: lifetime of a first-run pairing code before it expires (minutes).
# The installer opens the browser right after the smoke check, so a short TTL
# is safe and shrinks the abuse window of the public claim endpoint.
PILOT_PAIRING_TTL_MINUTES = 15

# Notification contour defaults (phase 8). All persisted timestamps stay
# timezone-aware UTC; these defaults describe the *display/scheduling*
# behaviour of the recipient and are validated against zoneinfo.
DEFAULT_NOTIFICATION_TIMEZONE = "Europe/Moscow"
DEFAULT_QUIET_HOURS_START = "21:00"
DEFAULT_QUIET_HOURS_END = "08:00"
DEFAULT_WORKDAYS = "1,2,3,4,5"  # ISO weekdays, Monday = 1
# Approaching-event offsets (hours before starts_at) per event type.
DEFAULT_INTERVIEW_APPROACH_HOURS = "24,1"
DEFAULT_CALL_APPROACH_HOURS = "1"

# External channels (phase 9). Both channels are OPTIONAL: the application
# keeps working with in-app notifications only. Secrets (bot token, SMTP
# password) arrive exclusively via environment/secret storage and are never
# written to git, the database, logs, metrics or API responses.
DEFAULT_TELEGRAM_API_BASE_URL = "https://api.telegram.org"
DEFAULT_TELEGRAM_TIMEOUT_S = 10.0
DEFAULT_TELEGRAM_LINK_TTL_MINUTES = 15
# Telegram Bot API rejects text longer than 4096 UTF-16 code units; the
# adapter truncates before sending and never splits one notification into
# several messages.
TELEGRAM_MAX_MESSAGE_CHARS = 4096
# Upper bound for a single Telegram API response body kept in memory.
TELEGRAM_MAX_RESPONSE_BYTES = 65_536
DEFAULT_SMTP_PORT = 587
DEFAULT_SMTP_TIMEOUT_S = 10.0
# Upper bound for one rendered email message (headers + body).
DEFAULT_SMTP_MAX_MESSAGE_BYTES = 524_288
DEFAULT_EMAIL_VERIFICATION_TTL_HOURS = 24
# Current version of the channel-consent terms. Stored with every opt-in so a
# future policy change can ask users to re-confirm explicitly.
CONSENT_POLICY_VERSION = "phase9-v1"
# Phase 10: terms version of the candidate-communication consent policy.
CANDIDATE_CONSENT_POLICY_VERSION = "phase10-v1"


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
    # Phase 12: locally-trusted pilot profile. Only the explicit opt-in makes
    # the production guard accept plain HTTP: the Windows pilot runs the full
    # production checks (no dev secrets, no debug) while its frontend is bound
    # to 127.0.0.1 only. When enabled, session cookies lose the Secure flag
    # (a browser would otherwise never send them over http://127.0.0.1) and
    # the API additionally rejects every request whose Host is not loopback
    # (see app/host_guard.py). The flag is a deployment promise, not a
    # general relaxation: development profiles never need it, test enables it
    # only in explicit fixtures, production requires it for Secure=false.
    pilot_local_trusted: bool = Field(default=False, validation_alias="HRMGR_PILOT_LOCAL_TRUSTED")

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

    # Notification contour (phase 8): delivery queue, worker and quiet hours.
    notification_default_timezone: str = Field(
        default=DEFAULT_NOTIFICATION_TIMEZONE, validation_alias="NOTIFICATION_DEFAULT_TIMEZONE"
    )
    notification_quiet_hours_start: str = Field(
        default=DEFAULT_QUIET_HOURS_START, validation_alias="NOTIFICATION_QUIET_HOURS_START"
    )
    notification_quiet_hours_end: str = Field(
        default=DEFAULT_QUIET_HOURS_END, validation_alias="NOTIFICATION_QUIET_HOURS_END"
    )
    notification_workdays: str = Field(
        default=DEFAULT_WORKDAYS, validation_alias="NOTIFICATION_WORKDAYS"
    )
    notification_interview_approach_hours: str = Field(
        default=DEFAULT_INTERVIEW_APPROACH_HOURS,
        validation_alias="NOTIFICATION_INTERVIEW_APPROACH_HOURS",
    )
    notification_call_approach_hours: str = Field(
        default=DEFAULT_CALL_APPROACH_HOURS, validation_alias="NOTIFICATION_CALL_APPROACH_HOURS"
    )
    worker_poll_interval_s: float = Field(default=2.0, validation_alias="WORKER_POLL_INTERVAL_S")
    worker_lease_seconds: int = Field(default=120, validation_alias="WORKER_LEASE_SECONDS")
    worker_max_attempts: int = Field(default=5, validation_alias="WORKER_MAX_ATTEMPTS")
    worker_backoff_base_s: float = Field(default=60.0, validation_alias="WORKER_BACKOFF_BASE_S")
    worker_backoff_cap_s: float = Field(default=3600.0, validation_alias="WORKER_BACKOFF_CAP_S")
    worker_batch_size: int = Field(default=20, validation_alias="WORKER_BATCH_SIZE")
    worker_heartbeat_interval_s: float = Field(
        default=10.0, validation_alias="WORKER_HEARTBEAT_INTERVAL_S"
    )
    worker_stale_after_s: float = Field(default=45.0, validation_alias="WORKER_STALE_AFTER_S")

    # External channels (phase 9): Telegram Bot API and universal SMTP.
    # Disabled by default; enabling requires explicit configuration.
    telegram_enabled: bool = Field(default=False, validation_alias="TELEGRAM_ENABLED")
    telegram_bot_token: str = Field(default="", validation_alias="TELEGRAM_BOT_TOKEN")
    telegram_bot_username: str = Field(default="", validation_alias="TELEGRAM_BOT_USERNAME")
    telegram_api_base_url: str = Field(
        default=DEFAULT_TELEGRAM_API_BASE_URL, validation_alias="TELEGRAM_API_BASE_URL"
    )
    telegram_timeout_s: float = Field(
        default=DEFAULT_TELEGRAM_TIMEOUT_S, validation_alias="TELEGRAM_TIMEOUT_S"
    )
    telegram_link_ttl_minutes: int = Field(
        default=DEFAULT_TELEGRAM_LINK_TTL_MINUTES, validation_alias="TELEGRAM_LINK_TTL_MINUTES"
    )
    smtp_enabled: bool = Field(default=False, validation_alias="SMTP_ENABLED")
    smtp_host: str = Field(default="", validation_alias="SMTP_HOST")
    smtp_port: int = Field(default=DEFAULT_SMTP_PORT, validation_alias="SMTP_PORT")
    smtp_encryption: Literal["none", "starttls", "tls"] = Field(
        default="starttls", validation_alias="SMTP_ENCRYPTION"
    )
    smtp_username: str = Field(default="", validation_alias="SMTP_USERNAME")
    smtp_password: str = Field(default="", validation_alias="SMTP_PASSWORD")
    smtp_from_address: str = Field(default="", validation_alias="SMTP_FROM_ADDRESS")
    smtp_from_name: str = Field(default="HR Manager", validation_alias="SMTP_FROM_NAME")
    smtp_timeout_s: float = Field(default=DEFAULT_SMTP_TIMEOUT_S, validation_alias="SMTP_TIMEOUT_S")
    smtp_max_message_bytes: int = Field(
        default=DEFAULT_SMTP_MAX_MESSAGE_BYTES, validation_alias="SMTP_MAX_MESSAGE_BYTES"
    )
    email_verification_ttl_hours: int = Field(
        default=DEFAULT_EMAIL_VERIFICATION_TTL_HOURS,
        validation_alias="EMAIL_VERIFICATION_TTL_HOURS",
    )
    # Anti-spam for linking/verification/test endpoints (per user, per action).
    integration_rate_limit: int = Field(default=10, validation_alias="INTEGRATION_RATE_LIMIT")
    integration_rate_window_s: int = Field(
        default=300, validation_alias="INTEGRATION_RATE_WINDOW_S"
    )
    # Phase 10: anti-spam for manual candidate-message sends (per user).
    candidate_message_rate_limit: int = Field(
        default=20, validation_alias="CANDIDATE_MESSAGE_RATE_LIMIT"
    )
    candidate_message_rate_window_s: int = Field(
        default=60, validation_alias="CANDIDATE_MESSAGE_RATE_WINDOW_S"
    )
    # Phase 10: hours before an interview at which the candidate reminder is
    # queued (comma-separated list, same shape as the internal offsets).
    candidate_interview_reminder_hours: str = Field(
        default="24", validation_alias="CANDIDATE_INTERVIEW_REMINDER_HOURS"
    )
    # Phase 10: TTL of a candidate Telegram invitation token (minutes).
    candidate_telegram_link_ttl_minutes: int = Field(
        default=60, validation_alias="CANDIDATE_TELEGRAM_LINK_TTL_MINUTES"
    )
    # Phase 10 rework: TTL of a candidate email double-opt-in token (minutes).
    candidate_email_confirm_ttl_minutes: int = Field(
        default=1440, validation_alias="CANDIDATE_EMAIL_CONFIRM_TTL_MINUTES"
    )
    # Phase 10 rework: absolute base of the public confirmation URL mailed to
    # the candidate (e.g. https://hr.example.com). The link is built as
    # {base}/candidates/email/confirm?token=... — the backend serves that
    # page itself, so this must be the address the candidate's browser can
    # reach. Empty disables the initiation endpoint (503, fail-closed).
    candidate_email_confirm_base_url: str = Field(
        default="", validation_alias="CANDIDATE_EMAIL_CONFIRM_BASE_URL"
    )
    # Phase 10 rework: rate limit of the PUBLIC confirmation endpoint
    # (per client IP — the candidate is not authenticated).
    public_confirm_rate_limit: int = Field(default=20, validation_alias="PUBLIC_CONFIRM_RATE_LIMIT")
    public_confirm_rate_window_s: int = Field(
        default=3600, validation_alias="PUBLIC_CONFIRM_RATE_WINDOW_S"
    )

    def candidate_reminder_hours(self) -> list[float]:
        """Parsed reminder offsets (hours), ascending, deduplicated."""
        values: list[float] = []
        for part in self.candidate_interview_reminder_hours.split(","):
            part = part.strip()
            if not part:
                continue
            value = float(part)
            if value < 0:
                raise ValueError("CANDIDATE_INTERVIEW_REMINDER_HOURS must be non-negative")
            if value not in values:
                values.append(value)
        return sorted(values)

    @model_validator(mode="after")
    def _validate_candidate_settings(self) -> "Settings":
        if self.candidate_message_rate_limit < 1:
            raise ValueError("CANDIDATE_MESSAGE_RATE_LIMIT must be at least 1")
        if self.candidate_message_rate_window_s < 1:
            raise ValueError("CANDIDATE_MESSAGE_RATE_WINDOW_S must be at least 1")
        if self.candidate_telegram_link_ttl_minutes < 5:
            raise ValueError("CANDIDATE_TELEGRAM_LINK_TTL_MINUTES must be at least 5")
        if self.candidate_email_confirm_ttl_minutes < 5:
            raise ValueError("CANDIDATE_EMAIL_CONFIRM_TTL_MINUTES must be at least 5")
        base = self.candidate_email_confirm_base_url.strip()
        if base:
            if not base.startswith(("http://", "https://")) or " " in base:
                raise ValueError("CANDIDATE_EMAIL_CONFIRM_BASE_URL must be an absolute http(s) URL")
            self.candidate_email_confirm_base_url = base.rstrip("/")
        if self.public_confirm_rate_limit < 1:
            raise ValueError("PUBLIC_CONFIRM_RATE_LIMIT must be at least 1")
        if self.public_confirm_rate_window_s < 1:
            raise ValueError("PUBLIC_CONFIRM_RATE_WINDOW_S must be at least 1")
        self.candidate_reminder_hours()  # raises on garbage input
        return self

    @property
    def is_production(self) -> bool:
        return self.environment == "production"

    @property
    def session_cookie_is_secure(self) -> bool:
        """Effective Secure flag for session/CSRF cookies.

        The loopback pilot (``HRMGR_PILOT_LOCAL_TRUSTED``) is the ONLY way to
        run a production deployment over plain ``http://127.0.0.1``: browsers
        drop ``Secure`` cookies on non-HTTPS origins, so the pilot profile
        turns the flag off deliberately while keeping every other production
        check active. An explicit ``SESSION_COOKIE_SECURE`` still wins.
        """
        if self.session_cookie_secure is not None:
            return self.session_cookie_secure
        if self.pilot_local_trusted:
            return False
        return self.is_production

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

        # Phase 12: the loopback-pilot opt-in must never weaken a stack that
        # is reachable beyond this machine. It is a deliberate production/
        # test profile (the pilot overlay publishes ONLY 127.0.0.1); it
        # forbids debug output and a contradictory forced-Secure cookie.
        if self.pilot_local_trusted:
            if self.debug:
                problems.append("APP_DEBUG must be false when HRMGR_PILOT_LOCAL_TRUSTED is enabled")
            if self.environment == "development":
                problems.append(
                    "HRMGR_PILOT_LOCAL_TRUSTED is a production/test profile switch; "
                    "development stacks already run without the Secure cookie"
                )
            if self.session_cookie_secure is True:
                problems.append(
                    "SESSION_COOKIE_SECURE=true contradicts HRMGR_PILOT_LOCAL_TRUSTED "
                    "(the pilot is served over http://127.0.0.1)"
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
            if self.session_cookie_secure is False and not self.pilot_local_trusted:
                problems.append(
                    "SESSION_COOKIE_SECURE must not be disabled in production; a loopback "
                    "pilot uses the explicit HRMGR_PILOT_LOCAL_TRUSTED profile instead"
                )
            # The bootstrap administrator must never get an implicit weak
            # password in production. An EMPTY password explicitly disables
            # bootstrap creation (used by the phase-12 pilot profile, where the
            # single owner is created through the authenticated first-run
            # pairing instead): the table then stays empty and no weak account
            # exists.
            if self.bootstrap_admin_password == DEVELOPMENT_BOOTSTRAP_ADMIN_PASSWORD:
                problems.append(
                    "BOOTSTRAP_ADMIN_PASSWORD must be set to a strong value in production "
                    "(or create the administrator with 'python -m app.cli create-admin')"
                )
            if (
                self.bootstrap_admin_password
                and len(self.bootstrap_admin_password) < MIN_PASSWORD_LENGTH
            ):
                problems.append(
                    f"BOOTSTRAP_ADMIN_PASSWORD must be at least {MIN_PASSWORD_LENGTH} characters "
                    "in production (or be set to the empty string to disable bootstrap)"
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

        # Notification contour invariants (phase 8). These defaults are safe,
        # but a misconfigured deployment must fail fast instead of silently
        # delivering at wrong times or never delivering at all.
        if not _is_iana_timezone(self.notification_default_timezone):
            problems.append(
                "NOTIFICATION_DEFAULT_TIMEZONE must be a valid IANA timezone "
                f"(got {self.notification_default_timezone!r})"
            )
        for name, value in (
            ("NOTIFICATION_QUIET_HOURS_START", self.notification_quiet_hours_start),
            ("NOTIFICATION_QUIET_HOURS_END", self.notification_quiet_hours_end),
        ):
            if not _is_hh_mm(value):
                problems.append(f"{name} must be HH:MM (got {value!r})")
        if not _is_workday_list(self.notification_workdays):
            problems.append(
                "NOTIFICATION_WORKDAYS must be a comma-separated list of ISO weekdays "
                f"1..7 (got {self.notification_workdays!r})"
            )
        for name, value in (
            ("NOTIFICATION_INTERVIEW_APPROACH_HOURS", self.notification_interview_approach_hours),
            ("NOTIFICATION_CALL_APPROACH_HOURS", self.notification_call_approach_hours),
        ):
            if not _is_hour_list(value):
                problems.append(
                    f"{name} must be a comma-separated list of non-negative hours (got {value!r})"
                )
        if self.worker_lease_seconds < 5:
            problems.append("WORKER_LEASE_SECONDS must be at least 5")
        if self.worker_max_attempts < 1:
            problems.append("WORKER_MAX_ATTEMPTS must be at least 1")
        if self.worker_batch_size < 1:
            problems.append("WORKER_BATCH_SIZE must be at least 1")

        # External channels (phase 9). Misconfiguration must fail fast; the
        # messages below never echo secret values.
        if self.telegram_enabled:
            if not self.telegram_bot_token:
                problems.append("TELEGRAM_BOT_TOKEN is required when TELEGRAM_ENABLED=true")
            if not self.telegram_bot_username:
                problems.append("TELEGRAM_BOT_USERNAME is required when TELEGRAM_ENABLED=true")
        if self.telegram_api_base_url and not _is_http_base_url(self.telegram_api_base_url):
            problems.append("TELEGRAM_API_BASE_URL must be an http(s) base URL without a path")
        if self.is_production and self.telegram_api_base_url.startswith("http://"):
            problems.append("TELEGRAM_API_BASE_URL must use https in production")
        if self.telegram_timeout_s <= 0 or self.telegram_timeout_s > 120:
            problems.append("TELEGRAM_TIMEOUT_S must be within (0, 120]")
        if self.telegram_link_ttl_minutes < 1 or self.telegram_link_ttl_minutes > 24 * 60:
            problems.append("TELEGRAM_LINK_TTL_MINUTES must be within [1, 1440]")
        if self.smtp_enabled:
            if not self.smtp_host:
                problems.append("SMTP_HOST is required when SMTP_ENABLED=true")
            if not self.smtp_from_address or not _looks_like_email(self.smtp_from_address):
                problems.append(
                    "SMTP_FROM_ADDRESS must be a valid email address when SMTP_ENABLED=true"
                )
        if self.smtp_port < 1 or self.smtp_port > 65535:
            problems.append("SMTP_PORT must be within [1, 65535]")
        if self.smtp_from_address and not _looks_like_email(self.smtp_from_address):
            problems.append("SMTP_FROM_ADDRESS must be a valid email address")
        if self.smtp_timeout_s <= 0 or self.smtp_timeout_s > 120:
            problems.append("SMTP_TIMEOUT_S must be within (0, 120]")
        if self.smtp_max_message_bytes < 4096:
            problems.append("SMTP_MAX_MESSAGE_BYTES must be at least 4096")
        if self.email_verification_ttl_hours < 1 or self.email_verification_ttl_hours > 72:
            problems.append("EMAIL_VERIFICATION_TTL_HOURS must be within [1, 72]")
        if self.integration_rate_limit < 1:
            problems.append("INTEGRATION_RATE_LIMIT must be at least 1")
        if self.integration_rate_window_s < 1:
            problems.append("INTEGRATION_RATE_WINDOW_S must be at least 1")

        if problems:
            raise ValueError(
                f"invalid configuration for environment '{self.environment}': "
                + "; ".join(problems)
            )
        return self


def _is_iana_timezone(value: str) -> bool:
    """True when ``value`` names an IANA timezone known to the stdlib zoneinfo."""
    try:
        ZoneInfo(value)
    except Exception:
        return False
    return True


def _is_hh_mm(value: str) -> bool:
    if len(value) != 5 or value[2] != ":":
        return False
    try:
        hours, minutes = int(value[:2]), int(value[3:])
    except ValueError:
        return False
    return 0 <= hours <= 23 and 0 <= minutes <= 59


def _is_workday_list(value: str) -> bool:
    parts = value.split(",")
    if not parts or any(not part for part in parts):
        return False
    try:
        days = [int(part) for part in parts]
    except ValueError:
        return False
    return all(1 <= day <= 7 for day in days) and len(set(days)) == len(days)


def _is_hour_list(value: str) -> bool:
    parts = value.split(",")
    if not parts or any(not part for part in parts):
        return False
    try:
        hours = [float(part) for part in parts]
    except ValueError:
        return False
    return all(hour >= 0 for hour in hours)


def _is_http_base_url(value: str) -> bool:
    """True for ``http(s)://host[:port]`` without path, query or fragment."""
    from urllib.parse import urlsplit

    try:
        parts = urlsplit(value.strip())
    except ValueError:
        return False
    return (
        parts.scheme in ("http", "https")
        and bool(parts.hostname)
        and not (parts.path.strip("/") or parts.query or parts.fragment)
    )


def _looks_like_email(value: str) -> bool:
    """Cheap config-time email shape check (no DNS, no delivery promise)."""
    from email.utils import parseaddr

    _, address = parseaddr(value.strip())
    if "@" not in address or any(ch.isspace() or ch in "\r\n" for ch in address):
        return False
    local, _, domain = address.partition("@")
    return bool(local) and "." in domain and not domain.startswith(".")


@lru_cache
def get_settings() -> Settings:
    """Return the process-wide settings, loaded once from the environment."""
    return Settings()
