"""Periodic sweeper for stale ``SyncJob`` rows.

Closes the crash-recovery hole the option-c orchestrator chain
deliberately deferred: if a Celery worker dies mid-task (OOM,
SIGKILL, container restart), the ``SyncJob`` is stuck in
``running`` forever, the orchestrator's ``_claim_or_defer`` gate
returns False for every subsequent request to that host, and the
host's queue is blocked until an operator manually intervenes.

The sweeper runs every 5 minutes, finds ``SyncJob`` rows that have
been ``running`` for more than 30 minutes (2× the worst-case
orchestrator timeout — 60s base + 120s × 7 modules = 15 min — with
headroom for retries and network slowness), flips them to
``failed`` via the same ``_finalise_run`` write path the orchestrator
uses on real failures, and dispatches the queued successor so the
host's pipeline resumes.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from app.db import task_session
from app.tasks import celery_app

logger = logging.getLogger(__name__)


# Stuck-job threshold. The orchestrator's worst-case timeout is
# ``60 + 120 × 7 = 900s = 15min`` (D3 in option-c). 30 min gives 2× that
# as headroom for retries, slow networks, and the periodic sweep cadence.
# Operators running with a much-bumped ``ansible.playbook_timeout`` should
# raise this constant in tandem.
STALE_THRESHOLD_MINUTES = 30

# How often the sweeper runs. 5 minutes means a stuck job is detected at
# most ``STALE_THRESHOLD_MINUTES + 5min`` after it started; the queue
# behind it is unblocked at the same time.
SWEEP_FREQUENCY_SECONDS = 300


async def _sweep_stale_syncs_async() -> dict:
    """Core sweeper logic. One sweep pass.

    Returns a summary suitable for Celery result inspection:
    ``{"swept": [job_ids…], "dispatched": [next_job_ids…]}``.
    """
    from app.models.sync_job import JobStatus, SyncJob
    from app.tasks.host_sync_orchestrator import (
        _dispatch_next_pending_for_host,
        _filter_from_module_type,
        _finalise_run,
        _resolve_modules,
    )

    cutoff = datetime.now(UTC) - timedelta(minutes=STALE_THRESHOLD_MINUTES)

    # First pass: find candidate row IDs in a short read transaction so
    # we don't hold a lock across the per-job processing below.
    async with task_session() as db:
        result = await db.execute(
            select(SyncJob.id).where(
                SyncJob.status == JobStatus.running,
                SyncJob.started_at.isnot(None),
                SyncJob.started_at < cutoff,
            )
        )
        stuck_ids: list[int] = [row[0] for row in result.all()]

    swept: list[int] = []
    dispatched: list[int] = []

    for job_id in stuck_ids:
        # Each stuck job in its own transaction. A re-read inside the
        # transaction guards against two sweeper invocations racing on
        # the same row: the second one sees ``status="failed"`` and
        # skips.
        async with task_session() as db:
            job = (
                await db.execute(select(SyncJob).where(SyncJob.id == job_id))
            ).scalar_one_or_none()
            if job is None or job.status != JobStatus.running:
                continue

            module_filter = _filter_from_module_type(job.module_type)
            seeded_modules = _resolve_modules(module_filter)
            synthesized_outcomes = {m: "error" for m in seeded_modules}
            error_message = (
                f"Stuck in 'running' for >{STALE_THRESHOLD_MINUTES} min "
                "— assumed worker died; swept by sync_sweeper."
            )

            await _finalise_run(
                db=db,
                job_id=job.id,
                host_id=job.host_id,
                module_filter=module_filter,
                seeded_modules=seeded_modules,
                module_outcomes=synthesized_outcomes,
                triggered_by_user_id=job.triggered_by_user_id,
                firewall_pre_error=False,
                error_message=error_message,
            )
            swept.append(job.id)

            # Dispatch the queued successor in a fresh session so the
            # finalise commit is durable before the next task picks it
            # up (mirrors the orchestrator's finally-block ordering).
            host_id = job.host_id

        async with task_session() as db:
            next_id = await _dispatch_next_pending_for_host(
                db, host_id=host_id, exclude_job_id=job_id
            )
            if next_id is not None:
                dispatched.append(next_id)

    if swept:
        logger.warning(
            "sync_sweeper: marked %d stuck SyncJob(s) as failed, dispatched %d queued successor(s)",
            len(swept),
            len(dispatched),
        )

    return {"swept": swept, "dispatched": dispatched}


@celery_app.task(
    name="app.tasks.sync_sweeper.sweep_stale_syncs",
    queue="default",
)
def sweep_stale_syncs() -> dict:
    """Celery entrypoint. Drives the async sweeper inside ``asyncio.run``."""
    import asyncio

    return asyncio.run(_sweep_stale_syncs_async())


# ---------------------------------------------------------------------------
# RedBeat registration on module import. Mirrors the pattern in
# ``scan_schedule.py``: try/except so test-time imports without Redis
# don't blow up.
# ---------------------------------------------------------------------------


def _register_beat_schedule() -> None:
    from celery.schedules import schedule
    from redbeat import RedBeatSchedulerEntry

    entry = RedBeatSchedulerEntry(
        name="app.tasks.sync_sweeper.sweep_stale_syncs",
        task="app.tasks.sync_sweeper.sweep_stale_syncs",
        schedule=schedule(run_every=SWEEP_FREQUENCY_SECONDS),
        app=celery_app,
    )
    entry.save()


try:
    _register_beat_schedule()
except Exception:
    pass
