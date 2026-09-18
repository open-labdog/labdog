"""BUG-64: a deferred bulk sync must reapply what it was asked to, and no more.

``SyncJob`` had no column for the module list. The bulk endpoint stored
the literal ``module_type="bulk"`` and passed the real filter only as a
Celery kwarg — which does not survive a defer. When the target host was
busy the job sat in ``pending``, and the re-dispatch rebuilt the filter
from the row with ``_filter_from_module_type("bulk")``, which returns
``None``, meaning *every* module.

So an operator who asked to reapply firewall on a busy host got packages
reinstalled, services restarted and ``/etc/hosts`` rewritten when the
queue drained — silently, and only on busy hosts, which is why it did not
show up in ordinary use.

The escalation is the failure that matters, so most of these assert on
what a re-dispatch is *given*, not on what the column holds.
"""

from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.sync_job import SyncJob
from app.tasks.host_lock import dispatch_next_pending_for_host
from app.tasks.host_sync_orchestrator import module_filter_for
from tests.conftest import create_host, create_ssh_key

pytestmark = pytest.mark.integration


async def _pending(db, host_id, *, module_type="bulk", module_filter=None, age_seconds=0) -> int:
    job = SyncJob(
        host_id=host_id,
        status="pending",
        module_type=module_type,
        module_filter=module_filter,
    )
    db.add(job)
    await db.flush()
    if age_seconds:
        job.created_at = datetime.now(UTC) - timedelta(seconds=age_seconds)
        await db.flush()
    return job.id


class TestTheRowRecordsWhatWasAsked:
    async def test_a_filtered_bulk_sync_persists_its_filter(self, superuser_client, db):
        ssh = await create_ssh_key(db)
        host = await create_host(db, ssh_key_id=ssh.id)
        await db.commit()

        with patch("app.tasks.host_sync_orchestrator.run_host_sync.delay", new=MagicMock()):
            resp = await superuser_client.post(
                f"/api/sync/hosts/{host.id}/bulk",
                json={"module_filter": ["firewall"]},
            )
        assert resp.status_code == 201, resp.text

        job = await db.scalar(select(SyncJob).where(SyncJob.id == resp.json()["job_id"]))
        assert job.module_filter == ["firewall"]

    async def test_an_unfiltered_bulk_sync_records_nothing(self, superuser_client, db):
        """NULL and "every module" are the same instruction, and NULL is
        what a pre-existing row holds — keep the two indistinguishable
        rather than inventing a seven-element list."""
        ssh = await create_ssh_key(db)
        host = await create_host(db, ssh_key_id=ssh.id)
        await db.commit()

        with patch("app.tasks.host_sync_orchestrator.run_host_sync.delay", new=MagicMock()):
            resp = await superuser_client.post(f"/api/sync/hosts/{host.id}/bulk", json={})
        assert resp.status_code == 201, resp.text

        job = await db.scalar(select(SyncJob).where(SyncJob.id == resp.json()["job_id"]))
        assert job.module_filter is None


class TestReconstructingTheFilter:
    async def test_a_recorded_filter_wins(self, db: AsyncSession):
        ssh = await create_ssh_key(db)
        host = await create_host(db, ssh_key_id=ssh.id)
        job_id = await _pending(db, host.id, module_filter=["firewall"])
        job = await db.scalar(select(SyncJob).where(SyncJob.id == job_id))
        assert module_filter_for(job) == ["firewall"]

    async def test_a_legacy_bulk_row_still_means_every_module(self, db: AsyncSession):
        """Rows written before the column existed genuinely did apply
        everything; changing that on upgrade would be its own surprise."""
        ssh = await create_ssh_key(db)
        host = await create_host(db, ssh_key_id=ssh.id)
        job_id = await _pending(db, host.id, module_type="bulk", module_filter=None)
        job = await db.scalar(select(SyncJob).where(SyncJob.id == job_id))
        assert module_filter_for(job) is None

    async def test_a_per_module_row_needs_no_recorded_filter(self, db: AsyncSession):
        """The per-tab endpoints name their single module in module_type,
        which reconstructs exactly — they are not affected by this bug."""
        ssh = await create_ssh_key(db)
        host = await create_host(db, ssh_key_id=ssh.id)
        job_id = await _pending(db, host.id, module_type="firewall", module_filter=None)
        job = await db.scalar(select(SyncJob).where(SyncJob.id == job_id))
        assert module_filter_for(job) == ["firewall"]


class TestTheDeferredRedispatch:
    """The bug itself: what the queue hands the orchestrator when it drains."""

    async def test_a_deferred_filtered_bulk_sync_does_not_escalate(self, db: AsyncSession):
        ssh = await create_ssh_key(db)
        host = await create_host(db, ssh_key_id=ssh.id)
        job_id = await _pending(db, host.id, module_filter=["firewall"], age_seconds=30)

        delay = MagicMock()
        with patch("app.tasks.host_sync_orchestrator.run_host_sync.delay", new=delay):
            result = await dispatch_next_pending_for_host(db, host.id)

        assert result == ("sync", job_id)
        delay.assert_called_once()
        assert delay.call_args.kwargs["module_filter"] == ["firewall"], (
            "the re-dispatch escalated to every module — this is the bug"
        )

    async def test_a_deferred_bulk_sync_with_no_filter_still_applies_everything(self, db):
        ssh = await create_ssh_key(db)
        host = await create_host(db, ssh_key_id=ssh.id)
        job_id = await _pending(db, host.id, module_filter=None, age_seconds=30)

        delay = MagicMock()
        with patch("app.tasks.host_sync_orchestrator.run_host_sync.delay", new=delay):
            result = await dispatch_next_pending_for_host(db, host.id)

        assert result == ("sync", job_id)
        assert delay.call_args.kwargs["module_filter"] is None

    async def test_a_deferred_per_module_sync_is_unchanged(self, db: AsyncSession):
        """`module_type` holds the short DB name ("service"), which maps to
        the canonical name the orchestrator wants ("services")."""
        ssh = await create_ssh_key(db)
        host = await create_host(db, ssh_key_id=ssh.id)
        job_id = await _pending(db, host.id, module_type="service", age_seconds=30)

        delay = MagicMock()
        with patch("app.tasks.host_sync_orchestrator.run_host_sync.delay", new=delay):
            result = await dispatch_next_pending_for_host(db, host.id)

        assert result == ("sync", job_id)
        assert delay.call_args.kwargs["module_filter"] == ["services"]


class TestTheApiReportsWhatWillHappen:
    async def test_the_idempotent_200_names_the_queued_jobs_filter(self, superuser_client, db):
        """A second request while one is in flight returns 200 and the
        *existing* job's filter — which may differ from what was just
        asked for. It used to return None because the row did not know."""
        ssh = await create_ssh_key(db)
        host = await create_host(db, ssh_key_id=ssh.id)
        await db.commit()

        with patch("app.tasks.host_sync_orchestrator.run_host_sync.delay", new=MagicMock()):
            first = await superuser_client.post(
                f"/api/sync/hosts/{host.id}/bulk", json={"module_filter": ["firewall"]}
            )
            assert first.status_code == 201, first.text
            second = await superuser_client.post(
                f"/api/sync/hosts/{host.id}/bulk", json={"module_filter": ["packages"]}
            )

        assert second.status_code == 200, second.text
        assert second.json()["job_id"] == first.json()["job_id"]
        assert second.json()["module_filter"] == ["firewall"]
