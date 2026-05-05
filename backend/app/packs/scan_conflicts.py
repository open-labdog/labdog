"""Annotate a ``ScanResult`` with conflict info: same-key collisions
against the live ``ACTION_REGISTRY`` (so the wizard can surface a
per-key radio for the operator) and intra-repo collisions where two
scanned packs in the same tree contribute the same key (a hard error
the operator must resolve before activation).

The registry's freshness is whatever the orchestrator ran into last
— we don't force a reload here. A pack added five seconds ago that
hasn't been picked up yet may be missing from ``existing_key_winners``;
that's acceptable: the activation endpoint re-validates against the
authoritative DB rows and will catch any drift.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from typing import Literal

from sqlalchemy.ext.asyncio import AsyncSession

from app.packs.repo_scanner import ScanResult


@dataclass(frozen=True)
class KeyOwner:
    """Whoever currently owns a particular action key in the running registry.

    ``source="bundled"`` means the in-image bundled pack
    (``app.actions.registry.BUNDLED_PACK_NAME``). ``source="db_pack"``
    means an installed ``ActionPack`` row; ``pack_id`` references the
    ``ActionPack.id`` so the UI can link to it.
    """

    key: str
    source: Literal["bundled", "db_pack"]
    pack_name: str
    pack_id: int | None = None


@dataclass(frozen=True)
class KeyConflict:
    """Two or more scanned packs in the same repo claim the same action key.

    ``contributing_packs`` is the list of ``DetectedPack.path`` values
    (the relative paths inside the repo) that produced the conflict.
    The wizard renders these rows with a red border; the operator
    must uncheck all but one before submission.
    """

    key: str
    contributing_packs: tuple[str, ...]


@dataclass(frozen=True)
class AnnotatedScanResult:
    """``ScanResult`` plus annotations the wizard needs to render review."""

    base: ScanResult
    existing_key_winners: dict[str, KeyOwner] = field(default_factory=dict)
    intra_repo_key_conflicts: list[KeyConflict] = field(default_factory=list)


async def annotate_scan(db: AsyncSession, result: ScanResult) -> AnnotatedScanResult:
    """Decorate ``result`` with conflict metadata. Pure-ish async helper.

    The DB session is reserved for pack-row lookups when we need to
    surface the ``ActionPack.id`` of a conflicting installed pack —
    today we only read from the in-memory ``ACTION_REGISTRY``, but
    keeping ``db`` in the signature lets future evolutions (e.g.
    surfacing currently-disabled packs) slot in without an API change.
    """
    del db  # reserved for future use; currently unused.

    existing = _existing_key_winners(result)
    intra = _intra_repo_conflicts(result)
    return AnnotatedScanResult(
        base=result,
        existing_key_winners=existing,
        intra_repo_key_conflicts=intra,
    )


def _existing_key_winners(result: ScanResult) -> dict[str, KeyOwner]:
    # Deferred import: ACTION_REGISTRY pulls in the action subsystem
    # which we don't want at module load.
    from app.actions.registry import ACTION_REGISTRY, BUNDLED_PACK_NAME

    scanned_keys: set[str] = set()
    for pack in result.packs:
        scanned_keys.update(pack.contributed_keys)

    winners: dict[str, KeyOwner] = {}
    for key in scanned_keys:
        defn = ACTION_REGISTRY.get(key)
        if defn is None:
            continue
        if defn.pack_name == BUNDLED_PACK_NAME:
            winners[key] = KeyOwner(key=key, source="bundled", pack_name=defn.pack_name)
        else:
            winners[key] = KeyOwner(key=key, source="db_pack", pack_name=defn.pack_name)
    return winners


def _intra_repo_conflicts(result: ScanResult) -> list[KeyConflict]:
    """Two scanned packs in the same repo contributing the same key."""
    contributors: dict[str, list[str]] = defaultdict(list)
    for pack in result.packs:
        for key in pack.contributed_keys:
            contributors[key].append(pack.path)

    conflicts = [
        KeyConflict(key=key, contributing_packs=tuple(sorted(paths)))
        for key, paths in contributors.items()
        if len(paths) >= 2
    ]
    conflicts.sort(key=lambda c: c.key)
    return conflicts
