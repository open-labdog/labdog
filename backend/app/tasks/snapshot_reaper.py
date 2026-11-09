"""Hourly removal of action-run snapshots that outlived their run (BUG-169).

A failed host's pre-run snapshot can stay behind: kept on purpose when
``auto_rollback`` is off or the rollback failed, or left by a delete that
failed. The action paths said such a snapshot stays "for the periodic
cleanup task to reap", but until this module nothing ran
:func:`~app.workflows.snapshot_cleanup.cleanup_orphaned_snapshots` except
the manual ``POST /api/proxmox-nodes/cleanup-snapshots``, so they piled up
on the VMs until someone removed them by hand.

The sweep removes ``labdog-<run>-<ts>`` snapshots older than
``workflow.snapshot_max_age_hours`` whose run is not still using them. It
gives a finished run's snapshot the same grace as one of unknown origin,
so an operator has that long to restore a kept snapshot by hand. AI
sessions' snapshots have their own sweep (:mod:`app.tasks.ai_snapshots`).

Tasks:
    reap_orphaned_snapshots -- hourly; deletes leftover action-run snapshots
"""

from __future__ import annotations

import asyncio
import logging

from app.db import with_task_engine
from app.tasks import celery_app

logger = logging.getLogger(__name__)

#: Hourly. The window is set in hours (1 to 168), so a sweep a day could
#: keep a snapshot for almost a day longer than the setting says.
SWEEP_INTERVAL_SECONDS = 3_600


@celery_app.task(
    name="app.tasks.snapshot_reaper.reap_orphaned_snapshots",
    queue="default",
)
def reap_orphaned_snapshots() -> dict:
    """Delete action-run snapshots older than ``workflow.snapshot_max_age_hours``."""
    return asyncio.run(with_task_engine(_reap_orphaned_snapshots()))


async def _reap_orphaned_snapshots() -> dict:
    from app.db import task_session  # noqa: PLC0415
    from app.workflows.snapshot_cleanup import cleanup_orphaned_snapshots  # noqa: PLC0415

    async with task_session() as db:
        result = await cleanup_orphaned_snapshots(db, grace_for_finished_runs=True)
    if result["deleted"] or result["errors"]:
        logger.info(
            "snapshot_reaper: deleted %d snapshot(s), %d failed",
            result["deleted"],
            len(result["errors"]),
        )
    return result


# ---------------------------------------------------------------------------
# RedBeat registration
# ---------------------------------------------------------------------------


def _register_beat_schedules() -> None:
    from app.tasks.beat_registry import ensure_entry  # noqa: PLC0415

    ensure_entry(
        name="reap-orphaned-snapshots",
        task="app.tasks.snapshot_reaper.reap_orphaned_snapshots",
        run_every_seconds=SWEEP_INTERVAL_SECONDS,
        app=celery_app,
    )


# Registration happens from ``beat_init`` (app.tasks.beat_registry), not at
# import — see the note at the foot of ``audit_retention`` (BUG-70).
