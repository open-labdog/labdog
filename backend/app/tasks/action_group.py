"""Group-dispatch action executor Celery task.

Drives a single ansible-runner invocation against every member of a
host group, using a flat ``all.hosts`` inventory. This dispatch shape
is selected when the action's manifest declares ``supports_host: false``
— the action is a "whole-group operation" (e.g. ``k8s-upgrade``) where
the playbook itself orchestrates serialisation across nodes (drain /
upgrade / re-admit) and uses ``add_host`` for self-discovery.

Difference from ``action_host.py``:

- One ansible-runner invocation drives N hosts, not N invocations of
  one host each.
- The orchestrator does NOT pick a "driver" host. Each member of the
  group gets its own ``ActionHostRun`` row anchored to its real host;
  per-host outcomes are derived from ansible-runner events keyed by
  inventory hostname.
- The inventory is intentionally flat — no ``children``, no role
  grouping. Whatever topology the playbook needs, it discovers itself
  (e.g. ``add_host`` based on facts / labels).

Snapshot / verify / rollback envelope
-------------------------------------

The group path applies the **same** Proxmox snapshot → verify →
rollback envelope as the per-host path, on a **per-host** basis:

* **Phase A — snapshot**: every member with a Proxmox VM mapping is
  snapshotted in parallel before the playbook starts. The snapshot
  name is recorded on the host's ``ActionHostRun`` row. Hosts without
  a VM mapping log a notice and continue without rollback safety.
* **Phase B — playbook**: one ``ansible-playbook`` run against a flat
  multi-host inventory. The playbook owns its own multi-node
  serialisation (``serial:``, ``add_host``, etc.).
* **Phase C — per-host outcome**: failure / unreachable events from
  ``runner.events`` route per-host. Hosts the playbook never touched
  (e.g. aborted by ``serial: 1`` after an earlier failure) inherit
  the playbook's overall status.
* **Phase D — verify**: for every host that succeeded the playbook
  and was snapshotted, run the pack's ``verify_playbook`` (single-host
  inventory) or the built-in SSH/services/packages check. A failed
  verify flips the host from ``succeeded`` to ``failed``.
* **Phase E — rollback**: every host that ended up ``failed`` and has
  a snapshot gets reverted. The per-host rollback policy applies —
  succeeded hosts keep their state, failed hosts are restored.
* **Phase F — cleanup**: snapshots on hosts that ended up
  ``succeeded`` are deleted. Snapshots on rolled-back hosts are left
  in place (matching the per-host path).
* **Phase G — aggregate**: ``ActionRun.status`` is ``succeeded`` /
  ``failed`` / ``partial`` based on the per-host roll-up.

The envelope honours the run-time toggles ``snapshot_enabled``,
``verify_enabled``, ``auto_rollback`` from ``ActionRun`` (mirrored
from the scheduler / API at dispatch time) and is a no-op when the
action's manifest declares ``destructive: false``.

Note: unlike the per-host path (``action_host.py``), this path has no
explicit SSH preflight. Unreachable members surface as
``runner_on_unreachable`` events within seconds (the inventory's
``ConnectTimeout``/keepalives from ``build_ssh_common_args`` bound the
attempt), and Phase C routes those straight to per-host ``failed``
rows — so a dead member can't wedge the run.
"""

from __future__ import annotations

import asyncio
import glob
import json
import logging
import os
import shutil
import tempfile
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from app.db import with_task_engine
from app.tasks import celery_app

logger = logging.getLogger(__name__)

MAX_OUTPUT_BYTES = 1_048_576  # 1 MB — same cap as action_host.py


# ---------------------------------------------------------------------------
# Celery task entry point
# ---------------------------------------------------------------------------


@celery_app.task(
    bind=True,
    name="app.tasks.action_group.run_action_group",
    queue="long_running",
)
def run_action_group(self, action_run_id: int) -> dict:
    """Run an ansible-playbook against every member of a group as one invocation.

    Args:
        action_run_id: ID of the parent ActionRun. The run's ``group_id``
            must be set; the action must have ``supports_host=False``
            (caller — :mod:`app.tasks.action_orchestrator` — enforces).

    Returns:
        A dict summarising the outcome.
    """
    asyncio.run(with_task_engine(_run_action_group_async(action_run_id)))
    return {"action_run_id": action_run_id}


# ---------------------------------------------------------------------------
# Per-host state struct (group-task local — never persisted)
# ---------------------------------------------------------------------------


class _HostCtx:
    """In-memory bookkeeping for one group member across the envelope.

    Holds the data the per-host envelope needs (snapshot, Proxmox
    client, ssh key path, etc.) so Phases A/D/E/F can route work
    keyed by inventory hostname without re-querying the DB.
    """

    __slots__ = (
        "host_id",
        "host_run_id",
        "inv_name",
        "ip",
        "port",
        "ssh_user",
        "ssh_key_path",
        "known_hosts_path",
        "hostname",
        "snapshot_name",
        "snapshot_error",
        "proxmox_client",
        "pve_node",
        "vmid",
        "vm_type",
        "verify_passed",
        "verify_error",
        "step_log",
    )

    def __init__(self, host_id: int, host_run_id: int, inv_name: str):
        self.host_id = host_id
        self.host_run_id = host_run_id
        self.inv_name = inv_name
        self.ip: str = ""
        self.port: int = 22
        self.ssh_user: str = "root"
        self.ssh_key_path: str = ""
        self.known_hosts_path: str | None = None
        self.hostname: str = ""
        self.snapshot_name: str | None = None
        self.snapshot_error: str | None = None
        self.proxmox_client: Any = None
        self.pve_node: str | None = None
        self.vmid: int | None = None
        self.vm_type: str = "qemu"
        # Verify defaults to "passed" so hosts we don't verify (failed
        # playbook, no snapshot, verify disabled) don't get downgraded.
        self.verify_passed: bool = True
        self.verify_error: str | None = None
        self.step_log: list[str] = []


# ---------------------------------------------------------------------------
# Async implementation
# ---------------------------------------------------------------------------


async def _claim_or_defer_group(action_run_id: int) -> list[int] | None:
    """Serialize the whole group against any in-flight op on any member.

    Returns the member host ids this call claimed — the list the caller's
    ``finally`` releases — or ``None`` when the caller must stop: the run
    row is gone, or a member was busy and the run has been parked as
    ``pending`` for the in-flight op's dispatch hook to re-fire.

    An empty list is *not* a stop. A run with no ``group_id``, or a group
    with no members, has nothing to lock and nothing to release; the main
    loader fails it cleanly through its existing branches.

    Extracted from ``_run_action_group_async`` so the single-transaction
    invariant below can be asserted by a test. That is the whole reason it
    is a function: the gate and the flip must commit together, and while
    this lived inline there was no seam to call.
    """
    from datetime import UTC, datetime  # noqa: PLC0415

    from sqlalchemy import select  # noqa: PLC0415

    from app.db import task_session  # noqa: PLC0415
    from app.models.action_run import ActionHostRun, ActionRun  # noqa: PLC0415
    from app.models.host import Host, HostGroupMembership  # noqa: PLC0415
    from app.tasks.host_lock import (  # noqa: PLC0415
        acquire_host_locks,
        check_hosts_busy,
        format_pending_reason,
    )

    # One session, one transaction, deliberately. The advisory locks taken
    # by ``acquire_host_locks`` are transaction-scoped, so the busy check
    # and the status flip below are only safe against a concurrent claim
    # while they are inside *this* ``async with``. Splitting it is BUG-62.
    async with task_session() as db:
        run_peek = (
            await db.execute(select(ActionRun).where(ActionRun.id == action_run_id))
        ).scalar_one_or_none()
        if run_peek is None:
            logger.warning("action_group: action_run %d not found at claim", action_run_id)
            return None

        if run_peek.group_id is None:
            # The main loader fails this run cleanly with its existing
            # "no group_id" branch — let it run.
            return []

        hosts_peek = await db.execute(
            select(Host.id)
            .join(HostGroupMembership, Host.id == HostGroupMembership.c.host_id)
            .where(HostGroupMembership.c.group_id == run_peek.group_id)
            .order_by(Host.id)
        )
        member_ids_peek = list(hosts_peek.scalars().all())
        if not member_ids_peek:
            return []

        await acquire_host_locks(db, member_ids_peek)
        blocker = await check_hosts_busy(db, member_ids_peek)
        if blocker is not None:
            reason = await format_pending_reason(db, blocker)
            # Defer: mark parent + any pre-created per-host rows as
            # ``pending`` and stamp them with the blocker diagnostic. The
            # pre-created rows only exist if an earlier dispatch flipped
            # them; first-time runs will have none yet (the main loader
            # creates them). Every per-host row gets the same string — the
            # defer is run-level (one busy member blocks the whole group),
            # not host-level.
            run_peek.status = "pending"
            run_peek.pending_reason = reason
            existing_hrs = (
                (
                    await db.execute(
                        select(ActionHostRun).where(ActionHostRun.action_run_id == action_run_id)
                    )
                )
                .scalars()
                .all()
            )
            for hr in existing_hrs:
                if hr.status in ("queued", "running"):
                    hr.status = "pending"
                    hr.pending_reason = reason
            await db.commit()
            logger.info(
                "action_group: deferred action_run=%d (host %d busy: %s)",
                action_run_id,
                blocker.host_id,
                reason,
            )
            return None

        # All free → claim *here*, while this transaction still holds every
        # member's advisory lock.
        #
        # BUG-62: this used to commit with the run still ``queued``, and the
        # load phase marked it running in a later session. In between, the
        # group held every member's lock and nothing recorded it:
        # check_host_busy matches a group run through its ActionHostRun rows,
        # and those are created in that later phase. A sync entering its gate
        # in the gap saw the host free and claimed it. Flipping the run here,
        # plus the membership case added to check_host_busy, closes it from
        # both sides.
        run_peek.status = "running"
        run_peek.started_at = datetime.now(UTC)
        await db.commit()

    return list(member_ids_peek)


@dataclass
class _GroupRunSpec:
    """Run and action attributes, copied out while the loading session is open.

    The session closes before the playbook runs, so everything the later
    phases need is read here once.
    """

    action_key: str
    playbook_path: Any
    roles_paths: tuple
    destructive: bool
    verify_playbook_path: Any
    verify_timeout: int
    ai_verify_prompt: str | None
    ai_verify_fail_closed: bool
    playbook_timeout: int | None
    parameters: dict
    snapshot_enabled: bool
    verify_enabled: bool
    auto_rollback: bool


@dataclass
class _GroupPlaybookResult:
    """What Phase B's single ansible-runner invocation produced."""

    runner: Any
    output: str
    status: str
    rc: int
    extra_vars: dict | None
    dry_run: Any


@dataclass
class _MemberOutcomes:
    """Per-host playbook verdicts derived from the runner's events (Phase C).

    Both maps are keyed by ``ActionHostRun.id``. The verify and rollback
    phases read ``succeeded`` rather than re-querying the DB.
    """

    succeeded: dict[int, bool]
    error_message: dict[int, str]


async def _run_action_group_async(action_run_id: int) -> None:
    """Drive a single ansible-runner invocation across all group members,
    wrapped in a per-host snapshot/verify/rollback envelope for destructive
    actions when the host has a Proxmox VM mapping.
    """
    import redis as redis_lib
    from celery.exceptions import SoftTimeLimitExceeded

    from app.config import settings
    from app.crypto import get_master_key
    from app.tasks.host_lock import release_host_queue

    redis_client = redis_lib.from_url(settings.redis.url)
    channel = f"actions.run.{action_run_id}"

    private_data_dir = tempfile.mkdtemp(prefix="labdog-action-group-")
    # Per-host SSH key files written to tmpfs; tracked for cleanup.
    ssh_key_paths: list[str] = []

    # Track member host ids we successfully claimed so the finally block
    # can release each via dispatch-next-pending. Empty on the defer path.
    claimed_member_ids: list[int] = []

    try:
        # ------------------------------------------------------------------ #
        # Pre-flight cancel check                                             #
        # ------------------------------------------------------------------ #
        if redis_client.exists(f"actions.cancel.{action_run_id}"):
            await _mark_run_cancelled(action_run_id, channel, redis_client)
            return

        # ------------------------------------------------------------------ #
        # Claim-or-defer all members atomically.                              #
        # ------------------------------------------------------------------ #
        claim = await _claim_or_defer_group(action_run_id)
        if claim is None:
            return
        claimed_member_ids = claim

        master_key = get_master_key()
        loaded = await _load_group_run(action_run_id, master_key, ssh_key_paths)
        if loaded is None:
            return
        spec, host_ctxs = loaded

        await _run_envelope(
            action_run_id,
            spec,
            host_ctxs,
            master_key,
            private_data_dir,
            channel,
            redis_client,
        )

    except SoftTimeLimitExceeded:
        # Celery's graceful abort — the orchestrator sizes our soft limit
        # above the whole run deadline, so reaching it means a step hung
        # (playbook past its own timeout, Proxmox API, verify SSH).
        # _mark_run_failed finalises the run AND all in-flight host rows,
        # so nothing is left ``running`` to wedge the schedule.
        logger.error("action_group: action_run %d exceeded its soft time limit", action_run_id)
        await _mark_run_failed(
            action_run_id,
            "aborted: exceeded task soft time limit — playbook or "
            "snapshot/verify/rollback step hung",
            channel,
            redis_client,
        )
        return

    except Exception as exc:
        logger.exception("action_group: unhandled error for action_run %d", action_run_id)
        await _mark_run_failed(action_run_id, str(exc), channel, redis_client)
        return

    finally:
        # Dispatch-next-pending per claimed member. Each freed host can
        # unblock a different queued op; we honour exclude_action_run_id
        # so our own row (already finalised in a prior commit) doesn't
        # re-pick itself. The helper swallows per-host failures so they
        # neither mask the real task outcome nor stop the remaining
        # members from being released.
        await release_host_queue(
            claimed_member_ids,
            after=f"action_run_id={action_run_id}",
            exclude_action_run_id=action_run_id,
        )
        _remove_workspaces(ssh_key_paths, private_data_dir)


async def _load_group_run(
    action_run_id: int,
    master_key: bytes,
    ssh_key_paths: list[str],
) -> tuple[_GroupRunSpec, list[_HostCtx]] | None:
    """Phase 1: load run + action + group members + ssh keys, mark running.

    Returns ``None`` when the run ends here: an unknown action key, no
    ``group_id``, an empty group, or a cancel since the claim. Each key
    file is appended to *ssh_key_paths* as soon as it exists, so the
    caller's ``finally`` removes it even if a later member fails.
    """
    from sqlalchemy import select

    from app.actions.registry import ACTION_REGISTRY, ensure_registry_current
    from app.db import task_session
    from app.models.action_run import ActionRun
    from app.models.host import Host, HostGroupMembership

    async with task_session() as db:
        # Current before the lookup, not only on a miss: a key this
        # process knows from an older build never misses, and would
        # run whichever pack won back then (BUG-105).
        await ensure_registry_current(db)
        run_result = await db.execute(select(ActionRun).where(ActionRun.id == action_run_id))
        run: ActionRun = run_result.scalar_one()

        action = ACTION_REGISTRY.get(run.action_key)
        if action is None:
            # Registry may be stale (pack synced after worker started).
            # Reload from disk once and retry before failing the run.
            from app.actions.registry import reload_registry_async  # noqa: PLC0415

            await reload_registry_async(db)
            action = ACTION_REGISTRY.get(run.action_key)
        if action is None:
            await _fail_run_at_load(db, run, f"Unknown action key: {run.action_key}")
            return None

        if run.group_id is None:
            await _fail_run_at_load(db, run, "action_group: ActionRun has no group_id (caller bug)")
            return None

        # Resolve all member hosts of the group.
        hosts_result = await db.execute(
            select(Host)
            .join(HostGroupMembership, Host.id == HostGroupMembership.c.host_id)
            .where(HostGroupMembership.c.group_id == run.group_id)
            .order_by(Host.id)
        )
        hosts = list(hosts_result.scalars().all())

        if not hosts:
            logger.warning(
                "action_group: no hosts in group %d for action_run %d",
                run.group_id,
                action_run_id,
            )
            run.status = "succeeded"
            run.started_at = datetime.now(UTC)
            run.finished_at = datetime.now(UTC)
            await db.commit()
            return None

        # Already flipped under the member locks in the claim block; a
        # cancel is the one thing that can legitimately have moved it
        # since, and re-asserting "running" over that would run a group
        # action the operator stopped.
        if run.status == "cancelled":
            logger.info(
                "action_group: action_run %d was cancelled after the claim — not proceeding",
                action_run_id,
            )
            return None
        run.status = "running"
        if run.started_at is None:
            run.started_at = datetime.now(UTC)
        await db.flush()

        # Cache action + run attributes before the session closes.
        spec = _GroupRunSpec(
            action_key=run.action_key,
            playbook_path=action.playbook_path,
            roles_paths=action.roles_paths,
            destructive=action.destructive,
            verify_playbook_path=action.verify_playbook_path,
            verify_timeout=action.verify_timeout_seconds,
            ai_verify_prompt=action.ai_verify_prompt,
            ai_verify_fail_closed=action.ai_verify_fail_closed,
            playbook_timeout=action.playbook_timeout_seconds,
            parameters=dict(run.parameters or {}),
            snapshot_enabled=bool(run.snapshot_enabled),
            verify_enabled=bool(run.verify_enabled),
            auto_rollback=bool(run.auto_rollback),
        )

        # Per-host: create ActionHostRun, decrypt SSH key, write to tmpfs,
        # add to inventory list. Hosts without an SSH key get a skipped row.
        host_ctxs: list[_HostCtx] = []
        for host in hosts:
            ctx = await _prepare_member(db, action_run_id, host, master_key, ssh_key_paths)
            if ctx is not None:
                host_ctxs.append(ctx)

        await db.commit()

    return spec, host_ctxs


async def _fail_run_at_load(db: Any, run: Any, error_message: str) -> None:
    """Fail *run* before any member row exists, and commit."""
    run.status = "failed"
    run.error_message = error_message
    run.finished_at = datetime.now(UTC)
    await db.commit()


async def _prepare_member(
    db: Any,
    action_run_id: int,
    host: Any,
    master_key: bytes,
    ssh_key_paths: list[str],
) -> _HostCtx | None:
    """Create one member's ``ActionHostRun`` row and its connection context.

    A host with no SSH key gets a ``skipped`` row and no context. Any other
    host gets its key written to tmpfs and its row flipped to ``running``.
    """
    from sqlalchemy import select

    from app.ansible_runtime.known_hosts import write_known_hosts
    from app.crypto import decrypt_ssh_key
    from app.models.action_run import ActionHostRun
    from app.models.ssh_key import SSHKey

    hr = ActionHostRun(
        action_run_id=action_run_id,
        host_id=host.id,
        hostname=host.hostname,
        status="queued",
    )
    db.add(hr)
    await db.flush()

    if host.ssh_key_id is None:
        hr.status = "skipped"
        hr.error_message = "Host has no SSH key configured"
        hr.finished_at = datetime.now(UTC)
        return None

    key_result = await db.execute(select(SSHKey).where(SSHKey.id == host.ssh_key_id))
    ssh_key = key_result.scalar_one()
    private_key_text = decrypt_ssh_key(ssh_key.encrypted_private_key, master_key)
    key_path = _write_member_key(private_key_text)
    ssh_key_paths.append(key_path)

    # Stable inventory name = the hostname. ansible-runner's
    # event_data.host is keyed by inventory name, which we'll
    # use to route per-host events back to the right
    # ActionHostRun row.
    ctx = _HostCtx(host_id=host.id, host_run_id=hr.id, inv_name=host.hostname)
    ctx.ip = host.ip_address
    ctx.port = host.ssh_port or 22
    ctx.ssh_user = ssh_key.ssh_user or "root"
    ctx.ssh_key_path = key_path
    # SEC-26: pin this member's connections to the host key
    # LabDog recorded over asyncssh; None until first contact,
    # which leaves the accept-new fallback in place.
    ctx.known_hosts_path = write_known_hosts(host.ssh_host_key_entry, key_path)
    ctx.hostname = host.hostname

    # Mark this host's row as running now (matches per-host path's
    # behaviour where the row flips to running before ansible runs).
    hr.status = "running"
    hr.started_at = datetime.now(UTC)
    return ctx


def _write_member_key(private_key_text: str) -> str:
    """Write a member's private key to a fresh 0600 tmpfs file; return its path."""
    fd, key_path = tempfile.mkstemp(
        dir="/dev/shm",  # nosec B108 — intentional tmpfs for in-memory key storage
        prefix="labdog-action-group-",
        suffix=".key",
    )
    os.close(fd)
    with open(key_path, "w") as fh:
        fh.write(private_key_text)
        if not private_key_text.endswith("\n"):
            fh.write("\n")
    os.chmod(key_path, 0o600)
    return key_path


async def _run_envelope(
    action_run_id: int,
    spec: _GroupRunSpec,
    host_ctxs: list[_HostCtx],
    master_key: bytes,
    private_data_dir: str,
    channel: str,
    redis_client: Any,
) -> None:
    """Phases A to G for the members that have a usable SSH key."""
    # If every host was skipped (no SSH keys), aggregate and exit early.
    if not host_ctxs:
        await _aggregate_and_finalise(action_run_id, channel, redis_client)
        return

    # Notify SSE listeners that the run is moving — emit one host_status
    # per running host so the UI flips its rows.
    for ctx in host_ctxs:
        _publish(
            redis_client,
            channel,
            {"event": "host_status", "host_run_id": ctx.host_run_id, "status": "running"},
            "host_status running",
        )

    host_ctxs = await _snapshot_phase(
        action_run_id, spec, host_ctxs, master_key, channel, redis_client
    )
    if not host_ctxs:
        # Every host failed snapshot — nothing left to run.
        await _aggregate_and_finalise(action_run_id, channel, redis_client)
        return

    playbook = _run_group_playbook(spec, host_ctxs, private_data_dir)
    outcomes = _route_runner_events_to_hosts(
        host_ctxs,
        getattr(playbook.runner, "events", None) or [],
        playbook.status,
        playbook.rc,
    )

    # ------------------------------------------------------------------ #
    # Phase D: per-host verify (succeeded + snapshotted + verify_enabled) #
    # ------------------------------------------------------------------ #
    if spec.verify_enabled and spec.destructive:
        await _verify_all(
            host_ctxs,
            outcomes.succeeded,
            spec.verify_playbook_path,
            spec.verify_timeout,
            spec.roles_paths,
            playbook.extra_vars,
            private_data_dir,
            channel,
            redis_client,
            ai_verify_prompt=spec.ai_verify_prompt,
            ai_verify_fail_closed=spec.ai_verify_fail_closed,
            action_run_id=action_run_id,
            dry_run=playbook.dry_run,
        )

    # ------------------------------------------------------------------ #
    # Phase E: per-host rollback (failed playbook OR failed verify)      #
    # ------------------------------------------------------------------ #
    if spec.auto_rollback and spec.destructive:
        await _rollback_all(host_ctxs, outcomes.succeeded)

    # ------------------------------------------------------------------ #
    # Phase F: per-host cleanup (delete snapshots on succeeded hosts)    #
    # ------------------------------------------------------------------ #
    if spec.destructive:
        await _cleanup_all(host_ctxs, outcomes.succeeded)

    await _persist_member_outcomes(host_ctxs, outcomes, playbook, channel, redis_client)

    # Stream the tail of the playbook output once for the run.
    _publish(
        redis_client,
        channel,
        {
            "event": "output",
            "host_run_id": None,
            "text": "=== Ansible output ===\n" + playbook.output[-4000:],
        },
        "ansible output",
    )

    # ------------------------------------------------------------------ #
    # Phase G: aggregate run-level status                                 #
    # ------------------------------------------------------------------ #
    await _aggregate_and_finalise(action_run_id, channel, redis_client)


async def _snapshot_phase(
    action_run_id: int,
    spec: _GroupRunSpec,
    host_ctxs: list[_HostCtx],
    master_key: bytes,
    channel: str,
    redis_client: Any,
) -> list[_HostCtx]:
    """Phase A: per-host pre-action snapshot. Returns the members still in play.

    Destructive + snapshot_enabled + has VM mapping → take a snapshot.
    Snapshots are taken in parallel via asyncio.gather; a snapshot
    failure on any single host fails that host immediately (its row
    is marked ``failed`` and it is dropped from the inventory before
    the playbook runs — matching the per-host path's bail-on-snapshot
    behaviour).
    A dry run is ``ansible-playbook --check`` and changes nothing, so
    there is nothing to snapshot (and, with no snapshot, verify and
    rollback below don't run either).
    """
    from app.actions.validation import DRY_RUN_PARAM

    run_is_dry = bool(spec.parameters.get(DRY_RUN_PARAM, False))
    if run_is_dry:
        _publish_global(
            redis_client,
            channel,
            "[group] dry run: ansible-playbook --check, no changes are made; snapshot skipped\n",
        )
        return host_ctxs

    if spec.destructive and spec.snapshot_enabled:
        await _snapshot_all(
            host_ctxs,
            action_run_id,
            spec.action_key,
            master_key,
            channel,
            redis_client,
        )
        # Drop hosts whose snapshot failed: we already marked them
        # ``failed`` in the DB and emitted a status event. They're not
        # part of the inventory the playbook sees.
        return [c for c in host_ctxs if not _was_snapshot_failed(c)]

    if spec.destructive:
        # Destructive but snapshot_enabled=False — log the choice once.
        _publish_global(
            redis_client,
            channel,
            "[group] destructive action with snapshot_enabled=false "
            "— skipping snapshot envelope, playbook owns rollback\n",
        )
    return host_ctxs


def _run_group_playbook(
    spec: _GroupRunSpec,
    host_ctxs: list[_HostCtx],
    private_data_dir: str,
) -> _GroupPlaybookResult:
    """Phase B: build inventory + extra-vars, run ansible-runner once."""
    from app.actions.extra_vars import sanitize_extra_vars
    from app.actions.validation import DRY_RUN_PARAM
    from app.ansible_runtime.runner import generate_multi_host_inventory, run_ansible
    from app.grafana.service import METRICS_DESTINATIONS_PARAM
    from app.tasks.action_timeouts import effective_playbook_timeout

    inventory_json = generate_multi_host_inventory(
        [
            {
                "name": ctx.inv_name,
                "ip": ctx.ip,
                "port": ctx.port,
                "ssh_user": ctx.ssh_user,
                "ssh_key_path": ctx.ssh_key_path,
                "known_hosts_path": ctx.known_hosts_path,
            }
            for ctx in host_ctxs
        ]
    )

    parameters = spec.parameters
    dry_run = parameters.pop(DRY_RUN_PARAM, False)
    # Group dispatch doesn't inject metrics destinations (no manifest
    # that declares ``metrics_backend`` is group-only today), but the
    # reserved key must not reach the playbook as an extra-var.
    parameters.pop(METRICS_DESTINATIONS_PARAM, None)
    # Fail closed before these become extra-vars. Ansible evaluates
    # extra-vars on the controller — the LabDog host — not on the
    # target, so a template expression here is code execution here.
    # The API rejects them too; this catches rows that did not come
    # through that path. See app/actions/extra_vars.py.
    sanitize_extra_vars(parameters)
    extra_vars: dict | None = dict(parameters) if parameters else None
    # Dry run is ``--check`` via run_ansible, not an extra-var: see
    # run_ansible's ``check_mode``.

    # Per-action floor — see the matching comment in action_host.py.
    timeout = effective_playbook_timeout(spec.playbook_timeout)

    runner = run_ansible(
        playbook_path=spec.playbook_path,
        inventory_json=inventory_json,
        private_data_dir=private_data_dir,
        extra_vars=extra_vars,
        timeout=timeout,
        roles_paths=list(spec.roles_paths) if spec.roles_paths else None,
        check_mode=bool(dry_run),
    )

    playbook_output: str = (
        runner.stdout.read() if hasattr(runner.stdout, "read") else str(runner.stdout)
    )
    if len(playbook_output.encode()) > MAX_OUTPUT_BYTES:
        playbook_output = (
            playbook_output[:MAX_OUTPUT_BYTES] + "\n\n(truncated — output exceeded 1 MB)"
        )

    return _GroupPlaybookResult(
        runner=runner,
        output=playbook_output,
        status=getattr(runner, "status", "unknown"),
        rc=getattr(runner, "rc", -1),
        extra_vars=extra_vars,
        dry_run=dry_run,
    )


def _route_runner_events_to_hosts(
    host_ctxs: list[_HostCtx],
    events: Any,
    playbook_status: str,
    playbook_rc: int,
) -> _MemberOutcomes:
    """Phase C: route per-host events from runner.events back to rows.

    Derives each member's playbook outcome (succeeded / failed) from the
    failure and unreachable events keyed by inventory hostname.
    """
    per_host_failure: dict[str, str] = {}
    per_host_unreachable: set[str] = set()
    per_host_seen: set[str] = set()

    for event in events:
        event_type = event.get("event")
        event_data = event.get("event_data") or {}
        host_name = event_data.get("host")
        if not host_name:
            continue
        per_host_seen.add(host_name)
        if event_type == "runner_on_failed":
            msg = (event_data.get("res") or {}).get("msg") or "task failed"
            per_host_failure.setdefault(host_name, str(msg))
        elif event_type == "runner_on_unreachable":
            per_host_unreachable.add(host_name)
            per_host_failure.setdefault(host_name, "host unreachable")

    outcomes = _MemberOutcomes(succeeded={}, error_message={})
    for ctx in host_ctxs:
        if ctx.inv_name in per_host_failure:
            outcomes.succeeded[ctx.host_run_id] = False
            outcomes.error_message[ctx.host_run_id] = per_host_failure[ctx.inv_name]
        elif playbook_status == "successful" or ctx.inv_name in per_host_seen:
            # Either the run completed cleanly, or we saw at least one
            # event for this host with no failure event — treat as success.
            outcomes.succeeded[ctx.host_run_id] = True
        else:
            # Playbook failed globally before reaching this host
            # (e.g. parse error, ``serial: 1`` halt after an earlier
            # failure). Mark as failed; downstream verify is skipped
            # and rollback runs if a snapshot exists.
            outcomes.succeeded[ctx.host_run_id] = False
            outcomes.error_message[ctx.host_run_id] = (
                f"playbook did not reach host (status={playbook_status}, rc={playbook_rc})"
            )
    return outcomes


async def _persist_member_outcomes(
    host_ctxs: list[_HostCtx],
    outcomes: _MemberOutcomes,
    playbook: _GroupPlaybookResult,
    channel: str,
    redis_client: Any,
) -> None:
    """Persist final per-host outcomes and publish each host's final status."""
    from sqlalchemy import select

    from app.db import task_session
    from app.models.action_run import ActionHostRun

    async with task_session() as db:
        for ctx in host_ctxs:
            hr_result = await db.execute(
                select(ActionHostRun).where(ActionHostRun.id == ctx.host_run_id)
            )
            hr = hr_result.scalar_one()
            playbook_ok = outcomes.succeeded.get(ctx.host_run_id, False)
            final_ok = playbook_ok and ctx.verify_passed
            if final_ok:
                hr.status = "succeeded"
            else:
                hr.status = "failed"
                if ctx.verify_error and playbook_ok:
                    hr.error_message = ctx.verify_error
                elif ctx.host_run_id in outcomes.error_message:
                    hr.error_message = outcomes.error_message[ctx.host_run_id]
            hr.exit_code = playbook.rc
            hr.finished_at = datetime.now(UTC)
            hr.output = _member_output(ctx, playbook.output)

            # SSE host_status update.
            _publish(
                redis_client,
                channel,
                {"event": "host_status", "host_run_id": ctx.host_run_id, "status": hr.status},
                "host_status final",
            )

        await db.commit()


def _member_output(ctx: _HostCtx, playbook_output: str) -> str:
    """Compose a member's final output: its own step log plus the shared playbook log.

    The shared log is stamped on every host's row — the underlying
    ansible-runner ran once for the whole group, so per-host slicing of
    stdout isn't meaningful.
    """
    ctx_log = "\n".join(ctx.step_log)
    if ctx_log:
        return ctx_log + "\n=== Ansible output ===\n" + playbook_output
    return playbook_output


def _remove_workspaces(ssh_key_paths: list[str], private_data_dir: str) -> None:
    """Remove every key file, pinned known-hosts file and runner dir this run made."""
    from app.ansible_runtime.known_hosts import remove_known_hosts

    # CRITICAL: always remove SSH keys from tmpfs.
    for key_path in ssh_key_paths:
        try:
            if os.path.exists(key_path):
                os.unlink(key_path)
            # The pinned known-hosts file sits beside the key (SEC-26).
            remove_known_hosts(key_path)
        except Exception:
            logger.debug("action_group: failed to remove key %s", key_path, exc_info=True)
    if os.path.exists(private_data_dir):
        shutil.rmtree(private_data_dir, ignore_errors=True)
    # _verify_all allocates a sibling runner dir per verified host
    # (f"{private_data_dir}-verify-{host_id}"), so removing only the base
    # dir leaked one tree per host per run — each holding the rendered
    # inventory and the full ansible event stream. Globbing the siblings
    # cannot miss one and needs no bookkeeping threaded through
    # _verify_all; private_data_dir is an mkdtemp path, so the prefix is
    # unambiguous.
    for verify_dir in glob.glob(f"{private_data_dir}-verify*"):
        shutil.rmtree(verify_dir, ignore_errors=True)


# ---------------------------------------------------------------------------
# Phase helpers
# ---------------------------------------------------------------------------


_SNAPSHOT_FAILED_MARKER = "__snapshot_failed__"


def _was_snapshot_failed(ctx: _HostCtx) -> bool:
    return ctx.snapshot_name == _SNAPSHOT_FAILED_MARKER


async def _snapshot_all(
    host_ctxs: list[_HostCtx],
    action_run_id: int,
    action_key: str,
    master_key: bytes,
    channel: str,
    redis_client: Any,
) -> None:
    """Take a Proxmox snapshot on every host that has a VM mapping.

    Runs all snapshots in parallel. Per-host outcome:

    * VM mapping present + snapshot succeeds → ``ctx.snapshot_name`` set,
      ``ActionHostRun.snapshot_name`` persisted.
    * VM mapping present + snapshot fails → host's ``ActionHostRun.status``
      flipped to ``failed`` with an error message; caller drops the host
      from the inventory before the playbook runs.
    * No VM mapping → log notice, leave host in inventory without
      rollback safety.
    * Proxmox modules not installed → log once, no-op for all hosts.
    """
    try:
        from app.proxmox.client import ProxmoxClient  # noqa: PLC0415
        from app.proxmox.models import ProxmoxNode  # noqa: PLC0415
        from app.proxmox.vm_mapping import VMMapping  # noqa: PLC0415
    except ImportError:
        logger.debug(
            "action_group: proxmox modules not available; "
            "skipping snapshot phase for action_run %d",
            action_run_id,
        )
        for ctx in host_ctxs:
            ctx.step_log.append(
                "[snapshot] skipped — proxmox modules not installed "
                "(no rollback available on failure)"
            )
        return

    mappings, proxmox_nodes = await _load_vm_mappings(host_ctxs, VMMapping, ProxmoxNode)

    # ------------------------------------------------------------------ #
    # Step 1 — Proxmox I/O (parallel, no DB writes)                       #
    # ------------------------------------------------------------------ #
    # We do the actual snapshot calls in parallel via asyncio.gather, but
    # delay every DB write until step 2. Per-host DB writes inside a
    # gather would compete for the same session in tests (a single
    # ``task_session()`` is patched to yield the shared test session) and
    # in production would still serialise on the asyncpg pool. Keeping
    # the parallel section pure-I/O lets the Proxmox API calls actually
    # overlap.
    from app.workflows.steps.snapshot import snapshot_lock_wait_seconds  # noqa: PLC0415

    lock_wait_seconds = snapshot_lock_wait_seconds()

    tasks: list[Any] = []
    for ctx in host_ctxs:
        mapping = mappings.get(ctx.host_id)
        if mapping is None:
            ctx.step_log.append(
                "[snapshot] skipped — host has no Proxmox VM mapping "
                "(no rollback available on failure)"
            )
            continue
        proxmox_node = proxmox_nodes.get(mapping.proxmox_node_id)
        if proxmox_node is None:
            ctx.step_log.append("[snapshot] skipped — VM mapping references missing Proxmox node")
            continue
        tasks.append(
            _snapshot_member(
                ctx,
                mapping,
                proxmox_node,
                client_cls=ProxmoxClient,
                action_run_id=action_run_id,
                action_key=action_key,
                master_key=master_key,
                lock_wait_seconds=lock_wait_seconds,
                channel=channel,
                redis_client=redis_client,
            )
        )

    if tasks:
        await asyncio.gather(*tasks, return_exceptions=False)

    # ------------------------------------------------------------------ #
    # Step 2 — persist results sequentially in one session                #
    # ------------------------------------------------------------------ #
    await _persist_snapshot_results(host_ctxs)

    # Emit SSE host_status=failed for any snapshot-failed hosts (broker
    # publish is sync and fine outside the DB session).
    for ctx in host_ctxs:
        if ctx.snapshot_name == _SNAPSHOT_FAILED_MARKER:
            _publish(
                redis_client,
                channel,
                {"event": "host_status", "host_run_id": ctx.host_run_id, "status": "failed"},
                "snapshot-failed status",
            )


async def _load_vm_mappings(
    host_ctxs: list[_HostCtx],
    vm_mapping_model: Any,
    proxmox_node_model: Any,
) -> tuple[dict[int, Any], dict[int, Any]]:
    """Load every member's VM mapping and the Proxmox nodes they reference.

    Returns ``(mappings by host id, proxmox nodes by id)``.
    """
    from sqlalchemy import select  # noqa: PLC0415

    from app.db import task_session  # noqa: PLC0415

    # Load every VM mapping in one query, then attach to the ctxs.
    async with task_session() as db:
        host_ids = [c.host_id for c in host_ctxs]
        vm_map_result = await db.execute(
            select(vm_mapping_model).where(vm_mapping_model.host_id.in_(host_ids))
        )
        mappings = {m.host_id: m for m in vm_map_result.scalars().all()}

        # Cache proxmox node rows referenced by any mapping.
        node_ids = {m.proxmox_node_id for m in mappings.values()}
        proxmox_nodes: dict[int, Any] = {}
        if node_ids:
            node_result = await db.execute(
                select(proxmox_node_model).where(proxmox_node_model.id.in_(node_ids))
            )
            for n in node_result.scalars().all():
                proxmox_nodes[n.id] = n

    return mappings, proxmox_nodes


async def _snapshot_member(
    ctx: _HostCtx,
    mapping: Any,
    proxmox_node: Any,
    *,
    client_cls: Any,
    action_run_id: int,
    action_key: str,
    master_key: bytes,
    lock_wait_seconds: Any,
    channel: str,
    redis_client: Any,
) -> None:
    """Snapshot one member's VM, recording the result on *ctx* only (no DB writes)."""
    from app.crypto import decrypt_ssh_key  # noqa: PLC0415
    from app.workflows.steps.snapshot import create_snapshot  # noqa: PLC0415

    def _on_lock_wait(msg: str) -> None:
        line = f"[snapshot] {msg}"
        ctx.step_log.append(line)
        _publish(
            redis_client,
            channel,
            {"event": "output", "host_run_id": ctx.host_run_id, "text": line + "\n"},
            "snapshot wait",
        )

    token_secret = decrypt_ssh_key(proxmox_node.encrypted_token_secret, master_key)
    client = client_cls(
        api_url=proxmox_node.api_url,
        token_id=proxmox_node.token_id,
        token_secret=token_secret,
        verify_ssl=proxmox_node.verify_ssl,
        ca_cert_pem=proxmox_node.ca_cert_pem,
    )
    ctx.proxmox_client = client
    ctx.pve_node = mapping.pve_node_name
    ctx.vmid = mapping.vmid
    ctx.vm_type = mapping.vm_type

    try:
        snap_name = await create_snapshot(
            client,
            ctx.pve_node,
            ctx.vmid,
            action_run_id,
            ctx.vm_type,
            action_key,
            lock_wait_seconds=lock_wait_seconds,
            on_lock_wait=_on_lock_wait,
        )
    except Exception as exc:
        logger.exception(
            "action_group: snapshot failed for action_run %d host %d: %s",
            action_run_id,
            ctx.host_id,
            exc,
        )
        ctx.step_log.append(f"[snapshot] FAILED: {exc}")
        ctx.snapshot_name = _SNAPSHOT_FAILED_MARKER
        ctx.snapshot_error = f"Snapshot failed: {exc}"
        return

    ctx.snapshot_name = snap_name
    ctx.step_log.append(f"[snapshot] created {snap_name} on {ctx.pve_node}/{ctx.vmid}")


async def _persist_snapshot_results(host_ctxs: list[_HostCtx]) -> None:
    """Write each member's snapshot name, or its snapshot failure, to its row.

    Each row update is independent; we don't need cross-row consistency
    here — just a clean serial commit so concurrent writers (in tests
    or under asyncpg) can't trip the SQLAlchemy state machine.
    """
    from sqlalchemy import select  # noqa: PLC0415

    from app.db import task_session  # noqa: PLC0415
    from app.models.action_run import ActionHostRun  # noqa: PLC0415

    async with task_session() as db:
        for ctx in host_ctxs:
            if ctx.snapshot_name is None:
                # No VM mapping or skipped — nothing to persist.
                continue
            hr_result = await db.execute(
                select(ActionHostRun).where(ActionHostRun.id == ctx.host_run_id)
            )
            hr = hr_result.scalar_one()
            if ctx.snapshot_name == _SNAPSHOT_FAILED_MARKER:
                hr.status = "failed"
                hr.error_message = ctx.snapshot_error or "Snapshot failed"
                hr.finished_at = datetime.now(UTC)
                hr.output = "\n".join(ctx.step_log)
            else:
                hr.snapshot_name = ctx.snapshot_name
        await db.commit()


async def _verify_all(
    host_ctxs: list[_HostCtx],
    per_host_playbook_success: dict[int, bool],
    verify_playbook_path: Any,
    verify_timeout: int,
    roles_paths: tuple,
    extra_vars: dict | None,
    private_data_dir: str,
    channel: str,
    redis_client: Any,
    *,
    ai_verify_prompt: str | None = None,
    ai_verify_fail_closed: bool = False,
    action_run_id: int | None = None,
    dry_run: bool = False,
) -> None:
    """Verify every host that succeeded the playbook and was snapshotted.

    Per-host: run the pack's ``verify_playbook`` against a single-host
    inventory, or fall back to the built-in SSH/services/packages check.
    On verify failure, set ``ctx.verify_passed = False`` and stash an
    error message; the caller flips ``ActionHostRun.status`` to ``failed``
    during the final persist.

    Verify is a no-op for:
    * hosts whose playbook failed (already known failed)
    * hosts without a snapshot (no VM mapping, or snapshot disabled)
    """
    # Filter to the hosts that are actually eligible for verify, so we
    # only preload DB data for them.
    eligible = [
        ctx
        for ctx in host_ctxs
        if per_host_playbook_success.get(ctx.host_run_id, False) and ctx.snapshot_name is not None
    ]
    if not eligible:
        return

    preloaded: dict[int, tuple[Any, list, list]] = {}
    if verify_playbook_path is None:
        preloaded = await _preload_builtin_verify_inputs(eligible)

    async def _verify_one(ctx: _HostCtx) -> None:
        if verify_playbook_path is not None:
            _verify_member_with_pack_playbook(
                ctx,
                verify_playbook_path,
                verify_timeout,
                roles_paths,
                extra_vars,
                private_data_dir,
                channel,
                redis_client,
            )
        else:
            await _verify_member_with_builtin_checks(
                ctx,
                preloaded,
                ai_verify_prompt=ai_verify_prompt,
                ai_verify_fail_closed=ai_verify_fail_closed,
                action_run_id=action_run_id,
                dry_run=dry_run,
            )

    await asyncio.gather(*(_verify_one(ctx) for ctx in eligible), return_exceptions=False)


async def _preload_builtin_verify_inputs(
    eligible: list[_HostCtx],
) -> dict[int, tuple[Any, list, list]]:
    """Load each host's row and effective services and packages, by host id.

    For the built-in path, preload Host / effective services / packages
    for every eligible host in ONE session — we can't share an async
    SQLAlchemy session across parallel verify tasks (state machine
    races). The verify itself is SSH-bound and runs in parallel.
    """
    from sqlalchemy import select  # noqa: PLC0415

    from app.db import task_session  # noqa: PLC0415
    from app.models.host import Host  # noqa: PLC0415
    from app.packages.merge import get_effective_packages  # noqa: PLC0415
    from app.services.merge import get_effective_services  # noqa: PLC0415

    preloaded: dict[int, tuple[Any, list, list]] = {}
    async with task_session() as db:
        for ctx in eligible:
            host_result = await db.execute(select(Host).where(Host.id == ctx.host_id))
            host = host_result.scalar_one()
            effective_services = await get_effective_services(ctx.host_id, db)
            effective_packages = await get_effective_packages(ctx.host_id, db)
            preloaded[ctx.host_id] = (host, effective_services, effective_packages)
    return preloaded


def _verify_member_with_pack_playbook(
    ctx: _HostCtx,
    verify_playbook_path: Any,
    verify_timeout: int,
    roles_paths: tuple,
    extra_vars: dict | None,
    private_data_dir: str,
    channel: str,
    redis_client: Any,
) -> None:
    """Pack-supplied verify: run the pack's verify playbook against this host only."""
    from app.ansible_runtime.inventory import generate_inventory  # noqa: PLC0415
    from app.ansible_runtime.runner import run_ansible  # noqa: PLC0415

    try:
        inv_json = generate_inventory(
            ctx.ip,
            ctx.port,
            ctx.ssh_key_path,
            ssh_user=ctx.ssh_user,
            hostname=ctx.hostname,
            known_hosts_path=ctx.known_hosts_path,
        )
        verify_runner = run_ansible(
            playbook_path=verify_playbook_path,
            inventory_json=inv_json,
            private_data_dir=f"{private_data_dir}-verify-{ctx.host_id}",
            extra_vars=extra_vars,
            timeout=verify_timeout,
            roles_paths=list(roles_paths) if roles_paths else None,
        )
        verify_output: str = (
            verify_runner.stdout.read()
            if hasattr(verify_runner.stdout, "read")
            else str(verify_runner.stdout)
        )
        ctx.verify_passed = verify_runner.status == "successful"
        ctx.step_log.append(
            f"[verify] pack playbook exit={verify_runner.rc} "
            f"status={verify_runner.status} "
            f"passed={ctx.verify_passed}"
        )
        ctx.step_log.append("=== Verify playbook output ===")
        ctx.step_log.append(verify_output)
        if not ctx.verify_passed:
            ctx.verify_error = (
                f"Verify playbook failed (status={verify_runner.status}, rc={verify_runner.rc})"
            )
        _publish(
            redis_client,
            channel,
            {
                "event": "output",
                "host_run_id": ctx.host_run_id,
                "text": "=== Verify playbook output ===\n" + verify_output[-4000:],
            },
            "verify output",
        )
    except Exception as exc:
        logger.exception(
            "action_group: verify playbook errored for host %d: %s",
            ctx.host_id,
            exc,
        )
        ctx.verify_passed = False
        ctx.verify_error = f"Verify playbook error: {exc}"
        ctx.step_log.append(f"[verify] ERROR: {exc}")


async def _verify_member_with_builtin_checks(
    ctx: _HostCtx,
    preloaded: dict[int, tuple[Any, list, list]],
    *,
    ai_verify_prompt: str | None,
    ai_verify_fail_closed: bool,
    action_run_id: int | None,
    dry_run: bool,
) -> None:
    """Built-in SSH/services/packages check — runs with the data preloaded above.

    ``db=None`` is still correct: the only thing verify used a session
    for was the AI path, and that now opens its own, because these
    verifications run concurrently and one async session cannot serve
    them all.
    """
    try:
        from app.workflows.steps.verify import run_verification  # noqa: PLC0415

        host, effective_services, effective_packages = preloaded[ctx.host_id]
        verify_result = await run_verification(
            host,
            ctx.ssh_key_path,
            effective_services,
            effective_packages,
            ai_verify_prompt,
            None,
            ai_fail_closed=ai_verify_fail_closed,
            action_run_id=action_run_id,
            dry_run=dry_run,
        )
        ctx.verify_passed = bool(verify_result.get("passed"))
        ctx.step_log.append(
            f"[verify] passed={ctx.verify_passed} "
            f"services_ok={verify_result.get('services_ok')} "
            f"({verify_result.get('services_checked', 0)} checked) "
            f"packages_ok={verify_result.get('packages_ok')} "
            f"({verify_result.get('packages_checked', 0)} checked)"
        )
        if ai_result := verify_result.get("ai_result"):
            # Logged apart from `passed` because the two can
            # legitimately differ: under the default policy an
            # inconclusive verdict is a pass, and this line is
            # where an operator finds that out.
            ctx.step_log.append(
                f"[verify] ai verdict={ai_result.get('verdict')} "
                f"session={ai_result.get('session_id')}"
            )
            ctx.step_log.append("=== AI verification ===")
            ctx.step_log.append(str(ai_result.get("output") or ""))
        if not ctx.verify_passed:
            ctx.verify_error = f"Post-run verification failed: {verify_result}"
    except Exception as exc:
        logger.exception(
            "action_group: verification failed for host %d: %s",
            ctx.host_id,
            exc,
        )
        ctx.verify_passed = False
        ctx.verify_error = f"Verification error: {exc}"
        ctx.step_log.append(f"[verify] ERROR: {exc}")


async def _rollback_all(
    host_ctxs: list[_HostCtx],
    per_host_playbook_success: dict[int, bool],
) -> None:
    """Roll back the snapshot for every host whose action OR verify failed.

    Per-host policy (matches the per-host path):

    * Snapshot present + (playbook failed OR verify failed) → revert.
    * No snapshot → no-op (caller logged earlier that rollback isn't
      possible for this host).
    * Succeeded hosts → no-op; their snapshots are deleted in Phase F.

    Rollbacks run one host at a time (see the loop below); the underlying
    ``rollback_to_snapshot`` helper waits for SSH recovery before
    returning.
    """
    # Filter to hosts that actually need rollback.
    needs_rollback = [
        ctx
        for ctx in host_ctxs
        if ctx.snapshot_name is not None
        and ctx.proxmox_client is not None
        and (not per_host_playbook_success.get(ctx.host_run_id, False) or not ctx.verify_passed)
    ]
    if not needs_rollback:
        return

    hosts_by_id = await _load_hosts_by_id([c.host_id for c in needs_rollback])

    # Run rollbacks sequentially — they each open a DB session and
    # commit, which doesn't play nicely with the shared test session
    # under gather. In production the Proxmox API call itself
    # dominates the wall-clock cost; rollbacks for failed hosts in a
    # group dispatch are the exception, not the rule.
    for ctx in needs_rollback:
        await _rollback_member(ctx, hosts_by_id)


async def _load_hosts_by_id(host_ids: list[int]) -> dict[int, Any]:
    """Load the given ``Host`` rows in one session, keyed by id.

    The rollback helper uses ``db`` to mark the host out-of-sync. Each
    rollback gets its own short-lived session (see ``_rollback_member``);
    only the rows are shared.
    """
    from sqlalchemy import select  # noqa: PLC0415

    from app.db import task_session  # noqa: PLC0415
    from app.models.host import Host  # noqa: PLC0415

    hosts_by_id: dict[int, Any] = {}
    async with task_session() as db:
        host_result = await db.execute(select(Host).where(Host.id.in_(host_ids)))
        for h in host_result.scalars().all():
            hosts_by_id[h.id] = h
    return hosts_by_id


async def _rollback_member(ctx: _HostCtx, hosts_by_id: dict[int, Any]) -> None:
    """Restore one member's snapshot, then delete it if the restore succeeded."""
    from app.db import task_session  # noqa: PLC0415
    from app.workflows.steps.rollback import rollback_to_snapshot  # noqa: PLC0415

    ctx.step_log.append(f"[rollback] restoring {ctx.snapshot_name}")
    try:
        host = hosts_by_id.get(ctx.host_id)
        if host is None:
            ctx.step_log.append("[rollback] ERROR: host row missing")
            return
        async with task_session() as db:
            # rollback_to_snapshot writes to the session via ``flush``;
            # we commit here for the persistent side-effect (host
            # marked out_of_sync).
            rb = await rollback_to_snapshot(
                ctx.proxmox_client,
                ctx.pve_node,
                ctx.vmid,
                ctx.snapshot_name,
                host,
                ctx.ssh_key_path,
                db,
                vm_type=ctx.vm_type,
            )
            await db.commit()
        ctx.step_log.append(f"[rollback] success={rb.get('success')} {rb.get('error', '')}".strip())
        if rb.get("success") and ctx.snapshot_name is not None:
            await _delete_snapshot_after_rollback(ctx)
    except Exception as exc:
        logger.exception(
            "action_group: rollback failed for host %d: %s",
            ctx.host_id,
            exc,
        )
        ctx.step_log.append(f"[rollback] ERROR: {exc}")


async def _delete_snapshot_after_rollback(ctx: _HostCtx) -> None:
    """Delete the snapshot a successful rollback restored; a failure is only a warning."""
    from app.workflows.steps.cleanup import delete_snapshot  # noqa: PLC0415

    try:
        await delete_snapshot(
            ctx.proxmox_client,
            ctx.pve_node,
            ctx.vmid,
            ctx.snapshot_name,
            ctx.vm_type,
        )
        ctx.step_log.append(f"[cleanup] snapshot {ctx.snapshot_name} deleted after rollback")
    except Exception as clean_exc:
        logger.warning(
            "action_group: snapshot cleanup after rollback failed for host %d: %s",
            ctx.host_id,
            clean_exc,
        )
        ctx.step_log.append(f"[cleanup] WARN: {clean_exc}")


async def _cleanup_all(
    host_ctxs: list[_HostCtx],
    per_host_playbook_success: dict[int, bool],
) -> None:
    """Delete the pre-action snapshot on every succeeded host.

    Per-host policy (matches the per-host path):

    * Succeeded host + snapshot present → delete snapshot.
    * Failed host → leave snapshot in place; Phase E rollback may have
      already reverted to it, but the snapshot itself stays for the
      periodic cleanup task to reap.
    * Cleanup errors are non-fatal — the action succeeded, an orphan
      snapshot is cosmetic.
    """
    from app.workflows.steps.cleanup import delete_snapshot  # noqa: PLC0415

    async def _clean(ctx: _HostCtx) -> None:
        if ctx.snapshot_name is None or ctx.proxmox_client is None:
            return
        playbook_ok = per_host_playbook_success.get(ctx.host_run_id, False)
        if not (playbook_ok and ctx.verify_passed):
            return

        try:
            await delete_snapshot(
                ctx.proxmox_client, ctx.pve_node, ctx.vmid, ctx.snapshot_name, ctx.vm_type
            )
            ctx.step_log.append(f"[cleanup] snapshot {ctx.snapshot_name} deleted")
        except Exception as exc:
            logger.warning(
                "action_group: snapshot cleanup failed for host %d: %s",
                ctx.host_id,
                exc,
            )
            ctx.step_log.append(f"[cleanup] WARN: {exc}")

    await asyncio.gather(*(_clean(ctx) for ctx in host_ctxs), return_exceptions=False)


def _publish(redis_client: Any, channel: str, payload: dict, what: str) -> None:
    """Publish one SSE event; a broker failure is logged at debug and dropped."""
    try:
        redis_client.publish(channel, json.dumps(payload))
    except Exception:
        logger.debug("action_group: SSE publish failed for %s", what, exc_info=True)


def _publish_global(redis_client: Any, channel: str, text: str) -> None:
    """Publish a run-scoped log line (no host_run_id binding)."""
    _publish(
        redis_client,
        channel,
        {"event": "output", "host_run_id": None, "text": text},
        "run-scoped log",
    )


# ---------------------------------------------------------------------------
# Run-level helpers
# ---------------------------------------------------------------------------


async def _aggregate_and_finalise(action_run_id: int, channel: str, redis_client: Any) -> None:
    """Walk ActionHostRun rows, set the run's terminal status, publish."""
    from sqlalchemy import select

    from app.db import task_session
    from app.models.action_run import ActionHostRun, ActionRun

    async with task_session() as db:
        hr_result = await db.execute(
            select(ActionHostRun).where(ActionHostRun.action_run_id == action_run_id)
        )
        host_runs = list(hr_result.scalars().all())
        final_status, succeeded, total = _roll_up_host_runs(host_runs)

        run_result = await db.execute(select(ActionRun).where(ActionRun.id == action_run_id))
        run = run_result.scalar_one()
        if run.status != "cancelled":
            run.status = final_status
            run.finished_at = datetime.now(UTC)
        elif run.finished_at is None:
            # Cancelled mid-playbook: the cancel left the end to us (BUG-104).
            run.finished_at = datetime.now(UTC)
        await db.commit()

        logger.info(
            "action_group: action_run %d finished — %s (%d/%d hosts succeeded)",
            action_run_id,
            run.status,
            succeeded,
            total,
        )

        await _queue_post_run_followups(db, run, host_runs, succeeded, action_run_id)

    _publish(redis_client, channel, {"event": "status", "status": run.status}, "terminal status")


def _roll_up_host_runs(host_runs: list[Any]) -> tuple[str, int, int]:
    """Return ``(run status, succeeded count, total)`` for a run's host rows."""
    succeeded = sum(1 for hr in host_runs if hr.status == "succeeded")
    failed = sum(1 for hr in host_runs if hr.status in ("failed", "skipped"))
    total = len(host_runs)

    if failed == 0:
        final_status = "succeeded"
    elif succeeded == 0:
        final_status = "failed"
    else:
        final_status = "partial"
    return final_status, succeeded, total


async def _queue_post_run_followups(
    db: Any,
    run: Any,
    host_runs: list[Any],
    succeeded: int,
    action_run_id: int,
) -> None:
    """Queue the action's post-run module syncs and registrations per succeeded host.

    Post-run module sync fan-out: for group-dispatched actions
    (supports_host=false) one invocation produces N per-host
    outcomes; we queue post_run_sync for each host whose row is
    ``succeeded``, and the member release in the caller's
    ``finally`` starts them. Skipped on dry-run, on cancel, and on whole-
    run failure. Failures here are logged but never affect the
    action's terminal status -- the action itself already
    completed.
    """
    from app.actions.validation import DRY_RUN_PARAM  # noqa: PLC0415

    run_parameters = run.parameters or {}
    dry_run = bool(run_parameters.get(DRY_RUN_PARAM, False))
    if run.status in ("cancelled", "failed") or dry_run or succeeded == 0:
        return

    from app.actions.registry import ACTION_REGISTRY

    action = ACTION_REGISTRY.get(run.action_key)
    post_run_sync_modules: tuple[str, ...] = action.post_run_sync if action is not None else ()
    post_run_register_decls: dict[str, tuple[dict, ...]] = (
        dict(action.post_run_register) if action is not None else {}
    )
    if not (post_run_sync_modules or post_run_register_decls):
        return

    for hr in host_runs:
        if hr.status != "succeeded":
            continue
        if hr.host_id is None:
            # Host deleted while the play ran. The row keeps
            # its transcript (BUG-77) but there is nothing
            # left to sync or register against.
            continue
        if post_run_sync_modules:
            await _queue_post_run_sync_for_host(db, run, hr, post_run_sync_modules, action_run_id)
        if post_run_register_decls:
            await _dispatch_post_run_register_for_host(
                db, run, hr, post_run_register_decls, action_run_id
            )


async def _queue_post_run_sync_for_host(
    db: Any,
    run: Any,
    hr: Any,
    post_run_sync_modules: tuple[str, ...],
    action_run_id: int,
) -> None:
    """Queue the post-run module sync for one host; a failure is logged, not raised."""
    from app.sync.post_run import queue_post_run_sync

    try:
        queued_ids = await queue_post_run_sync(
            db,
            host_id=hr.host_id,
            modules=post_run_sync_modules,
            triggered_by_user_id=run.triggered_by_user_id,
        )
        if queued_ids:
            await db.commit()
            logger.info(
                "action_group: queued post_run_sync for "
                "action_run %d host %d -- modules=%s job_ids=%s",
                action_run_id,
                hr.host_id,
                list(post_run_sync_modules),
                queued_ids,
            )
    except Exception:
        logger.exception(
            "action_group: post_run_sync queueing failed for action_run %d host %d",
            action_run_id,
            hr.host_id,
        )


async def _dispatch_post_run_register_for_host(
    db: Any,
    run: Any,
    hr: Any,
    post_run_register_decls: dict[str, tuple[dict, ...]],
    action_run_id: int,
) -> None:
    """Run the post-run registrations for one host; a failure is logged, not raised."""
    from app.sync.post_run import dispatch_post_run_register

    try:
        inserted = await dispatch_post_run_register(
            db,
            host_id=hr.host_id,
            declarations=post_run_register_decls,
            triggered_by_user_id=run.triggered_by_user_id,
        )
        if inserted:
            await db.commit()
            logger.info(
                "action_group: dispatched post_run_register for "
                "action_run %d host %d -- inserted=%s",
                action_run_id,
                hr.host_id,
                inserted,
            )
    except Exception:
        logger.exception(
            "action_group: post_run_register dispatch failed for action_run %d host %d",
            action_run_id,
            hr.host_id,
        )


async def _mark_run_failed(action_run_id: int, error_message: str, channel: str, r) -> None:
    from sqlalchemy import select

    from app.db import task_session
    from app.models.action_run import ActionHostRun, ActionRun

    try:
        async with task_session() as db:
            run_result = await db.execute(select(ActionRun).where(ActionRun.id == action_run_id))
            run = run_result.scalar_one_or_none()
            if run is not None:
                run.status = "failed"
                run.error_message = error_message
                run.finished_at = datetime.now(UTC)
            # Mark in-flight host rows as failed too so the UI is consistent.
            hr_result = await db.execute(
                select(ActionHostRun).where(
                    ActionHostRun.action_run_id == action_run_id,
                    ActionHostRun.status.in_(["queued", "running"]),
                )
            )
            for hr in hr_result.scalars().all():
                hr.status = "failed"
                hr.error_message = error_message
                hr.finished_at = datetime.now(UTC)
            await db.commit()
    except Exception:
        logger.exception("action_group: could not mark action_run %d as failed", action_run_id)

    try:
        r.publish(channel, json.dumps({"event": "status", "status": "failed"}))
    except Exception:
        pass  # nosec B110


async def _mark_run_cancelled(action_run_id: int, channel: str, r) -> None:
    from sqlalchemy import select

    from app.db import task_session
    from app.models.action_run import ActionHostRun, ActionRun

    try:
        async with task_session() as db:
            run_result = await db.execute(select(ActionRun).where(ActionRun.id == action_run_id))
            run = run_result.scalar_one_or_none()
            if run is not None:
                run.status = "cancelled"
                run.finished_at = datetime.now(UTC)
            hr_result = await db.execute(
                select(ActionHostRun).where(
                    ActionHostRun.action_run_id == action_run_id,
                    ActionHostRun.status == "queued",
                )
            )
            for hr in hr_result.scalars().all():
                hr.status = "cancelled"
            await db.commit()
    except Exception:
        logger.exception("action_group: could not mark action_run %d as cancelled", action_run_id)
    try:
        r.publish(channel, json.dumps({"event": "status", "status": "cancelled"}))
    except Exception:
        pass  # nosec B110
