"""Which managed host LabDog itself runs on.

An automatic fix on that host could take LabDog down with it, and rolling
it back would be worse than the problem: Proxmox stops the VM to restore
it, LabDog stops with it, nothing is left to start the VM again, and
LabDog's own database goes back to the moment of the snapshot. So full
auto is refused there, and so is a rollback.

LabDog cannot see its own address — inside a container it does not even
have the host's — but the hosts it manages can. Every probe records the
address a host saw LabDog connect from (``Host.labdog_source_ip``), and
that gives the machine away in one of two ways:

* **Other hosts see LabDog coming from this host's address.** A container
  on a bridge network reaches other machines through its host's NAT, so
  they record the host's own IP. A typical Docker install is caught here,
  from the database alone.
* **This host sees LabDog coming from one of its own addresses, or from
  inside a container bridge it hosts.** A native install, or a container
  with host networking, connects from the host's own IP; a bridged
  container connects from the bridge's subnet. Telling those apart from a
  remote LabDog on the same LAN needs the host's interface list, so this
  is read live, over SSH, and only when the first test found nothing.

Neither catches a container on a macvlan network, which has an address of
its own on the LAN: such an install looks like any other machine. The
live test also cannot run against a host LabDog cannot reach — but then
the assistant cannot change that host either.
"""

from __future__ import annotations

import asyncio
import ipaddress
import logging
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

#: Interface names of container and VM bridges. A remote LabDog on the
#: same LAN shares a subnet with the host's ordinary interface, so being
#: inside a subnet only counts on one of these. Generic LAN bridges
#: (``br0``, ``vmbr0``) are deliberately absent for the same reason.
BRIDGE_PREFIXES = (
    "docker",
    "br-",
    "cni",
    "podman",
    "cali",
    "flannel",
    "cilium",
    "lxcbr",
    "lxdbr",
    "virbr",
    "kube-bridge",
    "weave",
)

#: Read-only, and run by LabDog itself rather than proposed by the model,
#: so it does not go through the command classifier. The first line is
#: where this SSH session came from, as the host sees it.
PROBE_COMMAND = 'echo "$SSH_CLIENT"; ip -o addr show 2>/dev/null'

PROBE_TIMEOUT_SECONDS = 15


Address = tuple[str, ipaddress.IPv4Interface | ipaddress.IPv6Interface]


async def seen_from(db: AsyncSession, host: Any) -> bool:
    """The database test: does any host record LabDog as this host?"""
    from app.models.host import Host

    if host.labdog_source_ip and host.labdog_source_ip == host.ip_address:
        return True
    other = await db.execute(
        select(Host.id).where(Host.id != host.id, Host.labdog_source_ip == host.ip_address).limit(1)
    )
    return other.scalar_one_or_none() is not None


def parse_probe(output: str) -> tuple[str | None, list[Address]]:
    """``(client_ip, [(interface, address), ...])`` from ``PROBE_COMMAND``."""
    lines = output.splitlines()
    first = lines[0].split() if lines else []
    client = first[0] if first else None
    addresses: list[Address] = []
    for line in lines[1:]:
        # "2: eth0    inet 10.0.0.5/24 brd 10.0.0.255 scope global eth0\ ..."
        parts = line.split()
        if len(parts) < 4 or parts[2] not in ("inet", "inet6"):
            continue
        name = parts[1].split("@", 1)[0]
        try:
            addresses.append((name, ipaddress.ip_interface(parts[3])))
        except ValueError:
            continue
    return client, addresses


def runs_here(client_ip: str, addresses: list[Address]) -> bool:
    """Whether a connection from ``client_ip`` came from this machine."""
    try:
        client = ipaddress.ip_address(client_ip.split("%", 1)[0])
    except ValueError:
        return False
    for name, interface in addresses:
        if interface.ip == client:
            return True
        if name.startswith(BRIDGE_PREFIXES) and client in interface.network:
            return True
    return False


async def ask_the_host(db: AsyncSession, host: Any) -> bool | None:
    """The live test. ``None`` when the host cannot be asked."""
    from app.ssh_utils import load_host_key, ssh_connect_host

    key = await load_host_key(db, host)
    if key is None:
        return None
    try:
        async with ssh_connect_host(
            host, db, client_keys=[key], connect_timeout=PROBE_TIMEOUT_SECONDS
        ) as conn:
            result = await asyncio.wait_for(
                conn.run(PROBE_COMMAND, check=False), timeout=PROBE_TIMEOUT_SECONDS
            )
    except Exception as exc:
        logger.info("labdog_host: could not ask %s where LabDog runs: %s", host.hostname, exc)
        return None
    client, addresses = parse_probe(str(result.stdout or ""))
    if not client:
        return None
    return runs_here(client, addresses)


async def labdog_runs_on(db: AsyncSession, host: Any, *, ask: bool = True) -> bool:
    """Whether LabDog runs on ``host``.

    ``ask=False`` skips the live test, for callers that cannot afford a
    connection — a page load — and accept the database's answer.
    """
    if await seen_from(db, host):
        return True
    if not ask:
        return False
    return bool(await ask_the_host(db, host))
