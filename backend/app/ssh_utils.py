"""Shared SSH utilities."""

import asyncio
import tempfile
from collections.abc import AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager, AsyncExitStack, asynccontextmanager
from typing import TYPE_CHECKING, Any

import asyncssh

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from app.models.host import Host

# Default SSH connect timeout in seconds.  Overridden at runtime by
# the "ssh.connect_timeout" app setting stored in the database.
SSH_CONNECT_TIMEOUT = 10

# Default per-command timeout in seconds, for one command on an already
# open session. Overridden at runtime by the "ssh.command_timeout" app
# setting. Separate from the connect timeout because they fail
# differently: an unreachable host is a host that is down, a host that
# hangs mid-command is a host that is up and stuck.
SSH_COMMAND_TIMEOUT = 60


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
        from app.settings_service import get_setting_cached_typed

        return int(get_setting_cached_typed("ssh.connect_timeout"))
    except Exception:
        return SSH_CONNECT_TIMEOUT


def _get_command_timeout() -> int:
    """Read the per-command timeout from DB settings, with fallback."""
    try:
        from app.settings_service import get_setting_cached_typed

        return int(get_setting_cached_typed("ssh.command_timeout"))
    except Exception:
        return SSH_COMMAND_TIMEOUT


class BoundedConnection:
    """An ``SSHClientConnection`` whose ``run()`` always has a deadline.

    ``connect_timeout`` bounds getting *to* a host. Nothing bounded what
    happened after: a host that completes TCP and authentication and then
    hangs — a wedged ``nft list ruleset``, a stuck NFS mount under
    ``systemctl list-units``, a D-state process — held the caller forever.
    Thirty of the thirty-two ``conn.run()`` call sites passed no timeout,
    and the ones that mattered most were in the drift sweep and
    ``collect-state``, which walk hosts in one serial loop: a single
    unresponsive host stopped every host after it from being checked at
    all, silently, on every tick.

    A wrapper rather than a `run_checked(conn, cmd, timeout)` helper that
    thirty call sites must remember to use. Every connection LabDog opens
    comes from this module, so binding the deadline here covers the call
    sites that exist and the ones written later, and there is no way to
    opt out by forgetting. A caller that genuinely needs longer passes its
    own ``timeout=`` and this steps aside.

    Everything except ``run`` delegates untouched — notably
    ``create_process``, which the interactive web terminal uses and which
    must *not* have a command deadline.
    """

    __slots__ = ("_conn", "_timeout")

    def __init__(self, conn: asyncssh.SSHClientConnection, timeout: int):
        self._conn = conn
        self._timeout = timeout

    def run(self, *args, **kwargs):
        # Not `async def`: asyncssh's run() returns an awaitable and some
        # callers hold it before awaiting. Defaulting the kwarg and
        # returning the original awaitable keeps that behaviour exact.
        kwargs.setdefault("timeout", self._timeout)
        return self._conn.run(*args, **kwargs)

    def __getattr__(self, name):
        return getattr(self._conn, name)

    @property
    def wrapped(self) -> asyncssh.SSHClientConnection:
        """The underlying connection, for the rare caller that needs it."""
        return self._conn


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

    async def __aenter__(self) -> BoundedConnection:
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
        # Every command on this session gets a deadline; see BoundedConnection.
        return BoundedConnection(self._conn, _get_command_timeout())

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

    async def __aenter__(self) -> BoundedConnection:
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

        # Every command on this session gets a deadline; see BoundedConnection.
        return BoundedConnection(self._conn, _get_command_timeout())

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


@asynccontextmanager
async def host_connection(
    host: "Host",
    db: "AsyncSession | None",
    private_key_pem: str,
    conn: Any = None,
) -> AsyncIterator[Any]:
    """``conn`` when the caller already has one open, else a new connection.

    For the collectors: each takes an optional ``conn`` so that a caller
    collecting several modules from one host — a preview, a collect-state —
    can hand them all one connection (BUG-159), while a caller with no
    connection (the per-module drift sweeps) gets one opened and closed
    around the block, as before. Only a new connection uses ``db``, for
    TOFU, and ``private_key_pem``.
    """
    if conn is not None:
        yield conn
        return
    key = asyncssh.import_private_key(private_key_pem)
    async with ssh_connect_host(host, db, client_keys=[key]) as own:
        yield own


class SharedHostConnection:
    """One SSH connection to a host, for several collectors at once.

    BUG-159: each module's collector opened its own connection, so a preview
    of one host made eight handshakes (about 90 ms each on loopback, against
    3 ms for a command on an open connection), and for a host that does not
    answer each of them waited out the connect timeout in turn: 80 s
    instead of 10.

    Connects on the first command, so a preview where no module has
    anything to read never connects, as before. A failure to connect is
    kept and raised to every later caller at once, rather than waited out
    again. At most ``max_channels`` commands run at a time; OpenSSH allows
    ten sessions per connection by default (``MaxSessions``). A host set to
    fewer refuses the session channel past its limit (OpenSSH answers
    "open failed", ``OPEN_CONNECT_FAILED``). When that happens while other
    commands are running, the refused command waits for them to finish and
    from then on commands go one at a time. A refusal with nothing else
    running is a real failure and is raised.

    Only ``run`` is offered, which is all the collectors use.
    """

    #: Channel-open failures that can mean the server's session limit.
    #: OpenSSH sends the first; other servers the second.
    _REFUSED = frozenset({asyncssh.OPEN_CONNECT_FAILED, asyncssh.OPEN_ADMINISTRATIVELY_PROHIBITED})
    #: Retries of a refused command once one at a time, 0.1 s apart: a
    #: server can free a closed session a moment after the client sees it
    #: close.
    _SERIAL_RETRIES = 5

    def __init__(
        self,
        open_connection: Callable[[], AbstractAsyncContextManager[Any]],
        max_channels: int = 4,
    ):
        self._open_connection = open_connection
        self._stack = AsyncExitStack()
        self._conn: Any = None
        self._error: Exception | None = None
        self._connecting = asyncio.Lock()
        self._channels = asyncio.Semaphore(max_channels)
        self._one_at_a_time = asyncio.Lock()
        self._serial = False
        self._busy = 0
        self._idle = asyncio.Condition()

    async def _connection(self) -> Any:
        if self._conn is not None:
            return self._conn
        async with self._connecting:
            if self._error is not None:
                raise self._error
            if self._conn is None:
                try:
                    self._conn = await self._stack.enter_async_context(self._open_connection())
                except Exception as exc:
                    self._error = exc
                    raise
        return self._conn

    async def run(self, *args, **kwargs):
        conn = await self._connection()
        async with self._channels:
            # Checked again here: a command queued for a channel before the
            # switch must not start alongside the serial ones.
            if not self._serial:
                others = self._busy
                self._busy += 1
                try:
                    return await conn.run(*args, **kwargs)
                except asyncssh.ChannelOpenError as exc:
                    if exc.code not in self._REFUSED or others == 0:
                        raise
                    self._serial = True
                finally:
                    self._busy -= 1
                    if self._busy == 0:
                        async with self._idle:
                            self._idle.notify_all()
        async with self._one_at_a_time:
            async with self._idle:
                await self._idle.wait_for(lambda: self._busy == 0)
            for attempt in range(self._SERIAL_RETRIES):
                try:
                    return await conn.run(*args, **kwargs)
                except asyncssh.ChannelOpenError as exc:
                    if exc.code not in self._REFUSED or attempt == self._SERIAL_RETRIES - 1:
                        raise
                    await asyncio.sleep(0.1)
        raise AssertionError("unreachable")  # pragma: no cover

    async def aclose(self) -> None:
        await self._stack.aclose()


@asynccontextmanager
async def shared_host_connection(
    host: "Host",
    db: "AsyncSession | None",
    private_key_pem: str | None,
    *,
    max_channels: int = 4,
) -> AsyncIterator[SharedHostConnection]:
    """A :class:`SharedHostConnection` to ``host``, closed at the end of the block.

    ``db`` is used for TOFU when the connection is made, as by
    :func:`ssh_connect_host`. A missing or unreadable key is raised to the
    first command, like any other failure to connect.
    """

    def _open() -> AbstractAsyncContextManager[Any]:
        if private_key_pem is None:
            raise ValueError("Host has no SSH key assigned")
        key = asyncssh.import_private_key(private_key_pem)
        return ssh_connect_host(host, db, client_keys=[key])

    shared = SharedHostConnection(_open, max_channels=max_channels)
    try:
        yield shared
    finally:
        await shared.aclose()


async def get_source_ip(conn: "BoundedConnection | asyncssh.SSHClientConnection") -> str | None:
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


async def load_host_key(db: "AsyncSession", host: "Host") -> asyncssh.SSHKey | None:
    """The private key LabDog authenticates to ``host`` with, decrypted.

    ``None`` when the host has no key assigned, the key row is gone, or it
    cannot be decrypted or parsed — every caller treats those alike: there
    is no way in, which is different from a host that refuses the key.
    """
    if not host.ssh_key_id:
        return None
    from sqlalchemy import select

    from app.crypto.encryption import decrypt_ssh_key
    from app.crypto.key_management import get_master_key
    from app.models.ssh_key import SSHKey

    row = (
        await db.execute(select(SSHKey).where(SSHKey.id == host.ssh_key_id))
    ).scalar_one_or_none()
    if row is None:
        return None
    try:
        return asyncssh.import_private_key(
            decrypt_ssh_key(row.encrypted_private_key, get_master_key())
        )
    except Exception:
        return None
