"""Celery task that deploys CA certificates to a host via Ansible.

Unlike declarative module sync tasks, this is an *action* — a one-time
imperative deploy. It does NOT update ``HostModuleStatus`` and does NOT
mutate the host's ``sync_status`` badge. Execution status is recorded
solely on the ``SyncJob`` row, which is reused as the action-run record
(distinguished by ``module_type='ca_cert'``).
"""

import os
import shutil
import tempfile
from datetime import UTC, datetime

from app.tasks import celery_app


@celery_app.task(
    bind=True,
    name="app.tasks.ca_cert_action.run_ca_cert_action",
    queue="long_running",
)
def run_ca_cert_action(self, job_id: int, host_id: int) -> dict:
    """SECURITY: SSH key decrypted inside task, written to /dev/shm/, cleaned in finally."""
    import ansible_runner

    private_data_dir = tempfile.mkdtemp(prefix="labdog-ca-")
    fd, ssh_key_path = tempfile.mkstemp(dir="/dev/shm", prefix="labdog-ca-", suffix=".key")
    os.close(fd)

    try:
        import asyncio

        import yaml
        from sqlalchemy import select

        from app.ca_certs.generator import generate_ca_cert_playbook
        from app.ca_certs.merge import get_effective_ca_certs
        from app.crypto import decrypt_ssh_key, get_master_key
        from app.db import task_session
        from app.models.host import Host
        from app.models.ssh_key import SSHKey
        from app.models.sync_job import SyncJob

        async def _prepare():
            async with task_session() as db:
                job = (await db.execute(select(SyncJob).where(SyncJob.id == job_id))).scalar_one()
                job.status = "running"
                job.started_at = datetime.now(UTC)
                await db.commit()

                host = (await db.execute(select(Host).where(Host.id == host_id))).scalar_one()

                ssh_key = (
                    await db.execute(select(SSHKey).where(SSHKey.id == host.ssh_key_id))
                ).scalar_one()
                master_key = get_master_key()
                private_key_text = decrypt_ssh_key(ssh_key.encrypted_private_key, master_key)

                with open(ssh_key_path, "w") as f:
                    f.write(private_key_text)
                    if not private_key_text.endswith("\n"):
                        f.write("\n")
                os.chmod(ssh_key_path, 0o600)

                effective = await get_effective_ca_certs(host_id, db)
                certs = [c.model_dump() for c in effective]

                result = generate_ca_cert_playbook(
                    host.ip_address,
                    certs,
                    ssh_key_path,
                    host.ssh_port,
                    ssh_user=ssh_key.ssh_user,
                )

                os.makedirs(f"{private_data_dir}/project", exist_ok=True)
                os.makedirs(f"{private_data_dir}/inventory", exist_ok=True)

                with open(f"{private_data_dir}/project/playbook.yml", "w") as f:
                    yaml.dump(result["playbook"], f, default_flow_style=False)

                with open(f"{private_data_dir}/inventory/hosts", "w") as f:
                    f.write(result["inventory"])

        asyncio.run(_prepare())

        from app.settings_service import get_setting_sync_typed

        playbook_timeout = int(get_setting_sync_typed("ansible.playbook_timeout"))
        runner = ansible_runner.run(
            private_data_dir=private_data_dir,
            playbook="playbook.yml",
            timeout=playbook_timeout,
        )

        async def _record_result():
            async with task_session() as db:
                job = (await db.execute(select(SyncJob).where(SyncJob.id == job_id))).scalar_one()
                job.status = "success" if runner.status == "successful" else "failed"
                job.completed_at = datetime.now(UTC)
                job.ansible_output = (
                    runner.stdout.read() if hasattr(runner.stdout, "read") else str(runner.stdout)
                )
                if runner.status != "successful":
                    from app.ansible_runtime.diagnose import interpret_runner_failure

                    job.error_message = interpret_runner_failure(runner)
                await db.commit()

        asyncio.run(_record_result())

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
        from app.models.sync_job import SyncJob

        async def _mark_failed():
            async with task_session() as db:
                job = (
                    await db.execute(select(SyncJob).where(SyncJob.id == job_id))
                ).scalar_one_or_none()
                if job:
                    job.status = "failed"
                    job.completed_at = datetime.now(UTC)
                    job.error_message = error_msg
                    await db.commit()

        try:
            asyncio.run(_mark_failed())
        except Exception:
            pass

        raise

    finally:
        if os.path.exists(ssh_key_path):
            os.unlink(ssh_key_path)
        if os.path.exists(private_data_dir):
            shutil.rmtree(private_data_dir, ignore_errors=True)
