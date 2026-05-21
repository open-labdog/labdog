"""SSH verification helper for discovered hosts.

Extracted from app.api.discovery so both the discovery API and the
background scan runner (app.tasks.scan_run) can share the same logic
without duplication.
"""

from __future__ import annotations

import socket as _socket

import asyncssh

from app.ssh_utils import _format_known_hosts_line, get_source_ip, ssh_connect


def placeholder_hostname(ip: str) -> str:
    """Canonical placeholder used when no real hostname can be resolved.

    Single source of truth for the ``host-<ip>`` shape so every code
    path produces the same string and ``is_placeholder_hostname`` can
    detect it reliably for opportunistic auto-update later.
    """
    return f"host-{ip}"


def is_placeholder_hostname(hostname: str | None, ip: str) -> bool:
    """True if *hostname* matches the canonical placeholder for *ip*."""
    return hostname is not None and hostname == placeholder_hostname(ip)


async def verify_ssh(
    ip: str,
    port: int,
    username: str,
    imported_key: asyncssh.SSHKey,
) -> tuple[bool, str | None, str | None, str | None, str | None]:
    """Attempt an SSH connection to *ip* and return hostname + source IP.

    This function is used during host discovery — the Host row does not
    exist yet.  Any server key is accepted (TOFU semantics) and returned
    so the caller can persist it on the new Host row at create time.

    Args:
        ip: Target IP address string.
        port: SSH port number.
        username: SSH username to authenticate as.
        imported_key: Already-imported asyncssh private key object.

    Returns:
        A five-tuple ``(success, hostname, source_ip, ssh_error,
        ssh_host_key_entry)`` where:

        * ``success``            -- True when the SSH session was established.
        * ``hostname``           -- Resolved hostname string on success (remote
                                   ``hostname`` command, falling back to reverse
                                   DNS), or ``None`` when neither yields a
                                   value.
        * ``source_ip``         -- The IP address the remote host sees as the
                                   origin of the connection, or None.
        * ``ssh_error``         -- Human-readable error message on failure, or
                                   None on success.
        * ``ssh_host_key_entry`` -- A ``known_hosts``-format line for the
                                   server's public key (e.g.
                                   ``"192.168.1.10 ssh-ed25519 AAAA..."``),
                                   or None when the key could not be captured
                                   or the connection failed.
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

            # Capture server host key for TOFU persistence.
            server_key = conn.get_server_host_key()
            ssh_host_key_entry: str | None = None
            if server_key is not None:
                ssh_host_key_entry = _format_known_hosts_line(ip, server_key)

            # Fall back to reverse DNS if remote returned nothing.
            if not hostname:
                try:
                    fqdn = _socket.getfqdn(ip)
                    if fqdn and fqdn != ip:
                        hostname = fqdn
                except Exception:
                    pass

            return True, hostname, source_ip, None, ssh_host_key_entry

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
        return False, None, None, err, None
