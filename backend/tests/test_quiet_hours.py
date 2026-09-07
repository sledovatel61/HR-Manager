"""Quiet-hours and scheduling math (frozen time, DST, timezones)."""

from datetime import UTC, datetime

from app.models import NotificationPreference
from app.quiet_hours import (
    effective_send_time,
    in_quiet_period,
    next_allowed_time,
    next_occurrence,
)

# Europe/Moscow has no DST (stable offset); Europe/Berlin has DST — used
# for the DST transition tests. All inputs/outputs are timezone-aware UTC.


def _utc(year: int, month: int, day: int, hour: int, minute: int = 0, second: int = 0) -> datetime:
    return datetime(year, month, day, hour, minute, second, tzinfo=UTC)


def test_midnight_crossing_interval() -> None:
    # Quiet 21:00-08:00 Moscow (UTC+3): 22:00 local = 19:00 UTC.
    assert in_quiet_period(
        _utc(2026, 9, 4, 19, 0),
        timezone="Europe/Moscow",
        quiet_hours_start="21:00",
        quiet_hours_end="08:00",
    )
    # 09:00 local = 06:00 UTC — allowed.
    assert not in_quiet_period(
        _utc(2026, 9, 4, 6, 0),
        timezone="Europe/Moscow",
        quiet_hours_start="21:00",
        quiet_hours_end="08:00",
    )
    # Right at the end boundary 08:00 local = 05:00 UTC — allowed.
    assert not in_quiet_period(
        _utc(2026, 9, 4, 5, 0),
        timezone="Europe/Moscow",
        quiet_hours_start="21:00",
        quiet_hours_end="08:00",
    )
    # Right at the start boundary 21:00 local = 18:00 UTC — quiet.
    assert in_quiet_period(
        _utc(2026, 9, 4, 18, 0),
        timezone="Europe/Moscow",
        quiet_hours_start="21:00",
        quiet_hours_end="08:00",
    )


def test_same_day_interval() -> None:
    assert in_quiet_period(
        _utc(2026, 9, 4, 8, 0),  # 11:00 MSK
        timezone="Europe/Moscow",
        quiet_hours_start="10:00",
        quiet_hours_end="12:00",
    )
    assert not in_quiet_period(
        _utc(2026, 9, 4, 9, 30),  # 12:30 MSK
        timezone="Europe/Moscow",
        quiet_hours_start="10:00",
        quiet_hours_end="12:00",
    )


def test_zero_length_interval_disables_quiet_hours() -> None:
    assert not in_quiet_period(
        _utc(2026, 9, 4, 23, 0),
        timezone="Europe/Moscow",
        quiet_hours_start="09:00",
        quiet_hours_end="09:00",
    )


def test_next_allowed_shifts_across_midnight() -> None:
    now = _utc(2026, 9, 4, 19, 30)  # 22:30 MSK Friday
    next_time = next_allowed_time(
        now, timezone="Europe/Moscow", quiet_hours_start="21:00", quiet_hours_end="08:00"
    )
    # Next morning 08:00 MSK = 05:00 UTC Saturday.
    assert next_time == _utc(2026, 9, 5, 5, 0)


def test_next_allowed_respects_workdays() -> None:
    # 2026-09-04 is a Friday. Friday 22:30 MSK with workdays Mon-Fri →
    # next allowed is Saturday 08:00 MSK... which is not a workday, so it
    # must walk to Monday 08:00 MSK.
    now = _utc(2026, 9, 4, 19, 30)
    next_time = next_allowed_time(
        now,
        timezone="Europe/Moscow",
        quiet_hours_start="21:00",
        quiet_hours_end="08:00",
        workdays=[1, 2, 3, 4, 5],
    )
    assert next_time == _utc(2026, 9, 7, 5, 0)  # Monday 08:00 MSK


def test_next_allowed_already_allowed_returns_now_rounded() -> None:
    now = _utc(2026, 9, 4, 6, 0, 30)  # 09:00:30 MSK
    next_time = next_allowed_time(
        now, timezone="Europe/Moscow", quiet_hours_start="21:00", quiet_hours_end="08:00"
    )
    assert next_time == _utc(2026, 9, 4, 6, 1, 0)  # next minute


def test_dst_spring_forward_daily_occurrence_keeps_local_hour() -> None:
    # Europe/Berlin: 2026-03-29 02:00 local jumps to 03:00 (DST start).
    due = _utc(2026, 3, 28, 8, 0)  # 09:00 CET
    nxt = next_occurrence(due, timezone="Europe/Berlin", recurrence="daily")
    # 2026-03-29 09:00 CEST = 07:00 UTC (offset +2).
    assert nxt == _utc(2026, 3, 29, 7, 0)
    assert nxt.astimezone(__import__("zoneinfo").ZoneInfo("Europe/Berlin")).hour == 9


def test_weekly_occurrence() -> None:
    due = _utc(2026, 9, 4, 10, 0)
    nxt = next_occurrence(due, timezone="Europe/Moscow", recurrence="weekly")
    assert nxt == _utc(2026, 9, 11, 10, 0)


def test_workdays_occurrence_skips_weekend() -> None:
    # Friday 09:00 MSK → Monday 09:00 MSK.
    due = _utc(2026, 9, 4, 6, 0)
    nxt = next_occurrence(
        due, timezone="Europe/Moscow", recurrence="workdays", workdays=[1, 2, 3, 4, 5]
    )
    assert nxt == _utc(2026, 9, 7, 6, 0)


def test_effective_send_time_future_quiet_is_shifted() -> None:
    pref = NotificationPreference(
        user_id=__import__("uuid").uuid4(),
        timezone="Europe/Moscow",
        quiet_hours_start="21:00",
        quiet_hours_end="08:00",
        workdays=[1, 2, 3, 4, 5],
        enabled_types=[],
        enabled_channels=[],
    )
    scheduled = _utc(2026, 9, 4, 19, 0)  # 22:00 MSK — quiet
    effective = effective_send_time(scheduled, preference=pref, now_utc=_utc(2026, 9, 4, 10, 0))
    assert effective == _utc(2026, 9, 5, 5, 0)  # 08:00 MSK next morning


def test_effective_send_time_allowed_is_unchanged() -> None:
    pref = NotificationPreference(
        user_id=__import__("uuid").uuid4(),
        timezone="Europe/Moscow",
        quiet_hours_start="21:00",
        quiet_hours_end="08:00",
        workdays=[1, 2, 3, 4, 5],
        enabled_types=[],
        enabled_channels=[],
    )
    scheduled = _utc(2026, 9, 4, 9, 0)  # 12:00 MSK — allowed
    effective = effective_send_time(scheduled, preference=pref, now_utc=_utc(2026, 9, 4, 8, 0))
    assert effective == scheduled


def test_effective_send_time_overdue_shifts_from_now() -> None:
    pref = NotificationPreference(
        user_id=__import__("uuid").uuid4(),
        timezone="Europe/Moscow",
        quiet_hours_start="21:00",
        quiet_hours_end="08:00",
        workdays=[1, 2, 3, 4, 5],
        enabled_types=[],
        enabled_channels=[],
    )
    now = _utc(2026, 9, 4, 19, 0)  # 22:00 MSK — quiet
    scheduled = _utc(2026, 9, 4, 5, 0)  # already past
    effective = effective_send_time(scheduled, preference=pref, now_utc=now)
    assert effective == _utc(2026, 9, 5, 5, 0)
