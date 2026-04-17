from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.users import current_active_user
from app.crypto.encryption import decrypt_ssh_key
from app.crypto.key_management import get_master_key
from app.db import get_db
from app.models.host import Host
from app.models.host_module_status import HostModuleStatus
from app.models.ssh_key import SSHKey
from app.models.user import User
from app.services.collector import collect_service_states
from app.services.diff import compute_service_diff
from app.services.merge import get_effective_services

router = APIRouter(prefix="/services", tags=["service-drift"])


# -- Schemas ------------------------------------------------------------------


class ServiceDriftResponse(BaseModel):
    host_id: int
    status: str  # "in_sync", "out_of_sync", "error"
    has_changes: bool
    services_to_update: list[dict]
    services_in_sync: list[str]
    services_with_errors: list[str]
    error_message: str | None = None
    checked_at: str


class DriftSettingsRequest(BaseModel):
    drift_check_enabled: bool


# -- Endpoints ----------------------------------------------------------------


@router.post("/hosts/{host_id}/drift-check", response_model=ServiceDriftResponse)
async def check_service_drift(
    host_id: int,
    user: User = Depends(current_active_user),
    db: AsyncSession = Depends(get_db),
):
    """Manual drift check: collect current service states and compare to desired."""
    # Verify host exists
    host_result = await db.execute(select(Host).where(Host.id == host_id))
    host = host_result.scalar_one_or_none()
    if not host:
        raise HTTPException(status_code=404, detail="Host not found")

    # Upsert HostModuleStatus
    status_result = await db.execute(
        select(HostModuleStatus).where(
            HostModuleStatus.host_id == host_id,
            HostModuleStatus.module_type == "service",
        )
    )
    hms = status_result.scalar_one_or_none()
    if hms is None:
        hms = HostModuleStatus(host_id=host_id, module_type="service")
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

        # Get desired service config
        desired = await get_effective_services(host_id, db)
        service_names = [s.service_name for s in desired]

        # Collect current state from host
        current = await collect_service_states(
            host.ip_address, host.ssh_port, private_key_pem, service_names
        )

        # Compute diff
        diff = compute_service_diff(current, desired)

        hms.sync_status = "in_sync" if not diff.has_changes else "out_of_sync"
        hms.last_drift_check_at = checked_at

        from app.api.host_state import refresh_host_sync_status

        await refresh_host_sync_status(host, db)
        await db.commit()

        return ServiceDriftResponse(
            host_id=host_id,
            status=hms.sync_status,
            has_changes=diff.has_changes,
            services_to_update=[
                {
                    "service_name": item.service_name,
                    "desired_state": item.desired_state,
                    "desired_enabled": item.desired_enabled,
                    "actual_state": item.actual_state,
                    "actual_enabled": item.actual_enabled,
                    "reason": item.reason,
                }
                for item in diff.services_to_update
            ],
            services_in_sync=diff.services_in_sync,
            services_with_errors=diff.services_with_errors,
            checked_at=checked_at.isoformat(),
        )

    except Exception as e:
        hms.sync_status = "error"
        hms.last_drift_check_at = checked_at
        await db.commit()

        return ServiceDriftResponse(
            host_id=host_id,
            status="error",
            has_changes=False,
            services_to_update=[],
            services_in_sync=[],
            services_with_errors=[],
            error_message=str(e),
            checked_at=checked_at.isoformat(),
        )


@router.put("/hosts/{host_id}/drift-settings")
async def update_drift_settings(
    host_id: int,
    body: DriftSettingsRequest,
    user: User = Depends(current_active_user),
    db: AsyncSession = Depends(get_db),
):
    """Toggle service drift checking for a host."""
    # Verify host exists
    host_result = await db.execute(select(Host).where(Host.id == host_id))
    host = host_result.scalar_one_or_none()
    if not host:
        raise HTTPException(status_code=404, detail="Host not found")

    # Upsert HostModuleStatus
    status_result = await db.execute(
        select(HostModuleStatus).where(
            HostModuleStatus.host_id == host_id,
            HostModuleStatus.module_type == "service",
        )
    )
    hms = status_result.scalar_one_or_none()
    if hms is None:
        hms = HostModuleStatus(host_id=host_id, module_type="service")
        db.add(hms)

    hms.drift_check_enabled = body.drift_check_enabled
    await db.commit()

    return {"drift_check_enabled": hms.drift_check_enabled}
