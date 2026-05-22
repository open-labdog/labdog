"""Reboot step for the update workflow.

Checks whether the host requires a reboot (via /var/run/reboot-required),
issues a shutdown if needed, and waits for SSH to become available again.
"""

import asyncio
import time
from typing import TYPE_CHECKING, Any, TypedDict

import asyncssh

from app.ssh_utils import ssh_connect_host

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from app.models.host import Host


class RebootResult(TypedDict):
    """Return type for check_and_reboot."""

    rebooted: bool
    success: bool
    downtime_seconds: float | None
    error: str | None


async def _ssh_is_reachable(
    host: "Host",
    db: "AsyncSession",
    ssh_key_path: str,
    connect_timeout: int = 8,
) -> bool:
    """Attempt a single SSH connection; return True if successful."""
    try:
        async with ssh_connect_host(
            host,
            db,
            client_keys=[ssh_key_path],
            connect_timeout=connect_timeout,
        ):
            pass
        return True
    except Exception:
        return False


async def check_and_reboot(
    host: "Host",
    db: "AsyncSession",
    ssh_key_path: str,
    timeout: int = 300,
) -> RebootResult:
    """Check whether the host needs a reboot and perform one if required.

    Connects via SSH, inspects /var/run/reboot-required, and, when present,
    issues a reboot.  Waits for the host to go down and come back up again
    before returning.

    Args:
        host: Host ORM object with ``ip_address``, ``ssh_port``, ``ssh_user``,
            and ``ssh_host_key_entry`` attributes.
        db: Active async SQLAlchemy session used to persist the TOFU key and
            update the host row.
        ssh_key_path: Path to the private key file on the local filesystem.
        timeout: Maximum seconds to wait for the host to come back after
            rebooting.  Defaults to 300 seconds (5 minutes).

    Returns:
        A :class:`RebootResult` dict with the following keys:

        - ``rebooted`` -- whether a reboot was actually issued.
        - ``success`` -- whether the operation completed without error.
        - ``downtime_seconds`` -- elapsed reboot time (only when rebooted).
        - ``error`` -- human-readable error message on failure.
    """
    # ------------------------------------------------------------------
    # 1. Check whether a reboot is required.
    # ------------------------------------------------------------------
    async with ssh_connect_host(
        host,
        db,
        client_keys=[ssh_key_path],
        connect_timeout=30,
    ) as conn:
        result = await conn.run(
            "test -f /var/run/reboot-required && echo REBOOT_NEEDED || echo NO_REBOOT",
            check=False,
        )
        output = result.stdout.strip()

    if output != "REBOOT_NEEDED":
        return RebootResult(rebooted=False, success=True, downtime_seconds=None, error=None)

    # ------------------------------------------------------------------
    # 2. Issue the reboot (fire-and-forget — the connection will drop).
    # ------------------------------------------------------------------
    try:
        async with ssh_connect_host(
            host,
            db,
            client_keys=[ssh_key_path],
            connect_timeout=30,
        ) as reboot_conn:
            try:
                await reboot_conn.run("shutdown -r now", check=False)
            except Exception:
                # Connection drop on reboot command is expected.
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
        if not await _ssh_is_reachable(host, db, ssh_key_path):
            break

    # ------------------------------------------------------------------
    # 4. Poll until SSH is reachable again (host back up).
    # ------------------------------------------------------------------
    up_deadline = time.monotonic() + timeout
    while time.monotonic() < up_deadline:
        await asyncio.sleep(10)
        if await _ssh_is_reachable(host, db, ssh_key_path):
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


# ---------------------------------------------------------------------------
# Legacy shim — kept so any callers that have not been updated yet continue
# to compile.  Remove once all callers pass Host + db.
# ---------------------------------------------------------------------------


async def _check_and_reboot_legacy(
    host_ip: str,
    ssh_port: int,
    ssh_user: str,
    ssh_key_path: str,
    timeout: int = 300,
    host: Any = None,
    db: Any = None,
) -> RebootResult:
    """Compatibility wrapper; prefer check_and_reboot(host, db, ...)."""
    if host is not None and db is not None:
        return await check_and_reboot(host, db, ssh_key_path, timeout)
    # Fallback to raw asyncssh for callers that do not yet supply host+db.
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

    return RebootResult(
        rebooted=True,
        success=False,
        downtime_seconds=None,
        error="Reboot issued but legacy shim cannot poll for recovery",
    )
