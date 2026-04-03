from datetime import datetime, timezone
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
from app.models.host_module_status import HostModuleStatus
from app.models.ssh_key import SSHKey
from app.models.sync_job import SyncJob
from app.models.user import User
from app.packages.collector import collect_package_states
from app.packages.diff import compute_diff
from app.packages.merge import get_effective_packages, get_effective_repos

router = APIRouter(prefix="/packages", tags=["package-sync"])


class PackageDiffEntry(BaseModel):
    package_name: str
    desired_state: str
    desired_version: Optional[str] = None
    actual_state: str
    actual_version: Optional[str] = None


class PackageDiffResponse(BaseModel):
    to_install: list[PackageDiffEntry]
    to_remove: list[PackageDiffEntry]
    to_upgrade: list[PackageDiffEntry]
    in_sync: list[PackageDiffEntry]


class PackageSyncPlan(BaseModel):
    host_id: int
    hostname: str
    has_changes: bool
    package_diff: PackageDiffResponse


def _entry_to_dict(entry) -> dict:
    return {
        "package_name": entry.package_name,
        "desired_state": entry.desired_state,
        "desired_version": entry.desired_version,
        "actual_state": entry.actual_state,
        "actual_version": entry.actual_version,
    }


@router.post("/hosts/{host_id}/plan", response_model=PackageSyncPlan)
async def plan_package_sync(
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

    effective_packages = await get_effective_packages(host_id, db)
    if not effective_packages:
        raise HTTPException(
            status_code=400, detail="No package rules defined for this host"
        )

    key_result = await db.execute(
        select(SSHKey).where(SSHKey.id == host.ssh_key_id)
    )
    ssh_key = key_result.scalar_one()
    private_key_pem = decrypt_ssh_key(
        ssh_key.encrypted_private_key, get_master_key()
    )

    desired_dicts = [p.model_dump() for p in effective_packages]
    package_names = [p.package_name for p in effective_packages]

    actual = await collect_package_states(
        host.ip_address, host.ssh_port, private_key_pem, package_names
    )

    diff = compute_diff(desired_dicts, actual)

    return PackageSyncPlan(
        host_id=host_id,
        hostname=host.hostname,
        has_changes=diff.has_drift,
        package_diff=PackageDiffResponse(
            to_install=[_entry_to_dict(e) for e in diff.to_install],
            to_remove=[_entry_to_dict(e) for e in diff.to_remove],
            to_upgrade=[_entry_to_dict(e) for e in diff.to_upgrade],
            in_sync=[_entry_to_dict(e) for e in diff.in_sync],
        ),
    )


@router.post(
    "/hosts/{host_id}/sync", response_model=SyncJobResponse, status_code=201
)
async def trigger_package_sync(
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
            SyncJob.module_type == "package",
            SyncJob.status.in_(["pending", "running"]),
        )
    )
    if running.scalar_one_or_none():
        raise HTTPException(
            status_code=409,
            detail="Package sync already in progress for this host",
        )

    effective_packages = await get_effective_packages(host_id, db)
    if not effective_packages:
        raise HTTPException(
            status_code=400, detail="No package rules defined for this host"
        )

    job = SyncJob(
        host_id=host_id,
        module_type="package",
        status="pending",
        triggered_by_user_id=user.id,
    )
    db.add(job)
    await db.commit()
    await db.refresh(job)

    from app.tasks.package_sync import run_package_sync

    run_package_sync.delay(job_id=job.id, host_id=host_id)

    return job


@router.post("/groups/{group_id}/sync", status_code=201)
async def trigger_group_package_sync(
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
    from app.tasks.package_sync import run_package_sync

    for hid in host_ids:
        running = await db.execute(
            select(SyncJob).where(
                SyncJob.host_id == hid,
                SyncJob.module_type == "package",
                SyncJob.status.in_(["pending", "running"]),
            )
        )
        if running.scalar_one_or_none():
            continue

        job = SyncJob(
            host_id=hid,
            group_id=group_id,
            module_type="package",
            status="pending",
            triggered_by_user_id=user.id,
        )
        db.add(job)
        await db.flush()
        run_package_sync.delay(job_id=job.id, host_id=hid)
        jobs.append(job)

    await db.commit()
    return {"triggered": len(jobs), "skipped": len(host_ids) - len(jobs)}


@router.get("/jobs/{job_id}", response_model=SyncJobResponse)
async def get_package_sync_job(
    job_id: int,
    _: User = Depends(current_superuser),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(SyncJob).where(SyncJob.id == job_id))
    job = result.scalar_one_or_none()
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return job


class PackageDriftCheckResponse(BaseModel):
    host_id: int
    status: str
    to_install: list[PackageDiffEntry]
    to_remove: list[PackageDiffEntry]
    to_upgrade: list[PackageDiffEntry]
    in_sync: list[PackageDiffEntry]
    error_message: Optional[str] = None
    checked_at: str


class DriftSettingsRequest(BaseModel):
    drift_check_enabled: bool


@router.post("/hosts/{host_id}/drift-check", response_model=PackageDriftCheckResponse)
async def check_package_drift(
    host_id: int,
    _: User = Depends(current_superuser),
    db: AsyncSession = Depends(get_db),
):
    host_result = await db.execute(select(Host).where(Host.id == host_id))
    host = host_result.scalar_one_or_none()
    if not host:
        raise HTTPException(status_code=404, detail="Host not found")

    status_result = await db.execute(
        select(HostModuleStatus).where(
            HostModuleStatus.host_id == host_id,
            HostModuleStatus.module_type == "package",
        )
    )
    hms = status_result.scalar_one_or_none()
    if hms is None:
        hms = HostModuleStatus(host_id=host_id, module_type="package")
        db.add(hms)

    checked_at = datetime.now(timezone.utc)

    try:
        if not host.ssh_key_id:
            raise HTTPException(
                status_code=400, detail="Host has no SSH key assigned"
            )

        key_result = await db.execute(
            select(SSHKey).where(SSHKey.id == host.ssh_key_id)
        )
        ssh_key = key_result.scalar_one_or_none()
        if not ssh_key:
            raise ValueError("SSH key not found")

        private_key_pem = decrypt_ssh_key(
            ssh_key.encrypted_private_key, get_master_key()
        )

        effective = await get_effective_packages(host_id, db)
        desired_dicts = [p.model_dump() for p in effective]
        package_names = [p.package_name for p in effective]

        actual = await collect_package_states(
            host.ip_address, host.ssh_port, private_key_pem, package_names
        )

        pkg_diff = compute_diff(desired_dicts, actual)

        hms.sync_status = "drifted" if pkg_diff.has_drift else "in_sync"
        hms.last_drift_check_at = checked_at

        from app.api.host_state import refresh_host_sync_status
        await refresh_host_sync_status(host, db)
        await db.commit()

        return PackageDriftCheckResponse(
            host_id=host_id,
            status=hms.sync_status,
            to_install=[_entry_to_dict(e) for e in pkg_diff.to_install],
            to_remove=[_entry_to_dict(e) for e in pkg_diff.to_remove],
            to_upgrade=[_entry_to_dict(e) for e in pkg_diff.to_upgrade],
            in_sync=[_entry_to_dict(e) for e in pkg_diff.in_sync],
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

        return PackageDriftCheckResponse(
            host_id=host_id,
            status="error",
            to_install=[],
            to_remove=[],
            to_upgrade=[],
            in_sync=[],
            error_message=str(e),
            checked_at=checked_at.isoformat(),
        )


@router.put("/hosts/{host_id}/drift-settings")
async def update_package_drift_settings(
    host_id: int,
    body: DriftSettingsRequest,
    _: User = Depends(current_superuser),
    db: AsyncSession = Depends(get_db),
):
    host_result = await db.execute(select(Host).where(Host.id == host_id))
    host = host_result.scalar_one_or_none()
    if not host:
        raise HTTPException(status_code=404, detail="Host not found")

    status_result = await db.execute(
        select(HostModuleStatus).where(
            HostModuleStatus.host_id == host_id,
            HostModuleStatus.module_type == "package",
        )
    )
    hms = status_result.scalar_one_or_none()
    if hms is None:
        hms = HostModuleStatus(host_id=host_id, module_type="package")
        db.add(hms)

    hms.drift_check_enabled = body.drift_check_enabled
    await db.commit()

    return {"drift_check_enabled": hms.drift_check_enabled}
