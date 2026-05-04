from datetime import UTC

from app.tasks import celery_app


@celery_app.task(name="app.tasks.user_drift.check_all_user_drift", queue="long_running")
def check_all_user_drift():
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
    from app.ssh_utils import get_source_ip, ssh_connect
    from app.user_mgmt.collector import collect_group_states, collect_user_states
    from app.user_mgmt.diff import diff_groups, diff_users
    from app.user_mgmt.merge import get_effective_groups, get_effective_users

    async def _run():
        async with task_session() as db:
            result = await db.execute(
                select(HostModuleStatus).where(
                    HostModuleStatus.module_type == "linux_user",
                    HostModuleStatus.drift_check_enabled,
                )
            )
            statuses = result.scalars().all()

            for hms in statuses:
                try:
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

                    desired_users = await get_effective_users(host.id, db)
                    desired_groups = await get_effective_groups(host.id, db)

                    desired_user_dicts = [u.model_dump() for u in desired_users]
                    desired_group_dicts = [g.model_dump() for g in desired_groups]

                    usernames = [u.username for u in desired_users]
                    groupnames = [g.groupname for g in desired_groups]

                    actual_users = await collect_user_states(
                        host.ip_address, host.ssh_port, private_key_pem, usernames
                    )
                    actual_groups = await collect_group_states(
                        host.ip_address, host.ssh_port, private_key_pem, groupnames
                    )

                    user_diff = diff_users(desired_user_dicts, actual_users)
                    group_diff = diff_groups(desired_group_dicts, actual_groups)

                    users_drifted = bool(
                        user_diff.users_to_add
                        or user_diff.users_to_remove
                        or user_diff.users_to_update
                    )
                    groups_drifted = bool(
                        group_diff.groups_to_add
                        or group_diff.groups_to_remove
                        or group_diff.groups_to_update
                    )

                    hms.sync_status = "drifted" if users_drifted or groups_drifted else "in_sync"
                    hms.last_drift_check_at = datetime.now(UTC)
                    hms.collected_state = {"users": actual_users, "groups": actual_groups}
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


user_drift_task = check_all_user_drift


def _register_user_drift_schedule():
    from celery.schedules import schedule
    from redbeat import RedBeatSchedulerEntry

    from app.config import settings

    interval = schedule(run_every=settings.drift.check_interval_minutes * 60)
    entry = RedBeatSchedulerEntry(
        name="check-user-drift-periodic",
        task="app.tasks.user_drift.check_all_user_drift",
        schedule=interval,
        app=celery_app,
    )
    entry.save()


try:
    _register_user_drift_schedule()
except Exception:
    pass
