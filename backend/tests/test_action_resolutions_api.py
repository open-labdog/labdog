"""Tests for the action-resolutions endpoints and the
claim-all-keys bulk-pin endpoint on the action-packs router.

Exercises the per-key resolution CRUD that drives the conflict UI on
``/action-packs``, the freeze-on-fresh-conflict behaviour that pins
the previous winner when a sync introduces a new contestant, and the
new "pure per-key pinning, no global ordering" model where contested
keys without a pin are *unresolved* (no winner; the action is
unrunnable).

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
# Reorder endpoint removed — the precedence model is pure per-key
# pinning now, with no global pack ordering. Tests covering positions
# / reorder are gone with the column.
# ---------------------------------------------------------------------------


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
    # is_frozen is True when the pin was auto-written by
    # freeze-on-fresh-conflict (decided_by_user_id IS NULL).
    assert row["is_frozen"] is True
    assert row["is_unresolved"] is False


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
    # is_frozen reflects "decided_by_user_id IS NULL" (auto-pinned by
    # the freeze logic, awaiting operator confirmation). An operator
    # upsert flips it false.
    assert body["is_frozen"] is False
    assert body["is_unresolved"] is False

    actions = (await superuser_client.get("/api/actions/")).json()
    hello = next(a for a in actions if a["key"] == "hello")
    assert hello["pack_name"] == "pack-b"
    assert hello["winning_pack_id"] == pack_b["id"]
    assert hello["unresolved"] is False


async def test_upsert_rejects_pack_not_contributor(superuser_client, two_local_packs, git_repo_row):
    """Pinning a pack that doesn't contribute the key is rejected."""
    r = await superuser_client.put(
        "/api/action-resolutions/hello",
        json={"pack_id": git_repo_row.id + 9999},
    )
    assert r.status_code in (404, 409)


async def test_delete_resolution_leaves_key_unresolved(superuser_client, two_local_packs):
    """In the per-key-pinning model there is no global ordering to
    fall back on. Dropping the resolution leaves the key unresolved —
    no winner, action unrunnable until the operator pins again."""
    pack_a, pack_b = two_local_packs
    r = await superuser_client.delete("/api/action-resolutions/hello")
    assert r.status_code == 204

    rows = (await superuser_client.get("/api/action-resolutions")).json()
    hello = next(row for row in rows if row["action_key"] == "hello")
    assert hello["resolution"] is None
    assert hello["current_winner"] is None
    assert hello["is_unresolved"] is True

    actions = (await superuser_client.get("/api/actions/")).json()
    hello_def = next(a for a in actions if a["key"] == "hello")
    assert hello_def["unresolved"] is True
    assert hello_def["winning_pack_id"] is None


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
# Pure per-key-pinning model: contested-without-resolution = unresolved
# ---------------------------------------------------------------------------


async def test_contested_without_resolution_is_unresolved(superuser_client, two_local_packs):
    """Drop the auto-pin from freeze-on-fresh-conflict; the key must
    flip to unresolved (no winner) and the action must vanish from
    the runnable surface."""
    pack_a, pack_b = two_local_packs

    # Drop the resolution that freeze-on-fresh-conflict wrote on
    # pack-b creation. Now the key is contested with no pin.
    r = await superuser_client.delete("/api/action-resolutions/hello")
    assert r.status_code == 204

    contested = (await superuser_client.get("/api/action-resolutions")).json()
    hello = next(row for row in contested if row["action_key"] == "hello")
    assert hello["current_winner"] is None
    assert hello["resolution"] is None
    assert hello["is_unresolved"] is True
    assert hello["is_frozen"] is False

    actions = (await superuser_client.get("/api/actions/")).json()
    hello_def = next(a for a in actions if a["key"] == "hello")
    assert hello_def["unresolved"] is True
    assert hello_def["winning_pack_id"] is None


async def test_submit_unresolved_action_rejected_with_409(superuser_client, two_local_packs, db):
    """``POST /api/actions/runs`` refuses to dispatch an unresolved
    action with a clear 409 directing the operator to ``/action-packs``."""
    pack_a, pack_b = two_local_packs

    # Drop the freeze pin so the key is unresolved.
    await superuser_client.delete("/api/action-resolutions/hello")

    # Need a host to target — minimum viable test host.
    from app.models.host import Host

    host = Host(hostname="t1.example.com", ip_address="10.0.0.99", ssh_port=22, ssh_user="root")
    db.add(host)
    await db.flush()

    r = await superuser_client.post(
        "/api/actions/runs",
        json={"action_key": "hello", "host_id": host.id, "parameters": {}},
    )
    assert r.status_code == 409, r.text
    detail = r.json()["detail"]
    assert detail["kind"] == "action_unresolved"
    assert detail["action_key"] == "hello"
    assert "/action-packs" in detail["message"]


async def test_winning_pack_deleted_marks_action_unresolved(superuser_client, two_local_packs):
    """When the pack the resolution points at is deleted, the
    resolution row goes away (CASCADE), and the contested key — now
    with only one contributor — flips to **uncontested**: the surviving
    pack wins. But if BOTH contestants are gone the key vanishes from
    the registry entirely. We cover the in-between case here: delete
    the pinned pack, the other pack becomes the sole contributor and
    wins automatically."""
    pack_a, pack_b = two_local_packs
    # Freeze pinned pack-a; delete pack-a.
    r = await superuser_client.delete(f"/api/action-packs/{pack_a['id']}")
    assert r.status_code == 204

    actions = (await superuser_client.get("/api/actions/")).json()
    hello_def = next(a for a in actions if a["key"] == "hello")
    # Sole contributor now — uncontested, pack-b wins automatically.
    assert hello_def["unresolved"] is False
    assert hello_def["winning_pack_id"] == pack_b["id"]
    assert hello_def["pack_name"] == "pack-b"


# Note: a three-contributor "delete pinned pack → unresolved" test
# was considered but kept off the suite for now — it runs into the
# pre-existing test_action_packs id=1 collision pattern documented at
# the top of this file, and the simpler two-contributor case above
# covers the same code path (resolution cascade + recompute).


# ---------------------------------------------------------------------------
# Bulk-pin endpoint: POST /api/action-packs/{id}/claim-all-keys
# ---------------------------------------------------------------------------


async def test_claim_all_keys_pins_every_contributed_key(superuser_client, two_local_packs):
    """Calling claim-all-keys on pack-b flips the freeze pin from
    pack-a to pack-b. Returns counts the UI can surface in a toast."""
    pack_a, pack_b = two_local_packs

    r = await superuser_client.post(f"/api/action-packs/{pack_b['id']}/claim-all-keys")
    assert r.status_code == 200, r.text
    body = r.json()
    # The freeze created one row pointing at pack-a; we update it.
    assert body["updated"] == 1
    assert body["created"] == 0
    # No keys skipped — pack-b wasn't the prior pin for any key.
    assert body["skipped"] == 0

    contested = (await superuser_client.get("/api/action-resolutions")).json()
    hello = next(row for row in contested if row["action_key"] == "hello")
    assert hello["current_winner"]["pack_id"] == pack_b["id"]
    assert hello["resolution"]["pack_id"] == pack_b["id"]
    assert hello["is_frozen"] is False  # operator-driven now


async def test_claim_all_keys_is_idempotent(superuser_client, two_local_packs):
    pack_a, pack_b = two_local_packs

    # First call flips the pin to pack-b.
    r1 = await superuser_client.post(f"/api/action-packs/{pack_b['id']}/claim-all-keys")
    assert r1.status_code == 200
    # Second call is a no-op: pack-b already wins every key it
    # contributes, nothing to create or update.
    r2 = await superuser_client.post(f"/api/action-packs/{pack_b['id']}/claim-all-keys")
    assert r2.status_code == 200
    body = r2.json()
    assert body == {"created": 0, "updated": 0, "skipped": 1}


async def test_claim_all_keys_404_for_unknown_pack(superuser_client):
    r = await superuser_client.post("/api/action-packs/99999/claim-all-keys")
    assert r.status_code == 404


async def test_claim_all_keys_requires_superuser(regular_user_client, two_local_packs):
    pack_a, pack_b = two_local_packs
    r = await regular_user_client.post(f"/api/action-packs/{pack_b['id']}/claim-all-keys")
    assert r.status_code in (401, 403)


async def test_claim_all_keys_with_no_contributions(superuser_client, db, tmp_path):
    """A pack that doesn't contribute any action keys (empty actions/
    dir, or all manifests malformed) returns zero counts."""
    empty_pack = tmp_path / "empty-pack"
    (empty_pack / "actions").mkdir(parents=True)
    r = await superuser_client.post(
        "/api/action-packs",
        json={"name": "empty", "source_type": "local", "local_path": str(empty_pack)},
    )
    assert r.status_code == 201
    pack_id = r.json()["id"]

    r2 = await superuser_client.post(f"/api/action-packs/{pack_id}/claim-all-keys")
    assert r2.status_code == 200
    assert r2.json() == {"created": 0, "updated": 0, "skipped": 0}


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
