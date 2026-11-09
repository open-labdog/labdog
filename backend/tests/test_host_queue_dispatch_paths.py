"""Characterisation tests for the dispatch branches ``test_host_lock`` leaves dark.

``dispatch_next_pending_for_host`` re-queries after any row it cannot
dispatch. These pin the two defensive exits ``test_host_lock.py`` does not
reach (a host-targeted run whose host is gone, and a run another finisher
claimed first), plus a CA certificate deploy taking its own task, so a
restructuring of ``app.tasks.host_lock`` has something to answer to.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.tasks import host_lock
from tests.conftest import create_group, create_host, create_ssh_key
from tests.test_host_lock import _create_action_run, _create_pending_sync_job

pytestmark = pytest.mark.integration


def _delays():
    sync_delay, action_delay, ca_delay = MagicMock(), MagicMock(), MagicMock()
    return (
        (sync_delay, action_delay, ca_delay),
        (
            patch("app.tasks.host_sync_orchestrator.run_host_sync.delay", new=sync_delay),
            patch("app.tasks.action_orchestrator.run_action.delay", new=action_delay),
            patch("app.tasks.ca_cert_action.run_ca_cert_action.delay", new=ca_delay),
        ),
    )


async def test_a_host_run_whose_host_is_gone_is_failed_and_skipped(db: AsyncSession):
    from app.models.action_run import ActionRun

    ssh = await create_ssh_key(db)
    host = await create_host(db, ssh_key_id=ssh.id, ip="10.0.0.61")
    run_id = await _create_action_run(db, host_id=host.id, status="pending", age_seconds=5)

    orig_execute = db.execute

    async def _host_missing(stmt, *args, **kwargs):
        sql = str(stmt)
        if "FROM hosts" in sql and "hosts.id" in sql:
            result = MagicMock()
            result.scalar_one_or_none.return_value = None
            return result
        return await orig_execute(stmt, *args, **kwargs)

    (sync_delay, action_delay, _), (p1, p2, p3) = _delays()
    with patch.object(db, "execute", side_effect=_host_missing), p1, p2, p3:
        result = await host_lock.dispatch_next_pending_for_host(db, host.id)

    assert result is None
    action_delay.assert_not_called()
    sync_delay.assert_not_called()
    run = (await db.execute(select(ActionRun).where(ActionRun.id == run_id))).scalar_one()
    assert run.status == "failed"
    assert run.error_message == "host no longer exists"


@pytest.mark.parametrize("target", ["host", "group"])
async def test_a_run_claimed_by_another_finisher_is_re_queried(db: AsyncSession, target):
    group = await create_group(db)
    ssh = await create_ssh_key(db)
    host = await create_host(db, ssh_key_id=ssh.id, ip="10.0.0.62", group_ids=[group.id])
    if target == "host":
        run_id = await _create_action_run(db, host_id=host.id, status="pending", age_seconds=5)
    else:
        run_id = await _create_action_run(db, group_id=group.id, status="pending", age_seconds=5)

    real_claim = host_lock._claim_pending_run
    claim = AsyncMock(side_effect=[False, True])

    async def _lose_then_win(db_, action_run_id):
        if await claim(db_, action_run_id):
            return await real_claim(db_, action_run_id)
        return False

    (_, action_delay, _), (p1, p2, p3) = _delays()
    with patch.object(host_lock, "_claim_pending_run", new=_lose_then_win), p1, p2, p3:
        result = await host_lock.dispatch_next_pending_for_host(db, host.id)

    assert result == (f"action_{target}", run_id)
    assert claim.await_count == 2
    action_delay.assert_called_once_with(run_id)


async def test_a_ca_certificate_deploy_goes_to_its_own_task(db: AsyncSession):
    from app.ca_certs.actions import CA_CERT_MODULE_TYPE

    ssh = await create_ssh_key(db)
    host = await create_host(db, ssh_key_id=ssh.id, ip="10.0.0.63")
    job_id = await _create_pending_sync_job(
        db, host.id, module_type=CA_CERT_MODULE_TYPE, age_seconds=5
    )

    (sync_delay, action_delay, ca_delay), (p1, p2, p3) = _delays()
    with p1, p2, p3:
        result = await host_lock.dispatch_next_pending_for_host(db, host.id)

    assert result == ("sync", job_id)
    ca_delay.assert_called_once_with(job_id=job_id, host_id=host.id)
    sync_delay.assert_not_called()
    action_delay.assert_not_called()


async def test_a_sync_and_a_run_created_at_the_same_instant_go_sync_first(db: AsyncSession):
    """The pick is ``min`` over (sync, action, child) in that order, so a tie
    goes to the earlier list entry."""
    from datetime import UTC, datetime

    from app.models.action_run import ActionRun
    from app.models.sync_job import SyncJob

    ssh = await create_ssh_key(db)
    host = await create_host(db, ssh_key_id=ssh.id, ip="10.0.0.64")
    job_id = await _create_pending_sync_job(db, host.id)
    run_id = await _create_action_run(db, host_id=host.id, status="pending")
    instant = datetime(2026, 1, 1, tzinfo=UTC)
    (
        await db.execute(select(SyncJob).where(SyncJob.id == job_id))
    ).scalar_one().created_at = instant
    (
        await db.execute(select(ActionRun).where(ActionRun.id == run_id))
    ).scalar_one().created_at = instant
    await db.flush()

    (sync_delay, action_delay, _), (p1, p2, p3) = _delays()
    with p1, p2, p3:
        result = await host_lock.dispatch_next_pending_for_host(db, host.id)

    assert result == ("sync", job_id)
    action_delay.assert_not_called()
