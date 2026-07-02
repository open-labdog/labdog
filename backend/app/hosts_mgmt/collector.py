from dataclasses import dataclass
from typing import TYPE_CHECKING

import asyncssh

from app.ssh_utils import ssh_connect_host

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from app.models.host import Host


@dataclass
class ParsedHostsEntry:
    ip_address: str
    hostname: str
    aliases: list[str]


async def collect_hosts_file(
    host: "Host",
    db: "AsyncSession",
    private_key_pem: str,
) -> list[ParsedHostsEntry]:
    """
    SSH into host, cat /etc/hosts, parse entries.
    Skip comment lines (starting with #) and empty lines.
    Handle tabs and multiple spaces as delimiters.
    """
    results = []
    try:
        private_key = asyncssh.import_private_key(private_key_pem)
        async with ssh_connect_host(host, db, client_keys=[private_key]) as conn:
            result = await conn.run("cat /etc/hosts", check=True)
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
