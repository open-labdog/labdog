from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.users import current_active_user
from app.crypto.encryption import decrypt_ssh_key
from app.crypto.key_management import get_master_key
from app.db import get_db
from app.hosts_mgmt.collector import collect_hosts_file
from app.hosts_mgmt.diff import compute_hosts_diff
from app.hosts_mgmt.merge import get_effective_hosts_entries
from app.models.host import Host
from app.models.host_module_status import HostModuleStatus
from app.models.ssh_key import SSHKey
from app.models.user import User

router = APIRouter(prefix="/hosts-mgmt", tags=["hosts-drift"])


# -- Schemas ------------------------------------------------------------------


class HostsDriftResponse(BaseModel):
    host_id: int
    status: str  # "in_sync", "out_of_sync", "error"
    has_changes: bool
    entries_to_add: list[dict]
    entries_to_remove: list[dict]
    entries_to_update: list[dict]
    entries_in_sync: list[str]
    error_message: str | None = None
    checked_at: str


class DriftSettingsRequest(BaseModel):
    drift_check_enabled: bool


# -- Endpoints ----------------------------------------------------------------


@router.post("/hosts/{host_id}/drift-check", response_model=HostsDriftResponse)
async def check_hosts_drift(
    host_id: int,
    user: User = Depends(current_active_user),
    db: AsyncSession = Depends(get_db),
):
    """Manual drift check: collect current /etc/hosts and compare to desired."""
    # Verify host exists
    host_result = await db.execute(select(Host).where(Host.id == host_id))
    host = host_result.scalar_one_or_none()
    if not host:
        raise HTTPException(status_code=404, detail="Host not found")

    # Upsert HostModuleStatus
    status_result = await db.execute(
        select(HostModuleStatus).where(
            HostModuleStatus.host_id == host_id,
            HostModuleStatus.module_type == "hosts_file",
        )
    )
    hms = status_result.scalar_one_or_none()
    if hms is None:
        hms = HostModuleStatus(host_id=host_id, module_type="hosts_file")
        db.add(hms)

    checked_at = datetime.now(UTC)

    try:
        # Need SSH key for remote collection
        if not host.ssh_key_id:
            raise ValueError("Host has no SSH key configured")

        key_result = await db.execute(select(SSHKey).where(SSHKey.id == host.ssh_key_id))
        ssh_key = key_result.scalar_one_or_none()
        if not ssh_key:
            raise ValueError("SSH key not found")

        private_key_pem = decrypt_ssh_key(ssh_key.encrypted_private_key, get_master_key())

        # Get desired hosts entries config
        desired = await get_effective_hosts_entries(host_id, db)

        # Collect current /etc/hosts from host
        current = await collect_hosts_file(host.ip_address, host.ssh_port, private_key_pem)

        # Compute diff
        diff = compute_hosts_diff(current, desired)

        hms.sync_status = "in_sync" if not diff.has_changes else "out_of_sync"
        hms.last_drift_check_at = checked_at

        from app.api.host_state import refresh_host_sync_status

        await refresh_host_sync_status(host, db)
        await db.commit()

        return HostsDriftResponse(
            host_id=host_id,
            status=hms.sync_status,
            has_changes=diff.has_changes,
            entries_to_add=[
                {
                    "ip_address": e.ip_address,
                    "hostname": e.hostname,
                    "aliases": e.aliases,
                    "reason": e.reason,
                }
                for e in diff.entries_to_add
            ],
            entries_to_remove=[
                {
                    "ip_address": e.ip_address,
                    "hostname": e.hostname,
                    "aliases": e.aliases,
                    "reason": e.reason,
                }
                for e in diff.entries_to_remove
            ],
            entries_to_update=[
                {
                    "ip_address": e.ip_address,
                    "hostname": e.hostname,
                    "aliases": e.aliases,
                    "reason": e.reason,
                }
                for e in diff.entries_to_update
            ],
            entries_in_sync=diff.entries_in_sync,
            checked_at=checked_at.isoformat(),
        )

    except Exception as e:
        hms.sync_status = "error"
        hms.last_drift_check_at = checked_at
        await db.commit()

        return HostsDriftResponse(
            host_id=host_id,
            status="error",
            has_changes=False,
            entries_to_add=[],
            entries_to_remove=[],
            entries_to_update=[],
            entries_in_sync=[],
            error_message=str(e),
            checked_at=checked_at.isoformat(),
        )


@router.put("/hosts/{host_id}/drift-settings")
async def update_hosts_drift_settings(
    host_id: int,
    body: DriftSettingsRequest,
    user: User = Depends(current_active_user),
    db: AsyncSession = Depends(get_db),
):
    """Toggle hosts file drift checking for a host."""
    # Verify host exists
    host_result = await db.execute(select(Host).where(Host.id == host_id))
    host = host_result.scalar_one_or_none()
    if not host:
        raise HTTPException(status_code=404, detail="Host not found")

    # Upsert HostModuleStatus
    status_result = await db.execute(
        select(HostModuleStatus).where(
            HostModuleStatus.host_id == host_id,
            HostModuleStatus.module_type == "hosts_file",
        )
    )
    hms = status_result.scalar_one_or_none()
    if hms is None:
        hms = HostModuleStatus(host_id=host_id, module_type="hosts_file")
        db.add(hms)

    hms.drift_check_enabled = body.drift_check_enabled
    await db.commit()

    return {"drift_check_enabled": hms.drift_check_enabled}
