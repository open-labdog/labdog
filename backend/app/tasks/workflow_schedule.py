"""Periodic task that checks for scheduled workflows and dispatches runs."""

from app.tasks import celery_app


@celery_app.task(name="app.tasks.workflow_schedule.check_scheduled_workflows", queue="long_running")
def check_scheduled_workflows():
    """Check all enabled workflows with a cron schedule and dispatch runs if due."""
    import asyncio
    from datetime import datetime, timezone

    from croniter import croniter
    from sqlalchemy import select

    from app.db import task_session
    from app.workflows.models import UpdateWorkflow, WorkflowRun, WorkflowRunStatus

    async def _check_async() -> int:
        dispatched = 0
        now = datetime.now(timezone.utc)

        async with task_session() as db:
            result = await db.execute(
                select(UpdateWorkflow).where(
                    UpdateWorkflow.enabled == True,  # noqa: E712
                    UpdateWorkflow.schedule_cron.isnot(None),
                )
            )
            workflows = result.scalars().all()

            for workflow in workflows:
                try:
                    # Skip if a run is already active
                    active_run = await db.scalar(
                        select(WorkflowRun).where(
                            WorkflowRun.workflow_id == workflow.id,
                            WorkflowRun.status.in_(
                                [WorkflowRunStatus.pending, WorkflowRunStatus.running]
                            ),
                        )
                    )
                    if active_run:
                        continue

                    # Determine the reference time: last run's started_at, or workflow creation
                    last_run = await db.scalar(
                        select(WorkflowRun)
                        .where(WorkflowRun.workflow_id == workflow.id)
                        .order_by(WorkflowRun.created_at.desc())
                    )

                    if last_run and last_run.started_at:
                        reference_dt = last_run.started_at
                    elif last_run:
                        reference_dt = last_run.created_at
                    else:
                        reference_dt = workflow.created_at

                    # Ensure reference_dt is timezone-aware
                    if reference_dt.tzinfo is None:
                        reference_dt = reference_dt.replace(tzinfo=timezone.utc)

                    # Determine if the cron expression is due since the reference time
                    cron = croniter(workflow.schedule_cron, reference_dt)
                    next_run_dt = cron.get_next(datetime)
                    if next_run_dt.tzinfo is None:
                        next_run_dt = next_run_dt.replace(tzinfo=timezone.utc)

                    if now < next_run_dt:
                        continue

                    # Create a new WorkflowRun and dispatch
                    run = WorkflowRun(
                        workflow_id=workflow.id,
                        status=WorkflowRunStatus.pending,
                        triggered_by=None,
                    )
                    db.add(run)
                    await db.flush()
                    await db.refresh(run)
                    await db.commit()

                    celery_app.send_task(
                        "app.tasks.workflow_orchestrator.run_group_workflow",
                        args=[workflow.id, run.id],
                    )
                    dispatched += 1

                except Exception:
                    # Do not let one bad workflow block the rest
                    pass

        return dispatched

    count = asyncio.run(_check_async())
    return {"dispatched": count}


# Register periodic schedule check via RedBeat (prevents duplicate schedules on restart)
def _register_beat_schedule() -> None:
    from celery.schedules import schedule

    from redbeat import RedBeatSchedulerEntry

    entry = RedBeatSchedulerEntry(
        name="check-scheduled-workflows",
        task="app.tasks.workflow_schedule.check_scheduled_workflows",
        schedule=schedule(run_every=60),
        app=celery_app,
    )
    entry.save()


try:
    _register_beat_schedule()
except Exception:
    # Fallback: Redis may not be available at import time (e.g., during tests)
    pass
