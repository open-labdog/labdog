"""Per-host Celery tasks for the three built-in pseudo-actions.

The action orchestrator (``app.tasks.action_orchestrator.run_action``)
forks per-host work to one of these wrappers when ``ActionRun.action_key``
starts with ``_builtin.``. Each wrapper:

1. Marks ``ActionHostRun.status="running"`` + ``started_at``.
2. Performs the underlying built-in operation against the host (sync,
   drift check, or facts collection).
3. Writes ``ActionHostRun.status="succeeded"|"failed"`` and
   ``finished_at`` so the orchestrator's Phase-3 status aggregation
   produces the right run-level outcome.

Pre-existing Celery tasks (``app.tasks.facts.collect_host_facts``,
``app.tasks.drift.check_all_drift``, ``app.tasks.host_sync_orchestrator
.run_host_sync``) are reused as the underlying engines so this module
stays a thin coordinator — the actual business logic doesn't move.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime

from app.tasks import celery_app

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Common helpers
# ---------------------------------------------------------------------------


async def _begin_host_run(host_run_id: int) -> int | None:
    """Mark the ActionHostRun as running. Returns the host_id or None
    if the row is missing (caller should bail out)."""
    from sqlalchemy import select

    from app.db import task_session
    from app.models.action_run import ActionHostRun

    async with task_session() as db:
        host_run = (
            await db.execute(
                select(ActionHostRun).where(ActionHostRun.id == host_run_id)
            )
        ).scalar_one_or_none()
        if host_run is None:
            logger.warning("builtin_dispatchers: ActionHostRun %d not found", host_run_id)
            return None
        host_run.status = "running"
        host_run.started_at = datetime.now(UTC)
        host_id = host_run.host_id
        await db.commit()
        return host_id


async def _finish_host_run(
    host_run_id: int, *, succeeded: bool, error: str | None = None
) -> None:
    from sqlalchemy import select

    from app.db import task_session
    from app.models.action_run import ActionHostRun

    async with task_session() as db:
        host_run = (
            await db.execute(
                select(ActionHostRun).where(ActionHostRun.id == host_run_id)
            )
        ).scalar_one_or_none()
        if host_run is None:
            return
        host_run.status = "succeeded" if succeeded else "failed"
        host_run.finished_at = datetime.now(UTC)
        if error is not None:
            host_run.error_message = error
        await db.commit()


async def _load_action_run_parameters(action_run_id: int) -> dict:
    from sqlalchemy import select

    from app.db import task_session
    from app.models.action_run import ActionRun

    async with task_session() as db:
        run = (
            await db.execute(select(ActionRun).where(ActionRun.id == action_run_id))
        ).scalar_one_or_none()
        return dict(run.parameters) if run else {}


# ---------------------------------------------------------------------------
# _builtin.collect_state
# ---------------------------------------------------------------------------


@celery_app.task(
    name="app.tasks.builtin_dispatchers.run_builtin_collect_state",
    queue="long_running",
)
def run_builtin_collect_state(action_run_id: int, host_run_id: int) -> dict:
    """Refresh cached host facts for one host."""
    asyncio.run(_collect_state_async(action_run_id, host_run_id))
    return {"action_run_id": action_run_id, "host_run_id": host_run_id}


async def _collect_state_async(action_run_id: int, host_run_id: int) -> None:
    host_id = await _begin_host_run(host_run_id)
    if host_id is None:
        return
    succeeded = True
    error: str | None = None
    try:
        from app.tasks.facts import collect_host_facts

        # Synchronously invoke the existing collector — running inside
        # the same worker is fine because we're already on a long_running
        # queue and the orchestrator doesn't want a fan-out grandchild.
        result = collect_host_facts.apply(args=[host_id])
        if result.failed():
            succeeded = False
            error = str(result.result)
    except Exception as exc:  # noqa: BLE001
        succeeded = False
        error = str(exc)
        logger.exception("collect_state failed for host %d", host_id)

    await _finish_host_run(host_run_id, succeeded=succeeded, error=error)


# ---------------------------------------------------------------------------
# _builtin.drift_check
# ---------------------------------------------------------------------------


@celery_app.task(
    name="app.tasks.builtin_dispatchers.run_builtin_drift_check",
    queue="long_running",
)
def run_builtin_drift_check(action_run_id: int, host_run_id: int) -> dict:
    """Drift-check one host using the same logic as the periodic sweep."""
    asyncio.run(_drift_check_async(action_run_id, host_run_id))
    return {"action_run_id": action_run_id, "host_run_id": host_run_id}


async def _drift_check_async(action_run_id: int, host_run_id: int) -> None:
    from sqlalchemy import select

    from app.db import task_session
    from app.models.host import Host
    from app.tasks.drift import _check_drift_for_one_host

    host_id = await _begin_host_run(host_run_id)
    if host_id is None:
        return

    succeeded = True
    error: str | None = None
    try:
        async with task_session() as db:
            host = (
                await db.execute(select(Host).where(Host.id == host_id))
            ).scalar_one_or_none()
            if host is None:
                succeeded = False
                error = f"Host {host_id} not found"
            else:
                # _check_drift_for_one_host already swallows SSH/network
                # errors and writes them as host.sync_status — for our
                # ActionHostRun status we treat skipped (firewall_backend
                # unknown) as succeeded too, matching the periodic sweep.
                await _check_drift_for_one_host(host, db)
    except Exception as exc:  # noqa: BLE001
        succeeded = False
        error = str(exc)
        logger.exception("drift_check failed for host %d", host_id)

    await _finish_host_run(host_run_id, succeeded=succeeded, error=error)


# ---------------------------------------------------------------------------
# _builtin.sync
# ---------------------------------------------------------------------------


@celery_app.task(
    name="app.tasks.builtin_dispatchers.run_builtin_sync",
    queue="long_running",
)
def run_builtin_sync(action_run_id: int, host_run_id: int) -> dict:
    """Coalesced per-host sync via the option-c orchestrator.

    Reads ``module_filter`` from the parent ActionRun's parameters
    (string, comma-separated; empty means all modules), creates a
    SyncJob row, and synchronously dispatches ``run_host_sync``. The
    SyncJob's terminal status maps to our ActionHostRun status:
    ``success → succeeded``, anything else → ``failed``.
    """
    asyncio.run(_sync_async(action_run_id, host_run_id))
    return {"action_run_id": action_run_id, "host_run_id": host_run_id}


async def _sync_async(action_run_id: int, host_run_id: int) -> None:
    from sqlalchemy import select

    from app.db import task_session
    from app.models.sync_job import JobStatus, SyncJob

    host_id = await _begin_host_run(host_run_id)
    if host_id is None:
        return

    params = await _load_action_run_parameters(action_run_id)
    raw_filter = params.get("module_filter") or ""
    module_filter: list[str] | None = (
        [m.strip() for m in raw_filter.split(",") if m.strip()] or None
    )

    succeeded = True
    error: str | None = None
    try:
        # Create the SyncJob row that run_host_sync expects.
        async with task_session() as db:
            job = SyncJob(host_id=host_id, status=JobStatus.pending)
            db.add(job)
            await db.flush()
            job_id = job.id
            await db.commit()

        from app.tasks.host_sync_orchestrator import run_host_sync

        # Apply synchronously — same worker, same thread.
        result = run_host_sync.apply(args=[job_id, host_id, module_filter])
        if result.failed():
            succeeded = False
            error = str(result.result)
        else:
            payload = result.result or {}
            if payload.get("status") != "success":
                succeeded = False
                error = "sync did not complete successfully"

        # Reflect job status back for observability (best-effort).
        async with task_session() as db:
            sj = (
                await db.execute(select(SyncJob).where(SyncJob.id == job_id))
            ).scalar_one_or_none()
            if sj is not None and sj.status not in (
                JobStatus.success,
                JobStatus.failed,
            ):
                # The orchestrator should always finalise, but defensive.
                logger.warning(
                    "_builtin.sync: SyncJob %d still %s after run", job_id, sj.status
                )
    except Exception as exc:  # noqa: BLE001
        succeeded = False
        error = str(exc)
        logger.exception("_builtin.sync failed for host %d", host_id)

    await _finish_host_run(host_run_id, succeeded=succeeded, error=error)
