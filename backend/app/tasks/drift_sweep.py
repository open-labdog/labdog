"""Locked driver shared by the seven periodic drift sweeps.

Each module — firewall, cron, hosts_file, package, resolver, service,
linux_user — registers a RedBeat entry that walks every host with drift
checking enabled for that module, collects the host's live state over
SSH, diffs it against the merged desired state, and writes the verdict
to ``HostModuleStatus.sync_status``.

Before BUG-67 all seven were inline loops that took no host lock at
all, and two things followed:

* A sweep that landed while a sync was applying that host's config read
  a half-applied state and wrote ``out_of_sync`` over the status the
  sync was maintaining. The host then showed as drifted immediately
  after a successful sync, with nothing to distinguish the stale
  verdict from a real one.
* Six of the seven held a single transaction open across the whole
  sweep and committed once at the end, so a worker restart mid-sweep
  discarded every host's result, and one host's failed statement left
  the transaction unusable for every host after it.

:func:`sweep_module` fixes both. Per host it:

1. Takes the advisory lock and asks ``check_host_busy``. A host claimed
   by a sync or an action run is skipped for this tick — a drift check
   is periodic and read-only, so skipping costs one interval, and the
   op holding the host leaves the status correct on its way out.
2. Drops the lock (nothing has been written yet) and runs the module's
   collect-and-diff, which writes into this session but does not commit.
3. Re-takes the lock and re-checks. If an op claimed the host while the
   SSH work was in flight, the per-host transaction is rolled back and
   the verdict discarded rather than written over the running op's
   status. Otherwise it commits — with the lock still held, so no claim
   can slip in between the check and the write.

Step 3 takes the lock with ``pg_try_advisory_xact_lock`` rather than
waiting for it. By that point the transaction holds row locks from the
collect-and-diff writes, so blocking on a lock another claim already
owns would be a lock-ordering hazard; and failing to get it instantly
means a claim is in progress, which is exactly the case this discards.

One narrow hole remains: ``ssh_connect_host`` commits the session
itself when TOFU records a host key, which makes that tick's writes
durable before step 3 can discard them. It fires only on the first
successful connection to a host LabDog has never reached, where there
is no established status to clobber.

Concurrency is unchanged — the sweep is still one serial pass. Since
BUG-66 gave every remote command a deadline, a hung host costs the
sweep one ``ssh.command_timeout`` rather than stopping it dead, which
is what took the second half of BUG-67 off the critical list.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from app.models.host import Host
    from app.models.host_module_status import HostModuleStatus

logger = logging.getLogger(__name__)


#: What each module contributes to the sweep: collect the host's live
#: state, diff it, and write the verdict onto ``hms`` / ``host`` in the
#: session it is handed. It must not commit — the driver owns the
#: transaction, and discarding the verdict is how a mid-check claim is
#: handled. ``hms`` is ``None`` only when no ``HostModuleStatus`` row
#: exists yet, which the firewall module creates on the fly.
#:
#: Returns ``True`` when a check actually ran and ``False`` when the
#: host was skipped for a module-specific reason (no SSH key, no
#: effective config, firewall backend unknown). Recoverable failures —
#: an unreachable host — are recorded on ``hms`` by the module itself
#: and still count as having run.
CheckOne = Callable[
    ["Host", "HostModuleStatus | None", "AsyncSession"],
    Awaitable[bool],
]


async def _blocker_reason(db: AsyncSession, host_id: int, *, wait: bool) -> str | None:
    """Human-readable reason ``host_id`` is claimed, or None if it is free.

    Leaves the advisory lock held on the caller's transaction when it
    returns ``None``, so the caller can act on the answer atomically.

    With ``wait=False`` the lock is taken non-blockingly and a lock we
    could not get is reported as a blocker in its own right: someone is
    between ``acquire_host_lock`` and their claim's commit, and from the
    sweep's point of view that host is spoken for.
    """
    from app.tasks.host_lock import (
        acquire_host_lock,
        check_host_busy,
        format_pending_reason,
        try_acquire_host_lock,
    )

    if wait:
        await acquire_host_lock(db, host_id)
    elif not await try_acquire_host_lock(db, host_id):
        return "another operation is claiming the host"

    blocker = await check_host_busy(db, host_id)
    if blocker is None:
        return None
    return await format_pending_reason(db, blocker)


async def sweep_module(
    module_type: str,
    check_one: CheckOne,
    *,
    host_gated: bool = False,
) -> dict[str, int]:
    """Run *check_one* against every host with *module_type* drift enabled.

    Args:
        module_type: The ``HostModuleStatus.module_type`` this sweep
            owns. Used both to select candidates and to load the row
            handed to *check_one*.
        check_one: The module's collect-and-diff body. See :data:`CheckOne`.
        host_gated: Select candidates from ``Host.drift_check_enabled``
            rather than from ``HostModuleStatus.drift_check_enabled``.
            The firewall module predates per-module toggles and still
            keys off the host-level flag; the other six do not.

    Returns:
        Counters for the tick: ``checked`` (a verdict was written),
        ``skipped`` (the module declined the host, or it was deleted
        mid-sweep), ``deferred`` (the host was claimed, before or
        during the check) and ``failed`` (the module raised).
    """
    from sqlalchemy import select

    from app.db import task_session
    from app.models.host import Host
    from app.models.host_module_status import HostModuleStatus

    counters = {"checked": 0, "skipped": 0, "deferred": 0, "failed": 0}

    async with task_session() as db:
        if host_gated:
            candidates = select(Host.id).where(Host.drift_check_enabled).order_by(Host.id)
        else:
            candidates = (
                select(HostModuleStatus.host_id)
                .where(
                    HostModuleStatus.module_type == module_type,
                    HostModuleStatus.drift_check_enabled,
                )
                .order_by(HostModuleStatus.host_id)
            )
        host_ids = list((await db.execute(candidates)).scalars().all())
        # Close the read snapshot the candidate scan opened so the first
        # per-host transaction starts clean.
        await db.rollback()

        for host_id in host_ids:
            reason = await _blocker_reason(db, host_id, wait=True)
            if reason is not None:
                await db.rollback()
                counters["deferred"] += 1
                logger.info(
                    "drift sweep (%s): skipping host %d this tick — %s",
                    module_type,
                    host_id,
                    reason,
                )
                continue
            # Release the lock before the SSH work. A drift check takes
            # as long as the host takes to answer and nothing has been
            # written yet, so there is nothing to protect until step 3.
            await db.rollback()

            try:
                host = (
                    await db.execute(select(Host).where(Host.id == host_id))
                ).scalar_one_or_none()
                if host is None:
                    # Deleted between the candidate scan and now.
                    await db.rollback()
                    counters["skipped"] += 1
                    continue
                hms = (
                    await db.execute(
                        select(HostModuleStatus).where(
                            HostModuleStatus.host_id == host_id,
                            HostModuleStatus.module_type == module_type,
                        )
                    )
                ).scalar_one_or_none()
                ran = await check_one(host, hms, db)
            except Exception:
                # Each module records its own expected failures on the
                # status row; reaching here means something the module
                # did not anticipate, and losing it silently is what
                # made these sweeps so hard to diagnose.
                logger.exception("drift sweep (%s): check raised for host %d", module_type, host_id)
                await db.rollback()
                counters["failed"] += 1
                continue

            reason = await _blocker_reason(db, host_id, wait=False)
            if reason is not None:
                await db.rollback()
                counters["deferred"] += 1
                logger.info(
                    "drift sweep (%s): discarding verdict for host %d — claimed mid-check (%s)",
                    module_type,
                    host_id,
                    reason,
                )
                continue

            await db.commit()
            counters["checked" if ran else "skipped"] += 1

    return counters
