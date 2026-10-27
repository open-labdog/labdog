"""What an alert's Grafana labels ask LabDog to do.

Three labels, set on the alert rule in Grafana, where a template can make
them depend on the host (``$labels.labdog_hostname``, which the
alloy-install action stamps on every series):

* ``labdog_action=<action key>`` — run this action on the alert's host
  first. Only actions on LabDog's list of actions alerts may run
  (:class:`app.alerts.models.AlertAction`) are started, and only on the
  hosts the list allows; the list starts empty.
* ``labdog_ai=true`` — have the AI investigate, when there is no action or
  the action did not fix the alert.
* ``labdog_ai_automate=true`` — the same, and the AI may fix what it finds
  (full auto, behind the safeguards in :mod:`app.ai.alert_autonomy`).
  Implies ``labdog_ai``. With ``ai.alert_full_auto_rules_enabled`` on,
  LabDog's own rules decide this instead (:mod:`app.ai.full_auto_rules`).

The AI values must be exactly ``true``.

The run an alert starts is always a single-host run on the host the alert
names, with the list entry's parameters and snapshot / verify / rollback
choices. What happened is recorded per alert in
:class:`app.alerts.models.AlertActionDispatch`, and judged afterwards by
:mod:`app.tasks.alert_handling`.

Guards, in the order they are checked:

* only a new firing alert dispatches — repeats of the same firing and
  resolve notifications do not;
* the alert must name a host LabDog manages;
* the action must exist, be on the list and not paused there, and the
  host must be inside the entry's scope;
* a webhook alert needs the webhook token to be at least
  ``MIN_WEBHOOK_TOKEN_CHARS`` long, as for full auto: the token is all
  that stands between the webhook and a run on your hosts;
* no second alert run of the action on the host while one is queued,
  waiting or running;
* the entry's ``cooldown_minutes`` since an alert last ran the action on
  the host.

The orchestrator's host lock then serialises the run with anything else
working on the host, as for any run.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.full_auto_rules import AUTOMATE_LABEL

logger = logging.getLogger(__name__)

ACTION_LABEL = "labdog_action"
AI_LABEL = "labdog_ai"
AI_AUTOMATE_LABEL = AUTOMATE_LABEL

#: Run states that count as "still going" for the in-flight guard.
IN_FLIGHT = ("queued", "pending", "running")


def is_true(labels: Mapping[str, Any] | None, key: str) -> bool:
    """Whether ``labels[key]`` is exactly ``true``.

    Exact, as for the full-auto label: a value like ``yes`` or ``True``
    is more likely a typo than an instruction, and a label that lets an
    alert spend money or change a host should not be read generously.
    """
    return (labels or {}).get(key) == "true"


def requested_action(labels: Mapping[str, Any] | None) -> str | None:
    """The action key the alert's ``labdog_action`` label names, if any."""
    value = (labels or {}).get(ACTION_LABEL)
    if not isinstance(value, str):
        return None
    return value.strip() or None


async def wants_ai(db: AsyncSession, event: Any) -> bool:
    """Whether the alert asks for the AI: ``labdog_ai=true``, or full auto.

    Full auto is the label ``labdog_ai_automate=true`` — or, with the
    rules on, a matching rule — so an alert allowed to be fixed is also
    one that gets investigated.
    """
    if is_true(event.labels, AI_LABEL):
        return True
    from app.ai.full_auto_rules import first_match

    return await first_match(db, event) is not None


async def in_scope(db: AsyncSession, entry: Any, host_id: int | None) -> bool:
    """Whether ``host_id`` is inside ``entry``'s target."""
    if host_id is None:
        return False
    if entry.target_kind == "fleet":
        return True
    if entry.target_kind == "host":
        return entry.target_id == host_id
    from app.models.host import HostGroupMembership

    found = await db.execute(
        select(HostGroupMembership.c.host_id).where(
            HostGroupMembership.c.host_id == host_id,
            HostGroupMembership.c.group_id == entry.target_id,
        )
    )
    return found.first() is not None


async def scope_label(db: AsyncSession, entry: Any) -> str:
    """``all hosts``, ``host web-1`` or ``group web``, for messages."""
    from app.actions.run_target import describe_target

    if entry.target_kind == "fleet":
        return "all hosts"
    kind, label = await describe_target(
        db,
        host_id=entry.target_id if entry.target_kind == "host" else None,
        group_id=entry.target_id if entry.target_kind == "group" else None,
    )
    return f"{kind} {label}"


async def _guard(db: AsyncSession, entry: Any, event: Any, now: datetime) -> tuple[str, str]:
    """``("started", "")`` if a run may be made, else the skip and why."""
    from app.ai.alert_autonomy import MIN_WEBHOOK_TOKEN_CHARS
    from app.config import settings
    from app.models.action_run import ActionRun

    if (
        event.source == "grafana_webhook"
        and len(settings.alerts.webhook_token) < MIN_WEBHOOK_TOKEN_CHARS
    ):
        return (
            "skipped_token",
            f"the alert webhook token is shorter than {MIN_WEBHOOK_TOKEN_CHARS} characters, "
            f"too weak to let an alert run an action",
        )

    alert_runs = (
        ActionRun.action_key == entry.action_key,
        ActionRun.host_id == event.host_id,
        ActionRun.alert_event_id.is_not(None),
    )
    running = await db.execute(
        select(ActionRun.id).where(*alert_runs, ActionRun.status.in_(IN_FLIGHT)).limit(1)
    )
    run_id = running.scalar_one_or_none()
    if run_id is not None:
        return "skipped_in_flight", f"run #{run_id} of this action is still going on the host"

    if entry.cooldown_minutes > 0:
        since = now - timedelta(minutes=entry.cooldown_minutes)
        last = await db.execute(
            select(ActionRun.id, ActionRun.created_at)
            .where(*alert_runs, ActionRun.created_at >= since)
            .order_by(ActionRun.created_at.desc())
            .limit(1)
        )
        row = last.first()
        if row is not None:
            minutes = max(1, int((now - row.created_at).total_seconds() // 60))
            return (
                "skipped_cooldown",
                f"an alert ran it as run #{row.id} {minutes} min ago "
                f"(cooldown {entry.cooldown_minutes} min)",
            )
    return "started", ""


async def dispatch_for(db: AsyncSession, event: Any) -> Any | None:
    """Act on ``event``'s ``labdog_action`` label, if it has one.

    Returns the dispatch row, or ``None`` when the alert asks for no
    action. Creates the run, flushes, and does not commit: the caller
    commits and only then sends the run to the orchestrator, so a worker
    cannot pick up a run that has not landed.
    """
    from app.actions.registry import ACTION_REGISTRY
    from app.actions.run_target import describe_target
    from app.alerts.models import AlertAction, AlertActionDispatch
    from app.audit.logger import log_action
    from app.models.action_run import ActionRun

    if event.status != "firing":
        return None
    key = requested_action(event.labels)
    if key is None:
        return None

    now = datetime.now(UTC)
    entry = (
        await db.execute(select(AlertAction).where(AlertAction.action_key == key))
    ).scalar_one_or_none()
    hand_to_ai = await wants_ai(db, event)

    def done(outcome: str, detail: str = "", run_id: int | None = None) -> Any:
        # Added only once complete: the lookups above autoflush, and a row
        # without its outcome would not insert.
        dispatch = AlertActionDispatch(
            alert_event_id=event.id,
            alert_action_id=entry.id if entry else None,
            action_key=key[:64],
            host_id=event.host_id,
            action_run_id=run_id,
            outcome=outcome,
            detail=detail or None,
            hand_to_ai=hand_to_ai,
            created_at=now,
        )
        db.add(dispatch)
        return dispatch

    if event.host_id is None:
        return done("skipped_no_host", "the alert does not name a host LabDog manages")
    action = ACTION_REGISTRY.get(key)
    if action is None:
        return done("unknown_action", f"no action has the key {key!r}")
    if entry is None:
        return done("not_allowed", f"{key} is not on the list of actions alerts may run")
    if not entry.enabled:
        return done("not_allowed", f"{key} is paused on the list of actions alerts may run")
    if not await in_scope(db, entry, event.host_id):
        _, hostname = await describe_target(db, host_id=event.host_id)
        return done(
            "not_allowed",
            f"alerts may run {key} on {await scope_label(db, entry)}, and {hostname} is not one",
        )
    if not action.supports_host:
        return done("failed", f"{key} no longer runs on a single host")

    outcome, detail = await _guard(db, entry, event, now)
    if outcome != "started":
        return done(outcome, detail)

    target_kind, target_label = await describe_target(db, host_id=event.host_id, group_id=None)
    run = ActionRun(
        action_key=key,
        action_version=action.version,
        host_id=event.host_id,
        group_id=None,
        target_kind=target_kind,
        target_label=target_label,
        alert_event_id=event.id,
        parameters=entry.parameters,
        parallelism=1,
        snapshot_enabled=entry.snapshot_enabled,
        verify_enabled=entry.verify_enabled,
        auto_rollback=entry.auto_rollback,
        status="queued",
        triggered_by_user_id=None,
    )
    db.add(run)
    await db.flush()
    dispatch = done("started", run_id=run.id)
    await log_action(
        db,
        action="alert_action.dispatched",
        entity_type="alert_action",
        entity_id=entry.id,
        user_id=None,
        after_state={
            "action_run_id": run.id,
            "alert_event_id": event.id,
            "alertname": event.alertname,
            "host_id": event.host_id,
        },
    )
    await db.flush()
    return dispatch
