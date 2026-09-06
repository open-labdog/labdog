"""/etc/hosts drift check: per-host body plus the periodic sweep delegator.

The sweep itself — host locking, per-host transaction boundary — lives
in `app.tasks.drift_sweep.sweep_module` (BUG-67). This module owns the
collect-and-diff and nothing else, and never commits: the driver
decides whether the verdict becomes durable.
"""

import logging
from datetime import UTC

from app.tasks import celery_app

logger = logging.getLogger(__name__)


async def _maybe_update_host_ip(conn, host, db):
    """If primary IP on the host differs from stored, update it and invalidate dependents."""
    from app.config import settings
    from app.hosts.dependents import invalidate_host_ref_dependents

    if not getattr(settings.hosts, "ip_recheck_on_drift", True):
        return
    try:
        # Probe the host's outbound-facing IP in the family we connected over.
        family = "-4" if ":" not in (host.ip_address or "") else "-6"
        cmd = (
            f"ip {family} -o route get 1.1.1.1 2>/dev/null"
            " | awk '{for(i=1;i<=NF;i++) if($i==\"src\") print $(i+1)}'"
            " | head -n1"
        )
        if family == "-6":
            cmd = (
                "ip -6 -o route get 2606:4700:4700::1111 2>/dev/null"
                " | awk '{for(i=1;i<=NF;i++) if($i==\"src\") print $(i+1)}'"
                " | head -n1"
            )
        result = await conn.run(cmd, check=False, timeout=10)
        new_ip = (result.stdout or "").strip()
    except Exception:
        logger.debug("outbound-IP probe failed for host %s", host.id, exc_info=True)
        return
    if not new_ip or new_ip == host.ip_address:
        return
    host.ip_address = new_ip
    await invalidate_host_ref_dependents(db, host.id)


async def check_hosts_drift_for_one_host(host, hms, db) -> bool:
    """Collect and diff this host's /etc/hosts, writing the verdict to *hms*.

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
    from app.hosts_mgmt.collector import collect_hosts_file
    from app.hosts_mgmt.diff import compute_hosts_diff
    from app.hosts_mgmt.merge import get_effective_hosts_entries
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
        desired = await get_effective_hosts_entries(host.id, db)

        _t0 = time.monotonic()
        current = await collect_hosts_file(host, db, private_key_pem)
        diff = compute_hosts_diff(current, desired)
        _duration_ms = int((time.monotonic() - _t0) * 1000)

        hms.sync_status = "in_sync" if not diff.has_changes else "out_of_sync"
        hms.last_drift_check_at = datetime.now(UTC)
        hms.collected_state = [
            {"ip_address": e.ip_address, "hostname": e.hostname, "aliases": e.aliases}
            for e in current
        ]
        hms.collected_at = datetime.now(UTC)
        hms.error_message = None
        await record_drift_sample(
            db,
            host_id=host.id,
            module_type="hosts_file",
            status=hms.sync_status,
            add_count=len(diff.entries_to_add),
            remove_count=len(diff.entries_to_remove),
            policy_change_count=len(diff.entries_to_update),
            duration_ms=_duration_ms,
        )

        try:
            imported_key = asyncssh.import_private_key(private_key_pem)
            async with ssh_connect_host(host, db, client_keys=[imported_key]) as probe:
                if not host.labdog_source_ip:
                    host.labdog_source_ip = await get_source_ip(probe)
                await _maybe_update_host_ip(probe, host, db)
        except Exception:
            # Best-effort follow-up probes; the diff above is the verdict.
            logger.debug("source-IP probe failed for host %s", host.id, exc_info=True)
        return True
    except (OSError, asyncssh.Error, TimeoutError) as e:
        hms.sync_status = "unknown"
        hms.last_drift_check_at = datetime.now(UTC)
        hms.error_message = f"Host unreachable: {e or 'connection timed out'}"
        return True
    except Exception as e:
        logger.exception("hosts_file drift check failed for host %s", host.id)
        hms.sync_status = "error"
        hms.last_drift_check_at = datetime.now(UTC)
        hms.error_message = str(e)
        return True


@celery_app.task(name="app.tasks.hosts_drift.check_all_hosts_drift", queue="long_running")
def check_all_hosts_drift():
    """Periodic task: hosts-file drift for every host with it enabled."""
    import asyncio

    from app.tasks.drift_sweep import sweep_module

    return asyncio.run(sweep_module("hosts_file", check_hosts_drift_for_one_host))


# Register periodic hosts drift check via RedBeat
def _register_hosts_drift_schedule():
    from app.config import settings
    from app.tasks.beat_registry import ensure_entry

    ensure_entry(
        name="check-hosts-drift-periodic",
        task="app.tasks.hosts_drift.check_all_hosts_drift",
        run_every_seconds=settings.drift.check_interval_minutes * 60,
        app=celery_app,
    )


# Registration happens from ``beat_init`` (app.tasks.beat_registry), not at
# import. Calling it here rewrote the entry's ``due_at`` in every process
# that imported this module — API included — so on a deployment that
# restarts more than once a day, a daily job never fired at all (BUG-70).
