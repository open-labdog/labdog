"""BUG-116, BUG-117, BUG-119: work queued on a host runs what it was queued
for, as soon as the host is free.

On valheim, alloy-install's ``post_run_register`` queued ``packages`` and
``services`` follow-ups at 18:59:31, while a CA certificate deploy (sync
35) was running there. They last deferred on "Waiting for sync 35", and
the deploy never handed the host on, so nothing re-dispatched them until
linux-upgrade released the host at 03:08 (BUG-119). An action's own
release had the same hole: it handed the host on *before* closing its
run, so a waiting op could find the run still ``running`` and defer again
(BUG-117). When the follow-ups did run, the queue rebuilt their module
list from ``module_type="packages"``, a name it did not know, and ran
every module — firewall and ``/etc/hosts`` included (BUG-116).
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy import select

from app.models.action_run import ActionHostRun, ActionRun
from app.models.host_module_status import HostModuleStatus
from app.models.sync_job import JobStatus, SyncJob
from app.tasks.host_lock import check_host_busy, dispatch_next_pending_for_host
from app.tasks.host_sync_orchestrator import (
    UnknownModuleType,
    _claim_or_defer,
    _filter_from_module_type,
)
from tests.conftest import create_host, create_ssh_key

pytestmark = pytest.mark.integration


@pytest.fixture(autouse=True)
def patch_task_session(db):
    @asynccontextmanager
    async def _fake():
        yield db

    with (
        patch("app.db.task_session", new=_fake),
        patch("app.tasks.sync_sweeper.task_session", new=_fake),
        patch("app.tasks.host_sync_orchestrator.task_session", new=_fake),
    ):
        yield


@pytest.fixture
def fake_redis():
    redis = MagicMock()
    redis.exists.return_value = 0
    with patch("redis.from_url", return_value=redis):
        yield redis


async def _host(db):
    key = await create_ssh_key(db)
    return await create_host(db, ssh_key_id=key.id)


async def _sync(db, host_id, *, module_type, module_filter=None, status="pending", age=30):
    job = SyncJob(
        host_id=host_id,
        module_type=module_type,
        module_filter=module_filter,
        status=status,
    )
    db.add(job)
    await db.flush()
    job.created_at = datetime.now(UTC) - timedelta(seconds=age)
    await db.flush()
    return job.id


class TestTheQueueRunsOnlyWhatItCanRead:
    def test_an_unknown_module_type_is_not_every_module(self):
        with pytest.raises(UnknownModuleType):
            _filter_from_module_type("packages")

    async def test_a_ca_cert_row_goes_to_its_own_task(self, db):
        """A CA certificate deploy is a ``sync_jobs`` row run by its own
        task. Waiting in the queue, it went out as a sync of every module."""
        host = await _host(db)
        ca = await _sync(db, host.id, module_type="ca_cert", age=60)

        sync_delay, ca_delay = MagicMock(), MagicMock()
        with (
            patch("app.tasks.host_sync_orchestrator.run_host_sync.delay", new=sync_delay),
            patch("app.tasks.ca_cert_action.run_ca_cert_action.delay", new=ca_delay),
        ):
            assert await dispatch_next_pending_for_host(db, host.id) == ("sync", ca)

        sync_delay.assert_not_called()
        ca_delay.assert_called_once_with(job_id=ca, host_id=host.id)

    async def test_a_row_of_no_known_kind_is_left_alone(self, db):
        host = await _host(db)
        await _sync(db, host.id, module_type="not-a-module", age=60)

        delay = MagicMock()
        with patch("app.tasks.host_sync_orchestrator.run_host_sync.delay", new=delay):
            assert await dispatch_next_pending_for_host(db, host.id) is None
        delay.assert_not_called()

    async def test_a_deferred_post_run_sync_runs_its_own_module(self, db):
        from app.sync.post_run import queue_post_run_sync

        host = await _host(db)
        (job_id,) = await queue_post_run_sync(
            db, host_id=host.id, modules=["packages"], triggered_by_user_id=None
        )

        delay = MagicMock()
        with patch("app.tasks.host_sync_orchestrator.run_host_sync.delay", new=delay):
            assert await dispatch_next_pending_for_host(db, host.id) == ("sync", job_id)

        assert delay.call_args.kwargs["module_filter"] == ["packages"]

    async def test_the_sweeper_fails_a_stuck_ca_cert_row_without_marking_modules(self, db):
        from app.tasks.sync_sweeper import _sweep_stale_syncs_async

        host = await _host(db)
        job = SyncJob(
            host_id=host.id,
            module_type="ca_cert",
            status=JobStatus.running,
            started_at=datetime.now(UTC) - timedelta(hours=2),
        )
        db.add(job)
        await db.flush()

        with patch("app.tasks.host_sync_orchestrator.run_host_sync.delay", new=MagicMock()):
            result = await _sweep_stale_syncs_async()

        assert job.id in result["swept"]
        assert (await db.get(SyncJob, job.id)).status == JobStatus.failed
        statuses = await db.scalar(
            select(HostModuleStatus.id).where(HostModuleStatus.host_id == host.id).limit(1)
        )
        assert statuses is None, "a CA deploy touched no module, so none is marked error"


class TestACaCertDeployTakesItsTurn:
    """BUG-119: valheim's follow-up syncs last deferred on "Waiting for sync
    35 on host valheim" — a CA certificate deploy, which ran without
    asking for the host and never handed it on."""

    async def test_it_waits_while_the_host_is_busy(self, db):
        from app.tasks.ca_cert_action import _claim_or_defer as claim_ca

        host = await _host(db)
        await _sync(db, host.id, module_type="firewall", status=JobStatus.running)
        ca = await _sync(db, host.id, module_type="ca_cert")

        assert await claim_ca(ca, host.id) is False

        job = await db.get(SyncJob, ca)
        assert job.status == JobStatus.pending
        assert job.pending_reason

    async def test_it_holds_the_host_while_it_runs(self, db):
        from app.tasks.ca_cert_action import _claim_or_defer as claim_ca

        host = await _host(db)
        ca = await _sync(db, host.id, module_type="ca_cert")

        assert await claim_ca(ca, host.id) is True

        assert (await db.get(SyncJob, ca)).status == JobStatus.running
        assert await check_host_busy(db, host.id) is not None

    async def test_a_second_dispatch_does_nothing(self, db):
        from app.tasks.ca_cert_action import _claim_or_defer as claim_ca

        host = await _host(db)
        ca = await _sync(db, host.id, module_type="ca_cert", status=JobStatus.success)

        assert await claim_ca(ca, host.id) is False
        assert (await db.get(SyncJob, ca)).status == JobStatus.success


class TestAJobRunsOnce:
    @pytest.mark.parametrize("status", [JobStatus.running, JobStatus.success])
    async def test_a_second_dispatch_of_a_taken_job_does_nothing(self, db, status):
        host = await _host(db)
        job_id = await _sync(db, host.id, module_type="firewall", status=status)

        assert await _claim_or_defer(db, job_id, host.id) is False

        job = await db.get(SyncJob, job_id)
        assert job.status == status
        assert job.pending_reason is None


class TestTheParentRunClosesBeforeTheHostIsHandedOn:
    """BUG-117. Asserted at the moment of the release: by then the run must
    be closed and the host free, or the op the release starts defers again
    and nothing wakes it."""

    @staticmethod
    def _spy_release(db, run_id, host_id, seen):
        from app.tasks import host_lock

        real = host_lock.release_host_queue

        async def spy(host_ids, **kwargs):
            seen["run"] = await db.scalar(select(ActionRun.status).where(ActionRun.id == run_id))
            seen["blocker"] = await check_host_busy(db, host_id)
            return await real(host_ids, **kwargs)

        return patch.object(host_lock, "release_host_queue", new=spy)

    async def test_action_host(self, db, fake_redis):
        from app.tasks.action_host import _run_action_host_async

        host = await _host(db)
        # ``running``, as the orchestrator leaves it before dispatching the
        # per-host task.
        run = ActionRun(
            action_key="_builtin.drift_check",
            action_version="1.0",
            host_id=host.id,
            parameters={},
            parallelism=1,
            status="running",
        )
        db.add(run)
        await db.flush()
        hr = ActionHostRun(action_run_id=run.id, host_id=host.id, status="queued")
        db.add(hr)
        await db.flush()
        # What the run's post-run hook leaves behind.
        follow_up = await _sync(
            db, host.id, module_type="package", module_filter=["packages"], age=5
        )
        await db.commit()

        runner = MagicMock(stdout="", status="successful", rc=0, events=[])
        delay = MagicMock()
        seen: dict = {}
        with (
            patch("app.ansible_runtime.runner.run_ansible", return_value=runner),
            patch("app.tasks.host_sync_orchestrator.run_host_sync.delay", new=delay),
            self._spy_release(db, run.id, host.id, seen),
        ):
            await _run_action_host_async(run.id, hr.id)

        assert seen["run"] in ("succeeded", "failed", "partial")
        assert seen["blocker"] is None
        delay.assert_called_once()
        assert delay.call_args.kwargs["job_id"] == follow_up

    async def test_builtin_dispatcher(self, db):
        from app.tasks.builtin_dispatchers import _finish_host_run

        host = await _host(db)
        run = ActionRun(
            action_key="_builtin.collect_state",
            action_version="1.0",
            host_id=host.id,
            parameters={},
            parallelism=1,
            status="running",
        )
        db.add(run)
        await db.flush()
        hr = ActionHostRun(action_run_id=run.id, host_id=host.id, status="running")
        db.add(hr)
        await db.flush()
        waiting = await _sync(db, host.id, module_type="firewall", age=5)
        await db.commit()

        delay = MagicMock()
        seen: dict = {}
        with (
            patch("app.tasks.host_sync_orchestrator.run_host_sync.delay", new=delay),
            self._spy_release(db, run.id, host.id, seen),
        ):
            await _finish_host_run(hr.id, succeeded=True)

        assert seen["run"] == "succeeded"
        assert seen["blocker"] is None
        assert delay.call_args.kwargs["job_id"] == waiting
