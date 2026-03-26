"""VM discovery by IP address via qemu-guest-agent.

Scans all registered Proxmox nodes, iterates every VM on every PVE node,
and matches network interfaces reported by the guest agent against a target
IP address (or all known host IPs for a full scan).
"""

import logging
from datetime import datetime, timezone

from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.crypto import decrypt_ssh_key, get_master_key
from app.models.host import Host
from app.proxmox.client import ProxmoxClient, ProxmoxError
from app.proxmox.models import ProxmoxNode
from app.proxmox.vm_mapping import VMMapping

logger = logging.getLogger(__name__)


def _extract_ips(interfaces: list[dict]) -> set[str]:
    """Return all non-loopback IPv4 addresses from guest agent interface data."""
    ips: set[str] = set()
    for iface in interfaces:
        for addr_info in iface.get("ip-addresses", []):
            if addr_info.get("ip-address-type") == "ipv4":
                ip = addr_info.get("ip-address", "")
                if ip and not ip.startswith("127."):
                    ips.add(ip)
    return ips


async def _build_client(node: ProxmoxNode) -> ProxmoxClient:
    """Decrypt token secret and return a configured ProxmoxClient."""
    master_key = get_master_key()
    token_secret = decrypt_ssh_key(node.encrypted_token_secret, master_key)
    return ProxmoxClient(
        api_url=node.api_url,
        token_id=node.token_id,
        token_secret=token_secret,
        verify_ssl=node.verify_ssl,
    )


async def discover_vm_by_ip(ip: str, db: AsyncSession) -> VMMapping | None:
    """Scan all Proxmox nodes to find the VM whose guest agent reports *ip*.

    On a match the VMMapping record is upserted (insert or update) and
    returned.  Returns None if no VM claims that address.

    ProxmoxError for an individual VM (e.g. agent not responding) is logged
    as a warning and skipped — it does not abort the full scan.
    """
    result = await db.execute(select(ProxmoxNode))
    nodes: list[ProxmoxNode] = list(result.scalars().all())

    # Also need the host_id for the given IP
    host_result = await db.execute(select(Host).where(Host.ip_address == ip))
    host = host_result.scalar_one_or_none()
    if host is None:
        logger.warning("discover_vm_by_ip: no host found with ip_address=%s", ip)
        return None

    for node in nodes:
        try:
            client = await _build_client(node)
        except Exception:
            logger.warning("Failed to decrypt credentials for Proxmox node %s", node.name)
            continue

        try:
            pve_nodes = await client.list_nodes()
        except ProxmoxError as exc:
            logger.warning("Failed to list nodes on %s: %s", node.name, exc)
            continue

        for pve_node_info in pve_nodes:
            pve_node_name: str = pve_node_info.get("node", "")
            if not pve_node_name:
                continue

            try:
                vms = await client.list_vms(pve_node_name)
            except ProxmoxError as exc:
                logger.warning(
                    "Failed to list VMs on %s/%s: %s", node.name, pve_node_name, exc
                )
                continue

            for vm in vms:
                vmid: int = int(vm.get("vmid", 0))
                vm_name: str = vm.get("name", f"vm-{vmid}")
                if not vmid:
                    continue

                try:
                    interfaces = await client.get_vm_agent_interfaces(pve_node_name, vmid)
                except ProxmoxError as exc:
                    logger.warning(
                        "Agent not available on %s/%s vmid=%s: %s",
                        node.name, pve_node_name, vmid, exc,
                    )
                    continue
                except Exception as exc:
                    logger.warning(
                        "Unexpected error querying agent on %s/%s vmid=%s: %s",
                        node.name, pve_node_name, vmid, exc,
                    )
                    continue

                vm_ips = _extract_ips(interfaces)
                if ip not in vm_ips:
                    continue

                # Match found — upsert the mapping
                mapping = await _upsert_mapping(
                    db=db,
                    host_id=host.id,
                    proxmox_node_id=node.id,
                    pve_node_name=pve_node_name,
                    vmid=vmid,
                    vm_name=vm_name,
                )
                logger.info(
                    "Mapped host %s (id=%s, ip=%s) -> Proxmox %s/%s vmid=%s name=%s",
                    host.hostname, host.id, ip, node.name, pve_node_name, vmid, vm_name,
                )
                return mapping

    logger.info("No VM found for ip=%s across %d Proxmox node(s)", ip, len(nodes))
    return None


async def discover_all_vms(db: AsyncSession) -> list[VMMapping]:
    """Full scan: discover VM mappings for every registered host.

    Existing mappings are updated in place; mappings for hosts that no
    longer match any VM are removed (stale mapping cleanup).

    Returns the list of current VMMapping records after the scan.
    """
    # Load all hosts and all Proxmox nodes
    hosts_result = await db.execute(select(Host))
    hosts: list[Host] = list(hosts_result.scalars().all())

    nodes_result = await db.execute(select(ProxmoxNode))
    nodes: list[ProxmoxNode] = list(nodes_result.scalars().all())

    if not hosts or not nodes:
        return []

    # Build ip -> host lookup
    ip_to_host: dict[str, Host] = {h.ip_address: h for h in hosts}

    # Collect all (ip, vmid, vm_name, pve_node, node_id) tuples found across
    # every Proxmox node
    found_host_ids: set[int] = set()
    upserted: list[VMMapping] = []

    for node in nodes:
        try:
            client = await _build_client(node)
        except Exception:
            logger.warning("Failed to decrypt credentials for Proxmox node %s", node.name)
            continue

        try:
            pve_nodes = await client.list_nodes()
        except ProxmoxError as exc:
            logger.warning("Failed to list nodes on %s: %s", node.name, exc)
            continue

        for pve_node_info in pve_nodes:
            pve_node_name: str = pve_node_info.get("node", "")
            if not pve_node_name:
                continue

            try:
                vms = await client.list_vms(pve_node_name)
            except ProxmoxError as exc:
                logger.warning(
                    "Failed to list VMs on %s/%s: %s", node.name, pve_node_name, exc
                )
                continue

            for vm in vms:
                vmid: int = int(vm.get("vmid", 0))
                vm_name: str = vm.get("name", f"vm-{vmid}")
                if not vmid:
                    continue

                try:
                    interfaces = await client.get_vm_agent_interfaces(pve_node_name, vmid)
                except ProxmoxError as exc:
                    logger.warning(
                        "Agent not available on %s/%s vmid=%s: %s",
                        node.name, pve_node_name, vmid, exc,
                    )
                    continue
                except Exception as exc:
                    logger.warning(
                        "Unexpected error querying agent on %s/%s vmid=%s: %s",
                        node.name, pve_node_name, vmid, exc,
                    )
                    continue

                vm_ips = _extract_ips(interfaces)
                for ip in vm_ips:
                    host = ip_to_host.get(ip)
                    if host is None:
                        continue

                    mapping = await _upsert_mapping(
                        db=db,
                        host_id=host.id,
                        proxmox_node_id=node.id,
                        pve_node_name=pve_node_name,
                        vmid=vmid,
                        vm_name=vm_name,
                    )
                    found_host_ids.add(host.id)
                    upserted.append(mapping)
                    logger.info(
                        "Mapped host %s (id=%s) -> %s/%s vmid=%s",
                        host.hostname, host.id, node.name, pve_node_name, vmid,
                    )
                    # Each host maps to at most one VM; stop searching IPs once matched
                    break

    # Delete stale mappings (hosts whose VM was not found in this scan)
    all_host_ids = {h.id for h in hosts}
    stale_host_ids = all_host_ids - found_host_ids
    if stale_host_ids:
        await db.execute(
            delete(VMMapping).where(VMMapping.host_id.in_(stale_host_ids))
        )
        logger.info("Removed stale VM mappings for host_ids=%s", stale_host_ids)

    await db.commit()
    return upserted


async def _upsert_mapping(
    db: AsyncSession,
    host_id: int,
    proxmox_node_id: int,
    pve_node_name: str,
    vmid: int,
    vm_name: str,
) -> VMMapping:
    """Insert or update a VMMapping row and return the refreshed object."""
    now = datetime.now(timezone.utc)

    stmt = (
        pg_insert(VMMapping)
        .values(
            host_id=host_id,
            proxmox_node_id=proxmox_node_id,
            pve_node_name=pve_node_name,
            vmid=vmid,
            vm_name=vm_name,
            discovered_at=now,
        )
        .on_conflict_do_update(
            index_elements=["host_id"],
            set_={
                "proxmox_node_id": proxmox_node_id,
                "pve_node_name": pve_node_name,
                "vmid": vmid,
                "vm_name": vm_name,
                "discovered_at": now,
            },
        )
        .returning(VMMapping)
    )
    result = await db.execute(stmt)
    mapping = result.scalar_one()
    await db.flush()
    return mapping
