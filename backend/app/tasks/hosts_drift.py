from app.tasks import celery_app


async def _maybe_update_host_ip(conn, host, db):
    """If primary IP on the host differs from stored, update it and invalidate dependents."""
    from app.config import settings
    from app.hosts.dependents import invalidate_host_ref_dependents

    if not getattr(settings.hosts, "ip_recheck_on_drift", True):
        return
    try:
        # Probe the host's outbound-facing IP in the family we connected over.
        family = "-4" if ":" not in (host.ip_address or "") else "-6"
        cmd = f"ip {family} -o route get 1.1.1.1 2>/dev/null | awk '{{for(i=1;i<=NF;i++) if($i==\"src\") print $(i+1)}}' | head -n1"
        if family == "-6":
            cmd = "ip -6 -o route get 2606:4700:4700::1111 2>/dev/null | awk '{for(i=1;i<=NF;i++) if($i==\"src\") print $(i+1)}' | head -n1"
        result = await conn.run(cmd, check=False, timeout=10)
        new_ip = (result.stdout or "").strip()
    except Exception:
        return
    if not new_ip or new_ip == host.ip_address:
        return
    host.ip_address = new_ip
    await invalidate_host_ref_dependents(db, host.id)


@celery_app.task(name="app.tasks.hosts_drift.check_all_hosts_drift", queue="long_running")
def check_all_hosts_drift():
    """Periodic task: check hosts file drift for all hosts with hosts_file drift enabled."""
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
    from app.hosts_mgmt.collector import collect_hosts_file
    from app.hosts_mgmt.diff import compute_hosts_diff
    from app.hosts_mgmt.merge import get_effective_hosts_entries
    from app.ssh_utils import get_source_ip, ssh_connect

    async def _run():
        async with task_session() as db:
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
                    hms.collected_state = [{"ip_address": e.ip_address, "hostname": e.hostname, "aliases": e.aliases} for e in current]
                    hms.collected_at = datetime.now(timezone.utc)
                    hms.error_message = None

                    try:
                        imported_key = asyncssh.import_private_key(private_key_pem)
                        async with ssh_connect(host.ip_address, port=host.ssh_port, username=ssh_key.ssh_user, client_keys=[imported_key]) as probe:
                            if not host.barricade_source_ip:
                                host.barricade_source_ip = await get_source_ip(probe)
                            await _maybe_update_host_ip(probe, host, db)
                    except Exception:
                        pass
                except (OSError, asyncssh.Error, TimeoutError) as e:
                    hms.sync_status = "unknown"
                    hms.last_drift_check_at = datetime.now(timezone.utc)
                    hms.error_message = f"Host unreachable: {e or 'connection timed out'}"
                except Exception as e:
                    hms.sync_status = "error"
                    hms.last_drift_check_at = datetime.now(timezone.utc)
                    hms.error_message = str(e)

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
