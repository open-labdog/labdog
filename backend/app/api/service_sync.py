from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.sync import SyncJobResponse
from app.auth.users import current_active_user
from app.db import get_db
from app.models.host import Host, HostGroupMembership
from app.models.ssh_key import SSHKey
from app.models.sync_job import SyncJob
from app.models.user import User
from app.crypto import decrypt_ssh_key, get_master_key
from app.services.collector import collect_service_states
from app.services.diff import compute_service_diff
from app.services.merge import get_effective_services

router = APIRouter(prefix="/services", tags=["service-sync"])


class ServiceDiffItemResponse(BaseModel):
    service_name: str
    desired_state: str
    desired_enabled: bool
    actual_state: str
    actual_enabled: bool
    reason: str


class ServiceSyncPlan(BaseModel):
    host_id: int
    hostname: str
    has_changes: bool
    services_to_update: list[ServiceDiffItemResponse]
    services_in_sync: list[str]
    services_with_errors: list[str]


@router.post("/hosts/{host_id}/plan", response_model=ServiceSyncPlan)
async def plan_service_sync(
    host_id: int,
    _: User = Depends(current_active_user),
    db: AsyncSession = Depends(get_db),
):
    """Preview service changes for a host (does NOT apply)."""
    host_result = await db.execute(select(Host).where(Host.id == host_id))
    host = host_result.scalar_one_or_none()
    if not host:
        raise HTTPException(status_code=404, detail="Host not found")

    if not host.ssh_key_id:
        raise HTTPException(status_code=400, detail="Host has no SSH key assigned")

    # Get desired services
    effective = await get_effective_services(host_id, db)
    if not effective:
        raise HTTPException(
            status_code=400, detail="No service rules defined for this host"
        )

    # Decrypt SSH key for collection
    key_result = await db.execute(
        select(SSHKey).where(SSHKey.id == host.ssh_key_id)
    )
    ssh_key = key_result.scalar_one()
    master_key = get_master_key()
    private_key_pem = decrypt_ssh_key(ssh_key.encrypted_private_key, master_key)

    # Collect current states
    service_names = [s.service_name for s in effective]
    current = await collect_service_states(
        host.ip_address, host.ssh_port, private_key_pem, service_names
    )

    # Compute diff
    diff = compute_service_diff(current, effective)

    return ServiceSyncPlan(
        host_id=host_id,
        hostname=host.hostname,
        has_changes=diff.has_changes,
        services_to_update=[
            ServiceDiffItemResponse(
                service_name=item.service_name,
                desired_state=item.desired_state,
                desired_enabled=item.desired_enabled,
                actual_state=item.actual_state,
                actual_enabled=item.actual_enabled,
                reason=item.reason,
            )
            for item in diff.services_to_update
        ],
        services_in_sync=diff.services_in_sync,
        services_with_errors=diff.services_with_errors,
    )


@router.post(
    "/hosts/{host_id}/sync", response_model=SyncJobResponse, status_code=201
)
async def trigger_service_sync(
    host_id: int,
    user: User = Depends(current_active_user),
    db: AsyncSession = Depends(get_db),
):
    """Trigger service sync for a single host."""
    # Check host exists
    host_result = await db.execute(select(Host).where(Host.id == host_id))
    host = host_result.scalar_one_or_none()
    if not host:
        raise HTTPException(status_code=404, detail="Host not found")

    if not host.ssh_key_id:
        raise HTTPException(status_code=400, detail="Host has no SSH key assigned")

    # Check no running service sync for this host
    running = await db.execute(
        select(SyncJob).where(
            SyncJob.host_id == host_id,
            SyncJob.module_type == "service",
            SyncJob.status.in_(["pending", "running"]),
        )
    )
    if running.scalar_one_or_none():
        raise HTTPException(
            status_code=409,
            detail="Service sync already in progress for this host",
        )

    # Check host has service rules
    effective = await get_effective_services(host_id, db)
    if not effective:
        raise HTTPException(
            status_code=400, detail="No service rules defined for this host"
        )

    # Create sync job
    job = SyncJob(
        host_id=host_id,
        module_type="service",
        status="pending",
        triggered_by_user_id=user.id,
    )
    db.add(job)
    await db.commit()
    await db.refresh(job)

    # Dispatch Celery task
    from app.tasks.service_sync import run_service_sync

    run_service_sync.delay(job_id=job.id, host_id=host_id)

    return job


@router.post("/groups/{group_id}/sync", status_code=201)
async def trigger_group_service_sync(
    group_id: int,
    user: User = Depends(current_active_user),
    db: AsyncSession = Depends(get_db),
):
    """Trigger service sync for all hosts in a group."""
    # Get hosts in group
    memberships = await db.execute(
        select(HostGroupMembership.c.host_id).where(
            HostGroupMembership.c.group_id == group_id
        )
    )
    host_ids = [r[0] for r in memberships.all()]
    if not host_ids:
        raise HTTPException(status_code=400, detail="No hosts in this group")

    jobs = []
    from app.tasks.service_sync import run_service_sync

    for hid in host_ids:
        # Skip hosts with running service syncs
        running = await db.execute(
            select(SyncJob).where(
                SyncJob.host_id == hid,
                SyncJob.module_type == "service",
                SyncJob.status.in_(["pending", "running"]),
            )
        )
        if running.scalar_one_or_none():
            continue

        job = SyncJob(
            host_id=hid,
            group_id=group_id,
            module_type="service",
            status="pending",
            triggered_by_user_id=user.id,
        )
        db.add(job)
        await db.flush()
        run_service_sync.delay(job_id=job.id, host_id=hid)
        jobs.append(job)

    await db.commit()
    return {"triggered": len(jobs), "skipped": len(host_ids) - len(jobs)}


@router.get("/jobs/{job_id}", response_model=SyncJobResponse)
async def get_service_job(
    job_id: int,
    _: User = Depends(current_active_user),
    db: AsyncSession = Depends(get_db),
):
    """Get a service sync job by ID."""
    result = await db.execute(select(SyncJob).where(SyncJob.id == job_id))
    job = result.scalar_one_or_none()
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return job
