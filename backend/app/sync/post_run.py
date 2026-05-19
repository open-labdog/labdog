"""Post-run sync dispatch helper for the action runtime.

When an action manifest declares ``post_run_sync: [<modules>]``,
LabDog dispatches a normal per-host sync against the same host after
the action succeeds, so labdog's desired state for those modules is
re-enforced. The action runtime calls
``dispatch_post_run_sync`` from the success branches of
``action_host`` (single host) and ``action_group`` (one call per
member host).

Semantics:

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
from collections.abc import Iterable

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.sync_job import SyncJob

logger = logging.getLogger(__name__)


async def dispatch_post_run_sync(
    db: AsyncSession,
    *,
    host_id: int,
    modules: Iterable[str],
    triggered_by_user_id: int | None,
) -> list[int]:
    """Create SyncJobs + dispatch ``run_host_sync`` for each module.

    Returns the list of dispatched SyncJob ids. Modules that collide
    with an already-active sync on the host are silently skipped.

    The caller's outer ``db`` session owns commit timing. We flush
    after each successful insert so the row is visible to the Celery
    worker by the time ``.delay()`` lands on the queue, then leave
    the final commit to the caller.
    """
    from app.tasks.host_sync_orchestrator import run_host_sync

    module_list = list(modules)
    if not module_list:
        return []

    dispatched: list[int] = []
    for module in module_list:
        # Wrap each insert in a savepoint so a uniqueness collision on
        # one module doesn't unwind the inserts that succeeded for
        # earlier modules in the same call.
        try:
            async with db.begin_nested():
                job = SyncJob(
                    host_id=host_id,
                    module_type=module,
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
                "post_run_sync: skipping module=%s on host=%d -- "
                "another sync already active",
                module,
                host_id,
            )
            continue
        dispatched.append(job_id)
        # Pass the explicit one-element module_filter so the orchestrator
        # only runs this module even if the fallback module_type-derivation
        # path (used by dispatch_next_pending_for_host) would pick up the
        # row later. With a single-module module_type the fallback would
        # do the right thing anyway; the explicit filter is belt-and-
        # suspenders.
        run_host_sync.delay(job_id, host_id, module_filter=[module])

    return dispatched
