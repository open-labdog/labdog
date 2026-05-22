"""Preflight step: SSH reachability, disk space, VM, and agent checks."""

import logging
from typing import Any

from app.ssh_utils import ssh_connect_host

logger = logging.getLogger(__name__)

_MIN_DISK_KB = 2_097_152  # 2 GB in kilobytes


async def run_preflight(
    host: Any,
    vm_mapping: Any,
    ssh_key_path: str,
    proxmox_client: Any,
    db: Any,
) -> dict[str, Any]:
    """Run all pre-update checks for a host.

    Verifies SSH reachability, available disk space, VM presence, and QEMU
    guest agent responsiveness.  Every check is attempted regardless of
    earlier failures; the caller receives a full picture of what passed and
    what did not.

    Args:
        host: Host ORM object with ``ip_address``, ``ssh_port``,
            ``ssh_user``, and ``ssh_host_key_entry`` attributes.
        vm_mapping: VMMapping ORM object (``pve_node_name``, ``vmid``), or
            ``None`` when snapshot support is not configured.
        ssh_key_path: Absolute path to the decrypted SSH private key on tmpfs.
        proxmox_client: Authenticated :class:`~app.proxmox.client.ProxmoxClient`
            instance, or ``None`` when Proxmox integration is unavailable.
        db: Active async SQLAlchemy session used for TOFU key persistence.

    Returns:
        A dict of the form::

            {
                "success": bool,
                "checks": {
                    "ssh": bool,
                    "disk_gb": float,
                    "vm_found": bool,
                    "agent_ok": bool,
                },
            }

        ``success`` is ``True`` only when every applicable check passed.
        ``disk_gb`` is ``0.0`` when the check could not be completed.
    """
    checks: dict[str, Any] = {
        "ssh": False,
        "disk_gb": 0.0,
        "vm_found": False,
        "agent_ok": False,
    }

    # ------------------------------------------------------------------
    # SSH reachability + disk check in a single connection
    # ------------------------------------------------------------------
    try:
        async with ssh_connect_host(
            host,
            db,
            client_keys=[ssh_key_path],
            connect_timeout=15,
        ) as ssh_conn:
            checks["ssh"] = True
            logger.debug("preflight: SSH reachable for host %s", host.ip_address)

            # Disk space check
            try:
                result = await ssh_conn.run("df --output=avail / | tail -1", check=True)
                avail_kb = int(result.stdout.strip())
                disk_gb = avail_kb / 1_048_576  # KB -> GB
                checks["disk_gb"] = round(disk_gb, 2)
                if avail_kb < _MIN_DISK_KB:
                    logger.warning(
                        "preflight: insufficient disk space on host %s: %.2f GB available",
                        host.ip_address,
                        disk_gb,
                    )
                else:
                    logger.debug(
                        "preflight: disk check passed for host %s (%.2f GB free)",
                        host.ip_address,
                        disk_gb,
                    )
            except Exception as exc:
                logger.warning(
                    "preflight: disk check failed for host %s: %s",
                    host.ip_address,
                    exc,
                )
    except Exception as exc:
        logger.warning(
            "preflight: SSH check failed for host %s: %s",
            host.ip_address,
            exc,
        )

    # ------------------------------------------------------------------
    # VM mapping presence
    # ------------------------------------------------------------------
    if proxmox_client is None:
        # Proxmox not configured — treat as not applicable (skip silently)
        checks["vm_found"] = True
        checks["agent_ok"] = True
    elif vm_mapping is None:
        logger.warning("preflight: no VMMapping found; vm_found=False")
    else:
        checks["vm_found"] = True

        # ---------------------------------------------------------------
        # QEMU guest agent check
        # ---------------------------------------------------------------
        try:
            await proxmox_client.get_vm_agent_interfaces(vm_mapping.pve_node_name, vm_mapping.vmid)
            checks["agent_ok"] = True
            logger.debug(
                "preflight: agent responded for vmid %d on %s",
                vm_mapping.vmid,
                vm_mapping.pve_node_name,
            )
        except Exception as exc:
            logger.warning(
                "preflight: agent check failed for vmid %d: %s",
                vm_mapping.vmid,
                exc,
            )

    # ------------------------------------------------------------------
    # Aggregate result
    # ------------------------------------------------------------------
    disk_ok = checks["disk_gb"] >= (_MIN_DISK_KB / 1_048_576)
    success = checks["ssh"] and disk_ok and checks["vm_found"] and checks["agent_ok"]

    return {"success": success, "checks": checks}
