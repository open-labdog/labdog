"""BUG-105: a worker's registry follows the database.

Celery forks its pool before the worker's boot rebuild, and a pool
process imports the registry module itself, so its registry held only
what shipped in the image (then the bundled pack, now the built-ins). It
rebuilt only when a lookup missed, and two lookups never rebuilt at all.
On lin-manager the ``labdog-private`` compose schedule went out 16
minutes, 18 minutes and two hours late on three of five nights, each
after a restart. Nothing told a worker about a pin or a pack sync made
through the API either.

Each test starts from what a freshly forked pool process holds: the
built-ins, never built from the database.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy import select, text, update

from app.actions import registry
from app.actions.registry import ACTION_REGISTRY, ensure_registry_current, reload_registry_async
from app.models.action_run import ActionHostRun, ActionRun
from app.models.scheduled_action import ScheduledAction
from app.packs.models import ActionPack, ActionResolution, PackSourceType
from tests.conftest import create_group, create_host

pytestmark = pytest.mark.integration


def _make_pack(root: Path, key: str, **manifest) -> Path:
    action_dir = root / "actions" / key
    action_dir.mkdir(parents=True)
    (action_dir / "playbook.yml").write_text(f"---\n- name: {key}\n  hosts: all\n  tasks: []\n")
    extra = "".join(
        f"{k}: {str(v).lower() if isinstance(v, bool) else v}\n" for k, v in manifest.items()
    )
    (action_dir / "manifest.yml").write_text(
        f"key: {key}\n"
        f"name: {key}\n"
        "description: demo\n"
        "icon: Box\n"
        "playbook: playbook.yml\n"
        'version: "1.0"\n'
        'estimated_duration: "1 min"\n' + extra
    )
    return root


def _local_pack(name: str, path: Path) -> ActionPack:
    return ActionPack(
        name=name, source_type=PackSourceType.LOCAL, local_path=str(path), enabled=True
    )


@pytest.fixture(autouse=True)
def as_a_fresh_pool_process():
    """Start from the import-time registry, and put the real one back after."""
    saved = dict(ACTION_REGISTRY)
    saved_contributors = dict(registry.ACTION_REGISTRY_CONTRIBUTORS)
    saved_built_from = registry._BUILT_FROM
    registry._load_builtins_only()
    registry._BUILT_FROM = None
    yield
    ACTION_REGISTRY.clear()
    ACTION_REGISTRY.update(saved)
    registry.ACTION_REGISTRY_CONTRIBUTORS.clear()
    registry.ACTION_REGISTRY_CONTRIBUTORS.update(saved_contributors)
    registry._BUILT_FROM = saved_built_from


@pytest.fixture(autouse=True)
def patch_task_session(db):
    @asynccontextmanager
    async def _fake():
        yield db

    with (
        patch("app.db.task_session", new=_fake),
        patch("app.tasks.action_sweeper.task_session", new=_fake),
    ):
        yield


@pytest.fixture(autouse=True)
def fixed_setting():
    """``ansible.playbook_timeout`` pinned at 1800s, so deadlines are known."""
    with patch("app.settings_service.get_setting_cached_typed", return_value=1800):
        yield


# ---------------------------------------------------------------------------
# A pool process that never rebuilt
# ---------------------------------------------------------------------------


class TestAPoolProcessThatNeverRebuilt:
    async def test_the_scheduler_dispatches_a_git_pack_schedule(self, db, tmp_path):
        from app.tasks.scheduled_action_schedule import _check_due_async

        db.add(_local_pack("private", _make_pack(tmp_path / "p", "compose-update")))
        host = await create_host(db)
        db.add(
            ScheduledAction(
                target_kind="host",
                target_id=host.id,
                action_key="compose-update",
                schedule_cron="* * * * *",
                enabled=True,
                last_dispatched_at=datetime.now(UTC) - timedelta(minutes=2),
            )
        )
        await db.commit()
        assert "compose-update" not in ACTION_REGISTRY

        with patch("celery.app.base.Celery.send_task") as send:
            result = await _check_due_async()

        assert result["dispatched"] == 1, result
        assert send.call_args.args[0] == "app.tasks.action_orchestrator.run_action"

    async def test_the_orchestrator_takes_the_dispatch_shape_from_the_pack(self, db, tmp_path):
        """A ``supports_host: false`` action a pack supplies. From the
        built-ins alone the orchestrator could not see that."""
        from app.tasks.action_orchestrator import _run_action_async

        db.add(
            _local_pack(
                "cluster-pack",
                _make_pack(tmp_path / "p", "cluster-upgrade", supports_host=False),
            )
        )
        group = await create_group(db)
        await create_host(db, group_ids=[group.id])
        run = ActionRun(
            action_key="cluster-upgrade",
            action_version="1.0",
            group_id=group.id,
            parameters={},
            parallelism=1,
            status="queued",
        )
        db.add(run)
        await db.commit()

        sent: list[str] = []
        redis = MagicMock()
        redis.exists.return_value = 0
        with (
            patch(
                "app.tasks.action_orchestrator.celery_app.send_task",
                side_effect=lambda name, **_kw: sent.append(name),
            ),
            patch("app.tasks.action_orchestrator._wait", new=AsyncMock(return_value="go")),
            patch("redis.from_url", return_value=redis),
        ):
            await _run_action_async(run.id)

        assert sent == ["app.tasks.action_group.run_action_group"]

    async def test_the_sweeper_reads_the_actions_own_deadline(self, db, tmp_path):
        """With the global 1800s timeout the per-host deadline is 3000s; this
        action declares 7200s of playbook. From the built-ins alone the
        sweeper used the global one, and failed the host while it was still
        running."""
        from app.tasks.action_sweeper import _sweep_stale_action_runs_async

        db.add(
            _local_pack(
                "slow-pack",
                _make_pack(tmp_path / "p", "slow-action", playbook_timeout_seconds=7200),
            )
        )
        host = await create_host(db)
        run = ActionRun(
            action_key="slow-action",
            action_version="1.0",
            host_id=host.id,
            parameters={},
            parallelism=1,
            status="running",
            started_at=datetime.now(UTC) - timedelta(seconds=4000),
        )
        db.add(run)
        await db.flush()
        host_run = ActionHostRun(
            action_run_id=run.id,
            host_id=host.id,
            status="running",
            started_at=datetime.now(UTC) - timedelta(seconds=4000),
        )
        db.add(host_run)
        await db.commit()
        host_run_id = host_run.id

        with (
            patch("app.tasks.action_orchestrator.run_action.delay"),
            patch("app.tasks.host_sync_orchestrator.run_host_sync.delay"),
        ):
            result = await _sweep_stale_action_runs_async()

        assert host_run_id not in result["host_runs_swept"]
        db.expire_all()
        row = (
            await db.execute(select(ActionHostRun).where(ActionHostRun.id == host_run_id))
        ).scalar_one()
        assert row.status == "running"


# ---------------------------------------------------------------------------
# Changes made in another process
# ---------------------------------------------------------------------------


class TestChangesMadeElsewhereReachThisProcess:
    async def test_a_pin_made_through_the_api(self, db, tmp_path):
        a = _local_pack("pack-a", _make_pack(tmp_path / "a", "hello"))
        b = _local_pack("pack-b", _make_pack(tmp_path / "b", "hello"))
        db.add_all([a, b])
        await db.flush()
        db.add(ActionResolution(action_key="hello", pack_id=a.id, decided_by_user_id=None))
        await db.commit()
        await reload_registry_async(db)
        assert ACTION_REGISTRY["hello"].pack_name == "pack-a"

        # What the API commits when the operator picks the other pack. This
        # process is not told.
        await db.execute(
            update(ActionResolution)
            .where(ActionResolution.action_key == "hello")
            .values(pack_id=b.id)
        )
        await db.commit()
        assert ACTION_REGISTRY["hello"].pack_name == "pack-a"

        await ensure_registry_current(db)

        assert ACTION_REGISTRY["hello"].pack_name == "pack-b"

    async def test_a_pack_sync(self, db, tmp_path):
        root = _make_pack(tmp_path / "p", "first")
        pack = _local_pack("syncing-pack", root)
        db.add(pack)
        await db.commit()
        await reload_registry_async(db)
        assert "second" not in ACTION_REGISTRY

        # A sync brings in a new action and stamps the pack.
        _make_pack(root, "second")
        pack.last_synced_at = datetime.now(UTC)
        await db.commit()

        await ensure_registry_current(db)

        assert "second" in ACTION_REGISTRY

    async def test_nothing_changed_nothing_rebuilt(self, db, tmp_path):
        db.add(_local_pack("still-pack", _make_pack(tmp_path / "p", "still")))
        await db.commit()
        await reload_registry_async(db)

        with patch.object(registry, "reload_registry_async", new=AsyncMock()) as rebuild:
            await ensure_registry_current(db)

        rebuild.assert_not_called()

    async def test_a_failed_rebuild_keeps_the_registry_and_the_session(self, db):
        before = dict(ACTION_REGISTRY)

        with patch.object(
            registry, "reload_registry_async", new=AsyncMock(side_effect=RuntimeError("db gone"))
        ):
            await ensure_registry_current(db)

        assert ACTION_REGISTRY == before
        assert await db.scalar(text("SELECT 1")) == 1
