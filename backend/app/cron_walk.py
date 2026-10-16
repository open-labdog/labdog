"""When a cron schedule next runs, read in the instance's timezone.

Every cron expression LabDog evaluates itself — scheduled actions and
discovery scans — is read in one timezone, the ``scheduling.timezone``
setting (an IANA name, default ``UTC``). Cron jobs LabDog writes to
hosts are not covered: those run on each host's own clock.

Daylight saving needs two rules, and croniter supplies only the first:

- Spring forward: a time that does not exist (02:30 on the night the
  clock jumps from 02:00 to 03:00) runs once, at the jump.
- Fall back: a time that happens twice (02:30 on the night the clock
  goes back from 03:00 to 02:00) runs once, the first time. croniter
  returns both, so walking on from the first run would fire a daily
  02:30 job twice that night. As in Vixie cron, the rule covers only
  fixed-time entries: one with ``*`` at the start of its minute or hour
  field (``*/15 * * * *``, ``0 * * * *``) follows the clock through the
  repeated hour like any other.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from datetime import UTC, datetime, tzinfo
from functools import cache
from typing import TYPE_CHECKING
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError, available_timezones

from croniter import croniter

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

TIMEZONE_SETTING = "scheduling.timezone"

# croniter's aliases, spelled out so the fixed-time test below can read
# their minute and hour fields.
_ALIASES = {
    "@yearly": "0 0 1 1 *",
    "@annually": "0 0 1 1 *",
    "@monthly": "0 0 1 * *",
    "@weekly": "0 0 * * 0",
    "@daily": "0 0 * * *",
    "@midnight": "0 0 * * *",
    "@hourly": "0 * * * *",
}


# Loadable, but not a place: the server's own clock, a template file, and
# a zone that reads "-00". Still accepted if typed; just not offered.
_NOT_OFFERED = frozenset({"localtime", "posixrules", "Factory"})


@cache
def timezone_names() -> list[str]:
    """The IANA names the settings page offers for ``scheduling.timezone``,
    sorted. Taken from the tz database ``validate_timezone`` loads from, so
    the page never offers a name the server would refuse."""
    return sorted(available_timezones() - _NOT_OFFERED)


def validate_timezone(name: str) -> str:
    """Settings validator: the IANA name, stripped, or ``ValueError``."""
    name = name.strip()
    try:
        ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        raise ValueError(
            f"unknown timezone {name!r} — use an IANA name such as Europe/Stockholm"
        ) from None
    return name


def resolve_timezone(name: str | None) -> tzinfo:
    """The zone called *name*, or UTC with a warning when it can't be loaded.

    The settings validator refuses unknown names, so this only falls back
    for a value written past it, or zone data missing from the install.
    Reading every schedule as UTC is better than running none of them.
    """
    if not name or name == "UTC":
        return UTC
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        logger.warning(
            "%s %r is not a timezone this install knows; reading cron schedules as UTC",
            TIMEZONE_SETTING,
            name,
        )
        return UTC


async def get_schedule_timezone(db: AsyncSession) -> tzinfo:
    """The configured scheduling timezone."""
    from app.settings_service import get_setting  # noqa: PLC0415

    return resolve_timezone(await get_setting(TIMEZONE_SETTING, db))


def _is_fixed_time(expr: str) -> bool:
    """Vixie cron's test: neither the minute nor the hour field starts with ``*``."""
    fields = _ALIASES.get(expr.strip().lower(), expr).split()
    return not (fields[0].startswith("*") or fields[1].startswith("*"))


def _is_repeat(when: datetime, tz: tzinfo) -> bool:
    """True when *when* is the second pass of a wall-clock time the fall-back repeats."""
    wall = when.astimezone(tz).replace(tzinfo=None)
    first = wall.replace(tzinfo=tz, fold=0)
    second = wall.replace(tzinfo=tz, fold=1)
    if first.utcoffset() == second.utcoffset():
        return False
    # Compared as UTC on purpose: two datetimes sharing a tzinfo compare
    # by wall clock, ignoring fold, so both passes would look equal.
    return when.astimezone(UTC) == second.astimezone(UTC)


def iter_fire_times(expr: str, after: datetime, tz: tzinfo) -> Iterator[datetime]:
    """Yield the times *expr* runs after *after*, as UTC, in order.

    *after* is an instant; naive values are taken to be UTC, which is how
    the database hands back ``timestamptz`` columns in some test setups.
    """
    if after.tzinfo is None:
        after = after.replace(tzinfo=UTC)
    fixed = _is_fixed_time(expr)
    it = croniter(expr, after.astimezone(tz))
    while True:
        when = it.get_next(datetime)
        if when.tzinfo is None:
            when = when.replace(tzinfo=tz)
        when = when.astimezone(UTC)
        # The second guard is belt and braces: a walk that starts inside
        # the repeated hour must never land on the hour's first pass,
        # which is already behind it.
        if (fixed and _is_repeat(when, tz)) or when <= after:
            continue
        yield when


def next_fire_time(expr: str, after: datetime, tz: tzinfo) -> datetime:
    """The first time *expr* runs after *after*, as UTC."""
    return next(iter_fire_times(expr, after, tz))
