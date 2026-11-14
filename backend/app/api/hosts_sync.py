from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api._group_sync import group_member_ids_or_400, sync_group_members
from app.api.sync import SyncJobResponse
from app.auth.permissions import require, row_scope
from app.crypto import decrypt_ssh_key, get_master_key
from app.db import get_db
from app.hosts_mgmt.collector import collect_hosts_file
from app.hosts_mgmt.diff import compute_hosts_diff, diff_entry_dict
from app.hosts_mgmt.merge import get_effective_hosts_entries
from app.models.host import Host
from app.models.ssh_key import SSHKey
from app.models.sync_job import SyncJob
from app.models.user import User

router = APIRouter(prefix="/hosts-mgmt", tags=["hosts-sync"])


class HostsSyncPlan(BaseModel):
    host_id: int
    hostname: str
    has_changes: bool
    entries_to_add: list[dict]
    entries_to_remove: list[dict]
    entries_to_update: list[dict]
    entries_in_sync: list[str]


@router.post("/hosts/{host_id}/plan", response_model=HostsSyncPlan)
async def plan_hosts_sync(
    host_id: int,
    _: User = Depends(require("sync.run")),
    db: AsyncSession = Depends(get_db),
):
    """Preview /etc/hosts changes for a host (does NOT apply)."""
    host_result = await db.execute(select(Host).where(Host.id == host_id))
    host = host_result.scalar_one_or_none()
    if not host:
        raise HTTPException(status_code=404, detail="Host not found")

    if not host.ssh_key_id:
        raise HTTPException(status_code=400, detail="Host has no SSH key assigned")

    # Get desired hosts entries
    effective = await get_effective_hosts_entries(host_id, db)
    if not effective:
        raise HTTPException(status_code=400, detail="No hosts entries defined for this host")

    # Decrypt SSH key for collection
    key_result = await db.execute(select(SSHKey).where(SSHKey.id == host.ssh_key_id))
    ssh_key = key_result.scalar_one()
    master_key = get_master_key()
    private_key_pem = decrypt_ssh_key(ssh_key.encrypted_private_key, master_key)

    # Collect current /etc/hosts from remote host
    current = await collect_hosts_file(host, db, private_key_pem)

    # Compute diff
    diff = compute_hosts_diff(current, effective)

    return HostsSyncPlan(
        host_id=host_id,
        hostname=host.hostname,
        has_changes=diff.has_changes,
        entries_to_add=[diff_entry_dict(e) for e in diff.entries_to_add],
        entries_to_remove=[diff_entry_dict(e) for e in diff.entries_to_remove],
        entries_to_update=[diff_entry_dict(e) for e in diff.entries_to_update],
        entries_in_sync=diff.entries_in_sync,
    )


@router.post("/hosts/{host_id}/sync", response_model=SyncJobResponse, status_code=201)
async def trigger_hosts_sync(
    host_id: int,
    user: User = Depends(require("sync.run")),
    db: AsyncSession = Depends(get_db),
):
    """Trigger /etc/hosts sync for a single host."""
    # Check host exists
    host_result = await db.execute(select(Host).where(Host.id == host_id))
    host = host_result.scalar_one_or_none()
    if not host:
        raise HTTPException(status_code=404, detail="Host not found")

    if not host.ssh_key_id:
        raise HTTPException(status_code=400, detail="Host has no SSH key assigned")

    # Check no running hosts_file sync for this host
    running = await db.execute(
        select(SyncJob).where(
            SyncJob.host_id == host_id,
            SyncJob.module_type == "hosts_file",
            SyncJob.status.in_(["pending", "running"]),
        )
    )
    if running.scalar_one_or_none():
        raise HTTPException(
            status_code=409,
            detail="Hosts file sync already in progress for this host",
        )

    # Check host has hosts entries
    effective = await get_effective_hosts_entries(host_id, db)
    if not effective:
        raise HTTPException(status_code=400, detail="No hosts entries defined for this host")

    # Create sync job
    job = SyncJob(
        host_id=host_id,
        module_type="hosts_file",
        status="pending",
        triggered_by_user_id=user.id,
    )
    db.add(job)
    await db.commit()
    await db.refresh(job)

    # Dispatch Celery task
    from app.tasks.hosts_sync import run_hosts_sync

    run_hosts_sync.delay(job_id=job.id, host_id=host_id)

    return job


@router.post("/groups/{group_id}/sync", status_code=201)
async def trigger_group_hosts_sync(
    group_id: int,
    user: User = Depends(require("sync.run")),
    db: AsyncSession = Depends(get_db),
):
    """Trigger /etc/hosts sync for all hosts in a group."""
    # Get hosts in group
    host_ids = await group_member_ids_or_400(db, group_id)

    from app.tasks.hosts_sync import run_hosts_sync

    return await sync_group_members(db, group_id, host_ids, "hosts_file", user, run_hosts_sync)


@router.get("/jobs/{job_id}", response_model=SyncJobResponse)
async def get_hosts_job(
    job_id: int,
    _: User = Depends(require("hosts.view", scope=row_scope(SyncJob, "job_id"))),
    db: AsyncSession = Depends(get_db),
):
    """Get a hosts file sync job by ID."""
    result = await db.execute(select(SyncJob).where(SyncJob.id == job_id))
    job = result.scalar_one_or_none()
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return job
