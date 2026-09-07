"""Quiet-hours and scheduling-time math for the notification contour.

All persisted timestamps are timezone-aware UTC. The user's quiet hours and
workdays are *local wall-clock* rules of their IANA timezone; every
evaluation converts the local boundaries to UTC on the fly (zoneinfo), so
DST transitions and timezone changes are handled correctly at processing
time. Original and effective send times are stored separately for audit.

Pure functions take ``now`` explicitly so tests can freeze time; the
worker/API pass ``utc_now()``.
"""

from datetime import datetime, timedelta, tzinfo
from zoneinfo import ZoneInfo

from app.models import NotificationPreference

# ISO weekday number (Monday=1 .. Sunday=7).
MONDAY = 1
SUNDAY = 7

# The scheduler scans up to this many days ahead for the next allowed
# moment (a full week plus a day covers every weekday combination).
_MAX_SCAN_DAYS = 8


def parse_hh_mm(value: str) -> tuple[int, int]:
    """Parse ``HH:MM`` into (hours, minutes). Raises ValueError on junk."""
    if len(value) != 5 or value[2] != ":":
        raise ValueError(f"expected HH:MM, got {value!r}")
    hours, minutes = int(value[:2]), int(value[3:])
    if not (0 <= hours <= 23 and 0 <= minutes <= 59):
        raise ValueError(f"expected HH:MM, got {value!r}")
    return hours, minutes


def _local_time(aware_utc: datetime, zone: tzinfo) -> datetime:
    return aware_utc.astimezone(zone).replace(tzinfo=None)


def _local_to_utc(local: datetime, zone: tzinfo) -> datetime:
    # zoneinfo handles DST: ambiguous/nonexistent local times resolve
    # deterministically (fold=0 default).
    return local.replace(tzinfo=zone).astimezone(ZoneInfo("UTC"))


def in_quiet_period(
    when_utc: datetime,
    *,
    timezone: str,
    quiet_hours_start: str,
    quiet_hours_end: str,
) -> bool:
    """Whether the UTC instant falls inside the quiet interval.

    The interval may cross midnight: ``21:00-08:00`` means quiet from 21:00
    through 08:00 next morning. A zero-length interval (start == end) means
    quiet hours are disabled.
    """
    zone = ZoneInfo(timezone)
    start_h, start_m = parse_hh_mm(quiet_hours_start)
    end_h, end_m = parse_hh_mm(quiet_hours_end)
    start_minutes = start_h * 60 + start_m
    end_minutes = end_h * 60 + end_m
    if start_minutes == end_minutes:
        return False

    local = _local_time(when_utc, zone)
    current = local.hour * 60 + local.minute
    if start_minutes < end_minutes:
        return start_minutes <= current < end_minutes
    # Crossing midnight: quiet is [start, 24:00) + [00:00, end).
    return current >= start_minutes or current < end_minutes


def is_workday(when_utc: datetime, *, timezone: str, workdays: list[int]) -> bool:
    """Whether the instant falls on one of the user's workdays.

    ``workdays`` are ISO weekday numbers (1=Monday .. 7=Sunday) in the
    user's own timezone.
    """
    if not workdays:
        return False
    zone = ZoneInfo(timezone)
    local = _local_time(when_utc, zone)
    return local.isoweekday() in workdays


def _apply_workdays(candidate: datetime, workdays: list[int], zone: tzinfo) -> datetime:
    """Walk forward minute by minute until the local date is a workday."""
    guard = 0
    while not is_workday(
        candidate.astimezone(ZoneInfo("UTC")), timezone=str(zone), workdays=workdays
    ):
        candidate = candidate + timedelta(minutes=1)
        guard += 1
        if guard > 7 * 24 * 60:
            # A 7-day scan must always find a workday for any non-empty set.
            raise RuntimeError("could not find a workday (invalid workdays?)")
    return candidate


def next_allowed_time(
    now_utc: datetime,
    *,
    timezone: str,
    quiet_hours_start: str,
    quiet_hours_end: str,
    workdays: list[int] | None = None,
) -> datetime:
    """First minute at/after ``now_utc`` allowed by quiet hours (and, when
    ``workdays`` is given, by the workday set).

    Scans minute by minute (bounded by a full week) and returns the first
    allowed minute. Deterministic for a frozen ``now_utc``; DST-safe because
    every step is evaluated through zoneinfo.
    """
    zone = ZoneInfo(timezone)
    candidate = now_utc.replace(second=0, microsecond=0)
    if now_utc != candidate:
        candidate = candidate + timedelta(minutes=1)
    if workdays:
        candidate = _apply_workdays(candidate, workdays, zone)
    guard = 0
    while in_quiet_period(
        candidate,
        timezone=timezone,
        quiet_hours_start=quiet_hours_start,
        quiet_hours_end=quiet_hours_end,
    ):
        candidate = candidate + timedelta(minutes=1)
        if workdays:
            candidate = _apply_workdays(candidate, workdays, zone)
        guard += 1
        if guard > 8 * 24 * 60:
            raise RuntimeError("quiet-hours scan exceeded a week — check the settings")
    return candidate


def next_occurrence(
    due_utc: datetime,
    *,
    timezone: str,
    recurrence: str,
    workdays: list[int] | None = None,
) -> datetime:
    """Next occurrence of a recurring reminder (daily/workdays/weekly).

    Computed in the reminder's display timezone so that «daily 09:00» stays
    09:00 local time across DST changes. ``workdays`` recurrence advances
    whole days (keeping the local time of day) until the date is one of the
    owner's workdays.
    """
    zone = ZoneInfo(timezone)
    local = _local_time(due_utc, zone)
    if recurrence == "daily":
        return _local_to_utc(local + timedelta(days=1), zone)
    if recurrence == "weekly":
        return _local_to_utc(local + timedelta(days=7), zone)
    if recurrence == "workdays":
        allowed = workdays or list(range(1, 6))
        candidate = local + timedelta(days=1)
        guard = 0
        while candidate.isoweekday() not in allowed:
            candidate = candidate + timedelta(days=1)
            guard += 1
            if guard > 10:
                raise RuntimeError("could not find a workday (invalid workdays?)")
        return _local_to_utc(candidate, zone)
    return due_utc


def effective_send_time(
    scheduled_at: datetime,
    *,
    preference: NotificationPreference,
    now_utc: datetime,
) -> datetime:
    """The actual time a job may be processed: the requested time shifted
    to the first allowed minute of the recipient's quiet-hours schedule."""
    if scheduled_at <= now_utc:
        if not in_quiet_period(
            now_utc,
            timezone=preference.timezone,
            quiet_hours_start=preference.quiet_hours_start,
            quiet_hours_end=preference.quiet_hours_end,
        ):
            return now_utc  # already due and allowed: deliver now
        return next_allowed_time(
            now_utc,
            timezone=preference.timezone,
            quiet_hours_start=preference.quiet_hours_start,
            quiet_hours_end=preference.quiet_hours_end,
        )
    if in_quiet_period(
        scheduled_at,
        timezone=preference.timezone,
        quiet_hours_start=preference.quiet_hours_start,
        quiet_hours_end=preference.quiet_hours_end,
    ):
        return next_allowed_time(
            scheduled_at,
            timezone=preference.timezone,
            quiet_hours_start=preference.quiet_hours_start,
            quiet_hours_end=preference.quiet_hours_end,
        )
    return scheduled_at
