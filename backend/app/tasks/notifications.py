"""The notification drain, once a minute.

One tick does two things, in order: queue a warning for every approval
request close to expiring, then send everything due. The warning scan
lives here rather than in the approval reaper because the reaper runs
every fifteen minutes, and a two-hour warning should not arrive up to a
quarter of an hour late.

Once a minute is also the coalescing window. Everything queued for one
person in that minute goes out as one email.
"""

from __future__ import annotations

import asyncio
import logging

from app.tasks import celery_app

logger = logging.getLogger(__name__)

TICK_SECONDS = 60


@celery_app.task(name="app.tasks.notifications.deliver")
def deliver() -> dict:
    """Queue expiry warnings, then send what is due."""
    return asyncio.run(_deliver())


async def _deliver() -> dict:
    from app.db import task_session
    from app.notifications.delivery import drain
    from app.notifications.service import scan_expiring_approvals

    async with task_session() as db:
        warned = await scan_expiring_approvals(db)
        await db.commit()
        stats = await drain(db)
        await db.commit()
    return {"warned": warned, **stats}


@celery_app.task(name="app.tasks.notifications.prune_old_notifications")
def prune_old_notifications() -> dict:
    """Delete sent and failed notifications older than ``logging.run_retention_days``."""
    return asyncio.run(_prune())


async def _prune() -> dict:
    """The outbox is also the delivery log, so it would grow forever.

    Same window as run history: both answer "what happened lately?", and
    neither is the audit trail. Pending rows are never pruned — they are
    live work, and the drain gives up on them by itself.
    """
    from datetime import UTC, datetime, timedelta

    from app.db import task_session
    from app.notifications.models import Notification
    from app.tasks.run_retention import _delete_in_batches, _disabled, _get_retention_days

    async with task_session() as db:
        days = await _get_retention_days(db)
        if days <= 0:
            return _disabled(days, "notifications")
        cutoff = datetime.now(UTC) - timedelta(days=days)
        deleted = await _delete_in_batches(
            db,
            Notification,
            (Notification.created_at < cutoff) & Notification.status.in_(("sent", "failed")),
        )
    logger.info("notifications: pruned %d older than %d days", deleted, days)
    return {"deleted": deleted, "retention_days": days}


def _register_beat_schedules() -> None:
    from app.tasks.beat_registry import ensure_entry

    ensure_entry(
        name="deliver-notifications",
        task="app.tasks.notifications.deliver",
        run_every_seconds=TICK_SECONDS,
        app=celery_app,
    )
    ensure_entry(
        name="prune-old-notifications",
        task="app.tasks.notifications.prune_old_notifications",
        run_every_seconds=86400,
        app=celery_app,
    )


# Registration happens from ``beat_init`` (app.tasks.beat_registry), not at
# import — see BUG-70 in that module's docstring.
