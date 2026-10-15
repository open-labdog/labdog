"""Alert-triggered schedules: run an action on the host an alert names.

A schedule with ``trigger = "alert"`` reacts to alerts on the hosts in its
target (one host, a group, or the fleet) whose name and labels match
``alert_match``. The match is the rule language of
:mod:`app.ai.full_auto_rules` without ``host:`` and ``group:``, which are
the target's job: one rule per line, comma-separated conditions that must
all hold, an alert qualifying when any rule does.

The run is always a single-host run on the alert's host, whatever the
target, with the schedule's parameters and snapshot / verify / rollback
choices. What happened is recorded per (alert, schedule) in
:class:`app.alerts.models.AlertActionDispatch`, and judged afterwards by
:mod:`app.tasks.alert_handling`.

Guards, in the order they are checked:

* only a new firing alert dispatches — repeats of the same firing and
  resolve notifications do not;
* the alert must name a host LabDog manages, inside the target;
* a webhook alert needs the webhook token to be at least
  ``MIN_WEBHOOK_TOKEN_CHARS`` long, as for full auto: the token is all
  that stands between the webhook and a run on your hosts;
* no second run of the schedule on the host while one is queued, waiting
  or running;
* ``alert_cooldown_minutes`` since the schedule's last alert-triggered
  run on the host.

The orchestrator's host lock then serialises the run with anything else
working on the host, as for any run.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.full_auto_rules import Rule, parse_rules

logger = logging.getLogger(__name__)

#: Run states that count as "still going" for the in-flight guard.
IN_FLIGHT = ("queued", "pending", "running")


def parse_match(raw: str | None) -> list[Rule]:
    """The rules in an alert trigger's match, or ``ValueError``.

    Hosts and groups are refused: an alert trigger's target already says
    which hosts it covers, and two places to say it would disagree.
    """
    rules = parse_rules(raw)
    if not rules:
        raise ValueError("an alert trigger needs at least one rule, such as the alert's name")
    for number, rule in enumerate(rules, start=1):
        if any(c.about_host for c in rule.conditions):
            raise ValueError(
                f"rule {number} ({rule.text}): hosts and groups come from the schedule's "
                f"target, not from the match"
            )
    return rules


def validate_match(raw: str) -> str:
    """Normalise a match for saving: each line trimmed, blank edges dropped."""
    parse_match(raw)
    return "\n".join(line.strip() for line in raw.strip().splitlines())


def matches(rules: list[Rule], event: Any) -> bool:
    labels = event.labels or {}
    return any(rule.matches(event.alertname, labels, None, set()) for rule in rules)


async def in_scope(db: AsyncSession, schedule: Any, host_id: int | None) -> bool:
    """Whether ``host_id`` is inside ``schedule``'s target."""
    if host_id is None:
        return False
    if schedule.target_kind == "fleet":
        return True
    if schedule.target_kind == "host":
        return schedule.target_id == host_id
    from app.models.host import HostGroupMembership

    found = await db.execute(
        select(HostGroupMembership.c.host_id).where(
            HostGroupMembership.c.host_id == host_id,
            HostGroupMembership.c.group_id == schedule.target_id,
        )
    )
    return found.first() is not None


async def alert_triggers(db: AsyncSession, *, enabled_only: bool = True) -> list[Any]:
    from app.models.scheduled_action import ScheduledAction

    stmt = select(ScheduledAction).where(ScheduledAction.trigger == "alert")
    if enabled_only:
        stmt = stmt.where(ScheduledAction.enabled.is_(True))
    return list((await db.execute(stmt.order_by(ScheduledAction.id))).scalars().all())


def _rules_of(schedule: Any) -> list[Rule] | None:
    try:
        return parse_match(schedule.alert_match)
    except ValueError:
        # Validated on save; only a row written around the API gets here.
        logger.warning("alert trigger %d has an unreadable match; skipping it", schedule.id)
        return None


async def _guard(db: AsyncSession, schedule: Any, event: Any, now: datetime) -> tuple[str, str]:
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

    running = await db.execute(
        select(ActionRun.id)
        .where(
            ActionRun.scheduled_action_id == schedule.id,
            ActionRun.host_id == event.host_id,
            ActionRun.status.in_(IN_FLIGHT),
        )
        .limit(1)
    )
    run_id = running.scalar_one_or_none()
    if run_id is not None:
        return "skipped_in_flight", f"run #{run_id} of this schedule is still going on the host"

    if schedule.alert_cooldown_minutes > 0:
        since = now - timedelta(minutes=schedule.alert_cooldown_minutes)
        last = await db.execute(
            select(ActionRun.id, ActionRun.created_at)
            .where(
                ActionRun.scheduled_action_id == schedule.id,
                ActionRun.host_id == event.host_id,
                ActionRun.alert_event_id.is_not(None),
                ActionRun.created_at >= since,
            )
            .order_by(ActionRun.created_at.desc())
            .limit(1)
        )
        row = last.first()
        if row is not None:
            minutes = max(1, int((now - row.created_at).total_seconds() // 60))
            return (
                "skipped_cooldown",
                f"run #{row.id} was {minutes} min ago "
                f"(cooldown {schedule.alert_cooldown_minutes} min)",
            )
    return "started", ""


async def dispatch_for(db: AsyncSession, event: Any) -> list[Any]:
    """Apply every enabled alert trigger to ``event``.

    Creates the runs and the dispatch rows, flushes, and does not commit:
    the caller commits and only then sends the runs to the orchestrator,
    so a worker cannot pick up a run that has not landed.
    """
    from app.actions.registry import ACTION_REGISTRY
    from app.actions.run_target import describe_target
    from app.alerts.models import AlertActionDispatch
    from app.audit.logger import log_action
    from app.models.action_run import ActionRun

    if event.status != "firing":
        return []

    now = datetime.now(UTC)
    dispatches: list[AlertActionDispatch] = []
    for schedule in await alert_triggers(db):
        rules = _rules_of(schedule)
        if not rules or not matches(rules, event):
            continue

        def record(outcome: str, detail: str = "", run_id: int | None = None, *, of=schedule):
            row = AlertActionDispatch(
                alert_event_id=event.id,
                scheduled_action_id=of.id,
                action_key=of.action_key,
                host_id=event.host_id,
                action_run_id=run_id,
                outcome=outcome,
                detail=detail or None,
                investigate_on_failure=of.investigate_on_failure,
                created_at=now,
            )
            db.add(row)
            dispatches.append(row)

        if event.host_id is None:
            # Recorded even though the target cannot be checked: a trigger
            # that matched the alert and did nothing should say why.
            record("skipped_no_host", "the alert does not name a host LabDog manages")
            continue
        if not await in_scope(db, schedule, event.host_id):
            continue

        action = ACTION_REGISTRY.get(schedule.action_key)
        if action is None:
            record("failed", f"action {schedule.action_key!r} is no longer registered")
            continue

        outcome, detail = await _guard(db, schedule, event, now)
        if outcome != "started":
            record(outcome, detail)
            continue

        target_kind, target_label = await describe_target(db, host_id=event.host_id, group_id=None)
        run = ActionRun(
            action_key=schedule.action_key,
            action_version=action.version,
            host_id=event.host_id,
            group_id=None,
            target_kind=target_kind,
            target_label=target_label,
            scheduled_action_id=schedule.id,
            alert_event_id=event.id,
            parameters=schedule.parameters,
            parallelism=1,
            snapshot_enabled=schedule.snapshot_enabled,
            verify_enabled=schedule.verify_enabled,
            auto_rollback=schedule.auto_rollback,
            status="queued",
            triggered_by_user_id=None,
        )
        db.add(run)
        await db.flush()
        schedule.last_dispatched_at = now
        record("started", "", run.id)
        await log_action(
            db,
            action="scheduled_action.dispatched",
            entity_type="scheduled_action",
            entity_id=schedule.id,
            user_id=None,
            after_state={
                "action_run_id": run.id,
                "trigger": "alert",
                "alert_event_id": event.id,
                "alertname": event.alertname,
                "host_id": event.host_id,
            },
        )

    await db.flush()
    return dispatches


async def would_have_matched(db: AsyncSession, schedule: Any, *, limit: int = 20) -> list[Any]:
    """Recent alerts this schedule's match and target would have fired on.

    Read from the alert history, newest first, so a trigger can be checked
    against what has actually arrived before it is switched on. Only the
    match and the target are applied; the guards depend on the moment.
    """
    from app.ai.models import AlertEvent

    rules = _rules_of(schedule)
    if not rules:
        return []
    recent = (
        (
            await db.execute(
                select(AlertEvent)
                .where(AlertEvent.status.in_(("firing", "resolved")))
                .order_by(AlertEvent.created_at.desc())
                .limit(500)
            )
        )
        .scalars()
        .all()
    )
    hits = []
    for event in recent:
        if matches(rules, event) and await in_scope(db, schedule, event.host_id):
            hits.append(event)
            if len(hits) >= limit:
                break
    return hits
