"""Action registry.

The registry is a dict of action_key → ActionDefinition built from the
built-in pseudo-actions (``app.actions.builtins``) and the DB-backed
packs. Packs are configured in the UI (Actions › Packs) and materialised
on disk under ``settings.ansible.packs_root_dir/<id>`` by
``app.packs.service``. They are loaded into the registry on FastAPI
lifespan startup, Celery worker startup, and any mutation to the
``action_packs`` table (via the router's call to
``reload_registry_async``). Nothing pack-supplied ships in the image: a
fresh install gets its actions from the seeded ``labdog-playbooks`` pack
on first sync, and until then the registry holds the built-ins alone.

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

A copy goes stale when another process changes what it was built from:
the API syncs a pack or records a pin, and a worker's copy still says
what it said at boot. And a Celery pool process starts with the
built-ins alone, from the import below. So worker code calls
:func:`ensure_registry_current` before it relies on the registry, which
rebuilds whenever the database no longer matches what this copy was
built from (BUG-105).

Callers (API handlers, Celery tasks) keep using ``ACTION_REGISTRY``
exactly as before — they don't need to know where the actions came
from. They MUST check ``ActionDefinition.is_unresolved`` (or
``winning_pack_id is None`` for non-built-ins) before dispatching.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging

from app.actions.types import ActionDefinition, ActionParameter

__all__ = [
    "ACTION_REGISTRY",
    "ACTION_REGISTRY_CONTRIBUTORS",
    "ActionDefinition",
    "ActionParameter",
    "ensure_registry_current",
    "reload_registry_async",
]

logger = logging.getLogger(__name__)


ACTION_REGISTRY: dict[str, ActionDefinition] = {}

#: Per-key list of every pack that contributed a manifest for the key
#: at the last rebuild. Drives the conflict view at
#: ``GET /api/action-resolutions`` so the UI doesn't need to re-scan
#: manifests on every render.
ACTION_REGISTRY_CONTRIBUTORS: dict[str, list] = {}

#: :func:`registry_inputs` as this process's registry was last built from
#: them. ``None`` until it has been built from the database at all: the
#: import-time registry is the built-ins alone.
_BUILT_FROM: str | None = None


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
    """Rebuild ACTION_REGISTRY from every enabled DB pack, plus the built-ins.

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
      the in-image actions only.
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

    global _BUILT_FROM

    await lock_registry(db)

    # Read before the inputs themselves, so that a change landing between
    # the two can only make this process rebuild once more than it needed
    # to, never once less.
    built_from = await registry_inputs(db)
    packs, missing = await scan_db_packs(db)

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
    _BUILT_FROM = built_from

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


async def ensure_registry_current(db) -> None:
    """Rebuild this process's registry unless it matches the database.

    For worker code to call before it relies on the registry. Two ways a
    worker's copy went wrong, both BUG-105:

    * A Celery pool process is forked before its parent's boot rebuild and
      imports this module itself, so its registry held only what ships in
      the image. Every pack action was missing from it, and the scheduler
      skipped a pack schedule as "unknown action" on every tick that
      landed on such a process.
    * Nothing told a worker when the API rebuilt after a pack sync or a
      pin, so the worker went on running what it had loaded at boot.

    Costs two small queries when nothing has changed. Commits the
    caller's session when it does rebuild, as every rebuild does, and
    rolls it back when the rebuild fails, so call it first, before the
    caller has anything of its own in the session.

    Never raises. A rebuild that fails — a pack whose path no longer
    passes the containment check, the database gone for a moment — is
    logged, and the process carries on with the registry it has: callers
    include the scheduler, and a stale registry stops fewer schedules
    than none.
    """
    try:
        if _BUILT_FROM is not None and await registry_inputs(db) == _BUILT_FROM:
            return
        await reload_registry_async(db)
    except Exception:
        logger.warning(
            "could not bring the action registry up to date; using the one this process has",
            exc_info=True,
        )
        try:
            await db.rollback()
        except Exception:
            logger.debug("rollback after a failed registry rebuild failed too", exc_info=True)


async def registry_inputs(db) -> str:
    """A digest of everything in the database that a rebuild depends on.

    The packs — which exist, which are enabled, where they live, whether
    they are trusted, and the commit and time of their last sync — and
    the operator's pins. Not the snapshot: every rebuild rewrites it, so
    including it would have each process's rebuild send all the others
    to rebuild in turn.
    """
    from sqlalchemy import select  # noqa: PLC0415

    from app.packs.models import ActionPack, ActionResolution  # noqa: PLC0415

    packs = (
        await db.execute(
            select(
                ActionPack.id,
                ActionPack.enabled,
                ActionPack.source_type,
                ActionPack.git_repository_id,
                ActionPack.path,
                ActionPack.local_path,
                ActionPack.trusted,
                ActionPack.current_sha,
                ActionPack.last_synced_at,
            ).order_by(ActionPack.id)
        )
    ).all()
    pins = (
        await db.execute(
            select(ActionResolution.action_key, ActionResolution.pack_id).order_by(
                ActionResolution.action_key
            )
        )
    ).all()
    material = repr(([tuple(r) for r in packs], [tuple(r) for r in pins]))
    return hashlib.sha256(material.encode()).hexdigest()


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------


async def _load_resolutions_and_snapshot_async(
    db,
) -> tuple[dict[str, int], dict[str, int]]:
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
    fresh_freezes: dict[str, int],
    snapshot: dict[str, int],
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


# Populate with the built-ins at import time so the registry is never
# empty. Packs join on FastAPI startup / Celery worker startup via
# reload_registry_async.
def _load_builtins_only() -> None:
    from app.actions.packs import load_packs_with_resolutions  # noqa: PLC0415

    result = load_packs_with_resolutions([], resolutions={}, prior_winners={})
    _install(result)


_load_builtins_only()
