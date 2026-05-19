"""Celery task: run a single ScanConfig against its configured CIDRs.

T4 -- Per-config runner
T6 -- Advisory-lock rate-limit (max 4 concurrent scan runs across all workers)

The task is registered as ``scans.run_config`` on the ``long_running`` queue.
It is dispatched by the T3 scheduler; do NOT call it from API endpoints.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging

from app.db import task_session
from app.tasks import celery_app

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Advisory-lock key derivation
# ---------------------------------------------------------------------------
# hash("scans.run_config") gives a stable 64-bit integer base; adding
# (config_id % 4) buckets all configs into 4 lock slots so at most 4 scan
# runs can execute concurrently across the entire worker fleet.
_TASK_HASH = int(hashlib.sha256(b"scans.run_config").hexdigest(), 16) % (2**62)


def _advisory_lock_key(config_id: int) -> int:
    """Return the pg_advisory_xact_lock key for *config_id*.

    Args:
        config_id: ScanConfig primary key.

    Returns:
        A non-negative integer that fits in a PostgreSQL bigint and maps
        all config_ids with the same (id % 4) remainder to the same lock.
    """
    return (_TASK_HASH + (config_id % 4)) % (2**63 - 1)


# ---------------------------------------------------------------------------
# Celery task
# ---------------------------------------------------------------------------


@celery_app.task(name="scans.run_config", bind=True, max_retries=0, queue="long_running")
def run_scan_config(self, config_id: int) -> dict:
    """Run a scan config end-to-end.

    Flow
    ----
    a. Load ScanConfig; return early if missing or disabled.
    b. Acquire pg_advisory_xact_lock keyed on hash(task_name) + config_id % 4
       -- caps concurrent scan runs at 4 regardless of worker count.
    c. Mark last_run_status="running", flush (lock held for full scan).
    d. Load + decrypt the SSH key.
    e. TCP-sweep every CIDR; collect (ip, status) hits.
    f. Dedup against existing hosts table.
    g. SSH-verify remaining hits.
    h. Branch on auto_add:
       - True  -> insert Host + HostGroupMembership rows; emit audit event.
       - False -> upsert PendingHost rows; emit audit event.
    i. Update last_run_* counters and status="ok", commit.
    j. On any exception: set status="error" in a separate session, re-raise.

    Args:
        config_id: Primary key of the ``ScanConfig`` row to run.

    Returns:
        A summary dict with ``hosts_added`` and ``hosts_pending`` counts.
    """
    return asyncio.run(_async_run(config_id))


async def _async_run(config_id: int) -> dict:  # noqa: C901 -- complexity is intentional
    """Async implementation of :func:`run_scan_config`.

    Args:
        config_id: ScanConfig primary key.

    Returns:
        Dict with ``hosts_added``, ``hosts_pending``, and optionally
        ``skipped`` (True) if the config was missing or disabled.
    """
    from datetime import UTC, datetime

    import asyncssh
    from sqlalchemy import insert as sa_insert
    from sqlalchemy import select, text

    from app.audit.logger import log_action
    from app.crypto.encryption import decrypt_ssh_key
    from app.crypto.key_management import get_master_key
    from app.discovery.scanner import scan_network
    from app.discovery.verify import placeholder_hostname, verify_ssh
    from app.models.host import Host, HostGroupMembership
    from app.models.scan_config import ScanConfig
    from app.models.ssh_key import SSHKey

    lock_key = _advisory_lock_key(config_id)

    try:
        async with task_session() as db:
            # ---- a. Load config -----------------------------------------
            cfg_result = await db.execute(select(ScanConfig).where(ScanConfig.id == config_id))
            config = cfg_result.scalar_one_or_none()
            if config is None or not config.enabled:
                return {"hosts_added": 0, "hosts_pending": 0, "skipped": True}

            # ---- b. Advisory lock (held for the entire scan) ------------
            # SQLAlchemy autobegin starts a transaction on the first execute,
            # making pg_advisory_xact_lock valid here.  The lock is released
            # when the session's transaction commits at the end of the block.
            await db.execute(text("SELECT pg_advisory_xact_lock(:key)").bindparams(key=lock_key))

            # ---- c. Mark running ----------------------------------------
            config.last_run_status = "running"
            config.last_run_at = datetime.now(UTC)
            await db.flush()  # visible to other readers; lock still held

            # ---- d. Load + decrypt SSH key ------------------------------
            key_row = (
                await db.execute(select(SSHKey).where(SSHKey.id == config.ssh_key_id))
            ).scalar_one()
            master_key = get_master_key()
            private_pem = decrypt_ssh_key(key_row.encrypted_private_key, master_key)
            imported_key = asyncssh.import_private_key(private_pem)

            # ---- e. TCP sweep all CIDRs ---------------------------------
            all_hits: list[tuple[str, str]] = []
            for cidr in config.cidrs:
                hits = await scan_network(cidr, port=config.ssh_port)
                all_hits.extend(hits)

            # Unique IPs (multiple CIDRs may overlap).
            hit_ips: list[str] = list({ip for ip, _status in all_hits})

            # ---- f. Dedup against existing hosts ------------------------
            if hit_ips:
                existing_result = await db.execute(
                    select(Host.ip_address).where(Host.ip_address.in_(hit_ips))
                )
                existing_ips = {row[0] for row in existing_result.all()}
            else:
                existing_ips = set()

            new_ips = [ip for ip in hit_ips if ip not in existing_ips]

            # ---- g. SSH-verify remaining hits ---------------------------
            verified: list[tuple[str, str]] = []  # (ip, hostname)
            unverified: list[tuple[str, str]] = []  # (ip, ssh_error)

            for ip in new_ips:
                ok, hostname, _source_ip, ssh_err = await verify_ssh(
                    ip,
                    port=config.ssh_port,
                    username=key_row.ssh_user,
                    imported_key=imported_key,
                )
                if not ok:
                    unverified.append((ip, ssh_err or "unknown error"))
                    continue
                # SSH succeeded. If the remote didn't return a real
                # hostname, use the canonical placeholder so the host
                # can still be added; collect_state will replace it
                # once the remote starts answering.
                if hostname is None:
                    hostname = placeholder_hostname(ip)
                # Ensure hostname uniqueness across the DB.
                base_hn = hostname
                suffix = 1
                while True:
                    hn_check = await db.execute(select(Host).where(Host.hostname == hostname))
                    if not hn_check.scalar_one_or_none():
                        break
                    hostname = f"{base_hn}-{suffix}"
                    suffix += 1
                verified.append((ip, hostname))

            hosts_added = 0
            hosts_pending = 0

            # ---- h. Branch on auto_add ----------------------------------
            auto_added_host_ids: list[int] = []
            if config.auto_add:
                for ip, hostname in verified:
                    host = Host(
                        hostname=hostname,
                        ip_address=ip,
                        ssh_port=config.ssh_port,
                        ssh_user=key_row.ssh_user,
                        ssh_key_id=config.ssh_key_id,
                    )
                    db.add(host)
                    await db.flush()  # populate host.id before inserting memberships

                    if config.default_group_ids:
                        await db.execute(
                            sa_insert(HostGroupMembership),
                            [
                                {"host_id": host.id, "group_id": gid}
                                for gid in config.default_group_ids
                            ],
                        )

                    await log_action(
                        db,
                        action="discovery.auto_add",
                        entity_type="scan_config",
                        entity_id=config_id,
                        after_state={"ip": ip, "hostname": hostname},
                    )
                    hosts_added += 1
                    auto_added_host_ids.append(host.id)

                # Unverified hits go to pending even in auto_add mode so
                # operators can see what was discovered but could not be added.
                for ip, ssh_err in unverified:
                    await _upsert_pending(
                        db,
                        config_id=config_id,
                        ip=ip,
                        hostname=None,
                        ssh_verified=False,
                        ssh_error=ssh_err,
                    )
                    await log_action(
                        db,
                        action="discovery.pending",
                        entity_type="scan_config",
                        entity_id=config_id,
                        after_state={"ip": ip, "ssh_error": ssh_err},
                    )
                    hosts_pending += 1

            else:
                # auto_add=False -- queue everything for manual review.
                for ip, hostname in verified:
                    await _upsert_pending(
                        db,
                        config_id=config_id,
                        ip=ip,
                        hostname=hostname,
                        ssh_verified=True,
                        ssh_error=None,
                    )
                    await log_action(
                        db,
                        action="discovery.pending",
                        entity_type="scan_config",
                        entity_id=config_id,
                        after_state={"ip": ip, "hostname": hostname},
                    )
                    hosts_pending += 1

                for ip, ssh_err in unverified:
                    await _upsert_pending(
                        db,
                        config_id=config_id,
                        ip=ip,
                        hostname=None,
                        ssh_verified=False,
                        ssh_error=ssh_err,
                    )
                    await log_action(
                        db,
                        action="discovery.pending",
                        entity_type="scan_config",
                        entity_id=config_id,
                        after_state={"ip": ip, "ssh_error": ssh_err},
                    )
                    hosts_pending += 1

            # ---- i. Update last_run counters (commit releases the lock) -
            config.last_run_hosts_added = hosts_added
            config.last_run_hosts_pending = hosts_pending
            config.last_run_status = "ok"
            config.last_run_error = None
            await db.commit()

            # Kick off OS-facts collection for any auto-added hosts so their
            # os_codename is populated before the operator opens them.
            # Best-effort: broker hiccups must not fail the scan.
            for hid in auto_added_host_ids:
                try:
                    celery_app.send_task("app.tasks.facts.collect_host_facts", args=[hid])
                except Exception:
                    logger.warning(
                        "scan_run: could not enqueue collect_host_facts for host %d",
                        hid,
                    )

            return {"hosts_added": hosts_added, "hosts_pending": hosts_pending}

    except Exception as exc:
        # ---- j. Error path ----------------------------------------------
        # Write the error status in a fresh session so the main session's
        # rollback does not discard it.
        async with task_session() as err_db:
            from sqlalchemy import select

            from app.models.scan_config import ScanConfig

            err_cfg = (
                await err_db.execute(select(ScanConfig).where(ScanConfig.id == config_id))
            ).scalar_one_or_none()
            if err_cfg is not None:
                err_cfg.last_run_status = "error"
                err_cfg.last_run_error = str(exc)
                await err_db.commit()
        raise


async def _upsert_pending(
    db,
    *,
    config_id: int,
    ip: str,
    hostname: str | None,
    ssh_verified: bool,
    ssh_error: str | None,
) -> None:
    """Insert or update a PendingHost row for (scan_config_id, ip_address).

    ON CONFLICT DO UPDATE ensures that running the same config twice does not
    produce duplicate rows -- it refreshes the existing data instead.

    Args:
        db: Active async SQLAlchemy session.
        config_id: Parent ScanConfig primary key.
        ip: Discovered IP address string.
        hostname: Resolved hostname, or None when unavailable.
        ssh_verified: Whether SSH verification succeeded.
        ssh_error: Human-readable error on SSH failure, or None on success.
    """
    from sqlalchemy.dialects.postgresql import insert as pg_insert

    from app.models.scan_config import PendingHost

    stmt = (
        pg_insert(PendingHost)
        .values(
            scan_config_id=config_id,
            ip_address=ip,
            hostname=hostname,
            ssh_verified=ssh_verified,
            ssh_error=ssh_error,
        )
        .on_conflict_do_update(
            index_elements=["scan_config_id", "ip_address"],
            set_={
                "hostname": hostname,
                "ssh_verified": ssh_verified,
                "ssh_error": ssh_error,
            },
        )
    )
    await db.execute(stmt)
