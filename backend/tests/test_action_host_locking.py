"""Per-host advisory-lock tests for the host-targeted action path.

Covers claim-or-defer at task entry and dispatch-next-pending in the
finally block of ``app.tasks.action_host._run_action_host_async``.

These tests deliberately do NOT exercise ansible-runner — they assert
that the lock plumbing happens (defer happens, dispatch fires) without
needing the full snapshot/playbook/verify pipeline. The success path
is patched at ``run_ansible`` so the per-host task can complete and
trigger the finally-block dispatch.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy import select

from app.models.action_run import ActionHostRun, ActionRun
from app.models.sync_job import SyncJob
from tests.conftest import create_host, create_ssh_key

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
    """Make ``task_session()`` yield the test session so writes are visible."""

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


async def _make_host(db):
    key = await create_ssh_key(db)
    host = await create_host(db, ssh_key_id=key.id)
    return host


async def _make_action_run_on_host(db, host_id: int, *, action_key: str = "_builtin.drift_check"):
    run = ActionRun(
        action_key=action_key,
        action_version="1.0",
        host_id=host_id,
        parameters={},
        parallelism=1,
        status="queued",
    )
    db.add(run)
    await db.flush()
    hr = ActionHostRun(action_run_id=run.id, host_id=host_id, status="queued")
    db.add(hr)
    await db.flush()
    await db.commit()
    return run.id, hr.id


# ---------------------------------------------------------------------------
# Defer cases
# ---------------------------------------------------------------------------


async def test_action_host_defers_when_sync_running_on_same_host(db, fake_redis):
    """A sync running on host X causes action_host on X to defer, and
    both the parent ActionRun and the per-host row are stamped with a
    pending_reason naming the blocking sync."""
    from app.tasks.action_host import _run_action_host_async

    host = await _make_host(db)

    # In-flight sync on this host.
    sj = SyncJob(host_id=host.id, status="running", module_type="firewall")
    db.add(sj)
    await db.flush()
    await db.commit()
    blocking_sync_id = sj.id

    run_id, hr_id = await _make_action_run_on_host(db, host.id)

    run_ansible_mock = MagicMock()
    with patch("app.ansible_runtime.runner.run_ansible", new=run_ansible_mock):
        await _run_action_host_async(run_id, hr_id)

    # ansible-runner never invoked.
    run_ansible_mock.assert_not_called()

    # ActionHostRun + ActionRun status flipped to pending and stamped
    # with a useful diagnostic naming the sync that holds the host.
    hr = (await db.execute(select(ActionHostRun).where(ActionHostRun.id == hr_id))).scalar_one()
    run = (await db.execute(select(ActionRun).where(ActionRun.id == run_id))).scalar_one()
    assert hr.status == "pending"
    assert run.status == "pending"
    expected = f"Waiting for sync {blocking_sync_id} on host {host.hostname}"
    assert hr.pending_reason == expected
    assert run.pending_reason == expected


async def test_action_host_runs_when_no_op_on_host(db, fake_redis):
    """A free host lets the action_host task proceed (no defer)."""
    from app.tasks.action_host import _run_action_host_async

    host = await _make_host(db)
    run_id, hr_id = await _make_action_run_on_host(db, host.id)

    # Patch run_ansible at its import site inside _run_action_host_async.
    runner = MagicMock()
    runner.stdout = ""
    runner.status = "successful"
    runner.rc = 0
    runner.events = []

    with patch("app.ansible_runtime.runner.run_ansible", return_value=runner):
        # Use drift_check action which isn't in the bundled registry —
        # the per-host path will fail because ACTION_REGISTRY.get returns
        # None, but it'll still pass through claim-or-defer and the
        # finally block. We assert lock plumbing, not pipeline.
        await _run_action_host_async(run_id, hr_id)

    # The row is no longer in pending state (it was claimed).
    hr = (await db.execute(select(ActionHostRun).where(ActionHostRun.id == hr_id))).scalar_one()
    assert hr.status != "pending"


async def test_action_host_on_x_does_not_block_action_on_y(db, fake_redis):
    """Action on host X doesn't block an action on host Y."""
    from app.tasks.action_host import _run_action_host_async

    host_x = await _make_host(db)
    host_y_key = await create_ssh_key(db)
    host_y = await create_host(db, ssh_key_id=host_y_key.id, ip="10.0.0.2")

    # In-flight sync on host X.
    sj = SyncJob(host_id=host_x.id, status="running", module_type="firewall")
    db.add(sj)
    await db.flush()
    await db.commit()

    run_id, hr_id = await _make_action_run_on_host(db, host_y.id)

    runner = MagicMock()
    runner.stdout = ""
    runner.status = "successful"
    runner.rc = 0
    runner.events = []
    with patch("app.ansible_runtime.runner.run_ansible", return_value=runner):
        await _run_action_host_async(run_id, hr_id)

    # Host Y action was NOT deferred — host X's sync isn't on the same host.
    hr = (await db.execute(select(ActionHostRun).where(ActionHostRun.id == hr_id))).scalar_one()
    assert hr.status != "pending", f"expected non-pending, got {hr.status}"


# ---------------------------------------------------------------------------
# Dispatch-next-pending in finally
# ---------------------------------------------------------------------------


async def test_action_host_dispatches_pending_sync_on_finish(db, fake_redis):
    """When the action_host finishes, it dispatches the oldest pending op."""
    from app.tasks.action_host import _run_action_host_async

    host = await _make_host(db)

    # Run-now action on this host.
    run_id, hr_id = await _make_action_run_on_host(db, host.id)

    # A queued sync waiting for the host to free up.
    pending_sync = SyncJob(host_id=host.id, status="pending", module_type="firewall")
    db.add(pending_sync)
    await db.flush()
    pending_sync.created_at = datetime.now(UTC) - timedelta(seconds=30)
    await db.flush()
    await db.commit()
    pending_sync_id = pending_sync.id

    runner = MagicMock()
    runner.stdout = ""
    runner.status = "successful"
    runner.rc = 0
    runner.events = []

    delay_mock = MagicMock()
    with (
        patch("app.ansible_runtime.runner.run_ansible", return_value=runner),
        patch("app.tasks.host_sync_orchestrator.run_host_sync.delay", new=delay_mock),
    ):
        await _run_action_host_async(run_id, hr_id)

    # The finally-block dispatcher fired on the queued sync.
    delay_mock.assert_called_once()
    kwargs = delay_mock.call_args.kwargs
    assert kwargs["job_id"] == pending_sync_id
    assert kwargs["host_id"] == host.id


async def test_action_host_defer_does_not_dispatch(db, fake_redis):
    """A deferred action_host does NOT call dispatch-next (that's the
    running op's responsibility, not ours)."""
    from app.tasks.action_host import _run_action_host_async

    host = await _make_host(db)

    # In-flight sync occupies the host.
    sj = SyncJob(host_id=host.id, status="running", module_type="firewall")
    db.add(sj)
    await db.flush()
    await db.commit()

    run_id, hr_id = await _make_action_run_on_host(db, host.id)

    delay_mock = MagicMock()
    action_delay_mock = MagicMock()
    with (
        patch("app.tasks.host_sync_orchestrator.run_host_sync.delay", new=delay_mock),
        patch("app.tasks.action_orchestrator.run_action.delay", new=action_delay_mock),
    ):
        await _run_action_host_async(run_id, hr_id)

    # Deferred path → no dispatch.
    delay_mock.assert_not_called()
    action_delay_mock.assert_not_called()
