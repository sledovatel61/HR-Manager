"""Own notification preferences (timezone, quiet hours, workdays, types)."""

from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from app.audit import record_event
from app.db import get_db
from app.deps import get_current_user
from app.models import AuditAction, DeliveryChannel, NotificationPreference, NotificationType, User
from app.schemas import PreferenceOut, PreferenceUpdate, TimezonesOut
from app.utils import utc_now

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
    return {
        "timezone": payload.timezone,
        "quiet_hours_start": payload.quiet_hours_start,
        "quiet_hours_end": payload.quiet_hours_end,
        "workdays": sorted(payload.workdays),
        "enabled_types": sorted(set(payload.enabled_types)),
        "enabled_channels": sorted(set(payload.enabled_channels)),
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
    }


@router.get("", response_model=PreferenceOut, summary="My notification preferences")
def get_preferences(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> PreferenceOut:
    row = db.get(NotificationPreference, user.id)
    if row is None:
        defaults = _defaults()
        defaults["timezone"] = request.app.state.settings.notification_default_timezone
        return PreferenceOut(**defaults)
    return PreferenceOut(
        timezone=row.timezone,
        quiet_hours_start=row.quiet_hours_start,
        quiet_hours_end=row.quiet_hours_end,
        workdays=row.workdays,
        enabled_types=row.enabled_types,
        enabled_channels=row.enabled_channels,
        initialized=True,
    )


@router.put("", response_model=PreferenceOut, summary="Save my notification preferences")
def put_preferences(
    payload: PreferenceUpdate,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> PreferenceOut:
    values = _validated(payload)
    row = db.get(NotificationPreference, user.id)
    if row is None:
        row = NotificationPreference(user_id=user.id, created_at=utc_now(), **values)
        db.add(row)
    else:
        row.timezone = values["timezone"]
        row.quiet_hours_start = values["quiet_hours_start"]
        row.quiet_hours_end = values["quiet_hours_end"]
        row.workdays = values["workdays"]
        row.enabled_types = values["enabled_types"]
        row.enabled_channels = values["enabled_channels"]
        row.updated_at = utc_now()
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
    )


@router.get("/timezones", response_model=TimezonesOut, summary="Timezones offered by the UI")
def timezones(
    user: User = Depends(get_current_user),
) -> TimezonesOut:
    return TimezonesOut(timezones=_COMMON_TIMEZONES)
