"""Own notification preferences (timezone, quiet hours, workdays, types, channels, consent)."""

from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from app.audit import record_event
from app.db import get_db
from app.deps import get_current_user
from app.models import AuditAction, DeliveryChannel, NotificationPreference, NotificationType, User
from app.schemas import PreferenceOut, PreferenceUpdate, TimezonesOut
from app.utils import normalize_email, utc_now

router = APIRouter(prefix="/notification-preferences", tags=["notifications"])

# Curated IANA list for the settings screen (the API also accepts any valid
# IANA zone for power users).
_COMMON_TIMEZONES = [
    "Europe/Moscow",
    "Europe/Kaliningrad",
    "Europe/Samara",
    "Asia/Yekaterinburg",
    "Asia/Omsk",
    "Asia/Novosibirsk",
    "Asia/Krasnoyarsk",
    "Asia/Irkutsk",
    "Asia/Yakutsk",
    "Asia/Vladivostok",
    "Asia/Magadan",
    "Asia/Kamchatka",
    "Europe/Minsk",
    "Europe/Kiev",
    "Asia/Almaty",
    "Asia/Tashkent",
    "Asia/Bishkek",
    "Asia/Dushanbe",
    "Asia/Yerevan",
    "Asia/Tbilisi",
    "Asia/Baku",
    "Europe/Berlin",
    "Europe/London",
    "America/New_York",
    "UTC",
]

_VALID_TYPES = {member.value for member in NotificationType}
_VALID_CHANNELS = {member.value for member in DeliveryChannel}


def _validated(payload: PreferenceUpdate) -> dict:
    try:
        ZoneInfo(payload.timezone)
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Неизвестная часовая зона IANA: {payload.timezone}",
        ) from None
    if not payload.workdays or any(not (1 <= day <= 7) for day in payload.workdays):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Рабочие дни — числа 1..7 (пн..вс).",
        )
    if len(set(payload.workdays)) != len(payload.workdays):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Рабочие дни не должны повторяться.",
        )
    for value in payload.enabled_types:
        if value not in _VALID_TYPES:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"Неизвестный тип уведомления: {value}",
            )
    for value in payload.enabled_channels:
        if value not in _VALID_CHANNELS:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"Неизвестный канал: {value}",
            )
    for name, value in (
        ("quiet_hours_start", payload.quiet_hours_start),
        ("quiet_hours_end", payload.quiet_hours_end),
    ):
        hours, minutes = int(value[:2]), int(value[3:])
        if not (0 <= hours <= 23 and 0 <= minutes <= 59):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"{name} должен быть временем вида HH:MM.",
            )

    cleaned_email: str | None = None
    if payload.email_address is not None:
        raw_email = payload.email_address.strip()
        if raw_email:
            norm = normalize_email(raw_email)
            if norm is None or "@" not in norm or "." not in norm.split("@")[-1]:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail="Некорректный адрес электронной почты.",
                )
            cleaned_email = norm

    return {
        "timezone": payload.timezone,
        "quiet_hours_start": payload.quiet_hours_start,
        "quiet_hours_end": payload.quiet_hours_end,
        "workdays": sorted(payload.workdays),
        "enabled_types": sorted(set(payload.enabled_types)),
        "enabled_channels": sorted(set(payload.enabled_channels)),
        "email_address": cleaned_email,
        "email_opt_in": payload.email_opt_in,
        "email_consent_granted": payload.email_consent_granted,
        "telegram_opt_in": payload.telegram_opt_in,
        "telegram_consent_granted": payload.telegram_consent_granted,
    }


def _defaults() -> dict:
    from app.config import DEFAULT_QUIET_HOURS_END, DEFAULT_QUIET_HOURS_START, DEFAULT_WORKDAYS

    return {
        "timezone": None,
        "quiet_hours_start": DEFAULT_QUIET_HOURS_START,
        "quiet_hours_end": DEFAULT_QUIET_HOURS_END,
        "workdays": [int(day) for day in DEFAULT_WORKDAYS.split(",")],
        "enabled_types": sorted(_VALID_TYPES),
        "enabled_channels": [DeliveryChannel.IN_APP.value],
        "initialized": False,
        "telegram_chat_id": None,
        "telegram_username": None,
        "telegram_linked_at": None,
        "telegram_opt_in": False,
        "telegram_consent_at": None,
        "telegram_consent_source": None,
        "telegram_consent_policy_version": None,
        "email_address": None,
        "email_opt_in": False,
        "email_consent_at": None,
        "email_consent_source": None,
        "email_consent_policy_version": None,
        "channel_health": None,
        "telegram_bot_username": None,
    }


@router.get("", response_model=PreferenceOut, summary="My notification preferences")
def get_preferences(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> PreferenceOut:
    settings = request.app.state.settings
    row = db.get(NotificationPreference, user.id)
    if row is None:
        defaults = _defaults()
        defaults["timezone"] = settings.notification_default_timezone
        defaults["telegram_bot_username"] = settings.telegram_bot_username or None
        return PreferenceOut(**defaults)
    return PreferenceOut(
        timezone=row.timezone,
        quiet_hours_start=row.quiet_hours_start,
        quiet_hours_end=row.quiet_hours_end,
        workdays=row.workdays,
        enabled_types=row.enabled_types,
        enabled_channels=row.enabled_channels,
        initialized=True,
        telegram_chat_id=row.telegram_chat_id,
        telegram_username=row.telegram_username,
        telegram_linked_at=row.telegram_linked_at,
        telegram_opt_in=row.telegram_opt_in,
        telegram_consent_at=row.telegram_consent_at,
        telegram_consent_source=row.telegram_consent_source,
        telegram_consent_policy_version=row.telegram_consent_policy_version,
        email_address=row.email_address,
        email_opt_in=row.email_opt_in,
        email_consent_at=row.email_consent_at,
        email_consent_source=row.email_consent_source,
        email_consent_policy_version=row.email_consent_policy_version,
        channel_health=row.channel_health,
        telegram_bot_username=settings.telegram_bot_username or None,
    )


@router.put("", response_model=PreferenceOut, summary="Save my notification preferences")
def put_preferences(
    payload: PreferenceUpdate,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> PreferenceOut:
    settings = request.app.state.settings
    values = _validated(payload)
    now = utc_now()
    row = db.get(NotificationPreference, user.id)
    if row is None:
        row = NotificationPreference(
            user_id=user.id,
            timezone=values["timezone"],
            quiet_hours_start=values["quiet_hours_start"],
            quiet_hours_end=values["quiet_hours_end"],
            workdays=values["workdays"],
            enabled_types=values["enabled_types"],
            enabled_channels=values["enabled_channels"],
            email_address=values["email_address"],
            created_at=now,
        )
        db.add(row)
    else:
        row.timezone = values["timezone"]
        row.quiet_hours_start = values["quiet_hours_start"]
        row.quiet_hours_end = values["quiet_hours_end"]
        row.workdays = values["workdays"]
        row.enabled_types = values["enabled_types"]
        row.enabled_channels = values["enabled_channels"]
        if values["email_address"] is not None:
            row.email_address = values["email_address"]
        row.updated_at = now

    # Handle Email opt-in / consent update
    if values["email_opt_in"] is not None:
        if values["email_opt_in"] and (
            values["email_consent_granted"] or values["email_consent_granted"] is None
        ):
            row.email_opt_in = True
            row.email_consent_at = now
            row.email_consent_source = "web_settings"
            row.email_consent_policy_version = "1.0"
            if "email" not in row.enabled_channels:
                row.enabled_channels = [*row.enabled_channels, "email"]
            record_event(
                db,
                AuditAction.EMAIL_CONSENT_UPDATED,
                actor=user,
                details="email consent granted (opt-in enabled)",
                commit=False,
            )
        elif not values["email_opt_in"]:
            row.email_opt_in = False
            row.email_consent_at = None
            if "email" in row.enabled_channels:
                row.enabled_channels = [c for c in row.enabled_channels if c != "email"]
            record_event(
                db,
                AuditAction.EMAIL_CONSENT_UPDATED,
                actor=user,
                details="email consent revoked (opt-in disabled)",
                commit=False,
            )

    # Handle Telegram opt-in / consent update
    if values["telegram_opt_in"] is not None:
        if values["telegram_opt_in"] and (
            values["telegram_consent_granted"] or values["telegram_consent_granted"] is None
        ):
            if row.telegram_chat_id is not None:
                row.telegram_opt_in = True
                row.telegram_consent_at = now
                row.telegram_consent_source = "web_settings"
                row.telegram_consent_policy_version = "1.0"
                if "telegram" not in row.enabled_channels:
                    row.enabled_channels = [*row.enabled_channels, "telegram"]
                record_event(
                    db,
                    AuditAction.TELEGRAM_CONSENT_UPDATED,
                    actor=user,
                    details="telegram consent granted (opt-in enabled)",
                    commit=False,
                )
        elif not values["telegram_opt_in"]:
            row.telegram_opt_in = False
            if "telegram" in row.enabled_channels:
                row.enabled_channels = [c for c in row.enabled_channels if c != "telegram"]
            record_event(
                db,
                AuditAction.TELEGRAM_CONSENT_UPDATED,
                actor=user,
                details="telegram consent revoked (opt-in disabled)",
                commit=False,
            )

    record_event(
        db,
        AuditAction.PREFERENCE_UPDATED,
        actor=user,
        details="notification preferences updated",
        commit=False,
    )
    db.commit()
    db.refresh(row)
    return PreferenceOut(
        timezone=row.timezone,
        quiet_hours_start=row.quiet_hours_start,
        quiet_hours_end=row.quiet_hours_end,
        workdays=row.workdays,
        enabled_types=row.enabled_types,
        enabled_channels=row.enabled_channels,
        initialized=True,
        telegram_chat_id=row.telegram_chat_id,
        telegram_username=row.telegram_username,
        telegram_linked_at=row.telegram_linked_at,
        telegram_opt_in=row.telegram_opt_in,
        telegram_consent_at=row.telegram_consent_at,
        telegram_consent_source=row.telegram_consent_source,
        telegram_consent_policy_version=row.telegram_consent_policy_version,
        email_address=row.email_address,
        email_opt_in=row.email_opt_in,
        email_consent_at=row.email_consent_at,
        email_consent_source=row.email_consent_source,
        email_consent_policy_version=row.email_consent_policy_version,
        channel_health=row.channel_health,
        telegram_bot_username=settings.telegram_bot_username or None,
    )


@router.get("/timezones", response_model=TimezonesOut, summary="Timezones offered by the UI")
def timezones(
    user: User = Depends(get_current_user),
) -> TimezonesOut:
    return TimezonesOut(timezones=_COMMON_TIMEZONES)
