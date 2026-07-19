"""Tests for the periodic stale-ActionRun/ActionHostRun sweeper.

Covers the crash-recovery hole for the action pipeline (the sibling of
``test_sync_sweeper.py``, which covers ``SyncJob`` only):

- a stuck ``running`` ActionHostRun past its deadline is failed and the
  host's next pending op is dispatched
- a fresh ``running`` host-run is left alone
- ``pending`` / ``cancelled`` rows are never touched
- a parent run whose children are all terminal is aggregated
  (succeeded / partial / failed)
- a parent past the whole-run deadline is failed and its queued/pending
  children cancelled
- a ``queued`` run never picked up within the orphan threshold is failed
- a second sweep is idempotent
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.action_run import ActionHostRun, ActionRun
from app.tasks.action_sweeper import (
    QUEUED_ORPHAN_THRESHOLD_SECONDS,
    _sweep_stale_action_runs_async,
)
from tests.conftest import create_host, create_ssh_key

pytestmark = pytest.mark.integration

# A made-up action key: the sweeper falls back to fixed deadlines on a
# registry miss, so we don't depend on the bundled pack being loaded.
STALE_KEY = "test.stale-action"


@pytest.fixture(autouse=True)
def patch_task_session(db):
    @asynccontextmanager
    async def _fake():
        yield db

    with patch("app.tasks.action_sweeper.task_session", new=_fake):
        yield


@pytest.fixture(autouse=True)
def fixed_setting():
    """Pin ``ansible.playbook_timeout`` so per-host/run deadlines are
    deterministic regardless of DB settings.

    per_host_deadline (registry miss) = 1800 + 300(verify) + 900(grace) = 3000s
    run_deadline (1 host, parallelism 1) = 3000 + 3600(slack) = 6600s
    """
    with patch("app.settings_service.get_setting_sync_typed", return_value=1800):
        yield


PER_HOST_DEADLINE = 3000
RUN_DEADLINE_1H = 6600


@pytest.fixture
def stub_redispatch():
    """dispatch_next_pending_for_host re-dispatches via ``.delay`` — stub
    both queue entrypoints so the sweeper doesn't enqueue real Celery work."""
    action_delay = MagicMock()
    with (
        patch("app.tasks.action_orchestrator.run_action.delay", action_delay),
        patch("app.tasks.host_sync_orchestrator.run_host_sync.delay", MagicMock()),
    ):
        yield action_delay


def _ago(seconds: int) -> datetime:
    return datetime.now(UTC) - timedelta(seconds=seconds)


async def _make_run(
    db: AsyncSession,
    *,
    host_id: int,
    status: str = "running",
    created_seconds_ago: int = 60,
    started_seconds_ago: int | None = 60,
    parallelism: int = 1,
    group_id: int | None = None,
) -> int:
    run = ActionRun(
        action_key=STALE_KEY,
        action_version="1.0",
        host_id=host_id if group_id is None else None,
        group_id=group_id,
        parameters={},
        parallelism=parallelism,
        status=status,
        created_at=_ago(created_seconds_ago),
        started_at=_ago(started_seconds_ago) if started_seconds_ago is not None else None,
    )
    db.add(run)
    await db.flush()
    return run.id


async def _make_host_run(
    db: AsyncSession,
    *,
    run_id: int,
    host_id: int,
    status: str = "running",
    started_seconds_ago: int | None = 60,
) -> int:
    hr = ActionHostRun(
        action_run_id=run_id,
        host_id=host_id,
        status=status,
        started_at=_ago(started_seconds_ago) if started_seconds_ago is not None else None,
    )
    db.add(hr)
    await db.flush()
    return hr.id


# ---------------------------------------------------------------------------
# Pass 1: stale host runs
# ---------------------------------------------------------------------------


async def test_stale_host_run_swept_and_dispatches_next(db, stub_redispatch):
    ssh_key = await create_ssh_key(db)
    host = await create_host(db, ssh_key_id=ssh_key.id)

    run_id = await _make_run(db, host_id=host.id, started_seconds_ago=2 * 86400)
    hr_id = await _make_host_run(
        db, run_id=run_id, host_id=host.id, started_seconds_ago=2 * 86400
    )
    # A queued sibling run on the same host, waiting to be dispatched.
    pending_id = await _make_run(
        db,
        host_id=host.id,
        status="pending",
        created_seconds_ago=1000,
        started_seconds_ago=None,
    )
    await db.commit()

    result = await _sweep_stale_action_runs_async()

    assert hr_id in result["host_runs_swept"]
    hr = (await db.execute(select(ActionHostRun).where(ActionHostRun.id == hr_id))).scalar_one()
    assert hr.status == "failed"
    assert "Stuck in 'running'" in (hr.error_message or "")
    assert "action_sweeper" in (hr.error_message or "")

    # The pending sibling was re-dispatched.
    assert stub_redispatch.called
    assert pending_id in result["dispatched"]


async def test_fresh_running_host_run_untouched(db, stub_redispatch):
    ssh_key = await create_ssh_key(db)
    host = await create_host(db, ssh_key_id=ssh_key.id)

    run_id = await _make_run(db, host_id=host.id, started_seconds_ago=60)
    hr_id = await _make_host_run(db, run_id=run_id, host_id=host.id, started_seconds_ago=60)
    await db.commit()

    result = await _sweep_stale_action_runs_async()

    assert hr_id not in result["host_runs_swept"]
    hr = (await db.execute(select(ActionHostRun).where(ActionHostRun.id == hr_id))).scalar_one()
    assert hr.status == "running"


async def test_pending_and_cancelled_rows_untouched(db, stub_redispatch):
    ssh_key = await create_ssh_key(db)
    host = await create_host(db, ssh_key_id=ssh_key.id)

    # A pending run (legitimate host-busy wait) that's very old.
    pending_run = await _make_run(
        db,
        host_id=host.id,
        status="pending",
        created_seconds_ago=3 * 86400,
        started_seconds_ago=None,
    )
    # A cancelled run, also old.
    cancelled_run = await _make_run(
        db,
        host_id=host.id,
        status="cancelled",
        created_seconds_ago=3 * 86400,
        started_seconds_ago=3 * 86400,
    )
    # A pending host-run on a different host.
    host2 = await create_host(db, ip="10.9.9.9", ssh_key_id=ssh_key.id)
    other_run = await _make_run(db, host_id=host2.id, started_seconds_ago=60)
    pending_hr = await _make_host_run(
        db, run_id=other_run, host_id=host2.id, status="pending", started_seconds_ago=None
    )
    await db.commit()

    result = await _sweep_stale_action_runs_async()

    assert result["host_runs_swept"] == []
    assert pending_run not in result["runs_failed"]
    assert cancelled_run not in result["runs_failed"]

    pr = (await db.execute(select(ActionRun).where(ActionRun.id == pending_run))).scalar_one()
    assert pr.status == "pending"
    cr = (await db.execute(select(ActionRun).where(ActionRun.id == cancelled_run))).scalar_one()
    assert cr.status == "cancelled"
    phr = (
        await db.execute(select(ActionHostRun).where(ActionHostRun.id == pending_hr))
    ).scalar_one()
    assert phr.status == "pending"


# ---------------------------------------------------------------------------
# Pass 2: parent reconciliation
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "child_statuses,expected",
    [
        (["succeeded", "succeeded"], "succeeded"),
        (["succeeded", "failed"], "partial"),
        (["failed", "failed"], "failed"),
    ],
)
async def test_parent_with_all_terminal_children_aggregated(
    db, stub_redispatch, child_statuses, expected
):
    ssh_key = await create_ssh_key(db)
    host = await create_host(db, ssh_key_id=ssh_key.id)

    # Parent still ``running`` but every child already terminal — the
    # orchestrator died after the children finished.
    run_id = await _make_run(db, host_id=host.id, started_seconds_ago=120)
    for i, st in enumerate(child_statuses):
        h = await create_host(db, ip=f"10.5.0.{i + 1}", ssh_key_id=ssh_key.id)
        await _make_host_run(db, run_id=run_id, host_id=h.id, status=st, started_seconds_ago=120)
    await db.commit()

    result = await _sweep_stale_action_runs_async()

    assert run_id in result["runs_finalised"]
    run = (await db.execute(select(ActionRun).where(ActionRun.id == run_id))).scalar_one()
    assert run.status == expected
    assert run.finished_at is not None


async def test_run_past_deadline_fails_and_cancels_queued_children(db, stub_redispatch):
    ssh_key = await create_ssh_key(db)
    host = await create_host(db, ssh_key_id=ssh_key.id)

    # Parent running past the whole-run deadline, with a queued child that
    # was never dispatched.
    run_id = await _make_run(
        db, host_id=host.id, started_seconds_ago=RUN_DEADLINE_1H + 600
    )
    queued_child = await _make_host_run(
        db, run_id=run_id, host_id=host.id, status="queued", started_seconds_ago=None
    )
    await db.commit()

    result = await _sweep_stale_action_runs_async()

    assert run_id in result["runs_failed"]
    run = (await db.execute(select(ActionRun).where(ActionRun.id == run_id))).scalar_one()
    assert run.status == "failed"
    assert "exceeded overall deadline" in (run.error_message or "")

    child = (
        await db.execute(select(ActionHostRun).where(ActionHostRun.id == queued_child))
    ).scalar_one()
    assert child.status == "cancelled"
    assert "parent run swept" in (child.error_message or "")


async def test_stale_queued_run_never_picked_up_is_failed(db, stub_redispatch):
    ssh_key = await create_ssh_key(db)
    host = await create_host(db, ssh_key_id=ssh_key.id)

    # Queued, no children, no started_at, older than the orphan threshold.
    run_id = await _make_run(
        db,
        host_id=host.id,
        status="queued",
        created_seconds_ago=QUEUED_ORPHAN_THRESHOLD_SECONDS + 600,
        started_seconds_ago=None,
    )
    await db.commit()

    result = await _sweep_stale_action_runs_async()

    assert run_id in result["runs_failed"]
    run = (await db.execute(select(ActionRun).where(ActionRun.id == run_id))).scalar_one()
    assert run.status == "failed"
    assert "never picked up" in (run.error_message or "")


async def test_recent_queued_run_untouched(db, stub_redispatch):
    ssh_key = await create_ssh_key(db)
    host = await create_host(db, ssh_key_id=ssh_key.id)

    run_id = await _make_run(
        db,
        host_id=host.id,
        status="queued",
        created_seconds_ago=120,
        started_seconds_ago=None,
    )
    await db.commit()

    result = await _sweep_stale_action_runs_async()

    assert run_id not in result["runs_failed"]
    run = (await db.execute(select(ActionRun).where(ActionRun.id == run_id))).scalar_one()
    assert run.status == "queued"


# ---------------------------------------------------------------------------
# Idempotency
# ---------------------------------------------------------------------------


async def test_second_sweep_is_idempotent(db, stub_redispatch):
    ssh_key = await create_ssh_key(db)
    host = await create_host(db, ssh_key_id=ssh_key.id)

    run_id = await _make_run(db, host_id=host.id, started_seconds_ago=2 * 86400)
    hr_id = await _make_host_run(
        db, run_id=run_id, host_id=host.id, started_seconds_ago=2 * 86400
    )
    await db.commit()

    first = await _sweep_stale_action_runs_async()
    second = await _sweep_stale_action_runs_async()

    assert hr_id in first["host_runs_swept"]
    assert second["host_runs_swept"] == []
    # Parent was aggregated on the first pass (its only child is terminal).
    assert run_id in first["runs_finalised"] or run_id in first["runs_failed"]
    assert second["runs_finalised"] == []
    assert second["runs_failed"] == []
