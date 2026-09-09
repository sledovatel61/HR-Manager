"""Phase 12 first-run: one-time local exchange and single pilot owner creation.

The Windows installer engine claims a one-time exchange token over loopback
and receives a short-lived ticket; the browser completes the first run with
that ticket. Only SHA-256 hashes are persisted; raw tokens/tickets are never
stored, logged or returned. The whole flow stops working as soon as the
owner exists (claim/preview/redeem require an empty ``users`` table and are
single-flight under a PostgreSQL advisory lock where available).
"""

import hashlib
import logging
import secrets
import unicodedata
from datetime import datetime, timedelta
from typing import Any, cast
from zoneinfo import ZoneInfo

from fastapi import HTTPException, Request, status
from sqlalchemy import func, select, text, update
from sqlalchemy.orm import Session

from app.audit import record_event
from app.config import DEFAULT_NOTIFICATION_TIMEZONE, Settings
from app.models import (
    AccessGrant,
    AccessGrantScope,
    AuditAction,
    BootstrapExchange,
    BootstrapTicket,
    DeliveryChannel,
    NotificationPreference,
    NotificationType,
    PilotWorkingMode,
    User,
    UserRole,
    UserSession,
)
from app.security import generate_csrf_token, hash_password, validate_password_policy
from app.utils import utc_now
from app.worker import worker_is_healthy

logger = logging.getLogger(__name__)

# The exchange token is 32 random bytes rendered URL-safe (~43 chars); the
# installer generates 64 hex chars (32 bytes). Require at least 32 bytes of
# material and no whitespace.
EXCHANGE_TOKEN_MIN_LENGTH = 43
EXCHANGE_TOKEN_MAX_LENGTH = 256
TICKET_BYTES = 32
TICKET_LENGTH = 43  # secrets.token_urlsafe(32)

# PostgreSQL transaction-scoped advisory lock key for the first-run
# single-flight section (claim and redeem must never race into two owners).
FIRST_RUN_ADVISORY_LOCK_KEY = 723_733_013

# Cyrillic -> Latin transliteration used to derive the technical username
# deterministically from the owner's surname. The user never needs to type
# it during first run; it is shown in the profile afterwards.
_TRANSLIT = {
    "а": "a",
    "б": "b",
    "в": "v",
    "г": "g",
    "д": "d",
    "е": "e",
    "ё": "e",
    "ж": "zh",
    "з": "z",
    "и": "i",
    "й": "y",
    "к": "k",
    "л": "l",
    "м": "m",
    "н": "n",
    "о": "o",
    "п": "p",
    "р": "r",
    "с": "s",
    "т": "t",
    "у": "u",
    "ф": "f",
    "х": "kh",
    "ц": "ts",
    "ч": "ch",
    "ш": "sh",
    "щ": "shch",
    "ъ": "",
    "ы": "y",
    "ь": "",
    "э": "e",
    "ю": "yu",
    "я": "ya",
}


def hash_claim_value(value: str) -> str:
    """SHA-256 hexdigest of a raw exchange token / ticket (the only form
    that is ever persisted)."""
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def transliterate_surname(surname: str) -> str:
    """Deterministic transliteration of a Cyrillic surname for the technical
    username. Non-letter characters collapse to ``-``."""
    slug_chars: list[str] = []
    last_dash = False
    for char in unicodedata.normalize("NFKD", surname.strip().lower()):
        if char in _TRANSLIT:
            slug_chars.append(_TRANSLIT[char])
            last_dash = False
            continue
        if char.isascii() and char.isalpha():
            slug_chars.append(char)
            last_dash = False
            continue
        if char.isdigit():
            continue
        if not last_dash and slug_chars:
            slug_chars.append("-")
            last_dash = True
    slug = "".join(slug_chars).strip("-")
    return slug or "pilot"


def build_owner_username(db: Session, surname: str) -> str:
    """``owner.<transliterated surname>``; a random suffix is appended only
    in the (practically impossible, defensive) collision case."""
    base = "owner." + transliterate_surname(surname)[:48]
    username = base
    exists = db.scalar(
        select(func.count()).select_from(User).where(func.lower(User.username) == username)
    )
    if not exists:
        return username
    suffix = secrets.token_hex(2)
    return f"{base}-{suffix}"[:64]


def validate_surname(surname: str) -> str:
    """Normalize and validate the owner's surname (Russian messages)."""
    if not isinstance(surname, str):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Укажите фамилию.")
    cleaned = " ".join(surname.split())
    if not cleaned:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Фамилия обязательна.")
    if len(cleaned) > 60:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "Фамилия не должна быть длиннее 60 символов.",
        )
    for char in cleaned:
        if unicodedata.category(char).startswith("C"):
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY,
                "Фамилия содержит недопустимые символы.",
            )
        if char.isdigit():
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY,
                "Фамилия не должна содержать цифры. Используйте только буквы, "
                "пробелы, дефис или апостроф.",
            )
        if not (char.isalpha() or char in " -'"):
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY,
                "Фамилия может содержать только буквы, пробелы, дефис или апостроф.",
            )
    return cleaned


def validate_first_run_preferences(
    timezone: str, workdays: list[int], quiet_hours_start: str, quiet_hours_end: str
) -> tuple[str, list[int], str, str]:
    """Validate the preferences submitted with the first run."""
    try:
        ZoneInfo(timezone)
    except Exception:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            f"Неизвестная часовая зона IANA: {timezone}",
        ) from None
    if not workdays or any(not isinstance(day, int) or not (1 <= day <= 7) for day in workdays):
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "Рабочие дни — числа 1..7 (пн..вс).",
        )
    if len(set(workdays)) != len(workdays):
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "Рабочие дни не должны повторяться.",
        )
    for name, value in (
        ("quiet_hours_start", quiet_hours_start),
        ("quiet_hours_end", quiet_hours_end),
    ):
        if len(value) != 5 or value[2] != ":":
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY,
                f"{name} должен быть временем вида HH:MM.",
            )
        try:
            hours, minutes = int(value[:2]), int(value[3:])
        except ValueError:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY,
                f"{name} должен быть временем вида HH:MM.",
            ) from None
        if not (0 <= hours <= 23 and 0 <= minutes <= 59):
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY,
                f"{name} должен быть временем вида HH:MM.",
            )
    return timezone, sorted(workdays), quiet_hours_start, quiet_hours_end


def _users_exist(db: Session) -> bool:
    return (db.scalar(select(func.count()).select_from(User)) or 0) > 0


def _acquire_first_run_lock(db: Session) -> None:
    """Single-flight section: PostgreSQL transaction advisory lock.

    The lock is skipped when the driver is ``pg8000``: that driver is used
    for local verification against the single-connection PGlite server,
    where a blocking advisory lock would deadlock the multiplexed queue.
    Every real deployment and CI run uses psycopg against real PostgreSQL,
    so the lock (and the race it guards) is always exercised there. The
    atomic conditional-UPDATE consumption below still guarantees single-use
    semantics regardless of the lock."""
    dialect = db.get_bind().dialect
    if dialect.name == "postgresql" and dialect.driver != "pg8000":
        db.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": FIRST_RUN_ADVISORY_LOCK_KEY})


def _first_run_enabled(settings: Settings) -> bool:
    """The flow exists only when the exchange token is configured (pilot) or
    under test. Everywhere else the endpoints answer 404."""
    return settings.environment == "test" or bool(settings.pilot_bootstrap_exchange_token)


def loopback_client_ok(request: Request, settings: Settings) -> bool:
    """Defense-in-depth: first-run endpoints answer only to loopback clients.
    The frontend nginx injects ``X-Real-IP``; without the header the direct
    peer address is used. In pilot the backend has no externally published
    port at all, so any request with a loopback address is by definition a
    local process."""
    if not _first_run_enabled(settings):
        return False
    forwarded = request.headers.get("x-real-ip")
    if forwarded:
        return _is_loopback(forwarded)
    host = request.client.host if request.client else None
    if not host:
        return False
    if settings.environment == "test" and host == "testclient":
        return True  # TestClient default base URL: local test process
    return _is_loopback(host)


def _is_loopback(host: str) -> bool:
    host = host.strip().lower()
    return host == "127.0.0.1" or host == "::1" or host.startswith("127.")


def store_pilot_exchange(db: Session, settings: Settings) -> bool:
    """Startup bootstrap hook: register the SHA-256 hash of the configured
    exchange token when the user table is empty. Returns True when a row was
    registered (or already existed for this token)."""
    token = (settings.pilot_bootstrap_exchange_token or "").strip()
    if not token:
        return False
    if _users_exist(db):
        return False
    token_hash = hash_claim_value(token)
    existing = db.scalar(
        select(BootstrapExchange).where(BootstrapExchange.token_hash == token_hash)
    )
    if existing is not None:
        return True
    now = utc_now()
    db.add(
        BootstrapExchange(
            token_hash=token_hash,
            created_at=now,
            expires_at=now + timedelta(minutes=settings.pilot_exchange_ttl_minutes),
        )
    )
    db.commit()
    logger.info("pilot first-run exchange registered (hash only; no token stored)")
    return True


def claim_owner(
    db: Session,
    settings: Settings,
    *,
    token: str,
    surname: str,
    working_mode: PilotWorkingMode,
    timezone: str | None,
    client_ip: str | None,
    now: datetime | None = None,
) -> tuple[str, datetime]:
    """Claim the one-time exchange and return a short-lived ticket."""
    if not _first_run_enabled(settings):
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            "Первоначальная настройка недоступна.",
        )
    if _users_exist(db):
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "Первоначальная настройка уже завершена. Войдите в приложение.",
        )
    now = now or utc_now()
    token = (token or "").strip()
    if (
        not token
        or len(token) < EXCHANGE_TOKEN_MIN_LENGTH
        or len(token) > EXCHANGE_TOKEN_MAX_LENGTH
        or any(char.isspace() for char in token)
    ):
        record_event(
            db,
            AuditAction.PILOT_SETUP_REJECTED,
            ip_address=client_ip,
            details="claim: malformed exchange token",
            commit=True,
        )
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            "Код установки недействителен. Переустановите приложение.",
        )
    surname = validate_surname(surname)

    _acquire_first_run_lock(db)
    token_hash = hash_claim_value(token)
    row = db.scalar(select(BootstrapExchange).where(BootstrapExchange.token_hash == token_hash))
    if row is None:
        record_event(
            db,
            AuditAction.PILOT_SETUP_REJECTED,
            ip_address=client_ip,
            details="claim: unknown exchange token",
            commit=True,
        )
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            "Код установки недействителен. Переустановите приложение.",
        )
    if row.consumed_at is not None:
        record_event(
            db,
            AuditAction.PILOT_SETUP_REJECTED,
            ip_address=client_ip,
            details="claim: exchange already consumed",
            commit=True,
        )
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            "Код установки уже использован. Переустановите приложение.",
        )
    if row.expires_at <= now:
        record_event(
            db,
            AuditAction.PILOT_SETUP_REJECTED,
            ip_address=client_ip,
            details="claim: exchange expired",
            commit=True,
        )
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            "Код установки устарел. Запустите установку заново.",
        )

    ticket = secrets.token_urlsafe(TICKET_BYTES)
    ticket_row = BootstrapTicket(
        ticket_hash=hash_claim_value(ticket),
        surname=surname,
        working_mode=working_mode.value,
        timezone=timezone or DEFAULT_NOTIFICATION_TIMEZONE,
        created_at=now,
        expires_at=now + timedelta(minutes=settings.pilot_ticket_ttl_minutes),
        client_ip=client_ip,
    )
    db.add(ticket_row)
    db.flush()

    # Atomic single-use consumption: a concurrent claim with the same token
    # sees rowcount 0 and is rejected.
    consumed = cast(
        Any,
        db.execute(
            update(BootstrapExchange)
            .where(BootstrapExchange.id == row.id, BootstrapExchange.consumed_at.is_(None))
            .values(consumed_at=now, consumed_ticket_id=ticket_row.id)
        ),
    ).rowcount
    if consumed != 1:
        db.rollback()
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            "Код установки уже использован. Переустановите приложение.",
        )

    record_event(
        db,
        AuditAction.PILOT_OWNER_CLAIMED,
        ip_address=client_ip,
        details=f"working_mode={working_mode.value}",
        commit=False,
    )
    db.commit()
    return ticket, ticket_row.expires_at


def _resolve_ticket(db: Session, ticket: str, now: datetime) -> BootstrapTicket:
    ticket = (ticket or "").strip()
    if not ticket or len(ticket) != TICKET_LENGTH or any(char.isspace() for char in ticket):
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            "Ссылка первого входа недействительна.",
        )
    row = db.scalar(
        select(BootstrapTicket).where(BootstrapTicket.ticket_hash == hash_claim_value(ticket))
    )
    if row is None:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            "Ссылка первого входа недействительна.",
        )
    if row.expires_at <= now:
        raise HTTPException(
            status.HTTP_410_GONE,
            "Ссылка первого входа устарела. Запустите приложение заново и войдите.",
        )
    if row.consumed_at is not None:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "Первый вход уже завершён. Откройте страницу входа в приложение.",
        )
    return row


def preview_owner(
    db: Session,
    settings: Settings,
    *,
    ticket: str,
    now: datetime | None = None,
) -> dict:
    """What the first-run screen shows: surname, working mode, readiness."""
    if not _first_run_enabled(settings):
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            "Первоначальная настройка недоступна.",
        )
    if _users_exist(db):
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "Первоначальная настройка уже завершена. Войдите в приложение.",
        )
    now = now or utc_now()
    row = _resolve_ticket(db, ticket, now)

    from app.smtp import config_from_settings as smtp_config_from_settings
    from app.telegram import config_from_settings as telegram_config_from_settings

    return {
        "surname": row.surname,
        "working_mode": row.working_mode,
        "timezone": row.timezone,
        "readiness": {
            "backend": "ok",
            "database": "ok",
            "worker": "ok" if worker_is_healthy(db, settings=settings, now=now) else "not_ready",
            "backup": _backup_readiness(settings),
        },
        "channels": {
            "telegram": (
                "available"
                if telegram_config_from_settings(settings).is_configured
                else "not_configured"
            ),
            "email": (
                "available"
                if smtp_config_from_settings(settings).is_configured
                else "not_configured"
            ),
        },
        "full_access": True,
    }


def _backup_readiness(settings: Settings) -> str:
    """Backup contour signal for the first-run screen (mirrors the ops
    signals without exposing record details)."""
    from pathlib import Path

    from app.backup import freshness_ok, load_state

    try:
        state = load_state(Path(settings.backup_state_file))
    except Exception:
        return "not_ready"
    if state is None or state.last_backup is None:
        return "not_ready"
    _, age = freshness_ok(state, now=utc_now(), max_age_hours=settings.backup_max_age_hours)
    record = state.last_backup
    if record.status != "ok":
        return "not_ready"
    if age is not None and age > settings.backup_max_age_hours * 3600.0:
        return "stale"
    return "ok"


def redeem_owner(
    db: Session,
    settings: Settings,
    *,
    ticket: str,
    password: str,
    timezone: str,
    workdays: list[int],
    quiet_hours_start: str,
    quiet_hours_end: str,
    client_ip: str | None,
    user_agent: str | None,
    now: datetime | None = None,
) -> tuple[User, UserSession]:
    """Atomically create the single pilot owner, its grant, preferences and
    a session. Returns ``(user, session)``; the caller attaches the session
    cookies."""
    if not _first_run_enabled(settings):
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            "Первоначальная настройка недоступна.",
        )
    if _users_exist(db):
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "Первоначальная настройка уже завершена. Войдите в приложение.",
        )
    now = now or utc_now()
    try:
        validate_password_policy(password)
    except ValueError as error:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(error)) from None
    timezone, workdays, quiet_hours_start, quiet_hours_end = validate_first_run_preferences(
        timezone, workdays, quiet_hours_start, quiet_hours_end
    )

    _acquire_first_run_lock(db)
    if _users_exist(db):  # re-check inside the single-flight section
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "Первоначальная настройка уже завершена. Войдите в приложение.",
        )
    row = _resolve_ticket(db, ticket, now)

    user = User(
        username=build_owner_username(db, row.surname),
        full_name=row.surname,
        role=UserRole.ADMIN,
        working_mode=PilotWorkingMode(row.working_mode),
        password_hash=hash_password(password),
        is_active=True,
        created_at=now,
        updated_at=now,
    )
    db.add(user)
    db.flush()

    # Atomic single-use consumption (with the redeemed user id in the same
    # guarded transition): a concurrent redeem sees rowcount 0.
    consumed = cast(
        Any,
        db.execute(
            update(BootstrapTicket)
            .where(BootstrapTicket.id == row.id, BootstrapTicket.consumed_at.is_(None))
            .values(consumed_at=now, redeemed_user_id=user.id)
        ),
    ).rowcount
    if consumed != 1:
        db.rollback()
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "Первый вход уже завершён. Откройте страницу входа в приложение.",
        )

    grant = AccessGrant(
        user_id=user.id,
        scope=AccessGrantScope.PILOT_FULL_ACCESS,
        granted_by_user_id=user.id,  # first-run system action, audited below
        granted_at=now,
    )
    db.add(grant)
    db.add(
        NotificationPreference(
            user_id=user.id,
            timezone=timezone,
            quiet_hours_start=quiet_hours_start,
            quiet_hours_end=quiet_hours_end,
            workdays=workdays,
            enabled_types=sorted([member.value for member in NotificationType]),
            enabled_channels=[DeliveryChannel.IN_APP.value],
            created_at=now,
            updated_at=now,
        )
    )

    session = UserSession(
        user_id=user.id,
        csrf_token=generate_csrf_token(),
        ip_address=client_ip,
        user_agent=user_agent,
        created_at=now,
        last_seen_at=now,
        expires_at=now + timedelta(minutes=settings.session_ttl_minutes),
    )
    db.add(session)

    record_event(
        db,
        AuditAction.PILOT_OWNER_CREATED,
        actor=user,
        subject=user,
        ip_address=client_ip,
        user_agent=user_agent,
        details=f"first_run working_mode={row.working_mode}",
        commit=False,
    )
    db.commit()
    db.refresh(user)
    db.refresh(session)
    return user, session
