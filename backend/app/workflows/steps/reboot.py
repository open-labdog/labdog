"""Reboot step for the update workflow.

Checks whether the host requires a reboot (via /var/run/reboot-required),
issues a shutdown if needed, and waits for SSH to become available again.
"""

import asyncio
import time
from typing import TypedDict

import asyncssh


class RebootResult(TypedDict):
    """Return type for check_and_reboot."""

    rebooted: bool
    success: bool
    downtime_seconds: float | None
    error: str | None


async def _ssh_is_reachable(
    host_ip: str,
    ssh_port: int,
    ssh_user: str,
    ssh_key_path: str,
    connect_timeout: int = 8,
) -> bool:
    """Attempt a single SSH connection; return True if successful."""
    try:
        conn = await asyncio.wait_for(
            asyncssh.connect(
                host_ip,
                port=ssh_port,
                username=ssh_user,
                client_keys=[ssh_key_path],
                known_hosts=None,
                login_timeout=connect_timeout,
            ),
            timeout=connect_timeout,
        )
        conn.close()
        await conn.wait_closed()
        return True
    except Exception:
        return False


async def check_and_reboot(
    host_ip: str,
    ssh_port: int,
    ssh_user: str,
    ssh_key_path: str,
    timeout: int = 300,
) -> RebootResult:
    """Check whether the host needs a reboot and perform one if required.

    Connects via SSH, inspects /var/run/reboot-required, and, when present,
    issues a reboot.  Waits for the host to go down and come back up again
    before returning.

    Args:
        host_ip: IP address (or hostname) of the target host.
        ssh_port: TCP port for the SSH daemon.
        ssh_user: SSH login username.
        ssh_key_path: Path to the private key file on the local filesystem.
        timeout: Maximum seconds to wait for the host to come back after
            rebooting.  Defaults to 300 seconds (5 minutes).

    Returns:
        A :class:`RebootResult` dict with the following keys:

        - ``rebooted`` – whether a reboot was actually issued.
        - ``success`` – whether the operation completed without error.
        - ``downtime_seconds`` – elapsed reboot time (only when rebooted).
        - ``error`` – human-readable error message on failure.
    """
    # ------------------------------------------------------------------
    # 1. Check whether a reboot is required.
    # ------------------------------------------------------------------
    conn = await asyncio.wait_for(
        asyncssh.connect(
            host_ip,
            port=ssh_port,
            username=ssh_user,
            client_keys=[ssh_key_path],
            known_hosts=None,
        ),
        timeout=30,
    )
    try:
        result = await conn.run(
            "test -f /var/run/reboot-required && echo REBOOT_NEEDED || echo NO_REBOOT",
            check=False,
        )
        output = result.stdout.strip()
    finally:
        conn.close()
        await conn.wait_closed()

    if output != "REBOOT_NEEDED":
        return RebootResult(rebooted=False, success=True, downtime_seconds=None, error=None)

    # ------------------------------------------------------------------
    # 2. Issue the reboot (fire-and-forget — the connection will drop).
    # ------------------------------------------------------------------
    try:
        reboot_conn = await asyncio.wait_for(
            asyncssh.connect(
                host_ip,
                port=ssh_port,
                username=ssh_user,
                client_keys=[ssh_key_path],
                known_hosts=None,
            ),
            timeout=30,
        )
        try:
            await reboot_conn.run("shutdown -r now", check=False)
        finally:
            reboot_conn.close()
            try:
                await reboot_conn.wait_closed()
            except Exception:
                pass
    except Exception:
        # Connection drop on reboot command is expected; swallow all errors.
        pass

    reboot_start = time.monotonic()

    # ------------------------------------------------------------------
    # 3. Wait for SSH to become *unreachable* (host going down).
    # ------------------------------------------------------------------
    down_deadline = time.monotonic() + 60  # max 60 s to see the host go down
    while time.monotonic() < down_deadline:
        await asyncio.sleep(5)
        if not await _ssh_is_reachable(host_ip, ssh_port, ssh_user, ssh_key_path):
            break

    # ------------------------------------------------------------------
    # 4. Poll until SSH is reachable again (host back up).
    # ------------------------------------------------------------------
    up_deadline = time.monotonic() + timeout
    while time.monotonic() < up_deadline:
        await asyncio.sleep(10)
        if await _ssh_is_reachable(host_ip, ssh_port, ssh_user, ssh_key_path):
            downtime = time.monotonic() - reboot_start
            return RebootResult(
                rebooted=True,
                success=True,
                downtime_seconds=round(downtime, 1),
                error=None,
            )

    return RebootResult(
        rebooted=True,
        success=False,
        downtime_seconds=None,
        error="Host did not come back after reboot",
    )
