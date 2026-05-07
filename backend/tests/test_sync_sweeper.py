"""Tests for the periodic stale-SyncJob sweeper.

Covers:
- a stuck job (started > threshold ago, status=running) is flipped to failed
- a fresh job (started < threshold ago) is left alone
- a non-running job (already success/failed) is left alone
- per-module HostModuleStatus rows are flipped to error
- audit row is emitted with action=sync_failed and the right shape
- the queued successor (next pending SyncJob for the same host) gets
  dispatched via run_host_sync.delay
- idempotency: running the sweeper twice doesn't double-emit audit
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.host_module_status import HostModuleStatus
from app.models.sync_job import JobStatus, SyncJob
from app.tasks.sync_sweeper import (
    STALE_THRESHOLD_MINUTES,
    _sweep_stale_syncs_async,
)
from tests.conftest import create_host, create_ssh_key

pytestmark = pytest.mark.integration


@pytest.fixture(autouse=True)
def patch_task_session(db):
    """Make the sweeper's ``task_session()`` yield the test's savepoint-wrapped
    session. Without this, the sweeper opens its own engine via the real
    ``task_session()`` and the test's writes are invisible to it."""

    @asynccontextmanager
    async def _fake():
        yield db

    with (
        patch("app.tasks.sync_sweeper.task_session", new=_fake),
        patch("app.tasks.host_sync_orchestrator.task_session", new=_fake),
    ):
        yield


def _utc_minutes_ago(minutes: int) -> datetime:
    return datetime.now(UTC) - timedelta(minutes=minutes)


async def _create_running_syncjob(
    db: AsyncSession,
    *,
    host_id: int,
    started_minutes_ago: int,
    module_type: str = "bulk",
    triggered_by_user_id: int | None = None,
) -> int:
    """Insert a SyncJob in ``running`` status with a controlled ``started_at``."""
    job = SyncJob(
        host_id=host_id,
        status=JobStatus.running,
        module_type=module_type,
        triggered_by_user_id=triggered_by_user_id,
        started_at=_utc_minutes_ago(started_minutes_ago),
    )
    db.add(job)
    await db.flush()
    return job.id


async def _seed_running_module_statuses(
    db: AsyncSession, host_id: int, db_module_types: list[str]
) -> None:
    for mt in db_module_types:
        db.add(
            HostModuleStatus(
                host_id=host_id,
                module_type=mt,
                sync_status="running",
            )
        )
    await db.flush()


@pytest.fixture
def mock_run_host_sync_delay():
    """Patch ``run_host_sync.delay`` at the orchestrator module so the sweeper's
    ``_dispatch_next_pending_for_host`` doesn't actually enqueue Celery work."""
    mock = MagicMock()
    with patch("app.tasks.host_sync_orchestrator.run_host_sync.delay", mock):
        yield mock


# ---------------------------------------------------------------------------
# happy-path: stuck job sweeps
# ---------------------------------------------------------------------------


async def test_sweeper_flips_stuck_job_to_failed(db: AsyncSession, mock_run_host_sync_delay):
    """A SyncJob in 'running' for > threshold gets marked failed."""
    from app.models.audit_log import AuditLog

    ssh_key = await create_ssh_key(db)
    host = await create_host(db, ssh_key_id=ssh_key.id)
    job_id = await _create_running_syncjob(
        db, host_id=host.id, started_minutes_ago=STALE_THRESHOLD_MINUTES + 5
    )
    # Seed all 7 modules in running so finalise has rows to flip.
    await _seed_running_module_statuses(
        db,
        host.id,
        ["firewall", "service", "package", "hosts_file", "cron", "linux_user", "resolver"],
    )
    await db.commit()

    result = await _sweep_stale_syncs_async()

    assert result["swept"] == [job_id]

    # SyncJob now failed with an explanatory error message.
    job = (await db.execute(select(SyncJob).where(SyncJob.id == job_id))).scalar_one()
    await db.refresh(job)
    assert job.status == JobStatus.failed
    assert job.completed_at is not None
    assert "Stuck in 'running'" in (job.error_message or "")
    assert "sync_sweeper" in (job.error_message or "")

    # All HostModuleStatus rows flipped to error with the same message.
    rows = (
        (await db.execute(select(HostModuleStatus).where(HostModuleStatus.host_id == host.id)))
        .scalars()
        .all()
    )
    assert len(rows) == 7
    assert all(r.sync_status == "error" for r in rows)
    assert all("Stuck in 'running'" in (r.error_message or "") for r in rows)

    # Audit row with action=sync_failed and the right shape.
    audits = (
        (
            await db.execute(
                select(AuditLog).where(
                    AuditLog.entity_type == "host",
                    AuditLog.entity_id == host.id,
                    AuditLog.action == "sync_failed",
                )
            )
        )
        .scalars()
        .all()
    )
    assert len(audits) == 1
    after = audits[0].after_state
    assert after["sync_job_id"] == job_id
    assert all(v == "error" for v in after["module_outcomes"].values())
    # The 7-module bulk filter is reconstructed as None for module_type="bulk".
    assert after["module_filter"] is None


# ---------------------------------------------------------------------------
# negative cases: fresh / completed jobs left alone
# ---------------------------------------------------------------------------


async def test_sweeper_leaves_fresh_running_job_alone(db: AsyncSession, mock_run_host_sync_delay):
    """A SyncJob that's been running < threshold is not touched."""
    ssh_key = await create_ssh_key(db)
    host = await create_host(db, ssh_key_id=ssh_key.id)
    job_id = await _create_running_syncjob(
        db,
        host_id=host.id,
        started_minutes_ago=STALE_THRESHOLD_MINUTES - 5,
    )
    await db.commit()

    result = await _sweep_stale_syncs_async()

    assert result["swept"] == []
    job = (await db.execute(select(SyncJob).where(SyncJob.id == job_id))).scalar_one()
    await db.refresh(job)
    assert job.status == JobStatus.running


async def test_sweeper_ignores_already_completed_job(db: AsyncSession, mock_run_host_sync_delay):
    """A SyncJob with ``status='success'`` whose ``started_at`` is old isn't re-touched."""
    ssh_key = await create_ssh_key(db)
    host = await create_host(db, ssh_key_id=ssh_key.id)

    job = SyncJob(
        host_id=host.id,
        status=JobStatus.success,
        module_type="bulk",
        started_at=_utc_minutes_ago(STALE_THRESHOLD_MINUTES + 60),
        completed_at=_utc_minutes_ago(STALE_THRESHOLD_MINUTES + 50),
    )
    db.add(job)
    await db.flush()
    await db.commit()
    job_id = job.id

    result = await _sweep_stale_syncs_async()
    assert result["swept"] == []

    refreshed = (await db.execute(select(SyncJob).where(SyncJob.id == job_id))).scalar_one()
    await db.refresh(refreshed)
    assert refreshed.status == JobStatus.success


# ---------------------------------------------------------------------------
# queue continuity: the queued successor gets dispatched
# ---------------------------------------------------------------------------


async def test_sweeper_dispatches_next_pending_for_same_host(
    db: AsyncSession, mock_run_host_sync_delay
):
    """When a stuck job is swept, any queued sibling for the same host gets ``.delay``-ed."""
    ssh_key = await create_ssh_key(db)
    host = await create_host(db, ssh_key_id=ssh_key.id)

    stuck_id = await _create_running_syncjob(
        db, host_id=host.id, started_minutes_ago=STALE_THRESHOLD_MINUTES + 1
    )
    # The queue's successor — different module_type to dodge the partial unique index.
    queued = SyncJob(
        host_id=host.id,
        status=JobStatus.pending,
        module_type="firewall",
        triggered_by_user_id=None,
    )
    db.add(queued)
    await db.flush()
    queued_id = queued.id
    await db.commit()

    result = await _sweep_stale_syncs_async()

    assert result["swept"] == [stuck_id]
    assert result["dispatched"] == [queued_id]
    assert mock_run_host_sync_delay.call_count == 1
    call = mock_run_host_sync_delay.call_args
    assert call.kwargs["job_id"] == queued_id
    assert call.kwargs["host_id"] == host.id
    assert call.kwargs["module_filter"] == ["firewall"]


# ---------------------------------------------------------------------------
# multi-host: each stuck job processed independently
# ---------------------------------------------------------------------------


async def test_sweeper_processes_multiple_stuck_jobs(db: AsyncSession, mock_run_host_sync_delay):
    ssh_key = await create_ssh_key(db)
    h1 = await create_host(db, ip="10.0.1.1", ssh_key_id=ssh_key.id)
    h2 = await create_host(db, ip="10.0.1.2", ssh_key_id=ssh_key.id)

    j1 = await _create_running_syncjob(
        db, host_id=h1.id, started_minutes_ago=STALE_THRESHOLD_MINUTES + 1
    )
    j2 = await _create_running_syncjob(
        db, host_id=h2.id, started_minutes_ago=STALE_THRESHOLD_MINUTES + 2
    )
    await db.commit()

    result = await _sweep_stale_syncs_async()
    assert set(result["swept"]) == {j1, j2}


# ---------------------------------------------------------------------------
# idempotency: running twice doesn't re-process already-failed jobs
# ---------------------------------------------------------------------------


async def test_sweeper_idempotent_on_second_run(db: AsyncSession, mock_run_host_sync_delay):
    """Once a job has been swept, a second sweeper pass doesn't re-emit audit."""
    from app.models.audit_log import AuditLog

    ssh_key = await create_ssh_key(db)
    host = await create_host(db, ssh_key_id=ssh_key.id)
    job_id = await _create_running_syncjob(
        db, host_id=host.id, started_minutes_ago=STALE_THRESHOLD_MINUTES + 1
    )
    await _seed_running_module_statuses(db, host.id, ["firewall"])
    await db.commit()

    first = await _sweep_stale_syncs_async()
    second = await _sweep_stale_syncs_async()

    assert first["swept"] == [job_id]
    assert second["swept"] == []  # nothing left to sweep

    audits = (
        (
            await db.execute(
                select(AuditLog).where(
                    AuditLog.entity_type == "host",
                    AuditLog.entity_id == host.id,
                    AuditLog.action == "sync_failed",
                )
            )
        )
        .scalars()
        .all()
    )
    assert len(audits) == 1
