"""Tests for ``POST /api/git-repos/{id}/activate``.

Re-uses the local file:// git repo pattern from
``test_repo_scan_api.py`` so no network access is needed.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import select

from app.models.audit_log import AuditLog
from app.models.git_repository import GitAuthType, GitRepository
from app.models.host_group import HostGroup
from app.packs.models import ActionPack, PackRole, PackSourceType

pytestmark = pytest.mark.integration


def _git(cwd: Path, args: list[str]) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


def _make_pack_tree(root: Path, pack_subpath: str, pack_name: str, action_key: str) -> None:
    pack = root / pack_subpath
    pack.mkdir(parents=True)
    (pack / "pack.yml").write_text(f"name: {pack_name}\n")
    actions = pack / "actions"
    actions.mkdir()
    (actions / f"{action_key}.yml").write_text("---\n- name: x\n  hosts: all\n  tasks: []\n")
    (actions / f"{action_key}.manifest.yml").write_text(
        f"key: {action_key}\n"
        f"name: {action_key}\n"
        "description: ''\n"
        "icon: Box\n"
        f"playbook: {action_key}.yml\n"
        'version: "1.0"\n'
        'estimated_duration: "5 min"\n'
        "destructive: false\n"
        "supports_group: false\n"
        "supports_host: true\n"
        "parameters: []\n"
    )


@pytest.fixture
def origin_with_packs_and_gitops(tmp_path: Path) -> Path:
    path = tmp_path / "origin"
    path.mkdir()
    _make_pack_tree(path, "packs/foo", "foo-pack", "foo-action")
    _make_pack_tree(path, "packs/bar", "bar-pack", "bar-action")
    groups = path / "groups"
    groups.mkdir()
    (groups / "web.yaml").write_text("group: web\npriority: 100\n")
    _git(path, ["init", "-b", "main"])
    _git(path, ["config", "user.email", "test@example.com"])
    _git(path, ["config", "user.name", "Test"])
    _git(path, ["add", "-A"])
    _git(path, ["commit", "-m", "initial"])
    return path


@pytest.fixture
def origin_with_intra_repo_conflict(tmp_path: Path) -> Path:
    """Two packs both contributing 'foo-action'."""
    path = tmp_path / "origin-conflict"
    path.mkdir()
    _make_pack_tree(path, "packs/a", "a-pack", "foo-action")
    _make_pack_tree(path, "packs/b", "b-pack", "foo-action")
    _git(path, ["init", "-b", "main"])
    _git(path, ["config", "user.email", "test@example.com"])
    _git(path, ["config", "user.name", "Test"])
    _git(path, ["add", "-A"])
    _git(path, ["commit", "-m", "initial"])
    return path


async def _make_repo(db, url: str, name: str = "test-activate-repo") -> GitRepository:
    repo = GitRepository(
        name=name,
        url=url,
        branch="main",
        auth_type=GitAuthType.ssh_key,
        ssh_key_id=None,
    )
    db.add(repo)
    await db.flush()
    return repo


@pytest.fixture(autouse=True)
def stub_post_commit_hooks():
    """sync_pack and reload_registry_async hit real git/disk and the
    registry. For activation tests we just want to assert the rows
    landed; the actual sync is covered by test_action_packs_api.py.
    Patch at the source modules since _repo_scan.py imports them
    lazily inside the function."""
    with (
        patch("app.packs.service.sync_pack", new=AsyncMock(return_value=True)),
        patch(
            "app.actions.registry.reload_registry_async",
            new=AsyncMock(return_value={}),
        ),
    ):
        yield


# ---------------------------------------------------------------------------
# auth + 404
# ---------------------------------------------------------------------------


async def test_activate_requires_superuser(regular_user_client, db, origin_with_packs_and_gitops):
    repo = await _make_repo(db, f"file://{origin_with_packs_and_gitops}")
    await db.commit()
    resp = await regular_user_client.post(
        f"/api/git-repos/{repo.id}/activate",
        json={"packs": [], "gitops_bindings": []},
    )
    assert resp.status_code == 403


async def test_activate_404_for_unknown_repo(superuser_client):
    resp = await superuser_client.post(
        "/api/git-repos/99999/activate",
        json={"packs": [], "gitops_bindings": []},
    )
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# happy path
# ---------------------------------------------------------------------------


async def test_activate_happy_path_creates_packs_and_bindings(
    superuser_client, db, origin_with_packs_and_gitops
):
    repo = await _make_repo(db, f"file://{origin_with_packs_and_gitops}")
    group = HostGroup(name="web", priority=100)
    db.add(group)
    await db.flush()
    group_id = group.id
    await db.commit()

    resp = await superuser_client.post(
        f"/api/git-repos/{repo.id}/activate",
        json={
            "packs": [
                {"path": "packs/foo", "name": "foo-pack", "role": "default"},
                {"path": "packs/bar", "name": "bar-pack", "role": "override"},
            ],
            "gitops_bindings": [
                {"file_path": "groups/web.yaml", "host_group_id": group_id},
            ],
        },
    )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert len(body["activated_packs"]) == 2
    assert len(body["activated_gitops_bindings"]) == 1
    assert isinstance(body["head_sha"], str)
    assert len(body["head_sha"]) == 40

    # ActionPack rows persisted with right columns.
    rows = (
        (await db.execute(select(ActionPack).where(ActionPack.git_repository_id == repo.id)))
        .scalars()
        .all()
    )
    assert len(rows) == 2
    by_name = {r.name: r for r in rows}
    assert by_name["foo-pack"].role == PackRole.DEFAULT
    assert by_name["foo-pack"].path == "packs/foo"
    assert by_name["foo-pack"].source_type == PackSourceType.GIT
    assert by_name["foo-pack"].enabled is True
    assert by_name["bar-pack"].role == PackRole.OVERRIDE

    # HostGroup gitops binding applied.
    refreshed_group = (
        await db.execute(select(HostGroup).where(HostGroup.id == group_id))
    ).scalar_one()
    await db.refresh(refreshed_group)
    assert refreshed_group.gitops_enabled is True
    assert refreshed_group.git_repository_id == repo.id
    assert refreshed_group.gitops_file_path == "groups/web.yaml"

    # Audit rows: 2 pack.activated + 1 gitops.bound + 1 repo.scan_activated.
    audits = (
        (
            await db.execute(
                select(AuditLog).where(
                    AuditLog.action.in_(["pack.activated", "gitops.bound", "repo.scan_activated"])
                )
            )
        )
        .scalars()
        .all()
    )
    actions = sorted(a.action for a in audits)
    assert actions == [
        "gitops.bound",
        "pack.activated",
        "pack.activated",
        "repo.scan_activated",
    ]
    summary = next(a for a in audits if a.action == "repo.scan_activated")
    assert summary.after_state["pack_count"] == 2
    assert summary.after_state["binding_count"] == 1


async def test_activate_pack_name_collision_appends_suffix(
    superuser_client, db, origin_with_packs_and_gitops
):
    """Pre-existing ActionPack with the same name → activation
    disambiguates to <name>-<repo_name>."""
    pre_existing = ActionPack(
        name="foo-pack",
        source_type=PackSourceType.LOCAL,
        local_path="/tmp/dummy",
        role=PackRole.DEFAULT,
        enabled=False,
    )
    db.add(pre_existing)
    await db.flush()

    repo = await _make_repo(db, f"file://{origin_with_packs_and_gitops}", name="my-test-repo")
    await db.commit()

    resp = await superuser_client.post(
        f"/api/git-repos/{repo.id}/activate",
        json={
            "packs": [{"path": "packs/foo", "name": "foo-pack", "role": "default"}],
            "gitops_bindings": [],
        },
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    activated = body["activated_packs"][0]
    assert activated["name"] == "foo-pack-my-test-repo"
    assert activated["requested_name"] == "foo-pack"
    assert activated["name_was_disambiguated"] is True


# ---------------------------------------------------------------------------
# 409 paths
# ---------------------------------------------------------------------------


async def test_activate_rejects_intra_repo_conflict(
    superuser_client, db, origin_with_intra_repo_conflict
):
    repo = await _make_repo(db, f"file://{origin_with_intra_repo_conflict}")
    await db.commit()

    resp = await superuser_client.post(
        f"/api/git-repos/{repo.id}/activate",
        json={
            "packs": [
                {"path": "packs/a", "name": "a-pack", "role": "default"},
                {"path": "packs/b", "name": "b-pack", "role": "default"},
            ],
            "gitops_bindings": [],
        },
    )
    assert resp.status_code == 409
    detail = resp.json()["detail"]
    assert detail["kind"] == "intra_repo_key_conflict"
    assert any(c["key"] == "foo-action" for c in detail["conflicts"])


async def test_activate_rejects_missing_pack_path(
    superuser_client, db, origin_with_packs_and_gitops
):
    """Submission references a path that doesn't exist in the scan."""
    repo = await _make_repo(db, f"file://{origin_with_packs_and_gitops}")
    await db.commit()

    resp = await superuser_client.post(
        f"/api/git-repos/{repo.id}/activate",
        json={
            "packs": [{"path": "packs/nonexistent", "name": "x", "role": "default"}],
            "gitops_bindings": [],
        },
    )
    assert resp.status_code == 409
    detail = resp.json()["detail"]
    assert detail["kind"] == "scan_drift"
    assert detail["missing_pack_paths"] == ["packs/nonexistent"]


async def test_activate_rejects_group_bound_to_other_repo(
    superuser_client, db, origin_with_packs_and_gitops
):
    """A group already bound to a different repo → 409."""
    other_repo = await _make_repo(db, "file:///nonexistent", name="other")
    group = HostGroup(
        name="already-bound",
        priority=50,
        git_repository_id=other_repo.id,
        gitops_enabled=True,
        gitops_file_path="elsewhere.yaml",
    )
    db.add(group)
    await db.flush()
    group_id = group.id

    repo = await _make_repo(db, f"file://{origin_with_packs_and_gitops}")
    await db.commit()

    resp = await superuser_client.post(
        f"/api/git-repos/{repo.id}/activate",
        json={
            "packs": [],
            "gitops_bindings": [{"file_path": "groups/web.yaml", "host_group_id": group_id}],
        },
    )
    assert resp.status_code == 409
    detail = resp.json()["detail"]
    assert detail["kind"] == "group_bound_to_other_repo"
    assert detail["host_group_id"] == group_id


async def test_activate_rejects_gitops_file_not_in_scan(
    superuser_client, db, origin_with_packs_and_gitops
):
    repo = await _make_repo(db, f"file://{origin_with_packs_and_gitops}")
    group = HostGroup(name="x", priority=10)
    db.add(group)
    await db.flush()
    group_id = group.id
    await db.commit()

    resp = await superuser_client.post(
        f"/api/git-repos/{repo.id}/activate",
        json={
            "packs": [],
            "gitops_bindings": [{"file_path": "groups/nope.yaml", "host_group_id": group_id}],
        },
    )
    assert resp.status_code == 409
    assert resp.json()["detail"]["kind"] == "gitops_file_missing"


async def test_activate_rejects_unknown_host_group(
    superuser_client, db, origin_with_packs_and_gitops
):
    repo = await _make_repo(db, f"file://{origin_with_packs_and_gitops}")
    await db.commit()

    resp = await superuser_client.post(
        f"/api/git-repos/{repo.id}/activate",
        json={
            "packs": [],
            "gitops_bindings": [{"file_path": "groups/web.yaml", "host_group_id": 99999}],
        },
    )
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# empty submission is a no-op (still emits the summary audit row)
# ---------------------------------------------------------------------------


async def test_activate_empty_submission_succeeds(
    superuser_client, db, origin_with_packs_and_gitops
):
    repo = await _make_repo(db, f"file://{origin_with_packs_and_gitops}")
    await db.commit()

    resp = await superuser_client.post(
        f"/api/git-repos/{repo.id}/activate",
        json={"packs": [], "gitops_bindings": []},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["activated_packs"] == []
    assert body["activated_gitops_bindings"] == []

    # Even an empty activation emits the summary row so auditors see the
    # operator's no-op intent.
    summary = (
        await db.execute(
            select(AuditLog).where(
                AuditLog.action == "repo.scan_activated",
                AuditLog.entity_id == repo.id,
            )
        )
    ).scalar_one_or_none()
    assert summary is not None
    assert summary.after_state["pack_count"] == 0
    assert summary.after_state["binding_count"] == 0
