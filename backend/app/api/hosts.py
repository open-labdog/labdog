from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import delete, insert, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_db
from app.models.host import Host, HostGroupMembership, FirewallBackend, SyncStatus
from app.models.user import User
from app.auth.users import current_active_user, current_superuser
from app.auth.rbac import get_user_accessible_group_ids
from app.schemas.hosts import HostCreate, HostUpdate, HostResponse

router = APIRouter(prefix="/hosts", tags=["hosts"])


@router.get("", response_model=list[HostResponse])
async def list_hosts(
    user: User = Depends(current_active_user),
    db: AsyncSession = Depends(get_db),
):
    accessible = await get_user_accessible_group_ids(user, db)
    if accessible is not None:
        # Get host IDs in accessible groups
        memberships = await db.execute(
            select(HostGroupMembership.c.host_id).where(
                HostGroupMembership.c.group_id.in_(accessible)
            )
        )
        host_ids = [r[0] for r in memberships.all()]
        result = await db.execute(select(Host).where(Host.id.in_(host_ids)))
    else:
        result = await db.execute(select(Host))
    return result.scalars().all()


@router.post("", response_model=HostResponse, status_code=201)
async def create_host(
    body: HostCreate,
    _: User = Depends(current_superuser),
    db: AsyncSession = Depends(get_db),
):
    host = Host(
        hostname=body.hostname,
        ip_address=body.ip_address,
        ssh_port=body.ssh_port,
        ssh_key_id=body.ssh_key_id,
    )
    db.add(host)
    await db.flush()  # get host.id

    if body.group_ids:
        await db.execute(
            insert(HostGroupMembership),
            [{"host_id": host.id, "group_id": gid} for gid in body.group_ids],
        )

    await db.commit()
    await db.refresh(host)
    return host


@router.get("/{host_id}", response_model=HostResponse)
async def get_host(
    host_id: int,
    user: User = Depends(current_active_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(Host).where(Host.id == host_id))
    host = result.scalar_one_or_none()
    if not host:
        raise HTTPException(status_code=404, detail="Host not found")

    # Check access
    accessible = await get_user_accessible_group_ids(user, db)
    if accessible is not None:
        memberships = await db.execute(
            select(HostGroupMembership).where(
                HostGroupMembership.c.host_id == host_id,
                HostGroupMembership.c.group_id.in_(accessible),
            )
        )
        if not memberships.first():
            raise HTTPException(status_code=403, detail="Not authorized")

    return host


@router.put("/{host_id}", response_model=HostResponse)
async def update_host(
    host_id: int,
    body: HostUpdate,
    _: User = Depends(current_superuser),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(Host).where(Host.id == host_id))
    host = result.scalar_one_or_none()
    if not host:
        raise HTTPException(status_code=404, detail="Host not found")

    update_data = body.model_dump(exclude_none=True)
    group_ids = update_data.pop("group_ids", None)

    for field, value in update_data.items():
        setattr(host, field, value)

    if group_ids is not None:
        await db.execute(
            delete(HostGroupMembership).where(HostGroupMembership.c.host_id == host_id)
        )
        if group_ids:
            await db.execute(
                insert(HostGroupMembership),
                [{"host_id": host_id, "group_id": gid} for gid in group_ids],
            )

    await db.commit()
    await db.refresh(host)
    return host


@router.delete("/{host_id}", status_code=204)
async def delete_host(
    host_id: int,
    _: User = Depends(current_superuser),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(Host).where(Host.id == host_id))
    host = result.scalar_one_or_none()
    if not host:
        raise HTTPException(status_code=404, detail="Host not found")

    await db.delete(host)
    await db.commit()


@router.post("/{host_id}/detect-firewall", response_model=HostResponse)
async def detect_firewall(
    host_id: int,
    _: User = Depends(current_superuser),
    db: AsyncSession = Depends(get_db),
):
    """Detect firewall backend on host via Ansible. Stub for now — returns current state."""
    result = await db.execute(select(Host).where(Host.id == host_id))
    host = result.scalar_one_or_none()
    if not host:
        raise HTTPException(status_code=404, detail="Host not found")

    # Real detection implemented in T16 (Ansible integration)
    # For now, return current state unchanged
    return host
