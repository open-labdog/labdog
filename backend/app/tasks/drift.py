"""Firewall drift-check periodic task and per-host helper.

``check_all_drift`` is the RedBeat-driven sweep that runs every
``drift.check_interval_minutes``. The per-host body lives in
``_check_drift_for_one_host`` so the unified action dispatcher
(``_builtin.drift_check``) can re-use it for one-host / one-group runs
without duplicating the SSH + drift-detector + HostModuleStatus
write-back logic.

The sweep itself is `app.tasks.drift_sweep.sweep_module`, which is
where the host locking and the per-host transaction boundary live
(BUG-67). This module supplies the collect-and-diff body and nothing
else; in particular it does not commit — its caller decides whether
the verdict becomes durable.
"""

import logging
from datetime import UTC

from app.tasks import celery_app

logger = logging.getLogger(__name__)


async def _check_drift_for_one_host(host, db, hms=None) -> bool:
    """Run a firewall drift check against one host.

    Writes the verdict onto ``hms`` (creating the row when *hms* is
    ``None`` and none exists) and onto ``host``, in *db*, and does
    **not** commit — the caller owns the transaction. The periodic
    sweep relies on that: it discards the whole transaction when
    another operation claims the host while this was running.

    Returns ``True`` if the check ran (regardless of drift outcome) and
    ``False`` if the host was skipped because its firewall_backend is
    ``unknown``. Unreachable hosts and unexpected errors are caught and
    recorded as ``host.sync_status="unknown"`` / ``"error"``; the caller
    does not see them, and they still count as having run.
    """
    import asyncio  # noqa: F401  — re-export for compatibility
    import time
    from dataclasses import asdict
    from datetime import datetime

    import asyncssh
    from sqlalchemy import select

    from app.crypto.encryption import decrypt_ssh_key
    from app.crypto.key_management import get_master_key
    from app.drift.detector import check_drift
    from app.metrics.recorder import record_drift_sample
    from app.models.host_module_status import HostModuleStatus
    from app.models.ssh_key import SSHKey
    from app.ssh_utils import get_source_ip, ssh_connect_host
    from app.sync.diff import SSHFetchError, fetch_current_firewall_state

    backend = (
        host.firewall_backend.value
        if hasattr(host.firewall_backend, "value")
        else host.firewall_backend
    )
    if backend == "unknown":
        return False

    try:
        from app.api.drift import _get_desired_state_for_host

        desired, desired_policies = await _get_desired_state_for_host(
            host.id, db, host_source_ip=host.labdog_source_ip
        )
        _t0 = time.monotonic()
        current_fw_state = await fetch_current_firewall_state(host.id, db)
        drift_result = await check_drift(
            host.id,
            desired,
            db,
            desired_policies=desired_policies,
            current_state=current_fw_state,
        )
        _duration_ms = int((time.monotonic() - _t0) * 1000)
        host.last_drift_check_at = datetime.now(UTC)

        diff = drift_result.diff
        await record_drift_sample(
            db,
            host_id=host.id,
            module_type="firewall",
            status=drift_result.status.value
            if hasattr(drift_result.status, "value")
            else drift_result.status,
            add_count=len(diff.rules_to_add) if diff else 0,
            remove_count=len(diff.rules_to_remove) if diff else 0,
            policy_change_count=len(diff.policy_changes) if diff else 0,
            duration_ms=_duration_ms,
        )

        if hms is None:
            hms = (
                await db.execute(
                    select(HostModuleStatus).where(
                        HostModuleStatus.host_id == host.id,
                        HostModuleStatus.module_type == "firewall",
                    )
                )
            ).scalar_one_or_none()
        if hms is None:
            hms = HostModuleStatus(host_id=host.id, module_type="firewall")
            db.add(hms)
        hms.sync_status = drift_result.status.value
        hms.collected_state = [asdict(r) for r in current_fw_state.rules]
        hms.collected_at = datetime.now(UTC)

        from app.api.host_state import refresh_host_sync_status

        await refresh_host_sync_status(host, db)

        if not host.labdog_source_ip and host.ssh_key_id:
            try:
                key_result = await db.execute(select(SSHKey).where(SSHKey.id == host.ssh_key_id))
                ssh_key = key_result.scalar_one_or_none()
                if ssh_key:
                    private_key_pem = decrypt_ssh_key(
                        ssh_key.encrypted_private_key, get_master_key()
                    )
                    imported_key = asyncssh.import_private_key(private_key_pem)
                    async with ssh_connect_host(
                        host,
                        db,
                        client_keys=[imported_key],
                    ) as probe:
                        host.labdog_source_ip = await get_source_ip(probe)
            except Exception:
                # Best-effort: the drift verdict is the point, the source
                # IP is an optimisation for the next merge. Logged at
                # debug because a host that fails this fails it on every
                # tick, and one line per host per interval would bury the
                # log for a value nothing is waiting on.
                logger.debug("source-IP probe failed for host %s", host.id, exc_info=True)
        return True
    except (OSError, asyncssh.Error, TimeoutError, SSHFetchError):
        from app.models.host import SyncStatus

        host.sync_status = SyncStatus.unknown
        host.last_drift_check_at = datetime.now(UTC)
        return True
    except Exception:
        from app.models.host import SyncStatus

        logger.exception("firewall drift check failed for host %s", host.id)
        host.sync_status = SyncStatus.error
        host.last_drift_check_at = datetime.now(UTC)
        return True


async def _sweep_one(host, hms, db) -> bool:
    """`app.tasks.drift_sweep.CheckOne` adapter for the firewall module."""
    return await _check_drift_for_one_host(host, db, hms=hms)


@celery_app.task(name="app.tasks.drift.check_all_drift", queue="long_running")
def check_all_drift():
    """Periodic task: check firewall drift for every drift-enabled host.

    Candidates come from ``Host.drift_check_enabled`` rather than from a
    per-module toggle: the firewall module predates ``HostModuleStatus``
    and still owns the host-level flag.
    """
    import asyncio

    from app.tasks.drift_sweep import sweep_module

    return asyncio.run(sweep_module("firewall", _sweep_one, host_gated=True))


# Register periodic drift check via RedBeat (prevents duplicate schedules on restart)
def _register_beat_schedule():
    from app.config import settings
    from app.tasks.beat_registry import ensure_entry

    ensure_entry(
        name="check-drift-periodic",
        task="app.tasks.drift.check_all_drift",
        run_every_seconds=settings.drift.check_interval_minutes * 60,
        app=celery_app,
    )


# Registration happens from ``beat_init`` (app.tasks.beat_registry), not at
# import. Calling it here rewrote the entry's ``due_at`` in every process
# that imported this module — API included — so on a deployment that
# restarts more than once a day, a daily job never fired at all (BUG-70).
