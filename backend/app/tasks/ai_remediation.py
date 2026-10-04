"""Checking automatic fixes afterwards, and rolling hosts back.

Three entry points:

* ``check_due_remediations`` — every minute. Finds full-auto alert
  sessions whose check has fallen due, claims each and hands it on, marks
  the ones too late to make as ``unchecked``, and closes whatever a
  killed worker left running.
* ``check_remediation`` — one alert's check, and the rollback when the fix
  made the host worse. On the long-running queue: the SSH probe alone can
  take a minute, and a rollback waits for the machine to come back.
* ``run_rollback`` — a rollback someone asked for from the session page.

The judging and the rolling back live in :mod:`app.ai.remediation`; this
module only schedules them and owns the transactions.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime

from app.tasks import celery_app

logger = logging.getLogger(__name__)

SWEEP_INTERVAL_SECONDS = 60


async def _sweep() -> dict:
    from app.ai import remediation
    from app.ai.models import AISession, AlertEvent
    from app.db import task_session
    from app.notifications.service import notify_remediation_checked

    async with task_session() as db:
        now = datetime.now(UTC)
        abandoned, failed_rollbacks = await remediation.release_abandoned(db, now)
        due, overdue = await remediation.find_due(db, now)
        claimed = [event_id for event_id in due if await remediation.claim(db, event_id, now)]
        late = [
            event_id
            for event_id in overdue
            if await remediation.mark_unchecked(
                db,
                event_id,
                now,
                "The check fell due while LabDog was not running, and it is too late now to "
                "judge the fix from what the host looks like. Look at the host and the session "
                "yourself.",
            )
        ]
        await db.commit()

        for event_id in [*abandoned, *late]:
            event = await db.get(AlertEvent, event_id, populate_existing=True)
            if event is None:
                continue
            session = (
                await db.get(AISession, event.investigation_session_id)
                if event.investigation_session_id
                else None
            )
            await notify_remediation_checked(db, event, session)
        await db.commit()

    # After the commit, so a check cannot start before its claim has landed.
    for event_id in claimed:
        celery_app.send_task(
            "app.tasks.ai_remediation.check_remediation", kwargs={"alert_event_id": event_id}
        )
    if claimed or late or abandoned or failed_rollbacks:
        logger.info(
            "ai_remediation: %d check(s) dispatched, %d too late, %d abandoned, "
            "%d rollback(s) abandoned",
            len(claimed),
            len(late),
            len(abandoned),
            failed_rollbacks,
        )
    return {
        "dispatched": len(claimed),
        "unchecked": len(late) + len(abandoned),
        "rollbacks_abandoned": failed_rollbacks,
    }


@celery_app.task(name="app.tasks.ai_remediation.check_due_remediations", queue="default")
def check_due_remediations() -> dict:
    """Dispatch every remediation check that has fallen due."""
    return asyncio.run(_sweep())


async def _check(alert_event_id: int) -> dict:
    from app.ai import remediation
    from app.ai.models import AISession, AlertEvent
    from app.db import task_session
    from app.notifications.service import notify_remediation_checked

    async with task_session() as db:
        event = await db.get(AlertEvent, alert_event_id, populate_existing=True)
        if event is None or event.remediation_outcome != "checking":
            # Gone, or another worker already finished it.
            return {"alert_event_id": alert_event_id, "outcome": "skipped"}

        session = (
            await db.get(AISession, event.investigation_session_id)
            if event.investigation_session_id
            else None
        )
        if session is None:
            assessment = remediation.Assessment(
                "unchecked", "The session was deleted before its fix could be checked."
            )
        else:
            assessment = await remediation.assess(db, event, session)
        await remediation.record_outcome(db, event, assessment, datetime.now(UTC))
        await db.commit()

        rollback = None
        if assessment.outcome == "made_worse" and session is not None:
            rollback = await remediation.roll_back_automatically(db, event, session)
            await db.commit()

        await notify_remediation_checked(db, event, session, rollback)
        await db.commit()

    return {
        "alert_event_id": alert_event_id,
        "outcome": assessment.outcome,
        "rollback": rollback.status if rollback is not None else None,
    }


@celery_app.task(
    name="app.tasks.ai_remediation.check_remediation",
    # The probe takes up to a minute, the busy wait up to ten, and a
    # rollback up to ten more while the machine restarts. Well inside
    # ``remediation.ABANDONED_AFTER``, which assumes it.
    soft_time_limit=2400,
    time_limit=2700,
)
def check_remediation(alert_event_id: int) -> dict:
    """Judge one automatic fix, and roll back a host it made worse."""
    return asyncio.run(_check(alert_event_id))


async def _run_rollback(rollback_id: int) -> dict:
    from app.ai import remediation
    from app.ai.models import AIRollback, AISession
    from app.db import task_session

    async with task_session() as db:
        rollback = await db.get(AIRollback, rollback_id, populate_existing=True)
        if rollback is None or rollback.status != "running":
            return {"rollback_id": rollback_id, "status": "skipped"}

        session = await db.get(AISession, rollback.session_id) if rollback.session_id else None
        try:
            if session is None or rollback.host_id is None:
                raise remediation.RollbackRefused(
                    "The session or the host was deleted before the rollback started."
                )
            # Planned again rather than carried over from the request: the
            # snapshot rows and the VM mapping are read now, when they are
            # about to be used.
            plan = await remediation.plan_rollback(db, session=session, host_id=rollback.host_id)
        except remediation.RollbackRefused as exc:
            rollback.status = "refused"
            rollback.detail = str(exc)[:4000]
            rollback.finished_at = datetime.now(UTC)
            await db.commit()
            return {"rollback_id": rollback_id, "status": "refused"}

        await remediation.perform_rollback(db, rollback, plan)
        await db.commit()
        return {"rollback_id": rollback_id, "status": rollback.status}


@celery_app.task(
    name="app.tasks.ai_remediation.run_rollback",
    soft_time_limit=2400,
    time_limit=2700,
)
def run_rollback(rollback_id: int) -> dict:
    """Carry out a rollback someone asked for."""
    return asyncio.run(_run_rollback(rollback_id))


# ---------------------------------------------------------------------------
# RedBeat registration
# ---------------------------------------------------------------------------


def _register_beat_schedules() -> None:
    from app.tasks.beat_registry import ensure_entry

    ensure_entry(
        name="check-due-remediations",
        task="app.tasks.ai_remediation.check_due_remediations",
        run_every_seconds=SWEEP_INTERVAL_SECONDS,
        app=celery_app,
    )


# Registration happens from ``beat_init`` (app.tasks.beat_registry), not at
# import — see BUG-70.
