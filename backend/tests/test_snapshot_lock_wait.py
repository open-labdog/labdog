"""Pre-run snapshot: wait out a locked VM instead of failing at once.

A scheduled vzdump backup holds the guest's config lock for the whole
backup, and Proxmox refuses a snapshot meanwhile ("VM 101 is locked
(backup)"). ``create_snapshot`` retries such errors for up to
``lock_wait_seconds`` and fails on anything else straight away.
"""

from __future__ import annotations

import pytest

from app.proxmox.client import ProxmoxError
from app.workflows.steps import snapshot as snap_mod
from app.workflows.steps.snapshot import create_snapshot, is_lock_error

HTTP_LOCKED = ProxmoxError('{"data":null,"message":"VM 101 is locked (backup)\\n"}', 500)
TASK_LOCKED = ProxmoxError("Task failed: VM 101 is locked (backup)")
FILE_LOCK = ProxmoxError(
    "Task failed: can't lock file '/var/lock/qemu-server/lock-101.conf' - got timeout"
)


class _Clock:
    """Fake monotonic clock that ``asyncio.sleep`` advances."""

    def __init__(self) -> None:
        self.now = 1000.0
        self.sleeps: list[float] = []

    def monotonic(self) -> float:
        return self.now

    async def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


@pytest.fixture
def clock(monkeypatch):
    c = _Clock()
    monkeypatch.setattr(snap_mod.time, "monotonic", c.monotonic)
    monkeypatch.setattr(snap_mod.asyncio, "sleep", c.sleep)
    return c


class _Client:
    """Fails the first ``len(errors)`` attempts with the given errors.

    Each entry is ``("post", exc)`` for an HTTP error on the snapshot POST
    or ``("task", exc)`` for a task that ends non-OK.
    """

    def __init__(self, errors: list[tuple[str, Exception]]) -> None:
        self.errors = list(errors)
        self.posts = 0
        self._pending: Exception | None = None

    async def create_snapshot(self, pve_node, vmid, name, description="", *, vm_type="qemu"):  # noqa: ARG002
        self.posts += 1
        if self.errors:
            where, exc = self.errors.pop(0)
            if where == "post":
                raise exc
            self._pending = exc
        return "UPID:x"

    async def wait_for_task(self, *args, **kwargs):  # noqa: ARG002
        exc, self._pending = self._pending, None
        if exc is not None:
            raise exc


@pytest.mark.parametrize("exc", [HTTP_LOCKED, TASK_LOCKED, FILE_LOCK])
def test_lock_errors_are_recognised(exc):
    assert is_lock_error(exc)


@pytest.mark.parametrize(
    "exc",
    [
        ProxmoxError("Task failed: snapshot feature is not available"),
        ProxmoxError("Task timed out after 120s"),
        ProxmoxError("permission denied", 403),
    ],
)
def test_other_errors_are_not_lock_errors(exc):
    assert not is_lock_error(exc)


async def test_retries_until_the_lock_clears(clock):
    client = _Client([("post", HTTP_LOCKED), ("task", TASK_LOCKED)])
    lines: list[str] = []

    name = await create_snapshot(
        client, "pve", 101, 7, lock_wait_seconds=600, on_lock_wait=lines.append
    )

    assert name.startswith("labdog-7-")
    assert client.posts == 3
    assert clock.sleeps == [30, 30]
    assert lines[0].startswith("VM 101 is locked (backup) — waiting for the lock to clear")
    assert "waited 0s of 10m" in lines[0]
    assert "waited 30s of 10m" in lines[1]
    assert lines[-1] == "lock cleared after 1m"


async def test_gives_up_after_the_wait_budget(clock):
    client = _Client([("task", TASK_LOCKED)] * 100)
    lines: list[str] = []

    with pytest.raises(ProxmoxError) as info:
        await create_snapshot(
            client, "pve", 101, 7, lock_wait_seconds=75, on_lock_wait=lines.append
        )

    # 30 + 30 + the 15s left, then one last attempt that is still locked.
    assert clock.sleeps == [30, 30, 15]
    assert client.posts == 4
    assert str(info.value) == (
        "VM 101 is locked (backup) — still locked after waiting 1m15s, giving up"
    )
    assert len(lines) == 3


async def test_zero_wait_fails_on_the_first_lock(clock):
    client = _Client([("task", TASK_LOCKED)])

    with pytest.raises(ProxmoxError) as info:
        await create_snapshot(client, "pve", 101, 7)

    assert info.value is TASK_LOCKED
    assert client.posts == 1
    assert clock.sleeps == []


async def test_non_lock_errors_fail_at_once(clock):
    boom = ProxmoxError("Task failed: snapshot feature is not available")
    client = _Client([("task", boom)])

    with pytest.raises(ProxmoxError) as info:
        await create_snapshot(client, "pve", 101, 7, lock_wait_seconds=600)

    assert info.value is boom
    assert client.posts == 1
    assert clock.sleeps == []


async def test_a_failing_callback_does_not_break_the_retry(clock):
    client = _Client([("task", TASK_LOCKED)])

    def _boom(_msg: str) -> None:
        raise RuntimeError("broker down")

    await create_snapshot(client, "pve", 101, 7, lock_wait_seconds=600, on_lock_wait=_boom)
    assert client.posts == 2
