"""Periodic task that checks for scheduled scan configs and dispatches runs."""

import asyncio
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from app.tasks import celery_app

if TYPE_CHECKING:
    from app.models.scan_config import ScanConfig


def _is_due(config: "ScanConfig", now: datetime) -> bool:
    """Return True when a scan config is due to run.

    Args:
        config: The ScanConfig ORM instance to evaluate.
        now: The current UTC-aware datetime used as the reference point.

    Returns:
        True if the config should fire a run right now, False otherwise.
    """
    has_interval = config.interval_minutes is not None
    has_cron = config.cron_expression is not None

    if has_interval and not has_cron:
        if config.last_run_at is None:
            return True
        elapsed = now - config.last_run_at
        return elapsed >= timedelta(minutes=config.interval_minutes)

    if has_cron and not has_interval:
        from croniter import croniter

        # Use last_run_at as the base; if never run, pretend the last run was
        # one minute ago so the first get_next() fires on the very first tick
        # that the cron would have matched.
        base = config.last_run_at if config.last_run_at is not None else now - timedelta(minutes=1)
        cron = croniter(config.cron_expression, base)
        next_fire = cron.get_next(datetime)
        # croniter may return naive datetimes; normalise to UTC-aware.
        if next_fire.tzinfo is None:
            next_fire = next_fire.replace(tzinfo=UTC)
        return next_fire <= now

    # Both set or neither set — db constraint prevents this, but be defensive.
    return False


@celery_app.task(name="scans.check_scheduled", queue="default")
def check_scheduled_scans() -> dict:
    """Check all enabled scan configs and dispatch runs for those that are due.

    Runs every 60 s via RedBeat. Fires ``scans.run_config`` for each due
    config without importing ``scan_run`` at module level so that T4 can be
    added later without a circular-import risk.

    Returns:
        A dict with the number of configs dispatched this tick.
    """
    count = asyncio.run(_check())
    return {"dispatched": count}


async def _check() -> int:
    """Core async logic: query enabled configs and dispatch due ones.

    Returns:
        Number of scan configs dispatched.
    """
    from sqlalchemy import select

    from app.db import task_session
    from app.models.scan_config import ScanConfig

    dispatched = 0
    now = datetime.now(UTC)

    async with task_session() as db:
        configs = (
            (
                await db.execute(
                    select(ScanConfig).where(ScanConfig.enabled == True)  # noqa: E712
                )
            )
            .scalars()
            .all()
        )

        for config in configs:
            try:
                if _is_due(config, now):
                    # Use send_task so this module does not need to import
                    # scan_run (which T4 will create).
                    celery_app.send_task("scans.run_config", args=[config.id])
                    dispatched += 1
            except Exception:
                # Do not let one bad config block the rest.
                pass

    return dispatched


# ---------------------------------------------------------------------------
# Register the periodic RedBeat entry on module import.
# Wrapped in try/except so tests (and import-time checks without Redis) do
# not blow up.
# ---------------------------------------------------------------------------


def _register_beat_schedule() -> None:
    from celery.schedules import schedule
    from redbeat import RedBeatSchedulerEntry

    entry = RedBeatSchedulerEntry(
        name="scans.check_scheduled",
        task="scans.check_scheduled",
        schedule=schedule(run_every=60),
        app=celery_app,
    )
    entry.save()


try:
    _register_beat_schedule()
except Exception:
    # Redis may not be available at import time (e.g., during tests or CI).
    pass
