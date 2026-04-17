"""Orphaned snapshot detection and cleanup for Barricade update workflows.

Snapshots created by the workflow follow the naming convention::

    barricade-{run_id}-{unix_timestamp}

A snapshot is considered orphaned when:
- No active (pending/running) WorkflowHostRun references it, AND
- Its embedded timestamp is older than the configured max-age threshold.
"""

import logging
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.crypto.encryption import decrypt_ssh_key
from app.crypto.key_management import get_master_key
from app.proxmox.client import ProxmoxClient, ProxmoxError
from app.proxmox.models import ProxmoxNode
from app.settings_service import get_setting_typed
from app.workflows.models import WorkflowHostRun, WorkflowHostStatus

logger = logging.getLogger(__name__)

_SNAPSHOT_PREFIX = "barricade-"
_ACTIVE_STATUSES = {WorkflowHostStatus.pending, WorkflowHostStatus.running}


def _parse_snapshot_timestamp(snapshot_name: str) -> datetime | None:
    """Extract the UTC timestamp embedded in a barricade snapshot name.

    Args:
        snapshot_name: Snapshot name in the form ``barricade-{run_id}-{ts}``.

    Returns:
        A timezone-aware :class:`datetime` in UTC, or ``None`` if the name
        does not match the expected format.
    """
    # Expected format: barricade-<run_id>-<unix_timestamp>
    parts = snapshot_name.split("-")
    if len(parts) < 3:
        return None
    try:
        ts = int(parts[-1])
        return datetime.fromtimestamp(ts, tz=UTC)
    except (ValueError, OSError):
        return None


async def find_orphaned_snapshots(
    db: AsyncSession,
    max_age_hours: int = 24,
) -> list[dict]:
    """Discover barricade snapshots that are no longer associated with an active run.

    Iterates over every configured :class:`ProxmoxNode`, enumerates all VMs on
    each PVE node, and inspects their snapshots.  A snapshot whose name starts
    with ``barricade-`` is treated as orphaned when:

    1. No :class:`WorkflowHostRun` with status *pending* or *running* references
       the snapshot by name, AND
    2. The timestamp embedded in the snapshot name is older than *max_age_hours*.

    Args:
        db: An active async SQLAlchemy session.
        max_age_hours: Snapshots older than this many hours are eligible for
            cleanup.  Defaults to 24.

    Returns:
        A list of dicts, each with keys:

        - ``pve_node`` (str): PVE node name.
        - ``vmid`` (int): VM identifier.
        - ``snapshot_name`` (str): Snapshot name.
        - ``age_hours`` (float): Age of the snapshot in hours.
        - ``proxmox_node_id`` (int): Database ID of the :class:`ProxmoxNode`.
    """
    now = datetime.now(tz=UTC)

    # Collect all snapshot_names that belong to active host runs.
    active_result = await db.execute(
        select(WorkflowHostRun.snapshot_name).where(
            WorkflowHostRun.status.in_(_ACTIVE_STATUSES),
            WorkflowHostRun.snapshot_name.isnot(None),
        )
    )
    active_snapshots: set[str] = {row[0] for row in active_result.all()}

    # Load all ProxmoxNode records.
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

            try:
                vms = await client.list_vms(pve_node_name)
            except ProxmoxError as exc:
                logger.warning(
                    "Cannot list VMs on pve_node=%r (ProxmoxNode id=%d): %s",
                    pve_node_name,
                    pn.id,
                    exc,
                )
                continue

            for vm in vms:
                vmid: int = int(vm["vmid"])

                try:
                    snapshots = await client._request(
                        "GET",
                        f"/api2/json/nodes/{pve_node_name}/qemu/{vmid}/snapshot",
                    )
                except ProxmoxError as exc:
                    logger.debug(
                        "Cannot list snapshots for vmid=%d on pve_node=%r: %s",
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

                    # Skip snapshots held by active runs.
                    if snap_name in active_snapshots:
                        continue

                    created_at = _parse_snapshot_timestamp(snap_name)
                    if created_at is None:
                        logger.debug(
                            "Snapshot %r has unrecognized name format; skipping",
                            snap_name,
                        )
                        continue

                    age_hours = (now - created_at).total_seconds() / 3600.0
                    if age_hours < max_age_hours:
                        continue

                    orphans.append(
                        {
                            "pve_node": pve_node_name,
                            "vmid": vmid,
                            "snapshot_name": snap_name,
                            "age_hours": round(age_hours, 2),
                            "proxmox_node_id": pn.id,
                        }
                    )

    return orphans


async def cleanup_orphaned_snapshots(db: AsyncSession) -> dict:
    """Delete all orphaned barricade snapshots across every configured Proxmox node.

    The maximum age threshold is read from the ``workflow.snapshot_max_age_hours``
    application setting (default 24 hours).

    Args:
        db: An active async SQLAlchemy session.

    Returns:
        A dict with keys:

        - ``deleted`` (int): Number of snapshots successfully deleted.
        - ``errors`` (list[str]): Descriptions of any deletion failures.
    """
    max_age_hours = int(await get_setting_typed("workflow.snapshot_max_age_hours", db))

    orphans = await find_orphaned_snapshots(db, max_age_hours=max_age_hours)
    logger.info("Found %d orphaned snapshots (max_age=%dh)", len(orphans), max_age_hours)

    if not orphans:
        return {"deleted": 0, "errors": []}

    # Build a map of proxmox_node_id -> ProxmoxClient to avoid re-decrypting.
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
        snap_name: str = orphan["snapshot_name"]

        client = clients.get(node_id)
        if client is None:
            errors.append(
                f"No client available for ProxmoxNode id={node_id}; "
                f"cannot delete {snap_name} on vmid={vmid}"
            )
            continue

        try:
            upid = await client.delete_snapshot(pve_node, vmid, snap_name)
            await client.wait_for_task(pve_node, upid)
            logger.info(
                "Deleted orphaned snapshot %r vmid=%d pve_node=%r",
                snap_name,
                vmid,
                pve_node,
            )
            deleted += 1
        except ProxmoxError as exc:
            msg = (
                f"Failed to delete snapshot {snap_name!r} vmid={vmid} pve_node={pve_node!r}: {exc}"
            )
            logger.error(msg)
            errors.append(msg)

    return {"deleted": deleted, "errors": errors}
