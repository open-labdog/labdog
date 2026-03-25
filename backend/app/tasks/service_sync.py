import os
import shutil
import tempfile
from datetime import datetime, timezone

from app.tasks import celery_app


@celery_app.task(
    bind=True, name="app.tasks.service_sync.run_service_sync", queue="long_running"
)
def run_service_sync(self, job_id: int, host_id: int) -> dict:
    """
    Run an Ansible playbook to sync service states on a host.

    SECURITY: SSH key is decrypted INSIDE this task (never passed as argument).
    Key written to /dev/shm/ (tmpfs) and cleaned in finally block.

    Args:
        job_id: SyncJob ID for status tracking
        host_id: Host ID to look up details and SSH key
    """
    import ansible_runner

    # Create isolated working directory
    private_data_dir = tempfile.mkdtemp(prefix="barricade-")
    ssh_key_path = f"/dev/shm/barricade-{job_id}.key"

    try:
        # Import DB dependencies inside task (not at module level)
        import asyncio

        from sqlalchemy import select

        from app.crypto import decrypt_ssh_key, get_master_key
        from app.db import task_session
        from app.models.host import Host
        from app.models.host_module_status import HostModuleStatus
        from app.models.ssh_key import SSHKey
        from app.models.sync_job import SyncJob
        from app.services.merge import get_effective_services
        from app.services.generator import generate_service_playbook

        async def _run():
            async with task_session() as db:
                # Update job status to running
                job_result = await db.execute(
                    select(SyncJob).where(SyncJob.id == job_id)
                )
                job = job_result.scalar_one()
                job.status = "running"
                job.started_at = datetime.now(timezone.utc)
                await db.commit()

                # Get host details
                host_result = await db.execute(
                    select(Host).where(Host.id == host_id)
                )
                host = host_result.scalar_one()

                # Get and decrypt SSH key
                key_result = await db.execute(
                    select(SSHKey).where(SSHKey.id == host.ssh_key_id)
                )
                ssh_key = key_result.scalar_one()
                master_key = get_master_key()
                private_key_text = decrypt_ssh_key(
                    ssh_key.encrypted_private_key, master_key
                )

                # Write key to tmpfs
                with open(ssh_key_path, "w") as f:
                    f.write(private_key_text)
                    if not private_key_text.endswith("\n"):
                        f.write("\n")
                os.chmod(ssh_key_path, 0o600)

                # Get effective services for this host
                effective = await get_effective_services(host_id, db)
                services = [
                    {
                        "service_name": s.service_name,
                        "state": s.state,
                        "enabled": s.enabled,
                    }
                    for s in effective
                ]

                # Generate playbook and inventory
                playbook_yaml, inventory_json = generate_service_playbook(
                    host.ip_address, host.ssh_port, services, ssh_key_path,
                    ssh_user=ssh_key.ssh_user,
                )

                # Write to private_data_dir
                os.makedirs(f"{private_data_dir}/project", exist_ok=True)
                os.makedirs(f"{private_data_dir}/inventory", exist_ok=True)

                with open(f"{private_data_dir}/project/playbook.yml", "w") as f:
                    f.write(playbook_yaml)
                with open(f"{private_data_dir}/inventory/hosts", "w") as f:
                    f.write(inventory_json)

                return host, job, db

        host, job, db = asyncio.run(_run())

        # Run ansible-runner (synchronous in Celery worker)
        from app.settings_service import get_setting_sync_typed
        playbook_timeout = int(get_setting_sync_typed("ansible.playbook_timeout"))
        runner = ansible_runner.run(
            private_data_dir=private_data_dir,
            playbook="playbook.yml",
            timeout=playbook_timeout,
        )

        # Update job status and HostModuleStatus
        async def _update_status():
            async with task_session() as db:
                job_result = await db.execute(
                    select(SyncJob).where(SyncJob.id == job_id)
                )
                job = job_result.scalar_one()
                job.status = "success" if runner.status == "successful" else "failed"
                job.completed_at = datetime.now(timezone.utc)
                job.ansible_output = (
                    runner.stdout.read()
                    if hasattr(runner.stdout, "read")
                    else str(runner.stdout)
                )
                if runner.status != "successful":
                    job.error_message = (
                        f"Ansible runner status: {runner.status}, rc: {runner.rc}"
                    )

                # Upsert host_module_status
                status_result = await db.execute(
                    select(HostModuleStatus).where(
                        HostModuleStatus.host_id == host_id,
                        HostModuleStatus.module_type == "service",
                    )
                )
                hms = status_result.scalar_one_or_none()
                if hms is None:
                    hms = HostModuleStatus(
                        host_id=host_id, module_type="service"
                    )
                    db.add(hms)
                hms.sync_status = (
                    "in_sync" if runner.status == "successful" else "error"
                )
                hms.last_sync_at = datetime.now(timezone.utc)

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

        # Update job as failed + HostModuleStatus as error
        import asyncio

        from sqlalchemy import select

        from app.db import task_session
        from app.models.host_module_status import HostModuleStatus
        from app.models.sync_job import SyncJob

        async def _mark_failed():
            async with task_session() as db:
                job_result = await db.execute(
                    select(SyncJob).where(SyncJob.id == job_id)
                )
                job = job_result.scalar_one_or_none()
                if job:
                    job.status = "failed"
                    job.completed_at = datetime.now(timezone.utc)
                    job.error_message = error_msg

                # Upsert host_module_status as error
                status_result = await db.execute(
                    select(HostModuleStatus).where(
                        HostModuleStatus.host_id == host_id,
                        HostModuleStatus.module_type == "service",
                    )
                )
                hms = status_result.scalar_one_or_none()
                if hms is None:
                    hms = HostModuleStatus(
                        host_id=host_id, module_type="service"
                    )
                    db.add(hms)
                hms.sync_status = "error"
                hms.last_sync_at = datetime.now(timezone.utc)

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
