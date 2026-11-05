"""Post-run sync helpers for the action runtime.

When an action manifest declares ``post_run_sync: [<modules>]``,
LabDog queues a normal per-host sync against the same host after
the action succeeds, so labdog's desired state for those modules is
re-enforced. The action runtime calls
``queue_post_run_sync`` from the success branches of
``action_host`` (single host) and ``action_group`` (one call per
member host).

Semantics:

* **Queued, not dispatched.** The rows are left ``pending`` for the
  host queue: the action still holds the host when they are written,
  and its own release hands the host to the oldest pending op once it
  lets go. Dispatching them here as well only raced that release — a
  task that ran while the action was still ``running`` deferred, and
  could defer again on the release's re-dispatch if the parent run was
  not closed yet, after which nothing woke it (BUG-117).
* **Stored as the queue reads them.** ``module_type`` is the short
  database name and ``module_filter`` the one module. The queue
  re-dispatches a row from those two columns, and a canonical name in
  ``module_type`` read back as "every module" (BUG-116).

* **Push, not collect.** This routes through the normal
  ``host_sync_orchestrator`` pipeline, which means the host is
  reconciled against labdog's desired state for the requested
  modules. Action authors must only declare modules where pushing
  the existing desired state is what they want -- declaring
  ``packages`` from an action that just installed something
  labdog's desired-state list doesn't cover would (re)remove it.
* **Skip on dry-run / failure.** Caller is responsible for those
  gates; this helper does no policy checks.
* **One SyncJob per module.** Each module gets its own row. The
  active-row unique index on ``(host_id, module_type)`` enforces
  one-at-a-time per host+module; we treat a unique-constraint
  hit as "another sync already queued, no need to add ours" and
  move on instead of bubbling the IntegrityError.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable, Mapping
from typing import Any

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit.logger import log_action
from app.models.sync_job import SyncJob

logger = logging.getLogger(__name__)


async def queue_post_run_sync(
    db: AsyncSession,
    *,
    host_id: int,
    modules: Iterable[str],
    triggered_by_user_id: int | None,
) -> list[int]:
    """Create one ``pending`` SyncJob per module for the host queue to run.

    Returns the ids of the rows created. Modules that collide with an
    already-active sync on the host are skipped, as are names that are
    not sync modules.

    Nothing is dispatched: the caller still holds the host and releases
    it after committing, which is what starts these. The caller's outer
    ``db`` session owns commit timing.
    """
    from app.tasks.host_sync_orchestrator import _MODULE_TYPE_MAPPING

    queued: list[int] = []
    for module in modules:
        module_type = _MODULE_TYPE_MAPPING.get(module)
        if module_type is None:
            # The manifest validator only admits canonical names, so this
            # is a bug upstream; queueing the row anyway is how an unknown
            # name used to become a sync of every module (BUG-116).
            logger.warning("post_run_sync: skipping unknown module=%s on host=%d", module, host_id)
            continue
        # Wrap each insert in a savepoint so a uniqueness collision on
        # one module doesn't unwind the inserts that succeeded for
        # earlier modules in the same call.
        try:
            async with db.begin_nested():
                job = SyncJob(
                    host_id=host_id,
                    module_type=module_type,
                    module_filter=[module],
                    status="pending",
                    triggered_by_user_id=triggered_by_user_id,
                )
                db.add(job)
                await db.flush()
                job_id = job.id
        except IntegrityError:
            # Another sync for this (host, module) is already pending
            # or running -- the active-row unique index rejected our
            # insert. The post-run reconciliation we wanted is
            # effectively already queued, so skip silently.
            logger.info(
                "post_run_sync: skipping module=%s on host=%d -- another sync already active",
                module,
                host_id,
            )
            continue
        queued.append(job_id)

    return queued


async def dispatch_post_run_register(
    db: AsyncSession,
    *,
    host_id: int,
    declarations: Mapping[str, list[dict[str, Any]]],
    triggered_by_user_id: int | None,
) -> dict[str, int]:
    """Insert manifest-declared resources as host-scope override rows.

    For each module-name key in ``declarations``, iterate the list of
    pre-validated dicts (validated at manifest-load time against the
    module's REST API Create schema) and insert each as a host-scope
    row (``host_id=host_id``, ``group_id=NULL``) in the relevant table.

    Each insert is wrapped in a savepoint so a uniqueness collision on
    one item does not unwind earlier inserts. Collisions log + skip:
    operator-declared rows win over action-declared ones.

    After the inserts, queue a normal sync for the affected modules
    so the cache picks up the new desired state. The sync also serves
    as the "show alloy on the host" cache-refresh path -- a single
    pipeline handles both the push (no-op, host already matches) and
    the post-sync state collection that drives the UI tabs.

    Returns ``{module_name: inserted_count}`` for the modules where at
    least one row was inserted. Modules whose rows all collided do not
    appear in the result (and don't trigger a follow-up sync).

    The caller's outer ``db`` session owns commit timing.
    """
    from app.actions.register_schemas import CREATE_SCHEMAS, MODELS

    if not declarations:
        return {}

    inserted: dict[str, int] = {}
    for module, items in declarations.items():
        model_cls = MODELS.get(module)
        create_schema = CREATE_SCHEMAS.get(module)
        if model_cls is None or create_schema is None:
            # Shouldn't happen -- manifest validator rejected unknown
            # names. Defensive log.
            logger.warning(
                "post_run_register: skipping unknown module %s for host %d",
                module,
                host_id,
            )
            continue
        module_inserted = 0
        for i, item in enumerate(items):
            # Re-validate through the Create schema (defense in depth):
            # the manifest validator already ran at load time, but a
            # programmatic caller might invoke this helper with raw
            # dicts that haven't been through Pydantic. The schemas
            # also normalise fields (e.g. strip the ".service" suffix
            # on service_name) which must apply to whatever lands in
            # the DB.
            validated = create_schema(**item).model_dump()
            try:
                async with db.begin_nested():
                    row = model_cls(host_id=host_id, group_id=None, **validated)
                    db.add(row)
                    await db.flush()
                    await log_action(
                        db,
                        action="post_run_register",
                        entity_type=module,
                        entity_id=row.id,
                        user_id=triggered_by_user_id,
                        after_state=validated,
                    )
            except IntegrityError:
                logger.info(
                    "post_run_register: skipping %s[%d] on host=%d -- "
                    "already declared (likely operator-managed)",
                    module,
                    i,
                    host_id,
                )
                continue
            module_inserted += 1
        if module_inserted > 0:
            inserted[module] = module_inserted

    if inserted:
        # Dispatch a sync for the affected modules so labdog re-asserts
        # the now-registered desired state and refreshes the cached
        # current state (the latter is what surfaces the registration
        # in the Host detail tabs). The host is presumably already in
        # sync because the action just installed those resources, so
        # the sync is typically a no-op push with a state-collection
        # side effect.
        await queue_post_run_sync(
            db,
            host_id=host_id,
            modules=list(inserted),
            triggered_by_user_id=triggered_by_user_id,
        )

    return inserted
