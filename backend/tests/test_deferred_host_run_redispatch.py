"""BUG-102 and BUG-103: how the host queue resumes a deferred per-host row.

BUG-102. A host-targeted run defers per row: the orchestrator has already
created the host's ``ActionHostRun``, and the claim flips that row and the
run to ``pending`` together. When the host freed up, both were candidates
with the same ``created_at``; the run won the tie and was re-sent through
the orchestrator, which inserted the host's row a second time. The run
failed on ``uq_action_host_run`` and the real row stayed ``pending`` for
good.

BUG-103. The row branch always sent ``run_action_host``, the pack-playbook
runner, so a deferred member of a built-in run (``_builtin.drift_check`` on
a group) was handed to a runner with no playbook to run. A deferred
``_builtin.sync`` row was picked up by the same branch although the SyncJob
it queued is what closes it.

These run the real claim functions against Postgres; only the Celery sends
are patched.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import select

from app.models.action_run import ActionHostRun, ActionRun
from app.models.sync_job import SyncJob
from tests.conftest import create_group, create_host, create_ssh_key

pytestmark = pytest.mark.integration


def _session_yielding(db):
    """Make ``task_session()`` hand back the test's own session."""

    @asynccontextmanager
    async def _fake():
        yield db

    return _fake


async def _run(db, *, action_key, host_id=None, group_id=None, status="running"):
    run = ActionRun(
        action_key=action_key,
        action_version="1.0",
        host_id=host_id,
        group_id=group_id,
        parameters={},
        parallelism=1,
        status=status,
    )
    db.add(run)
    await db.flush()
    return run


async def _row(db, run, host_id, *, status="queued"):
    hr = ActionHostRun(action_run_id=run.id, host_id=host_id, status=status)
    db.add(hr)
    await db.flush()
    return hr


async def _status(db, model, row_id) -> str:
    return (await db.execute(select(model.status).where(model.id == row_id))).scalar_one()


@pytest.fixture
def registry_current():
    """The row branch brings the registry up to date before sizing limits;
    the registry is not what these tests are about."""
    with patch("app.actions.registry.ensure_registry_current", new=AsyncMock()) as m:
        yield m


class TestAHostTargetedRunResumesThroughItsRow:
    """BUG-102, end to end: defer behind a sync, free the host, resume."""

    async def test_a_pack_action(self, db, registry_current):
        from app.tasks import action_host
        from app.tasks.host_lock import check_host_busy, dispatch_next_pending_for_host

        ssh = await create_ssh_key(db)
        host = await create_host(db, ssh_key_id=ssh.id)
        sync = SyncJob(host_id=host.id, status="running", module_type="firewall")
        db.add(sync)
        # The state the orchestrator leaves a host-targeted run in.
        run = await _run(db, action_key="linux-upgrade", host_id=host.id)
        hr = await _row(db, run, host.id)
        await db.commit()

        ctx = action_host._RunCtx(
            action_run_id=run.id,
            host_run_id=hr.id,
            channel=f"actions.run.{run.id}",
            r=None,
            private_data_dir="/tmp/none",
        )
        with patch("app.db.task_session", _session_yielding(db)):
            assert await action_host._claim_or_defer(ctx) is False
        assert await _status(db, ActionRun, run.id) == "pending"
        assert await _status(db, ActionHostRun, hr.id) == "pending"

        sync.status = "success"
        await db.commit()

        with (
            patch("app.tasks.action_orchestrator.run_action.delay") as run_action,
            patch("app.tasks.action_orchestrator.send_host_task") as send,
        ):
            result = await dispatch_next_pending_for_host(db, host.id, exclude_sync_job_id=sync.id)

        assert result == ("action_host_run", hr.id), (
            "the run was re-sent whole; the orchestrator would insert its row a second time"
        )
        run_action.assert_not_called()
        send.assert_called_once_with("linux-upgrade", run.id, hr.id)

        # The re-sent task claims the host, and the run holds it again.
        with patch("app.db.task_session", _session_yielding(db)):
            assert await action_host._claim_or_defer(ctx) is True
        await db.refresh(run)
        assert run.status == "running"
        assert run.pending_reason is None
        assert await _status(db, ActionHostRun, hr.id) == "running"

        blocker = await check_host_busy(db, host.id)
        assert blocker is not None and blocker.id == run.id, (
            "a run left pending while its row runs is invisible to the busy check, "
            "so a sync could claim the host alongside it"
        )

    async def test_a_builtin(self, db, registry_current):
        from app.tasks.builtin_dispatchers import _begin_host_run
        from app.tasks.host_lock import dispatch_next_pending_for_host

        ssh = await create_ssh_key(db)
        host = await create_host(db, ssh_key_id=ssh.id)
        sync = SyncJob(host_id=host.id, status="running", module_type="firewall")
        db.add(sync)
        run = await _run(db, action_key="_builtin.drift_check", host_id=host.id)
        hr = await _row(db, run, host.id)
        await db.commit()

        with patch("app.db.task_session", _session_yielding(db)):
            assert await _begin_host_run(hr.id) is None
        assert await _status(db, ActionRun, run.id) == "pending"

        sync.status = "success"
        await db.commit()

        with (
            patch("app.tasks.action_orchestrator.run_action.delay") as run_action,
            patch("app.tasks.action_orchestrator.send_host_task") as send,
        ):
            result = await dispatch_next_pending_for_host(db, host.id, exclude_sync_job_id=sync.id)

        assert result == ("action_host_run", hr.id)
        run_action.assert_not_called()
        send.assert_called_once_with("_builtin.drift_check", run.id, hr.id)

        with patch("app.db.task_session", _session_yielding(db)):
            assert await _begin_host_run(hr.id) == host.id
        await db.refresh(run)
        assert run.status == "running"
        assert run.pending_reason is None

    async def test_a_run_that_deferred_before_it_had_rows_is_still_re_sent_whole(self, db):
        """A group dispatch defers as a whole, before its rows exist."""
        from app.tasks.host_lock import dispatch_next_pending_for_host

        group = await create_group(db)
        ssh = await create_ssh_key(db)
        host = await create_host(db, ssh_key_id=ssh.id, group_ids=[group.id])
        run = await _run(db, action_key="k8s-upgrade", group_id=group.id, status="pending")
        await db.commit()

        with patch("app.tasks.action_orchestrator.run_action.delay") as run_action:
            result = await dispatch_next_pending_for_host(db, host.id)

        assert result == ("action_group", run.id)
        run_action.assert_called_once_with(run.id)


class TestADeferredRowRunsItsOwnTask:
    """BUG-103: the row goes to the task the orchestrator would have used."""

    async def _dispatch_member(self, db, action_key):
        from app.tasks.host_lock import dispatch_next_pending_for_host

        group = await create_group(db)
        ssh = await create_ssh_key(db)
        host = await create_host(db, ssh_key_id=ssh.id, group_ids=[group.id])
        run = await _run(db, action_key=action_key, group_id=group.id)
        hr = await _row(db, run, host.id, status="pending")
        await db.commit()

        with patch("app.tasks.action_orchestrator.celery_app.send_task") as send_task:
            result = await dispatch_next_pending_for_host(db, host.id)

        assert result == ("action_host_run", hr.id)
        send_task.assert_called_once()
        return run, hr, send_task.call_args

    async def test_a_builtin_member_goes_to_its_builtin_task(self, db, registry_current):
        from app.tasks.action_orchestrator import CHILD_QUEUE
        from app.tasks.action_timeouts import (
            HARD_LIMIT_MARGIN_SECONDS,
            per_host_deadline_seconds,
        )

        run, hr, call = await self._dispatch_member(db, "_builtin.drift_check")

        assert call.args == ("app.tasks.builtin_dispatchers.run_builtin_drift_check",)
        assert call.kwargs["args"] == [run.id, hr.id]
        assert call.kwargs["queue"] == CHILD_QUEUE
        soft = per_host_deadline_seconds("_builtin.drift_check")
        assert call.kwargs["soft_time_limit"] == soft
        assert call.kwargs["time_limit"] == soft + HARD_LIMIT_MARGIN_SECONDS

    async def test_a_pack_action_member_still_goes_to_the_playbook_runner(
        self, db, registry_current
    ):
        _, _, call = await self._dispatch_member(db, "linux-upgrade")

        assert call.args == ("app.tasks.action_host.run_action_host",)

    async def test_the_registry_is_brought_up_to_date_before_sizing_the_limits(
        self, db, registry_current
    ):
        await self._dispatch_member(db, "linux-upgrade")

        registry_current.assert_awaited_once()


class TestABuiltinSyncRowIsLeftToItsSyncJob:
    """BUG-103, the ``_builtin.sync`` half.

    A deferred built-in sync leaves two pending things on the host: its row,
    and the SyncJob it queued, which closes the row when it runs (BUG-81).
    The row is older, so it used to win, and re-sending it would start a
    second sync for the same request.
    """

    async def test_the_job_is_dispatched_and_the_row_is_left_alone(self, db, registry_current):
        from app.tasks.host_lock import dispatch_next_pending_for_host

        ssh = await create_ssh_key(db)
        host = await create_host(db, ssh_key_id=ssh.id)
        run = await _run(db, action_key="_builtin.sync", host_id=host.id, status="pending")
        hr = await _row(db, run, host.id, status="pending")
        job = SyncJob(host_id=host.id, status="pending", origin_action_host_run_id=hr.id)
        db.add(job)
        await db.commit()

        with (
            patch("app.tasks.host_sync_orchestrator.run_host_sync.delay") as run_host_sync,
            patch("app.tasks.action_orchestrator.send_host_task") as send,
            patch("app.tasks.action_orchestrator.run_action.delay") as run_action,
        ):
            result = await dispatch_next_pending_for_host(db, host.id)

        assert result == ("sync", job.id)
        run_host_sync.assert_called_once()
        send.assert_not_called()
        run_action.assert_not_called()
        assert await _status(db, ActionHostRun, hr.id) == "pending"

    async def test_a_row_whose_job_has_finished_is_picked_up(self, db, registry_current):
        """Only an open job speaks for the row."""
        from app.tasks.host_lock import dispatch_next_pending_for_host

        ssh = await create_ssh_key(db)
        host = await create_host(db, ssh_key_id=ssh.id)
        run = await _run(db, action_key="_builtin.sync", host_id=host.id, status="pending")
        hr = await _row(db, run, host.id, status="pending")
        db.add(SyncJob(host_id=host.id, status="failed", origin_action_host_run_id=hr.id))
        await db.commit()

        with patch("app.tasks.action_orchestrator.send_host_task") as send:
            result = await dispatch_next_pending_for_host(db, host.id)

        assert result == ("action_host_run", hr.id)
        send.assert_called_once_with("_builtin.sync", run.id, hr.id)


class TestResumeDeferredParent:
    async def test_a_cancelled_run_stays_cancelled(self, db):
        from app.tasks.host_lock import resume_deferred_parent

        ssh = await create_ssh_key(db)
        host = await create_host(db, ssh_key_id=ssh.id)
        run = await _run(db, action_key="linux-upgrade", host_id=host.id, status="cancelled")

        await resume_deferred_parent(db, run.id)

        assert await _status(db, ActionRun, run.id) == "cancelled"

    async def test_a_group_run_is_not_touched(self, db):
        """A group-dispatch run is ``pending`` as a whole; a row claiming
        cannot be what resumes it."""
        from app.tasks.host_lock import resume_deferred_parent

        group = await create_group(db)
        run = await _run(db, action_key="k8s-upgrade", group_id=group.id, status="pending")

        await resume_deferred_parent(db, run.id)

        assert await _status(db, ActionRun, run.id) == "pending"
