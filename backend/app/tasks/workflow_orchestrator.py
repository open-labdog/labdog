import asyncio
import logging
from datetime import UTC, datetime

from app.tasks import celery_app

logger = logging.getLogger(__name__)


@celery_app.task(
    bind=True,
    name="app.tasks.workflow_orchestrator.run_group_workflow",
    queue="long_running",
)
def run_group_workflow(self, workflow_id: int, run_id: int) -> dict:
    """Orchestrate a group update workflow run.

    Resolves the workflow's group to individual hosts, creates per-host run
    records, then dispatches host executor tasks in batches of
    ``workflow.batch_size``.  Waits for each batch to finish before starting
    the next; individual host failures do not abort subsequent batches.

    Args:
        workflow_id: ID of the UpdateWorkflow configuration record.
        run_id: ID of the WorkflowRun that was pre-created by the API caller.
    """
    asyncio.run(_run_group_workflow_async(workflow_id, run_id))
    return {"workflow_id": workflow_id, "run_id": run_id}


async def _run_group_workflow_async(workflow_id: int, run_id: int) -> None:
    """Async implementation of :func:`run_group_workflow`."""
    from sqlalchemy import select

    from app.db import task_session
    from app.models.host import Host, HostGroupMembership
    from app.workflows.models import (
        UpdateWorkflow,
        WorkflowHostRun,
        WorkflowHostStatus,
        WorkflowRun,
        WorkflowRunStatus,
        WorkflowStep,
    )

    # ------------------------------------------------------------------ #
    # Phase 1: initialise run and create per-host records                 #
    # ------------------------------------------------------------------ #
    host_run_ids: list[tuple[int, int]] = []  # (host_id, host_run_id)

    try:
        async with task_session() as db:
            # Load workflow configuration
            wf_result = await db.execute(
                select(UpdateWorkflow).where(UpdateWorkflow.id == workflow_id)
            )
            workflow: UpdateWorkflow = wf_result.scalar_one()

            # Mark run as started
            run_result = await db.execute(select(WorkflowRun).where(WorkflowRun.id == run_id))
            run: WorkflowRun = run_result.scalar_one()
            run.status = WorkflowRunStatus.running
            run.started_at = datetime.now(UTC)
            await db.flush()

            # Resolve group → hosts
            hosts_result = await db.execute(
                select(Host)
                .join(
                    HostGroupMembership,
                    Host.id == HostGroupMembership.c.host_id,
                )
                .where(HostGroupMembership.c.group_id == workflow.group_id)
            )
            hosts: list[Host] = list(hosts_result.scalars().all())

            if not hosts:
                logger.warning(
                    "workflow_orchestrator: no hosts in group %d for workflow %d",
                    workflow.group_id,
                    workflow_id,
                )
                run.status = WorkflowRunStatus.completed
                run.completed_at = datetime.now(UTC)
                await db.commit()
                return

            # Create WorkflowHostRun records (status=pending, step=preflight)
            # VM mappings are loaded from DB by the host executor — no
            # re-discovery needed here.  Users trigger discovery explicitly
            # via the Proxmox discovery API.
            for host in hosts:
                host_run = WorkflowHostRun(
                    run_id=run_id,
                    host_id=host.id,
                    status=WorkflowHostStatus.pending,
                    step=WorkflowStep.preflight,
                )
                db.add(host_run)
                await db.flush()
                host_run_ids.append((host.id, host_run.id))

            batch_size: int = max(1, workflow.batch_size)
            await db.commit()

    except Exception as exc:
        error_msg = str(exc)
        logger.exception("workflow_orchestrator: failed during initialisation of run %d", run_id)
        await _mark_run_failed(run_id, error_msg)
        return

    # ------------------------------------------------------------------ #
    # Phase 2: dispatch host tasks in batches                             #
    # ------------------------------------------------------------------ #
    from celery import group as celery_group

    try:
        batches = [
            host_run_ids[i : i + batch_size] for i in range(0, len(host_run_ids), batch_size)
        ]

        for batch_index, batch in enumerate(batches):
            logger.info(
                "workflow_orchestrator: run %d dispatching batch %d/%d (%d hosts)",
                run_id,
                batch_index + 1,
                len(batches),
                len(batch),
            )

            tasks = celery_group(
                celery_app.signature(
                    "app.tasks.workflow_host.run_host_workflow",
                    args=[run_id, host_run_id],
                    queue="long_running",
                )
                for _host_id, host_run_id in batch
            )

            result = tasks.apply_async()
            try:
                # Wait up to 1 hour for the entire batch; propagate=False so
                # individual host failures do not raise here.
                # Use join() instead of get() to avoid the Celery
                # "never call result.get() within a task" deadlock risk.
                result.join(timeout=3600, propagate=False)
            except Exception as exc:
                # Timeout or backend error — log and continue with next batch.
                logger.warning(
                    "workflow_orchestrator: run %d batch %d wait error: %s",
                    run_id,
                    batch_index + 1,
                    exc,
                )

    except Exception as exc:
        error_msg = str(exc)
        logger.exception(
            "workflow_orchestrator: unhandled error during batch dispatch for run %d",
            run_id,
        )
        await _mark_run_failed(run_id, error_msg)
        return

    # ------------------------------------------------------------------ #
    # Phase 3: aggregate final status                                     #
    # ------------------------------------------------------------------ #
    try:
        from sqlalchemy import select

        from app.db import task_session
        from app.workflows.models import (
            WorkflowHostRun,
            WorkflowHostStatus,
            WorkflowRun,
            WorkflowRunStatus,
        )

        async with task_session() as db:
            host_runs_result = await db.execute(
                select(WorkflowHostRun).where(WorkflowHostRun.run_id == run_id)
            )
            host_runs = list(host_runs_result.scalars().all())

            success_count = sum(1 for hr in host_runs if hr.status == WorkflowHostStatus.success)
            failed_count = sum(1 for hr in host_runs if hr.status == WorkflowHostStatus.failed)
            total = len(host_runs)

            if failed_count == 0:
                final_status = WorkflowRunStatus.completed
            elif success_count == 0:
                final_status = WorkflowRunStatus.failed
            else:
                final_status = WorkflowRunStatus.partial

            run_result = await db.execute(select(WorkflowRun).where(WorkflowRun.id == run_id))
            run = run_result.scalar_one()
            run.status = final_status
            run.completed_at = datetime.now(UTC)
            await db.commit()

            logger.info(
                "workflow_orchestrator: run %d finished — %s (%d/%d hosts succeeded)",
                run_id,
                final_status.value,
                success_count,
                total,
            )

    except Exception as exc:
        error_msg = str(exc)
        logger.exception(
            "workflow_orchestrator: failed during status aggregation for run %d",
            run_id,
        )
        await _mark_run_failed(run_id, error_msg)


async def _mark_run_failed(run_id: int, error_message: str) -> None:
    """Best-effort: set WorkflowRun status to failed with an error message."""
    try:
        from sqlalchemy import select

        from app.db import task_session
        from app.workflows.models import WorkflowRun, WorkflowRunStatus

        async with task_session() as db:
            result = await db.execute(select(WorkflowRun).where(WorkflowRun.id == run_id))
            run = result.scalar_one_or_none()
            if run is not None:
                run.status = WorkflowRunStatus.failed
                run.completed_at = datetime.now(UTC)
                # WorkflowRun has no error_message column; store in step_output
                # of the run itself via a log entry only.
                await db.commit()
    except Exception:
        logger.exception("workflow_orchestrator: could not mark run %d as failed", run_id)
