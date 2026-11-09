"""Action orchestrator Celery task.

Resolves the target host(s) for an ActionRun, creates per-host
ActionHostRun records, then dispatches action_host tasks so that no more
than the run's ``parallelism`` are in flight at once: each time a host
finishes, the next one is sent (BUG-155: it used to send them in batches
and wait for a batch's slowest host before sending the next). Individual
host failures do not stop the rest.

The orchestrator works from the database, not from Celery results. It
waits for its hosts by re-reading their rows, and every read also
writes ``ActionRun.heartbeat_at``. That is what lets a run outlive its
orchestrator: when the worker dies — LabDog upgrading its own Docker
host restarts it, for one — the heartbeat goes stale, the action
sweeper hands the run to :func:`resume_run`, and the new orchestrator
carries on from the rows the old one left (BUG-101). It used to wait in
``result.join()``, which nothing but the task that called it could
pick up again.
"""

import asyncio
import logging
import time
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from app.db import with_task_engine
from app.tasks import celery_app
from app.tasks.action_timeouts import ORCHESTRATOR_POLL_SECONDS

logger = logging.getLogger(__name__)


# Per-host Celery task name for each built-in pseudo-action. Looked up
# by the orchestrator's per-host fork; pack-supplied actions fall
# through to the default ``app.tasks.action_host.run_action_host``.
PER_HOST_TASK_FOR_BUILTIN: dict[str, str] = {
    "_builtin.sync": "app.tasks.builtin_dispatchers.run_builtin_sync",
    "_builtin.drift_check": "app.tasks.builtin_dispatchers.run_builtin_drift_check",
    "_builtin.collect_state": "app.tasks.builtin_dispatchers.run_builtin_collect_state",
    # `_builtin.ai_task_group` has no entry: supports_host=False routes it
    # down the group-dispatch path before this map is consulted.
    "_builtin.ai_task": "app.tasks.ai_task.run_builtin_ai_task",
}
_DEFAULT_PER_HOST_TASK = "app.tasks.action_host.run_action_host"

# Group-dispatch (``supports_host=False``) equivalents. The default group
# task runs ansible-runner against the action's playbook, which a built-in
# does not have, so a built-in needs its own whole-group task here.
GROUP_TASK_FOR_BUILTIN: dict[str, str] = {
    "_builtin.ai_task_group": "app.tasks.ai_task.run_builtin_ai_task_group",
}
_DEFAULT_GROUP_TASK = "app.tasks.action_group.run_action_group"

#: Queue the orchestrator publishes its children to.
#:
#: Must not be a queue the orchestrator's own worker consumes: this task
#: blocks until these children finish, so sharing a pool with them is a
#: self-deadlock. ``tests/test_orchestrator_queue.py`` asserts the
#: separation rather than the names.
CHILD_QUEUE = "actions"


# ---------------------------------------------------------------------------
# Celery task entry point
# ---------------------------------------------------------------------------


@celery_app.task(
    bind=True,
    name="app.tasks.action_orchestrator.run_action",
    # A queue of its own, served by its own worker: this task blocks
    # waiting for children it publishes to long_running, so sharing that
    # pool means four concurrent orchestrators can starve every child of
    # a slot. See app/celery_manager.py.
    queue="orchestrator",
    # Override the global 1500/1800s limits: the orchestrator mostly sits
    # idle waiting for its children, and a hard-kill mid-wait skips the
    # Phase 3 aggregation. 12h covers the worst realistic case (fleet run
    # at parallelism 1 × a 5400s action); the action sweeper is the real
    # guardrail beyond that.
    soft_time_limit=43200,
    time_limit=43500,
)
def run_action(self, action_run_id: int) -> dict:
    """Orchestrate an action run against one host or a group of hosts.

    Args:
        action_run_id: ID of the ActionRun record to process.

    Returns:
        A dict summarising the outcome, e.g. ``{"action_run_id": 1}``.
    """
    asyncio.run(with_task_engine(_run_action_async(action_run_id, orchestrator_id=self.request.id)))
    return {"action_run_id": action_run_id}


@celery_app.task(
    bind=True,
    name="app.tasks.action_orchestrator.resume_run",
    # Same queue and limits as run_action, for the same reasons: it is
    # run_action, picked up part-way through.
    queue="orchestrator",
    soft_time_limit=43200,
    time_limit=43500,
)
def resume_run(self, action_run_id: int) -> dict:
    """Carry on a run whose orchestrator died (BUG-101).

    Sent only by the action sweeper, which first makes this task's id the
    run's ``orchestrator_id``; a copy that finds a different owner does
    nothing.

    Args:
        action_run_id: ID of the ActionRun to carry on.

    Returns:
        A dict summarising the outcome, e.g. ``{"action_run_id": 1}``.
    """
    asyncio.run(with_task_engine(_resume_run_async(action_run_id, self.request.id)))
    return {"action_run_id": action_run_id}


# ---------------------------------------------------------------------------
# Async implementation
# ---------------------------------------------------------------------------


async def finalise_run_if_complete(action_run_id: int, r=None) -> str | None:
    """Set an ActionRun's terminal status once every member has finished.

    Returns the status written, or ``None`` when the run is not finished
    yet — a member deferred behind a busy host sits ``pending`` until
    dispatch-next-pending re-fires it, and finalising then would report the
    run as complete on a host it never touched.

    Callable from two places on purpose. The orchestrator calls it once its
    last host has finished, which is the normal path. ``action_host`` calls it when a
    late-dispatched child finishes, which is the only thing that can close a
    run whose last member was deferred — the orchestrator has long since
    returned by then, and the sweeper only looks at ``running`` per-host
    rows, so nothing else would.
    """
    import json  # noqa: PLC0415

    from sqlalchemy import select  # noqa: PLC0415

    from app.db import task_session  # noqa: PLC0415
    from app.models.action_run import ActionHostRun, ActionRun  # noqa: PLC0415

    async with task_session() as db:
        host_runs = list(
            (
                await db.execute(
                    select(ActionHostRun).where(ActionHostRun.action_run_id == action_run_id)
                )
            )
            .scalars()
            .all()
        )
        if not host_runs:
            return None

        waiting = [hr for hr in host_runs if hr.status in ("pending", "queued", "running")]
        if waiting:
            logger.info(
                "action_orchestrator: action_run %d not final — %d of %d host run(s) still active",
                action_run_id,
                len(waiting),
                len(host_runs),
            )
            return None

        succeeded = sum(1 for hr in host_runs if hr.status == "succeeded")
        failed = sum(1 for hr in host_runs if hr.status == "failed")
        if failed == 0:
            final_status = "succeeded"
        elif succeeded == 0:
            final_status = "failed"
        else:
            final_status = "partial"

        run = (
            await db.execute(select(ActionRun).where(ActionRun.id == action_run_id))
        ).scalar_one_or_none()
        if run is None:
            return None
        if run.status == "cancelled":
            if run.finished_at is None:
                # Cancelled while this, its last host, was still running:
                # the cancel left the end of the run to whoever finished
                # last (BUG-104).
                run.finished_at = datetime.now(UTC)
                await db.commit()
            return run.status
        if run.status in ("succeeded", "failed", "partial"):
            # Already closed by whichever member finished last.
            return run.status
        run.status = final_status
        run.finished_at = datetime.now(UTC)
        await db.commit()
        written = run.status
        logger.info(
            "action_orchestrator: action_run %d finished — %s (%d/%d hosts succeeded)",
            action_run_id,
            written,
            succeeded,
            len(host_runs),
        )

    if r is not None:
        try:
            r.publish(
                f"actions.run.{action_run_id}",
                json.dumps({"event": "status", "status": written}),
            )
        except Exception:
            logger.debug("could not publish terminal status", exc_info=True)
    return written


async def _run_action_async(action_run_id: int, orchestrator_id: str | None = None) -> None:
    """Async implementation of :func:`run_action`.

    ``orchestrator_id`` is the Celery task id, recorded on the run as its
    owner. Callers that are not a Celery task (tests) get a fresh one.
    """
    orchestrator_id = orchestrator_id or uuid.uuid4().hex

    # ------------------------------------------------------------------ #
    # Phase 0: dispatch shape decision                                    #
    # ------------------------------------------------------------------ #
    if await _settled_before_start(action_run_id):
        return

    # ------------------------------------------------------------------ #
    # Phase 1: initialise run and create per-host records                 #
    # ------------------------------------------------------------------ #
    try:
        started = await _start_run(action_run_id, orchestrator_id)
    except Exception as exc:
        logger.exception(
            "action_orchestrator: initialisation failed for action_run %d",
            action_run_id,
        )
        await _mark_run_failed(action_run_id, str(exc))
        await _let_go(action_run_id, orchestrator_id)
        return
    if started is None:
        return

    # ------------------------------------------------------------------ #
    # Phases 2 and 3: dispatch within the parallelism, then aggregate     #
    # ------------------------------------------------------------------ #
    await _drive(
        _Driver(
            action_run_id=action_run_id,
            orchestrator_id=orchestrator_id,
            per_host_task=started.per_host_task,
            child_soft_limit=started.child_soft_limit,
            r=_redis(),
        ),
        started.host_run_ids,
        started.parallelism,
    )


async def _settled_before_start(action_run_id: int) -> bool:
    """Phase 0. True when this orchestrator has nothing to start: the run is
    missing, no longer ``queued``, or has been handed to the group task.

    Group target + action.supports_host=False ⇒ single-invocation
    group dispatch (one ansible-runner against a flat all-hosts
    inventory). The group-dispatch task owns its own ActionHostRun
    row creation and run-state transitions; we hand off and return.
    Done in a separate session so we don't take the per-host code
    path's "mark running" write before the group task even starts.

    A failing probe answers False, so the per-host path runs.
    """
    from sqlalchemy import select

    from app.actions.registry import ACTION_REGISTRY, ensure_registry_current
    from app.db import task_session
    from app.models.action_run import ActionRun

    try:
        async with task_session() as db:
            # The dispatch shape comes from the registry, so it has to be
            # this process's current one: a pool process still on the
            # built-ins alone would not know a pack's ``supports_host:
            # false`` action at all (BUG-105).
            await ensure_registry_current(db)
            run_result = await db.execute(select(ActionRun).where(ActionRun.id == action_run_id))
            run_peek: ActionRun | None = run_result.scalar_one_or_none()
            if run_peek is None:
                logger.warning("action_orchestrator: action_run %d not found", action_run_id)
                return True
            if run_peek.status != "queued":
                # Cancelled while it waited for a slot, failed by the
                # sweeper's never-picked-up rule, or a duplicate delivery
                # of a run another orchestrator already started. Starting
                # it would resurrect the first two and double-drive the
                # third.
                logger.info(
                    "action_orchestrator: action_run %d is %r, not queued — not starting it",
                    action_run_id,
                    run_peek.status,
                )
                return True
            action_peek = ACTION_REGISTRY.get(run_peek.action_key)
            target_is_group = run_peek.group_id is not None and run_peek.host_id is None
            if target_is_group and action_peek is not None and not action_peek.supports_host:
                logger.info(
                    "action_orchestrator: action_run %d → group-dispatch "
                    "(action=%s supports_host=False)",
                    action_run_id,
                    run_peek.action_key,
                )
                await _send_group_task(db, run_peek)
                return True
    except Exception:
        # Fall through to the per-host path on read errors — it has the
        # same defensive _mark_run_failed wrapper around its own work.
        logger.exception(
            "action_orchestrator: dispatch-shape probe failed for action_run %d; "
            "falling back to per-host path",
            action_run_id,
        )
    return False


async def _send_group_task(db, run) -> None:
    """Hand a group run to its whole-group task.

    Size the group task's Celery limits from the run's
    deadline (member_count sequential envelopes + slack)
    instead of the global 1500/1800s — a hard-kill at the
    global limit skips the group task's DB finalisation
    and orphans the run in ``running``.
    """
    from sqlalchemy import func, select  # noqa: PLC0415

    from app.models.host import HostGroupMembership
    from app.tasks.action_timeouts import HARD_LIMIT_MARGIN_SECONDS, run_deadline_seconds

    member_count = (
        await db.scalar(
            select(func.count())
            .select_from(HostGroupMembership)
            .where(HostGroupMembership.c.group_id == run.group_id)
        )
        or 1
    )
    group_soft = run_deadline_seconds(run.action_key, member_count, parallelism=1)
    group_task = GROUP_TASK_FOR_BUILTIN.get(run.action_key, _DEFAULT_GROUP_TASK)
    celery_app.send_task(
        group_task,
        args=[run.id],
        queue=CHILD_QUEUE,
        soft_time_limit=group_soft,
        time_limit=group_soft + HARD_LIMIT_MARGIN_SECONDS,
    )


@dataclass
class _StartedRun:
    """What Phase 1 hands the dispatch loop."""

    host_run_ids: list[int]
    per_host_task: str
    #: Per-child Celery limits, sized from the action's own deadline.
    child_soft_limit: int
    parallelism: int


async def _start_run(action_run_id: int, orchestrator_id: str) -> _StartedRun | None:
    """Phase 1: claim the run, resolve its hosts and create their rows.

    Returns None when the run was not started (another orchestrator has
    it), or was finished here (an unknown action key fails it, a target
    with no hosts succeeds it). Raises on anything unexpected; the caller
    fails the run.
    """
    from app.db import task_session
    from app.tasks.action_timeouts import per_host_deadline_seconds

    async with task_session() as db:
        run = await _claim_queued_run(db, action_run_id, orchestrator_id)
        if run is None:
            return None

        action = await _lookup_action(db, run.action_key)
        if action is None:
            run.status = "failed"
            run.error_message = f"Unknown action key: {run.action_key}"
            run.finished_at = datetime.now(UTC)
            run.orchestrator_id = None
            run.heartbeat_at = None
            await db.commit()
            return None

        # Pick the per-host task: built-ins each have their own
        # wrapper in app.tasks.builtin_dispatchers; pack-supplied
        # actions go through the default Ansible playbook runner.
        per_host_task_name = PER_HOST_TASK_FOR_BUILTIN.get(run.action_key, _DEFAULT_PER_HOST_TASK)

        # Per-child Celery limits: soft above the whole per-host
        # envelope (ansible timeout + verify + grace) so the child's
        # SoftTimeLimitExceeded handler and finally block get to
        # finalise the DB rows; hard-kill only after a further margin.
        # The global 1500/1800s config would SIGKILL a child at the
        # same moment its ansible timeout fires, orphaning the
        # ActionHostRun in ``running``.
        child_soft_limit = per_host_deadline_seconds(run.action_key)

        host_ids = await _resolve_target_host_ids(db, run)
        if not host_ids:
            logger.warning(
                "action_orchestrator: no hosts resolved for action_run %d",
                action_run_id,
            )
            run.status = "succeeded"
            run.finished_at = datetime.now(UTC)
            run.orchestrator_id = None
            run.heartbeat_at = None
            await db.commit()
            return None

        host_run_ids = await _create_host_runs(db, action_run_id, host_ids)

        parallelism: int = run.parallelism
        await db.commit()

    return _StartedRun(
        host_run_ids=host_run_ids,
        per_host_task=per_host_task_name,
        child_soft_limit=child_soft_limit,
        parallelism=parallelism,
    )


async def _claim_queued_run(db, action_run_id: int, orchestrator_id: str):
    """Load ActionRun and mark as running. Locked, and only from
    ``queued``: the claim that makes this orchestrator the run's
    owner, so a second copy of the task finds it taken. None when it
    was not ``queued``."""
    from sqlalchemy import select

    from app.models.action_run import ActionRun

    run_result = await db.execute(
        select(ActionRun).where(ActionRun.id == action_run_id).with_for_update()
    )
    run: ActionRun | None = run_result.scalar_one_or_none()
    if run is None or run.status != "queued":
        logger.info(
            "action_orchestrator: action_run %d is %r, not queued — not starting it",
            action_run_id,
            run.status if run is not None else None,
        )
        return None
    run.status = "running"
    run.started_at = datetime.now(UTC)
    run.orchestrator_id = orchestrator_id
    run.heartbeat_at = datetime.now(UTC)
    await db.flush()
    return run


async def _lookup_action(db, action_key: str):
    """The registered action, reloading the registry once on a miss (the
    worker may have been pre-forked before worker_ready fired). None when
    it is still unknown."""
    from app.actions.registry import ACTION_REGISTRY

    action = ACTION_REGISTRY.get(action_key)
    if action is None:
        from app.actions.registry import reload_registry_async  # noqa: PLC0415

        await reload_registry_async(db)
        action = ACTION_REGISTRY.get(action_key)
    return action


async def _resolve_target_host_ids(db, run) -> list[int]:
    """Resolve target hosts. Fleet runs (both host_id and
    group_id NULL) are scheduled-only — the action_runs
    check constraint forbids ad-hoc fleet rows."""
    from sqlalchemy import select

    from app.models.host import Host, HostGroupMembership

    if run.host_id is not None:
        return [run.host_id]
    if run.group_id is not None:
        hosts_result = await db.execute(
            select(Host)
            .join(HostGroupMembership, Host.id == HostGroupMembership.c.host_id)
            .where(HostGroupMembership.c.group_id == run.group_id)
        )
        return [h.id for h in hosts_result.scalars().all()]
    # fleet — every registered host
    hosts_result = await db.execute(select(Host))
    return [h.id for h in hosts_result.scalars().all()]


async def _create_host_runs(db, action_run_id: int, host_ids: list[int]) -> list[int]:
    """One ``queued`` ActionHostRun per host; returns their ids in order."""
    from sqlalchemy import select

    from app.models.action_run import ActionHostRun
    from app.models.host import Host

    # Snapshot the names now: ``ActionHostRun.hostname`` is what
    # the run detail page shows after a host is deleted, which is
    # the only thing left to show at that point (BUG-77).
    names = dict(
        (await db.execute(select(Host.id, Host.hostname).where(Host.id.in_(host_ids)))).all()
    )

    host_run_ids: list[int] = []
    for hid in host_ids:
        host_run = ActionHostRun(
            action_run_id=action_run_id,
            host_id=hid,
            hostname=names.get(hid, f"host {hid}"),
            status="queued",
        )
        db.add(host_run)
        await db.flush()
        host_run_ids.append(host_run.id)
    return host_run_ids


async def _resume_run_async(action_run_id: int, orchestrator_id: str) -> None:
    """Async implementation of :func:`resume_run`.

    Picks the run up from its rows. Hosts already finished, and hosts
    deferred behind other work (``pending``, which the host queue
    re-dispatches), are left alone. Hosts still ``running`` are waited for
    and count against the parallelism — if their worker died with the
    orchestrator, the sweeper fails them at their per-host deadline. Hosts
    still ``queued`` are dispatched as slots free up, as the dead
    orchestrator would have. Some of those it may already have sent;
    whichever copy of such a host's task runs second finds the row claimed
    and does nothing.
    """
    from sqlalchemy import select

    from app.actions.registry import ensure_registry_current
    from app.db import task_session
    from app.models.action_run import ActionHostRun, ActionRun
    from app.tasks.action_timeouts import per_host_deadline_seconds

    try:
        async with task_session() as db:
            await ensure_registry_current(db)
            run = (
                await db.execute(
                    select(ActionRun).where(ActionRun.id == action_run_id).with_for_update()
                )
            ).scalar_one_or_none()
            if run is None or run.orchestrator_id != orchestrator_id or run.status != "running":
                logger.info(
                    "action_orchestrator: not resuming action_run %d — status %r, owner %r",
                    action_run_id,
                    run.status if run is not None else None,
                    run.orchestrator_id if run is not None else None,
                )
                return

            action = await _lookup_action(db, run.action_key)
            if action is None:
                await db.rollback()
                await _mark_run_failed(action_run_id, f"Unknown action key: {run.action_key}")
                await _let_go(action_run_id, orchestrator_id)
                return

            rows = (
                await db.execute(
                    select(ActionHostRun.id, ActionHostRun.status)
                    .where(ActionHostRun.action_run_id == action_run_id)
                    .order_by(ActionHostRun.id)
                )
            ).all()
            run.heartbeat_at = datetime.now(UTC)
            driver = _Driver(
                action_run_id=action_run_id,
                orchestrator_id=orchestrator_id,
                per_host_task=PER_HOST_TASK_FOR_BUILTIN.get(run.action_key, _DEFAULT_PER_HOST_TASK),
                child_soft_limit=per_host_deadline_seconds(run.action_key),
                r=_redis(),
            )
            parallelism = run.parallelism
            await db.commit()
    except Exception:
        # Left owned and unattended on purpose: the heartbeat goes stale
        # again and the sweeper makes another attempt, until the run's own
        # deadline fails it.
        logger.exception("action_orchestrator: could not resume action_run %d", action_run_id)
        return

    in_flight = [hr_id for hr_id, status in rows if status == "running"]
    to_run = [hr_id for hr_id, status in rows if status == "queued"]
    logger.warning(
        "action_orchestrator: resuming action_run %d after its orchestrator died — "
        "%d host(s) still to run, %d in flight",
        action_run_id,
        len(to_run),
        len(in_flight),
    )
    await _drive(driver, in_flight + to_run, parallelism, sent=set(in_flight))


# ---------------------------------------------------------------------------
# The dispatch loop
# ---------------------------------------------------------------------------


@dataclass
class _Driver:
    """What the dispatch loop needs about the run it drives.

    Read once, in the session that claimed the run, so the loop itself
    only ever touches the run's status, its heartbeat, and its rows.
    """

    action_run_id: int
    orchestrator_id: str
    per_host_task: str
    child_soft_limit: int
    r: Any


def _redis():
    import redis as redis_lib  # noqa: PLC0415

    from app.config import settings  # noqa: PLC0415

    return redis_lib.from_url(settings.redis.url)


def _window_size(host_count: int, parallelism: int) -> int:
    """How many hosts may be in flight at once: ``parallelism <= 0`` means all."""
    return max(1, host_count) if parallelism <= 0 else max(1, parallelism)


def _host_wait_seconds(child_soft_limit: int) -> int:
    """How long one host may hold a slot before the run stops counting it.

    Past the children's own hard limit nothing is still working on them:
    a row still ``running`` then belongs to a killed worker, and the
    sweeper reaps it.
    """
    from app.tasks.action_timeouts import HARD_LIMIT_MARGIN_SECONDS  # noqa: PLC0415

    return max(3600, child_soft_limit + HARD_LIMIT_MARGIN_SECONDS)


#: What :func:`_check_in` can report, and what the loop does about it.
#:
#: ``go``         — still the owner and the run is open: carry on.
#: ``unknown``    — the database could not be read: wait and ask again.
#: ``cancelled``  — an operator cancelled the run: cancel what is left.
#: ``superseded`` — the sweeper gave the run to another orchestrator,
#:                  having taken this one for dead: stop, touch nothing.
#: ``closed``     — the run has ended — normally its last host finalising
#:                  it, otherwise the sweeper's deadline: stop.
_GO = "go"
_UNKNOWN = "unknown"


async def _check_in(
    d: _Driver, host_run_ids: list[int] | None = None
) -> tuple[str, set[int] | None]:
    """Write the heartbeat, and learn whether to carry on.

    Returns the state (see ``_GO``) and which of ``host_run_ids`` are
    still ``queued`` or ``running`` — ``None`` when that was not read.

    The heartbeat write is fenced on ``orchestrator_id``: once the sweeper
    has handed the run on, it matches nothing, and that is how an
    orchestrator presumed dead finds out it is not.

    A database error is not a reason to stop. The orchestrator keeps
    polling; if the outage outlasts ``ORCHESTRATOR_STALE_SECONDS`` the
    sweeper hands the run on, and the next successful check-in here says
    so.
    """
    from celery.exceptions import SoftTimeLimitExceeded  # noqa: PLC0415
    from sqlalchemy import select, update  # noqa: PLC0415

    from app.db import task_session  # noqa: PLC0415
    from app.models.action_run import ActionHostRun, ActionRun  # noqa: PLC0415

    try:
        async with task_session() as db:
            status = (
                await db.execute(
                    update(ActionRun)
                    .where(
                        ActionRun.id == d.action_run_id,
                        ActionRun.orchestrator_id == d.orchestrator_id,
                    )
                    .values(heartbeat_at=datetime.now(UTC))
                    .returning(ActionRun.status)
                )
            ).scalar_one_or_none()
            in_flight = None
            if host_run_ids:
                in_flight = set(
                    (
                        await db.execute(
                            select(ActionHostRun.id).where(
                                ActionHostRun.id.in_(host_run_ids),
                                ActionHostRun.status.in_(("queued", "running")),
                            )
                        )
                    )
                    .scalars()
                    .all()
                )
            await db.commit()
    except SoftTimeLimitExceeded:
        raise
    except Exception:
        logger.warning(
            "action_orchestrator: check-in failed for action_run %d; still polling",
            d.action_run_id,
            exc_info=True,
        )
        return _UNKNOWN, None

    if status is None:
        return "superseded", None
    if status == "cancelled":
        return "cancelled", in_flight
    # ``pending`` is a host-targeted run whose one host deferred: still open.
    if status not in ("running", "pending"):
        return "closed", in_flight
    try:
        # The cancel endpoint sets this token as well as the status, and
        # it is the only signal for a ``pending`` run, whose status the
        # endpoint leaves alone.
        if d.r.exists(f"actions.cancel.{d.action_run_id}"):
            return "cancelled", in_flight
    except Exception:
        logger.debug("cancel-token probe failed", exc_info=True)
    return _GO, in_flight


async def _pause() -> None:
    # A blocking sleep, on purpose. Celery delivers the soft time limit as
    # an exception raised by a signal handler, in whatever frame is
    # running: here that is this one, and ``_drive`` catches it. Under
    # ``asyncio.sleep`` it would be the event loop's own, and the run would
    # be torn down without finalising. Nothing else shares this loop.
    time.sleep(ORCHESTRATOR_POLL_SECONDS)


async def _ready(d: _Driver) -> str:
    """Check in until the answer is known."""
    while True:
        state, _ = await _check_in(d)
        if state != _UNKNOWN:
            return state
        await _pause()


async def _wait(
    d: _Driver, in_flight: dict[int, float], *, below: int
) -> tuple[str, dict[int, float]]:
    """Check in until fewer than ``below`` hosts are in flight.

    ``in_flight`` maps each host sent and not yet seen to finish to when it
    was sent (``time.monotonic()``); what comes back is the same, minus the
    hosts that have finished. A deferred host (``pending``) counts as
    finished: the host queue re-dispatches it, not this loop. So does a
    host that has held its slot past :func:`_host_wait_seconds`, which the
    sweeper deals with.
    """
    give_up_after = _host_wait_seconds(d.child_soft_limit)
    while True:
        state, still = await _check_in(d, list(in_flight))
        if still is not None:
            in_flight = {h: sent_at for h, sent_at in in_flight.items() if h in still}
        now = time.monotonic()
        overdue = [h for h, sent_at in in_flight.items() if now - sent_at >= give_up_after]
        if overdue:
            logger.warning(
                "action_orchestrator: action_run %d stopped waiting on host run(s) %s "
                "after %ds; moving on",
                d.action_run_id,
                overdue,
                give_up_after,
            )
            in_flight = {h: t for h, t in in_flight.items() if h not in overdue}
        # An unknown state is never returned: the database could not be
        # read, so check in again until it can.
        if state != _UNKNOWN and (state != _GO or len(in_flight) < below):
            return state, in_flight
        await _pause()


def _send_host_task(task: str, soft_limit: int, action_run_id: int, host_run_id: int) -> None:
    from app.tasks.action_timeouts import HARD_LIMIT_MARGIN_SECONDS  # noqa: PLC0415

    celery_app.send_task(
        task,
        args=[action_run_id, host_run_id],
        queue=CHILD_QUEUE,
        soft_time_limit=soft_limit,
        time_limit=soft_limit + HARD_LIMIT_MARGIN_SECONDS,
    )


def _dispatch(d: _Driver, host_run_ids: list[int]) -> None:
    for host_run_id in host_run_ids:
        _send_host_task(d.per_host_task, d.child_soft_limit, d.action_run_id, host_run_id)


def send_host_task(action_key: str, action_run_id: int, host_run_id: int) -> None:
    """Send one host's task for a run, as the orchestrator would have.

    For the host queue, which re-sends a member that deferred behind a busy
    host long after the orchestrator has let go. It used to send
    ``run_action_host`` whatever the action, and that is the pack-playbook
    runner: a built-in has no playbook, so a deferred member of a group
    ``_builtin.drift_check`` failed when its turn came (BUG-103). The task
    and its limits come from the same lookups as the orchestrator's own.
    """
    from app.tasks.action_timeouts import per_host_deadline_seconds  # noqa: PLC0415

    _send_host_task(
        PER_HOST_TASK_FOR_BUILTIN.get(action_key, _DEFAULT_PER_HOST_TASK),
        per_host_deadline_seconds(action_key),
        action_run_id,
        host_run_id,
    )


async def _drive(
    d: _Driver,
    host_run_ids: list[int],
    parallelism: int,
    *,
    sent: set[int] | None = None,
) -> None:
    """Phases 2 and 3: run the hosts, then aggregate.

    Keeps up to ``parallelism`` hosts in flight (``<= 0``: all of them),
    sending the next one in ``host_run_ids`` order as each finishes, and
    waits for the last ones before aggregating (BUG-155). A run used to
    send a batch of ``parallelism`` hosts and wait for all of them, so one
    slow host idled every other slot, and each batch joined the back of a
    busy queue again. Each host still starts no later than it did then,
    which keeps ``run_deadline_seconds`` an upper bound.

    ``sent`` names hosts whose tasks are already out — a resumed run's
    hosts in flight — so they are waited for, and count against the
    parallelism, but are not dispatched again.
    """
    from celery.exceptions import SoftTimeLimitExceeded  # noqa: PLC0415

    sent = sent or set()
    window = _window_size(len(host_run_ids), parallelism)
    started_at = time.monotonic()
    in_flight = {h: started_at for h in host_run_ids if h in sent}
    to_send = [h for h in host_run_ids if h not in sent]
    try:
        state = await _ready(d)
        while state == _GO:
            room = window - len(in_flight)
            if room > 0 and to_send:
                batch, to_send = to_send[:room], to_send[room:]
                logger.info(
                    "action_orchestrator: action_run %d dispatching %d host(s), "
                    "%d in flight, %d still to send",
                    d.action_run_id,
                    len(batch),
                    len(in_flight),
                    len(to_send),
                )
                _dispatch(d, batch)
                sent_at = time.monotonic()
                in_flight.update(dict.fromkeys(batch, sent_at))
            if not in_flight:
                break
            # With hosts still to send, wait for a free slot; with none
            # left, for every host in flight to finish.
            state, in_flight = await _wait(d, in_flight, below=window if to_send else 1)

        if state == "cancelled":
            await _mark_run_cancelled(d.action_run_id)
        elif state == "superseded":
            logger.warning(
                "action_orchestrator: action_run %d has another orchestrator now; "
                "this one stops here",
                d.action_run_id,
            )
        else:
            # Usually a no-op by now: each host's task finalises the run
            # when it is the last to finish.
            await finalise_run_if_complete(d.action_run_id, d.r)

    except (SoftTimeLimitExceeded, asyncio.CancelledError):
        # Celery's graceful abort fired (12h). It arrives as a cancellation
        # when the signal lands while the event loop waits on the database:
        # asyncio.run then cancels this coroutine on its way out, and this
        # is the last chance to finalise.
        #
        # Children that finished keep their statuses; still-queued ones are
        # cancelled; still-running ones are left for the action sweeper.
        # Aggregate what we have so the run reaches a terminal state
        # instead of wedging its schedule in ``running``.
        logger.error(
            "action_orchestrator: action_run %d exceeded orchestrator soft time "
            "limit; finalising with partial results",
            d.action_run_id,
        )
        await _finalise_soft_limited(d.action_run_id)

    except Exception as exc:
        logger.exception(
            "action_orchestrator: unhandled error while driving action_run %d",
            d.action_run_id,
        )
        await _mark_run_failed(d.action_run_id, str(exc))

    finally:
        # Whatever happened, this orchestrator is no longer driving the
        # run. A run left open here — hosts deferred, or still running past
        # the per-host wait — is closed by its last host's own task.
        await _let_go(d.action_run_id, d.orchestrator_id)


async def _let_go(action_run_id: int, orchestrator_id: str) -> None:
    """Clear the run's owner and heartbeat, if this orchestrator still owns it.

    Without this a run that ends with hosts still deferred would keep a
    heartbeat that stops moving, and the sweeper would take that for a
    dead orchestrator and resume it, every sweep, for nothing.
    """
    from sqlalchemy import update  # noqa: PLC0415

    from app.db import task_session  # noqa: PLC0415
    from app.models.action_run import ActionRun  # noqa: PLC0415

    try:
        async with task_session() as db:
            await db.execute(
                update(ActionRun)
                .where(
                    ActionRun.id == action_run_id,
                    ActionRun.orchestrator_id == orchestrator_id,
                )
                .values(orchestrator_id=None, heartbeat_at=None)
            )
            await db.commit()
    except Exception:
        # Harmless if lost: the sweeper resumes the run once, the resumed
        # orchestrator finds nothing to dispatch, and lets go itself.
        logger.warning(
            "action_orchestrator: could not release action_run %d", action_run_id, exc_info=True
        )


# ---------------------------------------------------------------------------
# Helper utilities
# ---------------------------------------------------------------------------


async def _mark_run_failed(action_run_id: int, error_message: str) -> None:
    """Best-effort: set ActionRun status to failed with an error message."""
    try:
        from sqlalchemy import select

        from app.db import task_session
        from app.models.action_run import ActionRun

        async with task_session() as db:
            result = await db.execute(select(ActionRun).where(ActionRun.id == action_run_id))
            run = result.scalar_one_or_none()
            if run is not None:
                run.status = "failed"
                run.error_message = error_message
                run.finished_at = datetime.now(UTC)
                await db.commit()
    except Exception:
        logger.exception(
            "action_orchestrator: could not mark action_run %d as failed",
            action_run_id,
        )


async def _finalise_soft_limited(action_run_id: int) -> None:
    """Best-effort terminal aggregation after the orchestrator's own soft
    time limit fired mid-run.

    Children with terminal statuses keep them; ``queued`` children were
    never dispatched and are cancelled; ``running``/``pending`` children
    are left alone — the action sweeper reaps or re-dispatches them by
    deadline. The run ends ``partial``/``failed`` with a truthful
    message so the scheduler's in-flight guard releases.
    """
    try:
        import json

        import redis as redis_lib
        from sqlalchemy import select

        from app.config import settings
        from app.db import task_session
        from app.models.action_run import ActionHostRun, ActionRun

        async with task_session() as db:
            hr_result = await db.execute(
                select(ActionHostRun).where(ActionHostRun.action_run_id == action_run_id)
            )
            host_runs = list(hr_result.scalars().all())

            for hr in host_runs:
                if hr.status == "queued":
                    hr.status = "cancelled"
                    hr.error_message = "parent orchestrator exceeded its time limit before dispatch"

            succeeded = sum(1 for hr in host_runs if hr.status == "succeeded")
            final_status = "partial" if succeeded else "failed"

            run_result = await db.execute(select(ActionRun).where(ActionRun.id == action_run_id))
            run = run_result.scalar_one_or_none()
            if run is not None and run.status != "cancelled":
                run.status = final_status
                run.error_message = (
                    "orchestrator exceeded its soft time limit while waiting on "
                    "its hosts; results aggregated partially"
                )
                run.finished_at = datetime.now(UTC)
            await db.commit()

        r = redis_lib.from_url(settings.redis.url)
        r.publish(
            f"actions.run.{action_run_id}",
            json.dumps({"event": "status", "status": final_status}),
        )
    except Exception:
        logger.exception(
            "action_orchestrator: could not finalise soft-limited action_run %d",
            action_run_id,
        )


async def _mark_run_cancelled(action_run_id: int) -> None:
    """Best-effort: set the ActionRun, and every host it has not started, to cancelled.

    Deferred hosts (``pending``) are included: the host queue only ever
    re-dispatches a host whose run is still open, so after a cancel they
    would read ``pending`` for good.
    """
    try:
        import json

        import redis as redis_lib
        from sqlalchemy import select

        from app.config import settings
        from app.db import task_session
        from app.models.action_run import ActionHostRun, ActionRun

        async with task_session() as db:
            run_result = await db.execute(select(ActionRun).where(ActionRun.id == action_run_id))
            run = run_result.scalar_one_or_none()
            if run is not None and run.status in ("succeeded", "failed", "partial"):
                # Finished before the cancel landed; the outcome stands.
                return
            if run is not None:
                run.status = "cancelled"
                run.finished_at = datetime.now(UTC)

            # Mark host runs that never started as cancelled
            hr_result = await db.execute(
                select(ActionHostRun).where(
                    ActionHostRun.action_run_id == action_run_id,
                    ActionHostRun.status.in_(("queued", "pending")),
                )
            )
            for hr in hr_result.scalars().all():
                hr.status = "cancelled"
                hr.finished_at = datetime.now(UTC)

            await db.commit()

        r = redis_lib.from_url(settings.redis.url)
        r.publish(
            f"actions.run.{action_run_id}",
            json.dumps({"event": "status", "status": "cancelled"}),
        )
    except Exception:
        logger.exception(
            "action_orchestrator: could not mark action_run %d as cancelled",
            action_run_id,
        )
