"""Snapshot step: create a Proxmox VM snapshot before an update run."""

import asyncio
import logging
import time
from collections.abc import Callable
from typing import Any

logger = logging.getLogger(__name__)

#: Default for ``workflow.snapshot_lock_wait_minutes``. Long enough to sit
#: out a typical nightly vzdump of a single guest.
DEFAULT_LOCK_WAIT_MINUTES = 30

#: Seconds between attempts while the guest is locked.
LOCK_RETRY_INTERVAL_SECONDS = 30

#: Fragments of the messages Proxmox gives when a guest's config is locked
#: by another operation: ``VM 101 is locked (backup)`` /
#: ``CT 101 is locked (snapshot)`` from the lock check, and
#: ``can't lock file '/var/lock/qemu-server/lock-101.conf' - got timeout``
#: when the config file lock itself can't be taken. Either can arrive as
#: the HTTP error on the snapshot POST or as the task's exit status.
_LOCK_MARKERS = ("is locked", "can't lock file")


def is_lock_error(exc: BaseException) -> bool:
    """True when ``exc`` says the guest is locked by another operation."""
    msg = str(exc).lower()
    return any(marker in msg for marker in _LOCK_MARKERS)


def _lock_reason(exc: BaseException) -> str:
    """One readable line out of a Proxmox lock error.

    The HTTP form is a JSON body (``{"data":null,"message":"VM 101 is
    locked (backup)\\n"}``), so pull out the ``message`` when there is one.
    """
    import json  # noqa: PLC0415

    text = str(exc).strip()
    try:
        body = json.loads(text)
    except ValueError:
        body = None
    if isinstance(body, dict) and body.get("message"):
        text = str(body["message"]).strip()
    return text.removeprefix("Task failed: ").splitlines()[0] if text else "locked"


async def create_snapshot(
    proxmox_client: Any,
    pve_node: str,
    vmid: int,
    run_id: int,
    vm_type: str = "qemu",
    action_key: str = "",
    name_prefix: str = "labdog",
    *,
    lock_wait_seconds: int = 0,
    on_lock_wait: Callable[[str], None] | None = None,
) -> str:
    """Create a pre-update VM snapshot and wait for the task to complete.

    The snapshot name embeds the workflow run ID and a Unix timestamp so that
    it is both human-readable and unique across concurrent runs.

    Args:
        proxmox_client: Authenticated
            :class:`~app.proxmox.client.ProxmoxClient` instance.
        pve_node: Proxmox node name that owns the VM.
        vmid: VM identifier.
        run_id: Parent :class:`~app.workflows.models.WorkflowRun` ID, used as
            part of the snapshot name.
        name_prefix: Leading segment of the snapshot name. AI sessions pass
            ``"labdog-ai"`` so that a snapshot the agent took is
            distinguishable from an action run's at a glance in the Proxmox
            UI — they have different lifetimes (an action deletes its own on
            success; an agent's is kept for a retention window so a human can
            still undo the change) and telling them apart matters when
            deciding what is safe to remove by hand.
        lock_wait_seconds: How long to keep retrying while the guest is
            locked by another operation, typically a scheduled backup
            (``vzdump`` holds the lock for the whole backup). ``0`` fails on
            the first lock error, as before. Any other error fails at once.
        on_lock_wait: Called with a human-readable line before each wait and
            once the lock clears, so the caller can surface it in the run's
            live log.

    Returns:
        The snapshot name string (e.g. ``"labdog-42-1711234567"``).

    Raises:
        :class:`~app.proxmox.client.ProxmoxError`: If the Proxmox task fails
            or times out, or the guest is still locked after
            ``lock_wait_seconds``.
    """
    from app.proxmox.client import ProxmoxError  # noqa: PLC0415

    name = f"{name_prefix}-{run_id}-{int(time.time())}"
    logger.info(
        "snapshot: creating snapshot %r for vmid %d on %s",
        name,
        vmid,
        pve_node,
    )

    description = (
        f"unbork pre-run snapshot for action '{action_key}' (run {run_id})"
        if action_key
        else f"unbork pre-run snapshot (run {run_id})"
    )

    def _notify(msg: str) -> None:
        logger.info("snapshot: vmid %d on %s: %s", vmid, pve_node, msg)
        if on_lock_wait is not None:
            try:
                on_lock_wait(msg)
            except Exception:
                logger.debug("snapshot: on_lock_wait callback failed", exc_info=True)

    started = time.monotonic()
    waited_for_lock = False
    while True:
        try:
            upid: str = await proxmox_client.create_snapshot(
                pve_node,
                vmid,
                name,
                description=description,
                vm_type=vm_type,
            )
            await proxmox_client.wait_for_task(pve_node, upid)
            break
        except Exception as exc:
            if not is_lock_error(exc):
                raise
            reason = _lock_reason(exc)
            elapsed = time.monotonic() - started
            remaining = lock_wait_seconds - elapsed
            if remaining <= 0:
                if lock_wait_seconds <= 0:
                    raise
                raise ProxmoxError(
                    f"{reason} — still locked after waiting {_fmt(elapsed)}, giving up"
                ) from exc
            pause = min(LOCK_RETRY_INTERVAL_SECONDS, remaining)
            _notify(
                f"{reason} — waiting for the lock to clear, retrying in {_fmt(pause)} "
                f"(waited {_fmt(elapsed)} of {_fmt(lock_wait_seconds)})"
            )
            waited_for_lock = True
            await asyncio.sleep(pause)

    if waited_for_lock:
        _notify(f"lock cleared after {_fmt(time.monotonic() - started)}")

    logger.info(
        "snapshot: snapshot %r created successfully for vmid %d on %s",
        name,
        vmid,
        pve_node,
    )
    return name


def _fmt(seconds: float) -> str:
    """``95`` → ``"1m35s"``; under a minute stays in seconds."""
    s = max(0, int(round(seconds)))
    if s < 60:
        return f"{s}s"
    m, s = divmod(s, 60)
    return f"{m}m{s:02d}s" if s else f"{m}m"


def snapshot_lock_wait_seconds() -> int:
    """The ``workflow.snapshot_lock_wait_minutes`` setting, in seconds."""
    from app.settings_service import get_setting_cached_typed  # noqa: PLC0415

    try:
        return max(0, int(get_setting_cached_typed("workflow.snapshot_lock_wait_minutes"))) * 60
    except Exception:
        return DEFAULT_LOCK_WAIT_MINUTES * 60
