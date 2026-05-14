"""Tests for the group-dispatch action executor.

Covers the ``supports_host: false`` dispatch shape: the orchestrator
hands off to ``app.tasks.action_group.run_action_group`` and the
group task runs ONE ansible-runner invocation against a flat
``all.hosts`` inventory containing every member of the target group.

Validation:

- Orchestrator routes group-target + ``supports_host=False`` to the
  group task (not the per-host fan-out).
- Group task creates one ``ActionHostRun`` row per group member,
  anchored to the actual host (no "driver" anchor).
- Inventory passed to ansible-runner is flat — ``all.hosts`` only,
  no ``children`` / ``control_plane`` / ``workers``.
- Exactly one ansible-runner invocation drives all hosts.
- POST /api/actions/runs rejects host-target submissions for
  ``supports_host=False`` actions with a 400 + clear message.
"""

from __future__ import annotations

import json
from contextlib import asynccontextmanager
from unittest.mock import MagicMock, patch

import pytest

from app.actions.registry import ACTION_REGISTRY
from app.models.action_run import ActionHostRun, ActionRun
from app.tasks.action_orchestrator import _run_action_async
from tests.conftest import create_group, create_host, create_ssh_key

pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
# Common stubs
# ---------------------------------------------------------------------------


class _FakeRedis:
    def __init__(self):
        self.published: list[tuple[str, str]] = []

    def exists(self, *_args):
        return 0  # never cancelled

    def publish(self, channel, payload):
        self.published.append((channel, payload))
        return None

    def setex(self, *_args, **_kwargs):
        return None


@pytest.fixture(autouse=True)
def patch_task_session(db):
    """Make the orchestrator/group-task ``task_session()`` yield the
    test session so writes made via the API or fixture helpers are
    visible inside the task."""

    @asynccontextmanager
    async def _fake():
        yield db

    with patch("app.db.task_session", new=_fake):
        yield


@pytest.fixture
def fake_redis():
    fr = _FakeRedis()
    with patch("redis.from_url", return_value=fr):
        yield fr


def _fake_runner(*, host_names: list[str], status: str = "successful", rc: int = 0):
    """Build a stand-in ansible-runner ``Runner`` object exposing the
    attributes the group task reads: ``stdout``, ``status``, ``rc``,
    ``events``."""
    runner = MagicMock()
    runner.stdout = "PLAY RECAP\n" + "\n".join(f"{h} : ok=1" for h in host_names)
    runner.status = status
    runner.rc = rc
    # No failure events → all hosts succeeded.
    runner.events = [
        {"event": "runner_on_ok", "event_data": {"host": h}} for h in host_names
    ]
    return runner


# ---------------------------------------------------------------------------
# Orchestrator routing
# ---------------------------------------------------------------------------


async def test_orchestrator_routes_group_target_with_supports_host_false(db, fake_redis):
    """When the action has ``supports_host=False`` AND the run targets a
    group, the orchestrator must hand off to the group dispatch task
    and NOT fan out per-host."""
    # Use the bundled k8s-upgrade action — it's the canonical
    # ``supports_host: false`` action in the registry.
    assert "k8s-upgrade" in ACTION_REGISTRY
    assert ACTION_REGISTRY["k8s-upgrade"].supports_host is False
    assert ACTION_REGISTRY["k8s-upgrade"].supports_group is True

    key = await create_ssh_key(db)
    group = await create_group(db)
    await create_host(db, ssh_key_id=key.id, group_ids=[group.id])
    await create_host(db, ssh_key_id=key.id, group_ids=[group.id])
    await db.flush()

    run = ActionRun(
        action_key="k8s-upgrade",
        action_version="1.0",
        host_id=None,
        group_id=group.id,
        parameters={"target_version": "1.30.4"},
        parallelism=1,
        status="queued",
    )
    db.add(run)
    await db.flush()
    await db.commit()
    run_id = run.id

    sent: list[tuple[str, list]] = []

    def _send_task(name, args=None, queue=None, **_):
        sent.append((name, args or []))
        return MagicMock()

    # Stub send_task so we observe the handoff without dispatching.
    # Also stub celery group/signature so any per-host fall-through would
    # surface in the captured list (it shouldn't).
    captured_per_host: list[str] = []

    def signature(name, args=None, queue=None, **_):
        captured_per_host.append(name)
        return ("sig", name, args)

    with (
        patch(
            "app.tasks.action_orchestrator.celery_app.send_task",
            side_effect=_send_task,
        ),
        patch(
            "app.tasks.action_orchestrator.celery_app.signature",
            side_effect=signature,
        ),
    ):
        await _run_action_async(run_id)

    # Group dispatch task was triggered exactly once with our run id.
    assert ("app.tasks.action_group.run_action_group", [run_id]) in sent
    # No per-host signatures were built.
    assert captured_per_host == []


# ---------------------------------------------------------------------------
# Group dispatch task
# ---------------------------------------------------------------------------


async def test_group_task_creates_one_host_run_per_member(db, fake_redis):
    """The group task creates exactly N ActionHostRun rows (N = group
    members), each anchored to the real host — no driver anchor."""
    key = await create_ssh_key(db)
    group = await create_group(db)
    h1 = await create_host(db, hostname="cl-h1", ssh_key_id=key.id, group_ids=[group.id])
    h2 = await create_host(db, hostname="cl-h2", ssh_key_id=key.id, group_ids=[group.id])
    h3 = await create_host(db, hostname="cl-h3", ssh_key_id=key.id, group_ids=[group.id])
    await db.flush()

    run = ActionRun(
        action_key="k8s-upgrade",
        action_version="1.0",
        host_id=None,
        group_id=group.id,
        parameters={"target_version": "1.30.4"},
        parallelism=1,
        status="queued",
    )
    db.add(run)
    await db.flush()
    await db.commit()
    run_id = run.id

    fake_runner = _fake_runner(host_names=["cl-h1", "cl-h2", "cl-h3"])
    inventories_seen: list[dict] = []
    invocation_count = 0

    def _capture_run(*, playbook_path, inventory_json, **_kwargs):  # noqa: ARG001
        nonlocal invocation_count
        invocation_count += 1
        inventories_seen.append(json.loads(inventory_json))
        return fake_runner

    from app.tasks.action_group import _run_action_group_async

    with patch("app.ansible_runtime.runner.run_ansible", side_effect=_capture_run):
        await _run_action_group_async(run_id)

    # Exactly one ansible-runner invocation drove all hosts.
    assert invocation_count == 1

    # Inventory shape is flat: top-level "all.hosts" keyed by hostname,
    # NO children / control_plane / workers.
    assert len(inventories_seen) == 1
    inv = inventories_seen[0]
    assert "all" in inv
    assert "hosts" in inv["all"]
    assert "children" not in inv["all"], (
        "group dispatch must use a flat inventory; cluster-mode role "
        "grouping was deliberately removed"
    )
    inv_host_keys = set(inv["all"]["hosts"].keys())
    assert inv_host_keys == {"cl-h1", "cl-h2", "cl-h3"}

    # One ActionHostRun row per real host (no extra driver row).
    from sqlalchemy import select

    host_runs = (
        (await db.execute(select(ActionHostRun).where(ActionHostRun.action_run_id == run_id)))
        .scalars()
        .all()
    )
    assert len(host_runs) == 3
    anchored_host_ids = {hr.host_id for hr in host_runs}
    assert anchored_host_ids == {h1.id, h2.id, h3.id}

    # Run-level outcome is succeeded (no failure events from the fake runner).
    run_after = await db.get(ActionRun, run_id)
    assert run_after.status == "succeeded"
    for hr in host_runs:
        assert hr.status == "succeeded"


async def test_group_task_routes_per_host_failures_from_events(db, fake_redis):
    """Per-host failure events from ansible-runner are routed back to
    the matching ActionHostRun row, leaving a partial run."""
    key = await create_ssh_key(db)
    group = await create_group(db)
    h1 = await create_host(db, hostname="part-h1", ssh_key_id=key.id, group_ids=[group.id])
    h2 = await create_host(db, hostname="part-h2", ssh_key_id=key.id, group_ids=[group.id])
    await db.flush()

    run = ActionRun(
        action_key="k8s-upgrade",
        action_version="1.0",
        host_id=None,
        group_id=group.id,
        parameters={"target_version": "1.30.4"},
        parallelism=1,
        status="queued",
    )
    db.add(run)
    await db.flush()
    await db.commit()
    run_id = run.id

    # Build a runner where part-h1 succeeded but part-h2 failed.
    runner = MagicMock()
    runner.stdout = "PLAY RECAP\npart-h1 : ok=1\npart-h2 : failed=1"
    runner.status = "failed"
    runner.rc = 2
    runner.events = [
        {"event": "runner_on_ok", "event_data": {"host": "part-h1"}},
        {
            "event": "runner_on_failed",
            "event_data": {"host": "part-h2", "res": {"msg": "drain timed out"}},
        },
    ]

    from app.tasks.action_group import _run_action_group_async

    with patch("app.ansible_runtime.runner.run_ansible", return_value=runner):
        await _run_action_group_async(run_id)

    from sqlalchemy import select

    host_runs = (
        (await db.execute(select(ActionHostRun).where(ActionHostRun.action_run_id == run_id)))
        .scalars()
        .all()
    )
    by_host = {hr.host_id: hr for hr in host_runs}
    assert by_host[h1.id].status == "succeeded"
    assert by_host[h2.id].status == "failed"
    assert "drain timed out" in (by_host[h2.id].error_message or "")

    run_after = await db.get(ActionRun, run_id)
    assert run_after.status == "partial"


# ---------------------------------------------------------------------------
# Submission validation (re: API rejects host-target for supports_host=False)
# ---------------------------------------------------------------------------


@pytest.fixture
def stub_celery_dispatch():
    """Block any Celery send_task during these tests."""
    with (
        patch("app.api.actions.celery_app", create=True),
        patch("celery.app.base.Celery.send_task"),
    ):
        yield


async def test_api_rejects_host_target_for_supports_host_false(
    superuser_client, db, stub_celery_dispatch
):
    """``POST /api/actions/runs`` with a host_id targeting an action that
    declared ``supports_host: false`` must be rejected with 400 and a
    clear "can only target a group" message — not silently dispatched."""
    host = await create_host(db)
    await db.commit()

    resp = await superuser_client.post(
        "/api/actions/runs",
        json={
            "action_key": "k8s-upgrade",
            "host_id": host.id,
            "parameters": {"target_version": "1.30.4"},
        },
    )
    assert resp.status_code == 400, resp.text
    body = resp.json()
    assert "group" in body["detail"].lower()
