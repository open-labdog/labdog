"""Linux user/group drift check: per-host body plus the sweep delegator.

The sweep itself — host locking, per-host transaction boundary — lives
in `app.tasks.drift_sweep.sweep_module` (BUG-67). This module owns the
collect-and-diff and nothing else, and never commits: the driver
decides whether the verdict becomes durable.
"""

import logging
from datetime import UTC

from app.tasks import celery_app

logger = logging.getLogger(__name__)


async def check_user_drift_for_one_host(host, hms, db) -> bool:
    """Collect and diff this host's users/groups, writing the verdict to *hms*.

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
    from app.ssh_utils import get_source_ip, ssh_connect_host
    from app.user_mgmt.collector import collect_group_states, collect_user_states
    from app.user_mgmt.diff import diff_groups, diff_users
    from app.user_mgmt.merge import get_effective_groups, get_effective_users

    try:
        if not host.ssh_key_id:
            return False
        ssh_key = (
            await db.execute(select(SSHKey).where(SSHKey.id == host.ssh_key_id))
        ).scalar_one_or_none()
        if not ssh_key:
            return False

        private_key_pem = decrypt_ssh_key(ssh_key.encrypted_private_key, get_master_key())

        desired_users = await get_effective_users(host.id, db)
        desired_groups = await get_effective_groups(host.id, db)

        desired_user_dicts = [u.model_dump() for u in desired_users]
        desired_group_dicts = [g.model_dump() for g in desired_groups]

        usernames = [u.username for u in desired_users]
        groupnames = [g.groupname for g in desired_groups]

        _t0 = time.monotonic()
        actual_users = await collect_user_states(host, db, private_key_pem, usernames)
        actual_groups = await collect_group_states(host, db, private_key_pem, groupnames)

        user_diff = diff_users(desired_user_dicts, actual_users)
        group_diff = diff_groups(desired_group_dicts, actual_groups)
        _duration_ms = int((time.monotonic() - _t0) * 1000)

        users_drifted = bool(
            user_diff.users_to_add or user_diff.users_to_remove or user_diff.users_to_update
        )
        groups_drifted = bool(
            group_diff.groups_to_add or group_diff.groups_to_remove or group_diff.groups_to_update
        )

        hms.sync_status = "drifted" if users_drifted or groups_drifted else "in_sync"
        hms.last_drift_check_at = datetime.now(UTC)
        hms.collected_state = {"users": actual_users, "groups": actual_groups}
        hms.collected_at = datetime.now(UTC)
        hms.error_message = None
        await record_drift_sample(
            db,
            host_id=host.id,
            module_type="linux_user",
            status="out_of_sync" if (users_drifted or groups_drifted) else "in_sync",
            add_count=len(user_diff.users_to_add) + len(group_diff.groups_to_add),
            remove_count=len(user_diff.users_to_remove) + len(group_diff.groups_to_remove),
            policy_change_count=len(user_diff.users_to_update) + len(group_diff.groups_to_update),
            duration_ms=_duration_ms,
        )

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
        logger.exception("linux_user drift check failed for host %s", host.id)
        hms.sync_status = "error"
        hms.last_drift_check_at = datetime.now(UTC)
        hms.error_message = str(e)
        return True


@celery_app.task(name="app.tasks.user_drift.check_all_user_drift", queue="long_running")
def check_all_user_drift():
    """Periodic task: user/group drift for every host with it enabled."""
    import asyncio

    from app.tasks.drift_sweep import sweep_module

    return asyncio.run(sweep_module("linux_user", check_user_drift_for_one_host))


user_drift_task = check_all_user_drift


def _register_user_drift_schedule():
    from app.config import settings
    from app.tasks.beat_registry import ensure_entry

    ensure_entry(
        name="check-user-drift-periodic",
        task="app.tasks.user_drift.check_all_user_drift",
        run_every_seconds=settings.drift.check_interval_minutes * 60,
        app=celery_app,
    )


# Registration happens from ``beat_init`` (app.tasks.beat_registry), not at
# import. Calling it here rewrote the entry's ``due_at`` in every process
# that imported this module — API included — so on a deployment that
# restarts more than once a day, a daily job never fired at all (BUG-70).
