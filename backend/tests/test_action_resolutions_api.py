"""Tests for the reorder + action-resolutions endpoints.

Exercises the freeze-on-fresh-conflict behaviour, the reorder endpoint
that rewrites ``ActionPack.position``, and the per-key resolution CRUD
that drives the conflict UI on ``/action-packs``.

Local file:// origin pattern matches ``test_action_packs_api.py`` so
no network access is required.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from sqlalchemy import select

from app.models.git_repository import GitAuthType, GitRepository
from app.packs.models import ActionResolution


def _git(cwd: Path, args: list[str]) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


def _make_local_pack(
    root: Path,
    *,
    action_key: str,
    description: str = "demo",
) -> Path:
    action_dir = root / "actions" / action_key
    action_dir.mkdir(parents=True)
    (action_dir / "playbook.yml").write_text(
        f"---\n- name: {action_key}\n  hosts: all\n  tasks: []\n"
    )
    (action_dir / "manifest.yml").write_text(
        f"key: {action_key}\n"
        f"name: {action_key}\n"
        f"description: {description}\n"
        "icon: Box\n"
        "playbook: playbook.yml\n"
        'version: "1.0"\n'
        'estimated_duration: "1 min"\n'
    )
    return root


@pytest.fixture
def local_pack_a(tmp_path) -> Path:
    return _make_local_pack(tmp_path / "pack-a", action_key="hello")


@pytest.fixture
def local_pack_b(tmp_path) -> Path:
    return _make_local_pack(tmp_path / "pack-b", action_key="hello")


@pytest.fixture
async def two_local_packs(superuser_client, local_pack_a, local_pack_b):
    """Two enabled local packs both contributing the 'hello' action."""
    r1 = await superuser_client.post(
        "/api/action-packs",
        json={"name": "pack-a", "source_type": "local", "local_path": str(local_pack_a)},
    )
    assert r1.status_code == 201, r1.text
    r2 = await superuser_client.post(
        "/api/action-packs",
        json={"name": "pack-b", "source_type": "local", "local_path": str(local_pack_b)},
    )
    assert r2.status_code == 201, r2.text
    return r1.json(), r2.json()


# ---------------------------------------------------------------------------
# Reorder endpoint
# ---------------------------------------------------------------------------


async def test_reorder_rewrites_positions_top_first_wins(superuser_client, two_local_packs):
    pack_a, pack_b = two_local_packs

    # By default, the freeze pins pack-a (the previous winner) when
    # pack-b joined. Reorder so pack-b is first (top of table) — the
    # request includes both pack ids in the desired order.
    listing = (await superuser_client.get("/api/action-packs")).json()
    seeded_ids = [p["id"] for p in listing if p["name"] == "labdog-playbooks"]
    desired = [pack_b["id"], pack_a["id"], *seeded_ids]
    r = await superuser_client.post(
        "/api/action-packs/reorder",
        json={"pack_ids": desired},
    )
    assert r.status_code == 200, r.text
    assert r.json() == {"reordered": len(desired)}

    listing_after = (await superuser_client.get("/api/action-packs")).json()
    after = {p["id"]: p["position"] for p in listing_after}
    # First in desired list gets the highest position number.
    assert after[pack_b["id"]] > after[pack_a["id"]]


async def test_reorder_rejects_set_mismatch(superuser_client, two_local_packs):
    pack_a, _pack_b = two_local_packs
    # Submit only one of the two packs — the server requires the full set.
    r = await superuser_client.post(
        "/api/action-packs/reorder",
        json={"pack_ids": [pack_a["id"]]},
    )
    assert r.status_code == 409
    detail = r.json()["detail"]
    assert detail["kind"] == "reorder_set_mismatch"
    assert detail["missing_pack_ids"]


async def test_reorder_rejects_unknown_id(superuser_client, two_local_packs):
    pack_a, pack_b = two_local_packs
    listing = (await superuser_client.get("/api/action-packs")).json()
    all_ids = [p["id"] for p in listing]
    r = await superuser_client.post(
        "/api/action-packs/reorder",
        json={"pack_ids": [*all_ids, 99999]},
    )
    assert r.status_code == 409
    detail = r.json()["detail"]
    assert 99999 in detail["unknown_pack_ids"]


async def test_reorder_requires_superuser(regular_user_client):
    r = await regular_user_client.post(
        "/api/action-packs/reorder",
        json={"pack_ids": [1]},
    )
    assert r.status_code in (401, 403)


# ---------------------------------------------------------------------------
# Freeze-on-fresh-conflict behaviour
# ---------------------------------------------------------------------------


async def test_fresh_conflict_writes_freeze_resolution_row(superuser_client, two_local_packs, db):
    """When pack-b is added contributing the same key as pack-a, the
    rebuild auto-creates an ``action_resolution`` row pinning the
    previous winner (pack-a) so behaviour does not silently flip."""
    pack_a, pack_b = two_local_packs
    rows = (await db.execute(select(ActionResolution))).scalars().all()
    by_key = {r.action_key: r for r in rows}
    assert "hello" in by_key
    assert by_key["hello"].pack_id == pack_a["id"]
    assert by_key["hello"].decided_by_user_id is None  # auto-pinned, not operator-driven


async def test_contested_keys_view_surfaces_conflict(superuser_client, two_local_packs):
    pack_a, pack_b = two_local_packs
    r = await superuser_client.get("/api/action-resolutions")
    assert r.status_code == 200
    rows = {row["action_key"]: row for row in r.json()}
    assert "hello" in rows
    row = rows["hello"]
    assert row["current_winner"]["pack_id"] == pack_a["id"]
    candidate_ids = {c["pack_id"] for c in row["candidates"]}
    assert candidate_ids == {pack_a["id"], pack_b["id"]}
    assert row["resolution"] is not None
    assert row["resolution"]["pack_id"] == pack_a["id"]
    assert row["is_frozen"] is True  # frozen against the position-based default


# ---------------------------------------------------------------------------
# Resolution upsert / delete
# ---------------------------------------------------------------------------


async def test_upsert_resolution_changes_winner(superuser_client, two_local_packs):
    pack_a, pack_b = two_local_packs
    r = await superuser_client.put(
        "/api/action-resolutions/hello",
        json={"pack_id": pack_b["id"]},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["current_winner"]["pack_id"] == pack_b["id"]
    assert body["resolution"]["pack_id"] == pack_b["id"]
    assert body["is_frozen"] is False  # operator-chosen winner = position default

    actions = (await superuser_client.get("/api/actions/")).json()
    hello = next(a for a in actions if a["key"] == "hello")
    assert hello["pack_name"] == "pack-b"


async def test_upsert_rejects_pack_not_contributor(superuser_client, two_local_packs, git_repo_row):
    """Pinning a pack that doesn't contribute the key is rejected."""
    r = await superuser_client.put(
        "/api/action-resolutions/hello",
        json={"pack_id": git_repo_row.id + 9999},
    )
    assert r.status_code in (404, 409)


async def test_delete_resolution_falls_back_to_default(superuser_client, two_local_packs):
    pack_a, pack_b = two_local_packs
    # Drop the freeze. Default = highest position. pack-b was inserted
    # second so it has the higher position — it wins by default.
    r = await superuser_client.delete("/api/action-resolutions/hello")
    assert r.status_code == 204

    rows = (await superuser_client.get("/api/action-resolutions")).json()
    hello = next(row for row in rows if row["action_key"] == "hello")
    assert hello["resolution"] is None
    assert hello["current_winner"]["pack_id"] == pack_b["id"]


async def test_delete_unknown_resolution_returns_404(superuser_client):
    r = await superuser_client.delete("/api/action-resolutions/no-such-key")
    assert r.status_code == 404


async def test_pack_delete_cascades_resolution_row(superuser_client, two_local_packs, db):
    pack_a, pack_b = two_local_packs
    # Delete pack-a (the one pinned by the freeze).
    r = await superuser_client.delete(f"/api/action-packs/{pack_a['id']}")
    assert r.status_code == 204

    rows = (await db.execute(select(ActionResolution))).scalars().all()
    by_key = {row.action_key: row for row in rows}
    # Either CASCADE removed the row, or the registry rebuild's
    # stale-resolution sweep dropped it. Either way, no row left.
    assert "hello" not in by_key

    # pack-b is now the sole contributor — uncontested.
    contested = (await superuser_client.get("/api/action-resolutions")).json()
    assert all(c["action_key"] != "hello" for c in contested)


# ---------------------------------------------------------------------------
# Auth on the resolutions router
# ---------------------------------------------------------------------------


async def test_resolutions_router_requires_superuser(regular_user_client):
    r1 = await regular_user_client.get("/api/action-resolutions")
    r2 = await regular_user_client.put("/api/action-resolutions/hello", json={"pack_id": None})
    r3 = await regular_user_client.delete("/api/action-resolutions/hello")
    for r in (r1, r2, r3):
        assert r.status_code in (401, 403)


# ---------------------------------------------------------------------------
# git_repo_row fixture (for the not-contributor test above)
# ---------------------------------------------------------------------------


@pytest.fixture
async def git_repo_row(db, tmp_path: Path) -> GitRepository:
    origin = tmp_path / "origin-resolution"
    origin.mkdir()
    _git(origin, ["init", "-b", "main"])
    _git(origin, ["config", "user.email", "test@example.com"])
    _git(origin, ["config", "user.name", "Test"])
    (origin / "actions").mkdir()
    (origin / "actions" / "x.yml").write_text("---\n- name: x\n  hosts: all\n  tasks: []\n")
    _git(origin, ["add", "-A"])
    _git(origin, ["commit", "-m", "init"])
    repo = GitRepository(
        name="resolution-test-repo",
        url=f"file://{origin}",
        branch="main",
        auth_type=GitAuthType.ssh_key,
        ssh_key_id=None,
    )
    db.add(repo)
    await db.flush()
    return repo
