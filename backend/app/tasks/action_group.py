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

Snapshot / verify / rollback envelopes from the per-host path are
deliberately NOT applied here: cluster-style playbooks tend to handle
their own safety (cordoning, verify steps, rollbacks) and a
LabDog-driven snapshot per host doesn't compose well with playbooks
that drive multi-node coordination. Destructive group actions still
log a notice that no LabDog-managed rollback is available.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
import tempfile
from datetime import UTC, datetime

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
# Async implementation
# ---------------------------------------------------------------------------


async def _run_action_group_async(action_run_id: int) -> None:  # noqa: C901, PLR0912, PLR0915
    """Drive a single ansible-runner invocation across all group members."""
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

    r = redis_lib.from_url(settings.redis.url)
    channel = f"actions.run.{action_run_id}"

    private_data_dir = tempfile.mkdtemp(prefix="labdog-action-group-")
    # Per-host SSH key files written to tmpfs; tracked for cleanup.
    ssh_key_paths: list[str] = []

    try:
        # ------------------------------------------------------------------ #
        # Pre-flight cancel check                                             #
        # ------------------------------------------------------------------ #
        if r.exists(f"actions.cancel.{action_run_id}"):
            await _mark_run_cancelled(action_run_id, channel, r)
            return

        # ------------------------------------------------------------------ #
        # Phase 1: load run + action + group members + ssh keys, mark running #
        # ------------------------------------------------------------------ #
        host_inventory_entries: list[dict] = []
        host_run_by_inventory_name: dict[str, int] = {}
        host_run_ids: list[int] = []
        master_key = get_master_key()

        async with task_session() as db:
            run_result = await db.execute(
                select(ActionRun).where(ActionRun.id == action_run_id)
            )
            run: ActionRun = run_result.scalar_one()

            action = ACTION_REGISTRY.get(run.action_key)
            if action is None:
                run.status = "failed"
                run.error_message = f"Unknown action key: {run.action_key}"
                run.finished_at = datetime.now(UTC)
                await db.commit()
                return

            if run.group_id is None:
                run.status = "failed"
                run.error_message = (
                    "action_group: ActionRun has no group_id (caller bug)"
                )
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

            # Cache action attributes before the session closes.
            playbook_path = action.playbook_path
            action_roles_paths: tuple = action.roles_paths
            action_destructive: bool = action.destructive
            parameters: dict = dict(run.parameters or {})

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
                host_run_ids.append(hr.id)

                if host.ssh_key_id is None:
                    hr.status = "skipped"
                    hr.error_message = "Host has no SSH key configured"
                    hr.finished_at = datetime.now(UTC)
                    continue

                key_result = await db.execute(
                    select(SSHKey).where(SSHKey.id == host.ssh_key_id)
                )
                ssh_key = key_result.scalar_one()
                private_key_text = decrypt_ssh_key(
                    ssh_key.encrypted_private_key, master_key
                )

                fd, key_path = tempfile.mkstemp(
                    dir="/dev/shm",
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
                inv_name = host.hostname
                host_inventory_entries.append(
                    {
                        "name": inv_name,
                        "ip": host.ip_address,
                        "port": host.ssh_port or 22,
                        "ssh_user": ssh_key.ssh_user or "root",
                        "ssh_key_path": key_path,
                    }
                )
                host_run_by_inventory_name[inv_name] = hr.id

                # Mark this host's row as running now (matches per-host path's
                # behaviour where the row flips to running before ansible runs).
                hr.status = "running"
                hr.started_at = datetime.now(UTC)

            await db.commit()

        # If every host was skipped (no SSH keys), aggregate and exit early.
        if not host_inventory_entries:
            await _aggregate_and_finalise(action_run_id, channel, r)
            return

        # Notify SSE listeners that the run is moving — emit one host_status
        # per running host so the UI flips its rows.
        for inv_name, hr_id in host_run_by_inventory_name.items():
            try:
                r.publish(
                    channel,
                    json.dumps(
                        {
                            "event": "host_status",
                            "host_run_id": hr_id,
                            "status": "running",
                        }
                    ),
                )
            except Exception:
                logger.debug(
                    "action_group: SSE publish failed for host_status running",
                    exc_info=True,
                )

        if action_destructive:
            # Group-mode actions don't get LabDog-managed snapshots — log it
            # so operators know the playbook is on its own for rollback.
            try:
                r.publish(
                    channel,
                    json.dumps(
                        {
                            "event": "output",
                            "host_run_id": None,
                            "text": (
                                "[group] destructive action — no LabDog-managed "
                                "snapshot taken; the pack playbook owns rollback\n"
                            ),
                        }
                    ),
                )
            except Exception:
                logger.debug("action_group: SSE publish failed for note", exc_info=True)

        # ------------------------------------------------------------------ #
        # Phase 2: build inventory + extra-vars, run ansible-runner once     #
        # ------------------------------------------------------------------ #
        inventory_json = generate_multi_host_inventory(host_inventory_entries)

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
                playbook_output[:MAX_OUTPUT_BYTES]
                + "\n\n(truncated — output exceeded 1 MB)"
            )

        playbook_status = getattr(runner, "status", "unknown")
        playbook_rc = getattr(runner, "rc", -1)

        # ------------------------------------------------------------------ #
        # Phase 3: route per-host events from runner.events back to rows     #
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
                msg = (
                    (event_data.get("res") or {}).get("msg")
                    or "task failed"
                )
                per_host_failure.setdefault(host_name, str(msg))
            elif event_type == "runner_on_unreachable":
                per_host_unreachable.add(host_name)
                per_host_failure.setdefault(host_name, "host unreachable")

        # Persist per-host outcomes.
        async with task_session() as db:
            for inv_name, hr_id in host_run_by_inventory_name.items():
                hr_result = await db.execute(
                    select(ActionHostRun).where(ActionHostRun.id == hr_id)
                )
                hr = hr_result.scalar_one()
                if inv_name in per_host_failure:
                    hr.status = "failed"
                    hr.error_message = per_host_failure[inv_name]
                else:
                    # No failure event for this host — it succeeded if the
                    # whole run was successful. If the playbook failed
                    # globally (e.g. parse error before any tasks ran),
                    # treat hosts with no events as failed.
                    if playbook_status == "successful":
                        hr.status = "succeeded"
                    elif inv_name in per_host_seen:
                        # Saw events but no failure → succeeded.
                        hr.status = "succeeded"
                    else:
                        hr.status = "failed"
                        hr.error_message = (
                            f"playbook failed before reaching host "
                            f"(status={playbook_status}, rc={playbook_rc})"
                        )
                hr.exit_code = playbook_rc
                hr.finished_at = datetime.now(UTC)
                # Stamp the (truncated) full playbook output on each host
                # row — the underlying ansible-runner ran once for the
                # whole group, so per-host slicing of stdout isn't
                # meaningful. Operators can still see the full log on
                # any host's output endpoint.
                hr.output = playbook_output

                # SSE host_status update.
                try:
                    r.publish(
                        channel,
                        json.dumps(
                            {
                                "event": "host_status",
                                "host_run_id": hr_id,
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
        # Phase 4: aggregate run-level status                                 #
        # ------------------------------------------------------------------ #
        await _aggregate_and_finalise(action_run_id, channel, r)

    except Exception as exc:
        logger.exception(
            "action_group: unhandled error for action_run %d", action_run_id
        )
        await _mark_run_failed(action_run_id, str(exc), channel, r)
        return

    finally:
        # CRITICAL: always remove SSH keys from tmpfs.
        for key_path in ssh_key_paths:
            try:
                if os.path.exists(key_path):
                    os.unlink(key_path)
            except Exception:
                logger.debug(
                    "action_group: failed to remove key %s", key_path, exc_info=True
                )
        if os.path.exists(private_data_dir):
            shutil.rmtree(private_data_dir, ignore_errors=True)


# ---------------------------------------------------------------------------
# Helper utilities
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
        failed = sum(
            1 for hr in host_runs if hr.status in ("failed", "skipped")
        )
        total = len(host_runs)

        if failed == 0:
            final_status = "succeeded"
        elif succeeded == 0:
            final_status = "failed"
        else:
            final_status = "partial"

        run_result = await db.execute(
            select(ActionRun).where(ActionRun.id == action_run_id)
        )
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
            run_result = await db.execute(
                select(ActionRun).where(ActionRun.id == action_run_id)
            )
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
        logger.exception(
            "action_group: could not mark action_run %d as failed", action_run_id
        )

    try:
        r.publish(channel, json.dumps({"event": "status", "status": "failed"}))
    except Exception:
        pass


async def _mark_run_cancelled(action_run_id: int, channel: str, r) -> None:
    from sqlalchemy import select

    from app.db import task_session
    from app.models.action_run import ActionHostRun, ActionRun

    try:
        async with task_session() as db:
            run_result = await db.execute(
                select(ActionRun).where(ActionRun.id == action_run_id)
            )
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
        logger.exception(
            "action_group: could not mark action_run %d as cancelled", action_run_id
        )
    try:
        r.publish(channel, json.dumps({"event": "status", "status": "cancelled"}))
    except Exception:
        pass
