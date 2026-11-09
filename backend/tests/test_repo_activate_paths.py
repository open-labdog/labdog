"""Characterisation tests for repo-activation paths the main suite leaves dark.

``tests/test_repo_activate_api.py`` covers the happy path and the scan,
conflict and gitops-binding rejections. These pin what it does not reach
(per-key resolutions, the post-commit hooks, a clone or credential
failure), so a restructuring of ``app.api._repo_scan`` has something to
answer to.
"""

from __future__ import annotations

import logging
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import select

from app.actions.registry import ACTION_REGISTRY
from app.packs.models import ActionPack, ActionResolution, PackSourceType
from tests.test_repo_activate_api import _git, _make_pack_tree, _make_repo

pytestmark = pytest.mark.integration


@pytest.fixture
def origin(tmp_path: Path) -> Path:
    path = tmp_path / "origin-paths"
    path.mkdir()
    _make_pack_tree(path, "packs/foo", "foo-pack", "foo-action")
    _make_pack_tree(path, "packs/bar", "bar-pack", "bar-action")
    _git(path, ["init", "-b", "main"])
    _git(path, ["config", "user.email", "test@example.com"])
    _git(path, ["config", "user.name", "Test"])
    _git(path, ["add", "-A"])
    _git(path, ["commit", "-m", "initial"])
    return path


@pytest.fixture
def hooks():
    sync_pack = AsyncMock(return_value=True)
    reload = AsyncMock(return_value={})
    with (
        patch("app.packs.service.sync_pack", new=sync_pack),
        patch("app.actions.registry.reload_registry_async", new=reload),
    ):
        yield SimpleNamespace(sync_pack=sync_pack, reload=reload)


async def _existing_pack(db, name: str) -> int:
    pack = ActionPack(
        name=name, source_type=PackSourceType.LOCAL, local_path="/tmp/dummy", enabled=True
    )
    db.add(pack)
    await db.flush()
    return pack.id


def _owned(**owners: int):
    """Registry entries saying an installed pack already owns each key."""
    return patch.dict(
        ACTION_REGISTRY,
        {
            key.replace("_", "-"): SimpleNamespace(winning_pack_id=pack_id, pack_name="old")
            for key, pack_id in owners.items()
        },
    )


async def _activate(client, repo_id: int, **body):
    body.setdefault("gitops_bindings", [])
    return await client.post(f"/api/git-repos/{repo_id}/activate", json=body)


async def test_a_key_an_installed_pack_owns_needs_a_resolution(superuser_client, db, origin, hooks):
    owner_id = await _existing_pack(db, "owner-pack")
    repo = await _make_repo(db, f"file://{origin}")
    await db.commit()

    with _owned(foo_action=owner_id):
        resp = await _activate(
            superuser_client, repo.id, packs=[{"path": "packs/foo", "name": "foo-pack"}]
        )

    assert resp.status_code == 409
    detail = resp.json()["detail"]
    assert detail["kind"] == "unresolved_key_conflicts"
    assert detail["action_keys"] == ["foo-action"]
    assert detail["message"] == (
        "Activation introduces action-key collisions that have no operator "
        "decision. Submit a winner for each contested key in key_resolutions."
    )
    hooks.sync_pack.assert_not_awaited()


async def test_a_resolution_naming_both_winners_is_rejected(superuser_client, db, origin, hooks):
    owner_id = await _existing_pack(db, "owner-pack")
    repo = await _make_repo(db, f"file://{origin}")
    await db.commit()

    with _owned(foo_action=owner_id):
        resp = await _activate(
            superuser_client,
            repo.id,
            packs=[{"path": "packs/foo", "name": "foo-pack"}],
            key_resolutions=[
                {
                    "action_key": "foo-action",
                    "winner_pack_path": "packs/foo",
                    "winner_existing_pack_id": owner_id,
                }
            ],
        )

    assert resp.status_code == 422
    assert resp.json()["detail"] == {
        "kind": "ambiguous_key_resolution",
        "action_key": "foo-action",
        "message": "Exactly one of winner_pack_path and winner_existing_pack_id must be set.",
    }


async def test_a_resolution_naming_no_winner_is_rejected(superuser_client, db, origin, hooks):
    repo = await _make_repo(db, f"file://{origin}")
    await db.commit()

    resp = await _activate(
        superuser_client,
        repo.id,
        packs=[{"path": "packs/foo", "name": "foo-pack"}],
        key_resolutions=[{"action_key": "foo-action"}],
    )

    assert resp.status_code == 422
    assert resp.json()["detail"]["kind"] == "ambiguous_key_resolution"


async def test_a_resolution_for_a_pack_path_not_submitted_is_rejected(
    superuser_client, db, origin, hooks
):
    repo = await _make_repo(db, f"file://{origin}")
    await db.commit()

    resp = await _activate(
        superuser_client,
        repo.id,
        packs=[{"path": "packs/foo", "name": "foo-pack"}],
        key_resolutions=[{"action_key": "bar-action", "winner_pack_path": "packs/bar"}],
    )

    assert resp.status_code == 422
    assert resp.json()["detail"] == {
        "kind": "key_resolution_unknown_pack",
        "action_key": "bar-action",
        "winner_pack_path": "packs/bar",
    }
    hooks.sync_pack.assert_not_awaited()


async def test_resolutions_pin_the_new_pack_or_keep_the_installed_one(
    superuser_client, db, origin, hooks
):
    keep_id = await _existing_pack(db, "keep-pack")
    lose_id = await _existing_pack(db, "lose-pack")
    repo = await _make_repo(db, f"file://{origin}")
    await db.commit()

    with _owned(foo_action=keep_id, bar_action=lose_id):
        resp = await _activate(
            superuser_client,
            repo.id,
            packs=[
                {"path": "packs/foo", "name": "foo-pack"},
                {"path": "packs/bar", "name": "bar-pack"},
            ],
            key_resolutions=[
                {"action_key": "foo-action", "winner_existing_pack_id": keep_id},
                {"action_key": "bar-action", "winner_pack_path": "packs/bar"},
            ],
        )

    assert resp.status_code == 200, resp.text
    new_ids = {p["path"]: p["pack_id"] for p in resp.json()["activated_packs"]}
    pins = {
        r.action_key: r
        for r in (
            await db.execute(
                select(ActionResolution).where(
                    ActionResolution.action_key.in_(["foo-action", "bar-action"])
                )
            )
        )
        .scalars()
        .all()
    }
    assert pins["foo-action"].pack_id == keep_id
    assert pins["bar-action"].pack_id == new_ids["packs/bar"]
    assert pins["foo-action"].decided_by_user_id is not None
    assert pins["bar-action"].decided_by_user_id == pins["foo-action"].decided_by_user_id


async def test_post_commit_hooks_sync_each_new_pack_then_reload_once_and_failures_are_logged(
    superuser_client, db, origin, hooks, caplog
):
    repo = await _make_repo(db, f"file://{origin}")
    await db.commit()
    hooks.sync_pack.side_effect = [RuntimeError("git broke"), True]
    hooks.reload.side_effect = RuntimeError("registry broke")

    with caplog.at_level(logging.ERROR, logger="app.api._repo_scan"):
        resp = await _activate(
            superuser_client,
            repo.id,
            packs=[
                {"path": "packs/foo", "name": "foo-pack"},
                {"path": "packs/bar", "name": "bar-pack"},
            ],
        )

    assert resp.status_code == 200, resp.text
    activated = resp.json()["activated_packs"]
    synced = [call.args[1].id for call in hooks.sync_pack.await_args_list]
    assert synced == [p["pack_id"] for p in activated]
    hooks.reload.assert_awaited_once()
    messages = [r.getMessage() for r in caplog.records]
    assert f"post-activate sync_pack failed for pack_id={activated[0]['pack_id']}" in messages
    assert "post-activate reload_registry_async failed" in messages


async def test_an_empty_activation_runs_no_post_commit_hooks(superuser_client, db, origin, hooks):
    repo = await _make_repo(db, f"file://{origin}")
    await db.commit()

    resp = await _activate(superuser_client, repo.id, packs=[])

    assert resp.status_code == 200
    hooks.sync_pack.assert_not_awaited()
    hooks.reload.assert_not_awaited()


async def test_a_clone_failure_is_a_502_naming_the_scrubbed_error(
    superuser_client, db, tmp_path, hooks, caplog
):
    repo = await _make_repo(db, f"file://{tmp_path / 'does-not-exist'}", name="gone-repo")
    await db.commit()

    with caplog.at_level(logging.WARNING, logger="app.api._repo_scan"):
        resp = await _activate(superuser_client, repo.id, packs=[])

    assert resp.status_code == 502
    detail = resp.json()["detail"]
    assert isinstance(detail, str)
    assert detail
    assert f"activate: clone failed for 'gone-repo': {detail}" in [
        r.getMessage() for r in caplog.records
    ]


@pytest.mark.parametrize(
    ("endpoint", "log_prefix"),
    [
        ("scan", "scan: credential decryption failed for repo 'locked-repo': "),
        ("activate", "activate: credential decryption failed for 'locked-repo': "),
    ],
)
async def test_a_credential_failure_is_a_502_that_reveals_nothing(
    superuser_client, db, origin, hooks, caplog, endpoint, log_prefix
):
    repo = await _make_repo(db, f"file://{origin}", name="locked-repo")
    await db.commit()

    with (
        patch(
            "app.api._repo_scan._decrypt_repo_credentials",
            new=AsyncMock(side_effect=RuntimeError("master key missing")),
        ),
        caplog.at_level(logging.WARNING, logger="app.api._repo_scan"),
    ):
        resp = await superuser_client.post(
            f"/api/git-repos/{repo.id}/{endpoint}", json={"packs": [], "gitops_bindings": []}
        )

    assert resp.status_code == 502
    assert resp.json()["detail"] == "Could not decrypt repository credentials"
    assert f"{log_prefix}master key missing" in [r.getMessage() for r in caplog.records]


@pytest.mark.parametrize("endpoint", ["scan", "activate"])
async def test_a_host_key_learned_on_first_clone_is_recorded(
    superuser_client, db, origin, hooks, endpoint
):
    """SEC-27: trust on first use, so later syncs are verified."""
    from app.models.git_repository import GitRepository
    from app.packs.clone import clone_to_thread

    repo = await _make_repo(db, f"file://{origin}", name="tofu-repo")
    await db.commit()

    async def _clone(url, branch, dest, **kwargs):
        head_sha, _ = await clone_to_thread(url, branch, dest, **kwargs)
        return head_sha, "git.example ssh-ed25519 AAAAlearned"

    with patch("app.api._repo_scan.clone_to_thread", new=_clone):
        resp = await superuser_client.post(
            f"/api/git-repos/{repo.id}/{endpoint}", json={"packs": [], "gitops_bindings": []}
        )

    assert resp.status_code == 200, resp.text
    stored = (
        await db.execute(select(GitRepository).where(GitRepository.id == repo.id))
    ).scalar_one()
    await db.refresh(stored)
    assert stored.ssh_host_key_entry == "git.example ssh-ed25519 AAAAlearned"
