"""Action registry.

The registry is a dict of action_key → ActionDefinition built from two
sources:

- **Bundled pack** (priority 0): hardcoded path at ``backend/app/ansible``.
  Loaded at import time so the app has actions available even before the
  DB is reachable.
- **DB-backed packs**: configured via the admin UI at ``/action-packs``.
  Materialised on disk under ``settings.ansible.packs_root_dir/<id>`` by
  ``app.packs.service``. Loaded into the registry on FastAPI lifespan
  startup, Celery worker startup, and any mutation to the ``action_packs``
  table (via the router's call to ``reload_registry``).

Each rebuild reads ``action_resolution`` (operator picks) and
``action_registry_snapshot`` (last-known winners), runs the merge from
:func:`app.actions.packs.load_packs_with_resolutions`, then persists
the new snapshot. Fresh conflicts surfaced by the merge are recorded
as ``action_resolution`` rows pinning the previous winner — the
operator resolves them via the conflict UI.

Callers (API handlers, Celery tasks) keep using ``ACTION_REGISTRY`` exactly
as before — they don't need to know where the actions came from.
"""

from __future__ import annotations

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
    "BUNDLED_PACK_PRIORITY",
    "reload_registry",
    "reload_registry_async",
]

logger = logging.getLogger(__name__)

ANSIBLE_DIR = Path(__file__).parent.parent / "ansible"
BUNDLED_PACK_NAME = "bundled"
BUNDLED_PACK_PRIORITY = 0


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
        priority=BUNDLED_PACK_PRIORITY,
        pack_id=None,
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


def reload_registry() -> dict[str, ActionDefinition]:
    """Rebuild ACTION_REGISTRY from bundled + any currently-materialised DB packs.

    Synchronous; callable from non-async contexts (imports, Celery worker
    signals). Uses the existing DB pack checkouts on disk but does NOT
    touch the network — call ``reload_registry_async`` via the service
    layer if you want a re-sync + reload.

    Reads ``action_resolution`` + ``action_registry_snapshot`` and
    persists new snapshot rows + any fresh-conflict freezes. Falls back
    to a bundled-only registry if the DB is unreachable.
    """
    from sqlalchemy import create_engine  # noqa: PLC0415

    from app.actions.packs import Pack, load_packs_with_resolutions  # noqa: PLC0415
    from app.config import settings  # noqa: PLC0415
    from app.packs.service import checkout_path_for  # noqa: PLC0415

    packs: list[Pack] = [_bundled_pack()]

    sync_url = settings.database.url.replace("+asyncpg", "").replace("+aiosqlite", "")
    try:
        engine = create_engine(sync_url, pool_pre_ping=True)
    except Exception:
        logger.debug("reload_registry: DB engine unavailable; bundled-only", exc_info=True)
        _install(load_packs_with_resolutions(packs, resolutions={}, prior_winners={}))
        return ACTION_REGISTRY

    try:
        with engine.connect() as conn:
            db_rows = _scan_db_pack_rows_sync(conn)
            for row in db_rows:
                path = checkout_path_for(row["id"])
                if path.is_dir():
                    packs.append(
                        Pack(
                            name=row["name"],
                            path=path,
                            priority=row["priority"],
                            pack_id=row["id"],
                        )
                    )
            resolutions, prior_winners = _load_resolutions_and_snapshot_sync(conn)
            result = load_packs_with_resolutions(
                packs,
                resolutions=resolutions,
                prior_winners=prior_winners,
            )
            _persist_merge_outcome_sync(conn, result)
            conn.commit()
    except Exception:
        logger.warning(
            "reload_registry: DB read/write failed; using bundled-only",
            exc_info=True,
        )
        result = load_packs_with_resolutions(packs, resolutions={}, prior_winners={})
    finally:
        engine.dispose()

    _install(result)
    return ACTION_REGISTRY


async def reload_registry_async(db) -> dict[str, ActionDefinition]:
    """Async variant that reads packs via the caller-supplied session.

    Prefer this from FastAPI handlers so the reload participates in the
    request's DB context instead of opening a separate sync connection.
    """
    from app.actions.packs import load_packs_with_resolutions  # noqa: PLC0415
    from app.packs.service import load_db_packs  # noqa: PLC0415

    packs = [_bundled_pack()]
    packs.extend(await load_db_packs(db))

    resolutions, prior_winners = await _load_resolutions_and_snapshot_async(db)
    result = load_packs_with_resolutions(
        packs,
        resolutions=resolutions,
        prior_winners=prior_winners,
    )
    await _persist_merge_outcome_async(db, result)

    _install(result)
    return ACTION_REGISTRY


# ---------------------------------------------------------------------------
# DB helpers — sync + async variants kept side-by-side so the two reload
# entry-points share a single source of truth.
# ---------------------------------------------------------------------------


def _scan_db_pack_rows_sync(conn) -> list[dict]:
    """Read enabled pack rows on a sync connection."""
    from sqlalchemy import select  # noqa: PLC0415

    from app.packs.models import ActionPack  # noqa: PLC0415

    result = conn.execute(
        select(
            ActionPack.id,
            ActionPack.name,
            ActionPack.position,
        ).where(ActionPack.enabled.is_(True))
    )
    return [{"id": r.id, "name": r.name, "priority": r.position + 1} for r in result]


def _load_resolutions_and_snapshot_sync(
    conn,
) -> tuple[dict[str, int | None], dict[str, int | None]]:
    from sqlalchemy import select  # noqa: PLC0415

    from app.packs.models import ActionRegistrySnapshot, ActionResolution  # noqa: PLC0415

    res = conn.execute(select(ActionResolution.action_key, ActionResolution.pack_id))
    resolutions = {row.action_key: row.pack_id for row in res}
    snap = conn.execute(
        select(ActionRegistrySnapshot.action_key, ActionRegistrySnapshot.pack_id)
    )
    prior = {row.action_key: row.pack_id for row in snap}
    return resolutions, prior


def _persist_merge_outcome_sync(conn, result) -> None:
    """Apply stale deletions, fresh freezes, and replace the snapshot."""
    from sqlalchemy import delete, insert  # noqa: PLC0415

    from app.packs.models import ActionRegistrySnapshot, ActionResolution  # noqa: PLC0415

    if result.stale_resolution_keys:
        conn.execute(
            delete(ActionResolution).where(
                ActionResolution.action_key.in_(result.stale_resolution_keys)
            )
        )
    if result.fresh_freezes:
        conn.execute(
            insert(ActionResolution),
            [
                {"action_key": key, "pack_id": pack_id, "decided_by_user_id": None}
                for key, pack_id in result.fresh_freezes.items()
            ],
        )
    conn.execute(delete(ActionRegistrySnapshot))
    if result.new_snapshot:
        conn.execute(
            insert(ActionRegistrySnapshot),
            [
                {"action_key": key, "pack_id": pack_id}
                for key, pack_id in result.new_snapshot.items()
            ],
        )


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


async def _persist_merge_outcome_async(db, result) -> None:
    from sqlalchemy import delete  # noqa: PLC0415

    from app.packs.models import ActionRegistrySnapshot, ActionResolution  # noqa: PLC0415

    if result.stale_resolution_keys:
        await db.execute(
            delete(ActionResolution).where(
                ActionResolution.action_key.in_(result.stale_resolution_keys)
            )
        )
    for key, pack_id in result.fresh_freezes.items():
        db.add(
            ActionResolution(
                action_key=key,
                pack_id=pack_id,
                decided_by_user_id=None,
            )
        )
    await db.execute(delete(ActionRegistrySnapshot))
    for key, pack_id in result.new_snapshot.items():
        db.add(ActionRegistrySnapshot(action_key=key, pack_id=pack_id))
    await db.commit()


# Populate with bundled pack + built-ins at import time so the registry
# is never empty. DB-backed packs join on FastAPI startup / Celery
# worker startup via reload_registry / reload_registry_async.
def _load_bundled_only() -> None:
    from app.actions.packs import load_packs_with_resolutions  # noqa: PLC0415

    result = load_packs_with_resolutions(
        [_bundled_pack()], resolutions={}, prior_winners={}
    )
    _install(result)


_load_bundled_only()
