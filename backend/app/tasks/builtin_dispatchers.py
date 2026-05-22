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


async def _begin_host_run(host_run_id: int, *, with_lock: bool = True) -> int | None:
    """Claim-or-defer + mark the ActionHostRun as running.

    Returns the host_id if the run was claimed, ``None`` if the row is
    missing OR if the host is busy with another op and we deferred.
    Callers MUST bail out when this returns None.

    Cross-table lock plumbing: like the action_host path, we serialize
    against any in-flight sync, host-targeted action, or group-targeted
    action that includes this host. Built-ins are first-class citizens
    of the queue — a `_builtin.drift_check` won't run while a sync is
    operating on the same host, and vice versa.

    ``with_lock=False`` skips the claim-or-defer step. Used by
    ``_builtin.sync`` which delegates to ``run_host_sync`` — that task
    runs its OWN claim-or-defer + dispatch-next-pending pair, so doing
    it again here would either double-claim or (worse) cause the inner
    sync to defer against the ActionHostRun's ``running`` row.
    """
    from sqlalchemy import select

    from app.db import task_session
    from app.models.action_run import ActionHostRun, ActionRun

    async with task_session() as db:
        host_run = (
            await db.execute(select(ActionHostRun).where(ActionHostRun.id == host_run_id))
        ).scalar_one_or_none()
        if host_run is None:
            logger.warning("builtin_dispatchers: ActionHostRun %d not found", host_run_id)
            return None
        host_id = host_run.host_id

        if with_lock:
            from app.tasks.host_lock import (
                acquire_host_lock,
                check_host_busy,
                format_pending_reason,
            )

            await acquire_host_lock(db, host_id)
            blocker = await check_host_busy(db, host_id)
            if blocker is not None:
                reason = await format_pending_reason(db, blocker)
                host_run.status = "pending"
                host_run.pending_reason = reason
                # Mark parent ActionRun pending too if it's a single-host
                # target; for multi-host (group with supports_host=True)
                # the parent reflects the aggregate, not one member.
                run_row = (
                    await db.execute(
                        select(ActionRun).where(ActionRun.id == host_run.action_run_id)
                    )
                ).scalar_one_or_none()
                if (
                    run_row is not None
                    and run_row.host_id is not None
                    and run_row.status
                    in (
                        "queued",
                        "running",
                    )
                ):
                    run_row.status = "pending"
                    run_row.pending_reason = reason
                await db.commit()
                logger.info(
                    "builtin_dispatchers: deferred host_run=%d (host %d busy: %s)",
                    host_run_id,
                    host_id,
                    reason,
                )
                return None

        host_run.status = "running"
        host_run.started_at = datetime.now(UTC)
        await db.commit()
        return host_id


async def _finish_host_run(
    host_run_id: int,
    *,
    succeeded: bool,
    error: str | None = None,
    dispatch_next: bool = True,
) -> None:
    """Persist terminal status AND optionally dispatch-next-pending.

    Mirrors the action_host pattern: after the per-host operation
    completes, fire the queue so any waiting sync / action can claim
    the freed host. Failures in the dispatcher are swallowed so they
    don't mask the real outcome.

    ``dispatch_next=False`` is used by ``_builtin.sync``: the underlying
    ``run_host_sync`` task already runs its own dispatch-next-pending in
    its finally hook, so this layer's dispatch would either fire
    redundantly or compete for the same pending row.
    """
    from sqlalchemy import select

    from app.db import task_session
    from app.models.action_run import ActionHostRun

    host_id_for_dispatch: int | None = None
    action_run_id_for_dispatch: int | None = None
    async with task_session() as db:
        host_run = (
            await db.execute(select(ActionHostRun).where(ActionHostRun.id == host_run_id))
        ).scalar_one_or_none()
        if host_run is None:
            return
        host_run.status = "succeeded" if succeeded else "failed"
        host_run.finished_at = datetime.now(UTC)
        if error is not None:
            host_run.error_message = error
        host_id_for_dispatch = host_run.host_id
        action_run_id_for_dispatch = host_run.action_run_id
        await db.commit()

    if not dispatch_next or host_id_for_dispatch is None:
        return
    from app.tasks.host_lock import dispatch_next_pending_for_host

    try:
        async with task_session() as db:
            await dispatch_next_pending_for_host(
                db,
                host_id_for_dispatch,
                exclude_action_run_id=action_run_id_for_dispatch,
            )
    except Exception:
        logger.exception(
            "builtin_dispatchers: dispatch-next-pending failed for host_id=%s after host_run_id=%s",
            host_id_for_dispatch,
            host_run_id,
        )


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
    from sqlalchemy import select

    from app.db import task_session
    from app.models.host import Host

    host_id = await _begin_host_run(host_run_id)
    if host_id is None:
        return

    # Snapshot the host's os_facts_collected_at before the call so we
    # can detect a silent SSH-error skip — collect_host_facts swallows
    # SSH/OSError/TimeoutError internally and returns success from
    # Celery's perspective even when no facts were collected.
    async with task_session() as db:
        host = (await db.execute(select(Host).where(Host.id == host_id))).scalar_one_or_none()
        before = host.os_facts_collected_at if host else None

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
        else:
            async with task_session() as db:
                host = (
                    await db.execute(select(Host).where(Host.id == host_id))
                ).scalar_one_or_none()
                after = host.os_facts_collected_at if host else None
            if after == before:
                # Collector ran but didn't advance the timestamp ⇒ SSH
                # failure (or no SSH key configured) was silently
                # swallowed. Surface as a failed ActionHostRun.
                succeeded = False
                error = (
                    "facts collection returned without writing — likely "
                    "SSH error or missing SSH key (see worker logs)"
                )
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
            host = (await db.execute(select(Host).where(Host.id == host_id))).scalar_one_or_none()
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

    # No claim-or-defer here: the underlying ``run_host_sync`` task
    # runs its own per-host gate + dispatch-next-pending. Layering
    # another gate on top would (a) double-mark this host as busy
    # via the ActionHostRun row, then (b) cause the inner sync to
    # observe its own caller's ``running`` row in ``check_host_busy``
    # and defer forever.
    host_id = await _begin_host_run(host_run_id, with_lock=False)
    if host_id is None:
        return

    params = await _load_action_run_parameters(action_run_id)
    raw_filter = params.get("module_filter") or ""
    module_filter: list[str] | None = [
        m.strip() for m in raw_filter.split(",") if m.strip()
    ] or None

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
            status = payload.get("status")
            if status == "success":
                pass  # succeeded as initialised
            elif status == "deferred":
                # The advisory lock was held by another sync job — ours
                # is queued behind it and the option-c dispatch-next
                # chain will run it when the in-flight job finishes.
                # The per-host work isn't done yet, but it's not a
                # failure either; treat as succeeded with a note.
                logger.info(
                    "_builtin.sync deferred (job_id=%s host_id=%s) — queued behind in-flight sync",
                    job_id,
                    host_id,
                )
            else:
                succeeded = False
                error = f"sync did not complete successfully (status={status!r})"

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
                logger.warning("_builtin.sync: SyncJob %d still %s after run", job_id, sj.status)
    except Exception as exc:  # noqa: BLE001
        succeeded = False
        error = str(exc)
        logger.exception("_builtin.sync failed for host %d", host_id)

    # dispatch_next=False: ``run_host_sync.apply`` above already ran
    # its own dispatch-next-pending in its finally; a second pick
    # here would race for the same pending row.
    await _finish_host_run(host_run_id, succeeded=succeeded, error=error, dispatch_next=False)
