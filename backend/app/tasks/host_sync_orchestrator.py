"""Celery task wrapper for the coalesced per-host sync orchestrator (v0.2.0).

The wrapper drives the full lifecycle that the pure orchestrator at
``app.sync.orchestrator.orchestrate_host_sync`` does *not* concern
itself with:

1. Pre-run DB writes flipping ``SyncJob.status`` to ``running`` and
   seeding one ``HostModuleStatus`` row per module the run will touch.
2. tmpfs allocation for the SSH key + ansible-runner private data
   directory (``/dev/shm`` when available, default tmpdir otherwise).
3. Timeout computation as ``base + per_module_budget * len(modules)``,
   floored by the existing ``ansible.playbook_timeout`` setting.
4. Driving ``orchestrate_host_sync`` via ``asyncio.run`` (sync Celery
   task, like the other tasks under ``app.tasks``).
5. Atomic post-run DB writes — ``SyncJob`` final state, per-module
   ``HostModuleStatus`` rows, and one composite ``AuditLog`` row — all
   committed together.
6. Firewall guard: when the host's ``firewall_backend`` is unknown we
   strip ``firewall`` from the orchestration call and pre-record an
   error row. Other modules still run.
7. Tmpfs cleanup in ``finally``.

Per-host serialization (commit C-2) is layered on top: at task entry
we ``_claim_or_defer`` against any in-flight ``SyncJob.running`` row
for the same host — if one exists, this task returns immediately and
leaves the SyncJob in ``pending``. When the in-flight task finishes
(success *or* failure) it scans for the oldest pending SyncJob on the
host via ``_dispatch_next_pending_for_host`` and re-dispatches it via
Celery. This guarantees a single in-flight orchestrator per host
without external locking. Crash recovery — a stale-``running`` sweeper
that releases jobs whose worker died mid-task — is deliberately
deferred to a future commit; the structure here is friendly to that
addition (the dispatch helper is the natural callsite to reuse from a
sweeper).

API wiring (commit C-3) lives elsewhere.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import shutil
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

from sqlalchemy import select, text

from app.db import task_session
from app.sync.orchestrator import orchestrate_host_sync
from app.tasks import celery_app

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Mapping from the orchestrator's canonical module names (used in
# ``CANONICAL_ORDER`` and as ansible task tags) to the short
# ``HostModuleStatus.module_type`` values already present in the DB
# (see existing per-tab tasks under ``app/tasks/*_sync.py``).
#
# This commit deliberately keeps both sets of names alive: the
# orchestrator stays canonical, the DB column stays back-compat, and
# the wrapper translates at the boundary.
_MODULE_TYPE_MAPPING: dict[str, str] = {
    "firewall": "firewall",
    "services": "service",
    "packages": "package",
    "cron": "cron",
    "linux-users": "linux_user",
    "hosts-file": "hosts_file",
    "resolver": "resolver",
}

# Reverse lookup for ``_filter_from_module_type``: given the short DB
# value persisted on ``SyncJob.module_type``, return the canonical name
# the orchestrator wants in its ``module_filter`` list.
_DB_TO_CANONICAL: dict[str, str] = {v: k for k, v in _MODULE_TYPE_MAPPING.items()}

# D3 timeout constants. Documented inline at the call site too.
_TIMEOUT_BASE_SECONDS = 60
_TIMEOUT_PER_MODULE_SECONDS = 120


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _firewall_backend_str(host) -> str:
    """Coerce ``host.firewall_backend`` to a plain string.

    Same shape as the helper in the orchestrator — duplicated here so
    the wrapper doesn't reach into the orchestrator's privates and so
    the firewall-backend guard can run *before* the orchestrator is
    invoked.
    """
    raw = getattr(host, "firewall_backend", None)
    if raw is None:
        return ""
    return raw.value if hasattr(raw, "value") else str(raw)


def _compute_timeout(modules_to_run: list[str]) -> int:
    """Compute the playbook timeout for this run.

    ``base + per_module_budget * len(modules_to_run)``, floored by the
    existing ``ansible.playbook_timeout`` configured value (if
    accessible). We never *lower* a configured floor — it represents
    the slowest acceptable per-run wall clock.
    """
    computed = _TIMEOUT_BASE_SECONDS + _TIMEOUT_PER_MODULE_SECONDS * len(modules_to_run)
    floor = 0
    try:
        from app.settings_service import get_setting_sync_typed

        floor_val = get_setting_sync_typed("ansible.playbook_timeout")
        floor = int(floor_val) if floor_val is not None else 0
    except Exception:
        # Best-effort: missing setting / DB unavailable shouldn't crash
        # an in-flight sync. Fall back to the computed value.
        logger.debug("ansible.playbook_timeout unavailable; using computed floor=0")
    return max(computed, floor)


def _make_tmpfs_workspace() -> tuple[str, str]:
    """Create the ansible-runner working directory + SSH key path.

    Prefer ``/dev/shm`` (tmpfs, never hits disk) when present. Returns
    ``(private_data_dir, ssh_key_path)``. The SSH key path is reserved
    inside the working directory; the orchestrator writes the
    decrypted plaintext there.
    """
    # /dev/shm is the preferred tmpfs for the SSH key (in-memory, never
    # hits disk). The bandit B108 hits below are deliberate: we don't
    # *write* to /dev/shm with a predictable filename — tempfile.mkdtemp
    # generates a random suffix, and the parent dir is checked for
    # existence before use.
    parent = "/dev/shm" if Path("/dev/shm").is_dir() else None  # nosec B108
    private_data_dir = tempfile.mkdtemp(prefix="labdog-sync-", dir=parent)
    ssh_key_path = os.path.join(private_data_dir, "id_ssh")
    return private_data_dir, ssh_key_path


def _cleanup_tmpfs(private_data_dir: str | None) -> None:
    """Best-effort tmpfs cleanup. Errors are swallowed by design."""
    if not private_data_dir:
        return
    try:
        shutil.rmtree(private_data_dir, ignore_errors=True)
    except Exception:
        logger.exception("tmpfs cleanup failed for %s", private_data_dir)


_TMPFS_PATH_RE = re.compile(r"(/dev/shm|/tmp)/labdog-sync-[A-Za-z0-9_]+(?:/[^\s'\"]*)?")


def _redact_path(msg: str | None) -> str | None:
    """Replace tmpfs SSH-key paths in error messages with ``<tmpfs>``.

    The orchestrator stages decrypted SSH keys under
    ``/dev/shm/labdog-sync-XXXXXXXX/id_ssh`` (or ``/tmp`` when
    ``/dev/shm`` is missing). Any exception traceback that includes the
    full path leaks (a) the use of ``/dev/shm``, (b) the
    ``labdog-sync-`` prefix convention, and (c) the per-run random
    suffix. None are credentials but a hardened deployment shouldn't
    surface them via the jobs API. (SEC-06.)

    Returns the redacted string. ``None`` round-trips. Anything that
    isn't a string falls through to ``str(...)`` first to be safe.
    """
    if msg is None:
        return None
    msg_str = msg if isinstance(msg, str) else str(msg)
    return _TMPFS_PATH_RE.sub("<tmpfs>", msg_str)


def _resolve_modules(module_filter: list[str] | None) -> list[str]:
    """Return the canonical-ordered list of modules implied by ``module_filter``."""
    from app.ansible_runtime.outcomes import determine_modules_to_run

    return determine_modules_to_run(module_filter)


def _filter_from_module_type(module_type: str) -> list[str] | None:
    """Reconstruct an orchestrator ``module_filter`` from a stored ``SyncJob.module_type``.

    The C-3 bulk endpoint persists ``module_type="bulk"`` to mark
    "all modules" runs; per-tab tasks persist their canonical module's
    short DB name (existing convention from ``app/tasks/*_sync.py``).

    Mapping rules:
    - ``"bulk"`` → ``None``: orchestrator interprets as "every module".
    - Known short DB name → ``[canonical]``: a single-element filter.
    - Anything else → ``None`` and a warning: better to over-run than
      to leave a queued job stuck on a typo'd ``module_type``.
    """
    if module_type == "bulk":
        return None
    canonical = _DB_TO_CANONICAL.get(module_type)
    if canonical is None:
        logger.warning(
            "Unknown SyncJob.module_type %r; dispatching as bulk to avoid stuck job",
            module_type,
        )
        return None
    return [canonical]


# ---------------------------------------------------------------------------
# Per-host serialization (queue mechanism)
# ---------------------------------------------------------------------------


async def _claim_or_defer(db: AsyncSession, job_id: int, host_id: int) -> bool:
    """Single-flight gate at task entry.

    Returns ``True`` when no other ``SyncJob`` for ``host_id`` is in
    ``running`` state — caller proceeds with the normal pre-run write
    that flips this job to ``running``.

    Returns ``False`` when another sync is already in flight for this
    host. The caller must return early without touching DB state; the
    in-flight task will dispatch us via ``_dispatch_next_pending_for_host``
    when it finishes (success or failure).

    Concurrency: a Postgres transaction-level advisory lock keyed on
    ``host_id`` is acquired at the very start of the transaction. This
    serializes the check-and-flip across workers — two tasks dispatched
    nearly simultaneously for the same host will run their gates one
    after the other rather than racing through the read-only SELECT.
    The lock auto-releases when the transaction commits or rolls back.
    Different ``host_id`` values use different lock keys, so unrelated
    hosts never block each other.

    BUG-38: prior to the advisory-lock fix the SELECT was unguarded,
    so two workers could both see "no other running job" and both
    proceed past the gate concurrently for the same host.
    """
    from app.models.sync_job import JobStatus, SyncJob

    await db.execute(text("SELECT pg_advisory_xact_lock(:host_id)"), {"host_id": host_id})
    other = await db.execute(
        select(SyncJob.id)
        .where(
            SyncJob.host_id == host_id,
            SyncJob.id != job_id,
            SyncJob.status == JobStatus.running,
        )
        .limit(1)
    )
    return other.scalar_one_or_none() is None


async def _dispatch_next_pending_for_host(
    db: AsyncSession, host_id: int, exclude_job_id: int
) -> int | None:
    """Pick the oldest queued ``SyncJob`` for ``host_id`` and dispatch it.

    Called from the just-finished task's finally block, after the
    finalise commit (so the row at ``exclude_job_id`` is already
    ``success``/``failed`` and the picked successor is guaranteed to
    not see *us* as ``running`` when it runs ``_claim_or_defer``).

    Returns the dispatched job id, or ``None`` when no pending job
    exists for this host.

    Uses ``run_host_sync.delay(...)`` from the *same* module so a
    test-side ``patch("...host_sync_orchestrator.run_host_sync.delay")``
    intercepts it.
    """
    from app.models.sync_job import JobStatus, SyncJob

    next_pending = await db.execute(
        select(SyncJob)
        .where(
            SyncJob.host_id == host_id,
            SyncJob.id != exclude_job_id,
            SyncJob.status == JobStatus.pending,
        )
        .order_by(SyncJob.created_at.asc())
        .limit(1)
    )
    next_job = next_pending.scalar_one_or_none()
    if next_job is None:
        return None

    module_filter = _filter_from_module_type(next_job.module_type)
    run_host_sync.delay(
        job_id=next_job.id,
        host_id=host_id,
        module_filter=module_filter,
    )
    return next_job.id


# ---------------------------------------------------------------------------
# Async pre/post DB phases
# ---------------------------------------------------------------------------


async def _prepare_run(
    db: AsyncSession,
    job_id: int,
    host_id: int,
    module_filter: list[str] | None,
) -> tuple[list[str], list[str], int | None]:
    """Pre-run DB write phase.

    Loads the SyncJob, flips it to ``running``, seeds
    ``HostModuleStatus(running)`` rows for every module the
    orchestrator will be asked to run, and applies the firewall-backend
    guard (if firewall is in scope and the host's backend is unknown,
    drop firewall from the orchestrator call and pre-record an error
    row so the audit / UI know why).

    Returns ``(modules_to_orchestrate, all_seeded_modules, triggered_by_user_id)``:
    - ``modules_to_orchestrate`` is what we hand to the orchestrator
      (firewall removed if unknown-backend guard tripped).
    - ``all_seeded_modules`` is every module that has a HostModuleStatus
      row created for this run, including any pre-errored ``firewall``.
    - ``triggered_by_user_id`` is captured for the post-run audit row.
    """
    from app.models.host import Host
    from app.models.host_module_status import HostModuleStatus
    from app.models.sync_job import SyncJob

    job = (await db.execute(select(SyncJob).where(SyncJob.id == job_id))).scalar_one_or_none()
    if job is None:
        raise LookupError(f"SyncJob {job_id} not found")
    triggered_by_user_id = job.triggered_by_user_id

    job.status = "running"
    job.started_at = datetime.now(UTC)

    # Resolve modules from the filter using the canonical helper, then
    # apply the firewall-backend guard so we know the *true* list to
    # hand the orchestrator before we seed status rows.
    canonical_modules = _resolve_modules(module_filter)

    modules_to_orchestrate = list(canonical_modules)
    firewall_skipped = False
    if "firewall" in canonical_modules:
        host = (await db.execute(select(Host).where(Host.id == host_id))).scalar_one_or_none()
        backend = _firewall_backend_str(host) if host is not None else ""
        if backend == "unknown":
            modules_to_orchestrate.remove("firewall")
            firewall_skipped = True

    # Seed HostModuleStatus rows. We seed every module the run will
    # touch (modules_to_orchestrate) plus the pre-errored firewall row
    # if the guard tripped.
    seeded_modules: list[str] = list(modules_to_orchestrate)
    if firewall_skipped:
        seeded_modules.append("firewall")

    for canonical in seeded_modules:
        module_type = _MODULE_TYPE_MAPPING[canonical]
        existing = (
            await db.execute(
                select(HostModuleStatus).where(
                    HostModuleStatus.host_id == host_id,
                    HostModuleStatus.module_type == module_type,
                )
            )
        ).scalar_one_or_none()
        if existing is None:
            row = HostModuleStatus(
                host_id=host_id,
                module_type=module_type,
                sync_status="running",
            )
            db.add(row)
            existing = row
        else:
            existing.sync_status = "running"
            existing.error_message = None

        # Pre-error the firewall row so the UI / audit reflect the
        # backend-unknown guard immediately. The orchestrator never
        # touches this module on this run.
        if firewall_skipped and canonical == "firewall":
            existing.sync_status = "error"
            existing.error_message = "Cannot sync firewall: backend not detected"
            existing.last_sync_at = datetime.now(UTC)

    await db.commit()
    return modules_to_orchestrate, seeded_modules, triggered_by_user_id


async def _finalise_run(
    db: AsyncSession,
    job_id: int,
    host_id: int,
    module_filter: list[str] | None,
    seeded_modules: list[str],
    module_outcomes: dict[str, str],
    triggered_by_user_id: int | None,
    firewall_pre_error: bool,
    error_message: str | None,
) -> str:
    """Post-run DB write phase. Atomic — single commit.

    Updates the SyncJob row to its final state, overwrites every seeded
    HostModuleStatus row with the matching outcome, and emits one
    composite AuditLog entry. Returns the final ``SyncJob.status``.

    ``firewall_pre_error`` indicates the firewall-backend guard already
    wrote an error to the firewall HostModuleStatus row; we leave it
    alone. ``error_message`` is set when the orchestrator raised — in
    that case ``module_outcomes`` is synthesized so every module is
    marked ``error``.
    """
    from app.audit.logger import log_action
    from app.models.host_module_status import HostModuleStatus
    from app.models.sync_job import SyncJob

    # SEC-06: redact tmpfs SSH-key paths from any orchestrator-supplied
    # error message before persisting it to columns the API surfaces to
    # authenticated users.
    error_message = _redact_path(error_message)

    job = (await db.execute(select(SyncJob).where(SyncJob.id == job_id))).scalar_one()

    now = datetime.now(UTC)

    # Build per-module final HostModuleStatus values + composite job status.
    final_outcomes_for_audit: dict[str, str] = {}
    any_error = False
    for canonical in seeded_modules:
        # Synthetic firewall outcome when the guard tripped — keep the
        # pre-error row untouched, but record it in the audit payload.
        if canonical == "firewall" and firewall_pre_error:
            final_outcomes_for_audit[canonical] = "error"
            any_error = True
            continue

        outcome = module_outcomes.get(canonical, "error")
        final_outcomes_for_audit[canonical] = outcome
        # ``no_tasks`` collapses to ``in_sync`` per the contract: zero
        # ansible tasks executed means desired state already matched.
        sync_status = "in_sync" if outcome in ("in_sync", "no_tasks") else "error"
        if sync_status == "error":
            any_error = True

        module_type = _MODULE_TYPE_MAPPING[canonical]
        row = (
            await db.execute(
                select(HostModuleStatus).where(
                    HostModuleStatus.host_id == host_id,
                    HostModuleStatus.module_type == module_type,
                )
            )
        ).scalar_one_or_none()
        if row is None:
            # Defensive — _prepare_run should have seeded this. Create
            # on the fly so we never lose a status update.
            row = HostModuleStatus(
                host_id=host_id, module_type=module_type, sync_status=sync_status
            )
            db.add(row)
        else:
            row.sync_status = sync_status
        row.last_sync_at = now
        if sync_status == "error" and error_message:
            row.error_message = error_message
        elif sync_status == "in_sync":
            row.error_message = None

    # SyncJob final state. ``"success"`` matches the existing
    # ``JobStatus`` enum; the contract said ``"completed"`` but the
    # model enum doesn't carry that value — we use the canonical name.
    job.completed_at = now
    if any_error:
        job.status = "failed"
        if error_message:
            job.error_message = error_message
        elif not job.error_message:
            job.error_message = "One or more modules failed; see audit log"
    else:
        job.status = "success"
        job.error_message = None

    # Audit row. ``after_state`` is the composite payload the contract
    # describes: SyncJob ID + per-module outcome map + the original
    # module filter the caller supplied (so audit consumers can tell
    # apart "operator asked for firewall only" from "operator asked
    # for everything").
    action = "sync_failed" if job.status == "failed" else "sync_completed"
    await log_action(
        db,
        action=action,
        entity_type="host",
        entity_id=host_id,
        user_id=triggered_by_user_id,
        before_state=None,
        after_state={
            "sync_job_id": job_id,
            "module_outcomes": final_outcomes_for_audit,
            "module_filter": module_filter,
        },
    )

    await db.commit()
    return job.status


# ---------------------------------------------------------------------------
# Async runtime body
# ---------------------------------------------------------------------------


async def _async_run(
    job_id: int,
    host_id: int,
    module_filter: list[str] | None,
    private_data_dir: str,
    ssh_key_path: str,
) -> dict:
    """Async implementation of :func:`run_host_sync`.

    Sequenced as: claim-or-defer → prepare → orchestrate → finalise →
    dispatch-next-pending. Each DB phase opens its own session for its
    own commit boundary (commit-on-prepare so the UI sees ``running``
    immediately; orchestrator runs read-only against a fresh session;
    commit-on-finalise is atomic for the post-run trio of writes;
    dispatch reads the queue *after* finalise has committed so the
    successor task sees us as no-longer-running).

    The dispatch step runs in a ``finally`` block — both the success
    path and the orchestrator-raised path must release the per-host
    queue. If we returned early via ``_claim_or_defer`` (this task is
    the queued one), no dispatch happens: the in-flight task will
    handle that when it finishes.
    """
    from app.crypto import decrypt_ssh_key

    # --- Phase 0+1: claim-and-prepare under one advisory lock --------
    # Single-flight gate combined with the pre-run write. The advisory
    # lock taken inside ``_claim_or_defer`` only serializes peer workers
    # for the duration of *its* transaction; if the status flip lived in
    # a separate transaction (as it did before BUG-38), two workers could
    # both pass the gate, then both flip the row to ``running``. By
    # committing the gate check and the ``running`` flip in the same
    # transaction, the second worker — which blocks on the advisory
    # lock — observes the first worker's commit and defers. (BUG-38.)
    # If ``_prepare_run`` raises *after* its internal commit (the SyncJob
    # is already visible as ``running`` to other workers, but
    # orchestration never started), we must run a compensating finalise
    # so the job ends up ``failed`` instead of stuck ``running`` forever.
    # See BUG-39. We detect post-commit raise by re-reading the SyncJob:
    # if its status is ``running`` we know _prepare_run's commit
    # succeeded but execution never made it to the orchestrator phase.
    claimed = False
    modules_to_orchestrate: list[str] = []
    seeded_modules: list[str] = []
    triggered_by_user_id: int | None = None

    try:
        async with task_session() as db:
            claimed = await _claim_or_defer(db, job_id, host_id)
            if claimed:
                modules_to_orchestrate, seeded_modules, triggered_by_user_id = await _prepare_run(
                    db, job_id, host_id, module_filter
                )
    except Exception as exc:
        # Was the post-commit window reached? Re-read the row.
        from app.models.sync_job import SyncJob as _SyncJob

        post_commit = False
        try:
            async with task_session() as probe:
                row = (
                    await probe.execute(select(_SyncJob).where(_SyncJob.id == job_id))
                ).scalar_one_or_none()
                if (
                    row is not None
                    and str(row.status.value if hasattr(row.status, "value") else row.status)
                    == "running"
                ):
                    post_commit = True
        except Exception:  # pragma: no cover - probe failure shouldn't mask root cause
            logger.exception("post-commit probe failed for job_id=%s", job_id)

        if post_commit:
            logger.exception(
                "_prepare_run raised post-commit for job_id=%s host_id=%s; compensating",
                job_id,
                host_id,
            )
            error_message = "prepare_run raised after commit"
            firewall_pre_error = (
                "firewall" in seeded_modules and "firewall" not in modules_to_orchestrate
            )
            # If _prepare_run raised before populating seeded_modules,
            # fall back to the module set implied by the caller filter
            # so audit + status rows still describe the requested run.
            seeded_for_compensation = seeded_modules or _resolve_modules(module_filter)
            module_outcomes_err = {
                m: "error"
                for m in seeded_for_compensation
                if not (m == "firewall" and firewall_pre_error)
            }
            try:
                async with task_session() as db:
                    await _finalise_run(
                        db,
                        job_id=job_id,
                        host_id=host_id,
                        module_filter=module_filter,
                        seeded_modules=seeded_for_compensation,
                        module_outcomes=module_outcomes_err,
                        triggered_by_user_id=triggered_by_user_id,
                        firewall_pre_error=firewall_pre_error,
                        error_message=error_message,
                    )
            except Exception:
                logger.exception(
                    "compensating finalise failed for job_id=%s host_id=%s", job_id, host_id
                )
            try:
                async with task_session() as db:
                    await _dispatch_next_pending_for_host(db, host_id, exclude_job_id=job_id)
            except Exception:
                logger.exception(
                    "dispatch-next-pending failed in BUG-39 compensation path "
                    "for host_id=%s after job_id=%s",
                    host_id,
                    job_id,
                )
        raise exc

    if not claimed:
        logger.info(
            "host_sync deferred: job_id=%s host_id=%s (another sync is running)",
            job_id,
            host_id,
        )
        return {"job_id": job_id, "status": "deferred", "module_outcomes": {}}

    try:
        firewall_pre_error = (
            "firewall" in seeded_modules and "firewall" not in modules_to_orchestrate
        )

        # --- Phase 2: orchestrate ------------------------------------
        timeout = _compute_timeout(modules_to_orchestrate)

        # The orchestrator wants the *filtered* canonical module list
        # when the caller supplied one, otherwise None to mean "all".
        # When the firewall guard removes firewall from a None-filter
        # run we must convert to an explicit list so the orchestrator
        # skips firewall.
        if module_filter is None and not firewall_pre_error:
            orchestrator_filter: list[str] | None = None
        else:
            orchestrator_filter = modules_to_orchestrate

        module_outcomes: dict[str, str] = {}
        orchestrator_error: str | None = None

        try:
            if modules_to_orchestrate:
                from app.ansible_runtime.runner import run_ansible

                async with task_session() as db:
                    module_outcomes, _playbook, _inventory = await orchestrate_host_sync(
                        host_id,
                        orchestrator_filter,
                        db,
                        decrypt_key_fn=decrypt_ssh_key,
                        run_ansible_fn=run_ansible,
                        ssh_key_path=ssh_key_path,
                        private_data_dir=private_data_dir,
                        timeout=timeout,
                    )
        except Exception as exc:
            # SEC-06: redact tmpfs SSH-key paths from the captured
            # message before it lands in DB columns the API surfaces.
            orchestrator_error = _redact_path(str(exc) or exc.__class__.__name__)
            # Synthesise an all-error outcome map so _finalise_run marks
            # every seeded module as error. The firewall pre-error path
            # is left alone (its row already says "backend not detected").
            module_outcomes = {
                m: "error" for m in seeded_modules if not (m == "firewall" and firewall_pre_error)
            }
            logger.exception(
                "orchestrate_host_sync raised for job_id=%s host_id=%s", job_id, host_id
            )

        # --- Phase 3: finalise (atomic) ------------------------------
        async with task_session() as db:
            final_status = await _finalise_run(
                db,
                job_id=job_id,
                host_id=host_id,
                module_filter=module_filter,
                seeded_modules=seeded_modules,
                module_outcomes=module_outcomes,
                triggered_by_user_id=triggered_by_user_id,
                firewall_pre_error=firewall_pre_error,
                error_message=orchestrator_error,
            )

        if orchestrator_error is not None:
            # Re-raise so Celery records the task failure. The DB writes
            # have already been committed in phase 3 — Celery's retry
            # policy is the caller's concern. The ``finally`` below still
            # runs the per-host dispatch step.
            raise RuntimeError(orchestrator_error)

        # Build the per-module-outcome map exposed to Celery result
        # inspection. We use the seeded_modules set so the firewall-guard
        # case still surfaces ``firewall: error`` in the result dict.
        result_outcomes: dict[str, str] = {}
        for m in seeded_modules:
            if m == "firewall" and firewall_pre_error:
                result_outcomes[m] = "error"
            else:
                result_outcomes[m] = module_outcomes.get(m, "error")

        return {
            "job_id": job_id,
            "status": final_status,
            "module_outcomes": result_outcomes,
        }
    finally:
        # --- Phase 4: dispatch the next pending sync for this host ---
        # Runs on the success path *and* the orchestrator-raised path.
        # By the time we get here phase 3 has committed (or wasn't
        # reached, in which case there's no successor to release —
        # ``_claim_or_defer`` short-circuits above and skips the try
        # entirely). Any failure in the dispatch helper itself is
        # swallowed so it never masks the real outcome of the task.
        try:
            async with task_session() as db:
                await _dispatch_next_pending_for_host(db, host_id, exclude_job_id=job_id)
        except Exception:
            logger.exception(
                "dispatch-next-pending failed for host_id=%s after job_id=%s; "
                "queue may be stuck until next sync triggers it",
                host_id,
                job_id,
            )


# ---------------------------------------------------------------------------
# Celery task
# ---------------------------------------------------------------------------


@celery_app.task(
    bind=True,
    name="app.tasks.host_sync_orchestrator.run_host_sync",
    queue="long_running",
)
def run_host_sync(
    self,
    job_id: int,
    host_id: int,
    module_filter: list[str] | None = None,
) -> dict:
    """Celery task wrapper for the coalesced per-host sync orchestrator.

    Caller (the API layer, in commit C-3) creates SyncJob with
    ``status="pending"`` and dispatches this task with the new job's ID.
    The task drives the full lifecycle: pre-run status writes,
    orchestration, post-run atomic writes (SyncJob.status,
    HostModuleStatus per module, one AuditLog row), and tmpfs cleanup.

    Args:
        job_id: The SyncJob row to drive.
        host_id: Target host.
        module_filter: Subset of canonical module names, or ``None`` for
            all of them.

    Returns:
        ``{"job_id": int, "status": str, "module_outcomes": dict[str, str]}``
        for Celery result inspection. ``status`` is the SyncJob's final
        status (``"success"`` or ``"failed"``).
    """
    private_data_dir, ssh_key_path = _make_tmpfs_workspace()
    try:
        return asyncio.run(
            _async_run(
                job_id=job_id,
                host_id=host_id,
                module_filter=module_filter,
                private_data_dir=private_data_dir,
                ssh_key_path=ssh_key_path,
            )
        )
    finally:
        _cleanup_tmpfs(private_data_dir)
