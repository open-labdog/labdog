"""Action registry.

The registry is a dict of action_key → ActionDefinition built from two
sources:

- **Bundled pack**: hardcoded path at ``backend/app/ansible``. Loaded
  at import time so the app has actions available even before the DB
  is reachable.
- **DB-backed packs**: configured via the admin UI at ``/action-packs``.
  Materialised on disk under ``settings.ansible.packs_root_dir/<id>`` by
  ``app.packs.service``. Loaded into the registry on FastAPI lifespan
  startup, Celery worker startup, and any mutation to the ``action_packs``
  table (via the router's call to ``reload_registry_async``).

Packs have **no inherent precedence**. Each rebuild reads
``action_resolution`` (operator picks) and ``action_registry_snapshot``
(last-known winners), runs the merge from
:func:`app.actions.packs.load_packs_with_resolutions`, then persists
the new snapshot. Contested action keys without an operator pin become
*unresolved* — they appear in the registry as placeholders with no
playbook, and ``POST /api/actions/runs`` rejects them with HTTP 409
until a pin is created.

The freeze-on-fresh-conflict behaviour persists: when a sync
introduces a new contestant for a previously-uncontested key, the
rebuild auto-pins the previous winner via an ``action_resolution``
row, so behaviour doesn't change silently before the operator looks.

Every process rebuilds its own copy — the API and both Celery workers —
so a rebuild takes a lock that serialises it against every other one
(:mod:`app.packs.locks`), and installs what it merged before it records
anything (BUG-96).

Callers (API handlers, Celery tasks) keep using ``ACTION_REGISTRY``
exactly as before — they don't need to know where the actions came
from. They MUST check ``ActionDefinition.is_unresolved`` (or
``winning_pack_id is None`` for non-built-ins) before dispatching.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from app.actions.types import ActionDefinition, ActionParameter

__all__ = [
    "ACTION_REGISTRY",
    "ACTION_REGISTRY_CONTRIBUTORS",
    "ActionDefinition",
    "ActionParameter",
    "ANSIBLE_DIR",
    "BUNDLED_PACK_NAME",
    "reload_registry_async",
]

logger = logging.getLogger(__name__)

ANSIBLE_DIR = Path(__file__).parent.parent / "ansible"
BUNDLED_PACK_NAME = "bundled"


ACTION_REGISTRY: dict[str, ActionDefinition] = {}

#: Per-key list of every pack that contributed a manifest for the key
#: at the last rebuild. Drives the conflict view at
#: ``GET /api/action-resolutions`` so the UI doesn't need to re-scan
#: manifests on every render.
ACTION_REGISTRY_CONTRIBUTORS: dict[str, list] = {}


def _bundled_pack():
    from app.actions.packs import Pack  # noqa: PLC0415

    return Pack(
        name=BUNDLED_PACK_NAME,
        path=ANSIBLE_DIR,
        pack_id=None,
        # In-image content shipped with the release at the SHA pinned in
        # LABDOG_PLAYBOOKS_REF — not a repository anyone pointed LabDog at,
        # so it is already as trusted as the application itself.
        trusted=True,
    )


def _install(result) -> None:
    """Replace ACTION_REGISTRY in-place with the merged result + builtins."""
    from app.actions.builtins import register_builtins  # noqa: PLC0415

    registry = dict(result.registry)
    register_builtins(registry)
    ACTION_REGISTRY.clear()
    ACTION_REGISTRY.update(registry)
    ACTION_REGISTRY_CONTRIBUTORS.clear()
    ACTION_REGISTRY_CONTRIBUTORS.update(result.contributors)
    logger.info(
        "loaded %d action(s) from %d pack(s)",
        len(ACTION_REGISTRY),
        len({d.pack_name for d in ACTION_REGISTRY.values()}),
    )


async def reload_registry_async(db) -> dict[str, ActionDefinition]:
    """Rebuild ACTION_REGISTRY from the bundled pack and every enabled DB pack.

    Reads the packs' checkouts as they are on disk; syncing them is the
    caller's business (``app.packs.service``). Then records the outcome —
    the new snapshot, any fresh-conflict freezes, the stale resolutions to
    drop — and commits the caller's session, as it always has.

    Three things keep a rebuild from losing to another process's (BUG-96):

    * It holds the registry lock from before it reads the resolutions and
      the snapshot until that commit, so rebuilds in different processes
      run one after the other, each reading what the last one wrote.
    * It installs what it merged before it records anything. The snapshot
      is bookkeeping for the next rebuild; failing to write it must not
      cost this process its registry, which is how the API was left with
      the bundled pack only.
    * An enabled pack with nothing on disk — a fresh container before its
      first sync, a clone that failed — is treated as unknown rather than
      as contributing nothing. Its pins are not dropped as stale and its
      keys keep their last-known winner in the snapshot, so that a boot
      with empty checkouts cannot make the next rebuild freeze a
      different winner, or forget the operator's pick, once the pack is
      back. The registry installed meanwhile is the best this process can
      serve without it.
    """
    from app.actions.packs import load_packs_with_resolutions  # noqa: PLC0415
    from app.packs.locks import lock_registry  # noqa: PLC0415
    from app.packs.service import scan_db_packs  # noqa: PLC0415

    await lock_registry(db)

    db_packs, missing = await scan_db_packs(db)
    packs = [_bundled_pack(), *db_packs]

    resolutions, prior_winners = await _load_resolutions_and_snapshot_async(db)
    # Off the loop (BUG-71). This walks every file in every enabled pack
    # repository — ``pack_policy`` does an ``rglob("*")`` over each root —
    # and parses the YAML of every playbook and manifest it finds. The
    # cost scales with repository size, which is the operator's to choose,
    # and four API handlers reach this. ``packs`` holds plain dataclasses
    # and the resolution maps are plain dicts, so nothing crossing into
    # the thread is attached to a session.
    result = await asyncio.to_thread(
        load_packs_with_resolutions,
        packs,
        resolutions=resolutions,
        prior_winners=prior_winners,
    )
    _install(result)

    stale_keys = result.stale_resolution_keys
    snapshot = result.new_snapshot
    if missing:
        logger.warning(
            "action pack(s) %s enabled but not on disk; their keys are left as last recorded",
            ", ".join(sorted(missing.values())),
        )
        stale_keys = [k for k in stale_keys if resolutions.get(k) not in missing]
        snapshot = {
            **snapshot,
            **{k: pid for k, pid in prior_winners.items() if pid in missing},
        }
    try:
        async with db.begin_nested():
            await _persist_merge_outcome_async(
                db,
                stale_keys=stale_keys,
                fresh_freezes=result.fresh_freezes,
                snapshot=snapshot,
            )
    except Exception:
        # Rolled back to the savepoint: the caller's own work in this
        # transaction stands, and so does the registry installed above.
        logger.warning(
            "could not record the action registry; it is loaded regardless",
            exc_info=True,
        )
    # Releases the registry lock, and commits whatever the caller had in
    # flight, which every caller has always relied on.
    await db.commit()
    return ACTION_REGISTRY


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------


async def _load_resolutions_and_snapshot_async(
    db,
) -> tuple[dict[str, int | None], dict[str, int | None]]:
    from sqlalchemy import select  # noqa: PLC0415

    from app.packs.models import ActionRegistrySnapshot, ActionResolution  # noqa: PLC0415

    res = await db.execute(select(ActionResolution.action_key, ActionResolution.pack_id))
    resolutions = {row.action_key: row.pack_id for row in res}
    snap = await db.execute(
        select(ActionRegistrySnapshot.action_key, ActionRegistrySnapshot.pack_id)
    )
    prior = {row.action_key: row.pack_id for row in snap}
    return resolutions, prior


async def _persist_merge_outcome_async(
    db,
    *,
    stale_keys,
    fresh_freezes: dict[str, int | None],
    snapshot: dict[str, int | None],
) -> None:
    """Apply stale deletions and fresh freezes, and replace the snapshot.

    Leaves the commit to the caller. Run under the registry lock: without
    it, two of these interleaved and the second one's INSERT failed on
    the snapshot's primary key (BUG-96).
    """
    from sqlalchemy import delete, insert  # noqa: PLC0415

    from app.packs.models import ActionRegistrySnapshot, ActionResolution  # noqa: PLC0415

    if stale_keys:
        await db.execute(
            delete(ActionResolution).where(ActionResolution.action_key.in_(stale_keys))
        )
    if fresh_freezes:
        await db.execute(
            insert(ActionResolution),
            [
                {"action_key": key, "pack_id": pack_id, "decided_by_user_id": None}
                for key, pack_id in fresh_freezes.items()
            ],
        )
    await db.execute(delete(ActionRegistrySnapshot))
    if snapshot:
        await db.execute(
            insert(ActionRegistrySnapshot),
            [{"action_key": key, "pack_id": pack_id} for key, pack_id in snapshot.items()],
        )


# Populate with bundled pack + built-ins at import time so the registry
# is never empty. DB-backed packs join on FastAPI startup / Celery
# worker startup via reload_registry_async.
def _load_bundled_only() -> None:
    from app.actions.packs import load_packs_with_resolutions  # noqa: PLC0415

    result = load_packs_with_resolutions([_bundled_pack()], resolutions={}, prior_winners={})
    _install(result)


_load_bundled_only()
