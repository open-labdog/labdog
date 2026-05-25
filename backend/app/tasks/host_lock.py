"""Per-host serialization helpers shared by sync and action paths.

A single host can have many operations submitted against it (syncs,
host-targeted actions, group-targeted actions whose member set includes
this host, plus the built-in drift_check / collect_state which route
through the action path). We want only one of those operations writing
state at a time per host — otherwise apt/dpkg races, nftables flush
races, and stale-read pathologies for drift / collect_state.

The pattern (lifted from the existing sync orchestrator and extended
to cover actions):

1. The Celery task starts. It opens a short transaction, takes a
   transaction-level Postgres advisory lock keyed on host_id, and
   checks `check_host_busy(...)` — which walks SyncJob AND ActionRun /
   ActionHostRun looking for rows in `running` state for this host.
2. If busy: the caller marks its own row as `pending`, commits (which
   releases the advisory lock), and returns. The Celery task is done.
3. If free: the caller marks its own row as `running`, commits, and
   proceeds with the actual operation. The advisory lock is released
   by the commit; the "host is busy" state now lives in the row's
   `status` column.
4. When the operation finishes (success or failure), in its `finally`
   block, the caller persists the final status (separate transaction)
   and then calls `dispatch_next_pending_for_host(...)`. That picks
   the oldest pending row for this host (across SyncJob AND ActionRun)
   and re-dispatches it via the appropriate Celery task. The picked
   row's task will go through its own claim-or-defer when it runs.

Group-targeted action runs (the `supports_host: false` case) target N
hosts in a single ansible-playbook invocation. Their claim-or-defer
covers ALL members: acquire advisory locks for every member host (in
sorted order — see `acquire_host_locks` for the deadlock-avoidance
rationale), check all members at once via `check_hosts_busy`. If any
member is busy, defer the whole run. If all are free, claim ALL members
by transitioning the per-member ActionHostRun rows to `running` before
committing. On finish, call `dispatch_next_pending_for_host` for every
member (each member can now unblock a different queued operation).

Self-exclusion in the busy check is NOT needed for sync and group-action
callers — their own rows are still in `queued`/`pending` state at the
time of the check. However, host-targeted action runs are a special case:
the orchestrator flips the parent ActionRun to `running` before dispatching
the per-host tasks, so `check_host_busy` would find the parent run and
incorrectly treat it as a blocker. Pass `exclude_action_run_id` from
`action_host` to avoid this false self-block.

This module is callable from any Celery task module. It does not own
any state of its own. Its only inputs are an AsyncSession (already
inside a transaction whose advisory lock has been acquired), host ids,
and exclude keys for the dispatch step (which runs outside the
acquiring transaction).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Literal

from sqlalchemy import or_, select, text
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Public types
# ---------------------------------------------------------------------------


BlockerKind = Literal["sync", "action_host", "action_group"]


@dataclass(frozen=True, slots=True)
class BlockerInfo:
    """Identity of the running op that prevents a host from being claimed.

    Returned by ``check_host_busy`` / ``check_hosts_busy`` so callers can
    build a human-readable ``pending_reason`` string (e.g. "Waiting for
    sync 47 on host node-1") and write it to the deferred row.

    ``kind`` reflects which queue the blocker came from:

    * ``sync``         — ``SyncJob`` is running on this host.
    * ``action_host``  — host-targeted ``ActionRun`` is running on this host.
    * ``action_group`` — group-targeted ``ActionRun`` is running and this
      host is one of its members.

    ``id`` is the primary key of the blocking row (``SyncJob.id`` or
    ``ActionRun.id``). ``host_id`` is the host being blocked (matters
    for group dispatch — names the specific busy member, not the run's
    nominal target). ``action_key`` is the action key for the two
    ``action_*`` kinds; ``None`` for ``sync``.
    """

    kind: BlockerKind
    id: int
    host_id: int
    action_key: str | None = None


# ---------------------------------------------------------------------------
# Advisory lock acquisition
# ---------------------------------------------------------------------------


async def acquire_host_lock(db: AsyncSession, host_id: int) -> None:
    """Take a transaction-level advisory lock on ``host_id``.

    The lock auto-releases when the surrounding transaction commits
    or rolls back. Use this to serialize a check-and-flip on the
    host: between `acquire_host_lock` and commit, no other transaction
    can hold the same key, so the "is host X busy?" question and the
    "claim host X" update happen atomically.

    Different ``host_id`` values map to independent lock keys, so
    operations on unrelated hosts never contend.

    Single-host callers (sync, host-targeted action) call this.
    Group-targeted actions call `acquire_host_locks` instead — never
    loop over `acquire_host_lock` with un-sorted ids, that's how
    you build deadlocks.

    Args:
        db: An open async session inside a transaction. The lock will
            release when the transaction commits/rolls back.
        host_id: Host id to lock on. Used directly as the advisory key.
    """
    await db.execute(text("SELECT pg_advisory_xact_lock(:h)"), {"h": host_id})


async def acquire_host_locks(db: AsyncSession, host_ids: list[int]) -> None:
    """Take advisory locks on multiple hosts in deterministic order.

    Used by group-targeted action runs that need to claim every member
    atomically. Acquires locks in ascending host_id order so that two
    overlapping group actions can never deadlock on each other (the
    classic "A holds X waits Y; B holds Y waits X" pattern is
    impossible when both always lock in the same order).

    Empty list is a no-op. Duplicate ids are de-duped.

    Args:
        db: An open async session inside a transaction. All locks
            release when the transaction commits/rolls back.
        host_ids: Host ids to lock on. Will be sorted ascending and
            de-duped before locking.
    """
    if not host_ids:
        return
    # Dedup + sort ascending. PG advisory locks are reentrant per-session,
    # so even if a caller passes duplicates dedup is just an optimisation;
    # the sort is the part that matters for deadlock avoidance.
    ordered = sorted(set(host_ids))
    for hid in ordered:
        await db.execute(text("SELECT pg_advisory_xact_lock(:h)"), {"h": hid})


# ---------------------------------------------------------------------------
# Busy check
# ---------------------------------------------------------------------------


async def check_host_busy(
    db: AsyncSession,
    host_id: int,
    *,
    exclude_action_run_id: int | None = None,
) -> BlockerInfo | None:
    """First blocker on ``host_id``, or None if the host is free.

    Walks three sources, returns at the first hit:

    1. ``SyncJob`` with ``status='running'`` and ``host_id=X``
       (the existing sync queue).
    2. ``ActionRun`` with ``status='running'`` and ``host_id=X``
       (host-targeted action runs).
    3. ``ActionHostRun`` with ``status='running'`` and ``host_id=X``
       belonging to an ActionRun with ``status='running'``
       (group-targeted action runs whose member set includes X).

    Must be called inside a transaction that already holds the
    advisory lock for ``host_id`` (via `acquire_host_lock`). Without
    the lock, two callers can both see "not busy" and both proceed
    to claim — the race the lock exists to close.

    ``exclude_action_run_id`` must be supplied by ``action_host`` tasks.
    The orchestrator marks the parent ActionRun ``running`` before
    dispatching per-host tasks, so without the exclusion the per-host
    task would find its own parent run in the running-rows scan and
    incorrectly defer itself as if the host were busy.

    Args:
        db: An open async session inside the acquiring transaction.
        host_id: Host id to check.
        exclude_action_run_id: ActionRun id to skip in the host-targeted
            scan (pass the parent action_run_id from action_host tasks).

    Returns:
        A :class:`BlockerInfo` describing the running op holding the
        host, or ``None`` when the host is free. Callers should
        ``is not None`` truthy-check (the return is a frozen dataclass,
        not a bool).
    """
    from app.models.action_run import ActionHostRun, ActionRun
    from app.models.sync_job import JobStatus, SyncJob

    # 1. SyncJob running on this host.
    sync_hit = (
        await db.execute(
            select(SyncJob.id)
            .where(SyncJob.host_id == host_id, SyncJob.status == JobStatus.running)
            .limit(1)
        )
    ).scalar_one_or_none()
    if sync_hit is not None:
        return BlockerInfo(kind="sync", id=sync_hit, host_id=host_id, action_key=None)

    # 2. Host-targeted ActionRun running on this host.
    host_action_stmt = select(ActionRun.id, ActionRun.action_key).where(
        ActionRun.host_id == host_id, ActionRun.status == "running"
    )
    if exclude_action_run_id is not None:
        host_action_stmt = host_action_stmt.where(ActionRun.id != exclude_action_run_id)
    host_action_hit = (await db.execute(host_action_stmt.limit(1))).first()
    if host_action_hit is not None:
        return BlockerInfo(
            kind="action_host",
            id=host_action_hit.id,
            host_id=host_id,
            action_key=host_action_hit.action_key,
        )

    # 3. Group-targeted ActionHostRun running for this host (parent ActionRun
    # must also be running — finished runs may leave per-host rows around
    # but the run itself is no longer holding the host).
    group_action_hit = (
        await db.execute(
            select(ActionRun.id, ActionRun.action_key)
            .join(ActionHostRun, ActionRun.id == ActionHostRun.action_run_id)
            .where(
                ActionHostRun.host_id == host_id,
                ActionHostRun.status == "running",
                ActionRun.status == "running",
            )
            .limit(1)
        )
    ).first()
    if group_action_hit is not None:
        return BlockerInfo(
            kind="action_group",
            id=group_action_hit.id,
            host_id=host_id,
            action_key=group_action_hit.action_key,
        )
    return None


async def check_hosts_busy(db: AsyncSession, host_ids: list[int]) -> BlockerInfo | None:
    """First-blocker (by sorted host id) across a set of hosts, or None.

    Group-action variant of `check_host_busy`. Used after
    `acquire_host_locks` to check every member of a group target at
    once. Returns a :class:`BlockerInfo` for the smallest busy host id
    so the caller can include a useful diagnostic in the per-run notice
    ("Waiting for sync 7 on host node-1").

    Equivalent to scanning each host via `check_host_busy` but
    expressed as three queries instead of ``3N``.

    Args:
        db: An open async session inside a transaction holding locks
            on all of ``host_ids`` (via `acquire_host_locks`).
        host_ids: Host ids to check. Order doesn't matter for
            correctness, but the helper sorts before scanning so the
            returned "first busy" is deterministic.

    Returns:
        A :class:`BlockerInfo` for the smallest busy host id (so the
        caller's diagnostic is deterministic across runs), or ``None``
        when all hosts in the input are free.
    """
    if not host_ids:
        return None

    from app.models.action_run import ActionHostRun, ActionRun
    from app.models.sync_job import JobStatus, SyncJob

    ordered = sorted(set(host_ids))

    # host_id → (kind, id, action_key) for the FIRST hit per host.
    # We accept any one of the three sources hitting first (the lock
    # invariant means a host has at most one running op anyway).
    blockers: dict[int, BlockerInfo] = {}

    sync_rows = (
        await db.execute(
            select(SyncJob.id, SyncJob.host_id).where(
                SyncJob.host_id.in_(ordered), SyncJob.status == JobStatus.running
            )
        )
    ).all()
    for row in sync_rows:
        blockers.setdefault(
            row.host_id,
            BlockerInfo(kind="sync", id=row.id, host_id=row.host_id, action_key=None),
        )

    host_action_rows = (
        await db.execute(
            select(ActionRun.id, ActionRun.host_id, ActionRun.action_key).where(
                ActionRun.host_id.in_(ordered), ActionRun.status == "running"
            )
        )
    ).all()
    for row in host_action_rows:
        if row.host_id is None:
            continue
        blockers.setdefault(
            row.host_id,
            BlockerInfo(
                kind="action_host",
                id=row.id,
                host_id=row.host_id,
                action_key=row.action_key,
            ),
        )

    group_action_rows = (
        await db.execute(
            select(ActionRun.id, ActionHostRun.host_id, ActionRun.action_key)
            .join(ActionHostRun, ActionRun.id == ActionHostRun.action_run_id)
            .where(
                ActionHostRun.host_id.in_(ordered),
                ActionHostRun.status == "running",
                ActionRun.status == "running",
            )
        )
    ).all()
    for row in group_action_rows:
        blockers.setdefault(
            row.host_id,
            BlockerInfo(
                kind="action_group",
                id=row.id,
                host_id=row.host_id,
                action_key=row.action_key,
            ),
        )

    if not blockers:
        return None
    first_host = min(blockers.keys())
    return blockers[first_host]


# ---------------------------------------------------------------------------
# pending_reason formatting
# ---------------------------------------------------------------------------


async def format_pending_reason(db: AsyncSession, blocker: BlockerInfo) -> str:
    """Return a short human-readable diagnostic for a deferred row.

    Format mirrors the shape the frontend renders in the amber "Host
    busy" badge tooltip and the run-detail banner:

    * sync         — ``"Waiting for sync 47 on host node-1"``
    * action_host  — ``"Waiting for action_host 12 on host node-1 (k8s-upgrade)"``
    * action_group — ``"Waiting for action_group 12 on host node-1 (k8s-upgrade)"``

    The hostname is resolved from ``Host.id``. If the host has been
    deleted (FK ON DELETE) the bare host id is used — the diagnostic is
    cosmetic, not load-bearing. The string is capped at 255 chars to
    match the column width on ``ActionRun.pending_reason``.

    Args:
        db: An open async session (any read-capable transaction).
        blocker: The :class:`BlockerInfo` returned by ``check_host_busy``
            or ``check_hosts_busy``.

    Returns:
        A diagnostic string suitable for writing to
        ``ActionRun.pending_reason`` and ``ActionHostRun.pending_reason``.
    """
    from app.models.host import Host

    hostname = (
        await db.execute(select(Host.hostname).where(Host.id == blocker.host_id))
    ).scalar_one_or_none()
    host_display = hostname or f"host:{blocker.host_id}"
    suffix = f" ({blocker.action_key})" if blocker.action_key else ""
    reason = f"Waiting for {blocker.kind} {blocker.id} on host {host_display}{suffix}"
    if len(reason) > 255:
        reason = reason[:252] + "..."
    return reason


# ---------------------------------------------------------------------------
# Dispatch the next pending operation
# ---------------------------------------------------------------------------


DispatchedKind = Literal["sync", "action_host", "action_group"]
"""What the dispatch picked, returned for log surfaces.

`action_host`: an ActionRun whose `host_id` matches the freed host.
`action_group`: an ActionRun whose member set includes the freed host
    (the dispatched task will re-check all its members and may defer
    again if another member is still busy).
"""


async def dispatch_next_pending_for_host(
    db: AsyncSession,
    host_id: int,
    *,
    exclude_sync_job_id: int | None = None,
    exclude_action_run_id: int | None = None,
) -> tuple[DispatchedKind, int] | None:
    """Pick the oldest pending op on ``host_id`` and re-dispatch it.

    Called in the `finally` block of a just-finished operation, after
    that operation's row has been persisted with its final
    `status` (so it doesn't show up as `running` in the scan). The
    exclude_* keys are belt-and-braces in case the caller's commit
    isn't visible yet in this transaction's snapshot.

    Considers BOTH queues, ordered by `created_at` ascending across the
    union — FIFO fairness between syncs and actions for the same host.

    For each candidate:

    - SyncJob → dispatches via ``run_host_sync.delay(...)`` (same
      task name the orchestrator already uses).
    - host-targeted ActionRun → dispatches via
      ``app.tasks.action_orchestrator.run_action.delay(...)``.
    - group-targeted ActionRun → dispatches via
      ``app.tasks.action_orchestrator.run_action.delay(...)`` (which
      routes to action_group based on the action's `supports_host`
      flag — same routing as the original submission).

    The dispatched task does its own claim-or-defer. A group action
    picked here may re-defer if any of its OTHER members is still
    busy; that's fine, it'll get another dispatch when that other
    member frees up. No starvation as long as ordering is FIFO.

    Defensive: if the candidate's host has been deleted, or the
    pending row's parent record is in an invalid state, the helper
    skips that row, marks it failed, and tries the next one.

    Args:
        db: An open async session. The helper opens its own short
            transaction with `acquire_host_lock(host_id)` to serialize
            the pick (so two concurrent finishers don't both dispatch
            the same pending row).
        host_id: The host that just freed up.
        exclude_sync_job_id: Sync job to exclude from the scan
            (typically the just-finished one, in case its commit isn't
            yet visible).
        exclude_action_run_id: Action run to exclude from the scan
            (typically the just-finished one).

    Returns:
        A tuple ``(kind, row_id)`` describing what was dispatched, or
        None if no pending work exists for this host.
    """
    from app.models.action_run import ActionHostRun, ActionRun
    from app.models.host import Host, HostGroupMembership
    from app.models.sync_job import JobStatus, SyncJob

    # Serialize the pick across concurrent finishers on the same host.
    await acquire_host_lock(db, host_id)

    # We iterate the FIFO queue and skip defensively-bad rows. Each
    # iteration is a fresh ordered query: when we mark a bad row failed
    # and re-query, the just-failed row drops out of the candidate set.
    while True:
        # ------------------------------------------------------------------
        # SyncJob candidate: pending + host_id matches + not excluded.
        # ------------------------------------------------------------------
        sync_stmt = (
            select(SyncJob)
            .where(SyncJob.host_id == host_id, SyncJob.status == JobStatus.pending)
            .order_by(SyncJob.created_at.asc())
            .limit(1)
        )
        if exclude_sync_job_id is not None:
            sync_stmt = sync_stmt.where(SyncJob.id != exclude_sync_job_id)
        sync_candidate = (await db.execute(sync_stmt)).scalar_one_or_none()

        # ------------------------------------------------------------------
        # ActionRun candidate: pending + (host_id matches OR group target
        # whose member set includes this host) + not excluded.
        # ------------------------------------------------------------------
        group_member_subq = (
            select(HostGroupMembership.c.group_id)
            .where(HostGroupMembership.c.host_id == host_id)
            .scalar_subquery()
        )
        action_stmt = (
            select(ActionRun)
            .where(
                ActionRun.status == "pending",
                or_(
                    ActionRun.host_id == host_id,
                    ActionRun.group_id.in_(group_member_subq),
                ),
            )
            .order_by(ActionRun.created_at.asc())
            .limit(1)
        )
        if exclude_action_run_id is not None:
            action_stmt = action_stmt.where(ActionRun.id != exclude_action_run_id)
        action_candidate = (await db.execute(action_stmt)).scalar_one_or_none()

        # Pick the older of the two by created_at — FIFO fairness across
        # queues. Either may be None.
        candidate_kind: str | None = None
        candidate_row = None
        if sync_candidate is not None and action_candidate is not None:
            if sync_candidate.created_at <= action_candidate.created_at:
                candidate_kind, candidate_row = "sync", sync_candidate
            else:
                candidate_kind, candidate_row = "action", action_candidate
        elif sync_candidate is not None:
            candidate_kind, candidate_row = "sync", sync_candidate
        elif action_candidate is not None:
            candidate_kind, candidate_row = "action", action_candidate

        if candidate_row is None:
            return None

        if candidate_kind == "sync":
            sync_row: SyncJob = candidate_row  # type: ignore[assignment]
            # Defensive: host gone? Mark failed and try again.
            host_exists = (
                await db.execute(select(Host.id).where(Host.id == sync_row.host_id).limit(1))
            ).scalar_one_or_none()
            if host_exists is None:
                logger.warning(
                    "dispatch_next_pending_for_host: SyncJob %s host %s missing — failing row",
                    sync_row.id,
                    sync_row.host_id,
                )
                sync_row.status = JobStatus.failed
                sync_row.error_message = "host no longer exists"
                await db.flush()
                continue
            # Dispatch via the existing run_host_sync task. Import lazily
            # to avoid a circular import at module load.
            from app.tasks.host_sync_orchestrator import (
                _filter_from_module_type,
                run_host_sync,
            )

            module_filter = _filter_from_module_type(sync_row.module_type)
            run_host_sync.delay(
                job_id=sync_row.id,
                host_id=sync_row.host_id,
                module_filter=module_filter,
            )
            return ("sync", sync_row.id)

        # candidate_kind == "action"
        action_row: ActionRun = candidate_row  # type: ignore[assignment]
        # Defensive: classify host vs group dispatch shape.
        if action_row.host_id is not None:
            host_exists = (
                await db.execute(select(Host.id).where(Host.id == action_row.host_id).limit(1))
            ).scalar_one_or_none()
            if host_exists is None:
                logger.warning(
                    "dispatch_next_pending_for_host: ActionRun %s host %s missing — failing row",
                    action_row.id,
                    action_row.host_id,
                )
                action_row.status = "failed"
                action_row.error_message = "host no longer exists"
                # Cascade per-host rows too.
                hrs = (
                    (
                        await db.execute(
                            select(ActionHostRun).where(
                                ActionHostRun.action_run_id == action_row.id,
                                ActionHostRun.status.in_(["queued", "pending", "running"]),
                            )
                        )
                    )
                    .scalars()
                    .all()
                )
                for hr in hrs:
                    hr.status = "failed"
                    hr.error_message = "host no longer exists"
                await db.flush()
                continue

            from app.tasks.action_orchestrator import run_action

            run_action.delay(action_row.id)
            return ("action_host", action_row.id)

        # group target: nothing to verify on host existence here (the
        # group task does its own member resolution + claim-or-defer).
        from app.tasks.action_orchestrator import run_action

        run_action.delay(action_row.id)
        return ("action_group", action_row.id)
