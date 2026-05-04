"""Shared SSH utilities."""

import asyncio

import asyncssh

# Default SSH connect timeout in seconds.  Overridden at runtime by
# the "ssh.connect_timeout" app setting stored in the database.
SSH_CONNECT_TIMEOUT = 10


def _get_connect_timeout() -> int:
    """Read SSH connect timeout from DB settings, with fallback."""
    try:
        from app.settings_service import get_setting_sync_typed

        return int(get_setting_sync_typed("ssh.connect_timeout"))
    except Exception:
        return SSH_CONNECT_TIMEOUT


class _SSHConnectContext:
    """Async context manager that wraps asyncssh.connect with a hard timeout.

    asyncssh's login_timeout only covers the SSH handshake/auth phase,
    not the initial TCP connection.  When a host is fully unreachable
    (packets dropped by firewall), the TCP SYN hangs for ~2 minutes.
    This wrapper enforces a wall-clock timeout over the entire connect.
    """

    def __init__(self, host, port, username, client_keys, known_hosts, timeout):
        self._host = host
        self._port = port
        self._username = username
        self._client_keys = client_keys
        self._known_hosts = known_hosts
        self._timeout = timeout
        self._conn = None

    async def __aenter__(self) -> asyncssh.SSHClientConnection:
        self._conn = await asyncio.wait_for(
            asyncssh.connect(
                self._host,
                port=self._port,
                username=self._username,
                client_keys=self._client_keys,
                known_hosts=self._known_hosts,
                login_timeout=self._timeout,
            ),
            timeout=self._timeout,
        )
        return self._conn

    async def __aexit__(self, *exc):
        if self._conn:
            self._conn.close()
            await self._conn.wait_closed()

    def __await__(self):
        return self.__aenter__().__await__()


def ssh_connect(
    host: str,
    port: int = 22,
    username: str = "root",
    client_keys: list | None = None,
    known_hosts: object = None,
    connect_timeout: int | None = None,
) -> _SSHConnectContext:
    """Connect via SSH with a TCP + login timeout.

    Can be used as either:
        async with ssh_connect(...) as conn: ...
    or:
        conn = await ssh_connect(...)
    """
    if connect_timeout is None:
        connect_timeout = _get_connect_timeout()
    return _SSHConnectContext(host, port, username, client_keys, known_hosts, connect_timeout)


async def get_source_ip(conn: asyncssh.SSHClientConnection) -> str | None:
    """Determine what IP the remote host sees us connecting from.

    Uses SSH_CLIENT env var on the remote side, which is authoritative
    even when labdog runs inside a Docker container (NAT/bridge).
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
