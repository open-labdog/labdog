"""Unified scheduler for the ``scheduled_actions`` table.

Replaces the old ``app.tasks.workflow_schedule.check_scheduled_workflows``
task. RedBeat ticks ``check_due`` every 60 s; the task walks every
enabled ``ScheduledAction`` row, computes whether each is due since
``last_dispatched_at`` (falling back to ``created_at``), and dispatches
an ``ActionRun`` via the existing ``app.tasks.action_orchestrator
.run_action`` Celery task. The orchestrator handles per-host fork-out
and fleet target resolution (C5).

Idempotency:

- ``last_dispatched_at`` is the cron walk's reference, not "wall-clock
  now" — so a missed tick (worker restart, Redis hiccup) doesn't
  fire-twice on the next tick.
- Schedules with a non-terminal ``ActionRun`` (status in queued/running)
  are skipped — the previous run hasn't finished yet.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime

from app.tasks import celery_app

logger = logging.getLogger(__name__)


@celery_app.task(
    name="app.tasks.scheduled_action_schedule.check_due",
    queue="long_running",
)
def check_due() -> dict:
    """Walk scheduled_actions, dispatch any rows that are due."""
    return asyncio.run(_check_due_async())


async def _check_due_async() -> dict:
    from croniter import croniter
    from sqlalchemy import select

    from app.actions.registry import ACTION_REGISTRY
    from app.audit.logger import log_action
    from app.db import task_session
    from app.models.action_run import ActionRun
    from app.models.scheduled_action import ScheduledAction

    dispatched = 0
    skipped_in_flight = 0
    skipped_orphan = 0

    now = datetime.now(UTC)

    async with task_session() as db:
        rows = (
            (
                await db.execute(
                    select(ScheduledAction).where(
                        ScheduledAction.enabled.is_(True),
                        ScheduledAction.schedule_cron.isnot(None),
                    )
                )
            )
            .scalars()
            .all()
        )

        for sa in rows:
            try:
                reference = sa.last_dispatched_at or sa.created_at
                if reference.tzinfo is None:
                    reference = reference.replace(tzinfo=UTC)
                next_run_at = croniter(sa.schedule_cron, reference).get_next(datetime)
                if next_run_at.tzinfo is None:
                    next_run_at = next_run_at.replace(tzinfo=UTC)
                if now < next_run_at:
                    continue

                in_flight = await db.scalar(
                    select(ActionRun.id)
                    .where(
                        ActionRun.scheduled_action_id == sa.id,
                        ActionRun.status.in_(("queued", "running")),
                    )
                    .limit(1)
                )
                if in_flight is not None:
                    skipped_in_flight += 1
                    continue

                action = ACTION_REGISTRY.get(sa.action_key)
                if action is None:
                    # The pack was disabled or removed; the schedule
                    # outlived its action. Log and move on — operators
                    # see this in the row's UI as "action not found".
                    logger.warning(
                        "scheduled_action %d references unknown action %r; skipping",
                        sa.id,
                        sa.action_key,
                    )
                    skipped_orphan += 1
                    continue

                run = ActionRun(
                    action_key=sa.action_key,
                    action_version=action.version,
                    host_id=sa.target_id if sa.target_kind == "host" else None,
                    group_id=sa.target_id if sa.target_kind == "group" else None,
                    scheduled_action_id=sa.id,
                    parameters=sa.parameters,
                    parallelism=sa.batch_size,
                    snapshot_enabled=sa.snapshot_enabled,
                    verify_enabled=sa.verify_enabled,
                    auto_rollback=sa.auto_rollback,
                    status="queued",
                    triggered_by_user_id=None,
                )
                db.add(run)
                sa.last_dispatched_at = now
                await db.flush()
                await db.refresh(run)
                run_id = run.id

                await log_action(
                    db,
                    action="scheduled_action.dispatched",
                    entity_type="scheduled_action",
                    entity_id=sa.id,
                    user_id=None,
                    after_state={"action_run_id": run_id, "manual": False},
                )
                await db.commit()

                celery_app.send_task(
                    "app.tasks.action_orchestrator.run_action",
                    args=[run_id],
                )
                dispatched += 1

            except Exception:
                # Don't let one bad schedule abort the rest of the walk.
                logger.exception("scheduler: failed to evaluate scheduled_action %d", sa.id)

    return {
        "dispatched": dispatched,
        "skipped_in_flight": skipped_in_flight,
        "skipped_orphan": skipped_orphan,
    }


# ---------------------------------------------------------------------------
# RedBeat registration
# ---------------------------------------------------------------------------


def _register_beat_schedule() -> None:
    from celery.schedules import schedule
    from redbeat import RedBeatSchedulerEntry

    # Best-effort: drop the old workflow scheduler entry. Otherwise the
    # legacy task name keeps firing across an upgrade and produces
    # confusing 'task not registered' errors in the worker log.
    try:
        legacy = RedBeatSchedulerEntry.from_key("redbeat:check-scheduled-workflows", app=celery_app)
        legacy.delete()
    except Exception:
        # Already gone, or Redis is unreachable — fall through.
        pass

    entry = RedBeatSchedulerEntry(
        name="check-due-scheduled-actions",
        task="app.tasks.scheduled_action_schedule.check_due",
        schedule=schedule(run_every=60),
        app=celery_app,
    )
    entry.save()


try:
    _register_beat_schedule()
except Exception:
    # Redis may not be available at import time (e.g. during tests).
    pass
