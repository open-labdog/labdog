"""Daily retention pruning for action_runs and sync_jobs.

BUG-73. ``audit_log`` and ``ssh_session_transcripts`` have had a
retention job since they were added; the run tables never did, and they
are the ones that grow fastest. ``ActionHostRun.output`` holds up to a
mebibyte of transcript per host per run — roughly 7 GB a year for a
nightly twenty-host action — in a table the claim protocol scans on
every sync, action and drift check.

Window: ``logging.run_retention_days`` (default 90, ``0`` = keep
forever), settable in ``labdog.toml`` or the settings UI. Kept separate
from ``logging.audit_retention_days`` deliberately: an audit trail is
usually wanted for longer than an ansible transcript, and an operator
who shortens one rarely means to shorten the other.

Only terminal rows are pruned. A ``queued``, ``pending`` or ``running``
row is live work — a run waiting behind a lock can sit for a long time
without that meaning it is stale — and reaping those belongs to the
sweepers, which understand deadlines.

Deleting an ``action_runs`` row takes its ``action_host_runs`` with it
(``ON DELETE CASCADE``), which is where the transcripts are.

Tasks:
    prune_old_action_runs -- deletes terminal action_runs past the window
    prune_old_sync_jobs   -- deletes terminal sync_jobs past the window
"""

from __future__ import annotations

import asyncio
import logging

from app.tasks import celery_app

logger = logging.getLogger(__name__)

#: Rows removed per statement. Deleting a year of transcripts in one
#: statement would hold locks and pile up WAL for as long as it took;
#: the job is idempotent and runs daily, so it can afford to loop.
_BATCH_SIZE = 1000

#: Safety stop, so a misconfiguration cannot turn one nightly run into an
#: unbounded loop. At the batch size above this is a million rows a night,
#: far more than any real backlog, and whatever is left goes tomorrow.
_MAX_BATCHES = 1000

#: Statuses that mean the work is over. Anything else is live and is the
#: sweepers' business, not retention's.
_TERMINAL_ACTION_RUN_STATUSES = ("succeeded", "partial", "failed", "cancelled")


@celery_app.task(
    name="app.tasks.run_retention.prune_old_action_runs",
    queue="default",
)
def prune_old_action_runs() -> dict:
    """Delete terminal action_runs older than ``logging.run_retention_days``."""
    return asyncio.run(_prune_action_runs())


@celery_app.task(
    name="app.tasks.run_retention.prune_old_sync_jobs",
    queue="default",
)
def prune_old_sync_jobs() -> dict:
    """Delete terminal sync_jobs older than ``logging.run_retention_days``."""
    return asyncio.run(_prune_sync_jobs())


async def _get_retention_days(db) -> int:
    """The configured window, read from the database rather than the cache.

    Same reasoning as ``audit_retention._get_retention_days``: this runs
    once a day and deletes irreversibly, so it is worth a query to read
    the value as stored.
    """
    from app.settings_service import get_setting_typed  # noqa: PLC0415

    return int(await get_setting_typed("logging.run_retention_days", db))


def _disabled(retention_days: int, what: str) -> dict:
    logger.info(
        "run_retention: retention disabled (%d) — keeping all %s rows",
        retention_days,
        what,
    )
    return {"deleted": 0, "retention_days": retention_days, "skipped": "retention disabled"}


async def _delete_in_batches(db, model, where) -> int:
    """Delete matching rows ``_BATCH_SIZE`` at a time, committing each batch.

    Postgres has no ``DELETE ... LIMIT``, so each batch selects the ids
    first. Committing per batch keeps any one transaction short, which
    matters on the table the claim protocol scans.
    """
    from sqlalchemy import delete, select  # noqa: PLC0415

    deleted = 0
    for _ in range(_MAX_BATCHES):
        ids = (await db.execute(select(model.id).where(where).limit(_BATCH_SIZE))).scalars().all()
        if not ids:
            break
        await db.execute(delete(model).where(model.id.in_(ids)))
        await db.commit()
        deleted += len(ids)
        if len(ids) < _BATCH_SIZE:
            break
    else:
        logger.warning(
            "run_retention: stopped after %d batches with rows still to delete; "
            "the rest go on the next run",
            _MAX_BATCHES,
        )
    return deleted


async def _prune_action_runs() -> dict:
    from datetime import UTC, datetime, timedelta  # noqa: PLC0415

    from app.db import task_session  # noqa: PLC0415
    from app.models.action_run import ActionRun  # noqa: PLC0415

    async with task_session() as db:
        retention_days = await _get_retention_days(db)
        # ``0`` means "keep forever". Without this guard the cutoff is
        # *now* and the value meaning "never delete" performs the largest
        # possible delete.
        if retention_days <= 0:
            return _disabled(retention_days, "action_runs")
        cutoff = datetime.now(UTC) - timedelta(days=retention_days)

        deleted = await _delete_in_batches(
            db,
            ActionRun,
            (ActionRun.created_at < cutoff) & ActionRun.status.in_(_TERMINAL_ACTION_RUN_STATUSES),
        )

    logger.info(
        "run_retention: pruned %d action_runs (and their host runs) older than %d days",
        deleted,
        retention_days,
    )
    return {"deleted": deleted, "retention_days": retention_days}


async def _prune_sync_jobs() -> dict:
    from datetime import UTC, datetime, timedelta  # noqa: PLC0415

    from app.db import task_session  # noqa: PLC0415
    from app.models.sync_job import JobStatus, SyncJob  # noqa: PLC0415

    terminal = [s for s in JobStatus if s not in (JobStatus.running, JobStatus.pending)]

    async with task_session() as db:
        retention_days = await _get_retention_days(db)
        if retention_days <= 0:
            return _disabled(retention_days, "sync_jobs")
        cutoff = datetime.now(UTC) - timedelta(days=retention_days)

        deleted = await _delete_in_batches(
            db,
            SyncJob,
            (SyncJob.created_at < cutoff) & SyncJob.status.in_(terminal),
        )

    logger.info(
        "run_retention: pruned %d sync_jobs older than %d days",
        deleted,
        retention_days,
    )
    return {"deleted": deleted, "retention_days": retention_days}


# ---------------------------------------------------------------------------
# RedBeat registration
# ---------------------------------------------------------------------------


def _register_beat_schedules() -> None:
    from app.tasks.beat_registry import ensure_entry  # noqa: PLC0415

    _SECONDS_PER_DAY = 86400

    for name, task in (
        ("prune-old-action-runs", "app.tasks.run_retention.prune_old_action_runs"),
        ("prune-old-sync-jobs", "app.tasks.run_retention.prune_old_sync_jobs"),
    ):
        ensure_entry(
            name=name,
            task=task,
            run_every_seconds=_SECONDS_PER_DAY,
            app=celery_app,
        )


# Registration happens from ``beat_init`` (app.tasks.beat_registry), not at
# import — see the note at the foot of ``audit_retention`` for what calling
# it here would break (BUG-70).
