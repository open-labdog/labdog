"""Tests for the group-dispatch action executor.

Covers the ``supports_host: false`` dispatch shape: the orchestrator
hands off to ``app.tasks.action_group.run_action_group`` and the
group task runs ONE ansible-runner invocation against a flat
``all.hosts`` inventory containing every member of the target group.

Validation:

- Orchestrator routes group-target + ``supports_host=False`` to the
  group task (not the per-host fan-out).
- Group task creates one ``ActionHostRun`` row per group member,
  anchored to the actual host (no "driver" anchor).
- Inventory passed to ansible-runner is flat — ``all.hosts`` only,
  no ``children`` / ``control_plane`` / ``workers``.
- Exactly one ansible-runner invocation drives all hosts.
- POST /api/actions/runs rejects host-target submissions for
  ``supports_host=False`` actions with a 400 + clear message.
"""

from __future__ import annotations

import json
from contextlib import asynccontextmanager
from unittest.mock import MagicMock, patch

import pytest

from app.actions.registry import ACTION_REGISTRY
from app.models.action_run import ActionHostRun, ActionRun
from app.tasks.action_orchestrator import _run_action_async
from tests.conftest import create_group, create_host, create_ssh_key

pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
# Common stubs
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
    """Make the orchestrator/group-task ``task_session()`` yield the
    test session so writes made via the API or fixture helpers are
    visible inside the task."""

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


def _fake_runner(*, host_names: list[str], status: str = "successful", rc: int = 0):
    """Build a stand-in ansible-runner ``Runner`` object exposing the
    attributes the group task reads: ``stdout``, ``status``, ``rc``,
    ``events``."""
    runner = MagicMock()
    runner.stdout = "PLAY RECAP\n" + "\n".join(f"{h} : ok=1" for h in host_names)
    runner.status = status
    runner.rc = rc
    # No failure events → all hosts succeeded.
    runner.events = [
        {"event": "runner_on_ok", "event_data": {"host": h}} for h in host_names
    ]
    return runner


# ---------------------------------------------------------------------------
# Orchestrator routing
# ---------------------------------------------------------------------------


async def test_orchestrator_routes_group_target_with_supports_host_false(db, fake_redis):
    """When the action has ``supports_host=False`` AND the run targets a
    group, the orchestrator must hand off to the group dispatch task
    and NOT fan out per-host."""
    # Use the bundled k8s-upgrade action — it's the canonical
    # ``supports_host: false`` action in the registry.
    assert "k8s-upgrade" in ACTION_REGISTRY
    assert ACTION_REGISTRY["k8s-upgrade"].supports_host is False
    assert ACTION_REGISTRY["k8s-upgrade"].supports_group is True

    key = await create_ssh_key(db)
    group = await create_group(db)
    await create_host(db, ssh_key_id=key.id, group_ids=[group.id])
    await create_host(db, ssh_key_id=key.id, group_ids=[group.id])
    await db.flush()

    run = ActionRun(
        action_key="k8s-upgrade",
        action_version="1.0",
        host_id=None,
        group_id=group.id,
        parameters={"target_version": "1.30.4"},
        parallelism=1,
        status="queued",
    )
    db.add(run)
    await db.flush()
    await db.commit()
    run_id = run.id

    sent: list[tuple[str, list]] = []

    def _send_task(name, args=None, queue=None, **_):
        sent.append((name, args or []))
        return MagicMock()

    # Stub send_task so we observe the handoff without dispatching.
    # Also stub celery group/signature so any per-host fall-through would
    # surface in the captured list (it shouldn't).
    captured_per_host: list[str] = []

    def signature(name, args=None, queue=None, **_):
        captured_per_host.append(name)
        return ("sig", name, args)

    with (
        patch(
            "app.tasks.action_orchestrator.celery_app.send_task",
            side_effect=_send_task,
        ),
        patch(
            "app.tasks.action_orchestrator.celery_app.signature",
            side_effect=signature,
        ),
    ):
        await _run_action_async(run_id)

    # Group dispatch task was triggered exactly once with our run id.
    assert ("app.tasks.action_group.run_action_group", [run_id]) in sent
    # No per-host signatures were built.
    assert captured_per_host == []


# ---------------------------------------------------------------------------
# Group dispatch task
# ---------------------------------------------------------------------------


async def test_group_task_creates_one_host_run_per_member(db, fake_redis):
    """The group task creates exactly N ActionHostRun rows (N = group
    members), each anchored to the real host — no driver anchor."""
    key = await create_ssh_key(db)
    group = await create_group(db)
    h1 = await create_host(db, hostname="cl-h1", ssh_key_id=key.id, group_ids=[group.id])
    h2 = await create_host(db, hostname="cl-h2", ssh_key_id=key.id, group_ids=[group.id])
    h3 = await create_host(db, hostname="cl-h3", ssh_key_id=key.id, group_ids=[group.id])
    await db.flush()

    run = ActionRun(
        action_key="k8s-upgrade",
        action_version="1.0",
        host_id=None,
        group_id=group.id,
        parameters={"target_version": "1.30.4"},
        parallelism=1,
        status="queued",
    )
    db.add(run)
    await db.flush()
    await db.commit()
    run_id = run.id

    fake_runner = _fake_runner(host_names=["cl-h1", "cl-h2", "cl-h3"])
    inventories_seen: list[dict] = []
    invocation_count = 0

    def _capture_run(*, playbook_path, inventory_json, **_kwargs):  # noqa: ARG001
        nonlocal invocation_count
        invocation_count += 1
        inventories_seen.append(json.loads(inventory_json))
        return fake_runner

    from app.tasks.action_group import _run_action_group_async

    with patch("app.ansible_runtime.runner.run_ansible", side_effect=_capture_run):
        await _run_action_group_async(run_id)

    # Exactly one ansible-runner invocation drove all hosts.
    assert invocation_count == 1

    # Inventory shape is flat: top-level "all.hosts" keyed by hostname,
    # NO children / control_plane / workers.
    assert len(inventories_seen) == 1
    inv = inventories_seen[0]
    assert "all" in inv
    assert "hosts" in inv["all"]
    assert "children" not in inv["all"], (
        "group dispatch must use a flat inventory; cluster-mode role "
        "grouping was deliberately removed"
    )
    inv_host_keys = set(inv["all"]["hosts"].keys())
    assert inv_host_keys == {"cl-h1", "cl-h2", "cl-h3"}

    # One ActionHostRun row per real host (no extra driver row).
    from sqlalchemy import select

    host_runs = (
        (await db.execute(select(ActionHostRun).where(ActionHostRun.action_run_id == run_id)))
        .scalars()
        .all()
    )
    assert len(host_runs) == 3
    anchored_host_ids = {hr.host_id for hr in host_runs}
    assert anchored_host_ids == {h1.id, h2.id, h3.id}

    # Run-level outcome is succeeded (no failure events from the fake runner).
    run_after = await db.get(ActionRun, run_id)
    assert run_after.status == "succeeded"
    for hr in host_runs:
        assert hr.status == "succeeded"


async def test_group_task_routes_per_host_failures_from_events(db, fake_redis):
    """Per-host failure events from ansible-runner are routed back to
    the matching ActionHostRun row, leaving a partial run."""
    key = await create_ssh_key(db)
    group = await create_group(db)
    h1 = await create_host(db, hostname="part-h1", ssh_key_id=key.id, group_ids=[group.id])
    h2 = await create_host(db, hostname="part-h2", ssh_key_id=key.id, group_ids=[group.id])
    await db.flush()

    run = ActionRun(
        action_key="k8s-upgrade",
        action_version="1.0",
        host_id=None,
        group_id=group.id,
        parameters={"target_version": "1.30.4"},
        parallelism=1,
        status="queued",
    )
    db.add(run)
    await db.flush()
    await db.commit()
    run_id = run.id

    # Build a runner where part-h1 succeeded but part-h2 failed.
    runner = MagicMock()
    runner.stdout = "PLAY RECAP\npart-h1 : ok=1\npart-h2 : failed=1"
    runner.status = "failed"
    runner.rc = 2
    runner.events = [
        {"event": "runner_on_ok", "event_data": {"host": "part-h1"}},
        {
            "event": "runner_on_failed",
            "event_data": {"host": "part-h2", "res": {"msg": "drain timed out"}},
        },
    ]

    from app.tasks.action_group import _run_action_group_async

    with patch("app.ansible_runtime.runner.run_ansible", return_value=runner):
        await _run_action_group_async(run_id)

    from sqlalchemy import select

    host_runs = (
        (await db.execute(select(ActionHostRun).where(ActionHostRun.action_run_id == run_id)))
        .scalars()
        .all()
    )
    by_host = {hr.host_id: hr for hr in host_runs}
    assert by_host[h1.id].status == "succeeded"
    assert by_host[h2.id].status == "failed"
    assert "drain timed out" in (by_host[h2.id].error_message or "")

    run_after = await db.get(ActionRun, run_id)
    assert run_after.status == "partial"


# ---------------------------------------------------------------------------
# Submission validation (re: API rejects host-target for supports_host=False)
# ---------------------------------------------------------------------------


@pytest.fixture
def stub_celery_dispatch():
    """Block any Celery send_task during these tests."""
    with (
        patch("app.api.actions.celery_app", create=True),
        patch("celery.app.base.Celery.send_task"),
    ):
        yield


async def test_api_rejects_host_target_for_supports_host_false(
    superuser_client, db, stub_celery_dispatch
):
    """``POST /api/actions/runs`` with a host_id targeting an action that
    declared ``supports_host: false`` must be rejected with 400 and a
    clear "can only target a group" message — not silently dispatched."""
    host = await create_host(db)
    await db.commit()

    resp = await superuser_client.post(
        "/api/actions/runs",
        json={
            "action_key": "k8s-upgrade",
            "host_id": host.id,
            "parameters": {"target_version": "1.30.4"},
        },
    )
    assert resp.status_code == 400, resp.text
    body = resp.json()
    assert "group" in body["detail"].lower()


# ---------------------------------------------------------------------------
# Snapshot / verify / rollback envelope
# ---------------------------------------------------------------------------


async def _seed_proxmox_node(db) -> int:
    """Insert a minimal ProxmoxNode row and return its id.

    Token secret is encrypted with the test master key just like the
    real API would, so ``decrypt_ssh_key`` round-trips cleanly.
    """
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
    """Attach a VMMapping row tying a host to a Proxmox VM."""
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


def _runner_all_ok(host_names: list[str]):
    runner = MagicMock()
    runner.stdout = "PLAY RECAP\n" + "\n".join(f"{h} : ok=1" for h in host_names)
    runner.status = "successful"
    runner.rc = 0
    runner.events = [
        {"event": "runner_on_ok", "event_data": {"host": h}} for h in host_names
    ]
    return runner


def _runner_mixed(succeeded: list[str], failed: list[str]):
    """Build a runner stub where ``failed`` hosts emit ``runner_on_failed``."""
    runner = MagicMock()
    runner.stdout = "PLAY RECAP"
    runner.status = "failed" if failed else "successful"
    runner.rc = 2 if failed else 0
    events: list[dict] = []
    for h in succeeded:
        events.append({"event": "runner_on_ok", "event_data": {"host": h}})
    for h in failed:
        events.append(
            {
                "event": "runner_on_failed",
                "event_data": {"host": h, "res": {"msg": f"{h} task failed"}},
            }
        )
    runner.events = events
    return runner


class _FakeProxmoxClient:
    """Stand-in for ProxmoxClient used to assert snapshot/rollback/cleanup
    calls without going over the network."""

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
    """Replace ProxmoxClient with the in-memory stub and short-circuit
    the SSH-wait in the rollback step so tests don't sleep for 5 min."""
    client = _FakeProxmoxClient()

    async def _fake_rollback(
        proxmox_client, pve_node, vmid, snapshot_name, host, ssh_key_path, db,  # noqa: ARG001
    ):
        # Mirror the real helper's side-effects we care about: call
        # rollback_snapshot + start_vm + wait_for_task, but skip the
        # SSH-recovery poll.
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
    """Stub the built-in verify helper to always pass — keeps tests
    that aren't focused on verify from running real SSH."""

    async def _ok(*args, **kwargs):  # noqa: ARG001
        return {"passed": True, "services_ok": True, "packages_ok": True}

    with patch("app.workflows.steps.verify.run_verification", side_effect=_ok):
        yield


async def test_snapshot_taken_per_member_with_vm_mapping(
    db, fake_redis, fake_proxmox, fake_verify_pass
):
    """Phase A: every member host with a VM mapping gets a snapshot
    pre-action, in parallel, and the snapshot id lands on its
    ActionHostRun row."""
    key = await create_ssh_key(db)
    group = await create_group(db)
    h1 = await create_host(db, hostname="snap-h1", ssh_key_id=key.id, group_ids=[group.id])
    h2 = await create_host(db, hostname="snap-h2", ssh_key_id=key.id, group_ids=[group.id])
    h3 = await create_host(db, hostname="snap-h3", ssh_key_id=key.id, group_ids=[group.id])
    pve_id = await _seed_proxmox_node(db)
    await _seed_vm_mapping(db, h1.id, pve_id, vmid=101)
    await _seed_vm_mapping(db, h2.id, pve_id, vmid=102)
    await _seed_vm_mapping(db, h3.id, pve_id, vmid=103)
    await db.flush()

    run = ActionRun(
        action_key="k8s-upgrade",
        action_version="1.0",
        host_id=None,
        group_id=group.id,
        parameters={"target_version": "1.30.4"},
        parallelism=1,
        status="queued",
    )
    db.add(run)
    await db.flush()
    await db.commit()
    run_id = run.id

    fake_runner = _runner_all_ok(["snap-h1", "snap-h2", "snap-h3"])
    from app.tasks.action_group import _run_action_group_async

    with patch("app.ansible_runtime.runner.run_ansible", return_value=fake_runner):
        await _run_action_group_async(run_id)

    # Three create_snapshot calls — one per VM-mapped host.
    assert len(fake_proxmox.created) == 3
    snapshotted_vmids = {vmid for _, vmid, _ in fake_proxmox.created}
    assert snapshotted_vmids == {101, 102, 103}

    # ActionHostRun rows carry the snapshot name.
    from sqlalchemy import select

    host_runs = (
        (await db.execute(select(ActionHostRun).where(ActionHostRun.action_run_id == run_id)))
        .scalars()
        .all()
    )
    assert len(host_runs) == 3
    for hr in host_runs:
        assert hr.snapshot_name is not None
        assert hr.snapshot_name.startswith(f"labdog-{run_id}-")

    # Run-level outcome: succeeded (no rollback needed).
    assert fake_proxmox.rolled_back == []
    # Snapshots on succeeded hosts are deleted.
    deleted_vmids = {vmid for _, vmid, _ in fake_proxmox.deleted}
    assert deleted_vmids == {101, 102, 103}

    run_after = await db.get(ActionRun, run_id)
    assert run_after.status == "succeeded"


async def test_hosts_without_vm_mapping_skip_snapshot_silently(
    db, fake_redis, fake_proxmox, fake_verify_pass
):
    """A host without a VMMapping row must NOT crash the run and must
    NOT get a snapshot — just a log notice on its ActionHostRun."""
    key = await create_ssh_key(db)
    group = await create_group(db)
    h1 = await create_host(db, hostname="map-h1", ssh_key_id=key.id, group_ids=[group.id])
    h2 = await create_host(db, hostname="map-h2", ssh_key_id=key.id, group_ids=[group.id])
    pve_id = await _seed_proxmox_node(db)
    # Only h1 has a VM mapping; h2 is bare metal / unmapped.
    await _seed_vm_mapping(db, h1.id, pve_id, vmid=201)
    await db.flush()

    run = ActionRun(
        action_key="k8s-upgrade",
        action_version="1.0",
        host_id=None,
        group_id=group.id,
        parameters={"target_version": "1.30.4"},
        parallelism=1,
        status="queued",
    )
    db.add(run)
    await db.flush()
    await db.commit()
    run_id = run.id

    fake_runner = _runner_all_ok(["map-h1", "map-h2"])
    from app.tasks.action_group import _run_action_group_async

    with patch("app.ansible_runtime.runner.run_ansible", return_value=fake_runner):
        await _run_action_group_async(run_id)

    # Exactly one snapshot taken — only the mapped host.
    assert len(fake_proxmox.created) == 1
    assert fake_proxmox.created[0][1] == 201

    from sqlalchemy import select

    host_runs = (
        (await db.execute(select(ActionHostRun).where(ActionHostRun.action_run_id == run_id)))
        .scalars()
        .all()
    )
    by_host = {hr.host_id: hr for hr in host_runs}
    assert by_host[h1.id].snapshot_name is not None
    assert by_host[h2.id].snapshot_name is None
    # Per-host log mentions the missing mapping for h2.
    assert "no Proxmox VM mapping" in (by_host[h2.id].output or "")

    # Run still succeeds.
    run_after = await db.get(ActionRun, run_id)
    assert run_after.status == "succeeded"


async def test_partial_failure_rolls_back_failed_hosts_only(
    db, fake_redis, fake_proxmox, fake_verify_pass
):
    """After a partial playbook failure: failed hosts are rolled back,
    succeeded hosts are retained, and succeeded snapshots are deleted."""
    key = await create_ssh_key(db)
    group = await create_group(db)
    h_ok = await create_host(db, hostname="ok-host", ssh_key_id=key.id, group_ids=[group.id])
    h_fail = await create_host(
        db, hostname="fail-host", ssh_key_id=key.id, group_ids=[group.id]
    )
    pve_id = await _seed_proxmox_node(db)
    await _seed_vm_mapping(db, h_ok.id, pve_id, vmid=301)
    await _seed_vm_mapping(db, h_fail.id, pve_id, vmid=302)
    await db.flush()

    run = ActionRun(
        action_key="k8s-upgrade",
        action_version="1.0",
        host_id=None,
        group_id=group.id,
        parameters={"target_version": "1.30.4"},
        parallelism=1,
        status="queued",
    )
    db.add(run)
    await db.flush()
    await db.commit()
    run_id = run.id

    runner = _runner_mixed(succeeded=["ok-host"], failed=["fail-host"])
    from app.tasks.action_group import _run_action_group_async

    with patch("app.ansible_runtime.runner.run_ansible", return_value=runner):
        await _run_action_group_async(run_id)

    # Rollback only the failed host's VM.
    rolled_back_vmids = {vmid for _, vmid, _ in fake_proxmox.rolled_back}
    assert rolled_back_vmids == {302}
    # Cleanup only the succeeded host's snapshot.
    deleted_vmids = {vmid for _, vmid, _ in fake_proxmox.deleted}
    assert deleted_vmids == {301}

    from sqlalchemy import select

    host_runs = (
        (await db.execute(select(ActionHostRun).where(ActionHostRun.action_run_id == run_id)))
        .scalars()
        .all()
    )
    by_host = {hr.host_id: hr for hr in host_runs}
    assert by_host[h_ok.id].status == "succeeded"
    assert by_host[h_fail.id].status == "failed"

    run_after = await db.get(ActionRun, run_id)
    assert run_after.status == "partial"


async def test_verify_failure_triggers_rollback_even_when_playbook_succeeded(
    db, fake_redis, fake_proxmox
):
    """A host that succeeded the playbook but fails verify must be
    rolled back and end up status=failed."""
    key = await create_ssh_key(db)
    group = await create_group(db)
    h_good = await create_host(db, hostname="vg-host", ssh_key_id=key.id, group_ids=[group.id])
    h_bad = await create_host(db, hostname="vb-host", ssh_key_id=key.id, group_ids=[group.id])
    pve_id = await _seed_proxmox_node(db)
    await _seed_vm_mapping(db, h_good.id, pve_id, vmid=401)
    await _seed_vm_mapping(db, h_bad.id, pve_id, vmid=402)
    await db.flush()

    run = ActionRun(
        action_key="k8s-upgrade",
        action_version="1.0",
        host_id=None,
        group_id=group.id,
        parameters={"target_version": "1.30.4"},
        parallelism=1,
        status="queued",
    )
    db.add(run)
    await db.flush()
    await db.commit()
    run_id = run.id

    runner = _runner_all_ok(["vg-host", "vb-host"])

    # Built-in verify path: stub run_verification to fail vb-host only.
    async def _fake_verify(host, *args, **kwargs):  # noqa: ARG001
        if host.hostname == "vb-host":
            return {
                "passed": False,
                "services_ok": True,
                "packages_ok": False,
                "reason": "package check failed",
            }
        return {"passed": True, "services_ok": True, "packages_ok": True}

    from app.tasks.action_group import _run_action_group_async

    with (
        patch("app.ansible_runtime.runner.run_ansible", return_value=runner),
        patch(
            "app.workflows.steps.verify.run_verification", side_effect=_fake_verify
        ),
    ):
        await _run_action_group_async(run_id)

    # vb-host should be rolled back, vg-host's snapshot should be deleted.
    rolled_back_vmids = {vmid for _, vmid, _ in fake_proxmox.rolled_back}
    assert rolled_back_vmids == {402}
    deleted_vmids = {vmid for _, vmid, _ in fake_proxmox.deleted}
    assert deleted_vmids == {401}

    from sqlalchemy import select

    host_runs = (
        (await db.execute(select(ActionHostRun).where(ActionHostRun.action_run_id == run_id)))
        .scalars()
        .all()
    )
    by_host = {hr.host_id: hr for hr in host_runs}
    assert by_host[h_good.id].status == "succeeded"
    assert by_host[h_bad.id].status == "failed"
    assert "verification failed" in (by_host[h_bad.id].error_message or "").lower()

    run_after = await db.get(ActionRun, run_id)
    assert run_after.status == "partial"


async def test_all_succeed_aggregates_to_succeeded_and_deletes_snapshots(
    db, fake_redis, fake_proxmox
):
    """When every host succeeds the playbook (and verify), the run
    aggregates to ``succeeded`` and every snapshot is deleted."""
    key = await create_ssh_key(db)
    group = await create_group(db)
    hs = [
        await create_host(
            db, hostname=f"all-ok-{i}", ssh_key_id=key.id, group_ids=[group.id]
        )
        for i in range(3)
    ]
    pve_id = await _seed_proxmox_node(db)
    for i, h in enumerate(hs):
        await _seed_vm_mapping(db, h.id, pve_id, vmid=500 + i)
    await db.flush()

    run = ActionRun(
        action_key="k8s-upgrade",
        action_version="1.0",
        host_id=None,
        group_id=group.id,
        parameters={"target_version": "1.30.4"},
        parallelism=1,
        status="queued",
    )
    db.add(run)
    await db.flush()
    await db.commit()
    run_id = run.id

    runner = _runner_all_ok([h.hostname for h in hs])

    async def _verify_ok(*args, **kwargs):  # noqa: ARG001
        return {"passed": True, "services_ok": True, "packages_ok": True}

    from app.tasks.action_group import _run_action_group_async

    with (
        patch("app.ansible_runtime.runner.run_ansible", return_value=runner),
        patch("app.workflows.steps.verify.run_verification", side_effect=_verify_ok),
    ):
        await _run_action_group_async(run_id)

    # All snapshots deleted, none rolled back.
    assert len(fake_proxmox.deleted) == 3
    assert fake_proxmox.rolled_back == []

    from sqlalchemy import select

    host_runs = (
        (await db.execute(select(ActionHostRun).where(ActionHostRun.action_run_id == run_id)))
        .scalars()
        .all()
    )
    assert {hr.status for hr in host_runs} == {"succeeded"}

    run_after = await db.get(ActionRun, run_id)
    assert run_after.status == "succeeded"


async def test_aggregate_partial_when_mix_of_success_and_failure(
    db, fake_redis, fake_proxmox
):
    """Mixed per-host outcomes must aggregate to ``partial`` at the
    ActionRun level (matches per-host fan-out behaviour)."""
    key = await create_ssh_key(db)
    group = await create_group(db)
    h_ok = await create_host(db, hostname="mix-ok", ssh_key_id=key.id, group_ids=[group.id])
    h_fail = await create_host(db, hostname="mix-fail", ssh_key_id=key.id, group_ids=[group.id])
    # No VM mappings → snapshot/verify/rollback envelope is a no-op for
    # both. The aggregate-status path is what we're testing here.
    await db.flush()

    run = ActionRun(
        action_key="k8s-upgrade",
        action_version="1.0",
        host_id=None,
        group_id=group.id,
        parameters={"target_version": "1.30.4"},
        parallelism=1,
        status="queued",
    )
    db.add(run)
    await db.flush()
    await db.commit()
    run_id = run.id

    runner = _runner_mixed(succeeded=["mix-ok"], failed=["mix-fail"])
    from app.tasks.action_group import _run_action_group_async

    with patch("app.ansible_runtime.runner.run_ansible", return_value=runner):
        await _run_action_group_async(run_id)

    from sqlalchemy import select

    host_runs = (
        (await db.execute(select(ActionHostRun).where(ActionHostRun.action_run_id == run_id)))
        .scalars()
        .all()
    )
    by_host = {hr.host_id: hr for hr in host_runs}
    assert by_host[h_ok.id].status == "succeeded"
    assert by_host[h_fail.id].status == "failed"

    run_after = await db.get(ActionRun, run_id)
    assert run_after.status == "partial"
