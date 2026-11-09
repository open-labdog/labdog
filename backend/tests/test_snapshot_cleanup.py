"""Removing LabDog's snapshots: the delete itself, and the hourly reaper.

BUG-168: right after a rollback, Proxmox's qmeventd holds the guest's
config lock for about 30 s, and a delete sent then failed on the lock
every time, leaving the pre-run snapshot behind. The delete now retries a
lock error.

BUG-169: the action paths left a failed host's snapshot "for the periodic
cleanup task to reap", and no periodic task existed. ``snapshot_reaper``
is that task; it gives a finished run's snapshot the age grace too, so a
snapshot kept for a person to restore by hand is not gone the next hour,
and it leaves the AI sessions' ``labdog-ai-`` snapshots alone.
"""

from __future__ import annotations

import time
import uuid

import pytest

from app.crypto.encryption import encrypt_ssh_key
from app.crypto.key_management import get_master_key
from app.models.action_run import ActionRun
from app.proxmox.client import ProxmoxError
from app.proxmox.models import ProxmoxNode
from app.workflows import snapshot_cleanup
from app.workflows.steps import cleanup as cleanup_mod
from app.workflows.steps.cleanup import delete_snapshot, is_missing_snapshot_error

QMEVENTD_LOCK = ProxmoxError(
    "Task failed: can't lock file '/var/lock/qemu-server/lock-208.conf' - got timeout"
)


class _Clock:
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
    monkeypatch.setattr(cleanup_mod.time, "monotonic", c.monotonic)
    monkeypatch.setattr(cleanup_mod.asyncio, "sleep", c.sleep)
    return c


class _Client:
    """Each delete task fails with the next queued error, then succeeds."""

    def __init__(self, errors: list[Exception]) -> None:
        self.errors = list(errors)
        self.deletes = 0
        self._pending: Exception | None = None

    async def delete_snapshot(self, pve_node, vmid, name, *, vm_type="qemu"):  # noqa: ARG002
        self.deletes += 1
        self._pending = self.errors.pop(0) if self.errors else None
        return f"UPID:{self.deletes}"

    async def wait_for_task(self, pve_node, upid):  # noqa: ARG002
        if self._pending is not None:
            raise self._pending


class TestDeleteSnapshot:
    async def test_a_lock_held_after_a_rollback_is_waited_out(self, clock) -> None:
        client = _Client([QMEVENTD_LOCK, QMEVENTD_LOCK, QMEVENTD_LOCK])

        result = await delete_snapshot(client, "pve", 208, "labdog-411-1")

        assert result == {"deleted": True, "snapshot_name": "labdog-411-1"}
        assert client.deletes == 4
        assert clock.sleeps == [5, 10, 20]

    async def test_a_lock_that_never_clears_fails_after_the_window(self, clock) -> None:
        client = _Client([QMEVENTD_LOCK] * 100)

        with pytest.raises(ProxmoxError, match="can't lock file"):
            await delete_snapshot(client, "pve", 208, "labdog-411-1", lock_wait_seconds=60)

        assert sum(clock.sleeps) == 60

    async def test_any_other_error_fails_at_once(self, clock) -> None:
        client = _Client([ProxmoxError("Task failed: permission denied")])

        with pytest.raises(ProxmoxError, match="permission denied"):
            await delete_snapshot(client, "pve", 208, "labdog-411-1")

        assert client.deletes == 1
        assert clock.sleeps == []

    def test_a_missing_snapshot_is_told_from_a_missing_guest(self) -> None:
        assert is_missing_snapshot_error(
            ProxmoxError("Task failed: snapshot 'labdog-ai-42-1762500000' does not exist")
        )
        assert is_missing_snapshot_error(
            ProxmoxError('{"data":null,"message":"snapshot \'x\' does not exist\\n"}', 500)
        )
        # The mapping may point at the wrong node; the snapshot may still exist.
        assert not is_missing_snapshot_error(
            ProxmoxError("Configuration file 'nodes/pve/qemu-server/208.conf' does not exist")
        )


# ---------------------------------------------------------------------------
# The reaper
# ---------------------------------------------------------------------------


class _FakePve:
    """A Proxmox API with one node and one VM holding the given snapshots."""

    instances: list[_FakePve] = []

    def __init__(self, *_, **__) -> None:
        self.snapshots: list[str] = list(_FakePve.on_vm)
        self.deleted: list[str] = []
        _FakePve.instances.append(self)

    async def list_nodes(self):
        return [{"node": "pve"}]

    async def list_vms(self, node):  # noqa: ARG002
        return [{"vmid": 208}]

    async def list_containers(self, node):  # noqa: ARG002
        return []

    async def _request(self, method, path):  # noqa: ARG002
        return [{"name": n} for n in self.snapshots] + [{"name": "current"}]

    async def delete_snapshot(self, pve_node, vmid, name, *, vm_type="qemu"):  # noqa: ARG002
        self.deleted.append(name)
        return "UPID:1"

    async def wait_for_task(self, pve_node, upid):  # noqa: ARG002
        return None


@pytest.fixture
def pve(monkeypatch):
    _FakePve.instances = []
    _FakePve.on_vm = []
    monkeypatch.setattr(snapshot_cleanup, "ProxmoxClient", _FakePve)
    return _FakePve


async def _node(db) -> None:
    db.add(
        ProxmoxNode(
            name=f"pve-{uuid.uuid4().hex[:6]}",
            api_url="https://pve.test:8006",
            token_id="labdog@pve!ci",
            encrypted_token_secret=encrypt_ssh_key("secret", get_master_key()),
            verify_ssl=False,
        )
    )
    await db.flush()


async def _finished_run(db) -> int:
    run = ActionRun(
        action_key="k8s-upgrade",
        action_version="1.0",
        parameters={},
        parallelism=1,
        status="failed",
    )
    db.add(run)
    await db.flush()
    return run.id


def _deleted(pve) -> list[str]:
    return sorted(name for client in pve.instances for name in client.deleted)


class TestTheReaper:
    async def test_the_manual_cleanup_removes_a_finished_runs_snapshot_now(self, db, pve):
        await _node(db)
        run_id = await _finished_run(db)
        fresh = f"labdog-{run_id}-{int(time.time())}"
        pve.on_vm = [fresh]

        result = await snapshot_cleanup.cleanup_orphaned_snapshots(db)

        assert result["deleted"] == 1
        assert _deleted(pve) == [fresh]

    async def test_the_sweep_gives_a_finished_runs_snapshot_the_max_age(self, db, pve):
        await _node(db)
        run_id = await _finished_run(db)
        fresh = f"labdog-{run_id}-{int(time.time())}"
        old = f"labdog-{run_id}-{int(time.time()) - 25 * 3600}"
        pve.on_vm = [fresh, old]

        result = await snapshot_cleanup.cleanup_orphaned_snapshots(db, grace_for_finished_runs=True)

        assert result["deleted"] == 1
        assert _deleted(pve) == [old]

    async def test_ai_snapshots_are_left_to_their_own_sweep(self, db, pve):
        """Their number is a session id: 'labdog-ai-<n>-…' named a finished
        run whenever run <n> had finished, and was deleted on the spot."""
        await _node(db)
        run_id = await _finished_run(db)
        ai = f"labdog-ai-{run_id}-{int(time.time()) - 30 * 24 * 3600}"
        pve.on_vm = [ai, "before-upgrade"]

        result = await snapshot_cleanup.cleanup_orphaned_snapshots(db)

        assert result["deleted"] == 0
        assert _deleted(pve) == []

    async def test_the_task_runs_the_sweep(self, db, pve, monkeypatch):
        from contextlib import asynccontextmanager

        from app.tasks.snapshot_reaper import _reap_orphaned_snapshots

        @asynccontextmanager
        async def _session():
            yield db

        monkeypatch.setattr("app.db.task_session", _session)
        await _node(db)
        run_id = await _finished_run(db)
        old = f"labdog-{run_id}-{int(time.time()) - 25 * 3600}"
        fresh = f"labdog-{run_id}-{int(time.time())}"
        pve.on_vm = [old, fresh]

        result = await _reap_orphaned_snapshots()

        assert result == {"deleted": 1, "errors": []}
        assert _deleted(pve) == [old]
