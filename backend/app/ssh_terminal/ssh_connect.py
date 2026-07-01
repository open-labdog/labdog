"""SSH shell connection helper for terminal sessions."""

import asyncssh
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.crypto.encryption import decrypt_ssh_key
from app.crypto.key_management import get_master_key
from app.models.host import Host
from app.models.ssh_key import SSHKey
from app.ssh_utils import HostKeyMismatchError, ssh_connect_host


class HostNotFoundError(Exception):
    """Raised when host is not found in database."""

    pass


class NoSSHKeyError(Exception):
    """Raised when host has no SSH key assigned."""

    pass


class SSHConnectionError(Exception):
    """Raised when SSH connection fails."""

    pass


async def open_ssh_shell(
    host_id: int,
    db: AsyncSession,
    initial_cols: int = 80,
    initial_rows: int = 24,
) -> tuple[asyncssh.SSHClientConnection, asyncssh.SSHClientProcess]:
    """Open an interactive SSH shell to a remote host.

    Args:
        host_id: ID of the host to connect to
        db: AsyncSession for database queries
        initial_cols: Initial terminal width in columns (default: 80)
        initial_rows: Initial terminal height in rows (default: 24)

    Returns:
        Tuple of (SSH connection, SSH process) for interactive shell

    Raises:
        HostNotFoundError: If host is not found
        NoSSHKeyError: If host has no SSH key assigned
        SSHConnectionError: If SSH connection fails
    """
    # Fetch host from database
    result = await db.execute(select(Host).where(Host.id == host_id))
    host = result.scalar_one_or_none()
    if host is None:
        raise HostNotFoundError(f"Host {host_id} not found")

    # Verify host has an SSH key assigned
    if host.ssh_key_id is None:
        raise NoSSHKeyError(f"Host {host.hostname} has no SSH key assigned")

    # Fetch SSH key from database
    key_result = await db.execute(select(SSHKey).where(SSHKey.id == host.ssh_key_id))
    ssh_key = key_result.scalar_one_or_none()
    if ssh_key is None:
        raise NoSSHKeyError(f"SSH key {host.ssh_key_id} not found")

    # Decrypt SSH private key
    master_key = get_master_key()
    private_pem = decrypt_ssh_key(ssh_key.encrypted_private_key, master_key)
    imported_key = asyncssh.import_private_key(private_pem)

    # Connect to remote host and open PTY shell. Use ssh_connect_host so the
    # interactive session enforces TOFU host-key verification (same as the
    # sync/collector paths) instead of blindly accepting any key — otherwise a
    # MITM could impersonate the host and capture the root PTY.
    try:
        conn = await ssh_connect_host(
            host,
            db,
            client_keys=[imported_key],
        )
        process = await conn.create_process(
            term_type="xterm-256color",
            term_size=(initial_cols, initial_rows),
            encoding=None,
        )
    except HostKeyMismatchError as e:
        raise SSHConnectionError(str(e))
    except (asyncssh.Error, OSError) as e:
        raise SSHConnectionError(f"Failed to connect to {host.hostname}: {e}")

    return conn, process
