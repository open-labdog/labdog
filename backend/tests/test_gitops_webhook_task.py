"""Characterisation tests for the GitOps webhook task body.

``_process_webhook_async`` had no test reaching it: ``test_gitops_pipeline``
only checks the Celery task's options. These drive it with the git service,
the importers and the sync dispatch stubbed, and pin each exit and what it
writes, so a restructuring of ``app.tasks.gitops`` has something to answer to.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy import select

from app.models.git_repository import GitAuthType, GitOpsStatus, GitRepository
from app.models.host_group import HostGroup
from app.tasks.gitops import _process_webhook_async
from tests.conftest import create_group, create_ssh_key

pytestmark = pytest.mark.integration

TIP = "f" * 40


@pytest.fixture(autouse=True)
def patch_task_session(db):
    @asynccontextmanager
    async def _fake():
        yield db

    with (
        patch("app.db.task_session", new=_fake),
        patch("app.actions.registry.ensure_registry_current", new=AsyncMock()),
    ):
        yield


def _import_result(*, success=True, changes=False, error=None, modules=()):
    return SimpleNamespace(
        success=success,
        error_message=error,
        any_changes=lambda: changes,
        modules=list(modules),
    )


class _World:
    """The stubbed git service, importers and dispatch for one run."""

    def __init__(self, tmp_path: Path, files: dict[str, str] | None = None, tip: str = TIP):
        self.repo_dir = tmp_path / "clone"
        self.files = files or {}
        self.git_repo = SimpleNamespace(head=SimpleNamespace(commit=SimpleNamespace(hexsha=tip)))
        self.clone = MagicMock(return_value=(self.git_repo, self.repo_dir))
        self.cleanup = MagicMock()
        self.read_shas: list[tuple[str, str]] = []
        self.import_global = AsyncMock(return_value=_import_result())
        self.import_group = AsyncMock(return_value=_import_result())
        self.trigger = AsyncMock(return_value=[])
        self.delay = MagicMock()

    def _read(self, repo_dir, path, sha):
        assert repo_dir == self.repo_dir
        self.read_shas.append((path, sha))
        if path not in self.files:
            raise FileNotFoundError(path)
        return self.files[path]

    @asynccontextmanager
    async def active(self):
        with (
            patch("app.gitops.git_service.clone_repo", self.clone),
            patch("app.gitops.git_service.cleanup_repo", self.cleanup),
            patch("app.gitops.git_service.read_file_at_sha", side_effect=self._read),
            patch("app.gitops.importer.import_global_from_yaml", self.import_global),
            patch("app.gitops.importer.import_group_from_yaml", self.import_group),
            patch("app.tasks.gitops._trigger_group_sync", self.trigger),
            patch("app.tasks.host_sync_orchestrator.run_host_sync.delay", self.delay),
        ):
            yield


async def _repo(db, **kw) -> GitRepository:
    repo = GitRepository(
        name=kw.pop("name", "gitops-task-repo"),
        url="https://git.example/repo.git",
        branch="main",
        auth_type=kw.pop("auth_type", GitAuthType.none),
        **kw,
    )
    db.add(repo)
    await db.flush()
    await db.commit()
    return repo


async def _bound_group(db, repo_id: int, path: str, name: str) -> HostGroup:
    group = await create_group(db, name=name)
    group.git_repository_id = repo_id
    group.gitops_enabled = True
    group.gitops_file_path = path
    await db.flush()
    await db.commit()
    return group


async def _fresh(db, model, row_id):
    row = (await db.execute(select(model).where(model.id == row_id))).scalar_one()
    await db.refresh(row)
    return row


async def test_a_missing_repo_does_nothing(db, tmp_path):
    world = _World(tmp_path)
    async with world.active():
        await _process_webhook_async(MagicMock(), 987654, "a" * 40)
    world.clone.assert_not_called()
    world.cleanup.assert_not_called()


async def test_a_sha_already_processed_is_skipped_before_cloning(db, tmp_path):
    repo = await _repo(db, last_commit_sha="a" * 40)
    world = _World(tmp_path)
    async with world.active():
        await _process_webhook_async(MagicMock(), repo.id, "a" * 40)
    world.clone.assert_not_called()


async def test_a_clone_failure_asks_celery_to_retry(db, tmp_path):
    repo = await _repo(db)
    world = _World(tmp_path)
    world.clone.side_effect = OSError("network down")
    task = MagicMock()
    task.retry.return_value = RuntimeError("retry scheduled")

    async with world.active():
        with pytest.raises(RuntimeError, match="retry scheduled"):
            await _process_webhook_async(task, repo.id, "a" * 40)

    assert isinstance(task.retry.call_args.kwargs["exc"], OSError)
    world.cleanup.assert_not_called()


async def test_an_ssh_repo_clones_with_its_encrypted_key(db, tmp_path):
    key = await create_ssh_key(db)
    repo = await _repo(db, auth_type=GitAuthType.ssh_key, ssh_key_id=key.id)
    world = _World(tmp_path)
    async with world.active():
        await _process_webhook_async(MagicMock(), repo.id, TIP)
    assert world.clone.call_args.kwargs == {"encrypted_ssh_key": key.encrypted_private_key}
    assert world.clone.call_args.args[0].id == repo.id


async def test_the_branch_tip_is_imported_and_a_processed_tip_is_skipped(db, tmp_path, caplog):
    repo = await _repo(db, last_commit_sha=TIP)
    world = _World(tmp_path)

    with caplog.at_level(logging.INFO, logger="app.tasks.gitops"):
        async with world.active():
            await _process_webhook_async(MagicMock(), repo.id, "b" * 40)

    assert f"named {'b' * 8}; importing the branch tip {TIP[:8]}" in caplog.text
    assert f"branch tip {TIP[:8]} already processed" in caplog.text
    world.import_global.assert_not_awaited()
    world.cleanup.assert_called_once_with(world.repo_dir)


async def test_with_no_bound_groups_the_repo_is_marked_processed(db, tmp_path):
    repo = await _repo(db)
    world = _World(tmp_path)
    async with world.active():
        await _process_webhook_async(MagicMock(), repo.id, "c" * 40)

    fresh = await _fresh(db, GitRepository, repo.id)
    assert fresh.last_commit_sha == TIP
    assert fresh.last_sync_at is not None
    assert world.read_shas == [("_global.yaml", TIP)]
    world.cleanup.assert_called_once_with(world.repo_dir)


@pytest.mark.parametrize("outcome", ["ok", "failed", "raises"])
async def test_global_yaml_problems_never_stop_the_run(db, tmp_path, caplog, outcome):
    repo = await _repo(db)
    world = _World(tmp_path, files={"_global.yaml": "x: 1\n"})
    if outcome == "failed":
        world.import_global.return_value = _import_result(success=False, error="bad key")
    if outcome == "raises":
        world.import_global.side_effect = ValueError("yaml exploded")

    with caplog.at_level(logging.INFO, logger="app.tasks.gitops"):
        async with world.active():
            await _process_webhook_async(MagicMock(), repo.id, TIP)

    kwargs = world.import_global.await_args.kwargs
    assert (kwargs["repo_id"], kwargs["yaml_content"], kwargs["commit_sha"]) == (
        repo.id,
        "x: 1\n",
        TIP,
    )
    expected = {
        "ok": "global import OK for repo gitops-task-repo (changes=False)",
        "failed": "global import failed for repo gitops-task-repo: bad key",
        "raises": "error importing _global.yaml for repo gitops-task-repo: yaml exploded",
    }[outcome]
    assert expected in caplog.text
    assert (await _fresh(db, GitRepository, repo.id)).last_commit_sha == TIP


async def test_each_group_is_imported_on_its_own_and_syncs_go_out_after_the_commit(
    db, tmp_path, caplog
):
    repo = await _repo(db)
    missing = await _bound_group(db, repo.id, "groups/missing.yaml", "gt-missing")
    failing = await _bound_group(db, repo.id, "groups/failing.yaml", "gt-failing")
    changed = await _bound_group(db, repo.id, "groups/changed.yaml", "gt-changed")
    broken = await _bound_group(db, repo.id, "groups/broken.yaml", "gt-broken")
    unchanged = await _bound_group(db, repo.id, "groups/same.yaml", "gt-same")
    world = _World(
        tmp_path,
        files={
            "groups/failing.yaml": "f",
            "groups/changed.yaml": "c",
            "groups/broken.yaml": "b",
            "groups/same.yaml": "s",
        },
    )
    results = {
        failing.id: _import_result(success=False, error="schema says no"),
        changed.id: _import_result(
            changes=True,
            modules=[
                SimpleNamespace(module="users", changed=True),
                SimpleNamespace(module="firewall", changed=True),
                SimpleNamespace(module="drift", changed=True),
            ],
        ),
        unchanged.id: _import_result(changes=False),
    }

    async def _import_group(*, group_id, yaml_content, commit_sha, db):  # noqa: ARG001
        if group_id == broken.id:
            raise RuntimeError("importer crashed")
        return results[group_id]

    world.import_group.side_effect = _import_group
    world.trigger.return_value = [(11, 101, ["firewall", "linux-users"])]
    order: list[str] = []
    world.delay.side_effect = lambda **kw: order.append("delay")
    real_commit = db.commit

    async def _commit():
        order.append("commit")
        await real_commit()

    with (
        caplog.at_level(logging.INFO, logger="app.tasks.gitops"),
        patch.object(db, "commit", side_effect=_commit),
    ):
        async with world.active():
            await _process_webhook_async(MagicMock(), repo.id, TIP)

    world.trigger.assert_awaited_once_with(changed.id, db, ["linux-users", "firewall"])
    world.delay.assert_called_once_with(
        job_id=11, host_id=101, module_filter=["firewall", "linux-users"]
    )
    assert order[-2:] == ["commit", "delay"]

    m = await _fresh(db, HostGroup, missing.id)
    assert m.gitops_status == GitOpsStatus.error
    assert m.gitops_error_message == "File not found: groups/missing.yaml"
    b = await _fresh(db, HostGroup, broken.id)
    assert b.gitops_status == GitOpsStatus.error
    assert b.gitops_error_message == "importer crashed"
    assert "import failed for group gt-failing: schema says no" in caplog.text
    assert f"processed webhook for repo gitops-task-repo, SHA {TIP[:8]}, 5 groups" in caplog.text
    assert "dispatched sync job 11 for host 101" in caplog.text
    fresh = await _fresh(db, GitRepository, repo.id)
    assert fresh.last_commit_sha == TIP
    world.cleanup.assert_called_once_with(world.repo_dir)
