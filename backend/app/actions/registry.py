"""Action registry.

The registry is a dict of action_key → ActionDefinition built from two
sources:

- **Bundled pack** (priority 0): hardcoded path at ``backend/app/ansible``.
  Loaded at import time so the app has actions available even before the
  DB is reachable.
- **DB-backed packs**: configured via the admin UI at ``/settings/packs``.
  Materialised on disk under ``settings.ansible.packs_root_dir/<id>`` by
  ``app.packs.service``. Loaded into the registry on FastAPI lifespan
  startup, Celery worker startup, and any mutation to the ``action_packs``
  table (via the router's call to ``reload_registry``).

Callers (API handlers, Celery tasks) keep using ``ACTION_REGISTRY`` exactly
as before — they don't need to know where the actions came from.
"""

from __future__ import annotations

import logging
from pathlib import Path

from app.actions.types import ActionDefinition, ActionParameter

__all__ = [
    "ACTION_REGISTRY",
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


def _bundled_pack():
    from app.actions.packs import Pack  # noqa: PLC0415

    return Pack(name=BUNDLED_PACK_NAME, path=ANSIBLE_DIR, priority=BUNDLED_PACK_PRIORITY)


def reload_registry() -> dict[str, ActionDefinition]:
    """Rebuild ACTION_REGISTRY from bundled + any currently-materialised DB packs.

    Synchronous; callable from non-async contexts (imports, Celery worker
    signals). Uses the existing DB pack checkouts on disk but does NOT
    touch the network — call ``reload_registry_async`` via the service
    layer if you want a re-sync + reload.
    """
    from app.actions.packs import Pack, load_packs  # noqa: PLC0415
    from app.packs.service import checkout_path_for  # noqa: PLC0415

    packs: list[Pack] = [_bundled_pack()]

    # Best-effort: if the DB isn't reachable (e.g. during tests before
    # migrations are up, or during very-early boot), fall back to just
    # the bundled pack.
    try:
        db_packs = _scan_db_pack_rows_sync()
    except Exception:
        logger.debug(
            "reload_registry: DB pack rows unavailable; bundled-only",
            exc_info=True,
        )
        db_packs = []

    for row in db_packs:
        path = checkout_path_for(row["id"])
        if path.is_dir():
            packs.append(Pack(name=row["name"], path=path, priority=row["priority"]))

    new_registry = load_packs(packs)
    ACTION_REGISTRY.clear()
    ACTION_REGISTRY.update(new_registry)
    logger.info(
        "loaded %d action(s) from %d pack(s)",
        len(ACTION_REGISTRY),
        len({d.pack_name for d in ACTION_REGISTRY.values()}),
    )
    return ACTION_REGISTRY


def _scan_db_pack_rows_sync() -> list[dict]:
    """Read enabled pack rows using a blocking psycopg connection.

    Avoids async/sync mismatch when reload_registry is called from
    non-async contexts (Celery signals, module import during migrations).
    Returns an empty list if the DB is unreachable.
    """
    from sqlalchemy import create_engine, select  # noqa: PLC0415

    from app.config import settings  # noqa: PLC0415
    from app.packs.models import ActionPack  # noqa: PLC0415

    sync_url = settings.database.url.replace("+asyncpg", "").replace("+aiosqlite", "")
    engine = create_engine(sync_url, pool_pre_ping=True)
    try:
        with engine.connect() as conn:
            result = conn.execute(
                select(
                    ActionPack.id,
                    ActionPack.name,
                    ActionPack.priority,
                ).where(ActionPack.enabled.is_(True))
            )
            return [{"id": r.id, "name": r.name, "priority": r.priority} for r in result]
    finally:
        engine.dispose()


async def reload_registry_async(db) -> dict[str, ActionDefinition]:
    """Async variant that reads packs via the caller-supplied session.

    Prefer this from FastAPI handlers so the reload participates in the
    request's DB context instead of opening a separate sync connection.
    """
    from app.actions.packs import load_packs  # noqa: PLC0415
    from app.packs.service import load_db_packs  # noqa: PLC0415

    packs = [_bundled_pack()]
    packs.extend(await load_db_packs(db))

    new_registry = load_packs(packs)
    ACTION_REGISTRY.clear()
    ACTION_REGISTRY.update(new_registry)
    logger.info(
        "loaded %d action(s) from %d pack(s)",
        len(ACTION_REGISTRY),
        len({d.pack_name for d in ACTION_REGISTRY.values()}),
    )
    return ACTION_REGISTRY


# Populate with bundled pack at import time so the registry is never
# empty. DB-backed packs join on FastAPI startup / Celery worker startup.
def _load_bundled_only() -> None:
    from app.actions.packs import load_packs  # noqa: PLC0415

    new_registry = load_packs([_bundled_pack()])
    ACTION_REGISTRY.clear()
    ACTION_REGISTRY.update(new_registry)


_load_bundled_only()
