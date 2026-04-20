"""SSH verification helper for discovered hosts.

Extracted from app.api.discovery so both the discovery API and the
background scan runner (app.tasks.scan_run) can share the same logic
without duplication.
"""

from __future__ import annotations

import socket as _socket

import asyncssh

from app.ssh_utils import get_source_ip, ssh_connect


async def verify_ssh(
    ip: str,
    port: int,
    username: str,
    imported_key: asyncssh.SSHKey,
) -> tuple[bool, str | None, str | None, str | None]:
    """Attempt an SSH connection to *ip* and return hostname + source IP.

    Args:
        ip: Target IP address string.
        port: SSH port number.
        username: SSH username to authenticate as.
        imported_key: Already-imported asyncssh private key object.

    Returns:
        A four-tuple ``(success, hostname, source_ip, ssh_error)`` where:

        * ``success``    -- True when the SSH session was established.
        * ``hostname``   -- Resolved hostname string on success (falls back to
                           reverse-DNS then the IP itself), or None on failure.
        * ``source_ip``  -- The IP address the remote host sees as the origin
                           of the connection, or None when unavailable.
        * ``ssh_error``  -- Human-readable error message on failure, or None
                           on success.
    """
    try:
        async with ssh_connect(
            ip,
            port=port,
            username=username,
            client_keys=[imported_key],
        ) as conn:
            result = await conn.run("hostname", check=True)
            hostname: str | None = result.stdout.strip() or None
            source_ip: str | None = await get_source_ip(conn)

            # Fall back to reverse DNS if remote returned nothing.
            if not hostname:
                try:
                    fqdn = _socket.getfqdn(ip)
                    if fqdn != ip:
                        hostname = fqdn
                except Exception:
                    pass

            if not hostname:
                hostname = ip

            return True, hostname, source_ip, None

    except Exception as exc:
        err = str(exc)
        if "Permission denied" in err or "Auth" in err:
            err = f"SSH auth failed for {username}@{ip}"
        elif "refused" in err.lower():
            err = f"SSH connection refused on {ip}:{port}"
        elif "timed out" in err.lower() or "Timeout" in err:
            err = f"SSH connection timed out for {ip}"
        else:
            err = f"SSH failed: {err[:120]}"
        return False, None, None, err
