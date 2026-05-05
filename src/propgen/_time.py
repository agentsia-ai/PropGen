"""UTC-aware datetime helpers — mirror SchedBot's `_time` module."""

from __future__ import annotations

from datetime import datetime, timezone, tzinfo
from typing import Optional

from dateutil import tz as _dateutil_tz


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def to_iso(dt: datetime | None) -> str | None:
    return dt.isoformat() if dt else None


def parse_iso(s: str | None) -> datetime | None:
    if not s:
        return None
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    dt = datetime.fromisoformat(s)
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def get_tz(name: str) -> tzinfo:
    resolved = _dateutil_tz.gettz(name)
    return resolved or timezone.utc


def to_local(dt: datetime | None, tz_name: str) -> Optional[datetime]:
    if dt is None:
        return None
    aware = dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    return aware.astimezone(get_tz(tz_name))


def format_local(
    dt: datetime | None, tz_name: str, fmt: str = "%a %b %d %I:%M %p %Z"
) -> str:
    if dt is None:
        return ""
    return to_local(dt, tz_name).strftime(fmt)
