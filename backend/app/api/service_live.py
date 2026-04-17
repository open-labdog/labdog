"""Live service inventory and ad-hoc command endpoints."""

import asyncio
import shlex

import asyncssh
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit.logger import log_action
from app.auth.users import current_active_user
from app.crypto import decrypt_ssh_key, get_master_key
from app.db import get_db
from app.models.host import Host
from app.models.ssh_key import SSHKey
from app.models.user import User
from app.services.collector import execute_service_command, list_all_services
from app.services.constants import PROTECTED_SERVICES, is_system_service
from app.services.live_schemas import (
    ServiceCommandRequest,
    ServiceCommandResponse,
    ServiceInventoryItem,
)
from app.services.merge import get_effective_services
from app.ssh_utils import ssh_connect

router = APIRouter(prefix="/services", tags=["service-live"])


@router.get("/hosts/{host_id}/inventory", response_model=list[ServiceInventoryItem])
async def get_service_inventory(
    host_id: int,
    user: User = Depends(current_active_user),
    db: AsyncSession = Depends(get_db),
):
    """List all systemd services on a host via SSH."""
    host_result = await db.execute(select(Host).where(Host.id == host_id))
    host = host_result.scalar_one_or_none()
    if not host:
        raise HTTPException(status_code=404, detail="Host not found")

    if not host.ssh_key_id:
        raise HTTPException(status_code=400, detail="Host has no SSH key assigned")

    # Decrypt SSH key
    key_result = await db.execute(select(SSHKey).where(SSHKey.id == host.ssh_key_id))
    ssh_key = key_result.scalar_one()
    master_key = get_master_key()
    private_key_pem = decrypt_ssh_key(ssh_key.encrypted_private_key, master_key)

    # Fetch inventory via SSH
    raw_services = await list_all_services(
        host.ip_address, host.ssh_port, private_key_pem, ssh_user=host.ssh_user
    )

    # Get managed service names for this host
    effective = await get_effective_services(host_id, db)
    managed_names = {s.service_name for s in effective} if effective else set()

    # Build response with is_managed and is_protected flags
    return [
        ServiceInventoryItem(
            unit=svc["unit"],
            load_state=svc["load_state"],
            active_state=svc["active_state"],
            sub_state=svc["sub_state"],
            description=svc["description"],
            is_managed=svc["unit"] in managed_names,
            is_protected=svc["unit"] in PROTECTED_SERVICES,
            is_system=is_system_service(svc["unit"]),
        )
        for svc in raw_services
    ]


@router.post("/hosts/{host_id}/command", response_model=ServiceCommandResponse)
async def run_service_command(
    host_id: int,
    body: ServiceCommandRequest,
    user: User = Depends(current_active_user),
    db: AsyncSession = Depends(get_db),
):
    """Execute an ad-hoc start/stop/restart command on a service via SSH."""
    host_result = await db.execute(select(Host).where(Host.id == host_id))
    host = host_result.scalar_one_or_none()
    if not host:
        raise HTTPException(status_code=404, detail="Host not found")

    if not host.ssh_key_id:
        raise HTTPException(status_code=400, detail="Host has no SSH key assigned")

    # Decrypt SSH key
    key_result = await db.execute(select(SSHKey).where(SSHKey.id == host.ssh_key_id))
    ssh_key = key_result.scalar_one()
    master_key = get_master_key()
    private_key_pem = decrypt_ssh_key(ssh_key.encrypted_private_key, master_key)

    is_protected = body.service_name in PROTECTED_SERVICES

    # Execute command via SSH
    result = await execute_service_command(
        host.ip_address,
        host.ssh_port,
        private_key_pem,
        body.service_name,
        body.action,
        ssh_user=host.ssh_user,
    )

    # Audit log
    await log_action(
        db,
        action=f"service_{body.action}",
        entity_type="service_command",
        entity_id=host_id,
        user_id=user.id,
        after_state={
            "service_name": body.service_name,
            "action": body.action,
            "exit_code": result["exit_code"],
            "is_protected": is_protected,
        },
    )
    await db.commit()

    return ServiceCommandResponse(
        success=result["success"],
        exit_code=result["exit_code"],
        stdout=result["stdout"],
        stderr=result["stderr"],
        service_name=body.service_name,
        action=body.action,
        is_protected=is_protected,
    )


@router.get("/hosts/{host_id}/unit-file/{service_name}")
async def get_unit_file(
    host_id: int,
    service_name: str,
    user: User = Depends(current_active_user),
    db: AsyncSession = Depends(get_db),
):
    host_result = await db.execute(select(Host).where(Host.id == host_id))
    host = host_result.scalar_one_or_none()
    if not host:
        raise HTTPException(status_code=404, detail="Host not found")

    if not host.ssh_key_id:
        raise HTTPException(status_code=400, detail="Host has no SSH key assigned")

    key_result = await db.execute(select(SSHKey).where(SSHKey.id == host.ssh_key_id))
    ssh_key = key_result.scalar_one()
    master_key = get_master_key()
    private_key_pem = decrypt_ssh_key(ssh_key.encrypted_private_key, master_key)

    cmd = f"systemctl cat {shlex.quote(service_name)}"

    try:
        private_key = asyncssh.import_private_key(private_key_pem)

        async def _run() -> str | None:
            async with ssh_connect(
                host.ip_address,
                port=host.ssh_port,
                username=host.ssh_user,
                client_keys=[private_key],
            ) as conn:
                result = await conn.run(cmd, check=False)
                if result.exit_status != 0:
                    return None
                return result.stdout or ""

        content = await asyncio.wait_for(_run(), timeout=30.0)
    except Exception:
        content = None

    return {"content": content}
