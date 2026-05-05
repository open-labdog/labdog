"""Smoke-tests for the ScheduledAction model + migration round-trip.

Covers the C1 scope: insert/round-trip, target-kind check constraint,
unique constraint on (target_kind, target_id, action_key), and the
relaxed action_runs scope check that allows fleet runs (both host_id
and group_id NULL when scheduled_action_id is set).
"""

from __future__ import annotations

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.models.action_run import ActionRun
from app.models.host import Host
from app.models.host_group import HostGroup
from app.models.scheduled_action import ScheduledAction

pytestmark = pytest.mark.integration


async def test_scheduled_action_round_trip(db):
    """Insert a row and read it back."""
    sa = ScheduledAction(
        target_kind="fleet",
        target_id=None,
        action_key="_builtin.drift_check",
        parameters={},
        schedule_cron="0 3 * * *",
        enabled=True,
    )
    db.add(sa)
    await db.flush()
    await db.refresh(sa)

    assert sa.id is not None
    assert sa.snapshot_enabled is True  # server default
    assert sa.batch_size == 1
    assert sa.last_dispatched_at is None

    fetched = (
        await db.execute(select(ScheduledAction).where(ScheduledAction.id == sa.id))
    ).scalar_one()
    assert fetched.action_key == "_builtin.drift_check"


async def test_scheduled_action_fleet_requires_null_target_id(db):
    """fleet + target_id != NULL is rejected by the check constraint."""
    db.add(
        ScheduledAction(
            target_kind="fleet",
            target_id=42,  # invalid
            action_key="_builtin.drift_check",
            schedule_cron="0 3 * * *",
        )
    )
    with pytest.raises(IntegrityError):
        await db.flush()


async def test_scheduled_action_group_requires_target_id(db):
    """group/host kinds require target_id NOT NULL."""
    db.add(
        ScheduledAction(
            target_kind="group",
            target_id=None,  # invalid
            action_key="linux-upgrade",
            schedule_cron="0 3 * * 0",
        )
    )
    with pytest.raises(IntegrityError):
        await db.flush()


async def test_scheduled_action_unique_per_target_action_key(db):
    """Two schedules for the same (target, action_key) collide."""
    group = HostGroup(name="dup-test-group", priority=100)
    db.add(group)
    await db.flush()

    db.add(
        ScheduledAction(
            target_kind="group",
            target_id=group.id,
            action_key="linux-upgrade",
            schedule_cron="0 3 * * 0",
        )
    )
    await db.flush()

    db.add(
        ScheduledAction(
            target_kind="group",
            target_id=group.id,
            action_key="linux-upgrade",
            schedule_cron="0 4 * * 0",
        )
    )
    with pytest.raises(IntegrityError):
        await db.flush()


async def test_action_run_fleet_scope_allowed_when_scheduled(db):
    """Both host_id AND group_id NULL is allowed when scheduled_action_id is set."""
    sa = ScheduledAction(
        target_kind="fleet",
        target_id=None,
        action_key="_builtin.drift_check",
        schedule_cron="0 3 * * *",
    )
    db.add(sa)
    await db.flush()

    run = ActionRun(
        action_key="_builtin.drift_check",
        action_version="1.0.0",
        host_id=None,
        group_id=None,
        scheduled_action_id=sa.id,
        parameters={},
        parallelism=1,
        status="queued",
    )
    db.add(run)
    await db.flush()
    await db.refresh(run)

    assert run.id is not None
    assert run.scheduled_action_id == sa.id
    # Universal columns default to true.
    assert run.snapshot_enabled is True
    assert run.verify_enabled is True
    assert run.auto_rollback is True


async def test_action_run_rejects_both_target_null_without_schedule(db):
    """Ad-hoc runs (no scheduled_action_id) still require host or group."""
    db.add(
        ActionRun(
            action_key="linux-upgrade",
            action_version="1.0.0",
            host_id=None,
            group_id=None,
            scheduled_action_id=None,  # not a scheduled run
            parameters={},
            parallelism=1,
            status="queued",
        )
    )
    with pytest.raises(IntegrityError):
        await db.flush()


async def test_deleting_scheduled_action_nulls_run_fk(db):
    """ON DELETE SET NULL preserves run history when a schedule is removed."""
    host = Host(hostname="del-test-host", ip_address="10.0.0.1", ssh_user="root")
    db.add(host)
    await db.flush()

    sa = ScheduledAction(
        target_kind="host",
        target_id=host.id,
        action_key="_builtin.collect_state",
        schedule_cron="0 * * * *",
    )
    db.add(sa)
    await db.flush()

    run = ActionRun(
        action_key="_builtin.collect_state",
        action_version="1.0.0",
        host_id=host.id,
        scheduled_action_id=sa.id,
        parameters={},
        parallelism=1,
        status="succeeded",
    )
    db.add(run)
    await db.flush()
    run_id = run.id

    await db.delete(sa)
    await db.flush()

    # The DB sets the FK to NULL via ON DELETE SET NULL, but the session
    # holds the run row with its old FK value cached. Expire so the next
    # read goes back to the DB.
    db.expire(run)
    fetched = (await db.execute(select(ActionRun).where(ActionRun.id == run_id))).scalar_one()
    assert fetched.scheduled_action_id is None
    assert fetched.action_key == "_builtin.collect_state"
