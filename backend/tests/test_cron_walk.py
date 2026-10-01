"""The cron walk: schedules read in the configured timezone, across daylight saving.

Europe/Stockholm is CEST (UTC+2) until 2026-10-25, when the clock goes back
from 03:00 to 02:00 CET (UTC+1), and jumps from 02:00 to 03:00 CEST again on
2027-03-28. Every expected value below is in UTC.
"""

from __future__ import annotations

from datetime import UTC, datetime
from itertools import islice

import pytest

from app import settings_service as ss
from app.cron_walk import (
    TIMEZONE_SETTING,
    iter_fire_times,
    next_fire_time,
    resolve_timezone,
    validate_timezone,
)

STOCKHOLM = resolve_timezone("Europe/Stockholm")


def utc(*args: int) -> datetime:
    return datetime(*args, tzinfo=UTC)


def walk(expr: str, start: datetime, n: int, tz=STOCKHOLM) -> list[datetime]:
    return list(islice(iter_fire_times(expr, start, tz), n))


def test_utc_reads_the_expression_as_before():
    assert walk("1 4 * * 0", utc(2026, 10, 1, 12), 2, UTC) == [
        utc(2026, 10, 4, 4, 1),
        utc(2026, 10, 11, 4, 1),
    ]


def test_the_expression_keeps_its_clock_time_across_the_change():
    """The schedule that prompted this: Sunday 04:01 local, before and after."""
    assert walk("1 4 * * 0", utc(2026, 10, 1, 12), 5) == [
        utc(2026, 10, 4, 2, 1),
        utc(2026, 10, 11, 2, 1),
        utc(2026, 10, 18, 2, 1),
        utc(2026, 10, 25, 3, 1),
        utc(2026, 11, 1, 3, 1),
    ]


def test_a_fixed_time_the_clock_repeats_runs_once():
    assert walk("30 2 * * *", utc(2026, 10, 24, 12), 2) == [
        utc(2026, 10, 25, 0, 30),
        utc(2026, 10, 26, 1, 30),
    ]


@pytest.mark.parametrize(
    "start",
    [utc(2026, 10, 25, 0, 30, 15), utc(2026, 10, 25, 1, 15)],
    ids=["just after the first pass", "inside the repeated hour"],
)
def test_walking_on_from_the_first_pass_skips_the_second(start):
    """The scheduler walks from its last dispatch, which is where this went wrong."""
    assert next_fire_time("30 2 * * *", start, STOCKHOLM) == utc(2026, 10, 26, 1, 30)


def test_a_wildcard_hour_follows_the_clock_through_the_repeated_hour():
    assert walk("0 * * * *", utc(2026, 10, 24, 23, 30), 4) == [
        utc(2026, 10, 25, 0),
        utc(2026, 10, 25, 1),
        utc(2026, 10, 25, 2),
        utc(2026, 10, 25, 3),
    ]


def test_a_wildcard_minute_runs_in_both_passes_of_its_hour():
    """Vixie cron's rule: ``*`` in the minute field makes it a wildcard entry."""
    assert walk("*/30 2 * * *", utc(2026, 10, 24, 23, 50), 4) == [
        utc(2026, 10, 25, 0, 0),
        utc(2026, 10, 25, 0, 30),
        utc(2026, 10, 25, 1, 0),
        utc(2026, 10, 25, 1, 30),
    ]


def test_a_time_the_clock_skips_runs_once_at_the_jump():
    assert walk("30 2 * * *", utc(2027, 3, 27, 12), 2) == [
        utc(2027, 3, 28, 1, 0),
        utc(2027, 3, 29, 0, 30),
    ]


def test_an_alias_is_read_in_the_zone_too():
    assert next_fire_time("@daily", utc(2026, 10, 1, 12), STOCKHOLM) == utc(2026, 10, 1, 22)


def test_a_naive_start_is_taken_as_utc():
    assert next_fire_time("0 3 * * *", datetime(2026, 10, 1, 12), UTC) == utc(2026, 10, 2, 3)


def test_the_validator_takes_iana_names():
    assert validate_timezone(" Europe/Stockholm ") == "Europe/Stockholm"
    assert validate_timezone("UTC") == "UTC"


@pytest.mark.parametrize("name", ["", "CEST", "Europe/Nowhere", "../etc/passwd"])
def test_the_validator_refuses_anything_else(name):
    with pytest.raises(ValueError, match="IANA name"):
        validate_timezone(name)


def test_the_setting_runs_the_validator():
    assert ss._validate(TIMEZONE_SETTING, " Europe/Stockholm ") == "Europe/Stockholm"
    with pytest.raises(ValueError, match=f"^{TIMEZONE_SETTING}: unknown timezone"):
        ss._validate(TIMEZONE_SETTING, "Europe/Nowhere")


def test_an_unloadable_stored_name_falls_back_to_utc(caplog):
    assert resolve_timezone("Europe/Nowhere") is UTC
    assert "reading cron schedules as UTC" in caplog.text
