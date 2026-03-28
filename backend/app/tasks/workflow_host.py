"""Per-host workflow executor Celery task.

Each WorkflowHostRun is processed independently by this task.  The
orchestrator (workflow_orchestrator.py) dispatches one instance of this
task per host inside a batch, waits for the whole batch, then moves on.
"""

import asyncio
import logging
import os
from datetime import datetime, timezone
from typing import Any

from app.tasks import celery_app
from app.workflows.steps.preflight import run_preflight
from app.workflows.steps.snapshot import create_snapshot
from app.workflows.steps.update import run_system_update
from app.workflows.steps.reboot import check_and_reboot
from app.workflows.steps.verify import run_verification
from app.workflows.steps.cleanup import delete_snapshot
from app.workflows.steps.rollback import rollback_to_snapshot

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Celery task entry point
# ---------------------------------------------------------------------------


@celery_app.task(
    bind=True,
    name="app.tasks.workflow_host.run_host_workflow",
    queue="long_running",
)
def run_host_workflow(self, run_id: int, host_run_id: int) -> dict:
    """Execute the update workflow for a single host.

    Dispatched by the group workflow orchestrator.  All DB access and I/O
    runs inside an async helper so that SQLAlchemy's async session is used
    consistently.

    Args:
        run_id: ID of the parent WorkflowRun.
        host_run_id: ID of the WorkflowHostRun record to drive.

    Returns:
        A dict summarising the outcome, e.g.
        ``{"run_id": 1, "host_run_id": 2, "status": "success"}``.
    """
    asyncio.run(_run_host_workflow_async(run_id, host_run_id))
    return {"run_id": run_id, "host_run_id": host_run_id}


# ---------------------------------------------------------------------------
# Async implementation
# ---------------------------------------------------------------------------


async def _run_host_workflow_async(run_id: int, host_run_id: int) -> None:
    """Drive a single WorkflowHostRun through all configured workflow steps."""
    from sqlalchemy import select

    from app.crypto.encryption import decrypt_ssh_key
    from app.crypto.key_management import get_master_key
    from app.db import task_session
    from app.models.host import Host
    from app.models.ssh_key import SSHKey
    from app.workflows.models import (
        UpdateWorkflow,
        WorkflowHostRun,
        WorkflowHostStatus,
        WorkflowRun,
        WorkflowStep,
    )

    ssh_key_path = f"/dev/shm/barricade-wf-{host_run_id}.key"

    try:
        async with task_session() as db:
            # -------------------------------------------------------------- #
            # Load core records                                               #
            # -------------------------------------------------------------- #
            host_run_result = await db.execute(
                select(WorkflowHostRun).where(WorkflowHostRun.id == host_run_id)
            )
            host_run: WorkflowHostRun = host_run_result.scalar_one()

            host_result = await db.execute(
                select(Host).where(Host.id == host_run.host_id)
            )
            host: Host = host_result.scalar_one()

            run_result = await db.execute(
                select(WorkflowRun).where(WorkflowRun.id == run_id)
            )
            workflow_run: WorkflowRun = run_result.scalar_one()

            workflow_result = await db.execute(
                select(UpdateWorkflow).where(
                    UpdateWorkflow.id == workflow_run.workflow_id
                )
            )
            workflow: UpdateWorkflow = workflow_result.scalar_one()

            # Mark host run as started
            host_run.status = WorkflowHostStatus.running
            host_run.started_at = datetime.now(timezone.utc)
            await db.flush()

            # -------------------------------------------------------------- #
            # Decrypt and write SSH key to tmpfs                              #
            # -------------------------------------------------------------- #
            if host.ssh_key_id is None:
                raise RuntimeError(
                    f"Host {host.id} ({host.hostname}) has no SSH key configured"
                )

            key_result = await db.execute(
                select(SSHKey).where(SSHKey.id == host.ssh_key_id)
            )
            ssh_key: SSHKey = key_result.scalar_one()

            master_key = get_master_key()
            private_key_text = decrypt_ssh_key(
                ssh_key.encrypted_private_key, master_key
            )

            with open(ssh_key_path, "w") as fh:
                fh.write(private_key_text)
                if not private_key_text.endswith("\n"):
                    fh.write("\n")
            os.chmod(ssh_key_path, 0o600)

            # -------------------------------------------------------------- #
            # Optional: load VMMapping and Proxmox client                    #
            # -------------------------------------------------------------- #
            vm_mapping = None
            proxmox_client = None
            pve_node: str | None = None
            vmid: int | None = None

            if workflow.pre_update_snapshot:
                try:
                    from app.proxmox.vm_mapping import VMMapping  # type: ignore[import]
                    from app.proxmox.models import ProxmoxNode  # type: ignore[import]
                    from app.proxmox.client import ProxmoxClient  # type: ignore[import]

                    vm_map_result = await db.execute(
                        select(VMMapping).where(
                            VMMapping.host_id == host.id
                        )
                    )
                    vm_mapping = vm_map_result.scalar_one_or_none()

                    if vm_mapping is not None:
                        pve_node = vm_mapping.pve_node_name
                        vmid = vm_mapping.vmid

                        node_result = await db.execute(
                            select(ProxmoxNode).where(
                                ProxmoxNode.id == vm_mapping.proxmox_node_id
                            )
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
                    else:
                        logger.warning(
                            "workflow_host: no VMMapping for host %d (%s); "
                            "snapshot steps will be skipped",
                            host.id,
                            host.hostname,
                        )
                except ImportError:
                    logger.debug(
                        "workflow_host: proxmox modules not available; "
                        "snapshot steps will be skipped"
                    )

            # -------------------------------------------------------------- #
            # Step loop                                                       #
            # -------------------------------------------------------------- #
            STEPS: list[tuple[WorkflowStep, str]] = [
                (WorkflowStep.preflight, "_step_preflight"),
                (WorkflowStep.snapshot, "_step_snapshot"),
                (WorkflowStep.update, "_step_update"),
                (WorkflowStep.reboot, "_step_reboot"),
                (WorkflowStep.verify, "_step_verify"),
                (WorkflowStep.cleanup, "_step_cleanup"),
            ]

            step_output: dict[str, Any] = {}
            snapshot_name: str | None = None
            final_status = WorkflowHostStatus.success

            for step_enum, step_fn_name in STEPS:
                # Skip snapshot/cleanup when pre_update_snapshot is disabled
                if step_enum in (WorkflowStep.snapshot, WorkflowStep.cleanup):
                    if not workflow.pre_update_snapshot or vm_mapping is None:
                        logger.debug(
                            "workflow_host: skipping step %s for host %d",
                            step_enum.value,
                            host.id,
                        )
                        continue

                # Skip reboot when auto_reboot is disabled
                if step_enum == WorkflowStep.reboot and not workflow.auto_reboot:
                    logger.debug(
                        "workflow_host: skipping reboot step for host %d "
                        "(auto_reboot=False)",
                        host.id,
                    )
                    continue

                # Update progress
                host_run.step = step_enum
                host_run.status = WorkflowHostStatus.running
                await db.flush()

                logger.info(
                    "workflow_host: host_run %d — starting step %s",
                    host_run_id,
                    step_enum.value,
                )

                try:
                    if step_enum == WorkflowStep.preflight:
                        result = await _step_preflight(
                            host, vm_mapping, ssh_key_path, proxmox_client, workflow, db
                        )
                        step_output[step_enum.value] = result

                    elif step_enum == WorkflowStep.snapshot:
                        snap = await _step_snapshot(
                            proxmox_client, pve_node, vmid, run_id
                        )
                        snapshot_name = snap
                        host_run.snapshot_name = snapshot_name
                        await db.flush()
                        step_output[step_enum.value] = {
                            "snapshot_name": snapshot_name
                        }

                    elif step_enum == WorkflowStep.update:
                        result = await _step_update(host, ssh_key_path)
                        step_output[step_enum.value] = result

                    elif step_enum == WorkflowStep.reboot:
                        result = await _step_reboot(host, ssh_key_path)
                        step_output[step_enum.value] = result

                    elif step_enum == WorkflowStep.verify:
                        result = await _step_verify(
                            host, ssh_key_path, workflow, db
                        )
                        step_output[step_enum.value] = result

                    elif step_enum == WorkflowStep.cleanup:
                        result = await _step_cleanup(
                            proxmox_client, pve_node, vmid, snapshot_name
                        )
                        step_output[step_enum.value] = result

                except Exception as exc:
                    logger.exception(
                        "workflow_host: host_run %d — step %s failed: %s",
                        host_run_id,
                        step_enum.value,
                        exc,
                    )
                    host_run.error_message = str(exc)
                    host_run.status = WorkflowHostStatus.failed
                    final_status = WorkflowHostStatus.failed
                    host_run.step_output = step_output
                    await db.flush()

                    # -------------------------------------------------- #
                    # Rollback                                             #
                    # -------------------------------------------------- #
                    if (
                        workflow.auto_rollback
                        and snapshot_name is not None
                        and proxmox_client is not None
                        and pve_node is not None
                        and vmid is not None
                    ):
                        host_run.step = WorkflowStep.rollback
                        await db.flush()
                        try:
                            rollback_result = await _step_rollback(
                                proxmox_client,
                                pve_node,
                                vmid,
                                snapshot_name,
                                host,
                                ssh_key_path,
                                db,
                            )
                            step_output["rollback"] = rollback_result
                        except Exception as rb_exc:
                            logger.exception(
                                "workflow_host: host_run %d — rollback failed: %s",
                                host_run_id,
                                rb_exc,
                            )
                            step_output["rollback"] = {
                                "status": "error",
                                "error": str(rb_exc),
                            }

                    break  # stop step loop after failure

            # -------------------------------------------------------------- #
            # Finalise                                                        #
            # -------------------------------------------------------------- #
            host_run.status = final_status
            host_run.step_output = step_output
            host_run.completed_at = datetime.now(timezone.utc)
            await db.commit()

            logger.info(
                "workflow_host: host_run %d completed — %s",
                host_run_id,
                final_status.value,
            )

    finally:
        # CRITICAL: always remove the SSH key from tmpfs
        if os.path.exists(ssh_key_path):
            os.remove(ssh_key_path)


# ---------------------------------------------------------------------------
# Step adapter functions
# ---------------------------------------------------------------------------


async def _step_preflight(
    host: Any,
    vm_mapping: Any,
    ssh_key_path: str,
    proxmox_client: Any,
    workflow: Any,
    db: Any,
) -> dict[str, Any]:
    """Verify SSH connectivity and pre-conditions before the update.

    Args:
        host: Host ORM object.
        vm_mapping: VMMapping ORM object, or None.
        ssh_key_path: Path to the decrypted SSH private key on tmpfs.
        proxmox_client: Authenticated ProxmoxClient, or None.
        workflow: UpdateWorkflow configuration record.
        db: Active async DB session.

    Returns:
        Step result dict.
    """
    result = await run_preflight(host, vm_mapping, ssh_key_path, proxmox_client, db)
    if not result.get("success"):
        checks = result.get("checks", {})
        reasons = []
        if not checks.get("ssh"):
            reasons.append("SSH unreachable")
        disk_gb = checks.get("disk_gb", 0)
        if disk_gb < 2.0:
            reasons.append(f"Insufficient disk space: {disk_gb:.2f} GB available (minimum 2 GB)")
        if not checks.get("vm_found"):
            reasons.append("VM mapping not found in Proxmox")
        if not checks.get("agent_ok"):
            reasons.append("QEMU guest agent not responding")
        raise Exception("; ".join(reasons) if reasons else "Preflight checks failed")
    return result


async def _step_snapshot(
    proxmox_client: Any,
    pve_node: str | None,
    vmid: int | None,
    run_id: int,
) -> str | None:
    """Create a Proxmox VM snapshot before the update.

    Args:
        proxmox_client: Authenticated ProxmoxClient.
        pve_node: Proxmox node name.
        vmid: VM identifier.
        run_id: Parent WorkflowRun ID (used to derive snapshot name).

    Returns:
        The snapshot name that was created.
    """
    snapshot_name = await create_snapshot(proxmox_client, pve_node, vmid, run_id)
    return snapshot_name


async def _step_update(
    host: Any,
    ssh_key_path: str,
) -> dict[str, Any]:
    """Run the package update on the host via SSH.

    ``run_system_update`` is synchronous (uses ansible-runner) so it is
    executed in the default thread-pool executor to avoid blocking the
    event loop.

    Args:
        host: Host ORM object.
        ssh_key_path: Path to the decrypted SSH private key on tmpfs.

    Returns:
        Step result dict.
    """
    loop = asyncio.get_running_loop()
    result = await loop.run_in_executor(
        None,
        run_system_update,
        host.ip_address,
        host.ssh_port or 22,
        host.ssh_user or "root",
        ssh_key_path,
    )
    if not result.get("success"):
        raise Exception(f"Update failed: {result.get('stdout', '')}")
    return result


async def _step_reboot(
    host: Any,
    ssh_key_path: str,
) -> dict[str, Any]:
    """Reboot the host and wait for it to come back online.

    Args:
        host: Host ORM object.
        ssh_key_path: Path to the decrypted SSH private key on tmpfs.

    Returns:
        Step result dict.
    """
    result = await check_and_reboot(
        host.ip_address,
        host.ssh_port or 22,
        host.ssh_user or "root",
        ssh_key_path,
    )
    if not result.get("success"):
        raise Exception(f"Reboot failed: {result.get('error', '')}")
    return result


async def _step_verify(
    host: Any,
    ssh_key_path: str,
    workflow: Any,
    db: Any,
) -> dict[str, Any]:
    """Verify the host is healthy after the update.

    Args:
        host: Host ORM object.
        ssh_key_path: Path to the decrypted SSH private key on tmpfs.
        workflow: UpdateWorkflow configuration record (contains
            ``verification_prompt``).
        db: Active async DB session.

    Returns:
        Step result dict including a ``passed`` boolean.
    """
    from app.services.merge import get_effective_services
    from app.packages.merge import get_effective_packages

    effective_services = await get_effective_services(host.id, db)
    effective_packages = await get_effective_packages(host.id, db)
    result = await run_verification(
        host,
        ssh_key_path,
        effective_services,
        effective_packages,
        workflow.verification_prompt,
        db,
    )
    if not result.get("passed"):
        raise Exception(f"Verification failed: {result}")
    return result


async def _step_cleanup(
    proxmox_client: Any,
    pve_node: str | None,
    vmid: int | None,
    snapshot_name: str | None,
) -> dict[str, Any]:
    """Delete the pre-update snapshot after a successful run.

    Args:
        proxmox_client: Authenticated ProxmoxClient.
        pve_node: Proxmox node name.
        vmid: VM identifier.
        snapshot_name: Name of the snapshot to delete.

    Returns:
        Step result dict.
    """
    result = await delete_snapshot(proxmox_client, pve_node, vmid, snapshot_name)
    return result


async def _step_rollback(
    proxmox_client: Any,
    pve_node: str | None,
    vmid: int | None,
    snapshot_name: str | None,
    host: Any,
    ssh_key_path: str,
    db: Any,
) -> dict[str, Any]:
    """Roll the VM back to the pre-update snapshot.

    Args:
        proxmox_client: Authenticated ProxmoxClient.
        pve_node: Proxmox node name.
        vmid: VM identifier.
        snapshot_name: Name of the snapshot to restore.
        host: Host ORM object.
        ssh_key_path: Path to the decrypted SSH private key on tmpfs.
        db: Active async DB session.

    Returns:
        Step result dict.
    """
    result = await rollback_to_snapshot(
        proxmox_client,
        pve_node,
        vmid,
        snapshot_name,
        host,
        ssh_key_path,
        db,
    )
    return result
