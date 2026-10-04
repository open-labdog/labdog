"""Whether an automatic fix worked, and undoing one that made things worse.

A full-auto alert session ends when the model says it is done, which is
the model's opinion of its own work. ``ai.alert_remediation_check_minutes``
after the session finished, LabDog checks for itself:

1. Can it still reach the host over SSH?
2. Has a new critical alert fired on the host since the first change?
3. Has the alert the session was started for resolved?

A yes to either of the first two means the host is worse than before the
fix — ``made_worse`` — and, while ``ai.alert_auto_rollback`` is on, it is
restored to the snapshot taken before the session's first change. The
third decides between ``fixed`` and ``not_effective``.

**A fix that only failed is not rolled back.** The host was broken before
the fix as well, and a rollback is not free: Proxmox restores the disk,
which restarts the VM and discards everything written since the snapshot
— mail delivered, files uploaded, rows committed. That is worth it for a
host the fix broke, not for one it failed to mend. The operator is told,
and the session has a button for the rest.

**A check that cannot run in time is reported rather than caught up on.**
One that fell due while LabDog was down runs when it is back, up to
:data:`GRACE` late. Past that it is marked ``unchecked``: judging a host —
and perhaps rolling it back — long after the fact would throw away all
that time's writes on evidence that has gone stale.

Rolling back is shared with the button on the session page: both go
through :func:`plan_rollback`, :func:`prepare_rollback` and
:func:`perform_rollback`, so what a person can do and what LabDog does on
its own are the same operation with the same refusals.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.alert_autonomy import REACHED_HOST
from app.ai.models import AIRollback, AISession, AIToolCall, AlertEvent

logger = logging.getLogger(__name__)

#: How late a check may run, after its due time, and still be made.
GRACE = timedelta(minutes=30)
#: Sessions that finished longer ago than this are never looked at, so
#: switching the check on does not judge fixes made before it existed.
LOOKBACK = timedelta(hours=24)
#: A check or rollback still marked as running after this long was killed
#: with its worker. Longer than either task's hard time limit.
ABANDONED_AFTER = timedelta(hours=1)
#: How long a fix that made a host worse keeps full auto off that host.
WORSE_SUSPENDS_FULL_AUTO = timedelta(hours=24)
#: A host that fails one SSH probe may be mid-restart. Three, twenty
#: seconds apart, is a minute of being unreachable.
PROBE_ATTEMPTS = 3
PROBE_PAUSE_SECONDS = 20.0
PROBE_TIMEOUT_SECONDS = 10
#: How long an automatic rollback waits for a sync or action run on the
#: host to finish before giving up, and how often it looks.
BUSY_WAIT_SECONDS = 600
BUSY_POLL_SECONDS = 30.0
#: A new alert at least this severe, on the host, after the fix, means
#: the fix made things worse.
WORSE_SEVERITY = "critical"

_TERMINAL = ("succeeded", "failed", "cancelled")
_ACTIVE_ROLLBACK = ("running", "succeeded")


@dataclass(frozen=True)
class Assessment:
    outcome: str
    detail: str


class RollbackRefused(Exception):
    """A rollback that will not be attempted. The message says why."""


@dataclass
class RollbackPlan:
    """Everything a rollback needs, resolved before anything is touched."""

    host: Any
    #: The call whose snapshot is restored: the session's first change.
    first: AIToolCall
    #: Snapshots this session took on the host after the first, which some
    #: storage (ZFS) refuses to roll back past. Deleted newest first.
    later: list[AIToolCall]
    target: Any
    key: Any


# ---------------------------------------------------------------------------
# Finding what is due
# ---------------------------------------------------------------------------


async def _settle(db: AsyncSession) -> timedelta:
    from app.settings_service import get_setting_typed

    return timedelta(minutes=int(await get_setting_typed("ai.alert_remediation_check_minutes", db)))


def _changed_its_host():
    """Correlated: the session made a change that reached the alert's host."""
    return (
        select(AIToolCall.id)
        .where(
            AIToolCall.session_id == AISession.id,
            AIToolCall.target_host_id == AlertEvent.host_id,
            AIToolCall.classification == "mutating",
            AIToolCall.status.in_(REACHED_HOST),
        )
        .exists()
    )


async def find_due(db: AsyncSession, now: datetime) -> tuple[list[int], list[int]]:
    """Alert ids whose check is due, and those now too late to make.

    Only full-auto alert sessions that changed their host have anything to
    check. A read-only or approval session changed nothing on its own: a
    person made the call.
    """
    settle = await _settle(db)
    rows = (
        await db.execute(
            select(AlertEvent.id, AISession.finished_at)
            .join(AISession, AISession.id == AlertEvent.investigation_session_id)
            .where(
                AlertEvent.remediation_outcome.is_(None),
                AlertEvent.host_id.is_not(None),
                AISession.mode == "alert_investigation",
                AISession.autonomy_level == "full_auto",
                AISession.status.in_(_TERMINAL),
                AISession.finished_at.is_not(None),
                AISession.finished_at <= now - settle,
                AISession.finished_at > now - LOOKBACK,
                _changed_its_host(),
            )
            .order_by(AISession.finished_at)
        )
    ).all()
    due: list[int] = []
    overdue: list[int] = []
    for event_id, finished_at in rows:
        (overdue if finished_at < now - settle - GRACE else due).append(event_id)
    return due, overdue


async def _set_outcome_from(
    db: AsyncSession, event_id: int, now: datetime, *, expect: str | None, **values: Any
) -> bool:
    """Compare-and-set on the outcome column. True if this caller won."""
    current = (
        AlertEvent.remediation_outcome.is_(None)
        if expect is None
        else AlertEvent.remediation_outcome == expect
    )
    result = await db.execute(
        update(AlertEvent)
        .where(AlertEvent.id == event_id, current)
        .values(remediation_checked_at=now, **values)
        .returning(AlertEvent.id)
        .execution_options(synchronize_session=False)
    )
    return result.scalar_one_or_none() is not None


async def claim(db: AsyncSession, event_id: int, now: datetime) -> bool:
    """Take one check, so two sweeps cannot both dispatch it."""
    return await _set_outcome_from(
        db, event_id, now, expect=None, remediation_outcome="checking", remediation_detail=None
    )


async def mark_unchecked(
    db: AsyncSession, event_id: int, now: datetime, reason: str, *, expect: str | None = None
) -> bool:
    return await _set_outcome_from(
        db,
        event_id,
        now,
        expect=expect,
        remediation_outcome="unchecked",
        remediation_detail=reason,
    )


async def release_abandoned(db: AsyncSession, now: datetime) -> tuple[list[int], int]:
    """Close what a killed worker left running.

    Returns the alert ids whose check was abandoned — now ``unchecked`` —
    and how many rollbacks were marked failed. Neither is retried: by the
    time this runs the moment for both has passed.
    """
    stale = now - ABANDONED_AFTER
    events = (
        (
            await db.execute(
                update(AlertEvent)
                .where(
                    AlertEvent.remediation_outcome == "checking",
                    AlertEvent.remediation_checked_at < stale,
                )
                .values(
                    remediation_outcome="unchecked",
                    remediation_detail=(
                        "The check started but never finished — the worker running it "
                        "stopped. Look at the host and the session yourself."
                    ),
                    remediation_checked_at=now,
                )
                .returning(AlertEvent.id)
                .execution_options(synchronize_session=False)
            )
        )
        .scalars()
        .all()
    )
    rollbacks = (
        (
            await db.execute(
                update(AIRollback)
                .where(AIRollback.status == "running", AIRollback.started_at < stale)
                .values(
                    status="failed",
                    detail=(
                        "The rollback started but never finished — the worker running it "
                        "stopped. Check the VM in Proxmox: it may be stopped, or restored "
                        "but not started."
                    ),
                    finished_at=now,
                )
                .returning(AIRollback.id)
                .execution_options(synchronize_session=False)
            )
        )
        .scalars()
        .all()
    )
    return list(events), len(rollbacks)


# ---------------------------------------------------------------------------
# Judging the fix
# ---------------------------------------------------------------------------


async def first_change(db: AsyncSession, session_id: int, host_id: int) -> AIToolCall | None:
    """The session's first command that changed ``host_id``."""
    return (
        await db.execute(
            select(AIToolCall)
            .where(
                AIToolCall.session_id == session_id,
                AIToolCall.target_host_id == host_id,
                AIToolCall.classification == "mutating",
                AIToolCall.status.in_(REACHED_HOST),
            )
            .order_by(AIToolCall.id)
            .limit(1)
        )
    ).scalar_one_or_none()


async def probe_reachable(db: AsyncSession, host: Any) -> str | None:
    """Why LabDog cannot reach ``host``; ``None`` if it can.

    Also ``None`` when there is no key to try with: the session could not
    have changed the host without one, and "cannot tell" is not evidence
    that the fix broke anything.
    """
    from app.ssh_utils import HostKeyMismatchError, load_host_key, ssh_connect_host

    key = await load_host_key(db, host)
    if key is None:
        return None
    last = ""
    for attempt in range(PROBE_ATTEMPTS):
        if attempt:
            await asyncio.sleep(PROBE_PAUSE_SECONDS)
        try:
            async with ssh_connect_host(
                host, db, client_keys=[key], connect_timeout=PROBE_TIMEOUT_SECONDS
            ) as conn:
                await asyncio.wait_for(conn.run("true", check=False), PROBE_TIMEOUT_SECONDS)
            return None
        except HostKeyMismatchError:
            # Not retried: a changed host key does not change back. The fix
            # may have regenerated it, and LabDog will not connect until
            # someone trusts the new one — the host is out of its reach.
            return "its SSH host key has changed, so LabDog refuses to connect"
        except Exception as exc:
            last = str(exc) or type(exc).__name__
    pause = int(PROBE_PAUSE_SECONDS)
    return f"{PROBE_ATTEMPTS} attempts {pause}s apart all failed, the last with: {last}"


async def new_critical_alerts(
    db: AsyncSession, event: AlertEvent, *, since: datetime
) -> list[AlertEvent]:
    """Other alerts on the host, still firing, that began after ``since``.

    The alert's own later firings are excluded by fingerprint: the same
    alert coming back means the fix did not hold, not that it broke
    something else.
    """
    from app.ai.alerts import meets_severity

    rows = (
        (
            await db.execute(
                select(AlertEvent).where(
                    AlertEvent.host_id == event.host_id,
                    AlertEvent.id != event.id,
                    AlertEvent.fingerprint != event.fingerprint,
                    AlertEvent.status == "firing",
                    AlertEvent.starts_at >= since,
                )
            )
        )
        .scalars()
        .all()
    )
    return [row for row in rows if meets_severity(row.severity, WORSE_SEVERITY)]


async def still_firing(db: AsyncSession, event: AlertEvent) -> bool:
    """Whether the alert has not resolved, or has fired again since."""
    if event.status == "firing":
        return True
    again = await db.execute(
        select(AlertEvent.id)
        .where(
            AlertEvent.fingerprint == event.fingerprint,
            AlertEvent.starts_at > event.starts_at,
            AlertEvent.status == "firing",
        )
        .limit(1)
    )
    return again.scalar_one_or_none() is not None


async def assess(db: AsyncSession, event: AlertEvent, session: AISession) -> Assessment:
    """What became of the fix. See the module docstring for the rules."""
    from app.models.host import Host

    host = await db.get(Host, event.host_id)
    if host is None:
        return Assessment("unchecked", "The host was removed from LabDog before the check.")

    first = await first_change(db, session.id, host.id)
    since = first.started_at if first is not None else (session.started_at or session.created_at)
    minutes = int((await _settle(db)).total_seconds() // 60)

    worse: list[str] = []
    unreachable = await probe_reachable(db, host)
    if unreachable:
        worse.append(f"LabDog can no longer reach {host.hostname} over SSH: {unreachable}")
    fired = await new_critical_alerts(db, event, since=since)
    if fired:
        names = ", ".join(sorted({row.alertname for row in fired}))
        worse.append(f"a new critical alert fired on {host.hostname} after the fix: {names}")
    if worse:
        return Assessment("made_worse", "; ".join(worse) + ".")

    if await still_firing(db, event):
        return Assessment(
            "not_effective",
            f"{event.alertname} was still firing {minutes} min after the session ended. "
            f"{host.hostname} is reachable and no new critical alert fired on it, so it was "
            f"not rolled back.",
        )
    return Assessment(
        "fixed",
        f"{event.alertname} resolved, {host.hostname} is reachable over SSH, and no new "
        f"critical alert fired on it.",
    )


async def record_outcome(
    db: AsyncSession, event: AlertEvent, assessment: Assessment, now: datetime
) -> None:
    from app.audit.logger import log_action

    event.remediation_outcome = assessment.outcome
    event.remediation_detail = assessment.detail[:4000]
    event.remediation_checked_at = now
    await log_action(
        db,
        action="ai_remediation_checked",
        entity_type="alert_event",
        entity_id=event.id,
        user_id=None,
        after_state={
            "outcome": assessment.outcome,
            "detail": assessment.detail,
            "session_id": event.investigation_session_id,
            "host_id": event.host_id,
            "alertname": event.alertname,
        },
    )


async def awaiting_check(db: AsyncSession, host_id: int, now: datetime) -> bool:
    """Whether a fix on ``host_id`` has finished and not yet been judged.

    A second unattended fix in that window would leave the check unable to
    tell which one the host's state is down to — and a rollback of the
    first would undo the second too.
    """
    found = await db.execute(
        select(AlertEvent.id)
        .join(AISession, AISession.id == AlertEvent.investigation_session_id)
        .where(
            AlertEvent.host_id == host_id,
            or_(
                AlertEvent.remediation_outcome.is_(None),
                AlertEvent.remediation_outcome == "checking",
            ),
            AISession.mode == "alert_investigation",
            AISession.autonomy_level == "full_auto",
            AISession.status.in_(_TERMINAL),
            AISession.finished_at > now - LOOKBACK,
            _changed_its_host(),
        )
        .limit(1)
    )
    return found.scalar_one_or_none() is not None


async def recently_made_worse(db: AsyncSession, host_id: int, now: datetime) -> datetime | None:
    """When an automatic fix last made ``host_id`` worse, inside the window."""
    return (
        await db.execute(
            select(AlertEvent.remediation_checked_at)
            .where(
                AlertEvent.host_id == host_id,
                AlertEvent.remediation_outcome == "made_worse",
                AlertEvent.remediation_checked_at >= now - WORSE_SUSPENDS_FULL_AUTO,
            )
            .order_by(AlertEvent.remediation_checked_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()


# ---------------------------------------------------------------------------
# Rolling back
# ---------------------------------------------------------------------------


async def host_busy(db: AsyncSession, host_id: int) -> str | None:
    """What LabDog itself is doing to the host right now, if anything."""
    from app.tasks.host_lock import check_host_busy

    blocker = await check_host_busy(db, host_id)
    if blocker is None:
        return None
    what = {
        "sync": "sync",
        "action_host": "action run",
        "action_group": "group action run",
    }.get(blocker.kind, blocker.kind)
    return f"LabDog's {what} {blocker.id} is working on this host"


async def plan_rollback(db: AsyncSession, *, session: AISession, host_id: int) -> RollbackPlan:
    """Resolve what a rollback of ``session`` on ``host_id`` would restore.

    Raises :class:`RollbackRefused` with a reason a person can act on.
    Changes nothing.
    """
    from app.ai.labdog_host import labdog_runs_on
    from app.ai.snapshots import SnapshotFailed, resolve_target
    from app.models.host import Host
    from app.ssh_utils import load_host_key

    host = await db.get(Host, host_id)
    if host is None:
        raise RollbackRefused("The host is no longer in LabDog.")

    if await labdog_runs_on(db, host):
        raise RollbackRefused(
            f"LabDog runs on {host.hostname}. Rolling it back would stop LabDog half-way, "
            f"with nothing left to start the machine again, and put LabDog's own database "
            f"back to the snapshot. Roll it back in Proxmox if you need to."
        )

    first = await first_change(db, session.id, host_id)
    if first is None:
        raise RollbackRefused(f"This session did not change {host.hostname}.")
    if not first.snapshot_name:
        raise RollbackRefused(
            f"No snapshot was taken before this session's first change on {host.hostname}, "
            f"so there is nothing to restore that would undo it."
        )
    if first.snapshot_pruned_at is not None:
        raise RollbackRefused(
            f"The snapshot taken before the first change, {first.snapshot_name}, was removed "
            f"by retention on {first.snapshot_pruned_at:%Y-%m-%d}."
        )

    later = list(
        (
            await db.execute(
                select(AIToolCall)
                .where(
                    AIToolCall.session_id == session.id,
                    AIToolCall.target_host_id == host_id,
                    AIToolCall.snapshot_name.is_not(None),
                    AIToolCall.snapshot_pruned_at.is_(None),
                    AIToolCall.id > first.id,
                )
                .order_by(AIToolCall.id)
            )
        )
        .scalars()
        .all()
    )

    try:
        target = await resolve_target(db, host_id)
    except SnapshotFailed as exc:
        raise RollbackRefused(str(exc)) from exc
    if target is None:
        raise RollbackRefused(f"{host.hostname} no longer maps to a Proxmox VM.")

    key = await load_host_key(db, host)
    if key is None:
        raise RollbackRefused(
            f"{host.hostname} has no usable SSH key, so LabDog could not confirm it came back."
        )
    return RollbackPlan(host=host, first=first, later=later, target=target, key=key)


async def prepare_rollback(
    db: AsyncSession,
    *,
    session: AISession,
    host_id: int,
    trigger: str,
    user_id: int | None = None,
    alert_event_id: int | None = None,
) -> tuple[AIRollback, RollbackPlan]:
    """Claim the rollback: a ``running`` row, or :class:`RollbackRefused`.

    The partial unique index on ``ai_rollbacks`` is what stops two at once
    — a person and the check, say. The lookup first is only for a better
    message than the constraint's.
    """
    if session.status in ("queued", "running", "waiting_approval"):
        raise RollbackRefused(
            "The session is still running. Cancel it first: a rollback underneath it would "
            "leave it working on a host that has gone back in time."
        )
    existing = (
        await db.execute(
            select(AIRollback)
            .where(
                AIRollback.session_id == session.id,
                AIRollback.host_id == host_id,
                AIRollback.status.in_(_ACTIVE_ROLLBACK),
            )
            .limit(1)
        )
    ).scalar_one_or_none()
    if existing is not None:
        raise RollbackRefused(
            "This host is already being rolled back."
            if existing.status == "running"
            else f"This session's changes on this host were already rolled back, to "
            f"{existing.snapshot_name}, on {existing.started_at:%Y-%m-%d %H:%M} UTC."
        )

    plan = await plan_rollback(db, session=session, host_id=host_id)
    rollback = AIRollback(
        session_id=session.id,
        host_id=host_id,
        alert_event_id=alert_event_id,
        hostname=plan.host.hostname,
        snapshot_name=plan.first.snapshot_name,
        trigger=trigger,
        requested_by_user_id=user_id,
        status="running",
        started_at=datetime.now(UTC),
    )
    try:
        async with db.begin_nested():
            db.add(rollback)
            await db.flush()
    except IntegrityError as exc:
        raise RollbackRefused("This host is already being rolled back.") from exc
    return rollback, plan


async def record_refused(
    db: AsyncSession,
    *,
    session: AISession,
    host_id: int,
    trigger: str,
    reason: str,
    alert_event_id: int | None = None,
) -> AIRollback:
    """Keep a rollback that was wanted and not attempted, with the reason."""
    from app.models.host import Host

    host = await db.get(Host, host_id)
    now = datetime.now(UTC)
    rollback = AIRollback(
        session_id=session.id,
        host_id=host_id,
        alert_event_id=alert_event_id,
        hostname=host.hostname if host is not None else "",
        trigger=trigger,
        status="refused",
        detail=reason[:4000],
        started_at=now,
        finished_at=now,
    )
    db.add(rollback)
    await db.flush()
    return rollback


async def perform_rollback(db: AsyncSession, rollback: AIRollback, plan: RollbackPlan) -> None:
    """Restore the snapshot, start the machine, and wait for SSH.

    Never raises: the outcome, good or bad, is written to ``rollback``.
    """
    from app.audit.logger import log_action
    from app.workflows.steps import rollback as rollback_step
    from app.workflows.steps.cleanup import delete_snapshot

    target = plan.target
    notes: list[str] = []
    for call in reversed(plan.later):
        try:
            await delete_snapshot(
                target.client,
                target.pve_node,
                target.vmid,
                call.snapshot_name,
                vm_type=target.vm_type,
            )
            call.snapshot_pruned_at = datetime.now(UTC)
        except Exception as exc:
            notes.append(f"could not delete the later snapshot {call.snapshot_name} first ({exc})")

    try:
        result = await rollback_step.rollback_to_snapshot(
            target.client,
            target.pve_node,
            target.vmid,
            plan.first.snapshot_name,
            plan.host,
            plan.key,
            db,
            vm_type=target.vm_type,
        )
    except Exception as exc:
        logger.exception("remediation: rollback %s raised", rollback.id)
        result = {"success": False, "error": str(exc)}

    ok = bool(result.get("success"))
    if ok:
        summary = (
            f"Restored {target.vm_type} {target.vmid} to {plan.first.snapshot_name} and "
            f"started it; SSH answered again. The host is marked out of sync."
        )
    else:
        summary = f"Rolling back to {plan.first.snapshot_name} failed: {result.get('error')}"
    rollback.status = "succeeded" if ok else "failed"
    rollback.detail = "; ".join([summary, *notes])[:4000]
    rollback.finished_at = datetime.now(UTC)
    await log_action(
        db,
        action="ai_rollback",
        entity_type="ai_session",
        entity_id=rollback.session_id,
        user_id=rollback.requested_by_user_id,
        after_state={
            "host_id": rollback.host_id,
            "hostname": rollback.hostname,
            "snapshot_name": rollback.snapshot_name,
            "trigger": rollback.trigger,
            "status": rollback.status,
            "detail": rollback.detail,
        },
    )


async def roll_back_automatically(
    db: AsyncSession, event: AlertEvent, session: AISession
) -> AIRollback:
    """The check's rollback, after a fix made the host worse.

    Waits up to :data:`BUSY_WAIT_SECONDS` for LabDog's own work on the host
    to finish, the same rule the fix itself followed. Commits the
    ``running`` row before Proxmox is touched, so the page shows it while
    it happens; the caller commits the result.
    """
    from app.settings_service import get_setting_typed

    def refused(reason: str):
        return record_refused(
            db,
            session=session,
            host_id=event.host_id,
            trigger="automatic",
            reason=reason,
            alert_event_id=event.id,
        )

    if not int(await get_setting_typed("ai.alert_auto_rollback", db)):
        return await refused("Automatic rollback is off (ai.alert_auto_rollback).")

    deadline = time.monotonic() + BUSY_WAIT_SECONDS
    while busy := await host_busy(db, event.host_id):
        if time.monotonic() >= deadline:
            minutes = BUSY_WAIT_SECONDS // 60
            return await refused(f"{busy}, and still was after {minutes} minutes.")
        await db.commit()
        await asyncio.sleep(BUSY_POLL_SECONDS)

    try:
        rollback, plan = await prepare_rollback(
            db,
            session=session,
            host_id=event.host_id,
            trigger="automatic",
            alert_event_id=event.id,
        )
    except RollbackRefused as exc:
        return await refused(str(exc))
    await db.commit()
    await perform_rollback(db, rollback, plan)
    return rollback
