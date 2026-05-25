from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.sync import SyncJobResponse
from app.auth.users import current_active_user
from app.cron.collector import collect_cron_jobs
from app.cron.diff import diff_cron_jobs
from app.cron.merge import get_effective_cron_jobs
from app.crypto.encryption import decrypt_ssh_key
from app.crypto.key_management import get_master_key
from app.db import get_db
from app.models.host import Host, HostGroupMembership
from app.models.host_module_status import HostModuleStatus
from app.models.ssh_key import SSHKey
from app.models.sync_job import SyncJob
from app.models.user import User

router = APIRouter(prefix="/cron", tags=["cron-sync"])


class CronDiffResponse(BaseModel):
    jobs_to_add: list[str]
    jobs_to_remove: list[str]
    jobs_to_update: list[str]
    jobs_in_sync: list[str]


class CronSyncPlan(BaseModel):
    host_id: int
    hostname: str
    has_changes: bool
    cron_diff: CronDiffResponse


@router.post("/hosts/{host_id}/plan", response_model=CronSyncPlan)
async def plan_cron_sync(
    host_id: int,
    _: User = Depends(current_active_user),
    db: AsyncSession = Depends(get_db),
):
    host_result = await db.execute(select(Host).where(Host.id == host_id))
    host = host_result.scalar_one_or_none()
    if not host:
        raise HTTPException(status_code=404, detail="Host not found")

    if not host.ssh_key_id:
        raise HTTPException(status_code=400, detail="Host has no SSH key assigned")

    effective_jobs = await get_effective_cron_jobs(host_id, db)
    if not effective_jobs:
        raise HTTPException(status_code=400, detail="No cron job rules defined for this host")

    key_result = await db.execute(select(SSHKey).where(SSHKey.id == host.ssh_key_id))
    ssh_key = key_result.scalar_one()
    private_key_pem = decrypt_ssh_key(ssh_key.encrypted_private_key, get_master_key())

    desired = [j.model_dump() for j in effective_jobs]

    users = list({j["user"] for j in desired})
    actual = await collect_cron_jobs(host.ip_address, host.ssh_port, private_key_pem, users)

    diff = diff_cron_jobs(desired, actual)

    has_changes = bool(diff.jobs_to_add or diff.jobs_to_remove or diff.jobs_to_update)

    return CronSyncPlan(
        host_id=host_id,
        hostname=host.hostname,
        has_changes=has_changes,
        cron_diff=CronDiffResponse(
            jobs_to_add=diff.jobs_to_add,
            jobs_to_remove=diff.jobs_to_remove,
            jobs_to_update=diff.jobs_to_update,
            jobs_in_sync=diff.jobs_in_sync,
        ),
    )


@router.post("/hosts/{host_id}/sync", response_model=SyncJobResponse, status_code=201)
async def trigger_cron_sync(
    host_id: int,
    user: User = Depends(current_active_user),
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
            SyncJob.module_type == "cron",
            SyncJob.status.in_(["pending", "running"]),
        )
    )
    if running.scalar_one_or_none():
        raise HTTPException(
            status_code=409,
            detail="Cron sync already in progress for this host",
        )

    effective_jobs = await get_effective_cron_jobs(host_id, db)
    if not effective_jobs:
        raise HTTPException(status_code=400, detail="No cron job rules defined for this host")

    job = SyncJob(
        host_id=host_id,
        module_type="cron",
        status="pending",
        triggered_by_user_id=user.id,
    )
    db.add(job)
    await db.commit()
    await db.refresh(job)

    from app.tasks.cron_sync import cron_sync_task

    cron_sync_task.delay(job_id=job.id, host_id=host_id)

    return job


@router.post("/groups/{group_id}/sync", status_code=201)
async def trigger_group_cron_sync(
    group_id: int,
    user: User = Depends(current_active_user),
    db: AsyncSession = Depends(get_db),
):
    memberships = await db.execute(
        select(HostGroupMembership.c.host_id).where(HostGroupMembership.c.group_id == group_id)
    )
    host_ids = [r[0] for r in memberships.all()]
    if not host_ids:
        raise HTTPException(status_code=400, detail="No hosts in this group")

    from app.tasks.cron_sync import cron_sync_task

    # BUG-37: dispatch after commit, not before — see app/api/sync.py.
    pending: list[tuple[int, int]] = []
    for hid in host_ids:
        running = await db.execute(
            select(SyncJob).where(
                SyncJob.host_id == hid,
                SyncJob.module_type == "cron",
                SyncJob.status.in_(["pending", "running"]),
            )
        )
        if running.scalar_one_or_none():
            continue

        job = SyncJob(
            host_id=hid,
            group_id=group_id,
            module_type="cron",
            status="pending",
            triggered_by_user_id=user.id,
        )
        db.add(job)
        await db.flush()
        pending.append((job.id, hid))

    await db.commit()
    for job_id, hid in pending:
        cron_sync_task.delay(job_id=job_id, host_id=hid)
    return {"triggered": len(pending), "skipped": len(host_ids) - len(pending)}


@router.get("/jobs/{job_id}", response_model=SyncJobResponse)
async def get_cron_sync_job(
    job_id: int,
    _: User = Depends(current_active_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(SyncJob).where(SyncJob.id == job_id))
    job = result.scalar_one_or_none()
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return job


class CronDriftCheckResponse(BaseModel):
    host_id: int
    status: str
    jobs_to_add: list[str]
    jobs_to_remove: list[str]
    jobs_to_update: list[str]
    jobs_in_sync: list[str]
    error_message: str | None = None
    checked_at: str


class DriftSettingsRequest(BaseModel):
    drift_check_enabled: bool


@router.post("/hosts/{host_id}/drift-check", response_model=CronDriftCheckResponse)
async def check_cron_drift(
    host_id: int,
    _: User = Depends(current_active_user),
    db: AsyncSession = Depends(get_db),
):
    host_result = await db.execute(select(Host).where(Host.id == host_id))
    host = host_result.scalar_one_or_none()
    if not host:
        raise HTTPException(status_code=404, detail="Host not found")

    status_result = await db.execute(
        select(HostModuleStatus).where(
            HostModuleStatus.host_id == host_id,
            HostModuleStatus.module_type == "cron",
        )
    )
    hms = status_result.scalar_one_or_none()
    if hms is None:
        hms = HostModuleStatus(host_id=host_id, module_type="cron")
        db.add(hms)

    checked_at = datetime.now(UTC)

    try:
        if not host.ssh_key_id:
            raise HTTPException(status_code=400, detail="Host has no SSH key assigned")

        key_result = await db.execute(select(SSHKey).where(SSHKey.id == host.ssh_key_id))
        ssh_key = key_result.scalar_one_or_none()
        if not ssh_key:
            raise ValueError("SSH key not found")

        private_key_pem = decrypt_ssh_key(ssh_key.encrypted_private_key, get_master_key())

        effective = await get_effective_cron_jobs(host_id, db)
        desired_dicts = [
            {
                "name": j.name,
                "user": j.user,
                "schedule": j.schedule,
                "command": j.command,
            }
            for j in effective
        ]

        users = list({j.user for j in effective})

        actual = await collect_cron_jobs(host.ip_address, host.ssh_port, private_key_pem, users)

        cron_diff = diff_cron_jobs(desired_dicts, actual)

        drifted = bool(
            cron_diff.jobs_to_add or cron_diff.jobs_to_remove or cron_diff.jobs_to_update
        )

        hms.sync_status = "drifted" if drifted else "in_sync"
        hms.last_drift_check_at = checked_at

        from app.api.host_state import refresh_host_sync_status

        await refresh_host_sync_status(host, db)
        await db.commit()

        return CronDriftCheckResponse(
            host_id=host_id,
            status=hms.sync_status,
            jobs_to_add=cron_diff.jobs_to_add,
            jobs_to_remove=cron_diff.jobs_to_remove,
            jobs_to_update=cron_diff.jobs_to_update,
            jobs_in_sync=cron_diff.jobs_in_sync,
            checked_at=checked_at.isoformat(),
        )

    except HTTPException:
        raise
    except Exception as e:
        hms.sync_status = "error"
        hms.last_drift_check_at = checked_at

        from app.api.host_state import refresh_host_sync_status

        await refresh_host_sync_status(host, db)
        await db.commit()

        return CronDriftCheckResponse(
            host_id=host_id,
            status="error",
            jobs_to_add=[],
            jobs_to_remove=[],
            jobs_to_update=[],
            jobs_in_sync=[],
            error_message=str(e),
            checked_at=checked_at.isoformat(),
        )


@router.put("/hosts/{host_id}/drift-settings")
async def update_cron_drift_settings(
    host_id: int,
    body: DriftSettingsRequest,
    _: User = Depends(current_active_user),
    db: AsyncSession = Depends(get_db),
):
    host_result = await db.execute(select(Host).where(Host.id == host_id))
    host = host_result.scalar_one_or_none()
    if not host:
        raise HTTPException(status_code=404, detail="Host not found")

    status_result = await db.execute(
        select(HostModuleStatus).where(
            HostModuleStatus.host_id == host_id,
            HostModuleStatus.module_type == "cron",
        )
    )
    hms = status_result.scalar_one_or_none()
    if hms is None:
        hms = HostModuleStatus(host_id=host_id, module_type="cron")
        db.add(hms)

    hms.drift_check_enabled = body.drift_check_enabled
    await db.commit()

    return {"drift_check_enabled": hms.drift_check_enabled}
