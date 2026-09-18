"""Service drift check: per-host body plus the periodic sweep delegator.

The sweep itself — host locking, per-host transaction boundary — lives
in `app.tasks.drift_sweep.sweep_module` (BUG-67). This module owns the
collect-and-diff and nothing else, and never commits: the driver
decides whether the verdict becomes durable.
"""

import logging
from datetime import UTC

from app.tasks import celery_app

logger = logging.getLogger(__name__)


async def check_service_drift_for_one_host(host, hms, db) -> bool:
    """Collect and diff this host's services, writing the verdict to *hms*.

    See `app.tasks.drift_sweep.CheckOne` for the contract. Returns
    ``False`` when the host has no usable SSH key, in which case
    nothing is written.
    """
    import time
    from datetime import datetime

    import asyncssh
    from sqlalchemy import select

    from app.crypto.encryption import decrypt_ssh_key
    from app.crypto.key_management import get_master_key
    from app.metrics.recorder import record_drift_sample
    from app.models.ssh_key import SSHKey
    from app.services.collector import collect_service_states
    from app.services.diff import compute_service_diff
    from app.services.merge import get_effective_services
    from app.ssh_utils import get_source_ip, ssh_connect_host

    try:
        if not host.ssh_key_id:
            return False
        ssh_key = (
            await db.execute(select(SSHKey).where(SSHKey.id == host.ssh_key_id))
        ).scalar_one_or_none()
        if not ssh_key:
            return False

        private_key_pem = decrypt_ssh_key(ssh_key.encrypted_private_key, get_master_key())
        desired = await get_effective_services(host.id, db)
        service_names = [s.service_name for s in desired]

        _t0 = time.monotonic()
        current = await collect_service_states(host, db, private_key_pem, service_names)
        diff = compute_service_diff(current, desired)
        _duration_ms = int((time.monotonic() - _t0) * 1000)

        hms.sync_status = "in_sync" if not diff.has_changes else "out_of_sync"
        hms.last_drift_check_at = datetime.now(UTC)
        await record_drift_sample(
            db,
            host_id=host.id,
            module_type="service",
            status=hms.sync_status,
            policy_change_count=len(diff.services_to_update),
            duration_ms=_duration_ms,
        )
        hms.collected_state = [
            {
                "service_name": s.service_name,
                "active_state": s.active_state,
                "enabled": s.enabled,
            }
            for s in current
        ]
        hms.collected_at = datetime.now(UTC)
        hms.error_message = None

        if not host.labdog_source_ip:
            try:
                imported_key = asyncssh.import_private_key(private_key_pem)
                async with ssh_connect_host(host, db, client_keys=[imported_key]) as probe:
                    host.labdog_source_ip = await get_source_ip(probe)
            except Exception:
                logger.debug("source-IP probe failed for host %s", host.id, exc_info=True)
        return True
    except (OSError, asyncssh.Error, TimeoutError) as e:
        hms.sync_status = "unknown"
        hms.last_drift_check_at = datetime.now(UTC)
        hms.error_message = f"Host unreachable: {e or 'connection timed out'}"
        return True
    except Exception as e:
        logger.exception("service drift check failed for host %s", host.id)
        hms.sync_status = "error"
        hms.last_drift_check_at = datetime.now(UTC)
        hms.error_message = str(e)
        return True


@celery_app.task(name="app.tasks.service_drift.check_all_service_drift", queue="long_running")
def check_all_service_drift():
    """Periodic task: service drift for every host with service drift enabled."""
    import asyncio

    from app.tasks.drift_sweep import sweep_module

    return asyncio.run(sweep_module("service", check_service_drift_for_one_host))


# Register periodic service drift check via RedBeat
def _register_service_drift_schedule():
    from app.config import settings
    from app.tasks.beat_registry import ensure_entry

    ensure_entry(
        name="check-service-drift-periodic",
        task="app.tasks.service_drift.check_all_service_drift",
        run_every_seconds=settings.drift.check_interval_minutes * 60,
        app=celery_app,
    )


# Registration happens from ``beat_init`` (app.tasks.beat_registry), not at
# import. Calling it here rewrote the entry's ``due_at`` in every process
# that imported this module — API included — so on a deployment that
# restarts more than once a day, a daily job never fired at all (BUG-70).
