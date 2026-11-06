"""Single source of "now" for the whole application.

Production code must never call ``datetime.now()`` / ``date.today()`` directly: the host machine's
local timezone is irrelevant for FactoryPulse. All business dates ("today", "overdue", "this
month") are derived from UTC via the configured company timezone (``FP_TIMEZONE``).

Tests replace the clock with :func:`freeze` / :func:`set_clock`, so results never depend on
when or where the test suite runs (UTC midnight, daylight-saving transitions, CI timezone).
"""
from __future__ import annotations

import datetime as dt
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from functools import lru_cache
from zoneinfo import ZoneInfo

_provider: Callable[[], dt.datetime] | None = None


def _system_now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def now_utc() -> dt.datetime:
    """Current instant, timezone-aware in UTC."""
    value = (_provider or _system_now)()
    if value.tzinfo is None:
        raise ValueError("clock provider must return timezone-aware datetimes")
    return value.astimezone(dt.timezone.utc)


@lru_cache(maxsize=16)
def zone(name: str | None = None) -> ZoneInfo:
    if name is None:
        from .config import get_settings

        name = get_settings().timezone
    return ZoneInfo(name)


def local_now(tz_name: str | None = None) -> dt.datetime:
    """Current instant in the company timezone."""
    return now_utc().astimezone(zone(tz_name))


def local_today(tz_name: str | None = None) -> dt.date:
    """Current calendar date in the company timezone (not the host's timezone)."""
    return local_now(tz_name).date()


def local_day_bounds(day: dt.date, tz_name: str | None = None) -> tuple[dt.datetime, dt.datetime]:
    """[start, end) of a local calendar day, returned as **UTC** instants.

    Days are 23 h or 25 h long on daylight-saving transitions, so never compute them as
    ``start + 24 h``. UTC is returned on purpose: (1) SQLite stores timestamps without offset and
    would compare a local-time parameter as if it were UTC, (2) Python subtracts two datetimes that
    share the *same* tzinfo object as wall-clock times (an hour off on DST days)."""
    z = zone(tz_name)
    start = dt.datetime.combine(day, dt.time(), z).astimezone(dt.timezone.utc)
    end = dt.datetime.combine(day + dt.timedelta(days=1), dt.time(), z).astimezone(dt.timezone.utc)
    return start, end


def set_clock(provider: Callable[[], dt.datetime] | None) -> None:
    """Install a clock provider (None restores the system clock)."""
    global _provider
    _provider = provider


@contextmanager
def freeze(instant: dt.datetime) -> Iterator["FrozenClock"]:
    """Freeze time at ``instant`` (aware datetime). The yielded object can be moved forward."""
    fc = FrozenClock(instant)
    previous = _provider
    set_clock(fc)
    try:
        yield fc
    finally:
        set_clock(previous)


class FrozenClock:
    def __init__(self, instant: dt.datetime):
        if instant.tzinfo is None:
            raise ValueError("freeze() needs a timezone-aware datetime")
        self.instant = instant

    def __call__(self) -> dt.datetime:
        return self.instant

    def advance(self, **kwargs: float) -> dt.datetime:
        self.instant = self.instant + dt.timedelta(**kwargs)
        return self.instant

    def set(self, instant: dt.datetime) -> None:
        self.instant = instant
