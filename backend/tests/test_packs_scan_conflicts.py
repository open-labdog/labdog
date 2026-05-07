"""Tests for ``app.packs.scan_conflicts.annotate_scan``.

Pure annotation logic over an in-memory ``ScanResult``. The
``ACTION_REGISTRY`` is patched directly so the tests don't depend on
which packs are loaded by the runtime; the resolver just needs to
read whatever's in there at call time.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from app.packs.repo_scanner import DetectedPack, ScanResult
from app.packs.scan_conflicts import (
    AnnotatedScanResult,
    KeyConflict,
    KeyOwner,
    annotate_scan,
)

pytestmark = pytest.mark.asyncio


def _pack(path: str, *keys: str) -> DetectedPack:
    return DetectedPack(
        path=path,
        name=path or "root",
        contributed_keys=keys,
        pack_yml_present=True,
    )


@pytest.fixture
def empty_registry():
    """ACTION_REGISTRY emptied for the duration of one test."""
    with patch("app.actions.registry.ACTION_REGISTRY", {}):
        yield


@pytest.fixture
def registry_with_bundled_linux_upgrade():
    """ACTION_REGISTRY populated with one bundled action."""
    from app.actions.registry import BUNDLED_PACK_NAME
    from app.actions.types import ActionDefinition

    fake_defn = ActionDefinition(
        key="linux-upgrade",
        name="bundled linux-upgrade",
        description="",
        icon="",
        playbook_path=None,  # type: ignore[arg-type]
        version="1.0",
        estimated_duration="",
        pack_name=BUNDLED_PACK_NAME,
    )
    with patch("app.actions.registry.ACTION_REGISTRY", {"linux-upgrade": fake_defn}):
        yield


@pytest.fixture
def registry_with_db_pack_action():
    """ACTION_REGISTRY populated with one DB-pack action."""
    from app.actions.types import ActionDefinition

    fake_defn = ActionDefinition(
        key="custom-thing",
        name="custom-thing",
        description="",
        icon="",
        playbook_path=None,  # type: ignore[arg-type]
        version="1.0",
        estimated_duration="",
        pack_name="my-installed-pack",
    )
    with patch("app.actions.registry.ACTION_REGISTRY", {"custom-thing": fake_defn}):
        yield


# ---------------------------------------------------------------------------
# existing_key_winners
# ---------------------------------------------------------------------------


async def test_annotate_with_no_existing_keys_returns_empty(db, empty_registry):
    result = ScanResult(packs=[_pack("actions/foo", "novel-key")])
    annotated = await annotate_scan(db, result)
    assert isinstance(annotated, AnnotatedScanResult)
    assert annotated.existing_key_winners == {}
    assert annotated.intra_repo_key_conflicts == []


async def test_annotate_marks_bundled_collision(db, registry_with_bundled_linux_upgrade):
    """A scanned pack contributing the same key as the bundled pack
    is flagged with ``source="bundled"`` in existing_key_winners."""
    result = ScanResult(packs=[_pack("actions/upgrade", "linux-upgrade")])
    annotated = await annotate_scan(db, result)
    assert "linux-upgrade" in annotated.existing_key_winners
    owner = annotated.existing_key_winners["linux-upgrade"]
    assert owner.source == "bundled"
    assert owner.pack_id is None


async def test_annotate_marks_db_pack_collision(db, registry_with_db_pack_action):
    result = ScanResult(packs=[_pack("packs/custom", "custom-thing")])
    annotated = await annotate_scan(db, result)
    owner = annotated.existing_key_winners["custom-thing"]
    assert owner.source == "db_pack"
    assert owner.pack_name == "my-installed-pack"


async def test_annotate_only_decorates_keys_actually_scanned(
    db, registry_with_bundled_linux_upgrade
):
    """Bundled pack has ``linux-upgrade`` but the scan didn't see it —
    the winner map shouldn't include keys we don't care about."""
    result = ScanResult(packs=[_pack("actions/foo", "completely-different")])
    annotated = await annotate_scan(db, result)
    assert annotated.existing_key_winners == {}


# ---------------------------------------------------------------------------
# intra_repo_key_conflicts
# ---------------------------------------------------------------------------


async def test_annotate_intra_repo_duplicate_yields_conflict(db, empty_registry):
    """Two packs in the same scan claiming the same key produces one
    KeyConflict listing both contributing packs in sorted order."""
    result = ScanResult(
        packs=[
            _pack("actions/a", "shared-key"),
            _pack("actions/b", "shared-key"),
        ]
    )
    annotated = await annotate_scan(db, result)
    assert len(annotated.intra_repo_key_conflicts) == 1
    conflict = annotated.intra_repo_key_conflicts[0]
    assert conflict == KeyConflict(key="shared-key", contributing_packs=("actions/a", "actions/b"))


async def test_annotate_unique_keys_yield_no_conflicts(db, empty_registry):
    result = ScanResult(
        packs=[
            _pack("actions/a", "key-a"),
            _pack("actions/b", "key-b"),
        ]
    )
    annotated = await annotate_scan(db, result)
    assert annotated.intra_repo_key_conflicts == []


async def test_annotate_three_way_conflict_lists_all_contributors(db, empty_registry):
    result = ScanResult(
        packs=[
            _pack("actions/c", "shared"),
            _pack("actions/a", "shared"),
            _pack("actions/b", "shared"),
        ]
    )
    annotated = await annotate_scan(db, result)
    assert len(annotated.intra_repo_key_conflicts) == 1
    conflict = annotated.intra_repo_key_conflicts[0]
    # Contributors are sorted, so order is deterministic.
    assert conflict.contributing_packs == ("actions/a", "actions/b", "actions/c")


async def test_annotate_multiple_independent_conflicts_each_listed(db, empty_registry):
    result = ScanResult(
        packs=[
            _pack("actions/a", "shared-1", "shared-2"),
            _pack("actions/b", "shared-1", "shared-2"),
        ]
    )
    annotated = await annotate_scan(db, result)
    keys = sorted(c.key for c in annotated.intra_repo_key_conflicts)
    assert keys == ["shared-1", "shared-2"]


async def test_annotate_intra_repo_and_existing_collision_independent(
    db, registry_with_bundled_linux_upgrade
):
    """A scanned pack can both collide with bundled AND have an
    intra-repo duplicate. Both annotations must surface."""
    result = ScanResult(
        packs=[
            _pack("actions/a", "linux-upgrade"),
            _pack("actions/b", "linux-upgrade"),
        ]
    )
    annotated = await annotate_scan(db, result)
    assert "linux-upgrade" in annotated.existing_key_winners
    assert annotated.existing_key_winners["linux-upgrade"].source == "bundled"
    assert len(annotated.intra_repo_key_conflicts) == 1
    assert annotated.intra_repo_key_conflicts[0].key == "linux-upgrade"


# ---------------------------------------------------------------------------
# dataclass shape sanity
# ---------------------------------------------------------------------------


async def test_annotated_result_is_frozen(db, empty_registry):
    annotated = await annotate_scan(db, ScanResult())
    try:
        annotated.existing_key_winners = {}  # type: ignore[misc]
    except Exception:
        return
    raise AssertionError("AnnotatedScanResult should be frozen")


async def test_key_owner_is_frozen():
    owner = KeyOwner(key="x", source="bundled", pack_name="bundled")
    try:
        owner.key = "mutated"  # type: ignore[misc]
    except Exception:
        return
    raise AssertionError("KeyOwner should be frozen")
