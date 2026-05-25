from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.sync import SyncJobResponse
from app.auth.users import current_active_user
from app.crypto.encryption import decrypt_ssh_key
from app.crypto.key_management import get_master_key
from app.db import get_db
from app.models.host import Host, HostGroupMembership
from app.models.host_module_status import HostModuleStatus
from app.models.ssh_key import SSHKey
from app.models.sync_job import SyncJob
from app.models.user import User
from app.user_mgmt.collector import collect_group_states, collect_user_states
from app.user_mgmt.diff import diff_groups, diff_users
from app.user_mgmt.merge import get_effective_groups, get_effective_users

router = APIRouter(prefix="/linux-users", tags=["linux-user-sync"])


class UserDriftResponse(BaseModel):
    host_id: int
    status: str
    users_to_add: list[str]
    users_to_remove: list[str]
    users_to_update: list[str]
    users_in_sync: list[str]
    groups_to_add: list[str]
    groups_to_remove: list[str]
    groups_to_update: list[str]
    groups_in_sync: list[str]
    error_message: str | None = None
    checked_at: str


class DriftSettingsRequest(BaseModel):
    drift_check_enabled: bool


@router.post("/hosts/{host_id}/drift-check", response_model=UserDriftResponse)
async def check_user_drift(
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
            HostModuleStatus.module_type == "linux_user",
        )
    )
    hms = status_result.scalar_one_or_none()
    if hms is None:
        hms = HostModuleStatus(host_id=host_id, module_type="linux_user")
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

        desired_users = await get_effective_users(host_id, db)
        desired_groups = await get_effective_groups(host_id, db)

        desired_user_dicts = [u.model_dump() for u in desired_users]
        desired_group_dicts = [g.model_dump() for g in desired_groups]

        usernames = [u.username for u in desired_users]
        groupnames = [g.groupname for g in desired_groups]

        actual_users = await collect_user_states(
            host.ip_address, host.ssh_port, private_key_pem, usernames
        )
        actual_groups = await collect_group_states(
            host.ip_address, host.ssh_port, private_key_pem, groupnames
        )

        user_diff = diff_users(desired_user_dicts, actual_users)
        group_diff = diff_groups(desired_group_dicts, actual_groups)

        users_drifted = bool(
            user_diff.users_to_add or user_diff.users_to_remove or user_diff.users_to_update
        )
        groups_drifted = bool(
            group_diff.groups_to_add or group_diff.groups_to_remove or group_diff.groups_to_update
        )

        hms.sync_status = "drifted" if users_drifted or groups_drifted else "in_sync"
        hms.last_drift_check_at = checked_at

        from app.api.host_state import refresh_host_sync_status

        await refresh_host_sync_status(host, db)
        await db.commit()

        return UserDriftResponse(
            host_id=host_id,
            status=hms.sync_status,
            users_to_add=user_diff.users_to_add,
            users_to_remove=user_diff.users_to_remove,
            users_to_update=user_diff.users_to_update,
            users_in_sync=user_diff.users_in_sync,
            groups_to_add=group_diff.groups_to_add,
            groups_to_remove=group_diff.groups_to_remove,
            groups_to_update=group_diff.groups_to_update,
            groups_in_sync=group_diff.groups_in_sync,
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

        return UserDriftResponse(
            host_id=host_id,
            status="error",
            users_to_add=[],
            users_to_remove=[],
            users_to_update=[],
            users_in_sync=[],
            groups_to_add=[],
            groups_to_remove=[],
            groups_to_update=[],
            groups_in_sync=[],
            error_message=str(e),
            checked_at=checked_at.isoformat(),
        )


@router.put("/hosts/{host_id}/drift-settings")
async def update_user_drift_settings(
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
            HostModuleStatus.module_type == "linux_user",
        )
    )
    hms = status_result.scalar_one_or_none()
    if hms is None:
        hms = HostModuleStatus(host_id=host_id, module_type="linux_user")
        db.add(hms)

    hms.drift_check_enabled = body.drift_check_enabled
    await db.commit()

    return {"drift_check_enabled": hms.drift_check_enabled}


class UserDiffResponse(BaseModel):
    users_to_add: list[str]
    users_to_remove: list[str]
    users_to_update: list[str]
    users_in_sync: list[str]


class GroupDiffResponse(BaseModel):
    groups_to_add: list[str]
    groups_to_remove: list[str]
    groups_to_update: list[str]
    groups_in_sync: list[str]


class UserSyncPlan(BaseModel):
    host_id: int
    hostname: str
    has_changes: bool
    user_diff: UserDiffResponse
    group_diff: GroupDiffResponse


@router.post("/hosts/{host_id}/plan", response_model=UserSyncPlan)
async def plan_user_sync(
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

    effective_users = await get_effective_users(host_id, db)
    effective_groups = await get_effective_groups(host_id, db)

    if not effective_users and not effective_groups:
        raise HTTPException(status_code=400, detail="No user or group rules defined for this host")

    key_result = await db.execute(select(SSHKey).where(SSHKey.id == host.ssh_key_id))
    ssh_key = key_result.scalar_one()
    private_key_pem = decrypt_ssh_key(ssh_key.encrypted_private_key, get_master_key())

    desired_users = [u.model_dump() for u in effective_users]
    desired_groups = [g.model_dump() for g in effective_groups]

    actual_users = await collect_user_states(
        host.ip_address,
        host.ssh_port,
        private_key_pem,
        [u["username"] for u in desired_users],
    )
    actual_groups = await collect_group_states(
        host.ip_address,
        host.ssh_port,
        private_key_pem,
        [g["groupname"] for g in desired_groups],
    )

    u_diff = diff_users(desired_users, actual_users)
    g_diff = diff_groups(desired_groups, actual_groups)

    has_changes = bool(
        u_diff.users_to_add
        or u_diff.users_to_remove
        or u_diff.users_to_update
        or g_diff.groups_to_add
        or g_diff.groups_to_remove
        or g_diff.groups_to_update
    )

    return UserSyncPlan(
        host_id=host_id,
        hostname=host.hostname,
        has_changes=has_changes,
        user_diff=UserDiffResponse(
            users_to_add=u_diff.users_to_add,
            users_to_remove=u_diff.users_to_remove,
            users_to_update=u_diff.users_to_update,
            users_in_sync=u_diff.users_in_sync,
        ),
        group_diff=GroupDiffResponse(
            groups_to_add=g_diff.groups_to_add,
            groups_to_remove=g_diff.groups_to_remove,
            groups_to_update=g_diff.groups_to_update,
            groups_in_sync=g_diff.groups_in_sync,
        ),
    )


@router.post("/hosts/{host_id}/sync", response_model=SyncJobResponse, status_code=201)
async def trigger_user_sync(
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
            SyncJob.module_type == "linux_user",
            SyncJob.status.in_(["pending", "running"]),
        )
    )
    if running.scalar_one_or_none():
        raise HTTPException(
            status_code=409,
            detail="User sync already in progress for this host",
        )

    effective_users = await get_effective_users(host_id, db)
    effective_groups = await get_effective_groups(host_id, db)
    if not effective_users and not effective_groups:
        raise HTTPException(status_code=400, detail="No user or group rules defined for this host")

    job = SyncJob(
        host_id=host_id,
        module_type="linux_user",
        status="pending",
        triggered_by_user_id=user.id,
    )
    db.add(job)
    await db.commit()
    await db.refresh(job)

    from app.tasks.user_sync import user_sync_task

    user_sync_task.delay(job_id=job.id, host_id=host_id)

    return job


@router.post("/groups/{group_id}/sync", status_code=201)
async def trigger_group_user_sync(
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

    from app.tasks.user_sync import user_sync_task

    # BUG-37: dispatch after commit, not before — see app/api/sync.py.
    pending: list[tuple[int, int]] = []
    for hid in host_ids:
        running = await db.execute(
            select(SyncJob).where(
                SyncJob.host_id == hid,
                SyncJob.module_type == "linux_user",
                SyncJob.status.in_(["pending", "running"]),
            )
        )
        if running.scalar_one_or_none():
            continue

        job = SyncJob(
            host_id=hid,
            group_id=group_id,
            module_type="linux_user",
            status="pending",
            triggered_by_user_id=user.id,
        )
        db.add(job)
        await db.flush()
        pending.append((job.id, hid))

    await db.commit()
    for job_id, hid in pending:
        user_sync_task.delay(job_id=job_id, host_id=hid)
    return {"triggered": len(pending), "skipped": len(host_ids) - len(pending)}


@router.get("/jobs/{job_id}", response_model=SyncJobResponse)
async def get_user_sync_job(
    job_id: int,
    _: User = Depends(current_active_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(SyncJob).where(SyncJob.id == job_id))
    job = result.scalar_one_or_none()
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return job
