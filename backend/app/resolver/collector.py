"""Collect actual DNS resolver state from remote hosts via SSH."""

import asyncio
import re

import asyncssh

from app.ssh_utils import ssh_connect


def _stdout(result: asyncssh.SSHCompletedProcess) -> str:
    out = result.stdout
    if out is None:
        return ""
    return out if isinstance(out, str) else out.decode()


async def collect_resolver_state(
    host_ip: str,
    ssh_port: int,
    private_key_pem: str,
    resolver_type: str,
    ssh_user: str = "root",
) -> dict | None:
    """Return {"nameservers", "search_domains", "options"} or None if unmanaged."""
    private_key = asyncssh.import_private_key(private_key_pem)

    async def _run() -> dict | None:
        async with ssh_connect(
            host_ip,
            port=ssh_port,
            username=ssh_user,
            client_keys=[private_key],
        ) as conn:
            if resolver_type == "resolv_conf":
                result = await conn.run("cat /etc/resolv.conf 2>/dev/null", check=False)
                output = _stdout(result)
                if result.exit_status != 0 or not output.strip():
                    return None
                return parse_resolv_conf(output)

            elif resolver_type == "systemd_resolved":
                result = await conn.run("resolvectl status 2>/dev/null", check=False)
                output = _stdout(result)
                if result.exit_status == 0 and output.strip():
                    return parse_resolvectl_output(output)

                result = await conn.run("cat /etc/systemd/resolved.conf 2>/dev/null", check=False)
                output = _stdout(result)
                if result.exit_status != 0 or not output.strip():
                    return None
                return parse_resolved_conf(output)

            elif resolver_type == "networkmanager":
                result = await conn.run(
                    "cat /etc/NetworkManager/conf.d/90-labdog-dns.conf 2>/dev/null",
                    check=False,
                )
                output = _stdout(result)
                if result.exit_status != 0 or not output.strip():
                    return None
                return parse_networkmanager_conf(output)

            else:
                return None

    try:
        return await asyncio.wait_for(_run(), timeout=30.0)
    except Exception:
        return None


def parse_resolv_conf(text: str) -> dict:
    """Parse /etc/resolv.conf content into structured dict."""
    nameservers: list[str] = []
    search_domains: list[str] = []
    options: dict[str, int | str] = {}

    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or line.startswith(";"):
            continue

        if line.startswith("nameserver"):
            parts = line.split(None, 1)
            if len(parts) == 2:
                nameservers.append(parts[1].strip())

        elif line.startswith("search"):
            parts = line.split()[1:]
            search_domains = [d.strip() for d in parts if d.strip()]

        elif line.startswith("domain"):
            parts = line.split(None, 1)
            if len(parts) == 2:
                search_domains = [parts[1].strip()]

        elif line.startswith("options"):
            for opt in line.split()[1:]:
                if ":" in opt:
                    key, val = opt.split(":", 1)
                    try:
                        options[key] = int(val)
                    except ValueError:
                        options[key] = val
                else:
                    options[opt] = 1

    return {
        "nameservers": nameservers,
        "search_domains": search_domains,
        "options": options,
    }


def parse_resolvectl_output(text: str) -> dict:
    """Parse `resolvectl status` output."""
    nameservers: list[str] = []
    search_domains: list[str] = []

    for line in text.splitlines():
        stripped = line.strip()

        dns_match = re.match(r"(?:Current\s+)?DNS\s+Servers?:\s*(.+)", stripped, re.IGNORECASE)
        if dns_match:
            ips = dns_match.group(1).split()
            for ip in ips:
                ip = ip.strip()
                if ip and ip not in nameservers:
                    nameservers.append(ip)
            continue

        domain_match = re.match(r"DNS\s+Domain:\s*(.+)", stripped, re.IGNORECASE)
        if domain_match:
            domains = domain_match.group(1).split()
            for d in domains:
                d = d.strip()
                if d and d not in search_domains:
                    search_domains.append(d)
            continue

    return {
        "nameservers": nameservers,
        "search_domains": search_domains,
        "options": {},
    }


def parse_resolved_conf(text: str) -> dict:
    """Parse /etc/systemd/resolved.conf INI content."""
    nameservers: list[str] = []
    search_domains: list[str] = []
    options: dict[str, int | str] = {}

    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or line.startswith("["):
            continue

        if line.startswith("DNS="):
            val = line.split("=", 1)[1].strip()
            if val:
                nameservers = [ip.strip() for ip in val.split() if ip.strip()]

        elif line.startswith("Domains="):
            val = line.split("=", 1)[1].strip()
            if val:
                search_domains = [d.strip() for d in val.split() if d.strip()]

        elif line.startswith("DNSOverTLS="):
            val = line.split("=", 1)[1].strip().lower()
            if val in ("yes", "true", "opportunistic"):
                options["dns_over_tls"] = val

    return {
        "nameservers": nameservers,
        "search_domains": search_domains,
        "options": options,
    }


def parse_networkmanager_conf(text: str) -> dict:
    """Parse /etc/NetworkManager/conf.d/90-labdog-dns.conf INI content."""
    nameservers: list[str] = []
    search_domains: list[str] = []

    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or line.startswith("["):
            continue

        if line.startswith("servers="):
            val = line.split("=", 1)[1].strip()
            if val:
                nameservers = [ip.strip() for ip in val.split(",") if ip.strip()]

        elif line.startswith("searches="):
            val = line.split("=", 1)[1].strip()
            if val:
                search_domains = [d.strip() for d in val.split(",") if d.strip()]

    return {
        "nameservers": nameservers,
        "search_domains": search_domains,
        "options": {},
    }
