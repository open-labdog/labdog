"""Shared SSH utilities."""

import asyncio
import tempfile
from typing import TYPE_CHECKING

import asyncssh

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from app.models.host import Host

# Default SSH connect timeout in seconds.  Overridden at runtime by
# the "ssh.connect_timeout" app setting stored in the database.
SSH_CONNECT_TIMEOUT = 10


class HostKeyMismatchError(Exception):
    """Raised when the server's host key does not match the stored key.

    This indicates a potential MITM attack or that the host was legitimately
    re-keyed (OS reinstall, key rotation).  Use
    ``POST /api/hosts/{id}/trust-host-key`` to clear the stored key and
    allow a new TOFU on next connection.
    """


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


def _format_known_hosts_line(ip: str, pub_key: asyncssh.SSHKey) -> str:
    """Format a known_hosts line for *ip* from *pub_key*.

    The OpenSSH ``known_hosts`` line format is:
        <hostname> <keytype> <base64-encoded-key>

    ``export_public_key("openssh")`` returns ``b"<keytype> <base64>\\n"``
    so we strip trailing whitespace and prepend the IP.
    """
    openssh_line = pub_key.export_public_key("openssh").decode().strip()
    return f"{ip} {openssh_line}"


class _HostSSHConnectContext:
    """Async context manager for SSH connections to a known Host ORM row.

    Implements TOFU (Trust On First Use) host-key verification:

    * If ``host.ssh_host_key_entry`` is non-empty the stored key is written
      to a temporary file and passed as ``known_hosts`` to asyncssh.  A key
      mismatch raises :class:`HostKeyMismatchError` (asyncssh surfaces this
      as :class:`asyncssh.HostKeyNotVerifiable`).
    * If ``host.ssh_host_key_entry`` is empty (first contact) any key is
      accepted and the seen key is persisted on the Host row and committed.

    The *db* session is only written when TOFU is triggered; callers that
    pass a session with an active savepoint (test fixtures) will see the
    write merged into the surrounding transaction.
    """

    def __init__(self, host, port, username, client_keys, timeout, db):
        self._host = host
        self._port = port
        self._username = username
        self._client_keys = client_keys
        self._timeout = timeout
        self._db = db
        self._conn = None
        self._tmpfile = None

    async def __aenter__(self) -> asyncssh.SSHClientConnection:
        host = self._host
        ip = host.ip_address

        if host.ssh_host_key_entry:
            # Write the stored key to a temp file for asyncssh.
            self._tmpfile = tempfile.NamedTemporaryFile(
                mode="w",
                suffix=".known_hosts",
                delete=False,
            )
            self._tmpfile.write(host.ssh_host_key_entry + "\n")
            self._tmpfile.flush()
            known_hosts_arg: object = self._tmpfile.name
        else:
            # TOFU: accept whatever key the server presents.
            known_hosts_arg = None

        try:
            self._conn = await asyncio.wait_for(
                asyncssh.connect(
                    ip,
                    port=self._port,
                    username=self._username,
                    client_keys=self._client_keys,
                    known_hosts=known_hosts_arg,
                    login_timeout=self._timeout,
                ),
                timeout=self._timeout,
            )
        except asyncssh.HostKeyNotVerifiable as exc:
            raise HostKeyMismatchError(
                f"SSH host key for {ip} does not match the stored key. "
                f"If the host was legitimately reinstalled, call "
                f"POST /api/hosts/{host.id}/trust-host-key to clear and re-TOFU."
            ) from exc
        finally:
            if self._tmpfile is not None:
                import os

                try:
                    os.unlink(self._tmpfile.name)
                except OSError:
                    pass
                self._tmpfile = None

        # TOFU: persist the server's public key on first successful connect.
        if not host.ssh_host_key_entry:
            server_key = self._conn.get_server_host_key()
            if server_key is not None:
                host.ssh_host_key_entry = _format_known_hosts_line(ip, server_key)
                if self._db is not None:
                    await self._db.commit()

        return self._conn

    async def __aexit__(self, *exc):
        if self._conn:
            self._conn.close()
            await self._conn.wait_closed()

    def __await__(self):
        return self.__aenter__().__await__()


def ssh_connect_host(
    host: "Host",
    db: "AsyncSession",
    client_keys: list | None = None,
    connect_timeout: int | None = None,
) -> _HostSSHConnectContext:
    """Connect via SSH to a Host ORM object with TOFU host-key verification.

    Use this instead of the bare :func:`ssh_connect` for any connection
    to a persisted :class:`~app.models.host.Host` row so that:

    * The server's public key is captured and stored on first contact.
    * Subsequent connections verify the key and raise
      :class:`HostKeyMismatchError` on mismatch (potential MITM).

    Can be used as either:
        async with ssh_connect_host(host, db, client_keys=[...]) as conn: ...
    or:
        conn = await ssh_connect_host(host, db, client_keys=[...])

    Args:
        host: The :class:`~app.models.host.Host` ORM row.  Its
            ``ip_address``, ``ssh_port``, and ``ssh_host_key_entry``
            attributes are read; ``ssh_host_key_entry`` may be written
            when TOFU fires.
        db: Active async SQLAlchemy session used to persist the captured
            key.  A ``commit()`` is issued inside ``__aenter__`` when TOFU
            fires; this is a no-op when the session is wrapped in a
            savepoint (test fixtures).
        client_keys: List of asyncssh private key objects to authenticate
            with.  Defaults to ``None`` (use the SSH agent or default key).
        connect_timeout: Wall-clock timeout in seconds.  Defaults to the
            value returned by :func:`_get_connect_timeout`.
    """
    if connect_timeout is None:
        connect_timeout = _get_connect_timeout()
    return _HostSSHConnectContext(
        host=host,
        port=host.ssh_port,
        username=host.ssh_user,
        client_keys=client_keys,
        timeout=connect_timeout,
        db=db,
    )


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
