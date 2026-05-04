"""Action orchestrator Celery task.

Resolves the target host(s) for an ActionRun, creates per-host
ActionHostRun records, then dispatches action_host tasks in batches
according to the run's parallelism setting.  Waits for each batch
before moving to the next; individual host failures do not abort
subsequent batches.
"""

import asyncio
import logging
from datetime import UTC, datetime

from app.tasks import celery_app

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Celery task entry point
# ---------------------------------------------------------------------------


@celery_app.task(
    bind=True,
    name="app.tasks.action_orchestrator.run_action",
    queue="long_running",
)
def run_action(self, action_run_id: int) -> dict:
    """Orchestrate an action run against one host or a group of hosts.

    Args:
        action_run_id: ID of the ActionRun record to process.

    Returns:
        A dict summarising the outcome, e.g. ``{"action_run_id": 1}``.
    """
    asyncio.run(_run_action_async(action_run_id))
    return {"action_run_id": action_run_id}


# ---------------------------------------------------------------------------
# Async implementation
# ---------------------------------------------------------------------------


async def _run_action_async(action_run_id: int) -> None:
    """Async implementation of :func:`run_action`."""
    from sqlalchemy import select

    from app.actions.registry import ACTION_REGISTRY
    from app.db import task_session
    from app.models.action_run import ActionHostRun, ActionRun
    from app.models.host import Host, HostGroupMembership

    # ------------------------------------------------------------------ #
    # Phase 1: initialise run and create per-host records                 #
    # ------------------------------------------------------------------ #
    host_run_ids: list[int] = []

    try:
        async with task_session() as db:
            # Load ActionRun and mark as running
            run_result = await db.execute(select(ActionRun).where(ActionRun.id == action_run_id))
            run: ActionRun = run_result.scalar_one()
            run.status = "running"
            run.started_at = datetime.now(UTC)
            await db.flush()

            # Validate action key
            action = ACTION_REGISTRY.get(run.action_key)
            if action is None:
                run.status = "failed"
                run.error_message = f"Unknown action key: {run.action_key}"
                run.finished_at = datetime.now(UTC)
                await db.commit()
                return

            # Resolve target hosts
            if run.host_id is not None:
                host_ids: list[int] = [run.host_id]
            else:
                hosts_result = await db.execute(
                    select(Host)
                    .join(HostGroupMembership, Host.id == HostGroupMembership.c.host_id)
                    .where(HostGroupMembership.c.group_id == run.group_id)
                )
                host_ids = [h.id for h in hosts_result.scalars().all()]

            if not host_ids:
                logger.warning(
                    "action_orchestrator: no hosts resolved for action_run %d",
                    action_run_id,
                )
                run.status = "succeeded"
                run.finished_at = datetime.now(UTC)
                await db.commit()
                return

            # Create ActionHostRun records
            for hid in host_ids:
                host_run = ActionHostRun(
                    action_run_id=action_run_id,
                    host_id=hid,
                    status="queued",
                )
                db.add(host_run)
                await db.flush()
                host_run_ids.append(host_run.id)

            parallelism: int = run.parallelism
            await db.commit()

    except Exception as exc:
        logger.exception(
            "action_orchestrator: initialisation failed for action_run %d",
            action_run_id,
        )
        await _mark_run_failed(action_run_id, str(exc))
        return

    # ------------------------------------------------------------------ #
    # Phase 2: dispatch per-host tasks in batches                         #
    # ------------------------------------------------------------------ #
    import redis as redis_lib
    from celery import group as celery_group
    from celery.result import allow_join_result

    from app.config import settings

    r = redis_lib.from_url(settings.redis.url)

    def is_cancelled() -> bool:
        return r.exists(f"actions.cancel.{action_run_id}") > 0

    try:
        if is_cancelled():
            await _mark_run_cancelled(action_run_id)
            return

        # parallelism <= 0 means "all at once"; >= 1 means N at a time
        batch_size = len(host_run_ids) if parallelism <= 0 else max(1, parallelism)
        batches = [
            host_run_ids[i : i + batch_size] for i in range(0, len(host_run_ids), batch_size)
        ]

        for batch_index, batch in enumerate(batches):
            if is_cancelled():
                await _mark_run_cancelled(action_run_id)
                return

            logger.info(
                "action_orchestrator: action_run %d dispatching batch %d/%d (%d hosts)",
                action_run_id,
                batch_index + 1,
                len(batches),
                len(batch),
            )

            tasks = celery_group(
                celery_app.signature(
                    "app.tasks.action_host.run_action_host",
                    args=[action_run_id, host_run_id],
                    queue="long_running",
                )
                for host_run_id in batch
            )
            result = tasks.apply_async()
            try:
                # Celery refuses synchronous result-waiting from within a task
                # by default (deadlock risk when the worker pool is saturated).
                # allow_join_result() is the documented opt-in for orchestrators
                # that genuinely need to block until their children finish.
                with allow_join_result():
                    result.join(timeout=3600, propagate=False)
            except Exception as exc:
                logger.warning(
                    "action_orchestrator: action_run %d batch %d wait error: %s",
                    action_run_id,
                    batch_index + 1,
                    exc,
                )

    except Exception as exc:
        logger.exception(
            "action_orchestrator: unhandled error during batch dispatch for action_run %d",
            action_run_id,
        )
        await _mark_run_failed(action_run_id, str(exc))
        return

    # ------------------------------------------------------------------ #
    # Phase 3: aggregate final status                                     #
    # ------------------------------------------------------------------ #
    try:
        import json

        from sqlalchemy import select

        from app.db import task_session
        from app.models.action_run import ActionHostRun, ActionRun

        async with task_session() as db:
            hr_result = await db.execute(
                select(ActionHostRun).where(ActionHostRun.action_run_id == action_run_id)
            )
            host_runs = list(hr_result.scalars().all())

            succeeded = sum(1 for hr in host_runs if hr.status == "succeeded")
            failed = sum(1 for hr in host_runs if hr.status == "failed")
            total = len(host_runs)

            if failed == 0:
                final_status = "succeeded"
            elif succeeded == 0:
                final_status = "failed"
            else:
                final_status = "partial"

            run_result = await db.execute(select(ActionRun).where(ActionRun.id == action_run_id))
            run = run_result.scalar_one()
            # Only overwrite status if the run was not cancelled mid-flight
            if run.status != "cancelled":
                run.status = final_status
                run.finished_at = datetime.now(UTC)
            await db.commit()

            logger.info(
                "action_orchestrator: action_run %d finished — %s (%d/%d hosts succeeded)",
                action_run_id,
                run.status,
                succeeded,
                total,
            )

        # Publish terminal event to SSE channel
        r.publish(
            f"actions.run.{action_run_id}",
            json.dumps({"event": "status", "status": run.status}),
        )

    except Exception as exc:
        logger.exception(
            "action_orchestrator: status aggregation failed for action_run %d",
            action_run_id,
        )
        await _mark_run_failed(action_run_id, str(exc))


# ---------------------------------------------------------------------------
# Helper utilities
# ---------------------------------------------------------------------------


async def _mark_run_failed(action_run_id: int, error_message: str) -> None:
    """Best-effort: set ActionRun status to failed with an error message."""
    try:
        from sqlalchemy import select

        from app.db import task_session
        from app.models.action_run import ActionRun

        async with task_session() as db:
            result = await db.execute(select(ActionRun).where(ActionRun.id == action_run_id))
            run = result.scalar_one_or_none()
            if run is not None:
                run.status = "failed"
                run.error_message = error_message
                run.finished_at = datetime.now(UTC)
                await db.commit()
    except Exception:
        logger.exception(
            "action_orchestrator: could not mark action_run %d as failed",
            action_run_id,
        )


async def _mark_run_cancelled(action_run_id: int) -> None:
    """Best-effort: set ActionRun and queued ActionHostRun records to cancelled."""
    try:
        import json

        import redis as redis_lib
        from sqlalchemy import select

        from app.config import settings
        from app.db import task_session
        from app.models.action_run import ActionHostRun, ActionRun

        async with task_session() as db:
            run_result = await db.execute(select(ActionRun).where(ActionRun.id == action_run_id))
            run = run_result.scalar_one_or_none()
            if run is not None:
                run.status = "cancelled"
                run.finished_at = datetime.now(UTC)

            # Mark still-queued host runs as cancelled
            hr_result = await db.execute(
                select(ActionHostRun).where(
                    ActionHostRun.action_run_id == action_run_id,
                    ActionHostRun.status == "queued",
                )
            )
            for hr in hr_result.scalars().all():
                hr.status = "cancelled"

            await db.commit()

        r = redis_lib.from_url(settings.redis.url)
        r.publish(
            f"actions.run.{action_run_id}",
            json.dumps({"event": "status", "status": "cancelled"}),
        )
    except Exception:
        logger.exception(
            "action_orchestrator: could not mark action_run %d as cancelled",
            action_run_id,
        )
