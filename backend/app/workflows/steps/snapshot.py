"""Snapshot step: create a Proxmox VM snapshot before an update run."""

import logging
import time
from typing import Any

logger = logging.getLogger(__name__)


async def create_snapshot(
    proxmox_client: Any,
    pve_node: str,
    vmid: int,
    run_id: int,
) -> str:
    """Create a pre-update VM snapshot and wait for the task to complete.

    The snapshot name embeds the workflow run ID and a Unix timestamp so that
    it is both human-readable and unique across concurrent runs.

    Args:
        proxmox_client: Authenticated
            :class:`~app.proxmox.client.ProxmoxClient` instance.
        pve_node: Proxmox node name that owns the VM.
        vmid: VM identifier.
        run_id: Parent :class:`~app.workflows.models.WorkflowRun` ID, used as
            part of the snapshot name.

    Returns:
        The snapshot name string (e.g. ``"labdog-42-1711234567"``).

    Raises:
        :class:`~app.proxmox.client.ProxmoxError`: If the Proxmox task fails
            or times out.
    """
    name = f"labdog-{run_id}-{int(time.time())}"
    logger.info(
        "snapshot: creating snapshot %r for vmid %d on %s",
        name,
        vmid,
        pve_node,
    )

    upid: str = await proxmox_client.create_snapshot(
        pve_node,
        vmid,
        name,
        description="LabDog pre-update snapshot",
    )
    await proxmox_client.wait_for_task(pve_node, upid)

    logger.info(
        "snapshot: snapshot %r created successfully for vmid %d on %s",
        name,
        vmid,
        pve_node,
    )
    return name
