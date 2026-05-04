from datetime import UTC

from app.tasks import celery_app


@celery_app.task(name="app.tasks.service_drift.check_all_service_drift", queue="long_running")
def check_all_service_drift():
    """Periodic task: check service drift for all hosts with service drift enabled."""
    import asyncio
    from datetime import datetime

    import asyncssh
    from sqlalchemy import select

    from app.crypto.encryption import decrypt_ssh_key
    from app.crypto.key_management import get_master_key
    from app.db import task_session
    from app.models.host import Host
    from app.models.host_module_status import HostModuleStatus
    from app.models.ssh_key import SSHKey
    from app.services.collector import collect_service_states
    from app.services.diff import compute_service_diff
    from app.services.merge import get_effective_services
    from app.ssh_utils import get_source_ip, ssh_connect

    async def _run():
        async with task_session() as db:
            # Get hosts with service drift enabled
            result = await db.execute(
                select(HostModuleStatus).where(
                    HostModuleStatus.module_type == "service",
                    HostModuleStatus.drift_check_enabled,
                )
            )
            statuses = result.scalars().all()

            for hms in statuses:
                try:
                    # Get host SSH details
                    host_result = await db.execute(select(Host).where(Host.id == hms.host_id))
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
                    desired = await get_effective_services(host.id, db)
                    service_names = [s.service_name for s in desired]

                    current = await collect_service_states(
                        host.ip_address, host.ssh_port, private_key_pem, service_names
                    )
                    diff = compute_service_diff(current, desired)

                    hms.sync_status = "in_sync" if not diff.has_changes else "out_of_sync"
                    hms.last_drift_check_at = datetime.now(UTC)
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
                            async with ssh_connect(
                                host.ip_address,
                                port=host.ssh_port,
                                username=ssh_key.ssh_user,
                                client_keys=[imported_key],
                            ) as probe:
                                host.labdog_source_ip = await get_source_ip(probe)
                        except Exception:
                            pass
                except (OSError, asyncssh.Error, TimeoutError) as e:
                    hms.sync_status = "unknown"
                    hms.last_drift_check_at = datetime.now(UTC)
                    hms.error_message = f"Host unreachable: {e or 'connection timed out'}"
                except Exception as e:
                    hms.sync_status = "error"
                    hms.last_drift_check_at = datetime.now(UTC)
                    hms.error_message = str(e)

            await db.commit()
            return len(statuses)

    count = asyncio.run(_run())
    return {"checked": count}


# Register periodic service drift check via RedBeat
def _register_service_drift_schedule():
    from celery.schedules import schedule
    from redbeat import RedBeatSchedulerEntry

    from app.config import settings

    interval = schedule(run_every=settings.drift.check_interval_minutes * 60)
    entry = RedBeatSchedulerEntry(
        name="check-service-drift-periodic",
        task="app.tasks.service_drift.check_all_service_drift",
        schedule=interval,
        app=celery_app,
    )
    entry.save()


try:
    _register_service_drift_schedule()
except Exception:
    # Fallback: Redis may not be available at import time (e.g., during tests)
    pass
