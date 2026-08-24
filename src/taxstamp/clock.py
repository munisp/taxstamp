"""Time access.

All timestamps are timezone-aware UTC. Tests substitute a controllable clock instead
of patching module-level functions, and no business logic reads naive local time.
"""

from __future__ import annotations

import datetime as dt
from typing import Protocol


class Clock(Protocol):
    def now(self) -> dt.datetime: ...


class SystemClock:
    def now(self) -> dt.datetime:
        return dt.datetime.now(tz=dt.UTC)


class FixedClock:
    """A clock that only advances when explicitly told to. Test support."""

    def __init__(self, start: dt.datetime) -> None:
        if start.tzinfo is None:
            raise ValueError("FixedClock requires an aware datetime")
        self._now = start.astimezone(dt.UTC)

    def now(self) -> dt.datetime:
        return self._now

    def advance(self, seconds: float) -> None:
        self._now = self._now + dt.timedelta(seconds=seconds)


def utcnow() -> dt.datetime:
    return dt.datetime.now(tz=dt.UTC)


def ensure_utc(value: dt.datetime) -> dt.datetime:
    if value.tzinfo is None:
        raise ValueError("naive datetimes are not accepted")
    return value.astimezone(dt.UTC)
