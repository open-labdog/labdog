"""Cluster-mode action executor Celery task.

Used by actions whose manifest declares ``execution_mode: cluster``
(currently just ``k8s-upgrade``). Unlike :mod:`app.tasks.action_host`,
which runs once per host with a single-host inventory, this task runs
**once per group** with a multi-host inventory grouped under
``all.children.{control_plane,workers}``. The play layer in the
playbook drives serialisation via Ansible's ``serial`` keyword.

The orchestrator creates exactly one ``ActionHostRun`` row anchored to
the first control-plane host (the "driver"). That row carries the run
status the UI reflects; per-node detail lives in the streamed
ansible stdout for v1.

Snapshot wrapping (Proxmox) is **not** applied here. Cluster-wide
upgrades have their own rollback semantics (kubeadm + cluster state)
and snapshotting one node mid-run leaves the cluster in an
inconsistent state — operators should rely on the action's own drain /
re-admit invariants.
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


@celery_app.task(
    bind=True,
    name="app.tasks.action_cluster.run_action_cluster",
    queue="long_running",
)
def run_action_cluster(self, action_run_id: int, host_run_id: int) -> dict:
    """Run a cluster-mode action against one group.

    Args:
        action_run_id: parent ``ActionRun`` row.
        host_run_id: the single ``ActionHostRun`` driver row created by
            the orchestrator (its ``host_id`` is the first
            ``control_plane`` member).
    """
    asyncio.run(_run_action_cluster_async(action_run_id, host_run_id))
    return {"action_run_id": action_run_id, "host_run_id": host_run_id}


async def _run_action_cluster_async(action_run_id: int, host_run_id: int) -> None:
    import json

    import redis as redis_lib
    from sqlalchemy import select

    from app.actions.registry import ACTION_REGISTRY
    from app.ansible_runtime.inventory import generate_group_inventory
    from app.ansible_runtime.runner import run_ansible
    from app.config import settings
    from app.crypto import decrypt_ssh_key, get_master_key
    from app.db import task_session
    from app.models.action_run import ActionHostRun, ActionRun
    from app.models.host import Host, HostGroupMembership
    from app.models.ssh_key import SSHKey
    from app.settings_service import get_setting_sync_typed

    r = redis_lib.from_url(settings.redis.url)
    channel = f"actions.run.{action_run_id}"

    private_data_dir = tempfile.mkdtemp(prefix="labdog-cluster-")
    # Per-host SSH keys, written to tmpfs and removed in the finally block.
    ssh_key_paths: list[str] = []

    try:
        # --- Honour cancel token before doing real work ---
        if r.exists(f"actions.cancel.{action_run_id}"):
            async with task_session() as db:
                hr = (
                    await db.execute(select(ActionHostRun).where(ActionHostRun.id == host_run_id))
                ).scalar_one_or_none()
                if hr is not None and hr.status == "queued":
                    hr.status = "cancelled"
                    await db.commit()
            r.publish(
                channel,
                json.dumps(
                    {
                        "event": "host_status",
                        "host_run_id": host_run_id,
                        "status": "cancelled",
                    }
                ),
            )
            return

        # --- Load ActionRun + driver host_run + every member with role + ssh key ---
        async with task_session() as db:
            run = (
                await db.execute(select(ActionRun).where(ActionRun.id == action_run_id))
            ).scalar_one()
            hr = (
                await db.execute(select(ActionHostRun).where(ActionHostRun.id == host_run_id))
            ).scalar_one()

            action = ACTION_REGISTRY.get(run.action_key)
            if action is None:
                hr.status = "failed"
                hr.error_message = f"Action {run.action_key!r} not found in registry"
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

            if run.group_id is None:
                hr.status = "failed"
                hr.error_message = "cluster-mode actions require group_id"
                hr.finished_at = datetime.now(UTC)
                await db.commit()
                return

            # Memberships + role + host details + ssh key, in one round-trip
            # per table.
            membership_rows = (
                await db.execute(
                    select(
                        HostGroupMembership.c.host_id,
                        HostGroupMembership.c.role,
                    ).where(HostGroupMembership.c.group_id == run.group_id)
                )
            ).all()
            host_ids = [row.host_id for row in membership_rows]
            roles_by_host_id: dict[int, str | None] = {
                row.host_id: row.role for row in membership_rows
            }

            hosts = (await db.execute(select(Host).where(Host.id.in_(host_ids)))).scalars().all()
            ssh_key_ids = {h.ssh_key_id for h in hosts if h.ssh_key_id is not None}
            ssh_keys_by_id: dict[int, SSHKey] = {}
            if ssh_key_ids:
                rows = (
                    (await db.execute(select(SSHKey).where(SSHKey.id.in_(ssh_key_ids))))
                    .scalars()
                    .all()
                )
                ssh_keys_by_id = {k.id: k for k in rows}

            master_key = get_master_key()
            members: list[dict] = []
            decryption_error: str | None = None

            # Hosts the orchestrator already validated have a non-null role,
            # so any missing role here is a programmer error rather than
            # operator input — fail loudly.
            for h in hosts:
                role = roles_by_host_id.get(h.id)
                if role not in ("control_plane", "worker"):
                    hr.status = "failed"
                    hr.error_message = (
                        f"host {h.hostname!r} has no role assignment "
                        f"(got {role!r}); this should have been caught at "
                        "submit time"
                    )
                    hr.finished_at = datetime.now(UTC)
                    await db.commit()
                    return
                if h.ssh_key_id is None or h.ssh_key_id not in ssh_keys_by_id:
                    hr.status = "failed"
                    hr.error_message = f"host {h.hostname!r} has no SSH key configured"
                    hr.finished_at = datetime.now(UTC)
                    await db.commit()
                    return

                ssh_key = ssh_keys_by_id[h.ssh_key_id]
                try:
                    private_key_text = decrypt_ssh_key(ssh_key.encrypted_private_key, master_key)
                except Exception as exc:  # pragma: no cover — surfaces operator misconfig
                    decryption_error = f"SSH key decryption failed for host {h.hostname!r}: {exc}"
                    break

                fd, key_path = tempfile.mkstemp(
                    dir="/dev/shm", prefix="labdog-cluster-", suffix=".key"
                )
                os.close(fd)
                with open(key_path, "w") as fh:
                    fh.write(private_key_text)
                    if not private_key_text.endswith("\n"):
                        fh.write("\n")
                os.chmod(key_path, 0o600)
                ssh_key_paths.append(key_path)

                members.append(
                    {
                        "hostname": h.hostname,
                        "host_ip": h.ip_address,
                        "ssh_port": h.ssh_port or 22,
                        "ssh_user": ssh_key.ssh_user or "root",
                        "ssh_key_path": key_path,
                        "role": role,
                    }
                )

            if decryption_error:
                hr.status = "failed"
                hr.error_message = decryption_error
                hr.finished_at = datetime.now(UTC)
                await db.commit()
                return

            hr.status = "running"
            hr.started_at = datetime.now(UTC)
            await db.commit()

            playbook_path = action.playbook_path
            action_roles_paths: tuple = action.roles_paths
            parameters: dict = dict(run.parameters or {})

        # --- Build inventory + extra_vars + timeout ---
        inventory_json = generate_group_inventory(members)
        dry_run = parameters.pop("__dry_run", False)
        extra_vars: dict | None = dict(parameters) if parameters else None
        if dry_run:
            extra_vars = extra_vars or {}
            extra_vars["ansible_check_mode"] = True

        try:
            base_timeout = int(get_setting_sync_typed("ansible.playbook_timeout"))
        except Exception:
            base_timeout = 1800
        # Cluster runs are linear (serial:1) — budget grows roughly with
        # node count. Cap at 4 hours total so a runaway run doesn't park
        # a long_running worker forever.
        timeout = min(base_timeout * max(len(members), 1), 4 * 3600)

        r.publish(
            channel,
            json.dumps(
                {
                    "event": "host_status",
                    "host_run_id": host_run_id,
                    "status": "running",
                }
            ),
        )

        # --- Run ansible ---
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

        # Push final chunk to SSE
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
            logger.debug(
                "action_cluster: SSE publish failed for ansible output",
                exc_info=True,
            )

        # --- Persist final status ---
        final_status = "succeeded" if playbook_success else "failed"
        async with task_session() as db:
            hr = (
                await db.execute(select(ActionHostRun).where(ActionHostRun.id == host_run_id))
            ).scalar_one()
            hr.status = final_status
            hr.exit_code = exit_code
            hr.output = playbook_output
            hr.finished_at = datetime.now(UTC)
            if not playbook_success and not hr.error_message:
                hr.error_message = (
                    f"Cluster playbook failed (status={runner.status}, rc={exit_code})"
                )
            run = (
                await db.execute(select(ActionRun).where(ActionRun.id == action_run_id))
            ).scalar_one()
            run.status = final_status
            run.finished_at = datetime.now(UTC)
            await db.commit()

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

    finally:
        for p in ssh_key_paths:
            try:
                os.remove(p)
            except FileNotFoundError:
                pass
            except Exception:
                logger.debug("action_cluster: failed to remove %s", p, exc_info=True)
        try:
            shutil.rmtree(private_data_dir, ignore_errors=True)
        except Exception:
            logger.debug(
                "action_cluster: private_data_dir cleanup failed for %s",
                private_data_dir,
                exc_info=True,
            )
