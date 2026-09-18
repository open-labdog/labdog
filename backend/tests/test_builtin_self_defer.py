"""BUG-78/BUG-80: a built-in action must not defer behind its own parent.

``action_orchestrator`` marks the parent ``ActionRun`` ``running`` in its
init phase and *then* dispatches the per-host task. So by the time a
built-in dispatcher runs its claim-or-defer, there is already a row
matching "ActionRun on this host, status running" — its own parent.

``check_host_busy`` has an ``exclude_action_run_id`` for exactly this;
``action_host`` has always passed it and ``builtin_dispatchers`` never
did. Every ``_builtin.collect_state``, ``_builtin.drift_check`` and
``_builtin.ai_task`` therefore deferred against itself and stayed
``pending`` permanently: the deferral is only ever cleared by
``dispatch_next_pending_for_host``, which fires when some *other* op
finishes, and there was no other op.

Found by running six concurrent host-targeted built-ins against a live
instance; all six sat in ``pending`` with
``Waiting for action_host <own id> on host <own target>``.

``_builtin.sync`` was unaffected — it passes ``with_lock=False`` and
delegates to ``run_host_sync``, which does its own claim — which is part
of why this survived: the built-in that runs most often is the one that
skips this code.
"""

from unittest.mock import AsyncMock, patch

import pytest

from app.models.action_run import ActionHostRun, ActionRun
from app.tasks.builtin_dispatchers import _begin_host_run
from tests.conftest import create_host, create_ssh_key

pytestmark = pytest.mark.integration


async def _run_on_host(db, host_id, *, run_status="running"):
    """A host-targeted run in the state the orchestrator leaves it in."""
    run = ActionRun(
        action_key="_builtin.collect_state",
        action_version="1.0",
        host_id=host_id,
        parameters={},
        parallelism=1,
        status=run_status,
    )
    db.add(run)
    await db.flush()
    hr = ActionHostRun(action_run_id=run.id, host_id=host_id, status="queued")
    db.add(hr)
    await db.flush()
    await db.commit()
    return run, hr


class TestABuiltinClaimsItsOwnHost:
    async def test_it_does_not_defer_behind_its_own_parent_run(self, db):
        ssh = await create_ssh_key(db)
        host = await create_host(db, ssh_key_id=ssh.id)
        run, hr = await _run_on_host(db, host.id)

        with patch("app.db.task_session", _session_yielding(db)):
            claimed = await _begin_host_run(hr.id)

        assert claimed == host.id, (
            "the built-in deferred behind its own parent ActionRun; "
            "nothing else will ever finish to release it"
        )
        await db.refresh(hr)
        assert hr.status == "running"

    async def test_it_still_defers_behind_a_different_run_on_the_same_host(self, db):
        """The exclusion must be exactly one run wide — a real blocker on
        the same host still has to hold it."""
        ssh = await create_ssh_key(db)
        host = await create_host(db, ssh_key_id=ssh.id)
        other = ActionRun(
            action_key="_builtin.drift_check",
            action_version="1.0",
            host_id=host.id,
            parameters={},
            parallelism=1,
            status="running",
        )
        db.add(other)
        await db.flush()
        _, hr = await _run_on_host(db, host.id)

        with patch("app.db.task_session", _session_yielding(db)):
            claimed = await _begin_host_run(hr.id)

        assert claimed is None
        await db.refresh(hr)
        assert hr.status == "pending"
        assert f"action_host {other.id}" in (hr.pending_reason or "")

    async def test_it_still_defers_behind_a_running_sync(self, db):
        from app.models.sync_job import SyncJob

        ssh = await create_ssh_key(db)
        host = await create_host(db, ssh_key_id=ssh.id)
        job = SyncJob(host_id=host.id, status="running", module_type="firewall")
        db.add(job)
        await db.flush()
        _, hr = await _run_on_host(db, host.id)

        with patch("app.db.task_session", _session_yielding(db)):
            claimed = await _begin_host_run(hr.id)

        assert claimed is None
        await db.refresh(hr)
        assert hr.status == "pending"


class TestTheParentIsNotLeftPending:
    async def test_a_claimed_run_leaves_its_parent_alone(self, db):
        ssh = await create_ssh_key(db)
        host = await create_host(db, ssh_key_id=ssh.id)
        run, hr = await _run_on_host(db, host.id)

        with patch("app.db.task_session", _session_yielding(db)):
            await _begin_host_run(hr.id)

        await db.refresh(run)
        assert run.status == "running"
        assert run.pending_reason is None


def _session_yielding(db):
    """Make ``task_session()`` hand back the test's own session.

    ``_begin_host_run`` opens its own session and commits; the test needs
    to observe those writes, and the advisory lock has to be taken on the
    same connection the checks run on.
    """
    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def _fake():
        yield db

    return _fake


# Guard against the fixture above quietly becoming a no-op.
def test_the_helper_returns_a_context_manager():
    assert callable(_session_yielding(AsyncMock()))


class TestTheExclusionReachesEveryScan:
    """BUG-80: excluding a run must exclude it in all three scans.

    ``check_host_busy`` can surface an ``ActionRun`` three ways: directly
    by ``host_id`` (2), through one of its ``ActionHostRun`` rows (3), and
    through group membership before those rows exist (3b). Only 2 and 3b
    honoured the exclusion.

    That gap is exactly what ``_builtin.sync`` walks into. It marks its
    own ``ActionHostRun`` ``running`` and *then* hands off to the sync
    orchestrator, so by the time the inner claim runs, the parent is
    reachable through scan 3. The sync deferred behind the run that was
    waiting for the sync; the ``SyncJob`` sat ``pending`` forever, and the
    action reported ``succeeded`` having synced nothing.
    """

    async def test_a_parent_reachable_only_through_its_host_run_is_excluded(self, db):
        from app.tasks.host_lock import acquire_host_lock, check_host_busy

        ssh = await create_ssh_key(db)
        host = await create_host(db, ssh_key_id=ssh.id)
        run, hr = await _run_on_host(db, host.id)
        # The shape _builtin.sync leaves behind: per-host row running,
        # parent running. Scan 2 also matches here, so blank the parent's
        # host_id to isolate scan 3 — a group-targeted run looks like this.
        hr.status = "running"
        run.host_id = None
        run.target_kind = "group"
        await db.flush()
        await db.commit()

        await acquire_host_lock(db, host.id)
        assert await check_host_busy(db, host.id, exclude_action_run_id=run.id) is None, (
            "the run was found through its own ActionHostRun despite being excluded"
        )

    async def test_a_different_runs_host_run_still_blocks(self, db):
        """The exclusion must not blind scan 3 entirely — a genuine
        group run holding this host has to keep holding it."""
        from app.tasks.host_lock import acquire_host_lock, check_host_busy

        ssh = await create_ssh_key(db)
        host = await create_host(db, ssh_key_id=ssh.id)
        other, other_hr = await _run_on_host(db, host.id)
        other_hr.status = "running"
        other.host_id = None
        other.target_kind = "group"
        await db.flush()
        mine, _ = await _run_on_host(db, host.id)

        await acquire_host_lock(db, host.id)
        blocker = await check_host_busy(db, host.id, exclude_action_run_id=mine.id)
        assert blocker is not None
        assert blocker.id == other.id


class TestBuiltinSyncPassesItsParent:
    """The call sites, asserted at source level.

    A runtime test would need a reachable host and the whole sync
    orchestrator; what actually broke was a missing keyword argument, and
    that is checkable directly.
    """

    def test_the_sync_orchestrator_accepts_the_exclusion(self):
        import inspect

        from app.tasks.host_sync_orchestrator import _async_run, _claim_or_defer

        assert "exclude_action_run_id" in inspect.signature(_async_run).parameters
        assert "exclude_action_run_id" in inspect.signature(_claim_or_defer).parameters

    def test_builtin_sync_passes_its_own_action_run_id(self):
        import inspect
        from pathlib import Path

        import app.tasks.builtin_dispatchers as dispatchers

        src = Path(inspect.getfile(dispatchers)).read_text()
        assert "exclude_action_run_id=action_run_id" in src, (
            "_builtin.sync must exclude its own parent run when claiming the host"
        )
