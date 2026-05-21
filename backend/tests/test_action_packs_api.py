"""Integration tests for the action-pack CRUD + sync endpoints.

Uses the testcontainers Postgres + real ASGI client fixtures from
conftest. Git interactions are exercised against a local bare repo
surfaced via a ``GitRepository`` row using ``file://`` URLs — no
network dependency.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from app.models.git_repository import GitAuthType, GitRepository


def _git(cwd: Path, args: list[str]) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


@pytest.fixture
def origin_repo(tmp_path: Path) -> Path:
    """Initialise a local git repo with one commit, shaped as an action pack
    (actions/demo/manifest.yml + actions/demo/playbook.yml at the root)."""
    path = tmp_path / "origin"
    path.mkdir()
    _git(path, ["init", "-b", "main"])
    _git(path, ["config", "user.email", "test@example.com"])
    _git(path, ["config", "user.name", "Test"])
    demo_dir = path / "actions" / "demo"
    demo_dir.mkdir(parents=True)
    (demo_dir / "playbook.yml").write_text("---\n- name: demo\n  hosts: all\n  tasks: []\n")
    (demo_dir / "manifest.yml").write_text(
        "key: demo\n"
        "name: Demo\n"
        "description: Demo action\n"
        "icon: Box\n"
        "playbook: playbook.yml\n"
        'version: "1.0"\n'
        'estimated_duration: "1 min"\n'
    )
    _git(path, ["add", "-A"])
    _git(path, ["commit", "-m", "initial"])
    return path


@pytest.fixture
async def git_repo_row(db, origin_repo: Path) -> GitRepository:
    """Insert a GitRepository row pointing at the file:// origin."""
    repo = GitRepository(
        name="test-repo",
        url=f"file://{origin_repo}",
        branch="main",
        auth_type=GitAuthType.ssh_key,  # any value; file:// bypasses auth
        ssh_key_id=None,
    )
    db.add(repo)
    await db.flush()
    return repo


@pytest.fixture
def local_pack_dir(tmp_path: Path) -> Path:
    """Materialise a ready-to-load local pack directory (no git)."""
    p = tmp_path / "my-local-pack"
    hello_dir = p / "actions" / "hello"
    hello_dir.mkdir(parents=True)
    (hello_dir / "playbook.yml").write_text(
        "---\n- name: hello\n  hosts: all\n  tasks: []\n"
    )
    (hello_dir / "manifest.yml").write_text(
        "key: hello-local\n"
        "name: Hello from local\n"
        "description: Demo\n"
        "icon: Box\n"
        "playbook: playbook.yml\n"
        'version: "1.0"\n'
        'estimated_duration: "1 min"\n'
    )
    return p


# ---------------------------------------------------------------------------
# List
# ---------------------------------------------------------------------------


async def test_list_returns_seeded_labdog_playbooks_pack(superuser_client):
    """A fresh install ships one seeded pack pointing at the canonical
    labdog-playbooks repo. Operators that don't want it can delete it
    via the UI; the migration only inserts when missing."""
    resp = await superuser_client.get("/api/action-packs")
    assert resp.status_code == 200
    packs = resp.json()
    assert len(packs) == 1
    assert packs[0]["name"] == "labdog-playbooks"
    assert packs[0]["source_type"] == "git"
    # Pack rows no longer carry a ``position`` field — precedence is
    # per-key via ``action_resolution``.
    assert "position" not in packs[0]


async def test_regular_user_cannot_manage_packs(regular_user_client):
    resp = await regular_user_client.get("/api/action-packs")
    assert resp.status_code in (401, 403)


# ---------------------------------------------------------------------------
# Create — git source
# ---------------------------------------------------------------------------


async def test_create_git_pack_syncs_and_adds_action(
    superuser_client, git_repo_row, monkeypatch, tmp_path
):
    """Happy path: create a git pack referencing a GitRepository → server
    clones it → new action shows up in the registry."""
    from app.config import settings

    monkeypatch.setattr(settings.ansible, "packs_root_dir", str(tmp_path / "packs"))

    resp = await superuser_client.post(
        "/api/action-packs",
        json={
            "name": "demo-pack",
            "source_type": "git",
            "git_repository_id": git_repo_row.id,
            "enabled": True,
        },
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["source_type"] == "git"
    assert body["git_repository_id"] == git_repo_row.id
    assert body["git_repository_name"] == "test-repo"
    assert body["path"] == ""
    assert "position" not in body  # column dropped in migration 0004
    assert body["last_sync_status"] == "ok"
    assert body["current_sha"]

    actions = (await superuser_client.get("/api/actions/")).json()
    assert "demo" in {a["key"] for a in actions}


async def test_create_git_pack_missing_git_repository_id_rejected(
    superuser_client,
):
    resp = await superuser_client.post(
        "/api/action-packs",
        json={"name": "needs-repo", "source_type": "git"},
    )
    assert resp.status_code == 422
    assert "git_repository_id" in resp.text


async def test_create_git_pack_with_nonexistent_repo_rejected(superuser_client):
    resp = await superuser_client.post(
        "/api/action-packs",
        json={
            "name": "bad-repo",
            "source_type": "git",
            "git_repository_id": 9999,
        },
    )
    assert resp.status_code == 400
    assert "9999" in resp.text


async def test_create_no_longer_assigns_position(
    superuser_client, git_repo_row, monkeypatch, tmp_path
):
    """The position column is gone; the response shape no longer
    carries it. Packs are unordered — per-key resolution rows decide
    every contested winner."""
    from app.config import settings

    monkeypatch.setattr(settings.ansible, "packs_root_dir", str(tmp_path / "packs"))

    resp = await superuser_client.post(
        "/api/action-packs",
        json={
            "name": "new-pack",
            "source_type": "git",
            "git_repository_id": git_repo_row.id,
        },
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert "position" not in body


async def test_create_rejects_bundled_name(superuser_client):
    resp = await superuser_client.post(
        "/api/action-packs",
        json={"name": "bundled", "source_type": "local", "local_path": "/tmp/x"},
    )
    assert resp.status_code == 422


async def test_create_conflict_on_duplicate_name(
    superuser_client, git_repo_row, monkeypatch, tmp_path
):
    from app.config import settings

    monkeypatch.setattr(settings.ansible, "packs_root_dir", str(tmp_path / "packs"))

    body = {
        "name": "dup",
        "source_type": "git",
        "git_repository_id": git_repo_row.id,
    }
    r1 = await superuser_client.post("/api/action-packs", json=body)
    assert r1.status_code == 201
    r2 = await superuser_client.post("/api/action-packs", json=body)
    assert r2.status_code == 409


# ---------------------------------------------------------------------------
# Create — local source
# ---------------------------------------------------------------------------


async def test_create_local_pack_registers_action(superuser_client, local_pack_dir):
    resp = await superuser_client.post(
        "/api/action-packs",
        json={
            "name": "local-demo",
            "source_type": "local",
            "local_path": str(local_pack_dir),
        },
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["source_type"] == "local"
    assert body["git_repository_id"] is None
    assert body["local_path"] == str(local_pack_dir)
    assert "position" not in body
    assert body["last_sync_status"] == "ok"

    actions = (await superuser_client.get("/api/actions/")).json()
    assert "hello-local" in {a["key"] for a in actions}


async def test_create_local_pack_missing_local_path_rejected(superuser_client):
    resp = await superuser_client.post(
        "/api/action-packs",
        json={"name": "local-no-path", "source_type": "local"},
    )
    assert resp.status_code == 422


async def test_create_local_pack_rejects_git_repository_id(
    superuser_client, git_repo_row, local_pack_dir
):
    resp = await superuser_client.post(
        "/api/action-packs",
        json={
            "name": "mixed",
            "source_type": "local",
            "local_path": str(local_pack_dir),
            "git_repository_id": git_repo_row.id,
        },
    )
    assert resp.status_code == 422
    assert "local" in resp.text.lower()


async def test_create_local_pack_with_missing_path_marks_failed(superuser_client, tmp_path: Path):
    resp = await superuser_client.post(
        "/api/action-packs",
        json={
            "name": "local-broken",
            "source_type": "local",
            "local_path": str(tmp_path / "does-not-exist"),
        },
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["last_sync_status"] == "failed"
    assert "does not exist" in body["last_sync_error"]


# ---------------------------------------------------------------------------
# Subpath support
# ---------------------------------------------------------------------------


async def test_git_pack_with_subpath(superuser_client, tmp_path, db, monkeypatch):
    """A pack whose manifests live under a subpath loads from there,
    not the repo root."""
    from app.config import settings

    monkeypatch.setattr(settings.ansible, "packs_root_dir", str(tmp_path / "packs"))

    # Build an origin where the pack lives under vendor/my-pack/.
    origin = tmp_path / "origin"
    origin.mkdir()
    _git(origin, ["init", "-b", "main"])
    _git(origin, ["config", "user.email", "t@example.com"])
    _git(origin, ["config", "user.name", "Test"])
    nested_dir = origin / "vendor" / "my-pack" / "actions" / "nested"
    nested_dir.mkdir(parents=True)
    (nested_dir / "playbook.yml").write_text(
        "---\n- name: nested\n  hosts: all\n  tasks: []\n"
    )
    (nested_dir / "manifest.yml").write_text(
        "key: nested\n"
        "name: Nested\n"
        "description: From a subpath\n"
        "icon: Box\n"
        "playbook: playbook.yml\n"
        'version: "1.0"\n'
        'estimated_duration: "1 min"\n'
    )
    _git(origin, ["add", "-A"])
    _git(origin, ["commit", "-m", "initial"])

    repo = GitRepository(
        name="subpath-repo",
        url=f"file://{origin}",
        branch="main",
        auth_type=GitAuthType.ssh_key,
        ssh_key_id=None,
    )
    db.add(repo)
    await db.flush()

    resp = await superuser_client.post(
        "/api/action-packs",
        json={
            "name": "nested-pack",
            "source_type": "git",
            "git_repository_id": repo.id,
            "path": "vendor/my-pack",
        },
    )
    assert resp.status_code == 201, resp.text
    actions = (await superuser_client.get("/api/actions/")).json()
    assert "nested" in {a["key"] for a in actions}


# ---------------------------------------------------------------------------
# Switch source
# ---------------------------------------------------------------------------


async def test_switch_git_to_local_drops_checkout(
    superuser_client, git_repo_row, local_pack_dir, monkeypatch, tmp_path
):
    from app.config import settings
    from app.packs.service import checkout_path_for

    monkeypatch.setattr(settings.ansible, "packs_root_dir", str(tmp_path / "packs"))

    r = await superuser_client.post(
        "/api/action-packs",
        json={
            "name": "switch-to-local",
            "source_type": "git",
            "git_repository_id": git_repo_row.id,
        },
    )
    pack_id = r.json()["id"]
    git_checkout = checkout_path_for(pack_id)
    assert git_checkout.is_dir()

    r2 = await superuser_client.put(
        f"/api/action-packs/{pack_id}",
        json={"source_type": "local", "local_path": str(local_pack_dir)},
    )
    assert r2.status_code == 200, r2.text
    body = r2.json()
    assert body["source_type"] == "local"
    assert body["git_repository_id"] is None
    # The old managed checkout is gone; the admin-supplied path is untouched.
    assert not git_checkout.exists()
    assert local_pack_dir.is_dir()

    actions = (await superuser_client.get("/api/actions/")).json()
    assert "hello-local" in {a["key"] for a in actions}


# ---------------------------------------------------------------------------
# Delete
# ---------------------------------------------------------------------------


async def test_delete_removes_checkout_and_action(
    superuser_client, git_repo_row, monkeypatch, tmp_path
):
    from app.config import settings
    from app.packs.service import checkout_path_for

    monkeypatch.setattr(settings.ansible, "packs_root_dir", str(tmp_path / "packs"))

    r = await superuser_client.post(
        "/api/action-packs",
        json={
            "name": "to-delete",
            "source_type": "git",
            "git_repository_id": git_repo_row.id,
        },
    )
    pack_id = r.json()["id"]
    path = checkout_path_for(pack_id)
    assert path.is_dir()

    r2 = await superuser_client.delete(f"/api/action-packs/{pack_id}")
    assert r2.status_code == 204
    assert not path.exists()

    actions = (await superuser_client.get("/api/actions/")).json()
    assert "demo" not in {a["key"] for a in actions}


# ---------------------------------------------------------------------------
# Sync
# ---------------------------------------------------------------------------


async def test_manual_sync_after_upstream_change(
    superuser_client, git_repo_row, origin_repo, monkeypatch, tmp_path
):
    from app.config import settings

    monkeypatch.setattr(settings.ansible, "packs_root_dir", str(tmp_path / "packs"))

    r = await superuser_client.post(
        "/api/action-packs",
        json={
            "name": "syncable",
            "source_type": "git",
            "git_repository_id": git_repo_row.id,
        },
    )
    pack_id = r.json()["id"]
    original_sha = r.json()["current_sha"]

    # New commit upstream. Modify a tracked file so ``git commit -am``
    # actually has something to record -- the previous test code wrote
    # a new untracked ``actions/demo.yml`` (flat-layout leftover) which
    # ``-am`` ignores, leaving an empty commit attempt.
    (origin_repo / "actions" / "demo" / "playbook.yml").write_text(
        "---\n# updated\n- name: demo\n  hosts: all\n  tasks: []\n"
    )
    _git(origin_repo, ["commit", "-am", "upd"])

    r2 = await superuser_client.post(f"/api/action-packs/{pack_id}/sync")
    assert r2.status_code == 200
    body = r2.json()
    assert body["success"] is True
    assert body["current_sha"] != original_sha


# ---------------------------------------------------------------------------
# Collision provenance
# ---------------------------------------------------------------------------


async def test_fresh_conflict_freezes_previous_winner(
    superuser_client, git_repo_row, monkeypatch, tmp_path
):
    """When a newly-added pack contributes a key already owned by an
    existing pack, the registry must NOT silently flip to the
    higher-positioned newcomer. Behaviour is frozen to the previous
    winner until the operator resolves the conflict via the
    ``/action-packs`` UI. The newer pack still appears in
    ``overridden_from`` for provenance."""
    from app.config import settings

    monkeypatch.setattr(settings.ansible, "packs_root_dir", str(tmp_path / "packs"))

    local_override = tmp_path / "local-override"
    demo_dir = local_override / "actions" / "demo"
    demo_dir.mkdir(parents=True)
    (demo_dir / "playbook.yml").write_text(
        "---\n- name: demo\n  hosts: all\n  tasks: []\n"
    )
    (demo_dir / "manifest.yml").write_text(
        "key: demo\n"
        "name: Demo\n"
        "description: Local override\n"
        "icon: Box\n"
        "playbook: playbook.yml\n"
        'version: "1.0"\n'
        'estimated_duration: "1 min"\n'
    )

    r1 = await superuser_client.post(
        "/api/action-packs",
        json={
            "name": "git-demo",
            "source_type": "git",
            "git_repository_id": git_repo_row.id,
        },
    )
    assert r1.status_code == 201, r1.text

    r2 = await superuser_client.post(
        "/api/action-packs",
        json={
            "name": "local-demo",
            "source_type": "local",
            "local_path": str(local_override),
        },
    )
    assert r2.status_code == 201, r2.text

    actions = (await superuser_client.get("/api/actions/")).json()
    demo = next(a for a in actions if a["key"] == "demo")
    assert demo["pack_name"] == "git-demo"
    assert "local-demo" in demo["overridden_from"]


# ---------------------------------------------------------------------------
# SEC-12 — path field traversal rejection (API layer)
# ---------------------------------------------------------------------------


async def test_create_git_pack_dotdot_path_rejected(superuser_client, git_repo_row):
    """A ``path`` containing ``..`` must be rejected at the schema layer
    with a 422 before any disk I/O is attempted."""
    resp = await superuser_client.post(
        "/api/action-packs",
        json={
            "name": "traversal-attempt",
            "source_type": "git",
            "git_repository_id": git_repo_row.id,
            "path": "../../../etc/labdog",
        },
    )
    assert resp.status_code == 422
    assert ".." in resp.text or "traversal" in resp.text.lower()


async def test_update_git_pack_dotdot_path_rejected(
    superuser_client, git_repo_row, monkeypatch, tmp_path
):
    """Updating an existing pack with a traversal path is also rejected."""
    from app.config import settings

    monkeypatch.setattr(settings.ansible, "packs_root_dir", str(tmp_path / "packs"))

    r = await superuser_client.post(
        "/api/action-packs",
        json={
            "name": "safe-pack",
            "source_type": "git",
            "git_repository_id": git_repo_row.id,
        },
    )
    assert r.status_code == 201, r.text
    pack_id = r.json()["id"]

    r2 = await superuser_client.put(
        f"/api/action-packs/{pack_id}",
        json={"path": "../../escape"},
    )
    assert r2.status_code == 422
    assert ".." in r2.text or "traversal" in r2.text.lower()


# ---------------------------------------------------------------------------
# SEC-13 — local_path dangerous prefix rejection (API layer)
# ---------------------------------------------------------------------------


async def test_create_local_pack_etc_path_rejected(superuser_client):
    """/etc as local_path must be rejected at the schema layer."""
    resp = await superuser_client.post(
        "/api/action-packs",
        json={
            "name": "etc-pack",
            "source_type": "local",
            "local_path": "/etc/labdog",
        },
    )
    assert resp.status_code == 422
    assert "/etc" in resp.text


async def test_create_local_pack_proc_path_rejected(superuser_client):
    """/proc as local_path must be rejected at the schema layer."""
    resp = await superuser_client.post(
        "/api/action-packs",
        json={
            "name": "proc-pack",
            "source_type": "local",
            "local_path": "/proc/self",
        },
    )
    assert resp.status_code == 422
    assert "/proc" in resp.text


async def test_create_local_pack_relative_path_rejected(superuser_client):
    """A relative local_path (no leading /) must be rejected."""
    resp = await superuser_client.post(
        "/api/action-packs",
        json={
            "name": "rel-pack",
            "source_type": "local",
            "local_path": "relative/path",
        },
    )
    assert resp.status_code == 422
    assert "absolute" in resp.text.lower()
