"""What an alert investigation may change, and the guards on full auto.

An alert investigation used to be read-only by construction. This module
decides the level instead, from two settings:

* ``ai.alert_autonomy_level`` — ``read_only`` or ``approval``. What every
  alert gets.
* ``ai.alert_full_auto_alertnames`` — the alerts that may go further and
  change the host with nobody asked.

**Full auto is per alert, never instance-wide.** There is deliberately no
``full_auto`` choice on the level. An alert is a machine's opinion that
something is wrong, and the operator is the one who knows which of those
opinions are specific enough that acting on them unattended is safe — a
"service down" alert with an obvious fix, say, rather than "disk will
fill in four hours". Naming them one by one keeps that judgement theirs.

**A guard downgrades; it never stops the investigation.** A listed alert
that fails any check below still gets a session, at the base level, and
the reason is recorded on the alert row. The checks are about whether a
change may be made without a person, not about whether the alert is worth
looking at — that was already decided by the investigation policy.

The settings layer imports :func:`validate_alertnames`, so nothing at
module level may import :mod:`app.settings_service` or anything that does
(the snapshot module, the models' service layer). Those imports are made
inside the functions that need them, the same arrangement as
:mod:`app.ai.alert_mission`.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import distinct, func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

#: The levels ``ai.alert_autonomy_level`` may take. ``full_auto`` is
#: reached only through the alertname list — see the module docstring.
BASE_LEVELS = ("read_only", "approval")

#: Shortest webhook token that may let an alert change a host.
#:
#: With full auto on, ``POST /api/webhooks/grafana-alerts`` is a way to make
#: LabDog run commands as root, and the shared token is all that guards
#: it. The endpoint already refuses everything while the token is unset;
#: this refuses the next weakest thing, a token someone could guess. 32
#: characters is ``openssl rand -hex 16``.
MIN_WEBHOOK_TOKEN_CHARS = 32

#: Advisory-lock namespace for the remediation decision. The two-key form
#: of ``pg_advisory_xact_lock`` is a separate key space from the one-key
#: form ``app.tasks.host_lock`` uses, so holding this cannot block a sync
#: or an action run from claiming the host. ASCII "AIRM".
_LOCK_NAMESPACE = 0x4149524D

#: A full-auto session still marked queued or running after this long is
#: treated as dead rather than as in flight. Matches the hard
#: ``time_limit`` on ``app.tasks.ai_task.run_chat_session``: past it,
#: Celery has killed the task, and the row only says otherwise because
#: nothing was left to update it. Without a bound, one crashed worker
#: would switch full auto off for that host forever.
_IN_FLIGHT_BOUND = timedelta(seconds=7500)

#: What counts as having changed the host: a mutating command that
#: reached it. ``error`` is included because a non-zero exit is still a
#: command that ran — a restart that failed halfway has changed something.
#: ``blocked`` is not: the classifier, a snapshot failure, or the busy
#: guard stopped it before a socket opened.
_REACHED_HOST = ("executed", "error")

_LEVEL_LABEL = {"read_only": "read-only", "approval": "approval"}


@dataclass(frozen=True)
class AlertAutonomy:
    """The level one alert's session runs at, and why."""

    level: str
    #: Shown on the alert row. Empty when there is nothing to explain — an
    #: alert that is not on the list simply runs at the base level, and
    #: saying so on every row would bury the rows where it matters.
    note: str = ""


def parse_alertnames(raw: str | None) -> list[str]:
    """The names in ``ai.alert_full_auto_alertnames``, one per line."""
    names: list[str] = []
    for line in (raw or "").splitlines():
        name = line.strip()
        if name and name not in names:
            names.append(name)
    return names


def validate_alertnames(raw: str) -> str:
    """Normalise the list, or raise ``ValueError`` saying what is wrong.

    Names are matched exactly, so a wildcard would match nothing and the
    operator would believe they had enabled full auto for a family of
    alerts. Refused rather than accepted silently.
    """
    names = parse_alertnames(raw)
    for name in names:
        if "*" in name or "?" in name:
            raise ValueError(
                f"{name!r}: names are matched exactly and wildcards are not supported. "
                f"List each alert by its full name, one per line."
            )
        if len(name) > 255:
            raise ValueError(f"{name[:40]!r}…: an alert name is at most 255 characters.")
    return "\n".join(names)


def is_unattended_remediation(session: Any) -> bool:
    """Whether ``session`` is an alert session allowed to change the host.

    The one kind of session that changes hosts with nobody having asked
    for it — a full-auto chat or scheduled session was started by a
    person who chose the level. The tighter caps and the busy guard key
    off this.
    """
    return session.mode == "alert_investigation" and session.autonomy_level == "full_auto"


async def resolve(db: AsyncSession, event: Any) -> AlertAutonomy:
    """The level ``event``'s investigation may run at.

    Takes a per-host advisory lock when the alert is a full-auto
    candidate, held until the caller's transaction ends. The caller must
    create the session in the same transaction: the in-flight check reads
    sessions, and two alerts for one host deciding at the same moment
    must not both see none and both go to full auto.
    """
    from app.settings_service import get_setting_typed

    base = str(await get_setting_typed("ai.alert_autonomy_level", db))
    if base not in BASE_LEVELS:
        # Validated on save, so only a hand-edited row reaches here. Fail
        # towards the level that cannot change anything.
        base = "read_only"

    listed = parse_alertnames(str(await get_setting_typed("ai.alert_full_auto_alertnames", db)))
    if event.alertname not in listed:
        return AlertAutonomy(base)

    refusal = await _full_auto_refusal(db, event)
    if refusal:
        return AlertAutonomy(
            base,
            f"On the full-auto list, but ran {_LEVEL_LABEL[base]}: {refusal}.",
        )
    return AlertAutonomy("full_auto", f"Full auto: {event.alertname} is on the full-auto list.")


async def _full_auto_refusal(db: AsyncSession, event: Any) -> str | None:
    """Why ``event`` may not run at full auto, or ``None`` if it may.

    Cheapest checks first; the ones that need the lock last.
    """
    from app.config import settings
    from app.settings_service import get_setting_typed

    if event.status != "firing":
        # Only the investigate button can reach this — the automatic path
        # skips resolved alerts — and a fix for a condition that has
        # already cleared has nothing to verify against.
        return "the alert has already resolved"

    if event.host_id is None:
        return "the alert does not name a host LabDog manages"

    if (
        event.source == "grafana_webhook"
        and len(settings.alerts.webhook_token) < MIN_WEBHOOK_TOKEN_CHARS
    ):
        return (
            f"the alert webhook token is shorter than {MIN_WEBHOOK_TOKEN_CHARS} characters, "
            f"too weak to let an alert change a host"
        )

    if int(await get_setting_typed("ai.alert_full_auto_requires_snapshot", db)):
        refusal = await _snapshot_refusal(db, event.host_id)
        if refusal:
            return refusal

    await db.execute(
        text("SELECT pg_advisory_xact_lock(:namespace, :host_id)"),
        {"namespace": _LOCK_NAMESPACE, "host_id": event.host_id},
    )

    now = datetime.now(UTC)
    if await _in_flight(db, event.host_id, now):
        return "another automatic remediation is already running on this host"

    cooldown = int(await get_setting_typed("ai.alert_remediation_cooldown_minutes", db))
    if cooldown > 0:
        last = await _last_change(
            db, event.host_id, event.alertname, now - timedelta(minutes=cooldown)
        )
        if last is not None:
            minutes = max(1, int((now - last).total_seconds() // 60))
            return (
                f"an automatic fix for this alert changed this host {minutes} min ago "
                f"(cooldown {cooldown} min)"
            )

    cap = int(await get_setting_typed("ai.alert_remediation_daily_cap", db))
    changed = await _sessions_that_changed(db, event.host_id, now - timedelta(hours=24))
    if changed >= cap:
        return f"{changed} automatic fixes changed this host in the last 24 hours (cap {cap})"

    return None


async def _snapshot_refusal(db: AsyncSession, host_id: int) -> str | None:
    """Why there would be no rollback point before a change, if there wouldn't.

    Only the precondition. A host that maps to a VM can still fail to
    snapshot at the moment of the change, and that case is already
    handled where the snapshot is taken: the command is not run.
    """
    from app.ai.snapshots import snapshots_enabled
    from app.proxmox.vm_mapping import VMMapping

    if not await snapshots_enabled(db, skip=False):
        return "ai.snapshot_before_mutating is off, so there would be no rollback point"
    mapped = (
        await db.execute(select(VMMapping.id).where(VMMapping.host_id == host_id).limit(1))
    ).scalar_one_or_none()
    if mapped is None:
        return "the host has no Proxmox VM mapping, so there would be no rollback point"
    return None


def _full_auto_sessions():
    from app.ai.models import AISession

    return (
        AISession.mode == "alert_investigation",
        AISession.autonomy_level == "full_auto",
    )


async def _in_flight(db: AsyncSession, host_id: int, now: datetime) -> bool:
    from app.ai.models import AISession

    found = await db.execute(
        select(AISession.id)
        .where(
            *_full_auto_sessions(),
            AISession.status.in_(("queued", "running")),
            AISession.target_host_ids.contains([host_id]),
            AISession.created_at >= now - _IN_FLIGHT_BOUND,
        )
        .limit(1)
    )
    return found.scalar_one_or_none() is not None


def _changes_on(column: Any, host_id: int, since: datetime):
    """Select ``column`` over the mutating commands full-auto alert
    sessions ran on ``host_id`` since ``since``."""
    from app.ai.models import AISession, AIToolCall

    return (
        select(column)
        .select_from(AIToolCall)
        .join(AISession, AISession.id == AIToolCall.session_id)
        .where(
            *_full_auto_sessions(),
            AIToolCall.target_host_id == host_id,
            AIToolCall.classification == "mutating",
            AIToolCall.status.in_(_REACHED_HOST),
            AIToolCall.started_at >= since,
        )
    )


async def _last_change(
    db: AsyncSession, host_id: int, alertname: str, since: datetime
) -> datetime | None:
    from app.ai.models import AISession, AIToolCall, AlertEvent

    stmt = (
        _changes_on(AIToolCall.started_at, host_id, since)
        .join(AlertEvent, AlertEvent.id == AISession.alert_event_id)
        .where(AlertEvent.alertname == alertname)
        .order_by(AIToolCall.started_at.desc())
        .limit(1)
    )
    return (await db.execute(stmt)).scalar_one_or_none()


async def _sessions_that_changed(db: AsyncSession, host_id: int, since: datetime) -> int:
    from app.ai.models import AIToolCall

    stmt = _changes_on(func.count(distinct(AIToolCall.session_id)), host_id, since)
    return int((await db.execute(stmt)).scalar_one())


async def busy_refusal(
    db: AsyncSession, session: Any, *, classification: str, arguments: dict
) -> str | None:
    """Refuse an unattended change while LabDog itself is changing the host.

    Alert sessions are not part of the per-host queue that syncs and
    action runs share, so without this a remediation could restart a
    service halfway through a sync that is reconfiguring it. Checked
    before every mutating command rather than once at the start, because
    a sync can be dispatched at any point during a session.

    This narrows the overlap rather than closing it: a sync can still
    start while the command is running. Closing it would mean making AI
    sessions a participant in the queue, which this does not attempt.

    Applies only to :func:`is_unattended_remediation` sessions. A person
    who started a full-auto chat chose to act now; an approved command was
    approved by someone looking at the host.
    """
    if classification != "mutating" or not is_unattended_remediation(session):
        return None
    host_id = arguments.get("host_id")
    if not isinstance(host_id, int):
        return None

    from app.tasks.host_lock import check_host_busy

    blocker = await check_host_busy(db, host_id, exclude_action_run_id=session.action_run_id)
    if blocker is None:
        return None

    what = {
        "sync": f"sync {blocker.id}",
        "action_host": f"action run {blocker.id}",
        "action_group": f"group action run {blocker.id}",
    }.get(blocker.kind, f"{blocker.kind} {blocker.id}")
    if blocker.action_key:
        what += f" ({blocker.action_key})"
    return (
        f"Not run: LabDog's {what} is working on this host right now, and an automatic "
        f"remediation does not change a host while LabDog is changing it. Report the change "
        f"you would make instead."
    )
