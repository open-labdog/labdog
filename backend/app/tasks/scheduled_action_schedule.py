"""Unified scheduler for the ``scheduled_actions`` table.

Replaces the old ``app.tasks.workflow_schedule.check_scheduled_workflows``
task. RedBeat ticks ``check_due`` every 60 s; the task walks every
enabled ``ScheduledAction`` row, computes whether each is due since
``ScheduledAction.walk_start()`` (the later of ``last_dispatched_at``
and ``schedule_changed_at``, falling back to ``created_at``), and
dispatches an ``ActionRun`` via the existing
``app.tasks.action_orchestrator.run_action`` Celery task. The
orchestrator handles per-host fork-out and fleet target resolution
(C5). Cron expressions are read in the ``scheduling.timezone``
setting's zone; ``app.cron_walk`` has the daylight-saving rules.

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
    from sqlalchemy import select

    from app.actions.registry import ensure_registry_current
    from app.cron_walk import get_schedule_timezone
    from app.db import task_session
    from app.models.scheduled_action import ScheduledAction

    counts = {"dispatched": 0, "skipped_in_flight": 0, "skipped_orphan": 0}

    now = datetime.now(UTC)

    async with task_session() as db:
        # Before any lookup below. This runs on whichever pool process the
        # tick lands on, and one that had not rebuilt since it was forked
        # held the built-ins alone: every pack schedule was skipped
        # as unknown until a tick happened to land elsewhere (BUG-105).
        await ensure_registry_current(db)
        tz = await get_schedule_timezone(db)
        # Ids only, and each row loaded inside the loop (BUG-134). The
        # per-row rollback below expires every ORM object in the session;
        # walking a list of loaded rows meant the next row's attributes,
        # and this row's ``sa.id`` in the log call, needed a lazy load,
        # which raises MissingGreenlet under asyncio inside the handler,
        # so one failing schedule ended the tick for every row after it.
        schedule_ids = (
            (
                await db.execute(
                    select(ScheduledAction.id)
                    .where(
                        ScheduledAction.enabled.is_(True),
                        ScheduledAction.schedule_cron.isnot(None),
                    )
                    .order_by(ScheduledAction.id)
                )
            )
            .scalars()
            .all()
        )

        for sa_id in schedule_ids:
            try:
                outcome = await _tick_schedule(db, sa_id, now, tz)
            except Exception:
                # Don't let one bad schedule abort the rest of the walk.
                #
                # The rollback is load-bearing, not tidiness: a failure part-way
                # through (e.g. the FK violation you get when a schedule's
                # target host has been deleted — ``scheduled_actions.target_id``
                # is an index, not a constraint) leaves the session needing
                # rollback. Without this, the *next* iteration's first query
                # raises PendingRollbackError, is caught here too, and so on —
                # so one broken row silently skipped every schedule ordered
                # after it, on every tick, indefinitely.
                await db.rollback()
                logger.exception("scheduler: failed to evaluate scheduled_action %d", sa_id)
                continue
            if outcome is not None:
                counts[outcome] += 1

    return counts


async def _tick_schedule(db, sa_id: int, now: datetime, tz) -> str | None:  # noqa: ANN001
    """Evaluate one schedule and dispatch it when due.

    Returns the counter the schedule lands in (``dispatched``,
    ``skipped_in_flight`` or ``skipped_orphan``), or None when it is gone,
    disabled or not due yet. Raises on anything unexpected; the caller
    rolls back and moves on.
    """
    from app.actions.registry import ACTION_REGISTRY

    sa = await _schedule_if_due(db, sa_id, now, tz)
    if sa is None:
        return None

    if await _held_by_in_flight_run(db, sa, now):
        return "skipped_in_flight"

    action = ACTION_REGISTRY.get(sa.action_key)
    if action is None:
        # The pack was disabled or removed; the schedule
        # outlived its action — the registry was brought up
        # to date above, so the miss is real. Log and move on
        # — operators see this in the row's UI as "action not
        # found".
        logger.warning(
            "scheduled_action %d references unknown action %r; skipping",
            sa.id,
            sa.action_key,
        )
        return "skipped_orphan"

    await _dispatch_scheduled_run(db, sa, action, now)
    return "dispatched"


async def _schedule_if_due(db, sa_id: int, now: datetime, tz):  # noqa: ANN001, ANN202
    """The schedule, freshly loaded, when it is still enabled and its next
    fire time since ``walk_start()`` has come; otherwise None."""
    from app.cron_walk import next_fire_time
    from app.models.scheduled_action import ScheduledAction

    sa = await db.get(ScheduledAction, sa_id, populate_existing=True)
    if sa is None or not sa.enabled or sa.schedule_cron is None:
        return None
    next_run_at = next_fire_time(sa.schedule_cron, sa.walk_start(), tz)
    if now < next_run_at:
        return None
    return sa


async def _held_by_in_flight_run(db, sa, now: datetime) -> bool:  # noqa: ANN001
    """Whether a queued or running run of this schedule should hold it back."""
    from sqlalchemy import select

    from app.models.action_run import ActionRun
    from app.tasks.action_timeouts import per_host_deadline_seconds

    in_flight = (
        await db.execute(
            select(ActionRun.id, ActionRun.created_at, ActionRun.action_key)
            .where(
                ActionRun.scheduled_action_id == sa.id,
                ActionRun.status.in_(("queued", "running")),
            )
            .order_by(ActionRun.created_at.desc())
            .limit(1)
        )
    ).first()
    if in_flight is None:
        return False
    # Age cap on the overlap guard: an in-flight row this
    # far past its action's deadline belongs to a dead
    # worker (the action sweeper will fail it within one
    # sweep). Without the cap, one orphaned ``running``
    # row silences the schedule forever.
    run_id, run_created_at, run_action_key = in_flight
    if run_created_at.tzinfo is None:
        run_created_at = run_created_at.replace(tzinfo=UTC)
    age_cap = per_host_deadline_seconds(run_action_key) * 4
    age = (now - run_created_at).total_seconds()
    if age <= age_cap:
        return True
    logger.warning(
        "scheduled_action %d: ignoring wedged in-flight "
        "ActionRun %d (age %.0fs > cap %ds) — dispatching "
        "anyway; the action sweeper will reap it",
        sa.id,
        run_id,
        age,
        age_cap,
    )
    return False


async def _dispatch_scheduled_run(db, sa, action, now: datetime) -> None:  # noqa: ANN001
    """Store a ``queued`` run for the schedule, audit it, commit, and send
    it to the orchestrator."""
    from app.actions.run_target import describe_target
    from app.audit.logger import log_action
    from app.models.action_run import ActionRun

    run_host_id = sa.target_id if sa.target_kind == "host" else None
    run_group_id = sa.target_id if sa.target_kind == "group" else None
    target_kind, target_label = await describe_target(
        db, host_id=run_host_id, group_id=run_group_id
    )
    run = ActionRun(
        action_key=sa.action_key,
        action_version=action.version,
        host_id=run_host_id,
        group_id=run_group_id,
        target_kind=target_kind,
        target_label=target_label,
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


# ---------------------------------------------------------------------------
# RedBeat registration
# ---------------------------------------------------------------------------


def _register_beat_schedule() -> None:
    from redbeat import RedBeatSchedulerEntry

    from app.tasks.beat_registry import ensure_entry

    # Best-effort: drop the old workflow scheduler entry. Otherwise the
    # legacy task name keeps firing across an upgrade and produces
    # confusing 'task not registered' errors in the worker log.
    try:
        legacy = RedBeatSchedulerEntry.from_key("redbeat:check-scheduled-workflows", app=celery_app)
        legacy.delete()
    except Exception:
        # Already gone, or Redis is unreachable — fall through.
        pass

    ensure_entry(
        name="check-due-scheduled-actions",
        task="app.tasks.scheduled_action_schedule.check_due",
        run_every_seconds=60,
        app=celery_app,
    )


# Registration happens from ``beat_init`` (app.tasks.beat_registry), not at
# import. Calling it here rewrote the entry's ``due_at`` in every process
# that imported this module — API included — so on a deployment that
# restarts more than once a day, a daily job never fired at all (BUG-70).
