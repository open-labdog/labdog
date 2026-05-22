"""Rollback step: restore a VM to its pre-update snapshot after a failed run."""

import asyncio
import logging
import time
from typing import Any

from app.ssh_utils import ssh_connect_host

logger = logging.getLogger(__name__)

_SSH_POLL_INTERVAL = 10  # seconds between SSH connectivity attempts
_SSH_WAIT_TIMEOUT = 300  # seconds before giving up on SSH recovery


async def rollback_to_snapshot(
    proxmox_client: Any,
    pve_node: str,
    vmid: int,
    snapshot_name: str,
    host: Any,
    ssh_key_path: str,
    db: Any,
) -> dict[str, Any]:
    """Roll a VM back to its pre-update snapshot and wait for SSH to recover.

    Performs the following steps in order:

    1. Issues a Proxmox rollback task and waits for it to complete.
    2. Starts the VM (rollback leaves the VM stopped) and waits for the
       start task to complete.
    3. Polls SSH connectivity every 10 seconds until the host responds or
       the 300-second deadline expires.
    4. Marks the host record as ``out_of_sync`` in the database and commits.

    Args:
        proxmox_client: Authenticated
            :class:`~app.proxmox.client.ProxmoxClient` instance.
        pve_node: Proxmox node name that owns the VM.
        vmid: VM identifier.
        snapshot_name: Name of the snapshot to restore.
        host: Host ORM object with ``ip_address``, ``ssh_port``,
            ``ssh_user``, and ``ssh_host_key_entry`` attributes.
        ssh_key_path: Absolute path to the decrypted SSH private key on
            tmpfs.
        db: Active async SQLAlchemy session used to update the host
            sync status and persist TOFU key.

    Returns:
        ``{"success": True}`` on success, or
        ``{"success": False, "error": "<description>"}`` when the SSH
        wait times out or any step raises an unexpected exception.
    """
    try:
        # ------------------------------------------------------------------
        # 1. Roll back to the snapshot
        # ------------------------------------------------------------------
        logger.info(
            "rollback: restoring vmid %d on %s to snapshot %r",
            vmid,
            pve_node,
            snapshot_name,
        )
        upid: str = await proxmox_client.rollback_snapshot(pve_node, vmid, snapshot_name)
        await proxmox_client.wait_for_task(pve_node, upid)
        logger.info(
            "rollback: rollback task completed for vmid %d on %s",
            vmid,
            pve_node,
        )

        # ------------------------------------------------------------------
        # 2. Start the VM (Proxmox rollback leaves the VM stopped)
        # ------------------------------------------------------------------
        logger.info(
            "rollback: starting vmid %d on %s after rollback",
            vmid,
            pve_node,
        )
        upid = await proxmox_client.start_vm(pve_node, vmid)
        await proxmox_client.wait_for_task(pve_node, upid)
        logger.info(
            "rollback: VM start task completed for vmid %d on %s",
            vmid,
            pve_node,
        )

        # ------------------------------------------------------------------
        # 3. Poll SSH until the host responds (max _SSH_WAIT_TIMEOUT seconds)
        # ------------------------------------------------------------------
        deadline = time.monotonic() + _SSH_WAIT_TIMEOUT
        ssh_recovered = False

        while time.monotonic() < deadline:
            await asyncio.sleep(_SSH_POLL_INTERVAL)
            try:
                async with ssh_connect_host(
                    host,
                    db,
                    client_keys=[ssh_key_path],
                    connect_timeout=8,
                ):
                    pass
                ssh_recovered = True
                logger.info(
                    "rollback: SSH recovered for host %s after rollback",
                    host.ip_address,
                )
                break
            except Exception:
                logger.debug(
                    "rollback: SSH not yet reachable for host %s, retrying...",
                    host.ip_address,
                )

        if not ssh_recovered:
            error_msg = (
                f"SSH did not recover within {_SSH_WAIT_TIMEOUT}s after rollback "
                f"for host {host.ip_address}"
            )
            logger.error("rollback: %s", error_msg)
            # Still mark host out_of_sync and commit before returning failure
            await _mark_out_of_sync(host, db)
            return {"success": False, "error": error_msg}

        # ------------------------------------------------------------------
        # 4. Mark host as out_of_sync
        # ------------------------------------------------------------------
        await _mark_out_of_sync(host, db)

        return {"success": True}

    except Exception as exc:
        logger.exception(
            "rollback: unexpected error during rollback for host %s: %s",
            getattr(host, "ip_address", "unknown"),
            exc,
        )
        return {"success": False, "error": str(exc)}


async def _mark_out_of_sync(host: Any, db: Any) -> None:
    """Set the host sync_status to out_of_sync and flush to the DB.

    Args:
        host: Host ORM object.
        db: Active async SQLAlchemy session.
    """
    from app.models.host import SyncStatus

    host.sync_status = SyncStatus.out_of_sync
    await db.flush()
    logger.debug(
        "rollback: host %s marked out_of_sync",
        getattr(host, "ip_address", "unknown"),
    )
