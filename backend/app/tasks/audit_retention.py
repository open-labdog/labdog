"""Daily retention pruning for audit_log and ssh_session_transcripts tables.

Both tables are treated as audit data and share the same retention window:
``logging.audit_retention_days`` (default 90, overridable via
``logging.audit_retention_days`` in labdog.toml or the settings UI).

Tasks:
    prune_old_audit_logs         -- deletes audit_log rows older than the window
    prune_old_ssh_transcripts    -- deletes ssh_session_transcripts rows older than
                                    the window

Both tasks are registered with RedBeat at module import and run daily.
"""

from __future__ import annotations

import asyncio
import logging

from app.tasks import celery_app

logger = logging.getLogger(__name__)


@celery_app.task(
    name="app.tasks.audit_retention.prune_old_audit_logs",
    queue="default",
)
def prune_old_audit_logs() -> dict:
    """Delete audit_log rows older than ``logging.audit_retention_days``."""
    return asyncio.run(_prune_audit_logs())


@celery_app.task(
    name="app.tasks.audit_retention.prune_old_ssh_transcripts",
    queue="default",
)
def prune_old_ssh_transcripts() -> dict:
    """Delete ssh_session_transcripts rows older than ``logging.audit_retention_days``."""
    return asyncio.run(_prune_ssh_transcripts())


async def _get_retention_days() -> int:
    from app.settings_service import get_setting_sync_typed  # noqa: PLC0415

    return int(get_setting_sync_typed("logging.audit_retention_days"))


async def _prune_audit_logs() -> dict:
    from datetime import UTC, datetime, timedelta  # noqa: PLC0415

    from sqlalchemy import delete, func  # noqa: PLC0415

    from app.db import task_session  # noqa: PLC0415
    from app.models.audit_log import AuditLog  # noqa: PLC0415

    retention_days = await _get_retention_days()
    cutoff = datetime.now(UTC) - timedelta(days=retention_days)

    async with task_session() as db:
        # Count before delete for reporting.
        count_result = await db.execute(
            func.count(AuditLog.id).select().where(AuditLog.created_at < cutoff)
        )
        count = count_result.scalar() or 0

        await db.execute(delete(AuditLog).where(AuditLog.created_at < cutoff))
        await db.commit()

    logger.info(
        "audit_retention: pruned %d audit_log rows older than %d days",
        count,
        retention_days,
    )
    return {"deleted": count, "retention_days": retention_days}


async def _prune_ssh_transcripts() -> dict:
    from datetime import UTC, datetime, timedelta  # noqa: PLC0415

    from sqlalchemy import delete, func  # noqa: PLC0415

    from app.db import task_session  # noqa: PLC0415
    from app.models.ssh_session_transcript import SSHSessionTranscript  # noqa: PLC0415

    retention_days = await _get_retention_days()
    cutoff = datetime.now(UTC) - timedelta(days=retention_days)

    async with task_session() as db:
        count_result = await db.execute(
            func.count(SSHSessionTranscript.id)
            .select()
            .where(SSHSessionTranscript.recorded_at < cutoff)
        )
        count = count_result.scalar() or 0

        await db.execute(
            delete(SSHSessionTranscript).where(SSHSessionTranscript.recorded_at < cutoff)
        )
        await db.commit()

    logger.info(
        "audit_retention: pruned %d ssh_session_transcripts rows older than %d days",
        count,
        retention_days,
    )
    return {"deleted": count, "retention_days": retention_days}


# ---------------------------------------------------------------------------
# RedBeat registration
# ---------------------------------------------------------------------------


def _register_beat_schedules() -> None:
    from celery.schedules import schedule  # noqa: PLC0415
    from redbeat import RedBeatSchedulerEntry  # noqa: PLC0415

    _SECONDS_PER_DAY = 86400

    for name, task in (
        (
            "prune-old-audit-logs",
            "app.tasks.audit_retention.prune_old_audit_logs",
        ),
        (
            "prune-old-ssh-transcripts",
            "app.tasks.audit_retention.prune_old_ssh_transcripts",
        ),
    ):
        entry = RedBeatSchedulerEntry(
            name=name,
            task=task,
            schedule=schedule(run_every=_SECONDS_PER_DAY),
            app=celery_app,
        )
        entry.save()


try:
    _register_beat_schedules()
except Exception:
    pass  # nosec B110 — Redis may not be available at import time (e.g. during tests)
