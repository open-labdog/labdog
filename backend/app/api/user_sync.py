from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.users import current_superuser
from app.crypto.encryption import decrypt_ssh_key
from app.crypto.key_management import get_master_key
from app.db import get_db
from app.models.host import Host
from app.models.host_module_status import HostModuleStatus
from app.models.ssh_key import SSHKey
from app.models.user import User
from app.user_mgmt.collector import collect_user_states, collect_group_states
from app.user_mgmt.diff import diff_users, diff_groups
from app.user_mgmt.merge import get_effective_users, get_effective_groups

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
            HostModuleStatus.module_type == "linux_user",
        )
    )
    hms = status_result.scalar_one_or_none()
    if hms is None:
        hms = HostModuleStatus(host_id=host_id, module_type="linux_user")
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
            user_diff.users_to_add
            or user_diff.users_to_remove
            or user_diff.users_to_update
        )
        groups_drifted = bool(
            group_diff.groups_to_add
            or group_diff.groups_to_remove
            or group_diff.groups_to_update
        )

        hms.sync_status = "drifted" if users_drifted or groups_drifted else "in_sync"
        hms.last_drift_check_at = checked_at
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
