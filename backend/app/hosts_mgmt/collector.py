from dataclasses import dataclass
from typing import TYPE_CHECKING

from app.ssh_utils import host_connection

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from app.models.host import Host


@dataclass
class ParsedHostsEntry:
    ip_address: str
    hostname: str
    aliases: list[str]


def hosts_entry_dict(entry: ParsedHostsEntry) -> dict:
    """A collected /etc/hosts line as ``HostModuleStatus.collected_state`` stores it."""
    return {"ip_address": entry.ip_address, "hostname": entry.hostname, "aliases": entry.aliases}


async def collect_hosts_file(
    host: "Host",
    db: "AsyncSession",
    private_key_pem: str,
    conn=None,
) -> list[ParsedHostsEntry]:
    """
    SSH into host, cat /etc/hosts, parse entries.
    Skip comment lines (starting with #) and empty lines.
    Handle tabs and multiple spaces as delimiters.
    """
    results = []
    try:
        async with host_connection(host, db, private_key_pem, conn) as c:
            result = await c.run("cat /etc/hosts", check=True)
            content = result.stdout

            for line in content.splitlines():
                line = line.strip()
                # Skip empty lines and comments
                if not line or line.startswith("#"):
                    continue
                # Strip inline comments
                if "#" in line:
                    line = line[: line.index("#")].strip()
                # Split on whitespace (handles tabs and multiple spaces)
                parts = line.split()
                if len(parts) < 2:
                    continue
                ip = parts[0]
                hostname = parts[1]
                aliases = parts[2:] if len(parts) > 2 else []
                results.append(
                    ParsedHostsEntry(
                        ip_address=ip,
                        hostname=hostname,
                        aliases=aliases,
                    )
                )
    except Exception:
        # Connection failure — return empty list (caller handles error)
        pass

    return results
