"""Cleanup step: delete the pre-update Proxmox snapshot after a successful run."""

import logging
from typing import Any

logger = logging.getLogger(__name__)


async def delete_snapshot(
    proxmox_client: Any,
    pve_node: str,
    vmid: int,
    snapshot_name: str,
    vm_type: str = "qemu",
) -> dict[str, Any]:
    """Delete a VM snapshot and wait for the task to complete.

    Called at the end of a successful workflow run to remove the pre-update
    snapshot that was created by the snapshot step.

    Args:
        proxmox_client: Authenticated
            :class:`~app.proxmox.client.ProxmoxClient` instance.
        pve_node: Proxmox node name that owns the VM.
        vmid: VM identifier.
        snapshot_name: Name of the snapshot to delete.

    Returns:
        A dict of the form::

            {"deleted": True, "snapshot_name": "<name>"}

    Raises:
        :class:`~app.proxmox.client.ProxmoxError`: If the Proxmox task fails
            or times out.
    """
    logger.info(
        "cleanup: deleting snapshot %r for vmid %d on %s",
        snapshot_name,
        vmid,
        pve_node,
    )

    upid: str = await proxmox_client.delete_snapshot(pve_node, vmid, snapshot_name, vm_type=vm_type)
    await proxmox_client.wait_for_task(pve_node, upid)

    logger.info(
        "cleanup: snapshot %r deleted for vmid %d on %s",
        snapshot_name,
        vmid,
        pve_node,
    )
    return {"deleted": True, "snapshot_name": snapshot_name}
