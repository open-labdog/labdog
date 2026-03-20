from app.tasks import celery_app


@celery_app.task(name="app.tasks.user_drift.check_all_user_drift", queue="long_running")
def check_all_user_drift():
    import asyncio
    from datetime import datetime, timezone

    from sqlalchemy import select

    from app.crypto.encryption import decrypt_ssh_key
    from app.crypto.key_management import get_master_key
    from app.db import AsyncSessionLocal
    from app.models.host import Host
    from app.models.host_module_status import HostModuleStatus
    from app.models.ssh_key import SSHKey
    from app.user_mgmt.collector import collect_user_states, collect_group_states
    from app.user_mgmt.diff import diff_users, diff_groups
    from app.user_mgmt.merge import get_effective_users, get_effective_groups

    async def _run():
        async with AsyncSessionLocal() as db:
            result = await db.execute(
                select(HostModuleStatus).where(
                    HostModuleStatus.module_type == "linux_user",
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

                    hms.sync_status = (
                        "drifted" if users_drifted or groups_drifted else "in_sync"
                    )
                    hms.last_drift_check_at = datetime.now(timezone.utc)
                except Exception:
                    hms.sync_status = "error"
                    hms.last_drift_check_at = datetime.now(timezone.utc)

            await db.commit()
            return len(statuses)

    count = asyncio.run(_run())
    return {"checked": count}


user_drift_task = check_all_user_drift


def _register_user_drift_schedule():
    from redbeat import RedBeatSchedulerEntry
    from celery.schedules import schedule
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
