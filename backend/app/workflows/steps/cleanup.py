"""Cleanup step: delete a Proxmox snapshot unbork took.

Deleting needs the guest's config lock, and right after a rollback that
lock is not free. The rollback kills the running QEMU process, and
Proxmox's ``qmeventd`` then runs its own cleanup for the guest: it takes the
config lock as soon as ``qmstart`` releases it and holds it for about 30
seconds, waiting for a process that has already been started again. A
delete sent in that window waits only Proxmox's default 10 s for the lock
and fails with ``can't lock file '/var/lock/qemu-server/lock-<vmid>.conf' -
got timeout``. After a rollback that happened every time, and each failure
left a full VM snapshot behind (BUG-168). Proxmox exposes no qmeventd state
to wait on, so :func:`delete_snapshot` retries a lock error with backoff.
"""

import asyncio
import logging
import re
import time
from typing import Any

from app.workflows.steps.snapshot import is_lock_error

logger = logging.getLogger(__name__)

#: How long a delete keeps retrying while the guest is locked. qmeventd
#: holds the lock about 30 s after a rollback; two minutes also covers a
#: slow node and a short lock held by something else.
DELETE_LOCK_WAIT_SECONDS = 120

#: Pauses between attempts while locked; the last one repeats.
DELETE_LOCK_BACKOFF_SECONDS = (5, 10, 20, 30)

#: ``snapshot 'labdog-ai-42-1762500000' does not exist``, from the qemu and
#: lxc snapshot code alike. Matched on the snapshot wording only: a
#: missing *guest* (``Configuration file ... does not exist``) can mean the
#: mapping points at the wrong node while the snapshot is still there.
_MISSING_SNAPSHOT = re.compile(r"snapshot '[^']*' does not exist", re.IGNORECASE)


def is_missing_snapshot_error(exc: BaseException) -> bool:
    """True when ``exc`` says the snapshot itself is already gone."""
    return _MISSING_SNAPSHOT.search(str(exc)) is not None


async def delete_snapshot(
    proxmox_client: Any,
    pve_node: str,
    vmid: int,
    snapshot_name: str,
    vm_type: str = "qemu",
    *,
    lock_wait_seconds: float = DELETE_LOCK_WAIT_SECONDS,
) -> dict[str, Any]:
    """Delete a VM snapshot and wait for the task to complete.

    A lock error (the guest's config is locked by another operation) is
    retried with backoff for up to ``lock_wait_seconds``; any other error
    is raised at once.

    Args:
        proxmox_client: Authenticated
            :class:`~app.proxmox.client.ProxmoxClient` instance.
        pve_node: Proxmox node name that owns the VM.
        vmid: VM identifier.
        snapshot_name: Name of the snapshot to delete.
        lock_wait_seconds: How long to keep retrying a lock error. ``0``
            tries once.

    Returns:
        A dict of the form::

            {"deleted": True, "snapshot_name": "<name>"}

    Raises:
        :class:`~app.proxmox.client.ProxmoxError`: If the Proxmox task fails
            or times out, or the guest is still locked after
            ``lock_wait_seconds``.
    """
    logger.info(
        "cleanup: deleting snapshot %r for vmid %d on %s",
        snapshot_name,
        vmid,
        pve_node,
    )

    started = time.monotonic()
    attempt = 0
    while True:
        try:
            upid: str = await proxmox_client.delete_snapshot(
                pve_node, vmid, snapshot_name, vm_type=vm_type
            )
            await proxmox_client.wait_for_task(pve_node, upid)
            break
        except Exception as exc:
            if not is_lock_error(exc):
                raise
            remaining = lock_wait_seconds - (time.monotonic() - started)
            if remaining <= 0:
                raise
            pause = min(
                DELETE_LOCK_BACKOFF_SECONDS[min(attempt, len(DELETE_LOCK_BACKOFF_SECONDS) - 1)],
                remaining,
            )
            attempt += 1
            logger.info(
                "cleanup: vmid %d on %s is locked (%s); retrying the delete of %r in %ss",
                vmid,
                pve_node,
                exc,
                snapshot_name,
                pause,
            )
            await asyncio.sleep(pause)

    logger.info(
        "cleanup: snapshot %r deleted for vmid %d on %s",
        snapshot_name,
        vmid,
        pve_node,
    )
    return {"deleted": True, "snapshot_name": snapshot_name}
