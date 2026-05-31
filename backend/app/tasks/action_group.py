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
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
import tempfile
from datetime import UTC, datetime
from typing import Any

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
    asyncio.run(_run_action_group_async(action_run_id))
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


async def _run_action_group_async(action_run_id: int) -> None:  # noqa: C901, PLR0912, PLR0915
    """Drive a single ansible-runner invocation across all group members,
    wrapped in a per-host snapshot/verify/rollback envelope for destructive
    actions when the host has a Proxmox VM mapping.
    """
    import redis as redis_lib
    from sqlalchemy import select

    from app.actions.registry import ACTION_REGISTRY
    from app.ansible_runtime.runner import generate_multi_host_inventory, run_ansible
    from app.config import settings
    from app.crypto import decrypt_ssh_key, get_master_key
    from app.db import task_session
    from app.models.action_run import ActionHostRun, ActionRun
    from app.models.host import Host, HostGroupMembership
    from app.models.ssh_key import SSHKey
    from app.settings_service import get_setting_sync_typed
    from app.tasks.host_lock import (
        acquire_host_locks,
        check_hosts_busy,
        dispatch_next_pending_for_host,
        format_pending_reason,
    )

    r = redis_lib.from_url(settings.redis.url)
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
        if r.exists(f"actions.cancel.{action_run_id}"):
            await _mark_run_cancelled(action_run_id, channel, r)
            return

        # ------------------------------------------------------------------ #
        # Claim-or-defer all members atomically. If any member is busy with #
        # another op (sync, host action, or another group action that      #
        # includes it), mark the ActionRun + every pre-created             #
        # ActionHostRun as ``pending`` and return. The in-flight op's      #
        # finally hook will re-fire us once it frees up.                   #
        # ------------------------------------------------------------------ #
        async with task_session() as db:
            run_peek = (
                await db.execute(select(ActionRun).where(ActionRun.id == action_run_id))
            ).scalar_one_or_none()
            if run_peek is None:
                logger.warning("action_group: action_run %d not found at claim", action_run_id)
                return
            if run_peek.group_id is None:
                # The main loader below will fail this run cleanly with
                # the existing "no group_id" branch — let it run.
                pass
            else:
                hosts_peek = await db.execute(
                    select(Host.id)
                    .join(HostGroupMembership, Host.id == HostGroupMembership.c.host_id)
                    .where(HostGroupMembership.c.group_id == run_peek.group_id)
                    .order_by(Host.id)
                )
                member_ids_peek = [hid for hid in hosts_peek.scalars().all()]
                if member_ids_peek:
                    await acquire_host_locks(db, member_ids_peek)
                    blocker = await check_hosts_busy(db, member_ids_peek)
                    if blocker is not None:
                        reason = await format_pending_reason(db, blocker)
                        # Defer: mark parent + any pre-created per-host rows
                        # as ``pending`` and stamp them with the blocker
                        # diagnostic. The pre-created rows only exist if
                        # an earlier dispatch flipped them; first-time runs
                        # will have none yet (the main loader creates them).
                        # Every per-host row gets the same string — the
                        # defer is run-level (one busy member blocks the
                        # whole group), not host-level.
                        run_peek.status = "pending"
                        run_peek.pending_reason = reason
                        existing_hrs = (
                            (
                                await db.execute(
                                    select(ActionHostRun).where(
                                        ActionHostRun.action_run_id == action_run_id
                                    )
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
                        return
                    # All free → record what we claimed for the finally release.
                    claimed_member_ids = list(member_ids_peek)
                    await db.commit()

        # ------------------------------------------------------------------ #
        # Phase 1: load run + action + group members + ssh keys, mark running #
        # ------------------------------------------------------------------ #
        host_ctxs: list[_HostCtx] = []
        host_run_by_inventory_name: dict[str, _HostCtx] = {}
        master_key = get_master_key()

        async with task_session() as db:
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
                run.status = "failed"
                run.error_message = f"Unknown action key: {run.action_key}"
                run.finished_at = datetime.now(UTC)
                await db.commit()
                return

            if run.group_id is None:
                run.status = "failed"
                run.error_message = "action_group: ActionRun has no group_id (caller bug)"
                run.finished_at = datetime.now(UTC)
                await db.commit()
                return

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
                return

            run.status = "running"
            run.started_at = datetime.now(UTC)
            await db.flush()

            # Cache action + run attributes before the session closes.
            action_key: str = run.action_key
            playbook_path = action.playbook_path
            action_roles_paths: tuple = action.roles_paths
            action_destructive: bool = action.destructive
            action_verify_playbook_path = action.verify_playbook_path
            action_verify_timeout: int = action.verify_timeout_seconds
            parameters: dict = dict(run.parameters or {})
            run_snapshot_enabled: bool = bool(run.snapshot_enabled)
            run_verify_enabled: bool = bool(run.verify_enabled)
            run_auto_rollback: bool = bool(run.auto_rollback)

            # Per-host: create ActionHostRun, decrypt SSH key, write to tmpfs,
            # add to inventory list. Hosts without an SSH key get a skipped row.
            for host in hosts:
                hr = ActionHostRun(
                    action_run_id=action_run_id,
                    host_id=host.id,
                    status="queued",
                )
                db.add(hr)
                await db.flush()

                if host.ssh_key_id is None:
                    hr.status = "skipped"
                    hr.error_message = "Host has no SSH key configured"
                    hr.finished_at = datetime.now(UTC)
                    continue

                key_result = await db.execute(select(SSHKey).where(SSHKey.id == host.ssh_key_id))
                ssh_key = key_result.scalar_one()
                private_key_text = decrypt_ssh_key(ssh_key.encrypted_private_key, master_key)

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
                ctx.hostname = host.hostname
                host_ctxs.append(ctx)
                host_run_by_inventory_name[ctx.inv_name] = ctx

                # Mark this host's row as running now (matches per-host path's
                # behaviour where the row flips to running before ansible runs).
                hr.status = "running"
                hr.started_at = datetime.now(UTC)

            await db.commit()

        # If every host was skipped (no SSH keys), aggregate and exit early.
        if not host_ctxs:
            await _aggregate_and_finalise(action_run_id, channel, r)
            return

        # Notify SSE listeners that the run is moving — emit one host_status
        # per running host so the UI flips its rows.
        for ctx in host_ctxs:
            try:
                r.publish(
                    channel,
                    json.dumps(
                        {
                            "event": "host_status",
                            "host_run_id": ctx.host_run_id,
                            "status": "running",
                        }
                    ),
                )
            except Exception:
                logger.debug(
                    "action_group: SSE publish failed for host_status running",
                    exc_info=True,
                )

        # ------------------------------------------------------------------ #
        # Phase A: per-host pre-action snapshot                               #
        # ------------------------------------------------------------------ #
        # Destructive + snapshot_enabled + has VM mapping → take a snapshot.
        # Snapshots are taken in parallel via asyncio.gather; a snapshot
        # failure on any single host fails that host immediately (its row
        # is marked ``failed`` and it is dropped from the inventory before
        # the playbook runs — matching the per-host path's bail-on-snapshot
        # behaviour).
        if action_destructive and run_snapshot_enabled:
            await _snapshot_all(
                host_ctxs,
                action_run_id,
                action_key,
                master_key,
                channel,
                r,
            )

            # Drop hosts whose snapshot failed: we already marked them
            # ``failed`` in the DB and emitted a status event. They're not
            # part of the inventory the playbook sees.
            snapshot_failed = [c for c in host_ctxs if _was_snapshot_failed(c)]
            host_ctxs = [c for c in host_ctxs if not _was_snapshot_failed(c)]
            for c in snapshot_failed:
                host_run_by_inventory_name.pop(c.inv_name, None)

            if not host_ctxs:
                # Every host failed snapshot — nothing left to run.
                await _aggregate_and_finalise(action_run_id, channel, r)
                return
        elif action_destructive:
            # Destructive but snapshot_enabled=False — log the choice once.
            _publish_global(
                r,
                channel,
                "[group] destructive action with snapshot_enabled=false "
                "— skipping snapshot envelope, playbook owns rollback\n",
            )

        # ------------------------------------------------------------------ #
        # Phase B: build inventory + extra-vars, run ansible-runner once     #
        # ------------------------------------------------------------------ #
        inventory_json = generate_multi_host_inventory(
            [
                {
                    "name": ctx.inv_name,
                    "ip": ctx.ip,
                    "port": ctx.port,
                    "ssh_user": ctx.ssh_user,
                    "ssh_key_path": ctx.ssh_key_path,
                }
                for ctx in host_ctxs
            ]
        )

        dry_run = parameters.pop("__dry_run", False)
        extra_vars: dict | None = dict(parameters) if parameters else None
        if dry_run:
            extra_vars = extra_vars or {}
            extra_vars["ansible_check_mode"] = True

        try:
            timeout = int(get_setting_sync_typed("ansible.playbook_timeout"))
        except Exception:
            timeout = 1800

        runner = run_ansible(
            playbook_path=playbook_path,
            inventory_json=inventory_json,
            private_data_dir=private_data_dir,
            extra_vars=extra_vars,
            timeout=timeout,
            roles_paths=list(action_roles_paths) if action_roles_paths else None,
        )

        playbook_output: str = (
            runner.stdout.read() if hasattr(runner.stdout, "read") else str(runner.stdout)
        )
        if len(playbook_output.encode()) > MAX_OUTPUT_BYTES:
            playbook_output = (
                playbook_output[:MAX_OUTPUT_BYTES] + "\n\n(truncated — output exceeded 1 MB)"
            )

        playbook_status = getattr(runner, "status", "unknown")
        playbook_rc = getattr(runner, "rc", -1)

        # ------------------------------------------------------------------ #
        # Phase C: route per-host events from runner.events back to rows     #
        # ------------------------------------------------------------------ #
        per_host_failure: dict[str, str] = {}
        per_host_unreachable: set[str] = set()
        per_host_seen: set[str] = set()

        for event in getattr(runner, "events", None) or []:
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

        # Derive per-host playbook outcome (succeeded / failed). Saved
        # to ActionHostRun below; the verify+rollback phases read from
        # `per_host_playbook_success` rather than re-querying the DB.
        per_host_playbook_success: dict[int, bool] = {}
        per_host_error_msg: dict[int, str] = {}
        for ctx in host_ctxs:
            if ctx.inv_name in per_host_failure:
                per_host_playbook_success[ctx.host_run_id] = False
                per_host_error_msg[ctx.host_run_id] = per_host_failure[ctx.inv_name]
            elif playbook_status == "successful" or ctx.inv_name in per_host_seen:
                # Either the run completed cleanly, or we saw at least one
                # event for this host with no failure event — treat as success.
                per_host_playbook_success[ctx.host_run_id] = True
            else:
                # Playbook failed globally before reaching this host
                # (e.g. parse error, ``serial: 1`` halt after an earlier
                # failure). Mark as failed; downstream verify is skipped
                # and rollback runs if a snapshot exists.
                per_host_playbook_success[ctx.host_run_id] = False
                per_host_error_msg[ctx.host_run_id] = (
                    f"playbook did not reach host (status={playbook_status}, rc={playbook_rc})"
                )

        # ------------------------------------------------------------------ #
        # Phase D: per-host verify (succeeded + snapshotted + verify_enabled) #
        # ------------------------------------------------------------------ #
        if run_verify_enabled and action_destructive:
            await _verify_all(
                host_ctxs,
                per_host_playbook_success,
                action_verify_playbook_path,
                action_verify_timeout,
                action_roles_paths,
                extra_vars,
                private_data_dir,
                channel,
                r,
            )

        # ------------------------------------------------------------------ #
        # Phase E: per-host rollback (failed playbook OR failed verify)      #
        # ------------------------------------------------------------------ #
        if run_auto_rollback and action_destructive:
            await _rollback_all(
                host_ctxs,
                per_host_playbook_success,
                channel,
                r,
            )

        # ------------------------------------------------------------------ #
        # Phase F: per-host cleanup (delete snapshots on succeeded hosts)    #
        # ------------------------------------------------------------------ #
        if action_destructive:
            await _cleanup_all(
                host_ctxs,
                per_host_playbook_success,
                channel,
                r,
            )

        # ------------------------------------------------------------------ #
        # Persist final per-host outcomes                                     #
        # ------------------------------------------------------------------ #
        async with task_session() as db:
            for ctx in host_ctxs:
                hr_result = await db.execute(
                    select(ActionHostRun).where(ActionHostRun.id == ctx.host_run_id)
                )
                hr = hr_result.scalar_one()
                playbook_ok = per_host_playbook_success.get(ctx.host_run_id, False)
                final_ok = playbook_ok and ctx.verify_passed
                if final_ok:
                    hr.status = "succeeded"
                else:
                    hr.status = "failed"
                    if ctx.verify_error and playbook_ok:
                        hr.error_message = ctx.verify_error
                    elif ctx.host_run_id in per_host_error_msg:
                        hr.error_message = per_host_error_msg[ctx.host_run_id]
                hr.exit_code = playbook_rc
                hr.finished_at = datetime.now(UTC)
                # Compose final per-host output: pre/post step log lines
                # plus the (truncated) shared playbook output. The shared
                # log is stamped on every host's row — the underlying
                # ansible-runner ran once for the whole group, so per-host
                # slicing of stdout isn't meaningful.
                ctx_log = "\n".join(ctx.step_log)
                if ctx_log:
                    hr.output = ctx_log + "\n=== Ansible output ===\n" + playbook_output
                else:
                    hr.output = playbook_output

                # SSE host_status update.
                try:
                    r.publish(
                        channel,
                        json.dumps(
                            {
                                "event": "host_status",
                                "host_run_id": ctx.host_run_id,
                                "status": hr.status,
                            }
                        ),
                    )
                except Exception:
                    logger.debug(
                        "action_group: SSE publish failed for host_status final",
                        exc_info=True,
                    )

            await db.commit()

        # Stream the tail of the playbook output once for the run.
        try:
            r.publish(
                channel,
                json.dumps(
                    {
                        "event": "output",
                        "host_run_id": None,
                        "text": "=== Ansible output ===\n" + playbook_output[-4000:],
                    }
                ),
            )
        except Exception:
            logger.debug("action_group: SSE publish failed for ansible output", exc_info=True)

        # ------------------------------------------------------------------ #
        # Phase G: aggregate run-level status                                 #
        # ------------------------------------------------------------------ #
        await _aggregate_and_finalise(action_run_id, channel, r)

    except Exception as exc:
        logger.exception("action_group: unhandled error for action_run %d", action_run_id)
        await _mark_run_failed(action_run_id, str(exc), channel, r)
        return

    finally:
        # Dispatch-next-pending per claimed member. Each freed host can
        # unblock a different queued op; we honour exclude_action_run_id
        # so our own row (already finalised in a prior commit) doesn't
        # re-pick itself. Failures here are swallowed so they never
        # mask the real task outcome.
        for host_id in claimed_member_ids:
            try:
                async with task_session() as db:
                    await dispatch_next_pending_for_host(
                        db, host_id, exclude_action_run_id=action_run_id
                    )
            except Exception:
                logger.exception(
                    "action_group: dispatch-next-pending failed for host_id=%s "
                    "after action_run_id=%s; queue may be stuck until next op triggers it",
                    host_id,
                    action_run_id,
                )

        # CRITICAL: always remove SSH keys from tmpfs.
        for key_path in ssh_key_paths:
            try:
                if os.path.exists(key_path):
                    os.unlink(key_path)
            except Exception:
                logger.debug("action_group: failed to remove key %s", key_path, exc_info=True)
        if os.path.exists(private_data_dir):
            shutil.rmtree(private_data_dir, ignore_errors=True)


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
    r: Any,
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

    from sqlalchemy import select  # noqa: PLC0415

    from app.crypto import decrypt_ssh_key  # noqa: PLC0415
    from app.db import task_session  # noqa: PLC0415
    from app.models.action_run import ActionHostRun  # noqa: PLC0415

    # Load every VM mapping in one query, then attach to the ctxs.
    async with task_session() as db:
        host_ids = [c.host_id for c in host_ctxs]
        vm_map_result = await db.execute(select(VMMapping).where(VMMapping.host_id.in_(host_ids)))
        mappings = {m.host_id: m for m in vm_map_result.scalars().all()}

        # Cache proxmox node rows referenced by any mapping.
        node_ids = {m.proxmox_node_id for m in mappings.values()}
        proxmox_nodes: dict[int, Any] = {}
        if node_ids:
            node_result = await db.execute(select(ProxmoxNode).where(ProxmoxNode.id.in_(node_ids)))
            for n in node_result.scalars().all():
                proxmox_nodes[n.id] = n

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
    async def _snap(ctx: _HostCtx, mapping: Any, proxmox_node: Any) -> None:
        from app.workflows.steps.snapshot import create_snapshot  # noqa: PLC0415

        token_secret = decrypt_ssh_key(proxmox_node.encrypted_token_secret, master_key)
        client = ProxmoxClient(
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
                client, ctx.pve_node, ctx.vmid, action_run_id, ctx.vm_type, action_key
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
        tasks.append(_snap(ctx, mapping, proxmox_node))

    if tasks:
        await asyncio.gather(*tasks, return_exceptions=False)

    # ------------------------------------------------------------------ #
    # Step 2 — persist results sequentially in one session                #
    # ------------------------------------------------------------------ #
    # Each row update is independent; we don't need cross-row consistency
    # here — just a clean serial commit so concurrent writers (in tests
    # or under asyncpg) can't trip the SQLAlchemy state machine.
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

    # Emit SSE host_status=failed for any snapshot-failed hosts (broker
    # publish is sync and fine outside the DB session).
    for ctx in host_ctxs:
        if ctx.snapshot_name == _SNAPSHOT_FAILED_MARKER:
            try:
                r.publish(
                    channel,
                    json.dumps(
                        {
                            "event": "host_status",
                            "host_run_id": ctx.host_run_id,
                            "status": "failed",
                        }
                    ),
                )
            except Exception:
                logger.debug(
                    "action_group: SSE publish failed for snapshot-failed status",
                    exc_info=True,
                )


async def _verify_all(
    host_ctxs: list[_HostCtx],
    per_host_playbook_success: dict[int, bool],
    verify_playbook_path: Any,
    verify_timeout: int,
    roles_paths: tuple,
    extra_vars: dict | None,
    private_data_dir: str,
    channel: str,
    r: Any,
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
    from app.ansible_runtime.inventory import generate_inventory  # noqa: PLC0415
    from app.ansible_runtime.runner import run_ansible  # noqa: PLC0415

    # Filter to the hosts that are actually eligible for verify, so we
    # only preload DB data for them.
    eligible = [
        ctx
        for ctx in host_ctxs
        if per_host_playbook_success.get(ctx.host_run_id, False) and ctx.snapshot_name is not None
    ]
    if not eligible:
        return

    # For the built-in path, preload Host / effective services / packages
    # for every eligible host in ONE session — we can't share an async
    # SQLAlchemy session across parallel verify tasks (state machine
    # races). The verify itself is SSH-bound and runs in parallel.
    preloaded: dict[int, tuple[Any, list, list]] = {}
    if verify_playbook_path is None:
        from sqlalchemy import select  # noqa: PLC0415

        from app.db import task_session  # noqa: PLC0415
        from app.models.host import Host  # noqa: PLC0415
        from app.packages.merge import get_effective_packages  # noqa: PLC0415
        from app.services.merge import get_effective_services  # noqa: PLC0415

        async with task_session() as db:
            for ctx in eligible:
                host_result = await db.execute(select(Host).where(Host.id == ctx.host_id))
                host = host_result.scalar_one()
                effective_services = await get_effective_services(ctx.host_id, db)
                effective_packages = await get_effective_packages(ctx.host_id, db)
                preloaded[ctx.host_id] = (host, effective_services, effective_packages)

    async def _verify_one(ctx: _HostCtx) -> None:
        if verify_playbook_path is not None:
            # Pack-supplied verify: run against this host only.
            try:
                inv_json = generate_inventory(
                    ctx.ip,
                    ctx.port,
                    ctx.ssh_key_path,
                    ssh_user=ctx.ssh_user,
                    hostname=ctx.hostname,
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
                        f"Verify playbook failed "
                        f"(status={verify_runner.status}, rc={verify_runner.rc})"
                    )
                try:
                    r.publish(
                        channel,
                        json.dumps(
                            {
                                "event": "output",
                                "host_run_id": ctx.host_run_id,
                                "text": "=== Verify playbook output ===\n" + verify_output[-4000:],
                            }
                        ),
                    )
                except Exception:
                    logger.debug(
                        "action_group: SSE publish failed for verify output",
                        exc_info=True,
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
        else:
            # Built-in SSH/services/packages check — runs with the
            # data preloaded above. We pass ``db=None`` (verify only
            # uses the session in the AI-verification path, which is
            # disabled for ad-hoc actions).
            try:
                from app.workflows.steps.verify import run_verification  # noqa: PLC0415

                host, effective_services, effective_packages = preloaded[ctx.host_id]
                verify_result = await run_verification(
                    host,
                    ctx.ssh_key_path,
                    effective_services,
                    effective_packages,
                    None,  # no AI prompt for ad-hoc actions
                    None,  # db unused on the non-AI path
                )
                ctx.verify_passed = bool(verify_result.get("passed"))
                ctx.step_log.append(
                    f"[verify] passed={ctx.verify_passed} "
                    f"services_ok={verify_result.get('services_ok')} "
                    f"packages_ok={verify_result.get('packages_ok')}"
                )
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

    await asyncio.gather(*(_verify_one(ctx) for ctx in eligible), return_exceptions=False)


async def _rollback_all(
    host_ctxs: list[_HostCtx],
    per_host_playbook_success: dict[int, bool],
    channel: str,
    r: Any,
) -> None:
    """Roll back the snapshot for every host whose action OR verify failed.

    Per-host policy (matches the per-host path):

    * Snapshot present + (playbook failed OR verify failed) → revert.
    * No snapshot → no-op (caller logged earlier that rollback isn't
      possible for this host).
    * Succeeded hosts → no-op; their snapshots are deleted in Phase F.

    Rollbacks run in parallel — each host's Proxmox client is independent
    and the underlying ``rollback_to_snapshot`` helper waits for SSH
    recovery before returning.
    """
    from sqlalchemy import select  # noqa: PLC0415

    from app.db import task_session  # noqa: PLC0415
    from app.models.host import Host  # noqa: PLC0415
    from app.workflows.steps.rollback import rollback_to_snapshot  # noqa: PLC0415

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

    # Preload Host rows in ONE session — the underlying rollback helper
    # uses ``db`` to mark the host out-of-sync. We give each parallel
    # rollback its own short-lived session below, but serialise the
    # commit so concurrent rollbacks don't trip the test session's
    # state machine. In production each session is independent.
    hosts_by_id: dict[int, Any] = {}
    async with task_session() as db:
        host_result = await db.execute(
            select(Host).where(Host.id.in_([c.host_id for c in needs_rollback]))
        )
        for h in host_result.scalars().all():
            hosts_by_id[h.id] = h

    # Per-host rollback locks are sequential by design: while the
    # Proxmox rollback call itself is the slow part, the SSH-recovery
    # poll inside ``rollback_to_snapshot`` is also async-friendly. We
    # still parallelise via gather — the DB write at the end is a
    # single ``flush`` against a per-rollback session.
    async def _rb(ctx: _HostCtx) -> None:
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
            ctx.step_log.append(
                f"[rollback] success={rb.get('success')} {rb.get('error', '')}".strip()
            )
            if rb.get("success") and ctx.snapshot_name is not None:
                from app.workflows.steps.cleanup import delete_snapshot  # noqa: PLC0415

                try:
                    await delete_snapshot(
                        ctx.proxmox_client,
                        ctx.pve_node,
                        ctx.vmid,
                        ctx.snapshot_name,
                        ctx.vm_type,
                    )
                    ctx.step_log.append(
                        f"[cleanup] snapshot {ctx.snapshot_name} deleted after rollback"
                    )
                except Exception as clean_exc:
                    logger.warning(
                        "action_group: snapshot cleanup after rollback failed for host %d: %s",
                        ctx.host_id,
                        clean_exc,
                    )
                    ctx.step_log.append(f"[cleanup] WARN: {clean_exc}")
        except Exception as exc:
            logger.exception(
                "action_group: rollback failed for host %d: %s",
                ctx.host_id,
                exc,
            )
            ctx.step_log.append(f"[rollback] ERROR: {exc}")

    # Run rollbacks sequentially — they each open a DB session and
    # commit, which doesn't play nicely with the shared test session
    # under gather. In production the Proxmox API call itself
    # dominates the wall-clock cost; rollbacks for failed hosts in a
    # group dispatch are the exception, not the rule.
    for ctx in needs_rollback:
        await _rb(ctx)


async def _cleanup_all(
    host_ctxs: list[_HostCtx],
    per_host_playbook_success: dict[int, bool],
    channel: str,
    r: Any,
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


def _publish_global(r: Any, channel: str, text: str) -> None:
    """Publish a run-scoped log line (no host_run_id binding)."""
    try:
        r.publish(
            channel,
            json.dumps({"event": "output", "host_run_id": None, "text": text}),
        )
    except Exception:
        logger.debug("action_group: SSE publish failed for run-scoped log", exc_info=True)


# ---------------------------------------------------------------------------
# Run-level helpers
# ---------------------------------------------------------------------------


async def _aggregate_and_finalise(action_run_id: int, channel: str, r) -> None:
    """Walk ActionHostRun rows, set the run's terminal status, publish."""
    from sqlalchemy import select

    from app.db import task_session
    from app.models.action_run import ActionHostRun, ActionRun

    async with task_session() as db:
        hr_result = await db.execute(
            select(ActionHostRun).where(ActionHostRun.action_run_id == action_run_id)
        )
        host_runs = list(hr_result.scalars().all())

        succeeded = sum(1 for hr in host_runs if hr.status == "succeeded")
        failed = sum(1 for hr in host_runs if hr.status in ("failed", "skipped"))
        total = len(host_runs)

        if failed == 0:
            final_status = "succeeded"
        elif succeeded == 0:
            final_status = "failed"
        else:
            final_status = "partial"

        run_result = await db.execute(select(ActionRun).where(ActionRun.id == action_run_id))
        run = run_result.scalar_one()
        if run.status != "cancelled":
            run.status = final_status
            run.finished_at = datetime.now(UTC)
        await db.commit()

        logger.info(
            "action_group: action_run %d finished — %s (%d/%d hosts succeeded)",
            action_run_id,
            run.status,
            succeeded,
            total,
        )

        # Post-run module sync fan-out: for group-dispatched actions
        # (supports_host=false) one invocation produces N per-host
        # outcomes; we fan out post_run_sync to each host whose row is
        # ``succeeded``. Skipped on dry-run, on cancel, and on whole-
        # run failure. Failures here are logged but never affect the
        # action's terminal status -- the action itself already
        # completed.
        run_parameters = run.parameters or {}
        dry_run = bool(run_parameters.get("__dry_run", False))
        if run.status not in ("cancelled", "failed") and not dry_run and succeeded > 0:
            from app.actions.registry import ACTION_REGISTRY

            action = ACTION_REGISTRY.get(run.action_key)
            post_run_sync_modules: tuple[str, ...] = (
                action.post_run_sync if action is not None else ()
            )
            post_run_register_decls: dict[str, tuple[dict, ...]] = (
                dict(action.post_run_register) if action is not None else {}
            )
            if post_run_sync_modules or post_run_register_decls:
                from app.sync.post_run import (
                    dispatch_post_run_register,
                    dispatch_post_run_sync,
                )

                for hr in host_runs:
                    if hr.status != "succeeded":
                        continue
                    if post_run_sync_modules:
                        try:
                            dispatched_ids = await dispatch_post_run_sync(
                                db,
                                host_id=hr.host_id,
                                modules=post_run_sync_modules,
                                triggered_by_user_id=run.triggered_by_user_id,
                            )
                            if dispatched_ids:
                                await db.commit()
                                logger.info(
                                    "action_group: dispatched post_run_sync for "
                                    "action_run %d host %d -- modules=%s job_ids=%s",
                                    action_run_id,
                                    hr.host_id,
                                    list(post_run_sync_modules),
                                    dispatched_ids,
                                )
                        except Exception:
                            logger.exception(
                                "action_group: post_run_sync dispatch failed for "
                                "action_run %d host %d",
                                action_run_id,
                                hr.host_id,
                            )
                    if post_run_register_decls:
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
                                "action_group: post_run_register dispatch failed for "
                                "action_run %d host %d",
                                action_run_id,
                                hr.host_id,
                            )

    try:
        r.publish(
            channel,
            json.dumps({"event": "status", "status": run.status}),
        )
    except Exception:
        logger.debug("action_group: SSE publish failed for terminal status", exc_info=True)


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
