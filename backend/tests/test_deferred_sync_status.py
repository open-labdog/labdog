"""BUG-81: a deferred sync must not report that it succeeded.

``_builtin.sync`` creates a ``SyncJob`` and drives it inline. When the
host turns out to be busy the inner sync defers — the job stays
``pending`` and the host queue re-dispatches it once the in-flight op
finishes — and the dispatcher used to fall through with ``succeeded``
unchanged, on the reasoning that a defer is not a failure.

True, but it is not success either. The run history said the sync had
happened at a time it had not, and an operator reading the run list had
no way to tell a real sync from a deferred one.

The row stays ``pending`` instead, with the same ``pending_reason`` any
other deferred op carries, and the parent run stays open —
``finalise_run_if_complete`` already treats ``pending`` as active. The
job carries ``origin_action_host_run_id`` so whoever eventually runs it
knows whose row to close.
"""

from contextlib import asynccontextmanager
from unittest.mock import patch

import pytest

from app.models.action_run import ActionHostRun, ActionRun
from app.models.sync_job import JobStatus, SyncJob
from tests.conftest import create_host, create_ssh_key

pytestmark = pytest.mark.integration


def _session_yielding(db):
    @asynccontextmanager
    async def _fake():
        yield db

    return _fake


async def _sync_run(db, host_id, *, status="running"):
    run = ActionRun(
        action_key="_builtin.sync",
        action_version="1.0",
        host_id=host_id,
        parameters={},
        parallelism=1,
        status=status,
        target_kind="host",
        target_label="node-1",
    )
    db.add(run)
    await db.flush()
    hr = ActionHostRun(action_run_id=run.id, host_id=host_id, hostname="node-1", status="running")
    db.add(hr)
    await db.flush()
    await db.commit()
    return run, hr


class TestADeferredSyncStaysPending:
    async def test_the_row_is_not_marked_succeeded(self, db):
        from app.tasks.builtin_dispatchers import _defer_for_queued_sync

        ssh = await create_ssh_key(db)
        host = await create_host(db, ssh_key_id=ssh.id)
        run, hr = await _sync_run(db, host.id)

        with patch("app.db.task_session", _session_yielding(db)):
            await _defer_for_queued_sync(hr.id, host.id, job_id=1)

        await db.refresh(hr)
        await db.refresh(run)
        assert hr.status == "pending", "a queued sync was reported as a completed one"
        assert hr.pending_reason
        assert hr.finished_at is None
        assert run.status == "pending"
        assert run.pending_reason == hr.pending_reason

    async def test_the_parent_is_not_finalised_while_the_row_is_pending(self, db):
        """The guarantee the whole approach rests on: ``pending`` counts as
        active, so the run stays open until the queued job actually runs."""
        from app.tasks.action_orchestrator import finalise_run_if_complete
        from app.tasks.builtin_dispatchers import _defer_for_queued_sync

        ssh = await create_ssh_key(db)
        host = await create_host(db, ssh_key_id=ssh.id)
        run, hr = await _sync_run(db, host.id)

        with patch("app.db.task_session", _session_yielding(db)):
            await _defer_for_queued_sync(hr.id, host.id, job_id=1)
            assert await finalise_run_if_complete(run.id) is None

        await db.refresh(run)
        assert run.status == "pending"
        assert run.finished_at is None


class TestTheQueuedJobClosesTheRow:
    async def _job_for(self, db, host_id, host_run_id, status=JobStatus.pending):
        job = SyncJob(
            host_id=host_id,
            status=status,
            module_type="firewall",
            origin_action_host_run_id=host_run_id,
        )
        db.add(job)
        await db.flush()
        await db.commit()
        return job

    async def test_a_successful_run_closes_the_row_and_the_parent(self, db):
        from app.tasks.builtin_dispatchers import close_origin_host_run

        ssh = await create_ssh_key(db)
        host = await create_host(db, ssh_key_id=ssh.id)
        run, hr = await _sync_run(db, host.id)
        hr.status = "pending"
        await db.flush()
        job = await self._job_for(db, host.id, hr.id)

        with patch("app.db.task_session", _session_yielding(db)):
            await close_origin_host_run(job.id, {"status": "success"})

        await db.refresh(hr)
        await db.refresh(run)
        assert hr.status == "succeeded"
        assert hr.finished_at is not None
        assert run.status == "succeeded"

    async def test_a_failed_run_closes_the_row_as_failed(self, db):
        from app.tasks.builtin_dispatchers import close_origin_host_run

        ssh = await create_ssh_key(db)
        host = await create_host(db, ssh_key_id=ssh.id)
        run, hr = await _sync_run(db, host.id)
        hr.status = "pending"
        await db.flush()
        job = await self._job_for(db, host.id, hr.id)

        with patch("app.db.task_session", _session_yielding(db)):
            await close_origin_host_run(job.id, {"status": "failed"})

        await db.refresh(hr)
        await db.refresh(run)
        assert hr.status == "failed"
        assert run.status == "failed"

    async def test_deferring_again_leaves_the_row_alone(self, db):
        """Re-dispatched and deferred again: still queued, still pending."""
        from app.tasks.builtin_dispatchers import close_origin_host_run

        ssh = await create_ssh_key(db)
        host = await create_host(db, ssh_key_id=ssh.id)
        _run, hr = await _sync_run(db, host.id)
        hr.status = "pending"
        await db.flush()
        job = await self._job_for(db, host.id, hr.id)

        with patch("app.db.task_session", _session_yielding(db)):
            await close_origin_host_run(job.id, {"status": "deferred"})

        await db.refresh(hr)
        assert hr.status == "pending"

    async def test_a_sync_with_no_origin_closes_nothing(self, db):
        """Syncs started from the API or the scheduler have no run behind
        them; this must be a no-op for them, not a crash."""
        from app.tasks.builtin_dispatchers import close_origin_host_run

        ssh = await create_ssh_key(db)
        host = await create_host(db, ssh_key_id=ssh.id)
        job = SyncJob(host_id=host.id, status=JobStatus.success, module_type="firewall")
        db.add(job)
        await db.flush()
        await db.commit()

        with patch("app.db.task_session", _session_yielding(db)):
            await close_origin_host_run(job.id, {"status": "success"})

    async def test_it_does_not_reopen_a_row_that_already_finished(self, db):
        """The inline path closes its own row. If the job is re-dispatched
        for any other reason, this must not overwrite that outcome."""
        from app.tasks.builtin_dispatchers import close_origin_host_run

        ssh = await create_ssh_key(db)
        host = await create_host(db, ssh_key_id=ssh.id)
        _run, hr = await _sync_run(db, host.id)
        hr.status = "failed"
        hr.error_message = "the real outcome"
        await db.flush()
        job = await self._job_for(db, host.id, hr.id)

        with patch("app.db.task_session", _session_yielding(db)):
            await close_origin_host_run(job.id, {"status": "success"})

        await db.refresh(hr)
        assert hr.status == "failed"
        assert hr.error_message == "the real outcome"


class TestTheJobRecordsWhoAskedForIt:
    async def test_the_column_round_trips(self, db):
        ssh = await create_ssh_key(db)
        host = await create_host(db, ssh_key_id=ssh.id)
        _run, hr = await _sync_run(db, host.id)
        job = SyncJob(
            host_id=host.id,
            status=JobStatus.pending,
            module_type="firewall",
            origin_action_host_run_id=hr.id,
        )
        db.add(job)
        await db.flush()
        job_id, hr_id = job.id, hr.id
        await db.commit()

        db.expire_all()
        stored = await db.get(SyncJob, job_id)
        assert stored.origin_action_host_run_id == hr_id

    async def test_deleting_the_run_nulls_the_link_and_keeps_the_job(self, db):
        """Run retention deletes action_host_runs on a schedule (BUG-73);
        it must not take sync history with it."""
        ssh = await create_ssh_key(db)
        host = await create_host(db, ssh_key_id=ssh.id)
        run, hr = await _sync_run(db, host.id)
        job = SyncJob(
            host_id=host.id,
            status=JobStatus.success,
            module_type="firewall",
            origin_action_host_run_id=hr.id,
        )
        db.add(job)
        await db.flush()
        job_id = job.id
        await db.commit()

        await db.delete(run)  # cascades to the host run
        await db.flush()

        db.expire_all()
        stored = await db.get(SyncJob, job_id)
        assert stored is not None, "the sync job went with the run history"
        assert stored.origin_action_host_run_id is None
