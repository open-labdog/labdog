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

**A cancelled session is not judged.** Someone stopped it, most likely to
take the host over, and what a check would then see is their work, not
the model's: a rollback would undo their repair without asking.

**A host LabDog cannot reach only counts if LabDog can reach Proxmox.**
When the SSH probe fails, Proxmox is asked as well. If it does not answer
either, the fault may be on LabDog's side — a lost route, DNS — and the
check is ``unchecked`` rather than a reason to roll back.

**A check that cannot run in time is reported rather than caught up on.**
One that fell due while LabDog was down runs when it is back, up to
:data:`GRACE` late. Past that it is marked ``unchecked``: judging a host —
and perhaps rolling it back — long after the fact would throw away all
that time's writes on evidence that has gone stale.

Rolling back is shared with the button on the session page: both go
through :func:`plan_rollback`, :func:`prepare_rollback` and
:func:`perform_rollback`, so what a person can do and what LabDog does on
its own are the same operation with the same refusals, and the button is
enabled by the same rules (:func:`check_rollback`). A rollback takes part
in the per-host queue (:mod:`app.tasks.host_lock`): it claims the host
under its lock, holds it while Proxmox restores the machine, and hands it
to the next queued sync or action run when it ends.
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import and_, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.ai.alert_autonomy import REACHED_HOST
from app.ai.models import (
    TERMINAL_SESSION_STATUSES,
    AIRollback,
    AISession,
    AIToolCall,
    AlertEvent,
)

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
#: How long Proxmox gets to answer when it is asked as the comparison for
#: a failed SSH probe.
CONTROL_TIMEOUT_SECONDS = 15
#: How long an automatic rollback waits for a sync or action run on the
#: host to finish before giving up, and how often it looks.
BUSY_WAIT_SECONDS = 600
BUSY_POLL_SECONDS = 30.0
#: A new alert at least this severe, on the host, after the fix, means
#: the fix made things worse.
WORSE_SEVERITY = "critical"

#: The sessions whose fix is judged. Not ``cancelled``: see the module
#: docstring.
_JUDGED = ("succeeded", "failed")
_ACTIVE_ROLLBACK = ("running", "succeeded")

#: Storage types that can only restore a disk's newest snapshot: ZFS,
#: local or over iSCSI. Proxmox refuses to roll back past a newer one.
NEWEST_ONLY_STORAGE = frozenset({"zfspool", "zfs"})
_QEMU_DISK = re.compile(r"^(ide|sata|scsi|virtio|efidisk|tpmstate)\d+$")
_LXC_DISK = re.compile(r"^(rootfs|mp\d+)$")


@dataclass(frozen=True)
class Assessment:
    outcome: str
    detail: str


class RollbackRefused(Exception):
    """A rollback that will not be attempted. The message says why."""


class HostBusy(RollbackRefused):
    """LabDog's own work holds the host. The message says which."""


@dataclass
class RollbackPlan:
    """Everything a rollback needs, resolved before anything is touched."""

    host: Any
    #: The call whose snapshot is restored: the session's first change.
    first: AIToolCall
    #: Snapshots this session took on the host after the first. Some
    #: storage (ZFS) refuses to roll back past them; there they are
    #: deleted first, newest first, once the restore is known to be
    #: possible.
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
                AISession.status.in_(_JUDGED),
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


async def release_abandoned(db: AsyncSession, now: datetime) -> tuple[list[int], list[int]]:
    """Close what a killed worker left running.

    Returns the alert ids whose check was abandoned — now ``unchecked`` —
    and the hosts of the rollbacks marked failed, whose queues the caller
    releases once this is committed. Neither is retried: by the time this
    runs the moment for both has passed.
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
                .returning(AIRollback.host_id)
                .execution_options(synchronize_session=False)
            )
        )
        .scalars()
        .all()
    )
    return list(events), [host_id for host_id in rollbacks if host_id is not None]


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


def _say(exc: BaseException) -> str:
    """An exception as an operator should read it.

    A timeout has no message of its own, and its class name is not one an
    operator should have to decode.
    """
    return str(exc) or ("timed out" if isinstance(exc, TimeoutError) else type(exc).__name__)


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
            last = _say(exc)
    pause = int(PROBE_PAUSE_SECONDS)
    return f"{PROBE_ATTEMPTS} attempts {pause}s apart all failed, the last with: {last}"


async def proxmox_answers(db: AsyncSession, host: Any) -> str | None:
    """Why LabDog cannot reach Proxmox; ``None`` if it can, or has none to ask.

    The comparison for a failed SSH probe. The host's own node is asked
    about its VM; a host without one is compared against any Proxmox node
    LabDog knows. An error *response* counts as an answer — a refused
    token still means the network between LabDog and Proxmox works.
    """
    from app.ai.snapshots import client_for, resolve_target
    from app.proxmox.client import ProxmoxError
    from app.proxmox.models import ProxmoxNode

    try:
        target = await resolve_target(db, host.id)
        if target is not None:
            asking = target.client.get_vm_status(
                target.pve_node, target.vmid, vm_type=target.vm_type
            )
        else:
            node = (
                await db.execute(select(ProxmoxNode).order_by(ProxmoxNode.id).limit(1))
            ).scalar_one_or_none()
            if node is None:
                return None
            asking = client_for(node).test_connection()
        await asyncio.wait_for(asking, CONTROL_TIMEOUT_SECONDS)
    except ProxmoxError as exc:
        return None if exc.status_code is not None else _say(exc)
    except Exception as exc:
        return _say(exc)
    return None


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
    cut_off = None
    unreachable = await probe_reachable(db, host)
    if unreachable:
        proxmox_down = await proxmox_answers(db, host)
        if proxmox_down:
            cut_off = (
                f"LabDog could not reach {host.hostname} over SSH ({unreachable}), and could "
                f"not reach Proxmox either ({proxmox_down}), so the fault may be on LabDog's side"
            )
        else:
            worse.append(f"LabDog can no longer reach {host.hostname} over SSH: {unreachable}")
    fired = await new_critical_alerts(db, event, since=since)
    if fired:
        names = ", ".join(sorted({row.alertname for row in fired}))
        worse.append(f"a new critical alert fired on {host.hostname} after the fix: {names}")
    if worse:
        return Assessment("made_worse", "; ".join([*worse, *filter(None, [cut_off])]) + ".")
    if cut_off:
        return Assessment(
            "unchecked",
            f"{cut_off}. The fix was not judged and the host was not rolled back. Look at the "
            f"host and the session yourself.",
        )

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
) -> bool:
    """Write the verdict, unless the check was closed while it ran.

    A check that waited long enough — in the queue, on the probe, on a
    busy host — is marked ``unchecked`` by :func:`release_abandoned`, and
    the operator is told so. A verdict written over that would contradict
    the email already sent, and a rollback after it would restore a host
    the operator was just told to look at themselves. ``False`` when that
    happened: the caller then stops.
    """
    from app.audit.logger import log_action

    if not await _set_outcome_from(
        db,
        event.id,
        now,
        expect="checking",
        remediation_outcome=assessment.outcome,
        remediation_detail=assessment.detail[:4000],
    ):
        return False
    await db.refresh(event)
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
    return True


async def auto_rollback_on(db: AsyncSession) -> bool:
    from app.settings_service import get_setting_typed

    return bool(int(await get_setting_typed("ai.alert_auto_rollback", db)))


async def judge(
    db: AsyncSession, event: AlertEvent, session: AISession
) -> tuple[Assessment, str | None]:
    """:func:`assess`, made sure of before anything acts on it.

    A ``made_worse`` that will be rolled back first waits up to
    :data:`BUSY_WAIT_SECONDS` for LabDog's own work on the host to finish,
    the same rule the fix itself followed. If it had to wait, it looks
    again: a probe that failed while a sync reloaded the firewall or
    restarted networking says nothing about the fix, and a rollback on
    that evidence would throw away the sync's work with everything else.

    Returns the verdict, and what still held the host if the wait ran out.
    Commits while it waits.
    """
    assessment = await assess(db, event, session)
    if assessment.outcome != "made_worse" or not await auto_rollback_on(db):
        return assessment, None
    waited, busy = await wait_until_free(db, event.host_id)
    if waited:
        assessment = await assess(db, event, session)
    return assessment, busy


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
            AISession.status.in_(_JUDGED),
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
    """What LabDog itself is doing to the host right now, if anything.

    Takes the host's advisory lock first, as ``check_host_busy`` requires,
    and holds it until the caller's transaction ends: a caller that goes on
    to claim the host does so atomically with this answer, and one that
    only wanted the answer commits straight away.
    """
    from app.tasks.host_lock import acquire_host_lock, check_host_busy

    await acquire_host_lock(db, host_id)
    blocker = await check_host_busy(db, host_id)
    if blocker is None:
        return None
    what = {
        "sync": "sync",
        "action_host": "action run",
        "action_group": "group action run",
        "ai_rollback": "rollback",
    }.get(blocker.kind, blocker.kind)
    return f"LabDog's {what} {blocker.id} is working on this host"


async def wait_until_free(db: AsyncSession, host_id: int) -> tuple[bool, str | None]:
    """Wait up to :data:`BUSY_WAIT_SECONDS` for LabDog's own work on the host.

    Returns whether it had to wait at all, and what still held the host
    when it gave up (``None`` once the host is free). Commits after every
    look, releasing the lock :func:`host_busy` took, so the sync it waits
    for can finish and hand the host on.
    """
    deadline = time.monotonic() + BUSY_WAIT_SECONDS
    waited = False
    while True:
        busy = await host_busy(db, host_id)
        await db.commit()
        if busy is None or time.monotonic() >= deadline:
            return waited, busy
        waited = True
        await asyncio.sleep(BUSY_POLL_SECONDS)


async def _passed_over(
    db: AsyncSession, host_id: int, session_id: int, first: AIToolCall
) -> AIRollback | None:
    """Another session's rollback of the host that went back past ``first``.

    On LVM-thin or qcow2 a snapshot outlives a rollback past it. Session
    S1 snapshots X, S2 later snapshots Y, and S1's rollback restores X: Y
    now holds S1's change, the one that rollback undid, and restoring Y
    would bring it back.
    """
    restored = aliased(AIToolCall)
    rows = (
        await db.execute(
            select(AIRollback, restored.started_at)
            .outerjoin(
                restored,
                and_(
                    restored.snapshot_name == AIRollback.snapshot_name,
                    restored.target_host_id == AIRollback.host_id,
                ),
            )
            .where(
                AIRollback.host_id == host_id,
                or_(AIRollback.session_id.is_(None), AIRollback.session_id != session_id),
                AIRollback.status.in_(_ACTIVE_ROLLBACK),
                AIRollback.started_at > first.started_at,
            )
            .order_by(AIRollback.started_at)
        )
    ).all()
    for rollback, taken_at in rows:
        # A snapshot LabDog has no record of is assumed to be older.
        if taken_at is None or taken_at < first.started_at:
            return rollback
    return None


async def check_rollback(
    db: AsyncSession,
    *,
    session: AISession,
    host: Any,
    ask: bool = True,
    exclude_rollback_id: int | None = None,
) -> AIToolCall:
    """Every rule that refuses rolling ``session`` back on ``host``.

    Returns the call whose snapshot a rollback would restore, or raises
    :class:`RollbackRefused`. One place for the endpoint, the worker and
    the button on the session page, so the button cannot be on for a
    rollback the endpoint refuses, or off for one it would allow.

    ``ask=False`` skips the live half of the LabDog-host test, for a page
    load; pressing the button asks. ``exclude_rollback_id`` is the
    worker's own ``running`` row. Reads the database only (and, with
    ``ask``, the host): Proxmox is asked when the rollback is carried out.
    """
    from app.ai.labdog_host import labdog_runs_on

    if session.status not in TERMINAL_SESSION_STATUSES:
        raise RollbackRefused(
            "The session is still running. Cancel it first: a rollback underneath it would "
            "leave it working on a host that has gone back in time."
        )
    existing_query = select(AIRollback).where(
        AIRollback.session_id == session.id,
        AIRollback.host_id == host.id,
        AIRollback.status.in_(_ACTIVE_ROLLBACK),
    )
    if exclude_rollback_id is not None:
        existing_query = existing_query.where(AIRollback.id != exclude_rollback_id)
    existing = (await db.execute(existing_query.limit(1))).scalar_one_or_none()
    if existing is not None:
        raise RollbackRefused(
            "This host is already being rolled back."
            if existing.status == "running"
            else f"This session's changes on this host were already rolled back, to "
            f"{existing.snapshot_name}, on {existing.started_at:%Y-%m-%d %H:%M} UTC."
        )

    first = await first_change(db, session.id, host.id)
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

    passed = await _passed_over(db, host.id, session.id, first)
    if passed is not None:
        raise RollbackRefused(
            f"{host.hostname} was rolled back to {passed.snapshot_name} on "
            f"{passed.started_at:%Y-%m-%d %H:%M} UTC, after this session took "
            f"{first.snapshot_name}. Restoring {first.snapshot_name} now would bring back "
            f"what that rollback undid."
        )

    if await labdog_runs_on(db, host, ask=ask):
        raise RollbackRefused(
            f"LabDog runs on {host.hostname}. Rolling it back would stop LabDog half-way, "
            f"with nothing left to start the machine again, and put LabDog's own database "
            f"back to the snapshot. Roll it back in Proxmox if you need to."
        )
    return first


async def plan_rollback(
    db: AsyncSession,
    *,
    session: AISession,
    host_id: int,
    exclude_rollback_id: int | None = None,
) -> RollbackPlan:
    """Resolve what a rollback of ``session`` on ``host_id`` would restore.

    Raises :class:`RollbackRefused` with a reason a person can act on.
    Changes nothing.
    """
    from app.ai.snapshots import SnapshotFailed, resolve_target
    from app.models.host import Host
    from app.ssh_utils import load_host_key

    host = await db.get(Host, host_id)
    if host is None:
        raise RollbackRefused("The host is no longer in LabDog.")

    first = await check_rollback(
        db, session=session, host=host, exclude_rollback_id=exclude_rollback_id
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

    The row is also the host's claim in the per-host queue. It is made
    under the host's advisory lock, after ``check_host_busy`` found no
    sync, action run or other rollback on the host, and from the caller's
    commit on it holds the host against them. The caller releases the
    queue (``release_host_queue``) once the rollback has ended.

    The partial unique index on ``ai_rollbacks`` stops two rollbacks of
    one session at once. The lookup in :func:`check_rollback` is only for
    a better message than the constraint's.
    """
    plan = await plan_rollback(db, session=session, host_id=host_id)

    busy = await host_busy(db, host_id)
    if busy:
        raise HostBusy(busy)

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


def _disk_storages(config: dict, vm_type: str) -> set[str]:
    """The storage ids a VM's or container's disks are on."""
    pattern = _LXC_DISK if vm_type == "lxc" else _QEMU_DISK
    storages: set[str] = set()
    for key, value in config.items():
        if not pattern.match(key) or not isinstance(value, str) or "media=cdrom" in value:
            continue
        volume = value.split(",", 1)[0]
        if ":" in volume:
            storages.add(volume.split(":", 1)[0])
    return storages


async def _newest_only(target: Any) -> bool:
    """Whether the machine's disks can only be restored to their newest snapshot.

    True when it cannot tell — the token may not be allowed to see the
    storage — because the cost of guessing wrong is lopsided: deleting the
    session's own later snapshots loses restore points that the rollback
    is about to make meaningless anyway, while a restore ZFS refuses fails
    the rollback.
    """
    try:
        config = await target.client.get_vm_config(
            target.pve_node, target.vmid, vm_type=target.vm_type
        )
        types = {
            entry.get("storage"): entry.get("type")
            for entry in await target.client.list_node_storage(target.pve_node)
        }
    except Exception as exc:
        logger.info("remediation: could not read the storage of %s: %s", target.vmid, exc)
        return True
    disks = _disk_storages(config or {}, target.vm_type)
    return not disks or any(types.get(d) in (None, *NEWEST_ONLY_STORAGE) for d in disks)


async def _clear_the_way(plan: RollbackPlan) -> list[AIToolCall]:
    """The session's later snapshots to delete before the restore, newest first.

    Raises :class:`RollbackRefused`, having deleted nothing, when the
    restore cannot happen: the snapshot is gone from Proxmox, or the disks
    are on storage that only restores the newest snapshot and a newer one
    is not this session's — another session's, an action run's, someone's
    own. On other storage nothing needs deleting.
    """
    target = plan.target
    name = plan.first.snapshot_name
    snapshots = [
        snap
        for snap in await target.client.list_snapshots(
            target.pve_node, target.vmid, vm_type=target.vm_type
        )
        if snap.get("name") != "current"
    ]
    taken = next((snap.get("snaptime") for snap in snapshots if snap.get("name") == name), False)
    if taken is False:
        raise RollbackRefused(
            f"{name} is no longer on {target.vm_type} {target.vmid} in Proxmox: it was removed "
            f"outside LabDog."
        )
    # Without a time to compare, a snapshot is counted as newer.
    newer = [
        snap
        for snap in snapshots
        if snap.get("name") != name
        and (taken is None or snap.get("snaptime") is None or snap["snaptime"] > taken)
    ]
    if not newer or not await _newest_only(target):
        return []

    ours = {call.snapshot_name: call for call in plan.later}
    theirs = sorted(snap["name"] for snap in newer if snap["name"] not in ours)
    if theirs:
        names = ", ".join(theirs)
        raise RollbackRefused(
            f"{plan.host.hostname}'s disks are on storage that can only restore the newest "
            f"snapshot, and {names} {'is' if len(theirs) == 1 else 'are'} newer than {name} "
            f"and not this session's. Nothing was deleted. Remove "
            f"{'it' if len(theirs) == 1 else 'them'} in Proxmox if no longer needed, then roll "
            f"back again."
        )
    newer.sort(key=lambda snap: snap.get("snaptime") or 0, reverse=True)
    return [ours[snap["name"]] for snap in newer]


async def perform_rollback(db: AsyncSession, rollback: AIRollback, plan: RollbackPlan) -> None:
    """Restore the snapshot, start the machine, and wait for SSH.

    Asks Proxmox first whether the restore can happen, and deletes the
    session's later snapshots only when it can and they are in the way.
    Never raises: the outcome, good or bad, is written to ``rollback``.
    """
    from app.audit.logger import log_action

    status, summary = await _restore(db, plan)
    if status == "failed":
        logger.warning("remediation: rollback %s failed: %s", rollback.id, summary)
    rollback.status = status
    rollback.detail = summary[:4000]
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
            "vmid": plan.target.vmid,
        },
    )


async def _restore(db: AsyncSession, plan: RollbackPlan) -> tuple[str, str]:
    """Carry out the rollback: ``(status, what happened)``."""
    from app.workflows.steps import rollback as rollback_step
    from app.workflows.steps.cleanup import delete_snapshot

    target = plan.target
    name = plan.first.snapshot_name
    try:
        in_the_way = await _clear_the_way(plan)
    except RollbackRefused as exc:
        return "refused", str(exc)
    except Exception as exc:
        return "failed", (
            f"Could not read the snapshots of {target.vm_type} {target.vmid} from Proxmox "
            f"({_say(exc)}). Nothing was changed."
        )

    deleted: list[str] = []
    for call in in_the_way:
        try:
            await delete_snapshot(
                target.client,
                target.pve_node,
                target.vmid,
                call.snapshot_name,
                vm_type=target.vm_type,
            )
        except Exception as exc:
            done = f" Deleted before that: {', '.join(deleted)}." if deleted else ""
            return "failed", (
                f"Could not delete the later snapshot {call.snapshot_name} ({_say(exc)}), "
                f"which is in the way of {name}, so nothing was restored.{done}"
            )
        call.snapshot_pruned_at = datetime.now(UTC)
        deleted.append(call.snapshot_name)

    try:
        result = await rollback_step.rollback_to_snapshot(
            target.client,
            target.pve_node,
            target.vmid,
            name,
            plan.host,
            plan.key,
            db,
            vm_type=target.vm_type,
        )
    except Exception as exc:
        logger.exception("remediation: restoring %s raised", name)
        result = {"success": False, "error": _say(exc)}

    cleared = f" Deleted the later snapshots {', '.join(deleted)} first." if deleted else ""
    if result.get("success"):
        return "succeeded", (
            f"Restored {target.vm_type} {target.vmid} to {name} and started it; SSH answered "
            f"again. The host is marked out of sync.{cleared}"
        )
    return "failed", f"Rolling back to {name} failed: {result.get('error')}.{cleared}"


async def roll_back_automatically(
    db: AsyncSession, event: AlertEvent, session: AISession, *, busy: str | None = None
) -> AIRollback:
    """The check's rollback, after a fix made the host worse.

    ``busy`` is what :func:`judge` found still holding the host when its
    wait ran out. Commits the ``running`` row before Proxmox is touched,
    so the page shows it while it happens; the caller commits the result
    and releases the host's queue.
    """

    def refused(reason: str):
        return record_refused(
            db,
            session=session,
            host_id=event.host_id,
            trigger="automatic",
            reason=reason,
            alert_event_id=event.id,
        )

    if not await auto_rollback_on(db):
        return await refused("Automatic rollback is off (ai.alert_auto_rollback).")
    if busy:
        minutes = BUSY_WAIT_SECONDS // 60
        return await refused(f"{busy}, and still was after {minutes} minutes.")

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
