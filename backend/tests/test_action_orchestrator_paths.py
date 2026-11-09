"""Characterisation tests for orchestrator start-up paths the suite leaves dark.

The routing, resume and locking tests drive ``_run_action_async`` through
its main line. These pin the exits around it: a missing or no-longer-queued
run, the registry reload on a miss, an unknown key, a target with no hosts,
a failing dispatch-shape probe, a failing initialisation, and the group
task's limits for an empty group, so a restructuring of
``app.tasks.action_orchestrator`` has something to answer to.
"""

from __future__ import annotations

import dataclasses
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import select

from app.actions.registry import ACTION_REGISTRY
from app.models.action_run import ActionHostRun, ActionRun
from app.tasks.action_orchestrator import _DEFAULT_PER_HOST_TASK, _run_action_async
from tests.conftest import create_group, create_host

pytestmark = [pytest.mark.integration, pytest.mark.usefixtures("sample_pack")]


@pytest.fixture(autouse=True)
def patch_task_session(db):
    @asynccontextmanager
    async def _fake():
        yield db

    with patch("app.db.task_session", new=_fake):
        yield


class _FakeRedis:
    def exists(self, *_args):
        return 0

    def publish(self, *_args, **_kwargs):
        return None


@pytest.fixture
def sent():
    calls: list[tuple[str, list, dict]] = []

    def _send_task(name, args=None, **kwargs):
        calls.append((name, args or [], kwargs))

    with (
        patch("app.tasks.action_orchestrator.celery_app.send_task", side_effect=_send_task),
        patch("app.tasks.action_orchestrator._wait", new=AsyncMock(return_value=("go", {}))),
        patch("redis.from_url", return_value=_FakeRedis()),
    ):
        yield calls


async def _run_row(db, *, action_key="linux-upgrade", status="queued", **target) -> int:
    run = ActionRun(
        action_key=action_key,
        action_version="1.0",
        parameters={},
        parallelism=1,
        status=status,
        **target,
    )
    db.add(run)
    await db.flush()
    await db.commit()
    return run.id


async def _reload(db, run_id) -> ActionRun:
    run = (await db.execute(select(ActionRun).where(ActionRun.id == run_id))).scalar_one()
    await db.refresh(run)
    return run


async def _host_rows(db, run_id) -> list[ActionHostRun]:
    return list(
        (await db.execute(select(ActionHostRun).where(ActionHostRun.action_run_id == run_id)))
        .scalars()
        .all()
    )


async def test_a_missing_run_is_left_alone(db, sent):
    await _run_action_async(987654)
    assert sent == []


@pytest.mark.parametrize("status", ["cancelled", "running", "failed"])
async def test_a_run_that_is_no_longer_queued_is_not_started(db, sent, status):
    host = await create_host(db)
    run_id = await _run_row(db, host_id=host.id, status=status)

    await _run_action_async(run_id)

    assert sent == []
    assert (await _reload(db, run_id)).status == status
    assert await _host_rows(db, run_id) == []


async def test_an_unknown_key_is_reloaded_once_then_fails_the_run(db, sent):
    host = await create_host(db)
    run_id = await _run_row(db, host_id=host.id, action_key="no-such-action")

    reload = AsyncMock(return_value={})
    with patch("app.actions.registry.reload_registry_async", reload):
        await _run_action_async(run_id, orchestrator_id="orch-1")

    reload.assert_awaited_once()
    run = await _reload(db, run_id)
    assert run.status == "failed"
    assert run.error_message == "Unknown action key: no-such-action"
    assert run.finished_at is not None
    assert run.started_at is not None
    assert run.orchestrator_id is None
    assert run.heartbeat_at is None
    assert await _host_rows(db, run_id) == []
    assert sent == []


async def test_a_key_the_reload_finds_is_run(db, sent):
    host = await create_host(db, hostname="reload-me.test")
    run_id = await _run_row(db, host_id=host.id, action_key="late-action")
    late = dataclasses.replace(ACTION_REGISTRY["linux-upgrade"], key="late-action")

    async def _reload_registry(_db):
        ACTION_REGISTRY["late-action"] = late
        return {}

    with (
        patch.dict(ACTION_REGISTRY),
        patch("app.actions.registry.reload_registry_async", side_effect=_reload_registry),
    ):
        await _run_action_async(run_id)

    rows = await _host_rows(db, run_id)
    assert [r.hostname for r in rows] == ["reload-me.test"]
    assert [(name, args) for name, args, _ in sent] == [
        (_DEFAULT_PER_HOST_TASK, [run_id, rows[0].id])
    ]


async def test_a_group_with_no_members_succeeds_with_nothing_to_do(db, sent):
    group = await create_group(db)
    run_id = await _run_row(db, group_id=group.id)

    await _run_action_async(run_id, orchestrator_id="orch-2")

    run = await _reload(db, run_id)
    assert run.status == "succeeded"
    assert run.finished_at is not None
    assert run.orchestrator_id is None
    assert run.heartbeat_at is None
    assert sent == []


async def test_a_failed_dispatch_shape_probe_falls_back_to_the_per_host_path(db, sent, caplog):
    host = await create_host(db)
    run_id = await _run_row(db, host_id=host.id)

    with patch(
        "app.actions.registry.ensure_registry_current",
        new=AsyncMock(side_effect=RuntimeError("registry offline")),
    ):
        await _run_action_async(run_id)

    assert "dispatch-shape probe failed for action_run" in caplog.text
    assert [name for name, _, _ in sent] == [_DEFAULT_PER_HOST_TASK]


async def test_a_failed_initialisation_fails_the_run_and_lets_it_go(db, sent):
    host = await create_host(db)
    run_id = await _run_row(db, host_id=host.id)

    with patch(
        "app.tasks.action_timeouts.per_host_deadline_seconds",
        side_effect=RuntimeError("deadline table broken"),
    ):
        await _run_action_async(run_id, orchestrator_id="orch-3")

    run = await _reload(db, run_id)
    assert run.status == "failed"
    assert run.error_message == "deadline table broken"
    assert run.orchestrator_id is None
    assert run.heartbeat_at is None
    assert sent == []


async def test_group_dispatch_of_an_empty_group_sizes_limits_for_one_member(db, sent):
    from app.tasks.action_timeouts import HARD_LIMIT_MARGIN_SECONDS, run_deadline_seconds

    group = await create_group(db)
    run_id = await _run_row(db, group_id=group.id, action_key="k8s-upgrade")

    await _run_action_async(run_id)

    soft = run_deadline_seconds("k8s-upgrade", 1, parallelism=1)
    assert sent == [
        (
            "app.tasks.action_group.run_action_group",
            [run_id],
            {
                "queue": "actions",
                "soft_time_limit": soft,
                "time_limit": soft + HARD_LIMIT_MARGIN_SECONDS,
            },
        )
    ]
    assert (await _reload(db, run_id)).status == "queued"
