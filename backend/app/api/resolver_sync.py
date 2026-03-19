from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.sync import SyncJobResponse
from app.auth.users import current_superuser
from app.crypto.encryption import decrypt_ssh_key
from app.crypto.key_management import get_master_key
from app.db import get_db
from app.models.host import Host, HostGroupMembership
from app.models.ssh_key import SSHKey
from app.models.sync_job import SyncJob
from app.models.user import User
from app.resolver.collector import collect_resolver_state
from app.resolver.diff import compute_resolver_diff
from app.resolver.merge import get_effective_resolver

router = APIRouter(prefix="/resolver", tags=["resolver-sync"])


class ResolverDiffResponse(BaseModel):
    nameservers_changed: bool
    search_domains_changed: bool
    options_changed: bool
    current: Optional[dict] = None
    desired: Optional[dict] = None


class ResolverSyncPlan(BaseModel):
    host_id: int
    hostname: str
    has_changes: bool
    diff: ResolverDiffResponse


@router.post("/hosts/{host_id}/plan", response_model=ResolverSyncPlan)
async def plan_resolver_sync(
    host_id: int,
    _: User = Depends(current_superuser),
    db: AsyncSession = Depends(get_db),
):
    host_result = await db.execute(select(Host).where(Host.id == host_id))
    host = host_result.scalar_one_or_none()
    if not host:
        raise HTTPException(status_code=404, detail="Host not found")

    if not host.ssh_key_id:
        raise HTTPException(status_code=400, detail="Host has no SSH key assigned")

    effective = await get_effective_resolver(host_id, db)
    if not effective:
        raise HTTPException(
            status_code=400, detail="No resolver config defined for this host"
        )

    key_result = await db.execute(
        select(SSHKey).where(SSHKey.id == host.ssh_key_id)
    )
    ssh_key = key_result.scalar_one()
    private_key_pem = decrypt_ssh_key(
        ssh_key.encrypted_private_key, get_master_key()
    )

    actual = await collect_resolver_state(
        host.ip_address, host.ssh_port, private_key_pem, effective.resolver_type
    )

    desired = {
        "nameservers": effective.nameservers,
        "search_domains": effective.search_domains,
        "options": effective.options,
    }
    diff = compute_resolver_diff(actual, desired)

    return ResolverSyncPlan(
        host_id=host_id,
        hostname=host.hostname,
        has_changes=diff.has_changes,
        diff=ResolverDiffResponse(
            nameservers_changed=diff.nameservers_changed,
            search_domains_changed=diff.search_domains_changed,
            options_changed=diff.options_changed,
            current=diff.current,
            desired=diff.desired,
        ),
    )


@router.post(
    "/hosts/{host_id}/sync", response_model=SyncJobResponse, status_code=201
)
async def trigger_resolver_sync(
    host_id: int,
    user: User = Depends(current_superuser),
    db: AsyncSession = Depends(get_db),
):
    host_result = await db.execute(select(Host).where(Host.id == host_id))
    host = host_result.scalar_one_or_none()
    if not host:
        raise HTTPException(status_code=404, detail="Host not found")

    if not host.ssh_key_id:
        raise HTTPException(status_code=400, detail="Host has no SSH key assigned")

    running = await db.execute(
        select(SyncJob).where(
            SyncJob.host_id == host_id,
            SyncJob.module_type == "resolver",
            SyncJob.status.in_(["pending", "running"]),
        )
    )
    if running.scalar_one_or_none():
        raise HTTPException(
            status_code=409,
            detail="Resolver sync already in progress for this host",
        )

    effective = await get_effective_resolver(host_id, db)
    if not effective:
        raise HTTPException(
            status_code=400, detail="No resolver config defined for this host"
        )

    job = SyncJob(
        host_id=host_id,
        module_type="resolver",
        status="pending",
        triggered_by_user_id=user.id,
    )
    db.add(job)
    await db.commit()
    await db.refresh(job)

    from app.tasks.resolver_sync import run_resolver_sync

    run_resolver_sync.delay(job_id=job.id, host_id=host_id)

    return job


@router.post("/groups/{group_id}/sync", status_code=201)
async def trigger_group_resolver_sync(
    group_id: int,
    user: User = Depends(current_superuser),
    db: AsyncSession = Depends(get_db),
):
    memberships = await db.execute(
        select(HostGroupMembership.c.host_id).where(
            HostGroupMembership.c.group_id == group_id
        )
    )
    host_ids = [r[0] for r in memberships.all()]
    if not host_ids:
        raise HTTPException(status_code=400, detail="No hosts in this group")

    jobs = []
    from app.tasks.resolver_sync import run_resolver_sync

    for hid in host_ids:
        running = await db.execute(
            select(SyncJob).where(
                SyncJob.host_id == hid,
                SyncJob.module_type == "resolver",
                SyncJob.status.in_(["pending", "running"]),
            )
        )
        if running.scalar_one_or_none():
            continue

        job = SyncJob(
            host_id=hid,
            group_id=group_id,
            module_type="resolver",
            status="pending",
            triggered_by_user_id=user.id,
        )
        db.add(job)
        await db.flush()
        run_resolver_sync.delay(job_id=job.id, host_id=hid)
        jobs.append(job)

    await db.commit()
    return {"triggered": len(jobs), "skipped": len(host_ids) - len(jobs)}


@router.get("/jobs/{job_id}", response_model=SyncJobResponse)
async def get_resolver_sync_job(
    job_id: int,
    _: User = Depends(current_superuser),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(SyncJob).where(SyncJob.id == job_id))
    job = result.scalar_one_or_none()
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return job
