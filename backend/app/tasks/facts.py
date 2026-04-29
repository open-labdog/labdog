import logging
import re

from app.tasks import celery_app

logger = logging.getLogger(__name__)


def _parse_os_release(content: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for line in content.splitlines():
        line = line.strip()
        if "=" not in line or line.startswith("#"):
            continue
        k, _, v = line.partition("=")
        out[k.strip()] = v.strip().strip('"')
    return out


def _derive_os_family(parsed: dict[str, str]) -> str | None:
    """Return the most useful family label from /etc/os-release.

    ID_LIKE wins because it groups Ubuntu under "debian", CentOS/Rocky under
    "rhel fedora", etc. Falls back to ID for distros that don't set ID_LIKE
    (Debian itself, Arch).
    """
    id_like = parsed.get("ID_LIKE", "").strip()
    if id_like:
        # ID_LIKE can be space-separated; first token is the canonical family.
        return id_like.split()[0] or None
    id_ = parsed.get("ID", "").strip()
    return id_ or None


# Skip loopback, docker, bridges, virtual, wireguard, tailscale, and most
# common virt interfaces when picking the primary NIC.
_VIRT_NIC_RE = re.compile(r"^(lo|docker|br-|veth|tailscale|wg|tun|tap|virbr|vnet|cni|flannel|cali)")


def _pick_default_nic(ip_link_output: str) -> str | None:
    """Pick the first non-virtual NIC name from `ip -o link show` output.

    Each line looks like:
        2: eth0: <BROADCAST,MULTICAST,UP,LOWER_UP> mtu 1500 ...
    We extract the name and skip loopback / virt interfaces.
    """
    for line in ip_link_output.splitlines():
        # Format: "<index>: <name>: <flags>"
        parts = line.split(":", 2)
        if len(parts) < 2:
            continue
        name = parts[1].strip()
        # Strip the @parent suffix that appears on some virtual interfaces.
        name = name.split("@", 1)[0]
        if not name or _VIRT_NIC_RE.match(name):
            continue
        return name
    return None


def _detect_firewall_backend(nft_probe: str, iptables_probe: str) -> str:
    """Return 'nftables' | 'iptables' | 'unknown' from `command -v` outputs."""
    if nft_probe.strip():
        return "nftables"
    if iptables_probe.strip():
        return "iptables"
    return "unknown"


@celery_app.task(name="app.tasks.facts.collect_host_facts", queue="long_running")
def collect_host_facts(host_id: int):
    """Collect host facts via SSH and persist them to the Host row.

    One SSH session gathers /etc/os-release, uname, default NIC, and firewall
    backend probes — the result is persisted on the Host row so the UI can
    pre-populate version pickers and pick the right firewall backend module
    without re-probing on every page load.
    """
    import asyncio
    from datetime import UTC, datetime

    import asyncssh
    from sqlalchemy import select

    from app.crypto.encryption import decrypt_ssh_key
    from app.crypto.key_management import get_master_key
    from app.db import task_session
    from app.models.host import FirewallBackend, Host
    from app.models.ssh_key import SSHKey
    from app.ssh_utils import ssh_connect

    async def _run():
        async with task_session() as db:
            result = await db.execute(select(Host).where(Host.id == host_id))
            host = result.scalar_one_or_none()
            if host is None:
                logger.warning("collect_host_facts: host %d not found", host_id)
                return

            if host.ssh_key_id is None:
                logger.info("collect_host_facts: host %d has no SSH key, skipping", host_id)
                return

            key_result = await db.execute(select(SSHKey).where(SSHKey.id == host.ssh_key_id))
            ssh_key = key_result.scalar_one_or_none()
            if ssh_key is None:
                logger.warning(
                    "collect_host_facts: SSH key %d not found for host %d",
                    host.ssh_key_id,
                    host_id,
                )
                return

            private_key_pem = decrypt_ssh_key(ssh_key.encrypted_private_key, get_master_key())
            imported_key = asyncssh.import_private_key(private_key_pem)

            try:
                async with ssh_connect(
                    host.ip_address,
                    port=host.ssh_port,
                    username=ssh_key.ssh_user,
                    client_keys=[imported_key],
                ) as conn:
                    os_release = await conn.run("cat /etc/os-release", check=False)
                    uname_r = await conn.run("uname -r", check=False)
                    uname_s = await conn.run("uname -s", check=False)
                    ip_link = await conn.run("ip -o link show", check=False)
                    # `command -v` returns empty output + rc=1 when the binary
                    # is absent, and the path + rc=0 when present. We only
                    # care about the presence signal, not the exit code.
                    nft = await conn.run("command -v nft || true", check=False)
                    iptables = await conn.run("command -v iptables || true", check=False)

                parsed = _parse_os_release(os_release.stdout or "")
                host.os_codename = parsed.get("VERSION_CODENAME") or None
                host.os_pretty_name = parsed.get("PRETTY_NAME") or None
                host.os_family = _derive_os_family(parsed)
                host.kernel_version = (uname_r.stdout or "").strip() or None
                host.kernel_release = (uname_s.stdout or "").strip() or None
                host.default_nic = _pick_default_nic(ip_link.stdout or "")
                backend_str = _detect_firewall_backend(nft.stdout or "", iptables.stdout or "")
                host.firewall_backend = FirewallBackend(backend_str)
                host.os_facts_collected_at = datetime.now(UTC)
                await db.commit()
            except (asyncssh.Error, OSError, TimeoutError) as exc:
                logger.warning("collect_host_facts: SSH error for host %d: %s", host_id, exc)
                # Do NOT update os_facts_collected_at so next tab load retriggers

    asyncio.run(_run())
