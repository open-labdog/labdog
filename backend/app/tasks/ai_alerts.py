"""Alert intake tasks: the poller, and the investigation decision.

Two entry points, deliberately separate:

* ``poll_alertmanager`` — the fallback producer. Asks the default Mimir
  instance's Alertmanager API what is currently firing and records it.
  Catches what happened while LabDog was unreachable, which the webhook
  by definition cannot. Only meaningful when alert rules live in Mimir's
  ruler; Grafana-managed rules go to Grafana's own Alertmanager and are
  invisible here. Off by default for that reason — see
  :mod:`app.ai.alerts`.
* ``investigate_alert`` — decides whether one recorded alert is worth a
  session, and starts it if so.

They are split because recording and spending are different risks. The
poller catching up on a hundred alerts after an outage must not open a
hundred sessions, so it records them all and asks about each one
individually — where the policy, the budget, and the dedup state can say
no cheaply.
"""

from __future__ import annotations

import asyncio
import logging

from app.tasks import celery_app

logger = logging.getLogger(__name__)

#: How much of the alert LabDog puts in front of the model. The whole
#: label and annotation set, because an investigation is only as good as
#: its context and the operator chose what to label.
_MISSION = """\
A monitoring alert fired and you are investigating it. Find out whether \
it reflects a real problem on the host, and if so, what is causing it.

Alert: {alertname}
Severity: {severity}
Status: {status}
Started: {starts_at}

Labels:
{labels}

Annotations:
{annotations}

Work out what this alert is telling you, check the host it points at, \
and report what you find. If the alert looks like a false positive or \
has already cleared, say so plainly — that is a useful answer.
"""


def _render_pairs(mapping: dict | None) -> str:
    if not mapping:
        return "(none)"
    return "\n".join(f"- {k}: {v}" for k, v in sorted(mapping.items()))


def build_mission(event) -> str:  # noqa: ANN001 - AlertEvent, imported lazily
    return _MISSION.format(
        alertname=event.alertname,
        severity=event.severity or "(not labelled)",
        status=event.status,
        starts_at=event.starts_at.isoformat() if event.starts_at else "(unknown)",
        labels=_render_pairs(event.labels),
        annotations=_render_pairs(event.annotations),
    )


async def _decide_and_run(alert_event_id: int) -> dict:
    """Apply the auto-investigation policy to one recorded alert.

    Every path writes ``investigation_outcome``. "Why did nothing happen
    for this alert?" has half a dozen answers and an operator should not
    have to reconstruct which one applied from logs that may have
    rotated.
    """
    from app.ai import service
    from app.ai.alerts import meets_severity
    from app.ai.loop import build_system_prompt
    from app.ai.models import AISession, AlertEvent
    from app.db import task_session
    from app.settings_service import get_setting_typed

    async def _finish(db, event, outcome: str, detail: str = "") -> dict:
        event.investigation_outcome = outcome
        event.investigation_detail = detail[:2000] or None
        await db.commit()
        return {"alert_event_id": event.id, "outcome": outcome}

    async with task_session() as db:
        event = await db.get(AlertEvent, alert_event_id)
        if event is None:
            return {"alert_event_id": alert_event_id, "outcome": "missing"}

        if not int(await get_setting_typed("ai.auto_investigate_enabled", db)):
            return await _finish(db, event, "skipped_disabled")

        # A resolved alert has nothing live to look at. Investigating it
        # would read a healthy host and report that nothing is wrong,
        # which is true and useless.
        if event.status != "firing":
            return await _finish(db, event, "skipped_resolved")

        if event.investigation_session_id is not None:
            return await _finish(db, event, "skipped_duplicate", "already investigated")

        minimum = str(await get_setting_typed("ai.auto_investigate_min_severity", db))
        if not meets_severity(event.severity, minimum):
            return await _finish(
                db,
                event,
                "skipped_severity",
                f"severity {event.severity!r} does not meet the {minimum!r} threshold",
            )

        try:
            provider = await service.resolve_provider(db, None)
            # An investigation on a backend that cannot run tools is
            # guesswork presented as fact — the same refusal the chat
            # page makes, for the same reason.
            service.assert_can_investigate(provider)
            await service.assert_within_budget(db, provider)
        except (service.AIDisabledError, service.BudgetExceededError) as exc:
            outcome = "skipped_budget" if isinstance(exc, service.BudgetExceededError) else "failed"
            return await _finish(db, event, outcome, str(exc))

        mission = build_mission(event)
        session = AISession(
            provider_id=provider.id,
            mode="alert_investigation",
            title=f"Alert: {event.alertname}"[:200],
            mission=mission,
            # Never anything else. An alert is a machine's opinion that
            # something is wrong; acting on it unattended is a different
            # feature with a different risk, and this one only looks.
            autonomy_level="read_only",
            status="queued",
            target_host_ids=[event.host_id] if event.host_id else [],
            alert_event_id=event.id,
        )
        db.add(session)
        await db.flush()
        await service.append_message(
            db, session.id, role="system", content=build_system_prompt("read_only")
        )
        await service.append_message(db, session.id, role="user", content=mission)

        event.investigation_session_id = session.id
        event.investigation_outcome = "started"
        event.investigation_detail = None
        await db.commit()

        celery_app.send_task(
            "app.tasks.ai_task.run_chat_session", kwargs={"session_id": session.id}
        )
        return {
            "alert_event_id": event.id,
            "outcome": "started",
            "session_id": session.id,
        }


@celery_app.task(name="app.tasks.ai_alerts.investigate_alert")
def investigate_alert(alert_event_id: int) -> dict:
    """Decide whether one alert warrants a session, and start it if so."""
    return asyncio.run(_decide_and_run(alert_event_id))


async def _poll() -> dict:
    """Read currently-firing alerts from the default Mimir's Alertmanager.

    Reuses the registered Grafana instance's auth and TLS settings rather
    than asking the operator for a second endpoint — the Alertmanager API
    lives under the same Mimir deployment LabDog already queries.
    """
    from app.ai.alerts import from_alertmanager_v2, record
    from app.db import task_session
    from app.grafana.service import get_default_client
    from app.settings_service import get_setting_typed

    async with task_session() as db:
        if not int(await get_setting_typed("ai.alert_intake_enabled", db)):
            return {"polled": 0, "reason": "alert intake disabled"}

        client = await get_default_client(db, kind="mimir")
        if client is None:
            return {"polled": 0, "reason": "no default Mimir instance registered"}

        from app.grafana.client import PrometheusError

        try:
            raw = await client.get_alertmanager_alerts()
        except PrometheusError as exc:
            logger.warning("ai_alerts: Alertmanager poll failed: %s", exc)
            return {"polled": 0, "error": str(exc)}

        alerts = from_alertmanager_v2(raw)
        created_ids: list[int] = []
        for alert in alerts:
            event, created = await record(db, alert, source="alertmanager_poll")
            if created and alert.is_firing:
                created_ids.append(event.id)
        await db.commit()

    # Dispatched after the commit so the task cannot read a row that the
    # transaction has not landed yet.
    for event_id in created_ids:
        celery_app.send_task(
            "app.tasks.ai_alerts.investigate_alert", kwargs={"alert_event_id": event_id}
        )

    return {"polled": len(alerts), "new": len(created_ids)}


@celery_app.task(name="app.tasks.ai_alerts.poll_alertmanager")
def poll_alertmanager() -> dict:
    """Fallback producer — see the module docstring."""
    return asyncio.run(_poll())


def _register_beat_schedule() -> None:
    """Self-register the poll on RedBeat, matching the drift sweep.

    The interval is read from the database-backed setting at worker
    start, not from the config file, because it is a UI-tunable value.
    A zero interval means "do not poll" — the entry is removed rather
    than scheduled at some arbitrary floor, so turning it off in the UI
    actually stops it.
    """
    import asyncio as _asyncio

    from celery.schedules import schedule
    from redbeat import RedBeatSchedulerEntry

    from app.db import task_session
    from app.settings_service import get_setting_typed

    async def _interval() -> int:
        async with task_session() as db:
            return int(await get_setting_typed("ai.alertmanager_poll_minutes", db))

    minutes = _asyncio.run(_interval())
    name = "poll-alertmanager"
    if minutes <= 0:
        try:
            RedBeatSchedulerEntry.from_key(f"redbeat:{name}", app=celery_app).delete()
        except KeyError:
            # Nothing registered, which is the desired end state anyway.
            logger.debug("ai_alerts: no %s entry to remove", name)
        return

    RedBeatSchedulerEntry(
        name=name,
        task="app.tasks.ai_alerts.poll_alertmanager",
        schedule=schedule(run_every=minutes * 60),
        app=celery_app,
    ).save()
    logger.info("ai_alerts: Alertmanager poll registered every %d minute(s)", minutes)


try:
    _register_beat_schedule()
except Exception:
    # Redis or the database may be unavailable at import time — tests, or
    # a worker that started before its dependencies. Logged rather than
    # swallowed: a poll that silently never registers is indistinguishable
    # from one that is running and finding nothing, which is the worst of
    # both. The webhook is unaffected either way.
    logger.warning(
        "ai_alerts: could not register the Alertmanager poll; "
        "it will not run until this worker restarts successfully",
        exc_info=True,
    )
