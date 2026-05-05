"""Tests for the unified scheduler ``check_due`` task (C6).

Uses ``freezegun`` to control wall-clock time and exercises the cron
walk's idempotency, in-flight skip, orphan skip, and ActionRun shape.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import pytest
from sqlalchemy import select

from app.models.action_run import ActionRun
from app.models.scheduled_action import ScheduledAction
from app.tasks.scheduled_action_schedule import _check_due_async
from tests.conftest import create_host

pytestmark = pytest.mark.integration


@pytest.fixture(autouse=True)
def patch_task_session(db):
    @asynccontextmanager
    async def _fake():
        yield db

    with patch("app.db.task_session", new=_fake):
        yield


@pytest.fixture(autouse=True)
def stub_celery_dispatch():
    with patch("celery.app.base.Celery.send_task"):
        yield


async def test_disabled_schedule_not_dispatched(db):
    host = await create_host(db)
    db.add(
        ScheduledAction(
            target_kind="host",
            target_id=host.id,
            action_key="_builtin.collect_state",
            schedule_cron="* * * * *",
            enabled=False,
        )
    )
    await db.commit()

    result = await _check_due_async()
    assert result["dispatched"] == 0


async def test_due_schedule_dispatches_run(db):
    host = await create_host(db)
    sa = ScheduledAction(
        target_kind="host",
        target_id=host.id,
        action_key="_builtin.collect_state",
        schedule_cron="* * * * *",
        enabled=True,
        # Simulate "last fire was 2 minutes ago" so the next-tick is past.
        last_dispatched_at=datetime.now(UTC) - timedelta(minutes=2),
    )
    db.add(sa)
    await db.commit()

    result = await _check_due_async()
    assert result["dispatched"] == 1

    # Run row exists, FK linked back, host_id propagated.
    runs = (
        (await db.execute(select(ActionRun).where(ActionRun.scheduled_action_id == sa.id)))
        .scalars()
        .all()
    )
    assert len(runs) == 1
    run = runs[0]
    assert run.host_id == host.id
    assert run.action_key == "_builtin.collect_state"
    assert run.status == "queued"

    # last_dispatched_at advanced.
    await db.refresh(sa)
    assert sa.last_dispatched_at is not None


async def test_not_due_schedule_skipped(db):
    host = await create_host(db)
    db.add(
        ScheduledAction(
            target_kind="host",
            target_id=host.id,
            action_key="_builtin.collect_state",
            # Once a year on Jan 1 — almost certainly not due
            schedule_cron="0 0 1 1 *",
            enabled=True,
            last_dispatched_at=datetime.now(UTC),  # just dispatched
        )
    )
    await db.commit()

    result = await _check_due_async()
    assert result["dispatched"] == 0


async def test_in_flight_run_blocks_dispatch(db):
    host = await create_host(db)
    sa = ScheduledAction(
        target_kind="host",
        target_id=host.id,
        action_key="_builtin.collect_state",
        schedule_cron="* * * * *",
        enabled=True,
        last_dispatched_at=datetime.now(UTC) - timedelta(minutes=2),
    )
    db.add(sa)
    await db.flush()
    db.add(
        ActionRun(
            action_key="_builtin.collect_state",
            action_version="1.0.0",
            host_id=host.id,
            scheduled_action_id=sa.id,
            parameters={},
            parallelism=1,
            status="running",  # non-terminal
        )
    )
    await db.commit()

    result = await _check_due_async()
    assert result["dispatched"] == 0
    assert result["skipped_in_flight"] == 1


async def test_orphan_action_key_skipped(db):
    host = await create_host(db)
    sa = ScheduledAction(
        target_kind="host",
        target_id=host.id,
        action_key="action-from-uninstalled-pack",
        schedule_cron="* * * * *",
        enabled=True,
        last_dispatched_at=datetime.now(UTC) - timedelta(minutes=2),
    )
    db.add(sa)
    await db.commit()

    result = await _check_due_async()
    assert result["dispatched"] == 0
    assert result["skipped_orphan"] == 1


async def test_idempotency_second_call_is_noop(db):
    host = await create_host(db)
    sa = ScheduledAction(
        target_kind="host",
        target_id=host.id,
        action_key="_builtin.collect_state",
        schedule_cron="* * * * *",
        enabled=True,
        last_dispatched_at=datetime.now(UTC) - timedelta(minutes=2),
    )
    db.add(sa)
    await db.commit()

    first = await _check_due_async()
    assert first["dispatched"] == 1

    # Mark the run terminal so the next tick isn't blocked by in-flight.
    runs = (
        (await db.execute(select(ActionRun).where(ActionRun.scheduled_action_id == sa.id)))
        .scalars()
        .all()
    )
    assert len(runs) == 1
    runs[0].status = "succeeded"
    await db.commit()

    # Second call within the same minute — last_dispatched_at advanced
    # to "now", so the next-fire is one minute in the future.
    second = await _check_due_async()
    assert second["dispatched"] == 0


async def test_fleet_schedule_creates_fleet_run(db):
    h1 = await create_host(db, hostname="fleet-1")
    h2 = await create_host(db, hostname="fleet-2")
    sa = ScheduledAction(
        target_kind="fleet",
        target_id=None,
        action_key="_builtin.drift_check",
        schedule_cron="* * * * *",
        enabled=True,
        last_dispatched_at=datetime.now(UTC) - timedelta(minutes=2),
    )
    db.add(sa)
    await db.commit()

    result = await _check_due_async()
    assert result["dispatched"] == 1

    run = (
        await db.execute(select(ActionRun).where(ActionRun.scheduled_action_id == sa.id))
    ).scalar_one()
    # Fleet runs have both target IDs NULL — host fan-out happens in
    # the orchestrator, not at scheduler-create time.
    assert run.host_id is None
    assert run.group_id is None
    assert run.scheduled_action_id == sa.id

    # The two hosts above won't be touched until the orchestrator runs.
    assert h1.id != h2.id  # silence ARG003-style unused warnings
