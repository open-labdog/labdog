"""Tests for the shared per-host advisory-lock helpers.

These cover the five public helpers in ``app.tasks.host_lock``:

- ``acquire_host_lock``: actually acquires the PG advisory lock so a
  concurrent ``pg_try_advisory_xact_lock`` on the same key fails.
- ``acquire_host_locks``: dedups + sorts ids before locking.
- ``check_host_busy``: returns a :class:`BlockerInfo` for each of the
  three sources (SyncJob, host-targeted ActionRun, group-targeted
  ActionHostRun) and ``None`` otherwise. The returned dataclass carries
  the kind, blocker id, host id, and action key (None for sync) so the
  caller can format a ``pending_reason`` diagnostic.
- ``check_hosts_busy``: returns the BlockerInfo for the smallest busy
  host id; ``None`` when all free.
- ``dispatch_next_pending_for_host``: picks oldest by created_at across
  both queues, returns None on empty queues, honors excludes, is
  defensive when a picked row's host has been deleted.
"""

from __future__ import annotations

import uuid as _uuid
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from tests.conftest import create_host, create_ssh_key

pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_session_patcher(db, *targets: str):
    """Patch ``task_session`` in one or more target modules."""

    @asynccontextmanager
    async def _fake_task_session():
        yield db

    patches = [patch(t, new=_fake_task_session) for t in targets]
    return patches


@asynccontextmanager
async def _patches(*ctxs):
    """Combine multiple sync context managers into one async-friendly block."""
    entered = []
    try:
        for c in ctxs:
            entered.append(c.__enter__())
        yield
    finally:
        for c in reversed(ctxs):
            try:
                c.__exit__(None, None, None)
            except Exception:
                pass


async def _create_pending_sync_job(
    db: AsyncSession, host_id: int, *, module_type: str = "firewall", age_seconds: int = 0
) -> int:
    from app.models.sync_job import SyncJob

    job = SyncJob(host_id=host_id, status="pending", module_type=module_type)
    db.add(job)
    await db.flush()
    if age_seconds:
        job.created_at = datetime.now(UTC) - timedelta(seconds=age_seconds)
        await db.flush()
    return job.id


async def _create_running_sync_job(
    db: AsyncSession, host_id: int, *, module_type: str = "firewall"
) -> int:
    from app.models.sync_job import SyncJob

    job = SyncJob(
        host_id=host_id, status="running", module_type=module_type, started_at=datetime.now(UTC)
    )
    db.add(job)
    await db.flush()
    return job.id


async def _create_action_run(
    db: AsyncSession,
    *,
    host_id: int | None = None,
    group_id: int | None = None,
    status: str = "pending",
    action_key: str = "_builtin.collect_state",
    age_seconds: int = 0,
) -> int:
    from app.models.action_run import ActionRun

    run = ActionRun(
        action_key=action_key,
        action_version="1.0.0",
        host_id=host_id,
        group_id=group_id,
        status=status,
        parameters={},
        parallelism=1,
    )
    db.add(run)
    await db.flush()
    if age_seconds:
        run.created_at = datetime.now(UTC) - timedelta(seconds=age_seconds)
        await db.flush()
    return run.id


async def _create_action_host_run(
    db: AsyncSession, action_run_id: int, host_id: int, *, status: str = "queued"
) -> int:
    from app.models.action_run import ActionHostRun

    hr = ActionHostRun(action_run_id=action_run_id, host_id=host_id, status=status)
    db.add(hr)
    await db.flush()
    return hr.id


# ---------------------------------------------------------------------------
# acquire_host_lock / acquire_host_locks
# ---------------------------------------------------------------------------


async def test_acquire_host_lock_actually_blocks(pg_url):
    """A second transaction can't take ``pg_try_advisory_xact_lock`` on the same key."""
    from app.tasks.host_lock import acquire_host_lock

    engine = create_async_engine(pg_url, pool_size=4, max_overflow=4)
    SessionMaker = async_sessionmaker(engine, expire_on_commit=False)

    key = 9_900_001  # arbitrary, unlikely to collide with real host ids
    try:
        async with SessionMaker() as a, SessionMaker() as b:
            await a.begin()
            await b.begin()
            await acquire_host_lock(a, key)
            # Different connection should fail to grab the same key.
            tried = (
                await b.execute(text("SELECT pg_try_advisory_xact_lock(:k)"), {"k": key})
            ).scalar()
            assert tried is False, "second tx should not be able to acquire the same lock"
            # A different key should succeed.
            tried_other = (
                await b.execute(text("SELECT pg_try_advisory_xact_lock(:k)"), {"k": key + 1})
            ).scalar()
            assert tried_other is True
            await a.rollback()
            await b.rollback()
    finally:
        await engine.dispose()


async def test_acquire_host_locks_dedup_and_sorts(db: AsyncSession):
    """``acquire_host_locks`` issues one call per unique id in ascending order."""
    from app.tasks import host_lock

    captured: list[int] = []

    async def _fake_execute(stmt, params=None, **kwargs):
        # Bound execute on the session — capture the param.
        if params is not None and "h" in params:
            captured.append(params["h"])
        # Real call still needs to succeed so the rest of the session
        # state doesn't get poisoned.
        return await AsyncSession.execute(db, stmt, params, **kwargs)

    with patch.object(db, "execute", side_effect=_fake_execute):
        await host_lock.acquire_host_locks(db, [5, 2, 5, 1, 2, 7])

    assert captured == [1, 2, 5, 7], f"expected sorted/deduped, got {captured}"


async def test_acquire_host_locks_empty_noop(db: AsyncSession):
    """Empty list ⇒ no execute calls."""
    from app.tasks import host_lock

    with patch.object(db, "execute") as exec_mock:
        await host_lock.acquire_host_locks(db, [])
    exec_mock.assert_not_called()


# ---------------------------------------------------------------------------
# check_host_busy
# ---------------------------------------------------------------------------


async def test_check_host_busy_false_when_nothing_running(db: AsyncSession):
    from app.tasks.host_lock import check_host_busy

    ssh = await create_ssh_key(db)
    host = await create_host(db, ssh_key_id=ssh.id)
    assert (await check_host_busy(db, host.id)) is None


async def test_check_host_busy_true_for_running_sync_job(db: AsyncSession):
    from app.tasks.host_lock import check_host_busy

    ssh = await create_ssh_key(db)
    host = await create_host(db, ssh_key_id=ssh.id)
    sync_id = await _create_running_sync_job(db, host.id)
    blocker = await check_host_busy(db, host.id)
    assert blocker is not None
    assert blocker.kind == "sync"
    assert blocker.id == sync_id
    assert blocker.host_id == host.id
    assert blocker.action_key is None


async def test_check_host_busy_true_for_running_host_action(db: AsyncSession):
    from app.tasks.host_lock import check_host_busy

    ssh = await create_ssh_key(db)
    host = await create_host(db, ssh_key_id=ssh.id)
    run_id = await _create_action_run(
        db, host_id=host.id, status="running", action_key="_builtin.collect_state"
    )
    blocker = await check_host_busy(db, host.id)
    assert blocker is not None
    assert blocker.kind == "action_host"
    assert blocker.id == run_id
    assert blocker.host_id == host.id
    assert blocker.action_key == "_builtin.collect_state"


async def test_check_host_busy_true_for_running_group_action_member(db: AsyncSession):
    from app.tasks.host_lock import check_host_busy
    from tests.conftest import create_group

    group = await create_group(db)
    ssh = await create_ssh_key(db)
    host = await create_host(db, ssh_key_id=ssh.id, group_ids=[group.id])
    run_id = await _create_action_run(
        db, group_id=group.id, status="running", action_key="k8s-upgrade"
    )
    await _create_action_host_run(db, run_id, host.id, status="running")
    blocker = await check_host_busy(db, host.id)
    assert blocker is not None
    assert blocker.kind == "action_group"
    assert blocker.id == run_id
    assert blocker.host_id == host.id
    assert blocker.action_key == "k8s-upgrade"


async def test_check_host_busy_ignores_other_hosts(db: AsyncSession):
    from app.tasks.host_lock import check_host_busy

    ssh = await create_ssh_key(db)
    host_a = await create_host(db, ssh_key_id=ssh.id, ip="10.0.0.10")
    host_b = await create_host(db, ssh_key_id=ssh.id, ip="10.0.0.11")
    await _create_running_sync_job(db, host_a.id)
    assert (await check_host_busy(db, host_a.id)) is not None
    assert (await check_host_busy(db, host_b.id)) is None


# ---------------------------------------------------------------------------
# check_hosts_busy
# ---------------------------------------------------------------------------


async def test_check_hosts_busy_returns_smallest_busy(db: AsyncSession):
    from app.tasks.host_lock import check_hosts_busy

    ssh = await create_ssh_key(db)
    h1 = await create_host(db, ssh_key_id=ssh.id, ip="10.0.0.21")
    h2 = await create_host(db, ssh_key_id=ssh.id, ip="10.0.0.22")
    h3 = await create_host(db, ssh_key_id=ssh.id, ip="10.0.0.23")

    # Mark h2 and h3 busy via different sources; the smallest busy id wins.
    sync_id = await _create_running_sync_job(db, h3.id)
    action_id = await _create_action_run(
        db, host_id=h2.id, status="running", action_key="_builtin.drift_check"
    )

    blocker = await check_hosts_busy(db, [h1.id, h2.id, h3.id])
    assert blocker is not None
    assert blocker.host_id == min(h2.id, h3.id)
    # The winner's identity matches the row we inserted on that host.
    if blocker.host_id == h2.id:
        assert blocker.kind == "action_host"
        assert blocker.id == action_id
        assert blocker.action_key == "_builtin.drift_check"
    else:
        assert blocker.kind == "sync"
        assert blocker.id == sync_id
        assert blocker.action_key is None


async def test_check_hosts_busy_none_when_all_free(db: AsyncSession):
    from app.tasks.host_lock import check_hosts_busy

    ssh = await create_ssh_key(db)
    h1 = await create_host(db, ssh_key_id=ssh.id, ip="10.0.0.31")
    h2 = await create_host(db, ssh_key_id=ssh.id, ip="10.0.0.32")
    assert (await check_hosts_busy(db, [h1.id, h2.id])) is None


async def test_check_hosts_busy_empty_returns_none(db: AsyncSession):
    from app.tasks.host_lock import check_hosts_busy

    assert (await check_hosts_busy(db, [])) is None


async def test_check_hosts_busy_picks_group_action_when_first(db: AsyncSession):
    """When the smallest busy member is held by a group-dispatch action,
    the returned BlockerInfo identifies the group ActionRun + action_key."""
    from app.tasks.host_lock import check_hosts_busy
    from tests.conftest import create_group

    group = await create_group(db)
    ssh = await create_ssh_key(db)
    # Two hosts in the group, both members.
    h1 = await create_host(db, ssh_key_id=ssh.id, ip="10.0.0.41", group_ids=[group.id])
    h2 = await create_host(db, ssh_key_id=ssh.id, ip="10.0.0.42", group_ids=[group.id])
    run_id = await _create_action_run(
        db, group_id=group.id, status="running", action_key="k8s-upgrade"
    )
    # Mark BOTH members as held by the running group run.
    await _create_action_host_run(db, run_id, h1.id, status="running")
    await _create_action_host_run(db, run_id, h2.id, status="running")

    blocker = await check_hosts_busy(db, [h1.id, h2.id])
    assert blocker is not None
    assert blocker.host_id == min(h1.id, h2.id)
    assert blocker.kind == "action_group"
    assert blocker.id == run_id
    assert blocker.action_key == "k8s-upgrade"


# ---------------------------------------------------------------------------
# format_pending_reason
# ---------------------------------------------------------------------------


async def test_format_pending_reason_sync_uses_hostname(db: AsyncSession):
    """A ``sync`` blocker renders without an action_key suffix."""
    from app.tasks.host_lock import BlockerInfo, format_pending_reason

    ssh = await create_ssh_key(db)
    host = await create_host(db, hostname="node-fmt-1", ssh_key_id=ssh.id)
    blocker = BlockerInfo(kind="sync", id=47, host_id=host.id, action_key=None)
    reason = await format_pending_reason(db, blocker)
    assert reason == "Waiting for sync 47 on host node-fmt-1"


async def test_format_pending_reason_action_includes_key(db: AsyncSession):
    """An action blocker (host or group) includes ``(action_key)`` suffix."""
    from app.tasks.host_lock import BlockerInfo, format_pending_reason

    ssh = await create_ssh_key(db)
    host = await create_host(db, hostname="node-fmt-2", ssh_key_id=ssh.id)
    blocker = BlockerInfo(kind="action_group", id=12, host_id=host.id, action_key="k8s-upgrade")
    reason = await format_pending_reason(db, blocker)
    assert reason == "Waiting for action_group 12 on host node-fmt-2 (k8s-upgrade)"


async def test_format_pending_reason_falls_back_when_host_deleted(db: AsyncSession):
    """When the blocker references a missing host id we still produce a
    usable diagnostic (host id placeholder) rather than crashing."""
    from app.tasks.host_lock import BlockerInfo, format_pending_reason

    # 999_999 is unlikely to map to a real host in the test session.
    blocker = BlockerInfo(kind="sync", id=3, host_id=999_999, action_key=None)
    reason = await format_pending_reason(db, blocker)
    assert "999999" in reason or "host:999999" in reason
    assert "sync 3" in reason


# ---------------------------------------------------------------------------
# dispatch_next_pending_for_host
# ---------------------------------------------------------------------------


async def test_dispatch_picks_oldest_across_queues(db: AsyncSession):
    """FIFO across the SyncJob and ActionRun queues."""
    from app.tasks.host_lock import dispatch_next_pending_for_host

    ssh = await create_ssh_key(db)
    host = await create_host(db, ssh_key_id=ssh.id)

    # SyncJob 30s old, ActionRun 10s old — sync should win.
    older_sync = await _create_pending_sync_job(db, host.id, age_seconds=30)
    younger_action = await _create_action_run(db, host_id=host.id, status="pending", age_seconds=10)

    sync_delay = MagicMock()
    action_delay = MagicMock()
    with (
        patch("app.tasks.host_sync_orchestrator.run_host_sync.delay", new=sync_delay),
        patch("app.tasks.action_orchestrator.run_action.delay", new=action_delay),
    ):
        result = await dispatch_next_pending_for_host(db, host.id)

    assert result == ("sync", older_sync)
    sync_delay.assert_called_once()
    action_delay.assert_not_called()

    # Now reverse the ages — action wins.
    from app.models.action_run import ActionRun
    from app.models.sync_job import SyncJob

    sync_row = (await db.execute(select(SyncJob).where(SyncJob.id == older_sync))).scalar_one()
    sync_row.created_at = datetime.now(UTC) - timedelta(seconds=5)
    action_row = (
        await db.execute(select(ActionRun).where(ActionRun.id == younger_action))
    ).scalar_one()
    action_row.created_at = datetime.now(UTC) - timedelta(seconds=60)
    await db.flush()

    sync_delay.reset_mock()
    action_delay.reset_mock()
    with (
        patch("app.tasks.host_sync_orchestrator.run_host_sync.delay", new=sync_delay),
        patch("app.tasks.action_orchestrator.run_action.delay", new=action_delay),
    ):
        result = await dispatch_next_pending_for_host(db, host.id)

    assert result == ("action_host", younger_action)
    action_delay.assert_called_once_with(younger_action)
    sync_delay.assert_not_called()


async def test_dispatch_returns_none_when_empty(db: AsyncSession):
    from app.tasks.host_lock import dispatch_next_pending_for_host

    ssh = await create_ssh_key(db)
    host = await create_host(db, ssh_key_id=ssh.id)

    sync_delay = MagicMock()
    action_delay = MagicMock()
    with (
        patch("app.tasks.host_sync_orchestrator.run_host_sync.delay", new=sync_delay),
        patch("app.tasks.action_orchestrator.run_action.delay", new=action_delay),
    ):
        result = await dispatch_next_pending_for_host(db, host.id)
    assert result is None
    sync_delay.assert_not_called()
    action_delay.assert_not_called()


async def test_dispatch_honors_excludes(db: AsyncSession):
    """When the only candidate is the excluded one, the helper returns None."""
    from app.tasks.host_lock import dispatch_next_pending_for_host

    ssh = await create_ssh_key(db)
    host = await create_host(db, ssh_key_id=ssh.id)

    sync_id = await _create_pending_sync_job(db, host.id, age_seconds=30)
    action_id = await _create_action_run(db, host_id=host.id, status="pending", age_seconds=10)

    sync_delay = MagicMock()
    action_delay = MagicMock()
    with (
        patch("app.tasks.host_sync_orchestrator.run_host_sync.delay", new=sync_delay),
        patch("app.tasks.action_orchestrator.run_action.delay", new=action_delay),
    ):
        # Exclude both — should be None.
        result = await dispatch_next_pending_for_host(
            db,
            host.id,
            exclude_sync_job_id=sync_id,
            exclude_action_run_id=action_id,
        )
    assert result is None
    sync_delay.assert_not_called()
    action_delay.assert_not_called()


async def test_dispatch_picks_group_action(db: AsyncSession):
    """A group ActionRun whose member set includes the freed host is dispatched."""
    from app.tasks.host_lock import dispatch_next_pending_for_host
    from tests.conftest import create_group

    group = await create_group(db)
    ssh = await create_ssh_key(db)
    host = await create_host(db, ssh_key_id=ssh.id, group_ids=[group.id])

    run_id = await _create_action_run(db, group_id=group.id, status="pending", age_seconds=5)

    sync_delay = MagicMock()
    action_delay = MagicMock()
    with (
        patch("app.tasks.host_sync_orchestrator.run_host_sync.delay", new=sync_delay),
        patch("app.tasks.action_orchestrator.run_action.delay", new=action_delay),
    ):
        result = await dispatch_next_pending_for_host(db, host.id)
    assert result == ("action_group", run_id)
    action_delay.assert_called_once_with(run_id)
    sync_delay.assert_not_called()


async def test_dispatch_defensive_when_sync_host_deleted(db: AsyncSession):
    """When a SyncJob candidate's host has been deleted between SELECT and
    dispatch, the helper marks the row failed and falls through.

    Real-world the FK on ``sync_jobs.host_id`` is ON DELETE CASCADE, so
    deleting the host wipes the SyncJob too. The defensive branch is a
    belt-and-braces guard in case the schema ever loosens or a stale
    row sneaks in some other way. We force the missing-host code path
    here by deferring the dispatch's Host lookup to return no row.
    """
    from app.models.sync_job import SyncJob
    from app.tasks import host_lock

    ssh = await create_ssh_key(db)
    host = await create_host(db, ssh_key_id=ssh.id, ip="10.0.0.41")
    sync_id = await _create_pending_sync_job(db, host.id, age_seconds=5)

    # Patch the inner Host existence check to return None. We do this
    # by monkey-patching the local function's behaviour through
    # SQLAlchemy by intercepting the Host SELECT.
    orig_execute = db.execute

    async def _intercepting_execute(stmt, *args, **kwargs):
        sql = str(stmt)
        if "FROM hosts" in sql and "hosts.id" in sql:

            class _R:
                def scalar_one_or_none(self):
                    return None

            return _R()
        return await orig_execute(stmt, *args, **kwargs)

    sync_delay = MagicMock()
    action_delay = MagicMock()
    with (
        patch.object(db, "execute", side_effect=_intercepting_execute),
        patch("app.tasks.host_sync_orchestrator.run_host_sync.delay", new=sync_delay),
        patch("app.tasks.action_orchestrator.run_action.delay", new=action_delay),
    ):
        result = await host_lock.dispatch_next_pending_for_host(db, host.id)

    assert result is None
    sync_delay.assert_not_called()
    action_delay.assert_not_called()

    # The bad SyncJob row is now failed.
    job = (await db.execute(select(SyncJob).where(SyncJob.id == sync_id))).scalar_one()
    assert job.status == "failed"
    assert "host no longer exists" in (job.error_message or "")


# Keep `_uuid` referenced for ruff parity with sibling tests.
_ = _uuid
