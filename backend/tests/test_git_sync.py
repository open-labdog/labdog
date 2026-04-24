"""Tests for the git-backed pack sync.

We build real local git repos in tmp_path and point the sync at them with
``file://`` URLs. This exercises the full subprocess path — clone, fetch,
reset, origin-mismatch recovery — without touching the network.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from app.actions.git_sync import GitSyncError, sync_remote_pack


def _init_origin_repo(path: Path, files: dict[str, str], branch: str = "main") -> None:
    """Create a bare-ish origin repo with initial commit on *branch*."""
    path.mkdir(parents=True)
    _git(path, ["init", "-b", branch])
    _git(path, ["config", "user.email", "test@example.com"])
    _git(path, ["config", "user.name", "Test"])
    for rel, body in files.items():
        target = path / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(body)
    _git(path, ["add", "-A"])
    _git(path, ["commit", "-m", "initial"])


def _git(cwd: Path, args: list[str]) -> None:
    subprocess.run(
        ["git", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
    )


def _add_commit(path: Path, rel: str, body: str, msg: str) -> None:
    (path / rel).write_text(body)
    _git(path, ["add", "-A"])
    _git(path, ["commit", "-m", msg])


def test_sync_clones_when_path_is_missing(tmp_path: Path):
    origin = tmp_path / "origin"
    _init_origin_repo(origin, {"actions/demo.yml": "x"})
    dest = tmp_path / "work" / "pack"

    sync_remote_pack(repo=f"file://{origin}", ref="main", path=dest)

    assert (dest / "actions" / "demo.yml").read_text() == "x"
    assert (dest / ".git").is_dir()


def test_sync_pulls_updates_on_existing_checkout(tmp_path: Path):
    origin = tmp_path / "origin"
    _init_origin_repo(origin, {"actions/demo.yml": "old"})
    dest = tmp_path / "pack"

    sync_remote_pack(repo=f"file://{origin}", ref="main", path=dest)
    assert (dest / "actions" / "demo.yml").read_text() == "old"

    _add_commit(origin, "actions/demo.yml", "new", "update")

    sync_remote_pack(repo=f"file://{origin}", ref="main", path=dest)
    assert (dest / "actions" / "demo.yml").read_text() == "new"


def test_sync_reclones_when_origin_url_changes(tmp_path: Path):
    first = tmp_path / "first"
    second = tmp_path / "second"
    _init_origin_repo(first, {"marker.txt": "first"})
    _init_origin_repo(second, {"marker.txt": "second"})
    dest = tmp_path / "pack"

    sync_remote_pack(repo=f"file://{first}", ref="main", path=dest)
    assert (dest / "marker.txt").read_text() == "first"

    sync_remote_pack(repo=f"file://{second}", ref="main", path=dest)
    assert (dest / "marker.txt").read_text() == "second"


def test_sync_reclones_when_path_is_not_a_repo(tmp_path: Path):
    origin = tmp_path / "origin"
    _init_origin_repo(origin, {"marker.txt": "from-origin"})
    dest = tmp_path / "pack"
    dest.mkdir()
    (dest / "marker.txt").write_text("stale-junk")

    sync_remote_pack(repo=f"file://{origin}", ref="main", path=dest)
    assert (dest / "marker.txt").read_text() == "from-origin"
    assert (dest / ".git").is_dir()


def test_sync_raises_git_sync_error_on_bad_repo(tmp_path: Path):
    dest = tmp_path / "pack"
    with pytest.raises(GitSyncError):
        sync_remote_pack(
            repo=f"file://{tmp_path / 'does-not-exist'}",
            ref="main",
            path=dest,
        )
