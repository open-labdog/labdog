"""Characterisation tests for group-dispatch paths the main suite leaves dark.

``tests/test_action_group.py`` covers the happy envelope. These pin the
branches it does not reach (a snapshot failure, the pack verify playbook,
post-run follow-ups, the soft time limit, an unknown key, the
destructive-without-snapshot notice), so a restructuring of
``app.tasks.action_group`` has something to answer to.
"""

from __future__ import annotations

import dataclasses
import json
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy import select

from app.actions.registry import ACTION_REGISTRY
from app.models.action_run import ActionHostRun, ActionRun
from tests.conftest import create_group, create_host, create_ssh_key

pytestmark = [pytest.mark.integration, pytest.mark.usefixtures("sample_pack")]


class _FakeRedis:
    def __init__(self):
        self.published: list[tuple[str, str]] = []

    def exists(self, *_args):
        return 0

    def publish(self, channel, payload):
        self.published.append((channel, payload))

    def texts(self) -> list[str]:
        return [json.loads(p).get("text") or "" for _, p in self.published]


@pytest.fixture(autouse=True)
def patch_task_session(db):
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


def _runner(host_names, *, status="successful", rc=0, failed=()):
    runner = MagicMock()
    runner.stdout = "PLAY RECAP"
    runner.status = status
    runner.rc = rc
    runner.events = [
        {"event": "runner_on_ok", "event_data": {"host": h}} for h in host_names if h not in failed
    ] + [
        {"event": "runner_on_failed", "event_data": {"host": h, "res": {"msg": f"{h} broke"}}}
        for h in failed
    ]
    return runner


async def _seed_proxmox_node(db) -> int:
    from app.crypto.encryption import encrypt_ssh_key
    from app.crypto.key_management import get_master_key
    from app.proxmox.models import ProxmoxNode

    node = ProxmoxNode(
        name=f"pve-paths-{id(db)}",
        api_url="https://pve.test:8006",
        token_id="labdog@pve!ci",
        encrypted_token_secret=encrypt_ssh_key("token-secret", get_master_key()),
        verify_ssl=False,
    )
    db.add(node)
    await db.flush()
    return node.id


async def _seed_vm_mapping(db, host_id: int, node_id: int, vmid: int) -> None:
    from app.proxmox.vm_mapping import VMMapping

    db.add(
        VMMapping(
            host_id=host_id,
            proxmox_node_id=node_id,
            pve_node_name="pve1",
            vmid=vmid,
            vm_name=f"vm-{vmid}",
        )
    )
    await db.flush()


class _FakeProxmoxClient:
    def __init__(self, fail_vmids=()):
        self.fail_vmids = set(fail_vmids)
        self.created: list[int] = []
        self.rolled_back: list[int] = []
        self.deleted: list[int] = []

    async def create_snapshot(self, pve_node, vmid, name, description="", *, vm_type="qemu"):  # noqa: ARG002
        if vmid in self.fail_vmids:
            raise RuntimeError(f"vm {vmid} storage is full")
        self.created.append(vmid)
        return f"UPID:{vmid}"

    async def delete_snapshot(self, pve_node, vmid, name, *, vm_type="qemu"):  # noqa: ARG002
        self.deleted.append(vmid)
        return f"UPID:rm:{vmid}"

    async def wait_for_task(self, *args, **kwargs):  # noqa: ARG002
        return None


def _proxmox(client):
    async def _fake_rollback(proxmox_client, pve_node, vmid, *_a, **_kw):  # noqa: ARG001
        proxmox_client.rolled_back.append(vmid)
        return {"success": True}

    return (
        patch("app.proxmox.client.ProxmoxClient", return_value=client),
        patch("app.workflows.steps.rollback.rollback_to_snapshot", side_effect=_fake_rollback),
    )


async def _group_run(db, hostnames, *, action_key="k8s-upgrade", **run_kwargs):
    key = await create_ssh_key(db)
    group = await create_group(db)
    hosts = [
        await create_host(db, hostname=name, ssh_key_id=key.id, group_ids=[group.id])
        for name in hostnames
    ]
    run = ActionRun(
        action_key=action_key,
        action_version="1.0",
        host_id=None,
        group_id=group.id,
        parameters={"target_version": "1.30.4"},
        parallelism=1,
        status="queued",
        **run_kwargs,
    )
    db.add(run)
    await db.flush()
    await db.commit()
    return run.id, hosts


async def _host_runs(db, run_id) -> dict[str, ActionHostRun]:
    rows = (
        (await db.execute(select(ActionHostRun).where(ActionHostRun.action_run_id == run_id)))
        .scalars()
        .all()
    )
    return {hr.hostname: hr for hr in rows}


async def _run(run_id):
    from app.tasks.action_group import _run_action_group_async

    await _run_action_group_async(run_id)


async def test_a_failed_snapshot_fails_that_member_and_drops_it_from_the_inventory(db, fake_redis):
    run_id, hosts = await _group_run(db, ["sf-ok", "sf-bad"])
    node_id = await _seed_proxmox_node(db)
    await _seed_vm_mapping(db, hosts[0].id, node_id, vmid=201)
    await _seed_vm_mapping(db, hosts[1].id, node_id, vmid=202)
    await db.commit()

    client = _FakeProxmoxClient(fail_vmids={202})
    seen_inventories: list[str] = []

    def _run_ansible(**kwargs):
        seen_inventories.append(kwargs["inventory_json"])
        return _runner(["sf-ok"])

    async def _ok(*_a, **_kw):
        return {"passed": True}

    p1, p2 = _proxmox(client)
    with (
        p1,
        p2,
        patch("app.workflows.steps.verify.run_verification", side_effect=_ok),
        patch("app.ansible_runtime.runner.run_ansible", side_effect=_run_ansible),
    ):
        await _run(run_id)

    rows = await _host_runs(db, run_id)
    assert rows["sf-bad"].status == "failed"
    assert rows["sf-bad"].error_message == "Snapshot failed: vm 202 storage is full"
    assert "[snapshot] FAILED: vm 202 storage is full" in rows["sf-bad"].output
    assert rows["sf-ok"].status == "succeeded"
    assert len(seen_inventories) == 1
    assert "sf-ok" in seen_inventories[0]
    assert "sf-bad" not in seen_inventories[0]
    run = (await db.execute(select(ActionRun).where(ActionRun.id == run_id))).scalar_one()
    assert run.status == "partial"
    statuses = [json.loads(p) for _, p in fake_redis.published]
    assert {"event": "host_status", "host_run_id": rows["sf-bad"].id, "status": "failed"} in (
        statuses
    )


async def test_a_failing_pack_verify_playbook_fails_and_rolls_back_the_member(
    db, fake_redis, tmp_path
):
    verify = tmp_path / "verify.yml"
    verify.write_text("- hosts: all\n  tasks: []\n")
    action = dataclasses.replace(ACTION_REGISTRY["k8s-upgrade"], verify_playbook_path=verify)

    run_id, hosts = await _group_run(db, ["pv-a", "pv-b"])
    node_id = await _seed_proxmox_node(db)
    await _seed_vm_mapping(db, hosts[0].id, node_id, vmid=301)
    await _seed_vm_mapping(db, hosts[1].id, node_id, vmid=302)
    await db.commit()

    client = _FakeProxmoxClient()
    calls: list[dict] = []

    def _run_ansible(**kwargs):
        calls.append(kwargs)
        if kwargs["playbook_path"] == verify:
            bad = "pv-b" in kwargs["inventory_json"]
            verify_runner = MagicMock()
            verify_runner.stdout = "verify said " + ("no" if bad else "yes")
            verify_runner.status = "failed" if bad else "successful"
            verify_runner.rc = 2 if bad else 0
            return verify_runner
        return _runner(["pv-a", "pv-b"])

    p1, p2 = _proxmox(client)
    with (
        patch.dict(ACTION_REGISTRY, {"k8s-upgrade": action}),
        p1,
        p2,
        patch("app.ansible_runtime.runner.run_ansible", side_effect=_run_ansible),
    ):
        await _run(run_id)

    verify_calls = [c for c in calls if c["playbook_path"] == verify]
    assert len(verify_calls) == 2
    assert all(c["private_data_dir"].rsplit("-verify-", 1)[1].isdigit() for c in verify_calls)
    rows = await _host_runs(db, run_id)
    assert rows["pv-a"].status == "succeeded"
    assert rows["pv-b"].status == "failed"
    assert rows["pv-b"].error_message == "Verify playbook failed (status=failed, rc=2)"
    assert "=== Verify playbook output ===" in rows["pv-b"].output
    assert client.rolled_back == [302]
    assert sorted(client.deleted) == [301, 302]
    assert any(t.startswith("=== Verify playbook output ===\n") for t in fake_redis.texts())


async def test_post_run_sync_is_queued_for_each_succeeded_member_only(db, fake_redis):
    action = dataclasses.replace(
        ACTION_REGISTRY["k8s-upgrade"], destructive=False, post_run_sync=("package",)
    )
    run_id, hosts = await _group_run(db, ["pr-ok", "pr-bad"])

    queue = AsyncMock(return_value=[])
    with (
        patch.dict(ACTION_REGISTRY, {"k8s-upgrade": action}),
        patch("app.sync.post_run.queue_post_run_sync", queue),
        patch(
            "app.ansible_runtime.runner.run_ansible",
            return_value=_runner(["pr-ok", "pr-bad"], status="failed", rc=2, failed={"pr-bad"}),
        ),
    ):
        await _run(run_id)

    assert queue.await_count == 1
    assert queue.await_args.kwargs["host_id"] == hosts[0].id
    assert queue.await_args.kwargs["modules"] == ("package",)


async def test_a_soft_time_limit_fails_the_run_and_its_running_members(db, fake_redis):
    from celery.exceptions import SoftTimeLimitExceeded

    action = dataclasses.replace(ACTION_REGISTRY["k8s-upgrade"], destructive=False)
    run_id, _ = await _group_run(db, ["st-a"])

    with (
        patch.dict(ACTION_REGISTRY, {"k8s-upgrade": action}),
        patch("app.ansible_runtime.runner.run_ansible", side_effect=SoftTimeLimitExceeded()),
    ):
        await _run(run_id)

    run = (await db.execute(select(ActionRun).where(ActionRun.id == run_id))).scalar_one()
    assert run.status == "failed"
    assert run.error_message.startswith("aborted: exceeded task soft time limit")
    rows = await _host_runs(db, run_id)
    assert rows["st-a"].status == "failed"


async def test_an_unknown_action_key_fails_the_run_before_any_member_row(db, fake_redis):
    run_id, _ = await _group_run(db, ["uk-a"], action_key="no-such-action")

    with patch("app.ansible_runtime.runner.run_ansible") as run_ansible:
        await _run(run_id)

    run_ansible.assert_not_called()
    run = (await db.execute(select(ActionRun).where(ActionRun.id == run_id))).scalar_one()
    assert run.status == "failed"
    assert run.error_message == "Unknown action key: no-such-action"
    assert await _host_runs(db, run_id) == {}


async def test_destructive_without_snapshots_says_so_and_takes_none(db, fake_redis):
    run_id, _ = await _group_run(db, ["ns-a"], snapshot_enabled=False)

    with patch("app.ansible_runtime.runner.run_ansible", return_value=_runner(["ns-a"])):
        await _run(run_id)

    assert (
        "[group] destructive action with snapshot_enabled=false "
        "— skipping snapshot envelope, playbook owns rollback\n"
    ) in fake_redis.texts()
    rows = await _host_runs(db, run_id)
    assert rows["ns-a"].status == "succeeded"
    assert rows["ns-a"].snapshot_name is None
