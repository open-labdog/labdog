import os
import shutil
import tempfile
from datetime import datetime, timezone

from app.tasks import celery_app


@celery_app.task(
    bind=True, name="app.tasks.cron_sync.cron_sync_task", queue="long_running"
)
def cron_sync_task(self, job_id: int, host_id: int) -> dict:
    """SECURITY: SSH key decrypted inside task, written to /dev/shm/, cleaned in finally."""
    import ansible_runner

    private_data_dir = tempfile.mkdtemp(prefix="barricade-")
    ssh_key_path = f"/dev/shm/barricade-{job_id}.key"

    try:
        import asyncio

        import yaml
        from sqlalchemy import select

        from app.crypto import decrypt_ssh_key, get_master_key
        from app.db import task_session
        from app.models.host import Host
        from app.models.host_module_status import HostModuleStatus
        from app.models.ssh_key import SSHKey
        from app.models.sync_job import SyncJob
        from app.cron.merge import get_effective_cron_jobs
        from app.cron.generator import generate_cron_playbook

        async def _run():
            async with task_session() as db:
                job_result = await db.execute(
                    select(SyncJob).where(SyncJob.id == job_id)
                )
                job = job_result.scalar_one()
                job.status = "running"
                job.started_at = datetime.now(timezone.utc)
                await db.commit()

                host_result = await db.execute(
                    select(Host).where(Host.id == host_id)
                )
                host = host_result.scalar_one()

                key_result = await db.execute(
                    select(SSHKey).where(SSHKey.id == host.ssh_key_id)
                )
                ssh_key = key_result.scalar_one()
                master_key = get_master_key()
                private_key_text = decrypt_ssh_key(
                    ssh_key.encrypted_private_key, master_key
                )

                with open(ssh_key_path, "w") as f:
                    f.write(private_key_text)
                    if not private_key_text.endswith("\n"):
                        f.write("\n")
                os.chmod(ssh_key_path, 0o600)

                effective_cron_jobs = await get_effective_cron_jobs(host_id, db)

                cron_jobs = [j.model_dump() for j in effective_cron_jobs]

                playbook_dict = generate_cron_playbook(
                    host.ip_address, cron_jobs, ssh_key_path
                )

                os.makedirs(f"{private_data_dir}/project", exist_ok=True)
                os.makedirs(f"{private_data_dir}/inventory", exist_ok=True)

                with open(f"{private_data_dir}/project/playbook.yml", "w") as f:
                    yaml.dump([playbook_dict], f, default_flow_style=False)

                inventory = {
                    "all": {
                        "hosts": {
                            host.ip_address: {
                                "ansible_port": host.ssh_port,
                                "ansible_user": ssh_key.ssh_user,
                                "ansible_ssh_private_key_file": ssh_key_path,
                                "ansible_ssh_common_args": "-o StrictHostKeyChecking=no",
                            }
                        }
                    }
                }
                with open(f"{private_data_dir}/inventory/hosts.yml", "w") as f:
                    yaml.dump(inventory, f, default_flow_style=False)

                return host, job, db

        host, job, db = asyncio.run(_run())

        runner = ansible_runner.run(
            private_data_dir=private_data_dir,
            playbook="playbook.yml",
            timeout=300,
        )

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

                status_result = await db.execute(
                    select(HostModuleStatus).where(
                        HostModuleStatus.host_id == host_id,
                        HostModuleStatus.module_type == "cron",
                    )
                )
                hms = status_result.scalar_one_or_none()
                if hms is None:
                    hms = HostModuleStatus(
                        host_id=host_id, module_type="cron"
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
        error_msg = str(e)

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

                status_result = await db.execute(
                    select(HostModuleStatus).where(
                        HostModuleStatus.host_id == host_id,
                        HostModuleStatus.module_type == "cron",
                    )
                )
                hms = status_result.scalar_one_or_none()
                if hms is None:
                    hms = HostModuleStatus(
                        host_id=host_id, module_type="cron"
                    )
                    db.add(hms)
                hms.sync_status = "error"
                hms.last_sync_at = datetime.now(timezone.utc)

                await db.commit()

        try:
            asyncio.run(_mark_failed())
        except Exception:
            pass

        raise

    finally:
        # SECURITY: always clean up decrypted SSH key from tmpfs
        if os.path.exists(ssh_key_path):
            os.unlink(ssh_key_path)
        if os.path.exists(private_data_dir):
            shutil.rmtree(private_data_dir, ignore_errors=True)
