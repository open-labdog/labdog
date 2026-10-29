"""Celery task that deploys CA certificates to a host via Ansible.

Unlike declarative module sync tasks, this is an *action* — a one-time
imperative deploy. It does NOT update ``HostModuleStatus`` and does NOT
mutate the host's ``sync_status`` badge. Execution status is recorded
solely on the ``SyncJob`` row, which is reused as the action-run record
(distinguished by ``module_type='ca_cert'``).

It takes its turn in the per-host queue like every other operation: it
waits while the host is busy, and hands the host on when it finishes.
It used to do neither, so it ran alongside whatever else was on the host
and left everything that had queued behind it waiting for the next
operation there — on valheim, an action's follow-up syncs for eight
hours (BUG-119).
"""

import asyncio
import os
import shutil
import tempfile
from datetime import UTC, datetime

from app.tasks import celery_app


async def _claim_or_defer(job_id: int, host_id: int) -> bool:
    """Take the host for this deploy, or leave the job ``pending``.

    The same gate as the sync orchestrator's: under the host's advisory
    lock, claim when nothing else is running there and flip the job to
    ``running`` in that transaction. A busy host gets the blocker stamped
    on the job and is retried by the host queue when it frees up. A job
    that is no longer pending — the queue can send one twice — is left
    alone.
    """
    from sqlalchemy import select

    from app.db import task_session
    from app.enum_utils import enum_str
    from app.models.sync_job import JobStatus, SyncJob
    from app.tasks.host_lock import acquire_host_lock, check_host_busy, format_pending_reason

    async with task_session() as db:
        await acquire_host_lock(db, host_id)
        job = (await db.execute(select(SyncJob).where(SyncJob.id == job_id))).scalar_one_or_none()
        if job is None or enum_str(job.status) != JobStatus.pending:
            return False
        blocker = await check_host_busy(db, host_id)
        if blocker is not None:
            job.pending_reason = await format_pending_reason(db, blocker)
            await db.commit()
            return False
        job.status = JobStatus.running
        job.started_at = datetime.now(UTC)
        job.pending_reason = None
        await db.commit()
        return True


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
    claimed = False

    try:
        claimed = asyncio.run(_claim_or_defer(job_id, host_id))
        if not claimed:
            return {"job_id": job_id, "status": "deferred"}

        import yaml
        from sqlalchemy import select

        from app.ansible_runtime.known_hosts import write_known_hosts
        from app.ca_certs.generator import generate_ca_cert_playbook
        from app.ca_certs.merge import get_effective_ca_certs
        from app.crypto import decrypt_ssh_key, get_master_key
        from app.db import task_session
        from app.models.host import Host
        from app.models.ssh_key import SSHKey
        from app.models.sync_job import SyncJob

        async def _prepare():
            async with task_session() as db:
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

                # SEC-26: pin the run to the host key LabDog recorded
                # over asyncssh. Written beside the key so the same
                # cleanup removes it.
                known_hosts_path = write_known_hosts(host.ssh_host_key_entry, ssh_key_path)

                effective = await get_effective_ca_certs(host_id, db)
                certs = [c.model_dump() for c in effective]

                result = generate_ca_cert_playbook(
                    host.ip_address,
                    certs,
                    ssh_key_path,
                    host.ssh_port,
                    ssh_user=ssh_key.ssh_user,
                    known_hosts_path=known_hosts_path,
                )

                os.makedirs(f"{private_data_dir}/project", exist_ok=True)
                os.makedirs(f"{private_data_dir}/inventory", exist_ok=True)

                with open(f"{private_data_dir}/project/playbook.yml", "w") as f:
                    yaml.dump(result["playbook"], f, default_flow_style=False)

                with open(f"{private_data_dir}/inventory/hosts", "w") as f:
                    f.write(result["inventory"])

        asyncio.run(_prepare())

        from app.settings_service import get_setting_cached_typed

        playbook_timeout = int(get_setting_cached_typed("ansible.playbook_timeout"))
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
        from app.ansible_runtime.known_hosts import remove_known_hosts

        if os.path.exists(ssh_key_path):
            os.unlink(ssh_key_path)
        # The pinned known-hosts file sits beside the key (SEC-26).
        remove_known_hosts(ssh_key_path)
        if os.path.exists(private_data_dir):
            shutil.rmtree(private_data_dir, ignore_errors=True)

        # The job is terminal by now, on every path that claimed the host.
        if claimed:
            from app.tasks.host_lock import release_host_queue

            asyncio.run(
                release_host_queue(
                    host_id, after=f"ca_cert job_id={job_id}", exclude_sync_job_id=job_id
                )
            )
