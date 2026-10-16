"""What a new alert sets off: its labels' action first, then the AI.

Two tasks:

* ``handle_alert`` — one new firing alert. Acts on its ``labdog_action``
  label (:mod:`app.alerts.labels`). If that started a run, the AI waits
  for it: ``waiting_on_action`` when the labels also ask for the AI,
  ``handled_by_action`` when the action alone handles the alert. If no
  action was asked for, or it could not run, the alert goes to the AI
  policy (:func:`app.tasks.ai_alerts._decide_and_run`), which records why
  nothing happened when the labels do not ask for the AI or it is off.
* ``check_alert_actions`` — every minute. Judges each started run: the run
  failed (``run_failed``), the alert resolved (``fixed``), or the alert is
  still firing ``alerts.action_check_minutes`` after the run finished
  (``not_effective``). When the labels asked for the AI, it then starts
  the investigation, with what was tried added to its mission.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime, timedelta
from typing import Any

from app.tasks import celery_app

logger = logging.getLogger(__name__)

SWEEP_INTERVAL_SECONDS = 60

_RUN_FAILED = ("failed", "cancelled", "partial")
_OUTPUT_TAIL_LINES = 40
_OUTPUT_TAIL_CHARS = 4000


def _why_not(dispatch: Any) -> str:
    return dispatch.detail or dispatch.outcome.removeprefix("skipped_").replace("_", " ")


async def _handle(alert_event_id: int) -> dict:
    from app.actions.registry import ensure_registry_current
    from app.ai.models import AlertEvent
    from app.alerts.labels import dispatch_for
    from app.db import task_session
    from app.tasks.ai_alerts import _decide_and_run

    async with task_session() as db:
        event = await db.get(AlertEvent, alert_event_id)
        if event is None:
            return {"alert_event_id": alert_event_id, "outcome": "missing"}

        # Before any registry lookup, as the cron scheduler does: a pool
        # process that has not rebuilt since it forked knows only the
        # built-ins (BUG-105).
        await ensure_registry_current(db)
        dispatch = await dispatch_for(db, event)
        handled = False
        already_tried = None
        run_id = None
        if dispatch is not None and dispatch.outcome == "started":
            handled = True
            run_id = dispatch.action_run_id
            event.investigation_outcome = (
                "waiting_on_action" if dispatch.hand_to_ai else "handled_by_action"
            )
            event.investigation_detail = f"{dispatch.action_key} run #{run_id}"
        elif dispatch is not None and dispatch.outcome == "skipped_in_flight":
            # The run already going decides, through its own alert. A second
            # investigation now would race it.
            handled = True
            event.investigation_outcome = "handled_by_action"
            event.investigation_detail = f"{dispatch.action_key}: {_why_not(dispatch)}"[:2000]
        elif dispatch is not None:
            already_tried = (
                f"The alert's labdog_action label asked for the action {dispatch.action_key}, "
                f"but LabDog did not run it: {_why_not(dispatch)}."
            )
        await db.commit()
        summary = (
            [
                {
                    "action_key": dispatch.action_key,
                    "outcome": dispatch.outcome,
                    "action_run_id": dispatch.action_run_id,
                }
            ]
            if dispatch is not None
            else []
        )

    if run_id is not None:
        celery_app.send_task("app.tasks.action_orchestrator.run_action", args=[run_id])

    if handled:
        return {"alert_event_id": alert_event_id, "outcome": "action", "actions": summary}
    result = await _decide_and_run(alert_event_id, already_tried=already_tried)
    return {**result, "actions": summary}


@celery_app.task(name="app.tasks.alert_handling.handle_alert")
def handle_alert(alert_event_id: int) -> dict:
    """Act on one alert's labdog_action label, then apply the AI policy."""
    return asyncio.run(_handle(alert_event_id))


async def _output_tail(db, run_id: int) -> str:  # noqa: ANN001
    from sqlalchemy import select

    from app.models.action_run import ActionHostRun

    output = (
        await db.execute(
            select(ActionHostRun.output).where(ActionHostRun.action_run_id == run_id).limit(1)
        )
    ).scalar_one_or_none()
    lines = (output or "").rstrip().splitlines()[-_OUTPUT_TAIL_LINES:]
    return "\n".join(lines)[-_OUTPUT_TAIL_CHARS:]


async def _tried(db, dispatch, run, event, verdict: str, host: str) -> str:  # noqa: ANN001
    from app.actions.registry import ACTION_REGISTRY

    action = ACTION_REGISTRY.get(dispatch.action_key)
    name = f"{action.name} ({dispatch.action_key})" if action else dispatch.action_key
    if run is None:
        return f"LabDog ran the action {name} on {host} for this alert, but its run is gone."
    if verdict == "not_effective":
        ending = "The run succeeded, but the alert is still firing."
    else:
        why = f": {run.error_message}" if run.error_message else ""
        ending = f"The run {run.status}{why}."
    text = [
        f"When this alert fired, LabDog ran the action {name} on {host} as run #{run.id}, "
        f"as the alert's labdog_action label asked. {ending}"
    ]
    tail = await _output_tail(db, run.id)
    if tail:
        text += ["", "The end of its output:", "", "```", tail, "```"]
    return "\n".join(text)


async def _check(now: datetime | None = None) -> dict:
    from sqlalchemy import select

    from app.ai.models import AlertEvent
    from app.alerts.models import AlertActionDispatch
    from app.db import task_session
    from app.models.action_run import ActionRun
    from app.models.host import Host
    from app.notifications.service import notify_alert_action
    from app.settings_service import get_setting_typed
    from app.tasks.ai_alerts import _decide_and_run

    now = now or datetime.now(UTC)
    judged = 0
    fallbacks: dict[int, str] = {}
    async with task_session() as db:
        window = timedelta(minutes=int(await get_setting_typed("alerts.action_check_minutes", db)))
        pending = (
            (
                await db.execute(
                    select(AlertActionDispatch)
                    .where(
                        AlertActionDispatch.outcome == "started",
                        AlertActionDispatch.check_outcome.is_(None),
                    )
                    .order_by(AlertActionDispatch.id)
                )
            )
            .scalars()
            .all()
        )
        for dispatch in pending:
            event = await db.get(AlertEvent, dispatch.alert_event_id)
            run = (
                await db.get(ActionRun, dispatch.action_run_id) if dispatch.action_run_id else None
            )
            if run is None or run.status in _RUN_FAILED:
                verdict = "run_failed"
            elif run.status != "succeeded":
                continue  # queued, waiting for the host, or running
            elif event is not None and event.status == "resolved":
                verdict = "fixed"
            elif run.finished_at is not None and now >= run.finished_at + window:
                verdict = "not_effective"
            else:
                continue

            dispatch.check_outcome = verdict
            dispatch.checked_at = now
            judged += 1
            host = None
            if dispatch.host_id is not None:
                found = await db.get(Host, dispatch.host_id)
                host = found.hostname if found else None
            host = host or "its host"

            hand_over = (
                verdict != "fixed"
                and dispatch.hand_to_ai
                and event is not None
                and event.investigation_session_id is None
                and event.status == "firing"
            )
            if event is not None and verdict == "fixed" and event.investigation_session_id is None:
                event.investigation_outcome = "handled_by_action"
                event.investigation_detail = (
                    f"fixed by {dispatch.action_key} run #{dispatch.action_run_id}"
                )
            if hand_over and event.id not in fallbacks:
                fallbacks[event.id] = await _tried(db, dispatch, run, event, verdict, host)
            await notify_alert_action(db, dispatch, run, event, host=host, handed_to_ai=hand_over)
        await db.commit()

    for event_id, tried in fallbacks.items():
        try:
            await _decide_and_run(event_id, already_tried=tried)
        except Exception:
            logger.exception("alert_handling: could not hand alert %d to the AI", event_id)
    return {"judged": judged, "handed_to_ai": len(fallbacks)}


@celery_app.task(name="app.tasks.alert_handling.check_alert_actions", queue="default")
def check_alert_actions() -> dict:
    """Judge finished runs that alerts started — see the module docstring."""
    return asyncio.run(_check())


def _register_beat_schedules() -> None:
    from app.tasks.beat_registry import ensure_entry

    ensure_entry(
        name="check-alert-actions",
        task="app.tasks.alert_handling.check_alert_actions",
        run_every_seconds=SWEEP_INTERVAL_SECONDS,
        app=celery_app,
    )


# Registration happens from ``beat_init`` (app.tasks.beat_registry), not at
# import — see BUG-70.
