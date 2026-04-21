import os
import shutil
import tempfile
from datetime import UTC, datetime

from app.tasks import celery_app


@celery_app.task(bind=True, name="app.tasks.sync.run_sync_playbook", queue="long_running")
def run_sync_playbook(self, job_id: int, host_id: int) -> dict:
    """
    Run an Ansible playbook for a sync job.

    SECURITY: SSH key is decrypted INSIDE this task (never passed as argument).
    Key written to /dev/shm/ (tmpfs) and cleaned in finally block.

    Args:
        job_id: SyncJob ID for status tracking
        host_id: Host ID to look up details and SSH key
    """
    # Create isolated working directory
    private_data_dir = tempfile.mkdtemp(prefix="barricade-")
    fd, ssh_key_path = tempfile.mkstemp(dir="/dev/shm", prefix="barricade-", suffix=".key")
    os.close(fd)

    try:
        # Import DB dependencies inside task (not at module level)
        import asyncio

        from sqlalchemy import select

        from app.ansible.generator import generate_playbook
        from app.ansible.inventory import generate_inventory
        from app.crypto import decrypt_ssh_key, get_master_key
        from app.db import task_session
        from app.models.host import Host
        from app.models.ssh_key import SSHKey
        from app.models.sync_job import SyncJob

        async def _run():
            async with task_session() as db:
                # Update job status to running
                job_result = await db.execute(select(SyncJob).where(SyncJob.id == job_id))
                job = job_result.scalar_one()
                job.status = "running"
                job.started_at = datetime.now(UTC)
                await db.commit()

                # Get host details
                host_result = await db.execute(select(Host).where(Host.id == host_id))
                host = host_result.scalar_one()

                # Get and decrypt SSH key
                key_result = await db.execute(select(SSHKey).where(SSHKey.id == host.ssh_key_id))
                ssh_key = key_result.scalar_one()
                master_key = get_master_key()
                private_key_text = decrypt_ssh_key(ssh_key.encrypted_private_key, master_key)

                # Write key to tmpfs
                with open(ssh_key_path, "w") as f:
                    f.write(private_key_text)
                    if not private_key_text.endswith("\n"):
                        f.write("\n")
                os.chmod(ssh_key_path, 0o600)

                # Get merged rules for this host, then resolve host-ref FKs to CIDRs
                from app.rules.desired_state import get_desired_state, resolve_specs

                merged_rules, merged_policies = await get_desired_state(
                    host_id, db, host_source_ip=host.barricade_source_ip
                )
                merged_rules = await resolve_specs(db, merged_rules)

                # Generate playbook and inventory
                backend = (
                    host.firewall_backend.value
                    if hasattr(host.firewall_backend, "value")
                    else host.firewall_backend
                )
                if backend == "unknown":
                    job.status = "failed"
                    job.completed_at = datetime.now(UTC)
                    job.error_message = (
                        "Cannot sync firewall: backend not detected. Run 'Collect State' first."
                    )
                    await db.commit()
                    return None, None, None, None, None, None
                playbook_yaml = generate_playbook(
                    backend,
                    host.ip_address,
                    merged_rules,
                    ssh_key_path,
                    policies=merged_policies,
                )
                inventory_json = generate_inventory(
                    host.ip_address, host.ssh_port, ssh_key_path, ssh_user=ssh_key.ssh_user
                )

                return host, job, db, merged_rules, playbook_yaml, inventory_json

        result = asyncio.run(_run())
        host, job, db, merged_rules, playbook_yaml, inventory_json = result
        if host is None:
            return {"status": "failed", "error": "Unsupported firewall backend"}

        # Run ansible-runner (synchronous in Celery worker)
        from app.ansible.runner import run_ansible
        from app.settings_service import get_setting_sync_typed

        playbook_timeout = int(get_setting_sync_typed("ansible.playbook_timeout"))
        runner = run_ansible(
            playbook_yaml=playbook_yaml,
            inventory_json=inventory_json,
            private_data_dir=private_data_dir,
            timeout=playbook_timeout,
        )

        # Update job status
        async def _update_status():
            async with task_session() as db:
                from dataclasses import asdict

                from app.models.host_module_status import HostModuleStatus

                job_result = await db.execute(select(SyncJob).where(SyncJob.id == job_id))
                job = job_result.scalar_one()
                job.status = "success" if runner.status == "successful" else "failed"
                job.completed_at = datetime.now(UTC)
                job.ansible_output = (
                    runner.stdout.read() if hasattr(runner.stdout, "read") else str(runner.stdout)
                )
                if runner.status != "successful":
                    job.error_message = f"Ansible runner status: {runner.status}, rc: {runner.rc}"

                # Update host sync status
                host_result = await db.execute(select(Host).where(Host.id == host_id))
                host = host_result.scalar_one()
                host.last_sync_at = datetime.now(UTC)

                # Collect actual state from host so it matches what collect-state returns
                if runner.status == "successful":
                    from app.sync.collector import collect_current_rules

                    now = datetime.now(UTC)
                    backend_str = (
                        host.firewall_backend.value
                        if hasattr(host.firewall_backend, "value")
                        else str(host.firewall_backend)
                    )
                    key_result = await db.execute(
                        select(SSHKey).where(SSHKey.id == host.ssh_key_id)
                    )
                    ssh_key = key_result.scalar_one()
                    private_pem = decrypt_ssh_key(ssh_key.encrypted_private_key, get_master_key())

                    try:
                        collected = await collect_current_rules(
                            host.ip_address,
                            host.ssh_port,
                            private_pem,
                            backend_str,
                            ssh_user=ssh_key.ssh_user,
                        )
                        collected_state = [asdict(r) for r in collected]
                    except Exception:
                        collected_state = None

                    hms_result = await db.execute(
                        select(HostModuleStatus).where(
                            HostModuleStatus.host_id == host_id,
                            HostModuleStatus.module_type == "firewall",
                        )
                    )
                    hms = hms_result.scalar_one_or_none()
                    if hms is None:
                        hms = HostModuleStatus(host_id=host_id, module_type="firewall")
                        db.add(hms)
                    if collected_state is not None:
                        hms.collected_state = collected_state
                    hms.collected_at = now
                    hms.sync_status = "in_sync"
                    hms.error_message = None
                else:
                    # Mark firewall module as error on failed sync
                    hms_result = await db.execute(
                        select(HostModuleStatus).where(
                            HostModuleStatus.host_id == host_id,
                            HostModuleStatus.module_type == "firewall",
                        )
                    )
                    hms = hms_result.scalar_one_or_none()
                    if hms:
                        hms.sync_status = "error"

                from app.api.host_state import refresh_host_sync_status

                await refresh_host_sync_status(host, db)
                await db.commit()

        asyncio.run(_update_status())

        return {
            "job_id": job_id,
            "status": runner.status,
            "rc": runner.rc,
        }

    except Exception as e:
        # Capture error message before closure (e is unbound after except block exits)
        error_msg = str(e)

        # Update job as failed
        import asyncio

        from sqlalchemy import select

        from app.db import task_session
        from app.models.sync_job import SyncJob

        async def _mark_failed():
            async with task_session() as db:
                job_result = await db.execute(select(SyncJob).where(SyncJob.id == job_id))
                job = job_result.scalar_one_or_none()
                if job:
                    job.status = "failed"
                    job.completed_at = datetime.now(UTC)
                    job.error_message = error_msg
                    await db.commit()

        try:
            asyncio.run(_mark_failed())
        except Exception:
            pass  # Best effort status update

        raise

    finally:
        # CRITICAL: Clean up SSH key and temp directory
        if os.path.exists(ssh_key_path):
            os.unlink(ssh_key_path)
        if os.path.exists(private_data_dir):
            shutil.rmtree(private_data_dir, ignore_errors=True)
