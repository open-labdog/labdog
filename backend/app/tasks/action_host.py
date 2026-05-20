"""Per-host action executor Celery task.

Each ActionHostRun is processed independently by this task.  The
orchestrator (action_orchestrator.py) dispatches one instance per host
inside a batch, waits for the whole batch, then moves on.
"""

import asyncio
import logging
import os
import shutil
import tempfile
from datetime import UTC, datetime

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
# Async implementation
# ---------------------------------------------------------------------------


async def _run_action_host_async(action_run_id: int, host_run_id: int) -> None:  # noqa: C901, PLR0912, PLR0915
    """Drive a single ActionHostRun through ansible-runner, optionally
    wrapping the run in a Proxmox snapshot → verify → rollback envelope
    when the action is destructive and the host has a VM mapping.
    """
    import json

    import redis as redis_lib
    from sqlalchemy import select

    from app.actions.registry import ACTION_REGISTRY
    from app.ansible_runtime.inventory import generate_inventory
    from app.ansible_runtime.runner import run_ansible
    from app.config import settings
    from app.crypto import decrypt_ssh_key, get_master_key
    from app.db import task_session
    from app.models.action_run import ActionHostRun, ActionRun
    from app.models.host import Host
    from app.models.ssh_key import SSHKey
    from app.settings_service import get_setting_sync_typed
    from app.tasks.host_lock import (
        acquire_host_lock,
        check_host_busy,
        dispatch_next_pending_for_host,
        format_pending_reason,
    )

    r = redis_lib.from_url(settings.redis.url)
    channel = f"actions.run.{action_run_id}"

    private_data_dir = tempfile.mkdtemp(prefix="labdog-action-")
    fd, ssh_key_path = tempfile.mkstemp(dir="/dev/shm", prefix="labdog-action-", suffix=".key")
    os.close(fd)

    # Track whether we claimed the host so the finally block knows whether
    # to release it via dispatch-next-pending. On the defer path we leave
    # the queue ownership to the in-flight op's finally hook.
    claimed = False
    claimed_host_id: int | None = None

    try:
        # ------------------------------------------------------------------ #
        # Pre-flight: honour cancel token before doing any real work          #
        # ------------------------------------------------------------------ #
        if r.exists(f"actions.cancel.{action_run_id}"):
            async with task_session() as db:
                hr_result = await db.execute(
                    select(ActionHostRun).where(ActionHostRun.id == host_run_id)
                )
                hr = hr_result.scalar_one_or_none()
                if hr is not None and hr.status == "queued":
                    hr.status = "cancelled"
                    await db.commit()
            r.publish(
                channel,
                json.dumps(
                    {"event": "host_status", "host_run_id": host_run_id, "status": "cancelled"}
                ),
            )
            return

        # ------------------------------------------------------------------ #
        # Per-host claim-or-defer: serialize against any in-flight op on     #
        # this host (sync, host-targeted action, or group-targeted action    #
        # that includes this host as a member). On busy → mark ActionRun +  #
        # ActionHostRun as ``pending`` and return; dispatch-next-pending on  #
        # the in-flight op's finally hook will re-fire us when the host     #
        # frees up.                                                          #
        # ------------------------------------------------------------------ #
        async with task_session() as db:
            hr_row = (
                await db.execute(select(ActionHostRun).where(ActionHostRun.id == host_run_id))
            ).scalar_one_or_none()
            if hr_row is None:
                logger.warning("action_host: host_run %d missing — exiting", host_run_id)
                return
            host_id_for_lock = hr_row.host_id
            await acquire_host_lock(db, host_id_for_lock)
            blocker = await check_host_busy(db, host_id_for_lock)
            if blocker is not None:
                reason = await format_pending_reason(db, blocker)
                hr_row.status = "pending"
                hr_row.pending_reason = reason
                # Only flip the parent ActionRun to ``pending`` when this
                # is the sole per-host row (single-host target). For the
                # group-with-supports_host=True dispatch shape the parent
                # ActionRun has many ActionHostRuns and our defer is
                # per-row; the parent's status should reflect the union,
                # not just one member.
                run_row = (
                    await db.execute(select(ActionRun).where(ActionRun.id == action_run_id))
                ).scalar_one_or_none()
                if run_row is not None and run_row.host_id is not None and run_row.status in (
                    "queued",
                    "running",
                ):
                    run_row.status = "pending"
                    run_row.pending_reason = reason
                await db.commit()
                logger.info(
                    "action_host: deferred action_run=%d host_run=%d "
                    "(host %d busy: %s)",
                    action_run_id,
                    host_run_id,
                    host_id_for_lock,
                    reason,
                )
                return
            # Free → claim by leaving the row in queued/running; the
            # main loader below will flip it to running atomically with
            # the rest of the run-state writes.
            claimed = True
            claimed_host_id = host_id_for_lock
            await db.commit()

        # ------------------------------------------------------------------ #
        # Load all required data from DB in a single session                  #
        # ------------------------------------------------------------------ #
        async with task_session() as db:
            hr_result = await db.execute(
                select(ActionHostRun).where(ActionHostRun.id == host_run_id)
            )
            hr: ActionHostRun = hr_result.scalar_one()

            run_result = await db.execute(select(ActionRun).where(ActionRun.id == action_run_id))
            run: ActionRun = run_result.scalar_one()

            host_result = await db.execute(select(Host).where(Host.id == hr.host_id))
            host: Host = host_result.scalar_one()

            if host.ssh_key_id is None:
                hr.status = "skipped"
                hr.error_message = "Host has no SSH key configured"
                hr.finished_at = datetime.now(UTC)
                await db.commit()
                r.publish(
                    channel,
                    json.dumps(
                        {
                            "event": "host_status",
                            "host_run_id": host_run_id,
                            "status": "skipped",
                        }
                    ),
                )
                return

            key_result = await db.execute(select(SSHKey).where(SSHKey.id == host.ssh_key_id))
            ssh_key: SSHKey = key_result.scalar_one()

            master_key = get_master_key()
            private_key_text = decrypt_ssh_key(ssh_key.encrypted_private_key, master_key)

            action = ACTION_REGISTRY.get(run.action_key)
            if action is None:
                hr.status = "failed"
                hr.error_message = f"Action '{run.action_key}' not found in registry"
                hr.finished_at = datetime.now(UTC)
                await db.commit()
                r.publish(
                    channel,
                    json.dumps(
                        {
                            "event": "host_status",
                            "host_run_id": host_run_id,
                            "status": "failed",
                        }
                    ),
                )
                return

            # Mark running
            hr.status = "running"
            hr.started_at = datetime.now(UTC)
            await db.commit()

            # Cache scalar values before the session closes
            host_id: int = host.id
            host_ip: str = host.ip_address
            host_hostname: str = host.hostname
            host_port: int = host.ssh_port or 22
            ssh_user: str = ssh_key.ssh_user or "root"
            parameters: dict = dict(run.parameters or {})
            playbook_path = action.playbook_path
            action_destructive: bool = action.destructive
            action_roles_paths: tuple = action.roles_paths
            action_verify_playbook_path = action.verify_playbook_path
            action_verify_timeout: int = action.verify_timeout_seconds
            # Run-time toggles mirrored from ScheduledAction at dispatch time.
            # Honoured the same way as action_group.py — see Phases A/D/E.
            # Ignored when the action is non-destructive (no envelope runs).
            run_snapshot_enabled: bool = bool(run.snapshot_enabled)
            run_verify_enabled: bool = bool(run.verify_enabled)
            run_auto_rollback: bool = bool(run.auto_rollback)
            # Manifest-declared post-run module syncs. Fired against the
            # same host after a successful, non-dry-run completion so
            # labdog's desired state is re-enforced. See
            # ``app.sync.post_run.dispatch_post_run_sync``.
            action_post_run_sync: tuple[str, ...] = action.post_run_sync
            # Manifest-declared post-run resource registrations. After
            # success the helper inserts host-scope override rows for
            # each declared resource (skipping operator-managed
            # collisions) and then dispatches a follow-up sync to
            # refresh the UI tabs. See
            # ``app.sync.post_run.dispatch_post_run_register``.
            action_post_run_register: dict[str, tuple[dict, ...]] = dict(
                action.post_run_register
            )
            triggered_by_user_id: int | None = run.triggered_by_user_id

        # ------------------------------------------------------------------ #
        # Load Proxmox VM mapping if the action is destructive AND snapshots #
        # are enabled for this run. The snapshot is taken before the playbook #
        # runs and (when ``auto_rollback`` is on) reverted on failure. Toggle #
        # semantics mirror ``action_group.py`` Phases A/D/E:                  #
        #                                                                     #
        # * ``snapshot_enabled=False``  → skip Phase A (no Proxmox client     #
        #   loaded, no snapshot taken). Rollback can't run without a          #
        #   snapshot so it is implicitly disabled too.                        #
        # * ``verify_enabled=False``    → skip Phase D (no post-run verify).  #
        # * ``auto_rollback=False``     → skip Phase E (snapshot left in      #
        #   place on failure instead of reverted).                            #
        # ------------------------------------------------------------------ #
        proxmox_client = None
        pve_node: str | None = None
        vmid: int | None = None
        snapshot_name: str | None = None
        step_log: list[str] = []

        def _log_step(msg: str) -> None:
            """Append a line to step_log AND stream it to the SSE channel so
            the UI sees snapshot / verify / rollback / cleanup progress
            live, not just at the end. Broker hiccups are silent — the DB
            output is persisted regardless.
            """
            step_log.append(msg)
            try:
                r.publish(
                    channel,
                    json.dumps(
                        {
                            "event": "output",
                            "host_run_id": host_run_id,
                            "text": msg + "\n",
                        }
                    ),
                )
            except Exception:
                logger.debug(
                    "action_host: SSE publish failed for step log line",
                    exc_info=True,
                )

        if action_destructive and run_snapshot_enabled:
            try:
                from app.proxmox.client import ProxmoxClient  # noqa: PLC0415
                from app.proxmox.models import ProxmoxNode  # noqa: PLC0415
                from app.proxmox.vm_mapping import VMMapping  # noqa: PLC0415

                async with task_session() as db:
                    vm_map_result = await db.execute(
                        select(VMMapping).where(VMMapping.host_id == host_id)
                    )
                    vm_mapping = vm_map_result.scalar_one_or_none()
                    if vm_mapping is not None:
                        pve_node = vm_mapping.pve_node_name
                        vmid = vm_mapping.vmid
                        node_result = await db.execute(
                            select(ProxmoxNode).where(ProxmoxNode.id == vm_mapping.proxmox_node_id)
                        )
                        proxmox_node = node_result.scalar_one()
                        token_secret = decrypt_ssh_key(
                            proxmox_node.encrypted_token_secret, master_key
                        )
                        proxmox_client = ProxmoxClient(
                            api_url=proxmox_node.api_url,
                            token_id=proxmox_node.token_id,
                            token_secret=token_secret,
                            verify_ssl=proxmox_node.verify_ssl,
                        )
            except ImportError:
                logger.debug(
                    "action_host: proxmox modules not available; "
                    "snapshot steps will be skipped for action_run %d",
                    action_run_id,
                )

        # ------------------------------------------------------------------ #
        # Write SSH key to tmpfs                                              #
        # ------------------------------------------------------------------ #
        with open(ssh_key_path, "w") as fh:
            fh.write(private_key_text)
            if not private_key_text.endswith("\n"):
                fh.write("\n")
        os.chmod(ssh_key_path, 0o600)

        # ------------------------------------------------------------------ #
        # Build inventory and extra vars                                      #
        # ------------------------------------------------------------------ #
        inventory_json = generate_inventory(
            host_ip, host_port, ssh_key_path, ssh_user=ssh_user, hostname=host_hostname
        )

        dry_run = parameters.pop("__dry_run", False)
        extra_vars: dict | None = dict(parameters) if parameters else None
        if dry_run:
            extra_vars = extra_vars or {}
            extra_vars["ansible_check_mode"] = True

        # ------------------------------------------------------------------ #
        # Resolve playbook timeout from app settings                          #
        # ------------------------------------------------------------------ #
        try:
            timeout = int(get_setting_sync_typed("ansible.playbook_timeout"))
        except Exception:
            timeout = 1800

        # ------------------------------------------------------------------ #
        # Optional pre-update snapshot (destructive + VM mapping only)        #
        # ------------------------------------------------------------------ #
        r.publish(
            channel,
            json.dumps(
                {
                    "event": "host_status",
                    "host_id": host_id,
                    "host_run_id": host_run_id,
                    "status": "running",
                }
            ),
        )

        if proxmox_client is not None and pve_node is not None and vmid is not None:
            from app.workflows.steps.snapshot import create_snapshot  # noqa: PLC0415

            try:
                snapshot_name = await create_snapshot(proxmox_client, pve_node, vmid, action_run_id)
                _log_step(f"[snapshot] created {snapshot_name} on {pve_node}/{vmid}")
                async with task_session() as db:
                    hr_result = await db.execute(
                        select(ActionHostRun).where(ActionHostRun.id == host_run_id)
                    )
                    hr = hr_result.scalar_one()
                    hr.snapshot_name = snapshot_name
                    await db.commit()
            except Exception as exc:
                logger.exception(
                    "action_host: snapshot failed for action_run %d host %d: %s",
                    action_run_id,
                    host_id,
                    exc,
                )
                _log_step(f"[snapshot] FAILED: {exc}")
                async with task_session() as db:
                    hr_result = await db.execute(
                        select(ActionHostRun).where(ActionHostRun.id == host_run_id)
                    )
                    hr = hr_result.scalar_one()
                    hr.status = "failed"
                    hr.error_message = f"Snapshot failed: {exc}"
                    hr.finished_at = datetime.now(UTC)
                    hr.output = "\n".join(step_log)
                    await db.commit()
                r.publish(
                    channel,
                    json.dumps(
                        {
                            "event": "host_status",
                            "host_run_id": host_run_id,
                            "status": "failed",
                        }
                    ),
                )
                return
        elif action_destructive and not run_snapshot_enabled:
            # Destructive action with snapshot_enabled=False — operator
            # opted out. Log it so the run history reflects the choice.
            _log_step(
                "[snapshot] skipped — snapshot_enabled=false "
                "(no rollback available on failure)"
            )
        elif action_destructive:
            # Destructive action but no Proxmox VM mapping — snapshot-wrap is
            # skipped. Surface this in the log so users know no rollback is
            # possible for this run.
            _log_step(
                "[snapshot] skipped — host has no Proxmox VM mapping "
                "(no rollback available on failure)"
            )

        # ------------------------------------------------------------------ #
        # Run ansible-runner                                                  #
        # ------------------------------------------------------------------ #
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

        playbook_success: bool = runner.status == "successful"
        exit_code: int = runner.rc

        _log_step(f"[playbook] exit={exit_code} status={runner.status}")
        step_log.append("=== Ansible output ===")
        step_log.append(playbook_output)

        # Publish last 4 KB of playbook output to SSE. Prepend a divider so
        # the step-log lines stay visually separated from the raw ansible
        # stream in the live view.
        try:
            r.publish(
                channel,
                json.dumps(
                    {
                        "event": "output",
                        "host_run_id": host_run_id,
                        "text": "=== Ansible output ===\n" + playbook_output[-4000:],
                    }
                ),
            )
        except Exception:
            logger.debug("action_host: SSE publish failed for ansible output", exc_info=True)

        # ------------------------------------------------------------------ #
        # Post-run verification (only when we also took a snapshot — i.e.     #
        # destructive + VM mapped — AND verify_enabled is on). Mirrors        #
        # workflow_host.py's verify step but without a verification_prompt,   #
        # so only SSH hard checks run.                                        #
        # ------------------------------------------------------------------ #
        verification_passed = True
        verification_error: str | None = None
        if playbook_success and snapshot_name is not None and run_verify_enabled:
            if action_verify_playbook_path is not None:
                # Pack-supplied verify: run it the same way as the main
                # playbook. Any non-zero rc or ansible-runner status other
                # than "successful" counts as verification failure. Output
                # is appended to step_log so the UI run view shows it
                # distinctly from the main playbook.
                try:
                    verify_runner = run_ansible(
                        playbook_path=action_verify_playbook_path,
                        inventory_json=inventory_json,
                        private_data_dir=private_data_dir + "-verify",
                        extra_vars=extra_vars,
                        timeout=action_verify_timeout,
                        roles_paths=list(action_roles_paths) if action_roles_paths else None,
                    )
                    verify_output: str = (
                        verify_runner.stdout.read()
                        if hasattr(verify_runner.stdout, "read")
                        else str(verify_runner.stdout)
                    )
                    verification_passed = verify_runner.status == "successful"
                    _log_step(
                        f"[verify] pack playbook exit={verify_runner.rc} "
                        f"status={verify_runner.status} "
                        f"passed={verification_passed}"
                    )
                    step_log.append("=== Verify playbook output ===")
                    step_log.append(verify_output)
                    try:
                        r.publish(
                            channel,
                            json.dumps(
                                {
                                    "event": "output",
                                    "host_run_id": host_run_id,
                                    "text": "=== Verify playbook output ===\n"
                                    + verify_output[-4000:],
                                }
                            ),
                        )
                    except Exception:
                        logger.debug(
                            "action_host: SSE publish failed for verify output",
                            exc_info=True,
                        )
                    if not verification_passed:
                        verification_error = (
                            f"Verify playbook failed "
                            f"(status={verify_runner.status}, rc={verify_runner.rc})"
                        )
                except Exception as exc:
                    logger.exception(
                        "action_host: verify playbook errored for action_run %d host %d: %s",
                        action_run_id,
                        host_id,
                        exc,
                    )
                    verification_passed = False
                    verification_error = f"Verify playbook error: {exc}"
                    _log_step(f"[verify] ERROR: {exc}")
            else:
                try:
                    from app.packages.merge import get_effective_packages  # noqa: PLC0415
                    from app.services.merge import get_effective_services  # noqa: PLC0415
                    from app.workflows.steps.verify import run_verification  # noqa: PLC0415

                    async with task_session() as db:
                        host_result = await db.execute(select(Host).where(Host.id == host_id))
                        host = host_result.scalar_one()
                        effective_services = await get_effective_services(host_id, db)
                        effective_packages = await get_effective_packages(host_id, db)
                        verify_result = await run_verification(
                            host,
                            ssh_key_path,
                            effective_services,
                            effective_packages,
                            None,  # no AI prompt for ad-hoc actions
                            db,
                        )
                    verification_passed = bool(verify_result.get("passed"))
                    _log_step(
                        f"[verify] passed={verification_passed} "
                        f"services_ok={verify_result.get('services_ok')} "
                        f"packages_ok={verify_result.get('packages_ok')}"
                    )
                    if not verification_passed:
                        verification_error = f"Post-run verification failed: {verify_result}"
                except Exception as exc:
                    logger.exception(
                        "action_host: verification failed for action_run %d host %d: %s",
                        action_run_id,
                        host_id,
                        exc,
                    )
                    verification_passed = False
                    verification_error = f"Verification error: {exc}"
                    _log_step(f"[verify] ERROR: {exc}")

        # ------------------------------------------------------------------ #
        # Decide overall success, roll back on failure, clean up on success   #
        # ------------------------------------------------------------------ #
        success = playbook_success and verification_passed
        error_msg: str | None = None
        if not playbook_success:
            error_msg = f"ansible-runner exited with status={runner.status}, rc={exit_code}"
        elif not verification_passed:
            error_msg = verification_error

        if (
            not success
            and snapshot_name is not None
            and proxmox_client is not None
            and run_auto_rollback
        ):
            from app.workflows.steps.rollback import rollback_to_snapshot  # noqa: PLC0415

            _log_step(f"[rollback] restoring {snapshot_name}")
            try:
                async with task_session() as db:
                    host_result = await db.execute(select(Host).where(Host.id == host_id))
                    host = host_result.scalar_one()
                    rb = await rollback_to_snapshot(
                        proxmox_client, pve_node, vmid, snapshot_name, host, ssh_key_path, db
                    )
                    await db.commit()
                _log_step(f"[rollback] success={rb.get('success')} {rb.get('error', '')}".strip())
            except Exception as exc:
                logger.exception(
                    "action_host: rollback failed for action_run %d host %d: %s",
                    action_run_id,
                    host_id,
                    exc,
                )
                _log_step(f"[rollback] ERROR: {exc}")
        elif not success and snapshot_name is not None and not run_auto_rollback:
            # Snapshot present but auto_rollback=False — leave it in place so
            # the operator can inspect/revert manually. Logged so the run
            # output reflects the choice rather than silently keeping it.
            _log_step(
                f"[rollback] skipped — auto_rollback=false "
                f"(snapshot {snapshot_name} retained for manual recovery)"
            )

        if success and snapshot_name is not None and proxmox_client is not None:
            from app.workflows.steps.cleanup import delete_snapshot  # noqa: PLC0415

            try:
                await delete_snapshot(proxmox_client, pve_node, vmid, snapshot_name)
                _log_step(f"[cleanup] snapshot {snapshot_name} deleted")
            except Exception as exc:
                # Non-fatal — the action succeeded; orphan snapshot is
                # cosmetic and will be reaped by the periodic cleanup task.
                logger.warning(
                    "action_host: snapshot cleanup failed for action_run %d host %d: %s",
                    action_run_id,
                    host_id,
                    exc,
                )
                _log_step(f"[cleanup] WARN: {exc}")

        # ------------------------------------------------------------------ #
        # Persist result to DB                                                #
        # ------------------------------------------------------------------ #
        final_output = "\n".join(step_log)
        async with task_session() as db:
            hr_result = await db.execute(
                select(ActionHostRun).where(ActionHostRun.id == host_run_id)
            )
            hr = hr_result.scalar_one()
            hr.status = "succeeded" if success else "failed"
            hr.exit_code = exit_code
            hr.finished_at = datetime.now(UTC)
            hr.output = final_output
            if not success and error_msg is not None:
                hr.error_message = error_msg
            final_status = hr.status
            await db.commit()

            # Post-run module sync: only on success, never on dry-run,
            # and only when the manifest opted in. Dispatch failures
            # are logged but do not affect the action's status -- the
            # action itself already completed.
            if success and not dry_run and action_post_run_sync:
                try:
                    from app.sync.post_run import dispatch_post_run_sync

                    dispatched_ids = await dispatch_post_run_sync(
                        db,
                        host_id=host_id,
                        modules=action_post_run_sync,
                        triggered_by_user_id=triggered_by_user_id,
                    )
                    if dispatched_ids:
                        await db.commit()
                        logger.info(
                            "action_host: dispatched post_run_sync for "
                            "action_run %d host %d -- modules=%s job_ids=%s",
                            action_run_id,
                            host_id,
                            list(action_post_run_sync),
                            dispatched_ids,
                        )
                except Exception:
                    logger.exception(
                        "action_host: post_run_sync dispatch failed for "
                        "action_run %d host %d",
                        action_run_id,
                        host_id,
                    )

            # Post-run resource registration: insert host-scope overrides
            # for resources declared in the manifest, then dispatch a
            # follow-up sync so the cache catches up. Same success-only
            # / non-dry-run gates as post_run_sync. Failures logged
            # only; never affect the action's terminal status.
            if success and not dry_run and action_post_run_register:
                try:
                    from app.sync.post_run import dispatch_post_run_register

                    inserted = await dispatch_post_run_register(
                        db,
                        host_id=host_id,
                        declarations=action_post_run_register,
                        triggered_by_user_id=triggered_by_user_id,
                    )
                    if inserted:
                        await db.commit()
                        logger.info(
                            "action_host: dispatched post_run_register for "
                            "action_run %d host %d -- inserted=%s",
                            action_run_id,
                            host_id,
                            inserted,
                        )
                except Exception:
                    logger.exception(
                        "action_host: post_run_register dispatch failed for "
                        "action_run %d host %d",
                        action_run_id,
                        host_id,
                    )

        r.publish(
            channel,
            json.dumps(
                {
                    "event": "host_status",
                    "host_run_id": host_run_id,
                    "status": final_status,
                }
            ),
        )

        logger.info(
            "action_host: action_run %d / host_run %d completed — %s",
            action_run_id,
            host_run_id,
            final_status,
        )

    except Exception as exc:
        error_msg = str(exc)
        logger.exception(
            "action_host: action_run %d / host_run %d failed",
            action_run_id,
            host_run_id,
        )
        try:
            from sqlalchemy import select

            from app.db import task_session
            from app.models.action_run import ActionHostRun

            async with task_session() as db:
                hr_result = await db.execute(
                    select(ActionHostRun).where(ActionHostRun.id == host_run_id)
                )
                hr = hr_result.scalar_one_or_none()
                if hr is not None:
                    hr.status = "failed"
                    hr.error_message = error_msg
                    hr.finished_at = datetime.now(UTC)
                    await db.commit()
        except Exception:
            logger.exception(
                "action_host: could not persist failure for host_run %d",
                host_run_id,
            )
        try:
            r.publish(
                channel,
                json.dumps(
                    {"event": "host_status", "host_run_id": host_run_id, "status": "failed"}
                ),
            )
        except Exception:
            pass
        raise

    finally:
        # Dispatch the next pending op on this host (if we claimed it).
        # Runs even on the orchestrator-raised path so the per-host queue
        # never stalls. Failures here are swallowed — they must not mask
        # the real outcome of the task.
        if claimed and claimed_host_id is not None:
            try:
                async with task_session() as db:
                    await dispatch_next_pending_for_host(
                        db, claimed_host_id, exclude_action_run_id=action_run_id
                    )
            except Exception:
                logger.exception(
                    "action_host: dispatch-next-pending failed for host_id=%s "
                    "after action_run_id=%s; queue may be stuck until next op triggers it",
                    claimed_host_id,
                    action_run_id,
                )

        # CRITICAL: always remove the SSH key from tmpfs
        if os.path.exists(ssh_key_path):
            os.unlink(ssh_key_path)
        if os.path.exists(private_data_dir):
            shutil.rmtree(private_data_dir, ignore_errors=True)
