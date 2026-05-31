"""Orphaned snapshot detection and cleanup for LabDog update workflows.

Snapshots created by the workflow follow the naming convention::

    labdog-{run_id}-{unix_timestamp}

A snapshot is considered orphaned when one of:

* The parsed ``run_id`` refers to an ``ActionRun`` row that is in a terminal
  state (``succeeded``/``failed``/``partial``/``cancelled``) — safe to
  delete immediately regardless of age.
* The parsed ``run_id`` does not correspond to any known run AND the
  embedded timestamp is older than the configured max-age threshold.

The age threshold is only a safety net for snapshots whose origin we
cannot identify; snapshots tied to a finished run never need to wait.
"""

import logging
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.crypto.encryption import decrypt_ssh_key
from app.crypto.key_management import get_master_key
from app.models.action_run import ActionHostRun, ActionRun
from app.proxmox.client import ProxmoxClient, ProxmoxError
from app.proxmox.models import ProxmoxNode
from app.settings_service import get_setting_typed

logger = logging.getLogger(__name__)

_SNAPSHOT_PREFIX = "labdog-"
# ActionHostRun status strings that indicate the snapshot is still in use.
_ACTIVE_STATUSES = ("queued", "running")
# ActionRun status strings that indicate the run has reached a final state.
_TERMINAL_RUN_STATUSES = ("succeeded", "failed", "partial", "cancelled")


def _parse_snapshot_name(snapshot_name: str) -> tuple[int, datetime] | None:
    """Extract the run_id and timestamp embedded in a labdog snapshot name.

    Returns ``(run_id, created_at)`` or ``None`` when the name does not
    match the ``labdog-{run_id}-{unix_ts}`` shape.
    """
    parts = snapshot_name.split("-")
    if len(parts) < 3:
        return None
    try:
        run_id = int(parts[-2])
        ts = int(parts[-1])
        return run_id, datetime.fromtimestamp(ts, tz=UTC)
    except (ValueError, OSError):
        return None


async def find_orphaned_snapshots(
    db: AsyncSession,
    max_age_hours: int = 24,
) -> list[dict]:
    """Discover labdog snapshots that are no longer associated with an active run.

    Walks every configured Proxmox node, enumerates QEMU VMs **and** LXC
    containers on each PVE node, and inspects their snapshots. A snapshot
    whose name starts with ``labdog-`` is flagged as orphaned when:

    1. No ``ActionHostRun`` with status ``queued``/``running`` references it,
       AND
    2. Either the parsed ``run_id`` refers to a terminal ``ActionRun``
       (safe to delete now), or the embedded timestamp is older than
       ``max_age_hours`` (safety net for unknown-origin snapshots).

    Returns a list of dicts with keys ``pve_node``, ``vmid``,
    ``vm_type`` (``"qemu"`` or ``"lxc"``), ``snapshot_name``, ``age_hours``,
    and ``proxmox_node_id``.
    """
    now = datetime.now(tz=UTC)

    # Snapshots held by active host-runs (do not delete).
    active_result = await db.execute(
        select(ActionHostRun.snapshot_name).where(
            ActionHostRun.status.in_(_ACTIVE_STATUSES),
            ActionHostRun.snapshot_name.isnot(None),
        )
    )
    active_snapshots: set[str] = {row[0] for row in active_result.all()}

    # ActionRun ids known to be terminal — safe to clean up regardless of age.
    terminal_result = await db.execute(
        select(ActionRun.id).where(ActionRun.status.in_(_TERMINAL_RUN_STATUSES))
    )
    terminal_run_ids: set[int] = {row[0] for row in terminal_result.all()}

    nodes_result = await db.execute(select(ProxmoxNode))
    proxmox_nodes: list[ProxmoxNode] = list(nodes_result.scalars().all())

    master_key = get_master_key()
    orphans: list[dict] = []

    for pn in proxmox_nodes:
        try:
            token_secret = decrypt_ssh_key(pn.encrypted_token_secret, master_key)
        except Exception as exc:
            logger.warning(
                "Cannot decrypt token for ProxmoxNode id=%d name=%r: %s",
                pn.id,
                pn.name,
                exc,
            )
            continue

        client = ProxmoxClient(
            api_url=pn.api_url,
            token_id=pn.token_id,
            token_secret=token_secret,
            verify_ssl=pn.verify_ssl,
            ca_cert_pem=pn.ca_cert_pem,
        )

        try:
            pve_nodes = await client.list_nodes()
        except ProxmoxError as exc:
            logger.warning(
                "Cannot list PVE nodes for ProxmoxNode id=%d name=%r: %s",
                pn.id,
                pn.name,
                exc,
            )
            continue

        for pve_node_info in pve_nodes:
            pve_node_name: str = pve_node_info["node"]

            # Scan QEMU VMs and LXC containers on this PVE node.
            for vm_type, list_method in (("qemu", "list_vms"), ("lxc", "list_containers")):
                try:
                    guests = await getattr(client, list_method)(pve_node_name)
                except ProxmoxError as exc:
                    logger.warning(
                        "Cannot list %s on pve_node=%r (ProxmoxNode id=%d): %s",
                        vm_type,
                        pve_node_name,
                        pn.id,
                        exc,
                    )
                    continue

                for guest in guests:
                    vmid: int = int(guest["vmid"])
                    try:
                        snapshots = await client._request(
                            "GET",
                            f"/api2/json/nodes/{pve_node_name}/{vm_type}/{vmid}/snapshot",
                        )
                    except ProxmoxError as exc:
                        logger.debug(
                            "Cannot list snapshots for %s vmid=%d on pve_node=%r: %s",
                            vm_type,
                            vmid,
                            pve_node_name,
                            exc,
                        )
                        continue

                    if not isinstance(snapshots, list):
                        continue

                    for snap in snapshots:
                        snap_name: str = snap.get("name", "")
                        if not snap_name.startswith(_SNAPSHOT_PREFIX):
                            continue

                        if snap_name in active_snapshots:
                            continue

                        parsed = _parse_snapshot_name(snap_name)
                        if parsed is None:
                            logger.debug(
                                "Snapshot %r has unrecognized name format; skipping",
                                snap_name,
                            )
                            continue
                        run_id, created_at = parsed
                        age_hours = (now - created_at).total_seconds() / 3600.0

                        # Either the run is known-terminal (always safe) OR
                        # we don't recognise it and it has aged past the
                        # configured safety threshold.
                        if run_id not in terminal_run_ids and age_hours < max_age_hours:
                            continue

                        orphans.append(
                            {
                                "pve_node": pve_node_name,
                                "vmid": vmid,
                                "vm_type": vm_type,
                                "snapshot_name": snap_name,
                                "age_hours": round(age_hours, 2),
                                "proxmox_node_id": pn.id,
                            }
                        )

    return orphans


async def cleanup_orphaned_snapshots(db: AsyncSession) -> dict:
    """Delete all orphaned labdog snapshots across every configured Proxmox node.

    The maximum age threshold is read from ``workflow.snapshot_max_age_hours``
    and is only applied to snapshots whose ``run_id`` does not match a known
    terminal ``ActionRun``. Snapshots tied to a finished run are cleaned up
    immediately.
    """
    max_age_hours = int(await get_setting_typed("workflow.snapshot_max_age_hours", db))

    orphans = await find_orphaned_snapshots(db, max_age_hours=max_age_hours)
    logger.info("Found %d orphaned snapshots (max_age=%dh)", len(orphans), max_age_hours)

    if not orphans:
        return {"deleted": 0, "errors": []}

    nodes_result = await db.execute(select(ProxmoxNode))
    proxmox_nodes_by_id: dict[int, ProxmoxNode] = {pn.id: pn for pn in nodes_result.scalars().all()}

    master_key = get_master_key()
    clients: dict[int, ProxmoxClient] = {}

    for node_id, pn in proxmox_nodes_by_id.items():
        try:
            token_secret = decrypt_ssh_key(pn.encrypted_token_secret, master_key)
            clients[node_id] = ProxmoxClient(
                api_url=pn.api_url,
                token_id=pn.token_id,
                token_secret=token_secret,
                verify_ssl=pn.verify_ssl,
                ca_cert_pem=pn.ca_cert_pem,
            )
        except Exception as exc:
            logger.warning(
                "Cannot build client for ProxmoxNode id=%d name=%r: %s",
                pn.id,
                pn.name,
                exc,
            )

    deleted = 0
    errors: list[str] = []

    for orphan in orphans:
        node_id: int = orphan["proxmox_node_id"]
        pve_node: str = orphan["pve_node"]
        vmid: int = orphan["vmid"]
        vm_type: str = orphan["vm_type"]
        snap_name: str = orphan["snapshot_name"]

        client = clients.get(node_id)
        if client is None:
            errors.append(
                f"No client available for ProxmoxNode id={node_id}; "
                f"cannot delete {snap_name} on vmid={vmid}"
            )
            continue

        try:
            upid = await client.delete_snapshot(pve_node, vmid, snap_name, vm_type=vm_type)
            await client.wait_for_task(pve_node, upid)
            logger.info(
                "Deleted orphaned snapshot %r %s vmid=%d pve_node=%r",
                snap_name,
                vm_type,
                vmid,
                pve_node,
            )
            deleted += 1
        except ProxmoxError as exc:
            msg = (
                f"Failed to delete snapshot {snap_name!r} {vm_type} vmid={vmid} "
                f"pve_node={pve_node!r}: {exc}"
            )
            logger.error(msg)
            errors.append(msg)

    return {"deleted": deleted, "errors": errors}
