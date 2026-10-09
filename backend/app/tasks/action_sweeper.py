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

An orchestrator is different: it is not doing the work, only handing it
out, so there is nothing to wait for when it dies. Its heartbeat going
stale is enough, and the run is handed to a new orchestrator that carries
on where the dead one stopped (BUG-101).
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import select, update

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
            dispatched += await _release_hosts([host_id], exclude_action_run_id=parent_run_id)

    return swept, dispatched


async def _release_hosts(host_ids, *, exclude_action_run_id: int | None) -> list[int]:
    """Dispatch the next pending op on each host; return what was dispatched.

    One session per host, as ``release_host_queue`` does and for the same
    reason: each dispatch takes that host's lock. A failure on one host is
    logged and does not stop the others.
    """
    from app.tasks.host_lock import dispatch_next_pending_for_host

    dispatched: list[int] = []
    for host_id in sorted(host_ids):
        try:
            async with task_session() as db:
                result = await dispatch_next_pending_for_host(
                    db, host_id, exclude_action_run_id=exclude_action_run_id
                )
                if result is not None:
                    dispatched.append(result[1])
        except Exception:
            logger.exception(
                "action_sweeper: dispatch-next-pending failed for host_id=%s",
                host_id,
            )
    return dispatched


async def _sweep_stale_runs(now: datetime) -> tuple[list[int], list[int], list[int], list[int]]:
    """Pass 2: reconcile parent ``ActionRun`` rows.

    * ``running`` with all children terminal → aggregate (the
      orchestrator died after its children finished, or Pass 1 just
      reaped them).
    * ``running`` past the whole-run deadline → failed; queued children
      cancelled; running children left for Pass 1 on a later sweep; the
      hosts the run was holding handed to whatever waits on them.
    * ``running`` with a stale orchestrator heartbeat → handed to a new
      orchestrator (:func:`app.tasks.action_orchestrator.resume_run`).
    * ``queued`` for over an hour with no orchestrator trace → failed.

    ``pending`` (legitimate host-busy wait) and ``cancelled`` rows are
    never touched.

    Returns ``(finalised_run_ids, failed_run_ids, resumed_run_ids,
    dispatched_ids)``.
    """
    from app.models.action_run import ActionHostRun, ActionRun
    from app.tasks.action_timeouts import ORCHESTRATOR_STALE_SECONDS, run_deadline_seconds

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
    resumed: list[int] = []
    dispatched: list[int] = []

    for run_id in candidate_ids:
        held: set[int] = set()
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
                        "never picked up by a worker within 1h of enqueue — swept by action_sweeper"
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
            deadline = run_deadline_seconds(run.action_key, max(1, len(children)), run.parallelism)
            if started is not None and started < now - timedelta(seconds=deadline):
                held = await _hosts_held(db, run, children)
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

            elif await _take_over(db, run, now, ORCHESTRATOR_STALE_SECONDS):
                resumed.append(run_id)

        # Outside the session above: each release takes its host's lock in
        # a session of its own. Ops deferred behind this run wait for an op
        # on the host to *finish*, and the sweeper failing the run is not
        # one, so without this they would wait until something else
        # happened to run on that host (BUG-101).
        if held:
            dispatched += await _release_hosts(held, exclude_action_run_id=run_id)

    return finalised, failed, resumed, dispatched


async def _hosts_held(db, run, children) -> set[int]:
    """The hosts ``check_host_busy`` counts as busy because of this run.

    A host-targeted run holds its host for as long as it is ``running``,
    whatever its per-host row is doing. A group run holds each member its
    row is ``running`` on — or, before it has rows at all, every member of
    its group (the claim window of a group dispatch). Those holds lapse
    the moment the run stops being ``running``, so these are the hosts to
    release when the sweeper ends it.
    """
    from app.models.host import HostGroupMembership  # noqa: PLC0415

    held = {c.host_id for c in children if c.status == "running" and c.host_id is not None}
    if run.host_id is not None:
        held.add(run.host_id)
    elif not children and run.group_id is not None:
        held.update(
            (
                await db.execute(
                    select(HostGroupMembership.c.host_id).where(
                        HostGroupMembership.c.group_id == run.group_id
                    )
                )
            )
            .scalars()
            .all()
        )
    return held


async def _take_over(db, run, now: datetime, stale_after: int) -> bool:
    """Hand a run whose orchestrator has died to a new one.

    The orchestrator writes ``heartbeat_at`` every few seconds while it
    drives the run, and clears it when it stops driving it, so a stale one
    means it died mid-run — LabDog's own host upgrading Docker and
    restarting the container under it, on lin-manager. The run then sat
    ``running`` with its remaining hosts ``queued`` until the whole-run
    deadline, hours later, failed it (BUG-101).

    The new owner is written before the task is sent, conditionally on the
    heartbeat still being stale, so a live orchestrator that beats in the
    meantime keeps its run; one that comes back after the hand-over finds
    it no longer owns the run and stops.
    """
    from app.models.action_run import ActionRun  # noqa: PLC0415

    heartbeat = _aware(run.heartbeat_at)
    stale_before = now - timedelta(seconds=stale_after)
    if heartbeat is None or heartbeat >= stale_before:
        return False

    new_owner = str(uuid.uuid4())
    taken = (
        await db.execute(
            update(ActionRun)
            .where(
                ActionRun.id == run.id,
                ActionRun.status == "running",
                ActionRun.heartbeat_at < stale_before,
            )
            .values(orchestrator_id=new_owner, heartbeat_at=now)
            .returning(ActionRun.id)
        )
    ).scalar_one_or_none()
    await db.commit()
    if taken is None:
        return False

    logger.warning(
        "action_sweeper: action_run %d lost its orchestrator (last heartbeat %s); resuming it",
        run.id,
        heartbeat.isoformat(),
    )
    try:
        celery_app.send_task(
            "app.tasks.action_orchestrator.resume_run", args=[run.id], task_id=new_owner
        )
    except Exception:
        # Owned by a task that never left: its heartbeat goes stale like
        # any other, and the next sweep tries again.
        logger.exception("action_sweeper: could not send resume for action_run %d", run.id)
    return True


async def _sweep_stale_action_runs_async() -> dict:
    """One sweep pass. Returns a summary for Celery result inspection."""
    from app.actions.registry import ensure_registry_current  # noqa: PLC0415

    # Every deadline below reads the action's own timeouts from the
    # registry. On a pool process still holding the built-ins alone, a
    # pack action with a long ``playbook_timeout_seconds`` got the
    # global default instead, and was swept while it was still running
    # (BUG-105).
    async with task_session() as db:
        await ensure_registry_current(db)

    now = datetime.now(UTC)

    host_runs_swept, dispatched = await _sweep_stale_host_runs(now)
    runs_finalised, runs_failed, runs_resumed, released = await _sweep_stale_runs(now)
    dispatched += released

    if host_runs_swept or runs_finalised or runs_failed or runs_resumed:
        logger.warning(
            "action_sweeper: swept %d stuck host-run(s), finalised %d run(s), "
            "failed %d run(s), resumed %d run(s), dispatched %d queued successor(s)",
            len(host_runs_swept),
            len(runs_finalised),
            len(runs_failed),
            len(runs_resumed),
            len(dispatched),
        )

    return {
        "host_runs_swept": host_runs_swept,
        "runs_finalised": runs_finalised,
        "runs_failed": runs_failed,
        "runs_resumed": runs_resumed,
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
    from app.tasks.beat_registry import ensure_entry

    ensure_entry(
        name="app.tasks.action_sweeper.sweep_stale_action_runs",
        task="app.tasks.action_sweeper.sweep_stale_action_runs",
        run_every_seconds=SWEEP_FREQUENCY_SECONDS,
        app=celery_app,
    )


# Registration happens from ``beat_init`` (app.tasks.beat_registry), not at
# import. Calling it here rewrote the entry's ``due_at`` in every process
# that imported this module — API included — so on a deployment that
# restarts more than once a day, a daily job never fired at all (BUG-70).
