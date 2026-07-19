"""Periodic sweeper for stale ``ActionRun`` / ``ActionHostRun`` rows.

Sibling of :mod:`app.tasks.sync_sweeper`, which covers ``SyncJob`` only.
If a Celery worker dies mid-action (OOM, SIGKILL at a time limit,
container restart), the rows it owned are stuck in ``running`` forever.
Two things then wedge silently:

* the scheduler skips any ``ScheduledAction`` with a non-terminal run
  (``check_due``'s in-flight guard), so the schedule never fires again;
* ``check_host_busy`` sees the ``running`` row and defers every new op
  on that host to ``pending`` — and only a *finishing* op dispatches
  the next pending one, which a dead worker never does.

The sweeper runs every 5 minutes. A row is only reaped once it is older
than its action's own deadline (ansible-runner wall-clock timeout +
verify + envelope grace — see :mod:`app.tasks.action_timeouts`), so a
legitimately slow run can never be swept: ansible's own timeout always
fires first on a live worker.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from app.db import task_session
from app.tasks import celery_app

logger = logging.getLogger(__name__)

# Same cadence as sync_sweeper: a stuck row is reaped at most
# deadline + 5 min after it started.
SWEEP_FREQUENCY_SECONDS = 300

# A run still ``queued`` this long after creation with no sign of an
# orchestrator (no started_at, no children) was lost by the broker or
# its send_task never landed; generous even for a backlogged homelab.
QUEUED_ORPHAN_THRESHOLD_SECONDS = 3600

_TERMINAL_HOST_STATUSES = ("succeeded", "failed", "skipped", "cancelled")


def _aware(dt: datetime | None) -> datetime | None:
    if dt is not None and dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt


async def _sweep_stale_host_runs(now: datetime) -> tuple[list[int], list[int]]:
    """Pass 1: fail ``running`` ActionHostRuns older than their per-host
    deadline, then dispatch the next pending op for each freed host.

    Returns ``(swept_host_run_ids, dispatched_ids)``.
    """
    from app.models.action_run import ActionHostRun, ActionRun
    from app.tasks.action_timeouts import per_host_deadline_seconds
    from app.tasks.host_lock import dispatch_next_pending_for_host

    # Candidate scan in a short read transaction; per-row deadline check
    # happens against the joined parent's action_key.
    async with task_session() as db:
        rows = (
            await db.execute(
                select(ActionHostRun.id, ActionHostRun.started_at, ActionRun.action_key)
                .join(ActionRun, ActionRun.id == ActionHostRun.action_run_id)
                .where(
                    ActionHostRun.status == "running",
                    ActionHostRun.started_at.isnot(None),
                )
            )
        ).all()

    stale: list[tuple[int, int]] = []  # (host_run_id, deadline_seconds)
    deadline_cache: dict[str, int] = {}
    for host_run_id, started_at, action_key in rows:
        deadline = deadline_cache.setdefault(action_key, per_host_deadline_seconds(action_key))
        if _aware(started_at) < now - timedelta(seconds=deadline):
            stale.append((host_run_id, deadline))

    swept: list[int] = []
    dispatched: list[int] = []

    for host_run_id, deadline in stale:
        # Each row in its own transaction with a re-read: a second
        # sweeper invocation (or the worker finishing against all odds)
        # sees a terminal status and skips.
        host_id: int | None = None
        parent_run_id: int | None = None
        async with task_session() as db:
            hr = (
                await db.execute(select(ActionHostRun).where(ActionHostRun.id == host_run_id))
            ).scalar_one_or_none()
            if hr is None or hr.status != "running":
                continue
            hr.status = "failed"
            hr.finished_at = now
            hr.error_message = (
                f"Stuck in 'running' past deadline ({deadline}s = playbook timeout "
                "+ verify + grace) — worker presumed dead (killed/restarted); "
                "swept by action_sweeper."
            )
            host_id = hr.host_id
            parent_run_id = hr.action_run_id
            await db.commit()
            swept.append(host_run_id)

        # Free the host's queue in a fresh session so the finalise
        # commit above is durable first (mirrors the executors'
        # finally-block ordering).
        if host_id is not None:
            try:
                async with task_session() as db:
                    result = await dispatch_next_pending_for_host(
                        db, host_id, exclude_action_run_id=parent_run_id
                    )
                    if result is not None:
                        dispatched.append(result[1])
            except Exception:
                logger.exception(
                    "action_sweeper: dispatch-next-pending failed for host_id=%s",
                    host_id,
                )

    return swept, dispatched


async def _sweep_stale_runs(now: datetime) -> tuple[list[int], list[int]]:
    """Pass 2: reconcile parent ``ActionRun`` rows.

    * ``running`` with all children terminal → aggregate (the
      orchestrator died after its children finished, or Pass 1 just
      reaped them).
    * ``running`` past the whole-run deadline → failed; queued children
      cancelled; running children left for Pass 1 on a later sweep.
    * ``queued`` for over an hour with no orchestrator trace → failed.

    ``pending`` (legitimate host-busy wait) and ``cancelled`` rows are
    never touched.

    Returns ``(finalised_run_ids, failed_run_ids)``.
    """
    from app.models.action_run import ActionHostRun, ActionRun
    from app.tasks.action_timeouts import run_deadline_seconds

    async with task_session() as db:
        candidate_ids = [
            row[0]
            for row in (
                await db.execute(
                    select(ActionRun.id).where(ActionRun.status.in_(("running", "queued")))
                )
            ).all()
        ]

    finalised: list[int] = []
    failed: list[int] = []

    for run_id in candidate_ids:
        async with task_session() as db:
            run = (
                await db.execute(select(ActionRun).where(ActionRun.id == run_id))
            ).scalar_one_or_none()
            if run is None or run.status not in ("running", "queued"):
                continue

            children = list(
                (
                    await db.execute(
                        select(ActionHostRun).where(ActionHostRun.action_run_id == run_id)
                    )
                )
                .scalars()
                .all()
            )

            if run.status == "queued":
                # Never picked up. Only reap when there's no trace of an
                # orchestrator (children/started_at) and the queue delay
                # is far past anything a healthy broker produces.
                created = _aware(run.created_at)
                if (
                    not children
                    and run.started_at is None
                    and created is not None
                    and created < now - timedelta(seconds=QUEUED_ORPHAN_THRESHOLD_SECONDS)
                ):
                    run.status = "failed"
                    run.finished_at = now
                    run.error_message = (
                        "never picked up by a worker within 1h of enqueue — "
                        "swept by action_sweeper"
                    )
                    await db.commit()
                    failed.append(run_id)
                continue

            # status == "running" from here on.
            if children and all(c.status in _TERMINAL_HOST_STATUSES for c in children):
                # Orchestrator died after (or as) its children finished —
                # aggregate exactly like its Phase 3 would have.
                succeeded = sum(1 for c in children if c.status == "succeeded")
                child_failed = sum(1 for c in children if c.status == "failed")
                if child_failed == 0:
                    run.status = "succeeded"
                elif succeeded == 0:
                    run.status = "failed"
                    run.error_message = run.error_message or (
                        "all hosts failed; run finalised by action_sweeper "
                        "(orchestrator presumed dead)"
                    )
                else:
                    run.status = "partial"
                run.finished_at = now
                await db.commit()
                finalised.append(run_id)
                continue

            started = _aware(run.started_at) or _aware(run.created_at)
            deadline = run_deadline_seconds(
                run.action_key, max(1, len(children)), run.parallelism
            )
            if started is not None and started < now - timedelta(seconds=deadline):
                run.status = "failed"
                run.finished_at = now
                run.error_message = (
                    f"run exceeded overall deadline ({deadline}s); orchestrator "
                    "presumed dead — swept by action_sweeper"
                )
                for c in children:
                    if c.status in ("queued", "pending"):
                        c.status = "cancelled"
                        c.error_message = "parent run swept by action_sweeper"
                        c.finished_at = now
                    # ``running`` children keep their own per-host
                    # deadline; Pass 1 reaps them on a later sweep.
                await db.commit()
                failed.append(run_id)

    return finalised, failed


async def _sweep_stale_action_runs_async() -> dict:
    """One sweep pass. Returns a summary for Celery result inspection."""
    now = datetime.now(UTC)

    host_runs_swept, dispatched = await _sweep_stale_host_runs(now)
    runs_finalised, runs_failed = await _sweep_stale_runs(now)

    if host_runs_swept or runs_finalised or runs_failed:
        logger.warning(
            "action_sweeper: swept %d stuck host-run(s), finalised %d run(s), "
            "failed %d run(s), dispatched %d queued successor(s)",
            len(host_runs_swept),
            len(runs_finalised),
            len(runs_failed),
            len(dispatched),
        )

    return {
        "host_runs_swept": host_runs_swept,
        "runs_finalised": runs_finalised,
        "runs_failed": runs_failed,
        "dispatched": dispatched,
    }


@celery_app.task(
    name="app.tasks.action_sweeper.sweep_stale_action_runs",
    queue="default",
)
def sweep_stale_action_runs() -> dict:
    """Celery entrypoint. Drives the async sweeper inside ``asyncio.run``."""
    import asyncio

    return asyncio.run(_sweep_stale_action_runs_async())


# ---------------------------------------------------------------------------
# RedBeat registration on module import. Mirrors sync_sweeper.py: try/except
# so test-time imports without Redis don't blow up.
# ---------------------------------------------------------------------------


def _register_beat_schedule() -> None:
    from celery.schedules import schedule
    from redbeat import RedBeatSchedulerEntry

    entry = RedBeatSchedulerEntry(
        name="app.tasks.action_sweeper.sweep_stale_action_runs",
        task="app.tasks.action_sweeper.sweep_stale_action_runs",
        schedule=schedule(run_every=SWEEP_FREQUENCY_SECONDS),
        app=celery_app,
    )
    entry.save()


try:
    _register_beat_schedule()
except Exception:
    pass
