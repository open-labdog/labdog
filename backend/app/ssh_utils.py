"""Shared SSH utilities."""

import asyncssh

# Default SSH connect timeout in seconds.  Prevents drift checks and
# collectors from hanging for minutes when a host is unreachable.
SSH_CONNECT_TIMEOUT = 10


def ssh_connect(
    host: str,
    port: int = 22,
    username: str = "root",
    client_keys: list | None = None,
    known_hosts: object = None,
    connect_timeout: int = SSH_CONNECT_TIMEOUT,
) -> asyncssh.SSHClientConnection:
    """Wrapper around asyncssh.connect with a default connect timeout."""
    return asyncssh.connect(
        host,
        port=port,
        username=username,
        client_keys=client_keys,
        known_hosts=known_hosts,
        login_timeout=connect_timeout,
    )


async def get_source_ip(conn: asyncssh.SSHClientConnection) -> str | None:
    """Determine what IP the remote host sees us connecting from.

    Uses SSH_CLIENT env var on the remote side, which is authoritative
    even when barricade runs inside a Docker container (NAT/bridge).
    Falls back to the local socket address if SSH_CLIENT is unavailable.
    """
    try:
        result = await conn.run("echo $SSH_CLIENT", check=False)
        if result.exit_status == 0 and result.stdout.strip():
            # SSH_CLIENT = "<client_ip> <client_port> <server_port>"
            parts = result.stdout.strip().split()
            if parts:
                return parts[0]
    except Exception:
        pass
    # Fallback to local socket (may be wrong behind NAT/Docker)
    try:
        sockname = conn.get_extra_info("sockname")
        if sockname:
            return sockname[0]
    except Exception:
        pass
    return None
