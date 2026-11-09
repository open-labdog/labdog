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

Fan-out. The periodic task no longer walks the hosts itself: it picks
the candidates and enqueues one :func:`check_host_module` task per host
on the ``drift`` queue (:func:`fan_out_module`), and returns without
waiting for them — waiting on children from inside a worker slot is the
trap the orchestrator queue split exists to avoid. Each per-host task
runs exactly the lock / check / re-check / commit sequence above
(:func:`check_host`), so BUG-67's guarantees are per host as before. A
sweep at 300 hosts used to hold one slot for the whole pass (about 400 s
for the package module); now it spreads over every ``drift`` slot. A
Redis ``SET NX`` per module and host keeps a slow tick from piling up a
second check of a host whose first is still queued or running.

:func:`sweep_module` keeps the old serial pass over :func:`check_host`
for callers that want the counters in one place.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from app.models.host import Host
    from app.models.host_module_status import HostModuleStatus

from app.tasks import celery_app

logger = logging.getLogger(__name__)


#: The per-host check each module contributes, by ``module_type``, as
#: ``"module:function"`` so this module does not import all seven (and
#: they can keep importing this one).
CHECKS: dict[str, str] = {
    "firewall": "app.tasks.drift:_sweep_one",
    "service": "app.tasks.service_drift:check_service_drift_for_one_host",
    "hosts_file": "app.tasks.hosts_drift:check_hosts_drift_for_one_host",
    "linux_user": "app.tasks.user_drift:check_user_drift_for_one_host",
    "cron": "app.tasks.cron_drift:check_cron_drift_for_one_host",
    "package": "app.tasks.package_drift:check_package_drift_for_one_host",
    "resolver": "app.tasks.resolver_drift:check_resolver_drift_for_one_host",
}

#: Modules that select candidates from ``Host.drift_check_enabled``.
HOST_GATED: frozenset[str] = frozenset({"firewall"})


def _resolve_check(module_type: str) -> CheckOne:
    import importlib  # noqa: PLC0415

    module_name, func = CHECKS[module_type].split(":")
    return getattr(importlib.import_module(module_name), func)


def _inflight_key(module_type: str, host_id: int) -> str:
    return f"labdog:drift:inflight:{module_type}:{host_id}"


def _inflight_ttl_seconds() -> int:
    """How long a queued check holds its host's slot if it never finishes.

    Just under the interval, so a check lost to a worker crash frees the
    host before the next tick wants it, while a merely slow one is not
    enqueued twice in the same tick.
    """
    from app.config import settings  # noqa: PLC0415

    return max(60, settings.drift.check_interval_minutes * 60 - 30)


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
    from app.db import task_session

    counters = {"checked": 0, "skipped": 0, "deferred": 0, "failed": 0}

    async with task_session() as db:
        host_ids = await _candidates(db, module_type, host_gated)

        for host_id in host_ids:
            outcome = await check_host(db, module_type, check_one, host_id)
            counters[outcome] += 1

    return counters


async def check_host(
    db: AsyncSession,
    module_type: str,
    check_one: CheckOne,
    host_id: int,
) -> str:
    """Lock, check, re-check and commit one host (the BUG-67 sequence).

    Returns which counter the host lands in: ``checked``, ``skipped``,
    ``deferred`` or ``failed``. *db* must have no transaction open; it
    has none open on return either.
    """
    from sqlalchemy import select  # noqa: PLC0415

    from app.models.host import Host  # noqa: PLC0415
    from app.models.host_module_status import HostModuleStatus  # noqa: PLC0415

    reason = await _blocker_reason(db, host_id, wait=True)
    if reason is not None:
        await db.rollback()
        logger.info(
            "drift sweep (%s): skipping host %d this tick — %s",
            module_type,
            host_id,
            reason,
        )
        return "deferred"
    # Release the lock before the SSH work. A drift check takes as long as
    # the host takes to answer and nothing has been written yet, so there
    # is nothing to protect until the re-check.
    await db.rollback()

    try:
        host = (await db.execute(select(Host).where(Host.id == host_id))).scalar_one_or_none()
        if host is None:
            # Deleted between the candidate scan and now.
            await db.rollback()
            return "skipped"
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
        # Each module records its own expected failures on the status row;
        # reaching here means something the module did not anticipate, and
        # losing it silently is what made these sweeps so hard to diagnose.
        logger.exception("drift sweep (%s): check raised for host %d", module_type, host_id)
        await db.rollback()
        return "failed"

    reason = await _blocker_reason(db, host_id, wait=False)
    if reason is not None:
        await db.rollback()
        logger.info(
            "drift sweep (%s): discarding verdict for host %d — claimed mid-check (%s)",
            module_type,
            host_id,
            reason,
        )
        return "deferred"

    await db.commit()
    return "checked" if ran else "skipped"


async def _candidates(db: AsyncSession, module_type: str, host_gated: bool) -> list[int]:
    from sqlalchemy import select  # noqa: PLC0415

    from app.models.host import Host  # noqa: PLC0415
    from app.models.host_module_status import HostModuleStatus  # noqa: PLC0415

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
    await db.rollback()
    return host_ids


async def fan_out_module(module_type: str, *, redis_client=None) -> dict[str, int]:
    """Enqueue one :func:`check_host_module` per candidate host; don't wait.

    Returns ``enqueued`` and ``in_flight`` (a check of that host for this
    module was still queued or running, so none was added).
    """
    from app.db import task_session  # noqa: PLC0415

    if redis_client is None:
        import redis as redis_lib  # noqa: PLC0415

        from app.config import settings  # noqa: PLC0415

        redis_client = redis_lib.from_url(settings.redis.url)

    async with task_session() as db:
        host_ids = await _candidates(db, module_type, module_type in HOST_GATED)

    ttl = _inflight_ttl_seconds()
    counters = {"enqueued": 0, "in_flight": 0}
    for host_id in host_ids:
        if not redis_client.set(_inflight_key(module_type, host_id), "1", nx=True, ex=ttl):
            counters["in_flight"] += 1
            continue
        check_host_module.apply_async(args=[module_type, host_id])
        counters["enqueued"] += 1
    if counters["in_flight"]:
        logger.warning(
            "drift sweep (%s): %d host(s) still had a check queued or running; not re-enqueued",
            module_type,
            counters["in_flight"],
        )
    return counters


async def _check_host_module_async(module_type: str, host_id: int) -> str:
    from app.db import task_session  # noqa: PLC0415

    async with task_session() as db:
        return await check_host(db, module_type, _resolve_check(module_type), host_id)


@celery_app.task(name="app.tasks.drift_sweep.check_host_module", queue="drift")
def check_host_module(module_type: str, host_id: int) -> dict:
    """One host, one module: the unit a sweep fans out to."""
    import asyncio  # noqa: PLC0415

    try:
        outcome = asyncio.run(_check_host_module_async(module_type, host_id))
    finally:
        try:
            import redis as redis_lib  # noqa: PLC0415

            from app.config import settings  # noqa: PLC0415

            redis_lib.from_url(settings.redis.url).delete(_inflight_key(module_type, host_id))
        except Exception:  # noqa: BLE001 — the key expires on its own
            logger.warning("drift: could not clear in-flight key for %s/%d", module_type, host_id)
    return {"module": module_type, "host_id": host_id, "outcome": outcome}


def run_sweep(module_type: str) -> dict[str, int]:
    """Body of each module's periodic task: fan out and return."""
    import asyncio  # noqa: PLC0415

    return asyncio.run(fan_out_module(module_type))
