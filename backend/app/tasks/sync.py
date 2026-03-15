import os
import tempfile
import shutil
from datetime import datetime, timezone
from celery import current_task
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
    import ansible_runner

    # Create isolated working directory
    private_data_dir = tempfile.mkdtemp(prefix="barricade-")
    ssh_key_path = f"/dev/shm/barricade-{job_id}.key"

    try:
        # Import DB dependencies inside task (not at module level)
        import asyncio
        from sqlalchemy import select
        from app.db import AsyncSessionLocal
        from app.models.sync_job import SyncJob
        from app.models.host import Host, HostGroupMembership
        from app.models.host_group import HostGroup
        from app.models.ssh_key import SSHKey
        from app.models.firewall_rule import FirewallRule
        from app.crypto import decrypt_ssh_key, get_master_key
        from app.ansible.generator import generate_playbook
        from app.ansible.inventory import generate_inventory
        from app.rules.model import FirewallRuleSpec
        from app.rules.merge import merge_group_rules

        async def _run():
            async with AsyncSessionLocal() as db:
                # Update job status to running
                job_result = await db.execute(select(SyncJob).where(SyncJob.id == job_id))
                job = job_result.scalar_one()
                job.status = "running"
                job.started_at = datetime.now(timezone.utc)
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
                os.chmod(ssh_key_path, 0o600)

                # Get merged rules for this host
                memberships = await db.execute(
                    select(HostGroupMembership.c.group_id).where(
                        HostGroupMembership.c.host_id == host_id
                    )
                )
                group_ids = [r[0] for r in memberships.all()]

                groups_data = []
                for gid in group_ids:
                    group_result = await db.execute(select(HostGroup).where(HostGroup.id == gid))
                    group = group_result.scalar_one()
                    rules_result = await db.execute(
                        select(FirewallRule).where(FirewallRule.group_id == gid)
                    )
                    rules = [
                        FirewallRuleSpec(
                            action=r.action.value if hasattr(r.action, 'value') else r.action,
                            protocol=r.protocol.value if hasattr(r.protocol, 'value') else r.protocol,
                            direction=r.direction.value if hasattr(r.direction, 'value') else r.direction,
                            source_cidr=r.source_cidr,
                            destination_cidr=r.destination_cidr,
                            port_start=r.port_start,
                            port_end=r.port_end,
                            comment=r.comment,
                            is_system=r.is_system,
                            priority=r.priority,
                            group_id=r.group_id,
                            rule_id=r.id,
                        )
                        for r in rules_result.scalars().all()
                    ]
                    groups_data.append({"id": gid, "priority": group.priority, "rules": rules})

                merged_rules = merge_group_rules(groups_data)

                # Generate playbook and inventory
                backend = host.firewall_backend.value if hasattr(host.firewall_backend, 'value') else host.firewall_backend
                playbook_yaml = generate_playbook(backend, host.ip_address, merged_rules, ssh_key_path)
                inventory_json = generate_inventory(host.ip_address, host.ssh_port, ssh_key_path)

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
        runner = ansible_runner.run(
            private_data_dir=private_data_dir,
            playbook="playbook.yml",
            timeout=300,
        )

        # Update job status
        async def _update_status():
            async with AsyncSessionLocal() as db:
                job_result = await db.execute(select(SyncJob).where(SyncJob.id == job_id))
                job = job_result.scalar_one()
                job.status = "success" if runner.status == "successful" else "failed"
                job.completed_at = datetime.now(timezone.utc)
                job.ansible_output = runner.stdout.read() if hasattr(runner.stdout, 'read') else str(runner.stdout)
                if runner.status != "successful":
                    job.error_message = f"Ansible runner status: {runner.status}, rc: {runner.rc}"

                # Update host sync status
                host_result = await db.execute(select(Host).where(Host.id == host_id))
                host = host_result.scalar_one()
                host.sync_status = "in_sync" if runner.status == "successful" else "error"
                host.last_sync_at = datetime.now(timezone.utc)

                await db.commit()

        asyncio.run(_update_status())

        return {
            "job_id": job_id,
            "status": runner.status,
            "rc": runner.rc,
        }

    except Exception as e:
        # Update job as failed
        import asyncio
        from sqlalchemy import select
        from app.db import AsyncSessionLocal
        from app.models.sync_job import SyncJob

        async def _mark_failed():
            async with AsyncSessionLocal() as db:
                job_result = await db.execute(select(SyncJob).where(SyncJob.id == job_id))
                job = job_result.scalar_one_or_none()
                if job:
                    job.status = "failed"
                    job.completed_at = datetime.now(timezone.utc)
                    job.error_message = str(e)
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
