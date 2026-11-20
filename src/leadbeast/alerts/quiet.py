"""Quiet hours with break-through, DST-safe via zoneinfo. Pure."""

from __future__ import annotations

from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo


def in_quiet(at: datetime, start: time | None, end: time | None, tz: str) -> bool:
    if start is None or end is None or start == end:
        return False
    t = at.astimezone(ZoneInfo(tz)).time()
    return start <= t < end if start < end else (t >= start or t < end)


def window_end(at: datetime, end: time, tz: str) -> datetime:
    local = at.astimezone(ZoneInfo(tz))
    candidate = datetime.combine(local.date(), end, tzinfo=ZoneInfo(tz))
    if candidate <= local:
        candidate = datetime.combine(local.date() + timedelta(days=1), end, tzinfo=ZoneInfo(tz))
    return candidate


def should_hold(at: datetime, start: time | None, end: time | None, tz: str, pressure: float,
                breakthrough_min: int | None) -> bool:
    if not in_quiet(at, start, end, tz):
        return False
    return not (breakthrough_min is not None and pressure >= breakthrough_min)
