import os
import re
import shutil
import tempfile
from datetime import datetime, timezone

from app.tasks import celery_app


def _parse_package_errors(ansible_output: str) -> dict[str, str]:
    """Extract per-package errors from Ansible output.

    Looks for task names like "Ensure present: nft" followed by
    fatal lines with error messages.

    Returns a dict mapping package_name -> error_message.
    """
    errors: dict[str, str] = {}
    lines = ansible_output.splitlines()
    current_package = None
    for line in lines:
        # Match task headers like:
        # "TASK [Ensure present: nft]", "TASK [Remove package: ufw]",
        # "TASK [Ensure latest: curl]", "TASK [Install package: nft version 1.0]"
        task_match = re.search(
            r"TASK \[(?:Ensure (?:present|latest)|Remove package|Install package): (.+?)\]",
            line,
        )
        if task_match:
            pkg = task_match.group(1).strip()
            # "Install package: nft version 1.0" -> "nft"
            current_package = re.sub(r"\s+version\s+.*", "", pkg)
            continue
        # Match fatal error with msg
        if current_package and "fatal:" in line:
            msg_match = re.search(r'"msg":\s*"(.+?)"', line)
            if msg_match:
                errors[current_package] = msg_match.group(1)
            else:
                errors[current_package] = "Task failed"
            current_package = None
    return errors


@celery_app.task(
    bind=True, name="app.tasks.package_sync.run_package_sync", queue="long_running"
)
def run_package_sync(self, job_id: int, host_id: int) -> dict:
    """SECURITY: SSH key decrypted inside task, written to /dev/shm/, cleaned in finally."""
    import ansible_runner

    private_data_dir = tempfile.mkdtemp(prefix="barricade-")
    fd, ssh_key_path = tempfile.mkstemp(dir="/dev/shm", prefix="barricade-", suffix=".key")
    os.close(fd)

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
        from app.packages.merge import get_effective_packages, get_effective_repos
        from app.packages.generator import generate_package_playbook

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

                effective_packages = await get_effective_packages(host_id, db)
                effective_repos = await get_effective_repos(host_id, db)

                packages = [p.model_dump() for p in effective_packages]
                repos = [r.model_dump() for r in effective_repos]

                result = generate_package_playbook(
                    host.ip_address, packages, repos, ssh_key_path, host.ssh_port,
                    ssh_user=ssh_key.ssh_user,
                )

                os.makedirs(f"{private_data_dir}/project", exist_ok=True)
                os.makedirs(f"{private_data_dir}/inventory", exist_ok=True)

                with open(f"{private_data_dir}/project/playbook.yml", "w") as f:
                    yaml.dump(result["playbook"], f, default_flow_style=False)

                with open(f"{private_data_dir}/inventory/hosts", "w") as f:
                    f.write(result["inventory"])

                desired_state = {"packages": packages, "repos": repos}
                return host, job, db, desired_state

        host, job, db, desired_state = asyncio.run(_run())

        from app.settings_service import get_setting_sync_typed
        playbook_timeout = int(get_setting_sync_typed("ansible.playbook_timeout"))
        runner = ansible_runner.run(
            private_data_dir=private_data_dir,
            playbook="playbook.yml",
            timeout=playbook_timeout,
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

                # Update host-level sync status
                from app.models.host import SyncStatus

                host_result = await db.execute(
                    select(Host).where(Host.id == host_id)
                )
                host_obj = host_result.scalar_one()
                host_obj.sync_status = (
                    SyncStatus.in_sync
                    if runner.status == "successful"
                    else SyncStatus.error
                )
                host_obj.last_sync_at = datetime.now(timezone.utc)

                status_result = await db.execute(
                    select(HostModuleStatus).where(
                        HostModuleStatus.host_id == host_id,
                        HostModuleStatus.module_type == "package",
                    )
                )
                hms = status_result.scalar_one_or_none()
                if hms is None:
                    hms = HostModuleStatus(
                        host_id=host_id, module_type="package"
                    )
                    db.add(hms)
                hms.sync_status = (
                    "in_sync" if runner.status == "successful" else "error"
                )
                hms.last_sync_at = datetime.now(timezone.utc)
                if runner.status == "successful" and desired_state:
                    hms.collected_state = desired_state
                    hms.collected_at = datetime.now(timezone.utc)
                    hms.error_message = None
                else:
                    # Parse per-package errors from Ansible output
                    ansible_out = job.ansible_output or ""
                    pkg_errors = _parse_package_errors(ansible_out)
                    if pkg_errors:
                        parts = [
                            f"{name}: {msg}" for name, msg in pkg_errors.items()
                        ]
                        hms.error_message = "; ".join(parts)
                    else:
                        hms.error_message = job.error_message

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
                        HostModuleStatus.module_type == "package",
                    )
                )
                hms = status_result.scalar_one_or_none()
                if hms is None:
                    hms = HostModuleStatus(
                        host_id=host_id, module_type="package"
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
