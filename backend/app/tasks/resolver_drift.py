"""Resolver drift check: per-host body, single-host task, sweep delegator.

The sweep itself — host locking, per-host transaction boundary — lives
in `app.tasks.drift_sweep.sweep_module` (BUG-67). This module owns the
collect-and-diff and nothing else, and never commits from the shared
body: the caller decides whether the verdict becomes durable.

``run_resolver_drift_check`` is the on-demand single-host task the API
dispatches. It shares the same body — before BUG-67 it carried its own
copy that had already drifted from the sweep's (it refreshed the host's
rolled-up status, the sweep didn't) — and commits for itself, because
nothing upstream of it does.
"""

import logging
from datetime import UTC

from app.tasks import celery_app

logger = logging.getLogger(__name__)


async def _collect_and_diff(host, hms, db):
    """Collect the host's resolver state and write the verdict to *hms*.

    Returns the :class:`~app.resolver.diff.ResolverDiff`, or ``None``
    when the host is unmanaged for this module (no effective resolver
    config, no SSH key) and nothing was written. Does not commit.

    ``hms`` may be ``None``; the row is created when the diff is
    actually computed, which is what the on-demand task needs — it can
    be the first thing to ever check a host.
    """
    import time
    from datetime import datetime

    import asyncssh
    from sqlalchemy import select

    from app.crypto.encryption import decrypt_ssh_key
    from app.crypto.key_management import get_master_key
    from app.metrics.recorder import record_drift_sample
    from app.models.host_module_status import HostModuleStatus
    from app.models.ssh_key import SSHKey
    from app.resolver.collector import collect_resolver_state
    from app.resolver.diff import compute_resolver_diff
    from app.resolver.merge import get_effective_resolver
    from app.ssh_utils import get_source_ip, ssh_connect_host

    effective = await get_effective_resolver(host.id, db)
    if not effective:
        return None
    if not host.ssh_key_id:
        return None
    ssh_key = (
        await db.execute(select(SSHKey).where(SSHKey.id == host.ssh_key_id))
    ).scalar_one_or_none()
    if not ssh_key:
        return None

    private_key_pem = decrypt_ssh_key(ssh_key.encrypted_private_key, get_master_key())

    _t0 = time.monotonic()
    actual = await collect_resolver_state(host, db, private_key_pem, effective.resolver_type)
    desired = {
        "nameservers": effective.nameservers,
        "search_domains": effective.search_domains,
        "options": effective.options,
    }
    diff = compute_resolver_diff(actual, desired)
    _duration_ms = int((time.monotonic() - _t0) * 1000)

    if hms is None:
        hms = (
            await db.execute(
                select(HostModuleStatus).where(
                    HostModuleStatus.host_id == host.id,
                    HostModuleStatus.module_type == "resolver",
                )
            )
        ).scalar_one_or_none()
    if hms is None:
        hms = HostModuleStatus(host_id=host.id, module_type="resolver")
        db.add(hms)

    hms.sync_status = "in_sync" if not diff.has_changes else "out_of_sync"
    hms.last_drift_check_at = datetime.now(UTC)
    hms.collected_state = actual
    hms.collected_at = datetime.now(UTC)
    hms.error_message = None
    await record_drift_sample(
        db,
        host_id=host.id,
        module_type="resolver",
        status=hms.sync_status,
        policy_change_count=sum(
            [
                diff.nameservers_changed,
                diff.search_domains_changed,
                diff.options_changed,
            ]
        ),
        duration_ms=_duration_ms,
    )

    from app.api.host_state import refresh_host_sync_status

    await refresh_host_sync_status(host, db)

    if not host.labdog_source_ip:
        try:
            imported_key = asyncssh.import_private_key(private_key_pem)
            async with ssh_connect_host(host, db, client_keys=[imported_key]) as probe:
                host.labdog_source_ip = await get_source_ip(probe)
        except Exception:
            # Best-effort optimisation for the next merge, not part of the
            # verdict. Debug level because a host that fails this fails it
            # on every tick.
            logger.debug("source-IP probe failed for host %s", host.id, exc_info=True)
    return diff


async def check_resolver_drift_for_one_host(host, hms, db) -> bool:
    """`app.tasks.drift_sweep.CheckOne` adapter for the resolver module."""
    from datetime import datetime

    import asyncssh

    try:
        diff = await _collect_and_diff(host, hms, db)
        return diff is not None
    except (OSError, asyncssh.Error, TimeoutError) as e:
        hms.sync_status = "unknown"
        hms.last_drift_check_at = datetime.now(UTC)
        hms.error_message = f"Host unreachable: {e or 'connection timed out'}"
        return True
    except Exception as e:
        logger.exception("resolver drift check failed for host %s", host.id)
        hms.sync_status = "error"
        hms.last_drift_check_at = datetime.now(UTC)
        hms.error_message = str(e)
        return True


@celery_app.task(
    bind=True,
    name="app.tasks.resolver_drift.run_resolver_drift_check",
    queue="long_running",
)
def run_resolver_drift_check(self, host_id: int) -> dict:
    """Check DNS resolver drift on a single host via SSH, on demand."""
    import asyncio

    from sqlalchemy import select

    from app.db import task_session
    from app.models.host import Host

    async def _run():
        async with task_session() as db:
            host = (await db.execute(select(Host).where(Host.id == host_id))).scalar_one()
            diff = await _collect_and_diff(host, None, db)
            if diff is None:
                return {"host_id": host_id, "status": "unmanaged", "has_drift": False}
            await db.commit()
            return {
                "host_id": host_id,
                "has_drift": diff.has_changes,
                "nameservers_changed": diff.nameservers_changed,
                "search_domains_changed": diff.search_domains_changed,
                "options_changed": diff.options_changed,
                "current": diff.current,
                "desired": diff.desired,
            }

    return asyncio.run(_run())


@celery_app.task(
    name="app.tasks.resolver_drift.check_all_resolver_drift",
    queue="long_running",
)
def check_all_resolver_drift():
    """Periodic task: resolver drift for every host with resolver drift enabled."""
    import asyncio

    from app.tasks.drift_sweep import sweep_module

    return asyncio.run(sweep_module("resolver", check_resolver_drift_for_one_host))


def _register_resolver_drift_schedule():
    from app.config import settings
    from app.tasks.beat_registry import ensure_entry

    ensure_entry(
        name="check-resolver-drift-periodic",
        task="app.tasks.resolver_drift.check_all_resolver_drift",
        run_every_seconds=settings.drift.check_interval_minutes * 60,
        app=celery_app,
    )


# Registration happens from ``beat_init`` (app.tasks.beat_registry), not at
# import. Calling it here rewrote the entry's ``due_at`` in every process
# that imported this module — API included — so on a deployment that
# restarts more than once a day, a daily job never fired at all (BUG-70).
