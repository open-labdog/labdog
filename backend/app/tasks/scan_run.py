"""Celery task: run a single ScanConfig against its configured CIDRs.

T4 -- Per-config runner
T6 -- Advisory-lock rate-limit (max 4 concurrent scan runs across all workers)

The task is registered as ``scans.run_config`` on the ``long_running`` queue.
It is dispatched by the T3 scheduler and, as a manual run, by
``POST /api/scans/{id}/run``.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging

from app.db import task_session, with_task_engine
from app.tasks import celery_app

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Advisory-lock key derivation
# ---------------------------------------------------------------------------
# hash("scans.run_config") gives a stable 64-bit integer base; adding
# (config_id % 4) buckets all configs into 4 lock slots so at most 4 scan
# runs can execute concurrently across the entire worker fleet.
_TASK_HASH = int(hashlib.sha256(b"scans.run_config").hexdigest(), 16) % (2**62)


#: Namespace for the per-config "a run of this config is in progress" lock
#: (two-int advisory lock form, so it cannot collide with the bucket keys).
_RUN_LOCK_NAMESPACE = int(hashlib.sha256(b"scans.run_config.single").hexdigest(), 16) % (2**31)


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
def run_scan_config(self, config_id: int, is_manual: bool = False) -> dict:
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
    f2. For scheduled runs (is_manual=False), also exclude dismissed hosts.
    g. SSH-verify remaining hits.
    h. Branch on auto_add:
       - True  -> insert Host + HostGroupMembership rows; emit audit event.
       - False -> upsert PendingHost rows; emit audit event.
    i. Update last_run_* counters and status="ok", commit.
    j. On any exception: set status="error" in a separate session, re-raise.

    Args:
        config_id: Primary key of the ``ScanConfig`` row to run.
        is_manual: When True (manual run triggered via API), dismissed hosts
            are included so the operator can re-review them.  Scheduled runs
            (default False) silently skip dismissed IPs.

    Returns:
        A summary dict with ``hosts_added`` and ``hosts_pending`` counts,
        plus ``config_id`` so ``GET /api/scans/{id}/runs/{task_id}`` can
        check the task id belongs to that config.
    """
    return {
        **asyncio.run(with_task_engine(_async_run(config_id, is_manual=is_manual))),
        "config_id": config_id,
    }


async def _async_run(config_id: int, is_manual: bool = False) -> dict:
    """Async implementation of :func:`run_scan_config`.

    Args:
        config_id: ScanConfig primary key.

    Returns:
        Dict with ``hosts_added``, ``hosts_pending``, and optionally
        ``skipped`` (True) if the config was missing or disabled.
    """
    lock_key = _advisory_lock_key(config_id)

    try:
        async with task_session() as db:
            skipped = {"hosts_added": 0, "hosts_pending": 0, "skipped": True}

            if not await _take_run_locks(db, config_id, lock_key):
                return skipped
            config = await _load_runnable_config(db, config_id, is_manual=is_manual)
            if config is None:
                return skipped

            await _mark_running(config, config_id)
            key_row, imported_key = await _load_scan_key(db, config)
            hit_ips = await _sweep_cidrs(config, config_id)
            new_ips = await _drop_known_ips(db, hit_ips, config_id, is_manual=is_manual)
            verified, unverified = await _verify_candidates(
                db, new_ips, config, key_row, imported_key
            )

            hosts_added = 0
            hosts_pending = 0

            # ---- h. Branch on auto_add ----------------------------------
            auto_added_host_ids: list[int] = []
            if config.auto_add:
                auto_added_host_ids = await _add_verified_hosts(
                    db, verified, config, config_id, key_row
                )
                hosts_added = len(auto_added_host_ids)
            else:
                # auto_add=False -- queue everything for manual review.
                hosts_pending += await _queue_verified_for_review(db, verified, config_id)
            # Unverified hits go to pending even in auto_add mode so
            # operators can see what was discovered but could not be added.
            hosts_pending += await _queue_unverified_for_review(db, unverified, config_id)

            # ---- i. Update last_run counters (commit releases the lock) -
            config.last_run_hosts_added = hosts_added
            config.last_run_hosts_pending = hosts_pending
            config.last_run_status = "ok"
            config.last_run_error = None
            await db.commit()

            _enqueue_facts_collection(auto_added_host_ids)

            return {"hosts_added": hosts_added, "hosts_pending": hosts_pending}

    except Exception as exc:
        await _record_run_error(config_id, exc)
        raise


async def _take_run_locks(db, config_id: int, lock_key: int) -> bool:
    """Take this config's run lock and a concurrency slot; False when
    another run of the config holds the first."""
    from sqlalchemy import text

    # ---- a. One run per config (BUG-136) -------------------------
    # The scheduler ticks every 60 s and saw the config as still
    # due for as long as a scan ran, because the "running" mark was
    # only flushed. Each extra dispatch blocked on the lock below,
    # holding a worker slot and a connection, then scanned again.
    # A try-lock per config turns those into an immediate skip.
    got = await db.scalar(
        text("SELECT pg_try_advisory_xact_lock(:ns, :id)").bindparams(
            ns=_RUN_LOCK_NAMESPACE, id=config_id
        )
    )
    if not got:
        logger.info("scan_run: config %d is already running; skipping", config_id)
        return False

    # ---- b. Concurrency cap (held for the entire scan) ----------
    # SQLAlchemy autobegin started the transaction above, making
    # pg_advisory_xact_lock valid here. Released at the final commit.
    await db.execute(text("SELECT pg_advisory_xact_lock(:key)").bindparams(key=lock_key))
    return True


async def _load_runnable_config(db, config_id: int, *, is_manual: bool):
    """Return the ScanConfig, or None when it is missing, disabled, or
    (for a scheduled run) no longer due."""
    from datetime import UTC, datetime

    from sqlalchemy import select

    from app.models.scan_config import ScanConfig

    # ---- c. Load config, after the locks so it is current --------
    cfg_result = await db.execute(select(ScanConfig).where(ScanConfig.id == config_id))
    config = cfg_result.scalar_one_or_none()
    if config is None or not config.enabled:
        return None
    if not is_manual:
        from app.cron_walk import get_schedule_timezone
        from app.tasks.scan_schedule import _is_due

        # A run queued behind the previous one finds it already
        # done: re-check rather than scan again straight away.
        if not _is_due(config, datetime.now(UTC), await get_schedule_timezone(db)):
            return None
    return config


async def _mark_running(config, config_id: int) -> None:
    """Mark the config running, committed so other sessions see it."""
    from datetime import UTC, datetime

    from sqlalchemy import update as sa_update

    from app.models.scan_config import ScanConfig

    # ---- d. Mark running, committed so other sessions see it -----
    started_at = datetime.now(UTC)
    async with task_session() as mark_db:
        await mark_db.execute(
            sa_update(ScanConfig)
            .where(ScanConfig.id == config_id)
            .values(last_run_status="running", last_run_at=started_at)
        )
        await mark_db.commit()
    config.last_run_status = "running"
    config.last_run_at = started_at


async def _load_scan_key(db, config):
    """Return the config's SSHKey row and its decrypted, imported key."""
    import asyncssh
    from sqlalchemy import select

    from app.crypto.encryption import decrypt_ssh_key
    from app.crypto.key_management import get_master_key
    from app.models.ssh_key import SSHKey

    # ---- d. Load + decrypt SSH key ------------------------------
    key_row = (await db.execute(select(SSHKey).where(SSHKey.id == config.ssh_key_id))).scalar_one()
    master_key = get_master_key()
    private_pem = decrypt_ssh_key(key_row.encrypted_private_key, master_key)
    imported_key = asyncssh.import_private_key(private_pem)
    return key_row, imported_key


async def _sweep_cidrs(config, config_id: int) -> list[str]:
    """TCP-sweep every allowed CIDR and return the unique responding IPs."""
    from app.discovery.scanner import scan_network, validate_cidr_against_blocklist

    # ---- e. TCP sweep all CIDRs ---------------------------------
    # Defence-in-depth: re-validate each CIDR against the blocklist
    # in case a row was inserted before this guard landed or was
    # written directly into the DB bypassing the API schema.
    all_hits: list[tuple[str, str]] = []
    for cidr in config.cidrs:
        try:
            validate_cidr_against_blocklist(cidr)
        except ValueError as blocked_err:
            logger.warning(
                "scan_run: skipping blocked CIDR %r in config %d: %s",
                cidr,
                config_id,
                blocked_err,
            )
            continue
        hits = await scan_network(cidr, port=config.ssh_port)
        all_hits.extend(hits)

    # Unique IPs (multiple CIDRs may overlap).
    return list({ip for ip, _status in all_hits})


async def _drop_known_ips(db, hit_ips: list[str], config_id: int, *, is_manual: bool) -> list[str]:
    """Drop IPs that are already hosts and, for a scheduled run, IPs the
    operator dismissed for this config."""
    from sqlalchemy import select

    from app.models.host import Host

    # ---- f. Dedup against existing hosts ------------------------
    if hit_ips:
        existing_result = await db.execute(
            select(Host.ip_address).where(Host.ip_address.in_(hit_ips))
        )
        existing_ips = {row[0] for row in existing_result.all()}
    else:
        existing_ips = set()

    new_ips = [ip for ip in hit_ips if ip not in existing_ips]

    # ---- f2. Exclude dismissed hosts (scheduled scans only) -----
    if not is_manual and new_ips:
        from app.models.scan_config import DismissedHost  # noqa: PLC0415

        dismissed_result = await db.execute(
            select(DismissedHost.ip_address).where(
                DismissedHost.scan_config_id == config_id,
                DismissedHost.ip_address.in_(new_ips),
            )
        )
        dismissed_ips = {row[0] for row in dismissed_result.all()}
        if dismissed_ips:
            logger.debug(
                "scan_run: config %d suppressing %d dismissed IP(s)",
                config_id,
                len(dismissed_ips),
            )
            new_ips = [ip for ip in new_ips if ip not in dismissed_ips]
    return new_ips


async def _verify_candidates(
    db, new_ips: list[str], config, key_row, imported_key
) -> tuple[list[tuple[str, str, str | None]], list[tuple[str, str]]]:
    """SSH-verify each IP. Returns ``(verified, unverified)``: verified as
    ``(ip, unique hostname, host key entry)``, unverified as ``(ip, error)``."""
    from app.discovery.verify import placeholder_hostname, verify_ssh

    # ---- g. SSH-verify remaining hits ---------------------------
    verified: list[tuple[str, str, str | None]] = []  # (ip, hostname, key_entry)
    unverified: list[tuple[str, str]] = []  # (ip, ssh_error)

    for ip in new_ips:
        ok, hostname, _source_ip, ssh_err, ssh_host_key_entry = await verify_ssh(
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
        hostname = await _unique_hostname(db, hostname)
        verified.append((ip, hostname, ssh_host_key_entry))
    return verified, unverified


async def _unique_hostname(db, hostname: str) -> str:
    """Ensure hostname uniqueness across the DB: ``name``, else
    ``name-1``, ``name-2``, ..."""
    from sqlalchemy import select

    from app.models.host import Host

    base_hn = hostname
    suffix = 1
    while True:
        hn_check = await db.execute(select(Host).where(Host.hostname == hostname))
        if not hn_check.scalar_one_or_none():
            break
        hostname = f"{base_hn}-{suffix}"
        suffix += 1
    return hostname


async def _add_verified_hosts(
    db, verified: list[tuple[str, str, str | None]], config, config_id: int, key_row
) -> list[int]:
    """Insert a Host (plus default group memberships and an audit row) per
    verified hit; return the new host ids."""
    from sqlalchemy import insert as sa_insert

    from app.audit.logger import log_action
    from app.models.host import Host, HostGroupMembership

    auto_added_host_ids: list[int] = []
    for ip, hostname, ssh_host_key_entry in verified:
        host = Host(
            hostname=hostname,
            ip_address=ip,
            ssh_port=config.ssh_port,
            ssh_user=key_row.ssh_user,
            ssh_key_id=config.ssh_key_id,
            ssh_host_key_entry=ssh_host_key_entry,
        )
        db.add(host)
        await db.flush()  # populate host.id before inserting memberships

        if config.default_group_ids:
            await db.execute(
                sa_insert(HostGroupMembership),
                [{"host_id": host.id, "group_id": gid} for gid in config.default_group_ids],
            )

        await log_action(
            db,
            action="discovery.auto_add",
            entity_type="scan_config",
            entity_id=config_id,
            after_state={"ip": ip, "hostname": hostname},
        )
        auto_added_host_ids.append(host.id)
    return auto_added_host_ids


async def _queue_verified_for_review(
    db, verified: list[tuple[str, str, str | None]], config_id: int
) -> int:
    """Upsert a verified PendingHost (plus an audit row) per hit; return
    how many were queued."""
    from app.audit.logger import log_action

    for ip, hostname, _key_entry in verified:
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
    return len(verified)


async def _queue_unverified_for_review(
    db, unverified: list[tuple[str, str]], config_id: int
) -> int:
    """Upsert an unverified PendingHost (plus an audit row) per hit;
    return how many were queued."""
    from app.audit.logger import log_action

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
    return len(unverified)


def _enqueue_facts_collection(host_ids: list[int]) -> None:
    # Kick off OS-facts collection for any auto-added hosts so their
    # os_codename is populated before the operator opens them.
    # Best-effort: broker hiccups must not fail the scan.
    for hid in host_ids:
        try:
            celery_app.send_task("app.tasks.facts.collect_host_facts", args=[hid])
        except Exception:
            logger.warning(
                "scan_run: could not enqueue collect_host_facts for host %d",
                hid,
            )


async def _record_run_error(config_id: int, exc: Exception) -> None:
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
