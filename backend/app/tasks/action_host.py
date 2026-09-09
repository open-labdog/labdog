"""Per-host action executor Celery task.

Each ActionHostRun is processed independently by this task.  The
orchestrator (action_orchestrator.py) dispatches one instance per host
inside a batch, waits for the whole batch, then moves on.

The run is driven through a prologue and five phases. The phase letters
match :mod:`app.tasks.action_group`, which runs the same lifecycle across
a whole group in a single playbook — same names, same order, same
meaning, so a change to one has an obvious counterpart in the other:

* **Prologue**: cancel check, claim-or-defer against the per-host lock,
  load run + host + key + action, load any Proxmox VM mapping, write the
  key to tmpfs, preflight the host, render inventory and extra-vars.
* **Phase A — snapshot**: destructive actions with a VM mapping and
  ``snapshot_enabled`` get a pre-run Proxmox snapshot. A failure here
  ends the run before the playbook touches anything.
* **Phase B — playbook**: one ``ansible-playbook`` run against the single
  host, streamed to the SSE channel task-by-task with a heartbeat.
* **Phase D — verify**: for a run that snapshotted and has
  ``verify_enabled``, either the pack's verify playbook or the built-in
  service/package checks plus AI verification.
* **Phase E — rollback**: a failed run with a snapshot and
  ``auto_rollback`` reverts to it.
* **Phase F — cleanup**: a succeeded run deletes its snapshot; so does a
  successful rollback.
* **Phase G — persist**: terminal status onto the row, then the
  manifest's post-run sync and register hooks.

There is no Phase C: routing per-host events back to rows is the group
path's problem, and this task owns exactly one row.
"""

import asyncio
import logging
import os
import shutil
import tempfile
import threading
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.tasks import celery_app

logger = logging.getLogger(__name__)

MAX_OUTPUT_BYTES = 1_048_576  # 1 MB


# ---------------------------------------------------------------------------
# Celery task entry point
# ---------------------------------------------------------------------------


@celery_app.task(
    bind=True,
    name="app.tasks.action_host.run_action_host",
    queue="long_running",
)
def run_action_host(self, action_run_id: int, host_run_id: int) -> dict:
    """Run ansible-runner for a single host within an action run.

    Args:
        action_run_id: ID of the parent ActionRun.
        host_run_id: ID of the ActionHostRun record to drive.

    Returns:
        A dict summarising the outcome, e.g.
        ``{"action_run_id": 1, "host_run_id": 2}``.
    """
    asyncio.run(_run_action_host_async(action_run_id, host_run_id))
    return {"action_run_id": action_run_id, "host_run_id": host_run_id}


# ---------------------------------------------------------------------------
# Preflight reachability
# ---------------------------------------------------------------------------


async def _stored_host_key_entry(host_id: int) -> str | None:
    """The SSH host key LabDog has recorded for *host_id*, if any.

    Read in its own session immediately before the inventory is built:
    preflight connects over asyncssh and records the key on first
    contact, so the value cached when the run was loaded can already be
    stale by the time it matters.
    """
    from sqlalchemy import select

    from app.db import task_session
    from app.models.host import Host

    async with task_session() as db:
        return (
            await db.execute(select(Host.ssh_host_key_entry).where(Host.id == host_id))
        ).scalar_one_or_none()


async def _preflight_reachable(host_id: int, ssh_key_path: str) -> tuple[bool, str | None]:
    """Bounded SSH liveness probe run before the action playbook.

    Reuses :func:`app.ssh_utils.ssh_connect_host` (inherits the
    ``ssh.connect_timeout`` setting and TOFU host-key handling) to run a
    trivial command. Two attempts with a short pause tolerate a
    transient blip while still failing a genuinely dead/hung host in
    ~25s instead of the full playbook timeout.

    Returns ``(True, None)`` on success, ``(False, reason)`` on failure.
    A host-key mismatch is a real, actionable condition and is surfaced
    with its own message rather than a generic timeout.
    """
    import asyncio

    import asyncssh
    from sqlalchemy import select

    from app.crypto import decrypt_ssh_key, get_master_key
    from app.db import task_session
    from app.models.host import Host
    from app.models.ssh_key import SSHKey
    from app.ssh_utils import HostKeyMismatchError, ssh_connect_host

    last_error = "unreachable"
    for attempt in range(2):
        if attempt:
            await asyncio.sleep(5)
        try:
            async with task_session() as db:
                host = (
                    await db.execute(select(Host).where(Host.id == host_id))
                ).scalar_one_or_none()
                if host is None:
                    return False, "host row not found"
                ssh_key = (
                    await db.execute(select(SSHKey).where(SSHKey.id == host.ssh_key_id))
                ).scalar_one_or_none()
                if ssh_key is None:
                    return False, "host has no SSH key"
                private_pem = decrypt_ssh_key(ssh_key.encrypted_private_key, get_master_key())
                imported_key = asyncssh.import_private_key(private_pem)
                async with ssh_connect_host(host, db, client_keys=[imported_key]) as conn:
                    await conn.run("true", check=False)
            return True, None
        except HostKeyMismatchError as exc:
            # Not transient — a re-key or MITM. Don't retry; surface it.
            return False, f"SSH host key mismatch: {exc}"
        except (TimeoutError, OSError, asyncssh.Error) as exc:
            last_error = str(exc) or "connection timed out"
        except Exception as exc:  # noqa: BLE001
            last_error = str(exc) or exc.__class__.__name__
    return False, last_error


# ---------------------------------------------------------------------------
# Run state
# ---------------------------------------------------------------------------


@dataclass
class _RunCtx:
    """Bookkeeping that outlives every phase of one host run.

    The equivalent of ``action_group._HostCtx`` for the single-host path.
    It exists for the same reason: the phases below need to publish to the
    same SSE channel, append to the same step log, and hand the ``finally``
    block enough to clean up whatever was allocated — without passing eight
    positional arguments each time.
    """

    action_run_id: int
    host_run_id: int
    channel: str
    r: Any
    private_data_dir: str
    #: None until mkstemp succeeds, so the finally block can tell "never
    #: created" from "created and needs unlinking".
    ssh_key_path: str | None = None
    #: ansible-runner data dirs derived from private_data_dir (the verify
    #: pass creates its own). Tracked so the finally block removes every
    #: one of them, not just the base.
    extra_data_dirs: list[str] = field(default_factory=list)
    #: Whether we claimed the host, so the finally block knows whether to
    #: release it via dispatch-next-pending. On the defer path we leave
    #: queue ownership to the in-flight op's finally hook.
    claimed: bool = False
    claimed_host_id: int | None = None
    step_log: list[str] = field(default_factory=list)


@dataclass
class _RunSpec:
    """Everything read from the database before the envelope starts.

    Loaded in one session and cached as plain scalars, because every phase
    after it runs outside that session — an attribute read on a detached
    ORM object would raise, and re-opening a session per phase would make
    the run's view of the world change underneath it.
    """

    host_id: int
    host_ip: str
    host_hostname: str
    host_port: int
    ssh_user: str
    private_key_text: str
    parameters: dict
    playbook_path: str
    action_key: str
    destructive: bool
    roles_paths: tuple
    verify_playbook_path: str | None
    verify_timeout: int
    ai_verify_prompt: str | None
    ai_verify_fail_closed: bool
    playbook_timeout: int | None
    metrics_backend: dict | None
    #: Run-time toggles mirrored from ScheduledAction at dispatch time.
    #: Honoured the same way as action_group.py — see Phases A/D/E.
    #: Ignored when the action is non-destructive (no envelope runs).
    snapshot_enabled: bool
    verify_enabled: bool
    auto_rollback: bool
    #: Manifest-declared post-run module syncs. Fired against the same
    #: host after a successful, non-dry-run completion so labdog's desired
    #: state is re-enforced. See ``app.sync.post_run.dispatch_post_run_sync``.
    post_run_sync: tuple[str, ...]
    #: Manifest-declared post-run resource registrations. After success the
    #: helper inserts host-scope override rows for each declared resource
    #: (skipping operator-managed collisions) and then dispatches a
    #: follow-up sync to refresh the UI tabs. See
    #: ``app.sync.post_run.dispatch_post_run_register``.
    post_run_register: dict[str, tuple[dict, ...]]
    triggered_by_user_id: int | None
    master_key: bytes


@dataclass
class _Envelope:
    """Proxmox snapshot state for this run, or an empty one when there is none."""

    client: Any = None
    pve_node: str | None = None
    vmid: int | None = None
    vm_type: str = "qemu"
    snapshot_name: str | None = None

    @property
    def can_snapshot(self) -> bool:
        return self.client is not None and self.pve_node is not None and self.vmid is not None

    @property
    def has_snapshot(self) -> bool:
        return self.snapshot_name is not None and self.client is not None


@dataclass
class _PlaybookResult:
    """Outcome of the main ansible-runner invocation (Phase B)."""

    output: str
    success: bool
    exit_code: int
    status: str


# ---------------------------------------------------------------------------
# Channel + row helpers
#
# Seven phases end the run early, and each used to repeat the same three
# steps: set the terminal fields on the row, commit, publish a host_status
# event. That repetition is where a divergence hides — one of them
# forgetting ``finished_at`` looks like nothing until a sweeper trips over
# a row that never finished.
# ---------------------------------------------------------------------------


def _publish(ctx: _RunCtx, payload: dict) -> None:
    """Publish one SSE event, swallowing broker hiccups.

    The DB row is authoritative; a lost notification only means the live
    view lags until the client refetches.
    """
    import json

    try:
        ctx.r.publish(ctx.channel, json.dumps(payload))
    except Exception:
        logger.debug("action_host: SSE publish failed", exc_info=True)


def _publish_status(ctx: _RunCtx, status: str, *, host_id: int | None = None) -> None:
    payload: dict = {
        "event": "host_status",
        "host_run_id": ctx.host_run_id,
        "status": status,
    }
    if host_id is not None:
        payload["host_id"] = host_id
    _publish(ctx, payload)


def _log_step(ctx: _RunCtx, msg: str) -> None:
    """Append a line to the step log AND stream it to the SSE channel so
    the UI sees snapshot / verify / rollback / cleanup progress live, not
    just at the end. Broker hiccups are silent — the DB output is
    persisted regardless.
    """
    ctx.step_log.append(msg)
    _publish(ctx, {"event": "output", "host_run_id": ctx.host_run_id, "text": msg + "\n"})


async def _finish_early(
    ctx: _RunCtx,
    status: str,
    *,
    error: str | None = None,
    include_output: bool = False,
) -> None:
    """Write a terminal status onto the row, then announce it.

    ``include_output`` persists whatever the step log holds so far — used
    once the run has produced output worth keeping (preflight, snapshot),
    and skipped before that, where the log is empty and writing it would
    only overwrite whatever the row already carried.
    """
    from sqlalchemy import select

    from app.db import task_session
    from app.models.action_run import ActionHostRun

    async with task_session() as db:
        hr = (
            await db.execute(select(ActionHostRun).where(ActionHostRun.id == ctx.host_run_id))
        ).scalar_one_or_none()
        if hr is not None:
            hr.status = status
            if error is not None:
                hr.error_message = error
            hr.finished_at = datetime.now(UTC)
            if include_output:
                hr.output = "\n".join(ctx.step_log)
            await db.commit()
    _publish_status(ctx, status)


# ---------------------------------------------------------------------------
# Prologue: cancel, claim, load
# ---------------------------------------------------------------------------


async def _cancelled_before_start(ctx: _RunCtx) -> bool:
    """Honour the cancel token before doing any real work."""
    from sqlalchemy import select

    from app.db import task_session
    from app.models.action_run import ActionHostRun

    if not ctx.r.exists(f"actions.cancel.{ctx.action_run_id}"):
        return False

    async with task_session() as db:
        hr = (
            await db.execute(select(ActionHostRun).where(ActionHostRun.id == ctx.host_run_id))
        ).scalar_one_or_none()
        if hr is not None and hr.status == "queued":
            hr.status = "cancelled"
            await db.commit()
    _publish_status(ctx, "cancelled")
    return True


async def _claim_or_defer(ctx: _RunCtx) -> bool:
    """Serialize against any in-flight op on this host.

    Competing ops are a sync, another host-targeted action, or a
    group-targeted action that includes this host as a member. On busy →
    mark ActionRun + ActionHostRun as ``pending`` and return False;
    dispatch-next-pending on the in-flight op's finally hook will re-fire
    us when the host frees up.

    Returns True when the host was claimed and the run may proceed.
    """
    from sqlalchemy import select

    from app.db import task_session
    from app.models.action_run import ActionHostRun, ActionRun
    from app.tasks.host_lock import acquire_host_lock, check_host_busy, format_pending_reason

    async with task_session() as db:
        hr_row = (
            await db.execute(select(ActionHostRun).where(ActionHostRun.id == ctx.host_run_id))
        ).scalar_one_or_none()
        if hr_row is None:
            logger.warning("action_host: host_run %d missing — exiting", ctx.host_run_id)
            return False

        host_id_for_lock = hr_row.host_id
        if host_id_for_lock is None:
            # The host was deleted between dispatch and pickup. The row
            # survives that now (BUG-77) and keeps whatever output it had,
            # but there is nothing left to run against and nothing to lock on.
            hr_row.status = "failed"
            hr_row.error_message = "Host was deleted before this run started"
            hr_row.finished_at = datetime.now(UTC)
            await db.commit()
            _publish_status(ctx, "failed")
            return False

        await acquire_host_lock(db, host_id_for_lock)
        blocker = await check_host_busy(
            db, host_id_for_lock, exclude_action_run_id=ctx.action_run_id
        )
        if blocker is not None:
            reason = await format_pending_reason(db, blocker)
            hr_row.status = "pending"
            hr_row.pending_reason = reason
            # Only flip the parent ActionRun to ``pending`` when this is the
            # sole per-host row (single-host target). For the
            # group-with-supports_host=True dispatch shape the parent
            # ActionRun has many ActionHostRuns and our defer is per-row; the
            # parent's status should reflect the union, not just one member.
            run_row = (
                await db.execute(select(ActionRun).where(ActionRun.id == ctx.action_run_id))
            ).scalar_one_or_none()
            if (
                run_row is not None
                and run_row.host_id is not None
                and run_row.status in ("queued", "running")
            ):
                run_row.status = "pending"
                run_row.pending_reason = reason
            await db.commit()
            logger.info(
                "action_host: deferred action_run=%d host_run=%d (host %d busy: %s)",
                ctx.action_run_id,
                ctx.host_run_id,
                host_id_for_lock,
                reason,
            )
            return False

        # Free → claim it *here*, inside the transaction still holding the
        # advisory lock.
        #
        # BUG-62: this used to leave the row in ``queued`` and let the loader
        # session below flip it, so the lock was released at this commit with
        # nothing yet marking the host busy. A concurrent run_host_sync
        # entering its own gate in that window saw check_host_busy return None
        # and claimed the same host — an action and a sync running against one
        # host at once, which is the apt/nftables race the lock exists to
        # prevent. host_sync_orchestrator does not have this shape: its
        # _claim_or_defer and _prepare_run share one session precisely so the
        # gate and the flip commit together (see its BUG-38 note).
        hr_row.status = "running"
        hr_row.started_at = datetime.now(UTC)
        ctx.claimed = True
        ctx.claimed_host_id = host_id_for_lock
        await db.commit()
    return True


async def _load_run_spec(ctx: _RunCtx) -> _RunSpec | None:
    """Load run + host + key + action in one session, or end the run.

    Returns None when the run cannot proceed; the row has already been
    given its terminal status and announced by then.
    """
    from sqlalchemy import select

    from app.actions.registry import ACTION_REGISTRY
    from app.crypto import decrypt_ssh_key, get_master_key
    from app.db import task_session
    from app.models.action_run import ActionHostRun, ActionRun
    from app.models.host import Host
    from app.models.ssh_key import SSHKey

    async with task_session() as db:
        hr: ActionHostRun = (
            await db.execute(select(ActionHostRun).where(ActionHostRun.id == ctx.host_run_id))
        ).scalar_one()
        run: ActionRun = (
            await db.execute(select(ActionRun).where(ActionRun.id == ctx.action_run_id))
        ).scalar_one()

        host: Host | None = (
            await db.execute(select(Host).where(Host.id == hr.host_id))
        ).scalar_one_or_none()
        if host is None:
            # Deleted in the window between the claim above and here.
            # ``scalar_one`` used to raise straight into the generic handler;
            # say what happened instead, since the row and its output now
            # outlive the host (BUG-77).
            hr.status = "failed"
            hr.error_message = "Host was deleted before this run started"
            hr.finished_at = datetime.now(UTC)
            await db.commit()
            _publish_status(ctx, "failed")
            return None

        if host.ssh_key_id is None:
            hr.status = "skipped"
            hr.error_message = "Host has no SSH key configured"
            hr.finished_at = datetime.now(UTC)
            await db.commit()
            _publish_status(ctx, "skipped")
            return None

        ssh_key: SSHKey = (
            await db.execute(select(SSHKey).where(SSHKey.id == host.ssh_key_id))
        ).scalar_one()
        master_key = get_master_key()
        private_key_text = decrypt_ssh_key(ssh_key.encrypted_private_key, master_key)

        action = ACTION_REGISTRY.get(run.action_key)
        if action is None:
            # Registry may be stale (pack synced after worker started).
            # Reload from disk once and retry before failing the run.
            from app.actions.registry import reload_registry_async  # noqa: PLC0415

            await reload_registry_async(db)
            action = ACTION_REGISTRY.get(run.action_key)
        if action is None:
            hr.status = "failed"
            hr.error_message = f"Action '{run.action_key}' not found in registry"
            hr.finished_at = datetime.now(UTC)
            await db.commit()
            _publish_status(ctx, "failed")
            return None

        # Already flipped to ``running`` under the advisory lock in the claim
        # block above. Re-check rather than re-write: a cancel landing between
        # the two sessions is the one thing that can legitimately have moved
        # it, and clobbering that back to ``running`` would run an action the
        # operator cancelled.
        if hr.status != "running":
            logger.info(
                "action_host: host_run %d left 'running' after the claim (now %r) — not proceeding",
                ctx.host_run_id,
                hr.status,
            )
            return None

        return _RunSpec(
            host_id=host.id,
            host_ip=host.ip_address,
            host_hostname=host.hostname,
            host_port=host.ssh_port or 22,
            ssh_user=ssh_key.ssh_user or "root",
            private_key_text=private_key_text,
            parameters=dict(run.parameters or {}),
            playbook_path=action.playbook_path,
            action_key=run.action_key,
            destructive=action.destructive,
            roles_paths=action.roles_paths,
            verify_playbook_path=action.verify_playbook_path,
            verify_timeout=action.verify_timeout_seconds,
            ai_verify_prompt=action.ai_verify_prompt,
            ai_verify_fail_closed=action.ai_verify_fail_closed,
            playbook_timeout=action.playbook_timeout_seconds,
            metrics_backend=action.metrics_backend,
            snapshot_enabled=bool(run.snapshot_enabled),
            verify_enabled=bool(run.verify_enabled),
            auto_rollback=bool(run.auto_rollback),
            post_run_sync=action.post_run_sync,
            post_run_register=dict(action.post_run_register),
            triggered_by_user_id=run.triggered_by_user_id,
            master_key=master_key,
        )


async def _load_envelope(ctx: _RunCtx, spec: _RunSpec) -> _Envelope:
    """Load the Proxmox VM mapping when the run will snapshot.

    The snapshot is taken before the playbook runs and (when
    ``auto_rollback`` is on) reverted on failure. Toggle semantics mirror
    ``action_group.py`` Phases A/D/E:

    * ``snapshot_enabled=False``  → skip Phase A (no Proxmox client loaded,
      no snapshot taken). Rollback can't run without a snapshot so it is
      implicitly disabled too.
    * ``verify_enabled=False``    → skip Phase D (no post-run verify).
    * ``auto_rollback=False``     → skip Phase E (snapshot left in place on
      failure instead of reverted).
    """
    from sqlalchemy import select

    from app.crypto import decrypt_ssh_key
    from app.db import task_session

    env = _Envelope()
    if not (spec.destructive and spec.snapshot_enabled):
        return env

    try:
        from app.proxmox.client import ProxmoxClient  # noqa: PLC0415
        from app.proxmox.models import ProxmoxNode  # noqa: PLC0415
        from app.proxmox.vm_mapping import VMMapping  # noqa: PLC0415

        async with task_session() as db:
            vm_mapping = (
                await db.execute(select(VMMapping).where(VMMapping.host_id == spec.host_id))
            ).scalar_one_or_none()
            if vm_mapping is not None:
                env.pve_node = vm_mapping.pve_node_name
                env.vmid = vm_mapping.vmid
                env.vm_type = vm_mapping.vm_type
                proxmox_node = (
                    await db.execute(
                        select(ProxmoxNode).where(ProxmoxNode.id == vm_mapping.proxmox_node_id)
                    )
                ).scalar_one()
                token_secret = decrypt_ssh_key(proxmox_node.encrypted_token_secret, spec.master_key)
                env.client = ProxmoxClient(
                    api_url=proxmox_node.api_url,
                    token_id=proxmox_node.token_id,
                    token_secret=token_secret,
                    verify_ssl=proxmox_node.verify_ssl,
                    ca_cert_pem=proxmox_node.ca_cert_pem,
                )
    except ImportError:
        logger.debug(
            "action_host: proxmox modules not available; "
            "snapshot steps will be skipped for action_run %d",
            ctx.action_run_id,
        )
    return env


def _write_ssh_key(ctx: _RunCtx, private_key_text: str) -> None:
    """Write the decrypted private key to the tmpfs path already allocated."""
    assert ctx.ssh_key_path is not None
    with open(ctx.ssh_key_path, "w") as fh:
        fh.write(private_key_text)
        if not private_key_text.endswith("\n"):
            fh.write("\n")
    os.chmod(ctx.ssh_key_path, 0o600)


async def _preflight_ok(ctx: _RunCtx, spec: _RunSpec) -> bool:
    """Bounded reachability probe before any snapshot.

    Runs before the snapshot so we never snapshot a host we can't reach. A
    dead/hung host fails here in ~25s instead of tying up a worker slot for
    the full playbook timeout. Opt-out via ``actions.preflight_enabled``.
    """
    from app.settings_service import get_setting_cached_typed

    try:
        preflight_on = bool(int(get_setting_cached_typed("actions.preflight_enabled")))
    except Exception:
        preflight_on = True
    if not preflight_on:
        return True

    assert ctx.ssh_key_path is not None
    ok, preflight_err = await _preflight_reachable(spec.host_id, ctx.ssh_key_path)
    if not ok:
        _log_step(ctx, f"[preflight] FAILED: {preflight_err}")
        await _finish_early(
            ctx,
            "failed",
            error=f"host unreachable (preflight): {preflight_err}",
            include_output=True,
        )
        return False
    _log_step(ctx, "[preflight] host reachable")
    return True


async def _build_inventory(ctx: _RunCtx, spec: _RunSpec) -> tuple[str, dict | None, bool]:
    """Render the inventory and extra-vars for this run.

    Returns ``(inventory_json, extra_vars, dry_run)``.
    """
    from app.actions.extra_vars import sanitize_extra_vars
    from app.actions.validation import DRY_RUN_PARAM
    from app.ansible_runtime.inventory import generate_inventory
    from app.ansible_runtime.known_hosts import write_known_hosts
    from app.grafana.service import build_metrics_extra_vars

    assert ctx.ssh_key_path is not None
    # SEC-26: pin the run to the host key LabDog recorded over asyncssh.
    # Read fresh rather than from the cached scalars in the spec, because
    # preflight runs in between and is what records the key on a host
    # contacted for the first time.
    known_hosts_path = write_known_hosts(
        await _stored_host_key_entry(spec.host_id), ctx.ssh_key_path
    )
    inventory_json = generate_inventory(
        spec.host_ip,
        spec.host_port,
        ctx.ssh_key_path,
        ssh_user=spec.ssh_user,
        hostname=spec.host_hostname,
        known_hosts_path=known_hosts_path,
    )

    dry_run = spec.parameters.pop(DRY_RUN_PARAM, False)
    # Fail closed before these become extra-vars. Ansible evaluates
    # extra-vars on the controller — the LabDog host — not on the target, so
    # a template expression here is code execution here. The API rejects them
    # too; this catches rows that did not come through that path. See
    # app/actions/extra_vars.py.
    sanitize_extra_vars(spec.parameters)
    extra_vars: dict | None = dict(spec.parameters) if spec.parameters else None
    if dry_run:
        extra_vars = extra_vars or {}
        extra_vars["ansible_check_mode"] = True

    # Inject metrics integration vars: identity labels (always, so the agent
    # stamps queryable labels) + the default Grafana instance's push URLs
    # when the manifest opts in via ``metrics_backend``. Operator-supplied
    # values win over injected URLs; identity is LabDog-owned and always wins.
    _url_vars, _identity_vars = await build_metrics_extra_vars(
        spec.host_id, spec.host_hostname, spec.metrics_backend
    )
    extra_vars = {**_url_vars, **(extra_vars or {}), **_identity_vars}
    return inventory_json, extra_vars, dry_run


# ---------------------------------------------------------------------------
# Phase A — snapshot
# ---------------------------------------------------------------------------


async def _phase_a_snapshot(ctx: _RunCtx, spec: _RunSpec, env: _Envelope) -> bool:
    """Optional pre-update snapshot (destructive + VM mapping only).

    Returns False when the snapshot failed and the run must stop.
    """
    from sqlalchemy import select

    from app.db import task_session
    from app.models.action_run import ActionHostRun

    if not env.can_snapshot:
        if spec.destructive and not spec.snapshot_enabled:
            # Destructive action with snapshot_enabled=False — operator
            # opted out. Log it so the run history reflects the choice.
            _log_step(
                ctx,
                "[snapshot] skipped — snapshot_enabled=false (no rollback available on failure)",
            )
        elif spec.destructive:
            # Destructive action but no Proxmox VM mapping — snapshot-wrap is
            # skipped. Surface this in the log so users know no rollback is
            # possible for this run.
            _log_step(
                ctx,
                "[snapshot] skipped — host has no Proxmox VM mapping "
                "(no rollback available on failure)",
            )
        return True

    from app.workflows.steps.snapshot import create_snapshot  # noqa: PLC0415

    try:
        env.snapshot_name = await create_snapshot(
            env.client, env.pve_node, env.vmid, ctx.action_run_id, env.vm_type, spec.action_key
        )
        _log_step(ctx, f"[snapshot] created {env.snapshot_name} on {env.pve_node}/{env.vmid}")
        async with task_session() as db:
            hr = (
                await db.execute(select(ActionHostRun).where(ActionHostRun.id == ctx.host_run_id))
            ).scalar_one()
            hr.snapshot_name = env.snapshot_name
            await db.commit()
        return True
    except Exception as exc:
        logger.exception(
            "action_host: snapshot failed for action_run %d host %d: %s",
            ctx.action_run_id,
            spec.host_id,
            exc,
        )
        _log_step(ctx, f"[snapshot] FAILED: {exc}")
        await _finish_early(ctx, "failed", error=f"Snapshot failed: {exc}", include_output=True)
        return False


# ---------------------------------------------------------------------------
# Phase B — playbook
# ---------------------------------------------------------------------------


def _phase_b_playbook(
    ctx: _RunCtx,
    spec: _RunSpec,
    inventory_json: str,
    extra_vars: dict | None,
    timeout: int,
) -> _PlaybookResult:
    """Run ansible-runner, streaming output live.

    ansible-runner blocks until the whole playbook finishes, so without
    streaming the live view sits on the pre-run step-log and looks frozen
    for the entire upgrade. Publish each event's stdout to the SSE channel
    as it arrives (task-by-task), and run a heartbeat so a single
    long-blocking task (e.g. the apt upgrade) still shows the run is alive.
    Runs in the Celery worker; the SSE endpoint (separate process) relays
    these over Redis, so blocking here is fine.
    """
    from app.ansible_runtime.runner import run_ansible

    stream = {"task": "Gathering facts", "start": time.monotonic(), "last": time.monotonic()}

    def _publish_stream(text: str) -> None:
        _publish(ctx, {"event": "output", "host_run_id": ctx.host_run_id, "text": text})

    def _on_event(event: dict) -> bool:
        # Track the active task name for the heartbeat, then stream this
        # event's rendered stdout. Must never raise — ansible-runner calls
        # this inside its event loop.
        try:
            if event.get("event") == "playbook_on_task_start":
                task_name = (event.get("event_data") or {}).get("task")
                if task_name:
                    stream["task"] = task_name
            out = event.get("stdout")
            if out:
                stream["last"] = time.monotonic()
                _publish_stream(out + "\n")
        except Exception:
            logger.debug("action_host: stream event handler error", exc_info=True)
        return True

    hb_stop = threading.Event()

    def _heartbeat() -> None:
        # While a task is blocking with no new output, emit a keepalive
        # every ~20s so long steps (apt upgrade) don't look hung.
        while not hb_stop.wait(20):
            if time.monotonic() - stream["last"] < 20:
                continue
            elapsed = int(time.monotonic() - stream["start"])
            _publish_stream(
                f"… still running: {stream['task']} ({elapsed // 60}m{elapsed % 60:02d}s elapsed)\n"
            )

    hb_thread = threading.Thread(
        target=_heartbeat, name=f"labdog-hb-{ctx.host_run_id}", daemon=True
    )
    hb_thread.start()
    try:
        runner = run_ansible(
            playbook_path=spec.playbook_path,
            inventory_json=inventory_json,
            private_data_dir=ctx.private_data_dir,
            extra_vars=extra_vars,
            timeout=timeout,
            roles_paths=list(spec.roles_paths) if spec.roles_paths else None,
            event_handler=_on_event,
        )
    finally:
        hb_stop.set()

    output: str = runner.stdout.read() if hasattr(runner.stdout, "read") else str(runner.stdout)
    if len(output.encode()) > MAX_OUTPUT_BYTES:
        output = output[:MAX_OUTPUT_BYTES] + "\n\n(truncated — output exceeded 1 MB)"

    result = _PlaybookResult(
        output=output,
        success=runner.status == "successful",
        exit_code=runner.rc,
        status=runner.status,
    )
    _log_step(ctx, f"[playbook] exit={result.exit_code} status={result.status}")
    ctx.step_log.append("=== Ansible output ===")
    ctx.step_log.append(result.output)

    # No end-of-run block is published to SSE: the playbook output was
    # already streamed task-by-task via the event handler above, and the
    # frontend reloads the authoritative full log from the DB once the run
    # reaches a terminal state. Publishing the tail again here would just
    # duplicate it in the live view.
    return result


# ---------------------------------------------------------------------------
# Phase D — verify
# ---------------------------------------------------------------------------


async def _phase_d_verify(
    ctx: _RunCtx,
    spec: _RunSpec,
    env: _Envelope,
    *,
    inventory_json: str,
    extra_vars: dict | None,
    dry_run: bool,
) -> tuple[bool, str | None]:
    """Post-run verification.

    Only runs when a snapshot was also taken — i.e. destructive + VM mapped
    — AND verify_enabled is on. Mirrors workflow_host.py's verify step but
    without a verification_prompt, so only SSH hard checks run.

    Returns ``(passed, error)``.
    """
    if spec.verify_playbook_path is not None:
        return _verify_with_pack_playbook(
            ctx, spec, inventory_json=inventory_json, extra_vars=extra_vars
        )
    return await _verify_with_builtin_checks(ctx, spec, dry_run=dry_run)


def _verify_with_pack_playbook(
    ctx: _RunCtx,
    spec: _RunSpec,
    *,
    inventory_json: str,
    extra_vars: dict | None,
) -> tuple[bool, str | None]:
    """Pack-supplied verify: run it the same way as the main playbook.

    Any non-zero rc or ansible-runner status other than "successful"
    counts as verification failure. Output is appended to the step log so
    the UI run view shows it distinctly from the main playbook.
    """
    from app.ansible_runtime.runner import run_ansible

    try:
        verify_data_dir = ctx.private_data_dir + "-verify"
        # Register before the call: run_ansible creates the dir, so it must
        # be cleaned up even if the run raises.
        ctx.extra_data_dirs.append(verify_data_dir)
        verify_runner = run_ansible(
            playbook_path=spec.verify_playbook_path,
            inventory_json=inventory_json,
            private_data_dir=verify_data_dir,
            extra_vars=extra_vars,
            timeout=spec.verify_timeout,
            roles_paths=list(spec.roles_paths) if spec.roles_paths else None,
        )
        verify_output: str = (
            verify_runner.stdout.read()
            if hasattr(verify_runner.stdout, "read")
            else str(verify_runner.stdout)
        )
        passed = verify_runner.status == "successful"
        _log_step(
            ctx,
            f"[verify] pack playbook exit={verify_runner.rc} "
            f"status={verify_runner.status} "
            f"passed={passed}",
        )
        ctx.step_log.append("=== Verify playbook output ===")
        ctx.step_log.append(verify_output)
        _publish(
            ctx,
            {
                "event": "output",
                "host_run_id": ctx.host_run_id,
                "text": "=== Verify playbook output ===\n" + verify_output[-4000:],
            },
        )
        if not passed:
            return False, (
                f"Verify playbook failed (status={verify_runner.status}, rc={verify_runner.rc})"
            )
        return True, None
    except Exception as exc:
        logger.exception(
            "action_host: verify playbook errored for action_run %d host %d: %s",
            ctx.action_run_id,
            spec.host_id,
            exc,
        )
        _log_step(ctx, f"[verify] ERROR: {exc}")
        return False, f"Verify playbook error: {exc}"


async def _verify_with_builtin_checks(
    ctx: _RunCtx, spec: _RunSpec, *, dry_run: bool
) -> tuple[bool, str | None]:
    """Built-in verification: services + packages over SSH, plus AI verify."""
    from sqlalchemy import select

    from app.db import task_session
    from app.models.host import Host

    try:
        from app.packages.merge import get_effective_packages  # noqa: PLC0415
        from app.services.merge import get_effective_services  # noqa: PLC0415
        from app.workflows.steps.verify import run_verification  # noqa: PLC0415

        async with task_session() as db:
            host = (await db.execute(select(Host).where(Host.id == spec.host_id))).scalar_one()
            effective_services = await get_effective_services(spec.host_id, db)
            effective_packages = await get_effective_packages(spec.host_id, db)
            verify_result = await run_verification(
                host,
                ctx.ssh_key_path,
                effective_services,
                effective_packages,
                spec.ai_verify_prompt,
                db,
                ai_fail_closed=spec.ai_verify_fail_closed,
                action_run_id=ctx.action_run_id,
                dry_run=dry_run,
            )
        passed = bool(verify_result.get("passed"))
        ai_result = verify_result.get("ai_result")
        _log_step(
            ctx,
            f"[verify] passed={passed} "
            f"services_ok={verify_result.get('services_ok')} "
            f"packages_ok={verify_result.get('packages_ok')}",
        )
        if ai_result:
            # The verdict is logged separately from `passed` because they
            # can legitimately differ: an inconclusive verdict under the
            # default policy is a pass, and the run log is where an operator
            # would look to find that out.
            _log_step(
                ctx,
                f"[verify] ai verdict={ai_result.get('verdict')} "
                f"session={ai_result.get('session_id')}",
            )
            ctx.step_log.append("=== AI verification ===")
            ctx.step_log.append(str(ai_result.get("output") or ""))
        if not passed:
            return False, f"Post-run verification failed: {verify_result}"
        return True, None
    except Exception as exc:
        logger.exception(
            "action_host: verification failed for action_run %d host %d: %s",
            ctx.action_run_id,
            spec.host_id,
            exc,
        )
        _log_step(ctx, f"[verify] ERROR: {exc}")
        return False, f"Verification error: {exc}"


# ---------------------------------------------------------------------------
# Phases E and F — rollback on failure, cleanup on success
# ---------------------------------------------------------------------------


async def _phase_ef_rollback_or_cleanup(
    ctx: _RunCtx, spec: _RunSpec, env: _Envelope, *, success: bool
) -> None:
    """Roll the snapshot back on failure, delete it on success."""
    if not success and env.has_snapshot and spec.auto_rollback:
        await _rollback(ctx, spec, env)
    elif not success and env.snapshot_name is not None and not spec.auto_rollback:
        # Snapshot present but auto_rollback=False — leave it in place so the
        # operator can inspect/revert manually. Logged so the run output
        # reflects the choice rather than silently keeping it.
        _log_step(
            ctx,
            f"[rollback] skipped — auto_rollback=false "
            f"(snapshot {env.snapshot_name} retained for manual recovery)",
        )
    elif success and env.has_snapshot:
        await _delete_snapshot(ctx, spec, env, note="deleted")


async def _rollback(ctx: _RunCtx, spec: _RunSpec, env: _Envelope) -> None:
    from sqlalchemy import select

    from app.db import task_session
    from app.models.host import Host
    from app.workflows.steps.rollback import rollback_to_snapshot  # noqa: PLC0415

    _log_step(ctx, f"[rollback] restoring {env.snapshot_name}")
    try:
        async with task_session() as db:
            host = (await db.execute(select(Host).where(Host.id == spec.host_id))).scalar_one()
            rb = await rollback_to_snapshot(
                env.client,
                env.pve_node,
                env.vmid,
                env.snapshot_name,
                host,
                ctx.ssh_key_path,
                db,
                vm_type=env.vm_type,
            )
            await db.commit()
        _log_step(ctx, f"[rollback] success={rb.get('success')} {rb.get('error', '')}".strip())
        if rb.get("success"):
            await _delete_snapshot(ctx, spec, env, note="deleted after rollback")
    except Exception as exc:
        logger.exception(
            "action_host: rollback failed for action_run %d host %d: %s",
            ctx.action_run_id,
            spec.host_id,
            exc,
        )
        _log_step(ctx, f"[rollback] ERROR: {exc}")


async def _delete_snapshot(ctx: _RunCtx, spec: _RunSpec, env: _Envelope, *, note: str) -> None:
    """Delete the run's snapshot. Never fatal.

    The action already reached its outcome; an orphan snapshot is cosmetic
    and will be reaped by the periodic cleanup task.
    """
    from app.workflows.steps.cleanup import delete_snapshot  # noqa: PLC0415

    try:
        await delete_snapshot(
            env.client, env.pve_node, env.vmid, env.snapshot_name, vm_type=env.vm_type
        )
        _log_step(ctx, f"[cleanup] snapshot {env.snapshot_name} {note}")
    except Exception as exc:
        logger.warning(
            "action_host: snapshot cleanup failed for action_run %d host %d: %s",
            ctx.action_run_id,
            spec.host_id,
            exc,
        )
        _log_step(ctx, f"[cleanup] WARN: {exc}")


# ---------------------------------------------------------------------------
# Phase G — persist the result and fire the post-run hooks
# ---------------------------------------------------------------------------


async def _phase_g_persist(
    ctx: _RunCtx,
    spec: _RunSpec,
    *,
    success: bool,
    exit_code: int,
    error_msg: str | None,
    dry_run: bool,
) -> str:
    """Write the terminal row, then fire the manifest's post-run hooks."""
    from sqlalchemy import select

    from app.db import task_session
    from app.models.action_run import ActionHostRun

    final_output = "\n".join(ctx.step_log)
    async with task_session() as db:
        hr = (
            await db.execute(select(ActionHostRun).where(ActionHostRun.id == ctx.host_run_id))
        ).scalar_one()
        hr.status = "succeeded" if success else "failed"
        hr.exit_code = exit_code
        hr.finished_at = datetime.now(UTC)
        hr.output = final_output
        if not success and error_msg is not None:
            hr.error_message = error_msg
        final_status = hr.status
        await db.commit()

        # Post-run module sync: only on success, never on dry-run, and only
        # when the manifest opted in. Dispatch failures are logged but do not
        # affect the action's status -- the action itself already completed.
        if success and not dry_run and spec.post_run_sync:
            try:
                from app.sync.post_run import dispatch_post_run_sync

                dispatched_ids = await dispatch_post_run_sync(
                    db,
                    host_id=spec.host_id,
                    modules=spec.post_run_sync,
                    triggered_by_user_id=spec.triggered_by_user_id,
                )
                if dispatched_ids:
                    await db.commit()
                    logger.info(
                        "action_host: dispatched post_run_sync for "
                        "action_run %d host %d -- modules=%s job_ids=%s",
                        ctx.action_run_id,
                        spec.host_id,
                        list(spec.post_run_sync),
                        dispatched_ids,
                    )
            except Exception:
                logger.exception(
                    "action_host: post_run_sync dispatch failed for action_run %d host %d",
                    ctx.action_run_id,
                    spec.host_id,
                )

        # Post-run resource registration: insert host-scope overrides for
        # resources declared in the manifest, then dispatch a follow-up sync
        # so the cache catches up. Same success-only / non-dry-run gates as
        # post_run_sync. Failures logged only; never affect the action's
        # terminal status.
        if success and not dry_run and spec.post_run_register:
            try:
                from app.sync.post_run import dispatch_post_run_register

                inserted = await dispatch_post_run_register(
                    db,
                    host_id=spec.host_id,
                    declarations=spec.post_run_register,
                    triggered_by_user_id=spec.triggered_by_user_id,
                )
                if inserted:
                    await db.commit()
                    logger.info(
                        "action_host: dispatched post_run_register for "
                        "action_run %d host %d -- inserted=%s",
                        ctx.action_run_id,
                        spec.host_id,
                        inserted,
                    )
            except Exception:
                logger.exception(
                    "action_host: post_run_register dispatch failed for action_run %d host %d",
                    ctx.action_run_id,
                    spec.host_id,
                )

    _publish_status(ctx, final_status)
    logger.info(
        "action_host: action_run %d / host_run %d completed — %s",
        ctx.action_run_id,
        ctx.host_run_id,
        final_status,
    )
    return final_status


# ---------------------------------------------------------------------------
# Failure paths and teardown
# ---------------------------------------------------------------------------


async def _on_soft_timeout(ctx: _RunCtx) -> None:
    """Celery's graceful abort.

    The soft limit sits above the ansible timeout + verify + grace, so by
    now the playbook's own timeout has long fired — this catches a hung
    envelope step (Proxmox API, verify SSH, rollback). Finalise the row
    here so the host queue and the run's schedule aren't wedged by an
    eternal ``running`` row; the caller's finally block still dispatches
    the next pending op and cleans up tmpfs.
    """
    from sqlalchemy import select

    from app.db import task_session
    from app.models.action_run import ActionHostRun

    logger.error(
        "action_host: action_run %d / host_run %d exceeded its soft time limit",
        ctx.action_run_id,
        ctx.host_run_id,
    )
    try:
        async with task_session() as db:
            hr = (
                await db.execute(select(ActionHostRun).where(ActionHostRun.id == ctx.host_run_id))
            ).scalar_one_or_none()
            if hr is not None and hr.status not in (
                "succeeded",
                "failed",
                "skipped",
                "cancelled",
            ):
                hr.status = "failed"
                hr.error_message = (
                    "aborted: exceeded task soft time limit — playbook or "
                    "snapshot/verify/rollback step hung"
                )
                hr.finished_at = datetime.now(UTC)
                await db.commit()
    except Exception:
        logger.exception(
            "action_host: could not persist soft-limit failure for host_run %d",
            ctx.host_run_id,
        )
    _publish_status(ctx, "failed")


async def _on_failure(ctx: _RunCtx, exc: Exception) -> None:
    """Generic failure: record it on the row and announce it."""
    from sqlalchemy import select

    from app.db import task_session
    from app.models.action_run import ActionHostRun

    logger.exception(
        "action_host: action_run %d / host_run %d failed",
        ctx.action_run_id,
        ctx.host_run_id,
    )
    try:
        async with task_session() as db:
            hr = (
                await db.execute(select(ActionHostRun).where(ActionHostRun.id == ctx.host_run_id))
            ).scalar_one_or_none()
            if hr is not None:
                hr.status = "failed"
                hr.error_message = str(exc)
                hr.finished_at = datetime.now(UTC)
                await db.commit()
    except Exception:
        logger.exception(
            "action_host: could not persist failure for host_run %d",
            ctx.host_run_id,
        )
    _publish_status(ctx, "failed")


async def _release_host(ctx: _RunCtx) -> None:
    """Hand the host to the next pending op and close the parent run.

    Runs even on the orchestrator-raised path so the per-host queue never
    stalls. Failures here are swallowed — they must not mask the real
    outcome of the task.
    """
    from app.db import task_session
    from app.tasks.host_lock import dispatch_next_pending_for_host

    if not (ctx.claimed and ctx.claimed_host_id is not None):
        return

    try:
        async with task_session() as db:
            await dispatch_next_pending_for_host(
                db, ctx.claimed_host_id, exclude_action_run_id=ctx.action_run_id
            )
    except Exception:
        logger.exception(
            "action_host: dispatch-next-pending failed for host_id=%s "
            "after action_run_id=%s; queue may be stuck until next op triggers it",
            ctx.claimed_host_id,
            ctx.action_run_id,
        )

    # Close the parent run if this was the last member outstanding.
    #
    # BUG-62: a member deferred behind a busy host is re-dispatched long
    # after the orchestrator's batch join returned, so nothing else would
    # ever aggregate. No-op unless every sibling is terminal, and idempotent
    # if two finish at once.
    try:
        from app.tasks.action_orchestrator import finalise_run_if_complete  # noqa: PLC0415

        await finalise_run_if_complete(ctx.action_run_id, ctx.r)
    except Exception:
        logger.exception(
            "action_host: could not finalise action_run %s after host_run %s",
            ctx.action_run_id,
            ctx.host_run_id,
        )


def _remove_workspaces(ctx: _RunCtx) -> None:
    """Remove the tmpfs key and every ansible-runner data dir."""
    from app.ansible_runtime.known_hosts import remove_known_hosts

    # CRITICAL: always remove the SSH key from tmpfs. ssh_key_path is None
    # when mkstemp itself failed, which is why it is checked first.
    if ctx.ssh_key_path is not None and os.path.exists(ctx.ssh_key_path):
        os.unlink(ctx.ssh_key_path)
    # The pinned known-hosts file sits beside the key (SEC-26).
    remove_known_hosts(ctx.ssh_key_path)
    if os.path.exists(ctx.private_data_dir):
        shutil.rmtree(ctx.private_data_dir, ignore_errors=True)
    # Sibling runner dirs (the verify pass allocates its own alongside
    # private_data_dir rather than inside it). Removing only the base dir
    # leaked one tree per verify, each holding the rendered inventory and
    # the full ansible event stream.
    for extra_dir in ctx.extra_data_dirs:
        if os.path.exists(extra_dir):
            shutil.rmtree(extra_dir, ignore_errors=True)


# ---------------------------------------------------------------------------
# Driver
# ---------------------------------------------------------------------------


async def _run_action_host_async(action_run_id: int, host_run_id: int) -> None:
    """Drive a single ActionHostRun through the phases above.

    Prologue — cancel check, claim-or-defer, load, Proxmox mapping, key,
    preflight, inventory — then the envelope proper: Phase A snapshot,
    Phase B playbook, Phase D verify, Phases E/F rollback-or-cleanup,
    Phase G persist. The phase letters match ``action_group.py``, which
    runs the same lifecycle across a whole group in one playbook.
    """
    import redis as redis_lib
    from celery.exceptions import SoftTimeLimitExceeded

    from app.config import settings
    from app.tasks.action_timeouts import effective_playbook_timeout

    ctx = _RunCtx(
        action_run_id=action_run_id,
        host_run_id=host_run_id,
        channel=f"actions.run.{action_run_id}",
        r=redis_lib.from_url(settings.redis.url),
        private_data_dir=tempfile.mkdtemp(prefix="labdog-action-"),
    )

    try:
        # Prefer tmpfs so the private key never touches disk, but fall back
        # to the default temp dir when /dev/shm is absent (hardened container
        # profiles, non-Linux dev machines) — the same guard
        # host_sync_orchestrator._make_tmpfs_workspace already applies.
        #
        # This must also stay *inside* the try. It used to be an unguarded
        # mkstemp(dir="/dev/shm") above it, so on such a host it raised
        # FileNotFoundError before the try was entered: private_data_dir
        # leaked, the ActionHostRun stayed "queued" forever, and dispatch-next
        # never fired — wedging that host's queue.
        key_dir = "/dev/shm" if Path("/dev/shm").is_dir() else None
        fd, ctx.ssh_key_path = tempfile.mkstemp(dir=key_dir, prefix="labdog-action-", suffix=".key")
        os.close(fd)

        if await _cancelled_before_start(ctx):
            return
        if not await _claim_or_defer(ctx):
            return

        spec = await _load_run_spec(ctx)
        if spec is None:
            return

        env = await _load_envelope(ctx, spec)
        _write_ssh_key(ctx, spec.private_key_text)

        if not await _preflight_ok(ctx, spec):
            return

        inventory_json, extra_vars, dry_run = await _build_inventory(ctx, spec)

        # A per-action floor (manifest ``playbook_timeout_seconds``) lets a
        # long-running action guarantee itself enough budget without forcing
        # operators to raise the global limit for every playbook. The global
        # setting can only widen it further, never shrink it below the floor.
        timeout = effective_playbook_timeout(spec.playbook_timeout)

        _publish_status(ctx, "running", host_id=spec.host_id)

        if not await _phase_a_snapshot(ctx, spec, env):
            return

        playbook = _phase_b_playbook(ctx, spec, inventory_json, extra_vars, timeout)

        verification_passed = True
        verification_error: str | None = None
        if playbook.success and env.snapshot_name is not None and spec.verify_enabled:
            verification_passed, verification_error = await _phase_d_verify(
                ctx,
                spec,
                env,
                inventory_json=inventory_json,
                extra_vars=extra_vars,
                dry_run=dry_run,
            )

        success = playbook.success and verification_passed
        error_msg: str | None = None
        if not playbook.success:
            error_msg = (
                f"ansible-runner exited with status={playbook.status}, rc={playbook.exit_code}"
            )
        elif not verification_passed:
            error_msg = verification_error

        await _phase_ef_rollback_or_cleanup(ctx, spec, env, success=success)
        await _phase_g_persist(
            ctx,
            spec,
            success=success,
            exit_code=playbook.exit_code,
            error_msg=error_msg,
            dry_run=dry_run,
        )

    except SoftTimeLimitExceeded:
        await _on_soft_timeout(ctx)
        raise

    except Exception as exc:
        await _on_failure(ctx, exc)
        raise

    finally:
        await _release_host(ctx)
        _remove_workspaces(ctx)
