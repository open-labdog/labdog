from app.tasks import celery_app


@celery_app.task(name="app.tasks.package_drift.check_all_package_drift", queue="long_running")
def check_all_package_drift():
    import asyncio
    from datetime import datetime, timezone

    from sqlalchemy import select

    from app.crypto.encryption import decrypt_ssh_key
    from app.crypto.key_management import get_master_key
    from app.db import task_session
    from app.models.host import Host
    from app.models.host_module_status import HostModuleStatus
    from app.models.ssh_key import SSHKey
    import asyncssh
    from app.packages.collector import collect_package_states
    from app.packages.diff import compute_diff
    from app.packages.merge import get_effective_packages
    from app.ssh_utils import get_source_ip, ssh_connect

    async def _run():
        async with task_session() as db:
            result = await db.execute(
                select(HostModuleStatus).where(
                    HostModuleStatus.module_type == "package",
                    HostModuleStatus.drift_check_enabled == True,
                )
            )
            statuses = result.scalars().all()

            for hms in statuses:
                try:
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

                    effective = await get_effective_packages(host.id, db)
                    desired_dicts = [p.model_dump() for p in effective]
                    package_names = [p.package_name for p in effective]

                    actual = await collect_package_states(
                        host.ip_address, host.ssh_port, private_key_pem, package_names
                    )

                    pkg_diff = compute_diff(desired_dicts, actual)

                    hms.sync_status = "drifted" if pkg_diff.has_drift else "in_sync"
                    hms.last_drift_check_at = datetime.now(timezone.utc)
                    hms.collected_state = actual
                    hms.collected_at = datetime.now(timezone.utc)
                    hms.error_message = None

                    if not host.barricade_source_ip:
                        try:
                            imported_key = asyncssh.import_private_key(private_key_pem)
                            async with ssh_connect(host.ip_address, port=host.ssh_port, username=ssh_key.ssh_user, client_keys=[imported_key]) as probe:
                                host.barricade_source_ip = await get_source_ip(probe)
                        except Exception:
                            pass
                except (OSError, asyncssh.Error) as e:
                    hms.sync_status = "unknown"
                    hms.last_drift_check_at = datetime.now(timezone.utc)
                    hms.error_message = f"Host unreachable: {e}"
                except Exception as e:
                    hms.sync_status = "error"
                    hms.last_drift_check_at = datetime.now(timezone.utc)
                    hms.error_message = str(e)

            await db.commit()
            return len(statuses)

    count = asyncio.run(_run())
    return {"checked": count}


package_drift_task = check_all_package_drift

DRIFT_SCHEDULE_KEY = "barricade:package:drift"


def _register_package_drift_schedule():
    from redbeat import RedBeatSchedulerEntry
    from celery.schedules import schedule
    from app.config import settings

    interval = schedule(run_every=settings.drift.check_interval_minutes * 60)
    entry = RedBeatSchedulerEntry(
        name="check-package-drift-periodic",
        task="app.tasks.package_drift.check_all_package_drift",
        schedule=interval,
        app=celery_app,
    )
    entry.save()


try:
    _register_package_drift_schedule()
except Exception:
    pass
