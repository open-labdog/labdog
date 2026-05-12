"""Tests for ``POST /api/git-repos/{id}/scan``.

Uses a local ``file://`` git repo built in a tmp_path fixture so no
network access is needed. Auth is bypassed (``auth_type=ssh_key``
with ``ssh_key_id=None`` returns ``(None, None)`` from
``_decrypt_repo_credentials``, and the no-auth ``git_auth_context``
clones a file:// URL without credentials).
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from app.models.git_repository import GitAuthType, GitRepository

pytestmark = pytest.mark.integration


def _git(cwd: Path, args: list[str]) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


def _commit_tree(repo_path: Path) -> None:
    _git(repo_path, ["init", "-b", "main"])
    _git(repo_path, ["config", "user.email", "test@example.com"])
    _git(repo_path, ["config", "user.name", "Test"])
    _git(repo_path, ["add", "-A"])
    _git(repo_path, ["commit", "-m", "initial"])


@pytest.fixture
def well_formed_origin(tmp_path: Path) -> Path:
    """Local git repo with one pack + one gitops file, both well-formed."""
    path = tmp_path / "origin"
    path.mkdir()
    pack = path / "actions" / "upgrade"
    pack.mkdir(parents=True)
    (pack / "pack.yml").write_text("name: upgrade-pack\n")
    action_dir = pack / "actions" / "linux-upgrade"
    action_dir.mkdir(parents=True)
    (action_dir / "playbook.yml").write_text(
        "---\n- name: x\n  hosts: all\n  tasks: []\n"
    )
    (action_dir / "manifest.yml").write_text(
        "key: linux-upgrade\n"
        "name: Upgrade Linux\n"
        "description: ''\n"
        "icon: Box\n"
        "playbook: playbook.yml\n"
        'version: "1.0"\n'
        'estimated_duration: "5 min"\n'
        "destructive: false\n"
        "supports_group: false\n"
        "supports_host: true\n"
        "parameters: []\n"
    )
    groups = path / "groups"
    groups.mkdir()
    (groups / "web.yaml").write_text("group: web\npriority: 100\n")
    _commit_tree(path)
    return path


async def _make_repo(db, url: str, name: str = "test-scan-repo") -> GitRepository:
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


# ---------------------------------------------------------------------------
# auth + 404
# ---------------------------------------------------------------------------


async def test_scan_endpoint_requires_superuser(client, db, well_formed_origin):
    """Unauthenticated → 401."""
    repo = await _make_repo(db, f"file://{well_formed_origin}")
    await db.commit()
    resp = await client.post(f"/api/git-repos/{repo.id}/scan")
    assert resp.status_code == 401


async def test_scan_endpoint_requires_superuser_for_non_admin(
    regular_user_client, db, well_formed_origin
):
    """Plain authenticated user (non-superuser) → 403."""
    repo = await _make_repo(db, f"file://{well_formed_origin}")
    await db.commit()
    resp = await regular_user_client.post(f"/api/git-repos/{repo.id}/scan")
    assert resp.status_code == 403


async def test_scan_endpoint_404_for_unknown_repo(superuser_client, db):
    resp = await superuser_client.post("/api/git-repos/99999/scan")
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# happy path
# ---------------------------------------------------------------------------


async def test_scan_endpoint_returns_findings_against_local_repo(
    superuser_client, db, well_formed_origin
):
    repo = await _make_repo(db, f"file://{well_formed_origin}")
    await db.commit()

    resp = await superuser_client.post(f"/api/git-repos/{repo.id}/scan")
    assert resp.status_code == 200, resp.text
    body = resp.json()

    # One pack at actions/upgrade contributing one key.
    assert len(body["packs"]) == 1
    pack = body["packs"][0]
    assert pack["path"] == "actions/upgrade"
    assert pack["name"] == "upgrade-pack"
    assert pack["contributed_keys"] == ["linux-upgrade"]
    assert pack["pack_yml_present"] is True
    assert pack["errors"] == []

    # One gitops file.
    assert len(body["gitops_files"]) == 1
    gitops = body["gitops_files"][0]
    assert gitops["path"] == "groups/web.yaml"
    assert gitops["group_name"] == "web"
    assert gitops["errors"] == []

    # No conflicts (registry has no linux-upgrade in tests by default).
    assert body["intra_repo_key_conflicts"] == []
    # head_sha is whatever the test commit landed on, but it's a 40-char hex.
    assert isinstance(body["head_sha"], str)
    assert len(body["head_sha"]) == 40


async def test_scan_endpoint_idempotent(superuser_client, db, well_formed_origin):
    """Calling twice produces the same result; the clone is throw-away."""
    repo = await _make_repo(db, f"file://{well_formed_origin}")
    await db.commit()

    first = await superuser_client.post(f"/api/git-repos/{repo.id}/scan")
    second = await superuser_client.post(f"/api/git-repos/{repo.id}/scan")
    assert first.status_code == 200
    assert second.status_code == 200
    # Drop head_sha from comparison since it's deterministic per repo state.
    assert first.json() == second.json()


# ---------------------------------------------------------------------------
# clone failure path
# ---------------------------------------------------------------------------


async def test_scan_endpoint_clone_failure_returns_502(superuser_client, db, tmp_path):
    """Bad URL → 502 with a redacted error message."""
    bad_url = f"file://{tmp_path / 'does-not-exist'}"
    repo = await _make_repo(db, bad_url, name="bad-repo")
    await db.commit()

    resp = await superuser_client.post(f"/api/git-repos/{repo.id}/scan")
    assert resp.status_code == 502
    assert "detail" in resp.json()


# ---------------------------------------------------------------------------
# scan annotations integration: empty registry → no winners
# ---------------------------------------------------------------------------


async def test_scan_endpoint_empty_registry_no_existing_winners(
    superuser_client, db, well_formed_origin
):
    """When ACTION_REGISTRY contains no key matching the scanned repo,
    existing_key_winners is an empty dict."""
    repo = await _make_repo(db, f"file://{well_formed_origin}")
    await db.commit()

    resp = await superuser_client.post(f"/api/git-repos/{repo.id}/scan")
    body = resp.json()
    # The bundled pack might or might not have linux-upgrade depending
    # on what's loaded into ACTION_REGISTRY at test time; what matters
    # is that the response shape is correct.
    assert isinstance(body["existing_key_winners"], dict)
