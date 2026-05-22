"""Per-host action envelope tests — snapshot / verify / rollback toggles.

Covers the run-time toggles ``snapshot_enabled``, ``verify_enabled``,
``auto_rollback`` on :class:`app.models.action_run.ActionRun` and
verifies the per-host executor (``app.tasks.action_host``) honours
each one in the same way the group-dispatch path
(``app.tasks.action_group``) already does:

* ``snapshot_enabled=False``  → no pre-action snapshot is taken (Phase A
  is skipped, no Proxmox client loaded, no snapshot row recorded).
* ``verify_enabled=False``    → no post-action verify runs (Phase D is
  skipped, even when the playbook took a snapshot).
* ``auto_rollback=False``     → the snapshot is left in place on failure
  (Phase E is skipped, ``rollback_to_snapshot`` is not called).

Lock-plumbing tests (claim-or-defer, dispatch-next-pending) live in
``test_action_host_locking.py``.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy import select

from app.models.action_run import ActionHostRun, ActionRun
from tests.conftest import create_host, create_ssh_key

pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
# Common stubs (mirrors test_action_group.py's helpers — kept local rather
# than imported so this file can be moved/renamed independently)
# ---------------------------------------------------------------------------


class _FakeRedis:
    def __init__(self):
        self.published: list[tuple[str, str]] = []

    def exists(self, *_args):
        return 0  # never cancelled

    def publish(self, channel, payload):
        self.published.append((channel, payload))
        return None

    def setex(self, *_args, **_kwargs):
        return None


@pytest.fixture(autouse=True)
def patch_task_session(db):
    """Make the action_host task's ``task_session()`` yield the test session."""

    @asynccontextmanager
    async def _fake():
        yield db

    with patch("app.db.task_session", new=_fake):
        yield


@pytest.fixture
def fake_redis():
    fr = _FakeRedis()
    with patch("redis.from_url", return_value=fr):
        yield fr


class _FakeProxmoxClient:
    """In-memory stand-in mirroring ``test_action_group.py``'s helper."""

    def __init__(self) -> None:
        self.created: list[tuple[str, int, str]] = []
        self.rolled_back: list[tuple[str, int, str]] = []
        self.deleted: list[tuple[str, int, str]] = []
        self.started: list[tuple[str, int]] = []

    async def create_snapshot(self, pve_node, vmid, name, description=""):  # noqa: ARG002
        self.created.append((pve_node, vmid, name))
        return f"UPID:{pve_node}:{vmid}:{name}"

    async def rollback_snapshot(self, pve_node, vmid, name):
        self.rolled_back.append((pve_node, vmid, name))
        return f"UPID:{pve_node}:{vmid}:rb:{name}"

    async def delete_snapshot(self, pve_node, vmid, name):
        self.deleted.append((pve_node, vmid, name))
        return f"UPID:{pve_node}:{vmid}:rm:{name}"

    async def start_vm(self, pve_node, vmid):
        self.started.append((pve_node, vmid))
        return f"UPID:{pve_node}:{vmid}:start"

    async def wait_for_task(self, *args, **kwargs):  # noqa: ARG002
        return None


@pytest.fixture
def fake_proxmox():
    """Patch ProxmoxClient + short-circuit the rollback's SSH-recovery
    poll so tests don't sleep. Yields the in-memory stub so assertions
    can inspect what was called."""
    client = _FakeProxmoxClient()

    async def _fake_rollback(
        proxmox_client,
        pve_node,
        vmid,
        snapshot_name,
        host,
        ssh_key_path,
        db,  # noqa: ARG001
    ):
        await proxmox_client.rollback_snapshot(pve_node, vmid, snapshot_name)
        await proxmox_client.start_vm(pve_node, vmid)
        return {"success": True}

    with (
        patch("app.proxmox.client.ProxmoxClient", return_value=client),
        patch("app.workflows.steps.rollback.rollback_to_snapshot", side_effect=_fake_rollback),
    ):
        yield client


@pytest.fixture
def fake_verify_pass():
    """Default-pass for the built-in verify helper. Tests that want to
    exercise the verify-skipped path can leave this in place — the
    helper just never gets called when ``verify_enabled=False``."""

    async def _ok(*args, **kwargs):  # noqa: ARG001
        return {"passed": True, "services_ok": True, "packages_ok": True}

    with patch("app.workflows.steps.verify.run_verification", side_effect=_ok):
        yield


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _seed_proxmox_node(db) -> int:
    from app.crypto.encryption import encrypt_ssh_key
    from app.crypto.key_management import get_master_key
    from app.proxmox.models import ProxmoxNode

    node = ProxmoxNode(
        name=f"pve-{id(db)}",
        api_url="https://pve.test:8006",
        token_id="labdog@pve!ci",
        encrypted_token_secret=encrypt_ssh_key("token-secret", get_master_key()),
        verify_ssl=False,
    )
    db.add(node)
    await db.flush()
    return node.id


async def _seed_vm_mapping(db, host_id: int, proxmox_node_id: int, vmid: int) -> None:
    from app.proxmox.vm_mapping import VMMapping

    mapping = VMMapping(
        host_id=host_id,
        proxmox_node_id=proxmox_node_id,
        pve_node_name="pve1",
        vmid=vmid,
        vm_name=f"vm-{vmid}",
    )
    db.add(mapping)
    await db.flush()


def _runner(*, status: str = "successful", rc: int = 0):
    runner = MagicMock()
    runner.stdout = "PLAY RECAP\n"
    runner.status = status
    runner.rc = rc
    runner.events = []
    return runner


async def _make_destructive_run(
    db,
    *,
    snapshot_enabled: bool = True,
    verify_enabled: bool = True,
    auto_rollback: bool = True,
):
    """Create a destructive host-targeted ActionRun with a Proxmox VM
    mapping, returning ``(run_id, host_run_id, host_id, proxmox_node_id)``."""
    key = await create_ssh_key(db)
    host = await create_host(db, ssh_key_id=key.id, hostname="envelope-host")
    pve_id = await _seed_proxmox_node(db)
    await _seed_vm_mapping(db, host.id, pve_id, vmid=801)
    await db.flush()

    # ``linux-upgrade`` is a bundled destructive action with
    # ``supports_host: true``. Use it so the orchestrator's per-host
    # path actually executes the envelope.
    run = ActionRun(
        action_key="linux-upgrade",
        action_version="1.0",
        host_id=host.id,
        parameters={},
        parallelism=1,
        status="queued",
        snapshot_enabled=snapshot_enabled,
        verify_enabled=verify_enabled,
        auto_rollback=auto_rollback,
    )
    db.add(run)
    await db.flush()
    hr = ActionHostRun(action_run_id=run.id, host_id=host.id, status="queued")
    db.add(hr)
    await db.flush()
    await db.commit()
    return run.id, hr.id, host.id, pve_id


# ---------------------------------------------------------------------------
# Toggle: snapshot_enabled
# ---------------------------------------------------------------------------


async def test_snapshot_enabled_false_skips_snapshot_phase(
    db, fake_redis, fake_proxmox, fake_verify_pass
):
    """``snapshot_enabled=False`` → no pre-action snapshot taken, no
    Proxmox snapshot row recorded on the per-host run. The rollback
    helper is also implicitly disabled (no snapshot to revert to)."""
    from app.tasks.action_host import _run_action_host_async

    run_id, hr_id, _, _ = await _make_destructive_run(db, snapshot_enabled=False)

    runner = _runner(status="successful", rc=0)
    with patch("app.ansible_runtime.runner.run_ansible", return_value=runner):
        await _run_action_host_async(run_id, hr_id)

    # No snapshots taken.
    assert fake_proxmox.created == []
    assert fake_proxmox.rolled_back == []
    assert fake_proxmox.deleted == []

    # ActionHostRun has no snapshot_name recorded.
    hr = (await db.execute(select(ActionHostRun).where(ActionHostRun.id == hr_id))).scalar_one()
    assert hr.snapshot_name is None
    assert hr.status == "succeeded"


# ---------------------------------------------------------------------------
# Toggle: verify_enabled
# ---------------------------------------------------------------------------


async def test_verify_enabled_false_skips_verify_phase(db, fake_redis, fake_proxmox):
    """``verify_enabled=False`` → the built-in verify helper is never
    invoked, even when the action took a snapshot."""
    from app.tasks.action_host import _run_action_host_async

    run_id, hr_id, _, _ = await _make_destructive_run(db, verify_enabled=False)

    runner = _runner(status="successful", rc=0)
    verify_mock = MagicMock()

    async def _verify(*args, **kwargs):  # noqa: ARG001
        verify_mock(*args, **kwargs)
        return {"passed": True, "services_ok": True, "packages_ok": True}

    with (
        patch("app.ansible_runtime.runner.run_ansible", return_value=runner),
        patch("app.workflows.steps.verify.run_verification", side_effect=_verify),
    ):
        await _run_action_host_async(run_id, hr_id)

    # Snapshot was taken (snapshot_enabled defaults to True).
    assert len(fake_proxmox.created) == 1
    # Verify helper was NOT invoked — verify_enabled=False short-circuits.
    verify_mock.assert_not_called()

    hr = (await db.execute(select(ActionHostRun).where(ActionHostRun.id == hr_id))).scalar_one()
    # No rollback since playbook succeeded; cleanup did delete the snapshot.
    assert fake_proxmox.rolled_back == []
    assert len(fake_proxmox.deleted) == 1
    assert hr.status == "succeeded"


# ---------------------------------------------------------------------------
# Toggle: auto_rollback
# ---------------------------------------------------------------------------


async def test_auto_rollback_false_keeps_snapshot_on_failure(
    db, fake_redis, fake_proxmox, fake_verify_pass
):
    """``auto_rollback=False`` → when the playbook fails the snapshot is
    NOT reverted, so the operator can inspect / revert manually."""
    from app.tasks.action_host import _run_action_host_async

    run_id, hr_id, _, _ = await _make_destructive_run(db, auto_rollback=False)

    # Playbook fails so the rollback branch would normally fire.
    runner = _runner(status="failed", rc=2)
    with patch("app.ansible_runtime.runner.run_ansible", return_value=runner):
        await _run_action_host_async(run_id, hr_id)

    # Snapshot was taken pre-action.
    assert len(fake_proxmox.created) == 1
    # But NOT rolled back — auto_rollback=false honoured.
    assert fake_proxmox.rolled_back == []
    # And NOT deleted — the snapshot is retained for manual recovery
    # (Phase F cleanup only runs on success).
    assert fake_proxmox.deleted == []

    hr = (await db.execute(select(ActionHostRun).where(ActionHostRun.id == hr_id))).scalar_one()
    assert hr.status == "failed"
    assert hr.snapshot_name is not None  # snapshot row preserved
