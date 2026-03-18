import asyncssh
from dataclasses import dataclass


@dataclass
class ParsedHostsEntry:
    ip_address: str
    hostname: str
    aliases: list[str]


async def collect_hosts_file(
    host_ip: str,
    ssh_port: int,
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
        async with asyncssh.connect(
            host_ip,
            port=ssh_port,
            username="root",
            client_keys=[private_key],
            known_hosts=None,
        ) as conn:
            result = await conn.run("cat /etc/hosts", check=True)
            content = result.stdout

            for line in content.splitlines():
                line = line.strip()
                # Skip empty lines and comments
                if not line or line.startswith("#"):
                    continue
                # Strip inline comments
                if "#" in line:
                    line = line[:line.index("#")].strip()
                # Split on whitespace (handles tabs and multiple spaces)
                parts = line.split()
                if len(parts) < 2:
                    continue
                ip = parts[0]
                hostname = parts[1]
                aliases = parts[2:] if len(parts) > 2 else []
                results.append(ParsedHostsEntry(
                    ip_address=ip,
                    hostname=hostname,
                    aliases=aliases,
                ))
    except Exception:
        # Connection failure — return empty list (caller handles error)
        pass

    return results
