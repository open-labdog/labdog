"""Cron drift check: per-host body plus the periodic sweep delegator.

The sweep itself — host locking, per-host transaction boundary — lives
in `app.tasks.drift_sweep.sweep_module` (BUG-67). This module owns the
collect-and-diff and nothing else, and never commits: the driver
decides whether the verdict becomes durable.
"""

import logging
from datetime import UTC

from app.tasks import celery_app

logger = logging.getLogger(__name__)


async def check_cron_drift_for_one_host(host, hms, db) -> bool:
    """Collect and diff this host's cron jobs, writing the verdict to *hms*.

    See `app.tasks.drift_sweep.CheckOne` for the contract. Returns
    ``False`` when the host has no usable SSH key, in which case
    nothing is written.
    """
    import time
    from datetime import datetime

    import asyncssh
    from sqlalchemy import select

    from app.cron.collector import collect_cron_jobs
    from app.cron.diff import diff_cron_jobs
    from app.cron.merge import get_effective_cron_jobs
    from app.crypto.encryption import decrypt_ssh_key
    from app.crypto.key_management import get_master_key
    from app.metrics.recorder import record_drift_sample
    from app.models.ssh_key import SSHKey
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

        effective = await get_effective_cron_jobs(host.id, db)
        desired_dicts = [
            {
                "name": j.name,
                "user": j.user,
                "schedule": j.schedule,
                "command": j.command,
            }
            for j in effective
        ]

        users = list({j.user for j in effective})

        _t0 = time.monotonic()
        actual = await collect_cron_jobs(host, db, private_key_pem, users)

        cron_diff = diff_cron_jobs(desired_dicts, actual)
        _duration_ms = int((time.monotonic() - _t0) * 1000)

        drifted = bool(
            cron_diff.jobs_to_add or cron_diff.jobs_to_remove or cron_diff.jobs_to_update
        )

        hms.sync_status = "drifted" if drifted else "in_sync"
        hms.last_drift_check_at = datetime.now(UTC)
        hms.collected_state = actual
        hms.collected_at = datetime.now(UTC)
        hms.error_message = None
        await record_drift_sample(
            db,
            host_id=host.id,
            module_type="cron",
            status="out_of_sync" if drifted else "in_sync",
            add_count=len(cron_diff.jobs_to_add),
            remove_count=len(cron_diff.jobs_to_remove),
            policy_change_count=len(cron_diff.jobs_to_update),
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
                # Best-effort optimisation for the next merge, not part of
                # the verdict. Debug level because a host that fails this
                # fails it every tick.
                logger.debug("source-IP probe failed for host %s", host.id, exc_info=True)
        return True
    except (OSError, asyncssh.Error, TimeoutError) as e:
        hms.sync_status = "unknown"
        hms.last_drift_check_at = datetime.now(UTC)
        hms.error_message = f"Host unreachable: {e or 'connection timed out'}"
        return True
    except Exception as e:
        logger.exception("cron drift check failed for host %s", host.id)
        hms.sync_status = "error"
        hms.last_drift_check_at = datetime.now(UTC)
        hms.error_message = str(e)
        return True


@celery_app.task(name="app.tasks.cron_drift.check_all_cron_drift", queue="long_running")
def check_all_cron_drift():
    """Periodic task: cron drift for every host with cron drift enabled."""
    import asyncio

    from app.tasks.drift_sweep import sweep_module

    return asyncio.run(sweep_module("cron", check_cron_drift_for_one_host))


cron_drift_task = check_all_cron_drift


def _register_cron_drift_schedule():
    from app.config import settings
    from app.tasks.beat_registry import ensure_entry

    ensure_entry(
        name="check-cron-drift-periodic",
        task="app.tasks.cron_drift.check_all_cron_drift",
        run_every_seconds=settings.drift.check_interval_minutes * 60,
        app=celery_app,
    )


# Registration happens from ``beat_init`` (app.tasks.beat_registry), not at
# import. Calling it here rewrote the entry's ``due_at`` in every process
# that imported this module — API included — so on a deployment that
# restarts more than once a day, a daily job never fired at all (BUG-70).
