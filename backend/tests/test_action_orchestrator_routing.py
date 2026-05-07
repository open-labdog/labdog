"""Tests for the action orchestrator's per-host task routing (C5).

Validates that:
- ``PER_HOST_TASK_FOR_BUILTIN`` maps each built-in to its dedicated
  per-host wrapper.
- For ``_builtin.*`` action_keys the orchestrator dispatches the
  matching wrapper, not the default Ansible runner.
- Pack-supplied actions still go through ``run_action_host``.
- Fleet runs (host_id NULL AND group_id NULL when scheduled_action_id
  is set) resolve to every host in the inventory.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from unittest.mock import patch

import pytest

from app.models.action_run import ActionHostRun, ActionRun
from app.models.scheduled_action import ScheduledAction
from app.tasks.action_orchestrator import (
    _DEFAULT_PER_HOST_TASK,
    PER_HOST_TASK_FOR_BUILTIN,
    _run_action_async,
)
from tests.conftest import create_host

pytestmark = pytest.mark.integration


@pytest.fixture(autouse=True)
def patch_task_session(db):
    """Make the orchestrator's task_session() yield the test session
    so writes made via the API fixture are visible to it."""

    @asynccontextmanager
    async def _fake():
        yield db

    with patch("app.db.task_session", new=_fake):
        yield


def test_builtin_routing_table_complete():
    """Every _builtin.* registered key has a dispatcher mapping."""
    expected = {
        "_builtin.sync",
        "_builtin.drift_check",
        "_builtin.collect_state",
    }
    assert set(PER_HOST_TASK_FOR_BUILTIN.keys()) == expected
    for task_name in PER_HOST_TASK_FOR_BUILTIN.values():
        assert task_name.startswith("app.tasks.builtin_dispatchers.")


class _FakeRedis:
    def exists(self, *_args):
        return 0  # never cancelled

    def publish(self, *_args, **_kwargs):
        return None


@pytest.fixture
def stub_celery_dispatch():
    with (
        patch("celery.app.base.Celery.send_task"),
        patch("redis.from_url", return_value=_FakeRedis()),
    ):
        yield


async def test_builtin_action_routes_to_dispatcher(superuser_client, db, stub_celery_dispatch):
    """When action_key=_builtin.collect_state, the per-host signature
    uses run_builtin_collect_state, not run_action_host."""
    host = await create_host(db)
    await db.commit()

    captured: list[str] = []

    def signature(name, args=None, queue=None, **_):
        captured.append(name)
        return ("sig", name, args)

    class _Result:
        def join(self, *args, **kwargs):  # noqa: ARG002
            return []

    def _group(sig_iter):
        # Force the lazy generator so each celery_app.signature(...)
        # call lands in the captured list.
        list(sig_iter)

        class _G:
            def apply_async(self):
                return _Result()

        return _G()

    # Create the run via API so it's persisted in the same DB session.
    resp = await superuser_client.post(
        "/api/actions/runs",
        json={"action_key": "_builtin.collect_state", "host_id": host.id},
    )
    assert resp.status_code == 201
    run_id = resp.json()["id"]

    with (
        patch("app.tasks.action_orchestrator.celery_app.signature", side_effect=signature),
        patch("celery.group", side_effect=_group),
    ):
        await _run_action_async(run_id)

    assert "app.tasks.builtin_dispatchers.run_builtin_collect_state" in captured
    assert _DEFAULT_PER_HOST_TASK not in captured


async def test_pack_action_routes_to_default_runner(superuser_client, db, stub_celery_dispatch):
    """linux-upgrade is pack-supplied → default per-host task."""
    host = await create_host(db)
    await db.commit()

    captured: list[str] = []

    def signature(name, args=None, queue=None, **_):
        captured.append(name)
        return ("sig", name, args)

    class _Result:
        def join(self, *args, **kwargs):  # noqa: ARG002
            return []

    def _group(sig_iter):
        # Force the lazy generator so each celery_app.signature(...)
        # call lands in the captured list.
        list(sig_iter)

        class _G:
            def apply_async(self):
                return _Result()

        return _G()

    resp = await superuser_client.post(
        "/api/actions/runs",
        json={
            "action_key": "linux-upgrade",
            "host_id": host.id,
            "parameters": {},
        },
    )
    assert resp.status_code == 201
    run_id = resp.json()["id"]

    with (
        patch("app.tasks.action_orchestrator.celery_app.signature", side_effect=signature),
        patch("celery.group", side_effect=_group),
    ):
        await _run_action_async(run_id)

    assert _DEFAULT_PER_HOST_TASK in captured


async def test_fleet_resolves_to_all_hosts(db, stub_celery_dispatch):
    """A scheduled fleet run (both target IDs NULL, scheduled_action_id
    set) resolves to every host in the inventory."""
    h1 = await create_host(db, hostname="fleet-h1")
    h2 = await create_host(db, hostname="fleet-h2")
    h3 = await create_host(db, hostname="fleet-h3")

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
        parallelism=10,
        status="queued",
    )
    db.add(run)
    await db.flush()
    await db.commit()
    run_id = run.id

    captured: list[tuple] = []

    def signature(name, args=None, queue=None, **_):
        captured.append((name, args))
        return ("sig", name, args)

    class _Result:
        def join(self, *args, **kwargs):  # noqa: ARG002
            return []

    def _group(sig_iter):
        # Force the lazy generator so each celery_app.signature(...)
        # call lands in the captured list.
        list(sig_iter)

        class _G:
            def apply_async(self):
                return _Result()

        return _G()

    with (
        patch("app.tasks.action_orchestrator.celery_app.signature", side_effect=signature),
        patch("celery.group", side_effect=_group),
    ):
        await _run_action_async(run_id)

    # One signature per host_run; 3 hosts × 1 signature each.
    assert len(captured) == 3
    # Verify ActionHostRun rows were persisted, one per host.
    from sqlalchemy import select

    host_runs = (
        (await db.execute(select(ActionHostRun).where(ActionHostRun.action_run_id == run_id)))
        .scalars()
        .all()
    )
    host_ids = {hr.host_id for hr in host_runs}
    assert host_ids == {h1.id, h2.id, h3.id}
