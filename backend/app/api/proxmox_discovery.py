from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.users import current_superuser
from app.db import get_db
from app.models.user import User
from app.proxmox.discovery import discover_all_vms, discover_vm_by_ip
from app.proxmox.schemas import VMMappingResponse
from app.proxmox.vm_mapping import VMMapping

router = APIRouter(prefix="/proxmox", tags=["proxmox"])


@router.post("/discover", response_model=list[VMMappingResponse])
async def trigger_full_discovery(
    _: User = Depends(current_superuser),
    db: AsyncSession = Depends(get_db),
):
    """Scan all Proxmox nodes and update VM mappings for every known host.

    Stale mappings (hosts whose VM is no longer found) are removed.
    Returns the list of current mappings after the scan.
    """
    return await discover_all_vms(db)


@router.get("/hosts/{host_id}/vm-mapping", response_model=VMMappingResponse)
async def get_host_vm_mapping(
    host_id: int,
    _: User = Depends(current_superuser),
    db: AsyncSession = Depends(get_db),
):
    """Return the cached VM mapping for a host.

    Raises 404 if no mapping has been discovered yet.
    """
    result = await db.execute(select(VMMapping).where(VMMapping.host_id == host_id))
    mapping = result.scalar_one_or_none()
    if mapping is None:
        raise HTTPException(status_code=404, detail="No VM mapping found for this host")
    return mapping


@router.post("/hosts/{host_id}/discover", response_model=VMMappingResponse)
async def discover_host_vm_mapping(
    host_id: int,
    _: User = Depends(current_superuser),
    db: AsyncSession = Depends(get_db),
):
    """Trigger VM discovery for a single host by scanning its IP address.

    Raises 404 if the host is not found or no VM claims its IP.
    """
    from app.models.host import Host

    host_result = await db.execute(select(Host).where(Host.id == host_id))
    host = host_result.scalar_one_or_none()
    if host is None:
        raise HTTPException(status_code=404, detail="Host not found")

    mapping = await discover_vm_by_ip(host.ip_address, db)
    if mapping is None:
        raise HTTPException(
            status_code=404,
            detail="No Proxmox VM found matching this host's IP address",
        )
    await db.commit()
    return mapping
