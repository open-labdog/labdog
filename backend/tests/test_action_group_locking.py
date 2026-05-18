"""Per-host advisory-lock tests for the group-dispatch action path.

Covers multi-host claim-or-defer at task entry and per-member
dispatch-next-pending in the finally block of
``app.tasks.action_group._run_action_group_async``.

These tests bypass the actual ansible-runner invocation and assert
that lock plumbing happens correctly. The full snapshot / playbook /
verify pipeline is covered in ``test_action_group.py``.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy import select

from app.models.action_run import ActionHostRun, ActionRun
from app.models.sync_job import SyncJob
from app.tasks.action_group import _run_action_group_async
from tests.conftest import create_group, create_host, create_ssh_key

pytestmark = pytest.mark.integration


class _FakeRedis:
    def __init__(self):
        self.published: list[tuple[str, str]] = []

    def exists(self, *_args):
        return 0

    def publish(self, channel, payload):
        self.published.append((channel, payload))
        return None

    def setex(self, *_args, **_kwargs):
        return None


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


def _runner_ok(host_names: list[str]):
    runner = MagicMock()
    runner.stdout = "PLAY RECAP\n"
    runner.status = "successful"
    runner.rc = 0
    runner.events = [
        {"event": "runner_on_ok", "event_data": {"host": h}} for h in host_names
    ]
    return runner


async def _make_group_with_hosts(db, count: int = 3):
    key = await create_ssh_key(db)
    group = await create_group(db)
    hosts = []
    for i in range(count):
        h = await create_host(
            db,
            hostname=f"glock-h{i}",
            ssh_key_id=key.id,
            ip=f"10.10.0.{i + 1}",
            group_ids=[group.id],
        )
        hosts.append(h)
    await db.flush()
    return group, hosts


async def _make_group_action_run(db, group_id: int) -> int:
    run = ActionRun(
        action_key="k8s-upgrade",
        action_version="1.0",
        host_id=None,
        group_id=group_id,
        parameters={"target_version": "1.30.4"},
        parallelism=1,
        status="queued",
    )
    db.add(run)
    await db.flush()
    await db.commit()
    return run.id


# ---------------------------------------------------------------------------
# Defer cases
# ---------------------------------------------------------------------------


async def test_group_action_defers_when_sync_running_on_member(db, fake_redis):
    """Group action on [h1, h2, h3] defers when a sync runs on h2."""
    group, hosts = await _make_group_with_hosts(db, count=3)

    sj = SyncJob(host_id=hosts[1].id, status="running", module_type="firewall")
    db.add(sj)
    await db.flush()
    await db.commit()

    run_id = await _make_group_action_run(db, group.id)

    run_ansible_mock = MagicMock()
    with patch("app.ansible_runtime.runner.run_ansible", new=run_ansible_mock):
        await _run_action_group_async(run_id)

    # No ansible invocation on the defer path.
    run_ansible_mock.assert_not_called()

    # Parent ActionRun in pending state.
    run = (await db.execute(select(ActionRun).where(ActionRun.id == run_id))).scalar_one()
    assert run.status == "pending"


async def test_two_group_actions_overlap_serialize_no_deadlock(db, fake_redis):
    """Two overlapping group actions on shared members serialize.

    Both acquire locks in sorted member order; the second one observes
    the first as ``running`` on the shared member and defers.
    """
    group_a, hosts_a = await _make_group_with_hosts(db, count=3)
    # group_b overlaps with group_a on hosts[1] and hosts[2].
    key = await create_ssh_key(db)
    group_b = (await create_group(db, name="grpb-overlap"))
    # Reuse hosts[1] and hosts[2] in group_b, plus a new host.
    from sqlalchemy import insert as sa_insert

    from app.models.host import HostGroupMembership

    await db.execute(
        sa_insert(HostGroupMembership).values(host_id=hosts_a[1].id, group_id=group_b.id)
    )
    await db.execute(
        sa_insert(HostGroupMembership).values(host_id=hosts_a[2].id, group_id=group_b.id)
    )
    extra = await create_host(
        db, hostname="gov-extra", ssh_key_id=key.id, ip="10.10.99.1", group_ids=[group_b.id]
    )
    await db.flush()
    _ = extra

    # First run: claim and stay running (we simulate by inserting a row
    # that's already in "running" with a running ActionHostRun on a
    # shared member).
    first_run = ActionRun(
        action_key="k8s-upgrade",
        action_version="1.0",
        host_id=None,
        group_id=group_a.id,
        parameters={},
        parallelism=1,
        status="running",
        started_at=datetime.now(UTC),
    )
    db.add(first_run)
    await db.flush()
    first_hr = ActionHostRun(
        action_run_id=first_run.id, host_id=hosts_a[1].id, status="running"
    )
    db.add(first_hr)
    await db.flush()
    await db.commit()

    # Second run on overlapping group_b should defer.
    second_run_id = await _make_group_action_run(db, group_b.id)

    run_ansible_mock = MagicMock()
    with patch("app.ansible_runtime.runner.run_ansible", new=run_ansible_mock):
        await _run_action_group_async(second_run_id)

    run_ansible_mock.assert_not_called()
    second = (
        await db.execute(select(ActionRun).where(ActionRun.id == second_run_id))
    ).scalar_one()
    assert second.status == "pending"


async def test_group_action_blocks_per_host_on_member_but_not_outsider(db, fake_redis):
    """Group action on [h1,h2,h3] running blocks host action on h2 but
    not host action on h4 (which isn't a member)."""
    from app.tasks.action_host import _run_action_host_async

    group, hosts = await _make_group_with_hosts(db, count=3)
    # An outsider host not in the group.
    key = await create_ssh_key(db)
    outsider = await create_host(db, hostname="out-h", ssh_key_id=key.id, ip="10.10.50.1")
    await db.flush()
    await db.commit()

    # Mark the group ActionRun + ActionHostRun on hosts[1] as running.
    grp_run = ActionRun(
        action_key="k8s-upgrade",
        action_version="1.0",
        host_id=None,
        group_id=group.id,
        parameters={},
        parallelism=1,
        status="running",
        started_at=datetime.now(UTC),
    )
    db.add(grp_run)
    await db.flush()
    grp_hr = ActionHostRun(
        action_run_id=grp_run.id, host_id=hosts[1].id, status="running"
    )
    db.add(grp_hr)
    await db.flush()
    await db.commit()

    # Host-targeted action on hosts[1] should defer.
    h_run = ActionRun(
        action_key="_builtin.drift_check",
        action_version="1.0",
        host_id=hosts[1].id,
        parameters={},
        parallelism=1,
        status="queued",
    )
    db.add(h_run)
    await db.flush()
    h_hr = ActionHostRun(action_run_id=h_run.id, host_id=hosts[1].id, status="queued")
    db.add(h_hr)
    await db.flush()
    await db.commit()

    # Host-targeted action on outsider should NOT defer.
    out_run = ActionRun(
        action_key="_builtin.drift_check",
        action_version="1.0",
        host_id=outsider.id,
        parameters={},
        parallelism=1,
        status="queued",
    )
    db.add(out_run)
    await db.flush()
    out_hr = ActionHostRun(action_run_id=out_run.id, host_id=outsider.id, status="queued")
    db.add(out_hr)
    await db.flush()
    await db.commit()

    runner = MagicMock()
    runner.stdout = ""
    runner.status = "successful"
    runner.rc = 0
    runner.events = []

    with patch("app.ansible_runtime.runner.run_ansible", return_value=runner):
        await _run_action_host_async(h_run.id, h_hr.id)
        await _run_action_host_async(out_run.id, out_hr.id)

    h_hr_after = (
        await db.execute(select(ActionHostRun).where(ActionHostRun.id == h_hr.id))
    ).scalar_one()
    out_hr_after = (
        await db.execute(select(ActionHostRun).where(ActionHostRun.id == out_hr.id))
    ).scalar_one()

    assert h_hr_after.status == "pending"
    assert out_hr_after.status != "pending"


# ---------------------------------------------------------------------------
# Dispatch-next-pending in finally
# ---------------------------------------------------------------------------


async def test_group_finish_dispatches_pending_for_each_member(db, fake_redis):
    """When the group action finishes, dispatch-next-pending fires once
    per claimed member host."""
    group, hosts = await _make_group_with_hosts(db, count=3)

    # Queue a pending sync on each member, with distinct ages so the
    # dispatcher always has a deterministic candidate.
    pending_sync_ids = []
    for i, h in enumerate(hosts):
        sj = SyncJob(host_id=h.id, status="pending", module_type="firewall")
        db.add(sj)
        await db.flush()
        sj.created_at = datetime.now(UTC) - timedelta(seconds=60 - i)
        await db.flush()
        pending_sync_ids.append(sj.id)
    await db.commit()

    run_id = await _make_group_action_run(db, group.id)

    runner = _runner_ok([h.hostname for h in hosts])
    delay_mock = MagicMock()
    with (
        patch("app.ansible_runtime.runner.run_ansible", return_value=runner),
        patch("app.tasks.host_sync_orchestrator.run_host_sync.delay", new=delay_mock),
    ):
        await _run_action_group_async(run_id)

    # One delay() per member host.
    assert delay_mock.call_count == 3
    dispatched_host_ids = {call.kwargs["host_id"] for call in delay_mock.call_args_list}
    assert dispatched_host_ids == {h.id for h in hosts}


async def test_group_defer_does_not_dispatch(db, fake_redis):
    """A deferred group run does NOT call dispatch-next."""
    group, hosts = await _make_group_with_hosts(db, count=2)
    sj = SyncJob(host_id=hosts[0].id, status="running", module_type="firewall")
    db.add(sj)
    await db.flush()
    await db.commit()

    run_id = await _make_group_action_run(db, group.id)

    delay_mock = MagicMock()
    action_delay_mock = MagicMock()
    with (
        patch("app.tasks.host_sync_orchestrator.run_host_sync.delay", new=delay_mock),
        patch("app.tasks.action_orchestrator.run_action.delay", new=action_delay_mock),
    ):
        await _run_action_group_async(run_id)

    delay_mock.assert_not_called()
    action_delay_mock.assert_not_called()


# ---------------------------------------------------------------------------
# FIFO-across-queues
# ---------------------------------------------------------------------------


async def test_fifo_across_queues(db, fake_redis):
    """Submit action then sync — sync gets dispatched by Celery first (in
    this test we simulate that by inserting it first as ``running``);
    when sync finishes, the queued action is dispatched even though both
    target the same host (FIFO across queues by created_at)."""
    from app.tasks.host_sync_orchestrator import _async_run

    key = await create_ssh_key(db)
    host = await create_host(db, ssh_key_id=key.id)
    await db.commit()

    # Pending host-targeted action submitted first (older).
    older_action = ActionRun(
        action_key="_builtin.drift_check",
        action_version="1.0",
        host_id=host.id,
        parameters={},
        parallelism=1,
        status="pending",
    )
    db.add(older_action)
    await db.flush()
    older_action.created_at = datetime.now(UTC) - timedelta(seconds=30)
    await db.flush()
    older_action_id = older_action.id

    # Sync job dispatched and currently running (simulating Celery
    # picked it up first because of dispatcher scheduling).
    sync_job = SyncJob(
        host_id=host.id,
        status="pending",
        module_type="firewall",
    )
    db.add(sync_job)
    await db.flush()
    sync_job.created_at = datetime.now(UTC) - timedelta(seconds=10)
    await db.flush()
    sync_job_id = sync_job.id
    await db.commit()

    # When the sync's finally runs and dispatches, the OLDER candidate
    # (the action) should win the FIFO ordering — even though the
    # action arrived first, in this test the sync was the one Celery
    # picked up. The dispatcher fires the action on the sync's exit.
    action_delay = MagicMock()
    sync_delay = MagicMock()

    # Patch the orchestrator inside the wrapper so the sync completes
    # with all-in-sync outcomes (no real ansible-runner).
    async def _fake_orch(*_args, **_kwargs):
        from app.ansible_runtime.composer import CANONICAL_ORDER

        return {m: "in_sync" for m in CANONICAL_ORDER}, "", "{}"

    @asynccontextmanager
    async def _fake_task_session():
        yield db

    with (
        patch("app.tasks.host_sync_orchestrator.task_session", new=_fake_task_session),
        patch("app.tasks.host_sync_orchestrator.orchestrate_host_sync", new=_fake_orch),
        patch("app.tasks.host_sync_orchestrator.run_host_sync.delay", new=sync_delay),
        patch("app.tasks.action_orchestrator.run_action.delay", new=action_delay),
    ):
        await _async_run(
            job_id=sync_job_id,
            host_id=host.id,
            module_filter=None,
            private_data_dir="/tmp/glock-pdir",
            ssh_key_path="/tmp/glock-key",
        )

    # The OLDER candidate (the action, 30s old) wins the FIFO pick.
    action_delay.assert_called_once_with(older_action_id)
    sync_delay.assert_not_called()
