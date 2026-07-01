"""Drift-check periodic task and per-host helper.

``check_all_drift`` is the RedBeat-driven sweep that runs every
``drift.check_interval_minutes``. The per-host body has been extracted
into ``_check_drift_for_one_host`` so the unified action dispatcher
(``_builtin.drift_check``) can re-use it for one-host / one-group runs
without duplicating the SSH + drift-detector + HostModuleStatus
write-back logic.
"""

from datetime import UTC

from app.tasks import celery_app


async def _check_drift_for_one_host(host, db) -> bool:
    """Run a drift check against one host. Commits per-host.

    Returns ``True`` if the check ran (regardless of drift outcome) and
    ``False`` if the host was skipped because its firewall_backend is
    ``unknown``. Exceptions are caught and recorded as
    ``host.sync_status="error"`` / ``"unknown"`` (matching the periodic
    sweep's behaviour) — the caller does not see them.
    """
    import asyncio  # noqa: F401  — re-export for compatibility
    from dataclasses import asdict
    from datetime import datetime

    import asyncssh
    from sqlalchemy import select

    from app.crypto.encryption import decrypt_ssh_key
    from app.crypto.key_management import get_master_key
    from app.drift.detector import check_drift
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
        current_fw_state = await fetch_current_firewall_state(host.id, db)
        drift_result = await check_drift(
            host.id,
            desired,
            db,
            desired_policies=desired_policies,
            current_state=current_fw_state,
        )
        host.last_drift_check_at = datetime.now(UTC)

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
                pass
        await db.commit()
        return True
    except (OSError, asyncssh.Error, TimeoutError, SSHFetchError):
        from app.models.host import SyncStatus

        host.sync_status = SyncStatus.unknown
        host.last_drift_check_at = datetime.now(UTC)
        await db.commit()
        return True
    except Exception:
        from app.models.host import SyncStatus

        host.sync_status = SyncStatus.error
        host.last_drift_check_at = datetime.now(UTC)
        await db.commit()
        return True


@celery_app.task(name="app.tasks.drift.check_all_drift", queue="long_running")
def check_all_drift():
    """Periodic task: check drift for all hosts with drift_check_enabled=True."""
    import asyncio

    from sqlalchemy import select

    from app.db import task_session
    from app.models.host import Host

    async def _run():
        async with task_session() as db:
            result = await db.execute(select(Host).where(Host.drift_check_enabled))
            hosts = result.scalars().all()
            for host in hosts:
                await _check_drift_for_one_host(host, db)
            return len(hosts)

    count = asyncio.run(_run())
    return {"checked": count}


# Register periodic drift check via RedBeat (prevents duplicate schedules on restart)
def _register_beat_schedule():
    from celery.schedules import schedule
    from redbeat import RedBeatSchedulerEntry

    from app.config import settings

    interval = schedule(run_every=settings.drift.check_interval_minutes * 60)
    entry = RedBeatSchedulerEntry(
        name="check-drift-periodic",
        task="app.tasks.drift.check_all_drift",
        schedule=interval,
        app=celery_app,
    )
    entry.save()


try:
    _register_beat_schedule()
except Exception:
    # Fallback: Redis may not be available at import time (e.g., during tests)
    pass
