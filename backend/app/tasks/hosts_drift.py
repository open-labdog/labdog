from app.tasks import celery_app


@celery_app.task(name="app.tasks.hosts_drift.check_all_hosts_drift", queue="long_running")
def check_all_hosts_drift():
    """Periodic task: check hosts file drift for all hosts with hosts_file drift enabled."""
    import asyncio
    from datetime import datetime, timezone

    from sqlalchemy import select

    from app.crypto.encryption import decrypt_ssh_key
    from app.crypto.key_management import get_master_key
    from app.db import AsyncSessionLocal
    from app.models.host import Host
    from app.models.host_module_status import HostModuleStatus
    from app.models.ssh_key import SSHKey
    from app.hosts_mgmt.collector import collect_hosts_file
    from app.hosts_mgmt.diff import compute_hosts_diff
    from app.hosts_mgmt.merge import get_effective_hosts_entries

    async def _run():
        async with AsyncSessionLocal() as db:
            # Get hosts with hosts_file drift enabled
            result = await db.execute(
                select(HostModuleStatus).where(
                    HostModuleStatus.module_type == "hosts_file",
                    HostModuleStatus.drift_check_enabled == True,
                )
            )
            statuses = result.scalars().all()

            for hms in statuses:
                try:
                    # Get host SSH details
                    host_result = await db.execute(
                        select(Host).where(Host.id == hms.host_id)
                    )
                    host = host_result.scalar_one_or_none()
                    if not host or not host.ssh_key_id:
                        continue

                    key_result = await db.execute(
                        select(SSHKey).where(SSHKey.id == host.ssh_key_id)
                    )
                    ssh_key = key_result.scalar_one_or_none()
                    if not ssh_key:
                        continue

                    private_key_pem = decrypt_ssh_key(
                        ssh_key.encrypted_private_key, get_master_key()
                    )
                    desired = await get_effective_hosts_entries(host.id, db)

                    current = await collect_hosts_file(
                        host.ip_address, host.ssh_port, private_key_pem
                    )
                    diff = compute_hosts_diff(current, desired)

                    hms.sync_status = (
                        "in_sync" if not diff.has_changes else "out_of_sync"
                    )
                    hms.last_drift_check_at = datetime.now(timezone.utc)
                except Exception:
                    hms.sync_status = "error"
                    hms.last_drift_check_at = datetime.now(timezone.utc)

            await db.commit()
            return len(statuses)

    count = asyncio.run(_run())
    return {"checked": count}


# Register periodic hosts drift check via RedBeat
def _register_hosts_drift_schedule():
    from redbeat import RedBeatSchedulerEntry
    from celery.schedules import schedule
    from app.config import settings

    interval = schedule(run_every=settings.drift.check_interval_minutes * 60)
    entry = RedBeatSchedulerEntry(
        name="check-hosts-drift-periodic",
        task="app.tasks.hosts_drift.check_all_hosts_drift",
        schedule=interval,
        app=celery_app,
    )
    entry.save()


try:
    _register_hosts_drift_schedule()
except Exception:
    # Fallback: Redis may not be available at import time (e.g., during tests)
    pass
