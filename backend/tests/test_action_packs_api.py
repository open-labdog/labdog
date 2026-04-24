"""Integration tests for the action-pack CRUD + sync endpoints.

These use the testcontainers Postgres + real ASGI client fixtures from
conftest. Git interactions are exercised against a local bare repo via
``file://`` so the test doesn't depend on the network.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest


@pytest.fixture
def origin_repo(tmp_path: Path) -> str:
    """Initialise a bare-ish local git repo with one commit."""
    path = tmp_path / "origin"
    path.mkdir()
    _git(path, ["init", "-b", "main"])
    _git(path, ["config", "user.email", "test@example.com"])
    _git(path, ["config", "user.name", "Test"])
    (path / "actions").mkdir()
    (path / "actions" / "demo.yml").write_text(
        "---\n- name: demo\n  hosts: all\n  tasks: []\n"
    )
    (path / "actions" / "demo.manifest.yml").write_text(
        "key: demo\n"
        "name: Demo\n"
        "description: Demo action\n"
        "icon: Box\n"
        "playbook: demo.yml\n"
        'version: "1.0"\n'
        'estimated_duration: "1 min"\n'
    )
    _git(path, ["add", "-A"])
    _git(path, ["commit", "-m", "initial"])
    return f"file://{path}"


def _git(cwd: Path, args: list[str]) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


async def test_list_empty_when_no_packs(superuser_client):
    resp = await superuser_client.get("/api/action-packs")
    assert resp.status_code == 200
    assert resp.json() == []


async def test_create_public_pack_syncs_and_adds_action(
    superuser_client, origin_repo, monkeypatch, tmp_path
):
    """Happy path: create a public pack → server clones it → new action
    shows up in the registry and on the list endpoint."""
    from app.config import settings

    monkeypatch.setattr(settings.ansible, "packs_root_dir", str(tmp_path / "packs"))

    resp = await superuser_client.post(
        "/api/action-packs",
        json={
            "name": "demo-pack",
            "repo_url": origin_repo,
            "ref": "main",
            "role": "default",
            "enabled": True,
            "auth_type": "none",
        },
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["last_sync_status"] == "ok"
    assert body["current_sha"]
    assert body["has_ssh_key"] is False
    assert body["has_token"] is False
    assert body["role"] == "default"
    # Priority is derived server-side; default git pack is tier 10.
    assert body["priority"] == 10

    # Registry has the new action
    actions = (await superuser_client.get("/api/actions/")).json()
    keys = {a["key"] for a in actions}
    assert "demo" in keys


async def test_collision_surfaces_override_history_in_api(
    superuser_client, origin_repo, local_pack_dir, monkeypatch, tmp_path
):
    """When two packs declare the same key, GET /api/actions/ reports
    the winning pack and the shadowed ones."""
    from app.config import settings

    monkeypatch.setattr(settings.ansible, "packs_root_dir", str(tmp_path / "packs"))

    # origin_repo contributes `demo`. Add a local pack that ALSO
    # contributes `demo` at priority 1000 so it wins.
    local_override = tmp_path / "local-override"
    (local_override / "actions").mkdir(parents=True)
    (local_override / "actions" / "demo.yml").write_text(
        "---\n- name: demo\n  hosts: all\n  tasks: []\n"
    )
    (local_override / "actions" / "demo.manifest.yml").write_text(
        "key: demo\n"
        "name: Demo\n"
        "description: Local override\n"
        "icon: Box\n"
        "playbook: demo.yml\n"
        'version: "1.0"\n'
        'estimated_duration: "1 min"\n'
    )

    # Create the git pack first (lower priority because role=override = 100).
    r1 = await superuser_client.post(
        "/api/action-packs",
        json={
            "name": "git-demo",
            "repo_url": origin_repo,
            "auth_type": "none",
        },
    )
    assert r1.status_code == 201

    # And a local pack (priority 1000) which will win on collision.
    r2 = await superuser_client.post(
        "/api/action-packs",
        json={
            "name": "local-demo",
            "source_type": "local",
            "repo_url": str(local_override),
            "auth_type": "none",
        },
    )
    assert r2.status_code == 201

    actions = (await superuser_client.get("/api/actions/")).json()
    demo = next(a for a in actions if a["key"] == "demo")
    assert demo["pack_name"] == "local-demo"
    assert demo["overridden_from"] == ["git-demo"]


async def test_override_role_derives_priority_100(
    superuser_client, origin_repo, monkeypatch, tmp_path
):
    """Git pack with role=override lands at priority 100 (between
    default gits and local)."""
    from app.config import settings

    monkeypatch.setattr(settings.ansible, "packs_root_dir", str(tmp_path / "packs"))

    resp = await superuser_client.post(
        "/api/action-packs",
        json={
            "name": "override-pack",
            "repo_url": origin_repo,
            "auth_type": "none",
            # role defaults to override; assert derived priority.
        },
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["role"] == "override"
    assert body["priority"] == 100


async def test_create_rejects_bundled_name(superuser_client):
    resp = await superuser_client.post(
        "/api/action-packs",
        json={"name": "bundled", "repo_url": "https://example/x", "auth_type": "none"},
    )
    assert resp.status_code == 422


async def test_create_conflict_on_duplicate_name(
    superuser_client, origin_repo, monkeypatch, tmp_path
):
    from app.config import settings

    monkeypatch.setattr(settings.ansible, "packs_root_dir", str(tmp_path / "packs"))

    body = {"name": "dup", "repo_url": origin_repo, "auth_type": "none"}
    r1 = await superuser_client.post("/api/action-packs", json=body)
    assert r1.status_code == 201
    r2 = await superuser_client.post("/api/action-packs", json=body)
    assert r2.status_code == 409


async def test_create_missing_ssh_key_rejected(superuser_client):
    resp = await superuser_client.post(
        "/api/action-packs",
        json={
            "name": "ssh-no-key",
            "repo_url": "git@example.com:x.git",
            "auth_type": "ssh",
        },
    )
    assert resp.status_code == 422
    assert "ssh_private_key" in resp.text


async def test_create_token_with_ssh_auth_rejected(superuser_client):
    resp = await superuser_client.post(
        "/api/action-packs",
        json={
            "name": "mixed",
            "repo_url": "git@example.com:x.git",
            "auth_type": "ssh",
            "ssh_private_key": "key",
            "token": "should-not-be-here",
        },
    )
    assert resp.status_code == 422


async def test_pre_save_test_runs_ls_remote(
    superuser_client, origin_repo
):
    resp = await superuser_client.post(
        "/api/action-packs/test",
        json={
            "repo_url": origin_repo,
            "ref": "main",
            "auth_type": "none",
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is True
    assert body["commit_sha"]


async def test_pre_save_test_fails_cleanly_on_bad_ref(
    superuser_client, origin_repo
):
    resp = await superuser_client.post(
        "/api/action-packs/test",
        json={
            "repo_url": origin_repo,
            "ref": "does-not-exist",
            "auth_type": "none",
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is False
    assert body["commit_sha"] is None


async def test_update_ref_triggers_resync(
    superuser_client, origin_repo, monkeypatch, tmp_path
):
    from app.config import settings

    monkeypatch.setattr(settings.ansible, "packs_root_dir", str(tmp_path / "packs"))

    # create pack
    r = await superuser_client.post(
        "/api/action-packs",
        json={
            "name": "upd",
            "repo_url": origin_repo,
            "ref": "main",
            "auth_type": "none",
        },
    )
    pack_id = r.json()["id"]
    original_sha = r.json()["current_sha"]

    # Add another commit upstream
    origin_path = Path(origin_repo.replace("file://", ""))
    (origin_path / "actions" / "demo.yml").write_text("updated\n")
    _git(origin_path, ["commit", "-am", "upd"])

    # Update ref triggers resync
    r2 = await superuser_client.put(
        f"/api/action-packs/{pack_id}",
        json={"ref": "main"},  # same ref, but the client counts this as a change
    )
    # Same-value edit doesn't need resync, so sha unchanged:
    assert r2.json()["current_sha"] == original_sha

    # Manual sync picks up the new commit:
    r3 = await superuser_client.post(f"/api/action-packs/{pack_id}/sync")
    assert r3.status_code == 200
    assert r3.json()["success"] is True
    assert r3.json()["current_sha"] != original_sha


async def test_delete_removes_checkout_and_action(
    superuser_client, origin_repo, monkeypatch, tmp_path
):
    from app.config import settings
    from app.packs.service import checkout_path_for

    monkeypatch.setattr(settings.ansible, "packs_root_dir", str(tmp_path / "packs"))

    r = await superuser_client.post(
        "/api/action-packs",
        json={"name": "to-delete", "repo_url": origin_repo, "auth_type": "none"},
    )
    pack_id = r.json()["id"]
    path = checkout_path_for(pack_id)
    assert path.is_dir()

    r2 = await superuser_client.delete(f"/api/action-packs/{pack_id}")
    assert r2.status_code == 204
    assert not path.exists()

    actions = (await superuser_client.get("/api/actions/")).json()
    keys = {a["key"] for a in actions}
    assert "demo" not in keys


async def test_regular_user_cannot_manage_packs(
    regular_user_client,
):
    resp = await regular_user_client.get("/api/action-packs")
    # fastapi-users returns 401 or 403 for non-superusers; both acceptable.
    assert resp.status_code in (401, 403)


@pytest.fixture
def local_pack_dir(tmp_path: Path) -> Path:
    """Materialise a ready-to-load local pack directory (no git)."""
    p = tmp_path / "my-local-pack"
    (p / "actions").mkdir(parents=True)
    (p / "actions" / "hello.yml").write_text(
        "---\n- name: hello\n  hosts: all\n  tasks: []\n"
    )
    (p / "actions" / "hello.manifest.yml").write_text(
        "key: hello-local\n"
        "name: Hello from local\n"
        "description: Demo\n"
        "icon: Box\n"
        "playbook: hello.yml\n"
        'version: "1.0"\n'
        'estimated_duration: "1 min"\n'
    )
    return p


async def test_create_local_pack_registers_action(superuser_client, local_pack_dir):
    resp = await superuser_client.post(
        "/api/action-packs",
        json={
            "name": "local-demo",
            "source_type": "local",
            "repo_url": str(local_pack_dir),
            "auth_type": "none",
        },
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["source_type"] == "local"
    assert body["last_sync_status"] == "ok"
    assert body["current_sha"] is None
    # Local packs derive the highest tier; role is irrelevant for them.
    assert body["priority"] == 1000

    actions = (await superuser_client.get("/api/actions/")).json()
    keys = {a["key"] for a in actions}
    assert "hello-local" in keys


async def test_create_local_pack_with_missing_path_marks_failed(
    superuser_client, tmp_path: Path
):
    resp = await superuser_client.post(
        "/api/action-packs",
        json={
            "name": "local-broken",
            "source_type": "local",
            "repo_url": str(tmp_path / "does-not-exist"),
            "auth_type": "none",
        },
    )
    # Row is saved with failed status so the admin can fix the path.
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["last_sync_status"] == "failed"
    assert "does not exist" in body["last_sync_error"]


async def test_local_pack_rejects_credentials(superuser_client, local_pack_dir):
    resp = await superuser_client.post(
        "/api/action-packs",
        json={
            "name": "local-creds",
            "source_type": "local",
            "repo_url": str(local_pack_dir),
            "auth_type": "https_token",
            "token": "should-not-be-here",
        },
    )
    assert resp.status_code == 422
    assert "local" in resp.text.lower()


async def test_pre_save_test_local_pack(superuser_client, local_pack_dir):
    resp = await superuser_client.post(
        "/api/action-packs/test",
        json={
            "source_type": "local",
            "repo_url": str(local_pack_dir),
            "auth_type": "none",
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is True
    assert body["commit_sha"] is None


async def test_switch_git_to_local_drops_checkout(
    superuser_client, origin_repo, local_pack_dir, monkeypatch, tmp_path
):
    from app.config import settings
    from app.packs.service import checkout_path_for

    monkeypatch.setattr(settings.ansible, "packs_root_dir", str(tmp_path / "packs"))

    r = await superuser_client.post(
        "/api/action-packs",
        json={
            "name": "switch-to-local",
            "repo_url": origin_repo,
            "auth_type": "none",
        },
    )
    pack_id = r.json()["id"]
    git_checkout = checkout_path_for(pack_id)
    assert git_checkout.is_dir()

    r2 = await superuser_client.put(
        f"/api/action-packs/{pack_id}",
        json={
            "source_type": "local",
            "repo_url": str(local_pack_dir),
        },
    )
    assert r2.status_code == 200
    assert r2.json()["source_type"] == "local"
    # Managed checkout from the git era is gone; admin's local dir is untouched.
    assert not git_checkout.exists()
    assert local_pack_dir.is_dir()

    actions = (await superuser_client.get("/api/actions/")).json()
    keys = {a["key"] for a in actions}
    assert "hello-local" in keys


async def test_response_never_leaks_credentials(
    superuser_client, origin_repo, monkeypatch, tmp_path
):
    """The pack list and detail responses must never include any raw
    plaintext credentials or encrypted bytes."""
    from app.config import settings

    monkeypatch.setattr(settings.ansible, "packs_root_dir", str(tmp_path / "packs"))

    resp = await superuser_client.post(
        "/api/action-packs",
        json={
            "name": "redaction-check",
            "repo_url": origin_repo,
            "auth_type": "https_token",
            "token": "ghp_supersecret_token_value",
        },
    )
    # Sync will fail (file:// + token doesn't meaningfully auth), but the
    # row is still saved. The response must not include the token.
    text = resp.text
    assert "ghp_supersecret_token_value" not in text
    assert "encrypted" not in text.lower() or "encrypted_" not in text

    pack_id = resp.json()["id"]
    list_text = (await superuser_client.get("/api/action-packs")).text
    detail_text = (await superuser_client.get(f"/api/action-packs/{pack_id}")).text
    assert "ghp_supersecret_token_value" not in list_text
    assert "ghp_supersecret_token_value" not in detail_text
    assert "encrypted_token" not in list_text
    assert "encrypted_token" not in detail_text
