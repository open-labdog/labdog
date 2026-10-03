"""BUG-104: cancelling a run stops what it has not started, and the run ends.

The endpoint used to set the run ``cancelled`` (only from ``queued`` or
``running``) and the Redis token, and leave the hosts to the orchestrator,
which cancels the rest at its next check. With no orchestrator left,
nothing did. On lin-manager, run 240 (cancelled after its orchestrator
died) kept two hosts ``queued``, run 237 (whose orchestrator had dispatched
everything and was only waiting on deferred hosts) kept six ``pending``,
and neither got a ``finished_at``. A ``pending`` run was not marked
cancelled at all, only given a token that expires after an hour.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy import select

from app.models.action_run import ActionHostRun, ActionRun
from app.models.sync_job import JobStatus, SyncJob
from tests.conftest import create_host, create_ssh_key

pytestmark = pytest.mark.integration


def _session_yielding(db):
    @asynccontextmanager
    async def _fake():
        yield db

    return _fake


async def _run(db, *, status="running", action_key="linux-upgrade", host_id=None) -> int:
    run = ActionRun(
        action_key=action_key,
        action_version="1.0",
        host_id=host_id,
        parameters={},
        parallelism=1,
        status=status,
    )
    db.add(run)
    await db.flush()
    return run.id


async def _rows(db, run_id, *statuses, host_id=None) -> list[int]:
    """One per-host row per status, each on its own host unless one is given."""
    rows = []
    for status in statuses:
        hid = host_id if host_id is not None else (await create_host(db)).id
        hr = ActionHostRun(action_run_id=run_id, host_id=hid, status=status)
        db.add(hr)
        rows.append(hr)
    await db.flush()
    ids = [hr.id for hr in rows]
    await db.commit()
    return ids


async def _cancel(client, run_id):
    with patch("redis.from_url", return_value=MagicMock()):
        resp = await client.post(f"/api/actions/runs/{run_id}/cancel")
    assert resp.status_code == 200, resp.text


async def _fresh(db, model, row_id):
    """The row as the database has it now. Expires the session, so tests
    keep ids rather than objects across calls."""
    db.expire_all()
    return (await db.execute(select(model).where(model.id == row_id))).scalar_one()


async def _set_status(db, model, row_id, status):
    (await _fresh(db, model, row_id)).status = status
    await db.commit()


class TestHostsNotStartedAreCancelled:
    async def test_queued_hosts_of_a_run_whose_orchestrator_died(self, superuser_client, db):
        """Run 240."""
        run_id = await _run(db)
        done, *queued = await _rows(db, run_id, "succeeded", "queued", "queued")

        await _cancel(superuser_client, run_id)

        run = await _fresh(db, ActionRun, run_id)
        assert run.status == "cancelled"
        assert run.finished_at is not None
        for hr_id in queued:
            hr = await _fresh(db, ActionHostRun, hr_id)
            assert hr.status == "cancelled"
            assert hr.finished_at is not None
        assert (await _fresh(db, ActionHostRun, done)).status == "succeeded"

    async def test_deferred_hosts_of_a_run_whose_orchestrator_returned(self, superuser_client, db):
        """Run 237."""
        run_id = await _run(db)
        rows = await _rows(db, run_id, "succeeded", "pending", "pending")

        await _cancel(superuser_client, run_id)

        assert (await _fresh(db, ActionRun, run_id)).finished_at is not None
        assert [(await _fresh(db, ActionHostRun, hr_id)).status for hr_id in rows] == [
            "succeeded",
            "cancelled",
            "cancelled",
        ]

    async def test_a_pending_run_is_cancelled(self, superuser_client, db):
        """Left ``pending``, the host queue would start it once the token
        expired."""
        ssh = await create_ssh_key(db)
        host = await create_host(db, ssh_key_id=ssh.id)
        run_id = await _run(db, status="pending", host_id=host.id)
        (hr_id,) = await _rows(db, run_id, "pending", host_id=host.id)

        await _cancel(superuser_client, run_id)

        run = await _fresh(db, ActionRun, run_id)
        assert run.status == "cancelled"
        assert run.finished_at is not None
        assert (await _fresh(db, ActionHostRun, hr_id)).status == "cancelled"

    async def test_a_finished_run_is_left_alone(self, superuser_client, db):
        run_id = await _run(db, status="succeeded")
        await db.commit()

        await _cancel(superuser_client, run_id)

        run = await _fresh(db, ActionRun, run_id)
        assert run.status == "succeeded"
        assert run.finished_at is None


class TestARunningHostFinishes:
    async def test_it_is_left_running_and_the_run_ends_when_it_does(self, superuser_client, db):
        from app.tasks.action_orchestrator import finalise_run_if_complete

        run_id = await _run(db)
        running, queued = await _rows(db, run_id, "running", "queued")

        await _cancel(superuser_client, run_id)

        run = await _fresh(db, ActionRun, run_id)
        assert run.status == "cancelled"
        assert run.finished_at is None, "the run ended while a host was still running"
        assert (await _fresh(db, ActionHostRun, running)).status == "running"
        assert (await _fresh(db, ActionHostRun, queued)).status == "cancelled"

        await _set_status(db, ActionHostRun, running, "succeeded")
        with patch("app.db.task_session", _session_yielding(db)):
            assert await finalise_run_if_complete(run_id) == "cancelled"

        run = await _fresh(db, ActionRun, run_id)
        assert run.status == "cancelled"
        assert run.finished_at is not None

    async def test_a_builtin_host_closes_the_run_itself(self, superuser_client, db):
        """Built-ins did not finalise at all; only the orchestrator did."""
        from app.tasks.builtin_dispatchers import _finish_host_run

        run_id = await _run(db, action_key="_builtin.drift_check")
        (running,) = await _rows(db, run_id, "running")
        await _cancel(superuser_client, run_id)

        with patch("app.db.task_session", _session_yielding(db)):
            await _finish_host_run(running, succeeded=True, dispatch_next=False)

        run = await _fresh(db, ActionRun, run_id)
        assert run.status == "cancelled"
        assert run.finished_at is not None

    async def test_a_group_task_cancelled_mid_playbook_ends_the_run(self, superuser_client, db):
        from app.tasks.action_group import _aggregate_and_finalise

        run_id = await _run(db, action_key="k8s-upgrade")
        rows = await _rows(db, run_id, "running", "running")
        await _cancel(superuser_client, run_id)
        assert (await _fresh(db, ActionRun, run_id)).finished_at is None

        for hr_id in rows:
            await _set_status(db, ActionHostRun, hr_id, "succeeded")
        with patch("app.db.task_session", _session_yielding(db)):
            await _aggregate_and_finalise(run_id, f"actions.run.{run_id}", MagicMock())

        run = await _fresh(db, ActionRun, run_id)
        assert run.status == "cancelled"
        assert run.finished_at is not None


class TestABuiltinSyncRow:
    """A deferred ``_builtin.sync`` row's work is the SyncJob it queued."""

    async def _deferred_sync(self, db, job_status) -> tuple[int, int, int]:
        ssh = await create_ssh_key(db)
        host = await create_host(db, ssh_key_id=ssh.id)
        run_id = await _run(db, status="pending", action_key="_builtin.sync", host_id=host.id)
        (hr_id,) = await _rows(db, run_id, "pending", host_id=host.id)
        job = SyncJob(host_id=host.id, status=job_status, origin_action_host_run_id=hr_id)
        db.add(job)
        await db.flush()
        job_id = job.id
        await db.commit()
        return run_id, hr_id, job_id

    async def test_its_queued_job_is_cancelled_with_it(self, superuser_client, db):
        run_id, hr_id, job_id = await self._deferred_sync(db, JobStatus.pending)

        await _cancel(superuser_client, run_id)

        assert (await _fresh(db, ActionHostRun, hr_id)).status == "cancelled"
        job = await _fresh(db, SyncJob, job_id)
        assert job.status == JobStatus.cancelled, "the sync would have run after the cancel"
        assert (await _fresh(db, ActionRun, run_id)).finished_at is not None

    async def test_a_row_whose_job_is_running_counts_as_running(self, superuser_client, db):
        run_id, hr_id, job_id = await self._deferred_sync(db, JobStatus.running)

        await _cancel(superuser_client, run_id)

        assert (await _fresh(db, ActionHostRun, hr_id)).status == "pending", (
            "the job closes the row when it finishes"
        )
        assert (await _fresh(db, SyncJob, job_id)).status == JobStatus.running
        run = await _fresh(db, ActionRun, run_id)
        assert run.status == "cancelled"
        assert run.finished_at is None
