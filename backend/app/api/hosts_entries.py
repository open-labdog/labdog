from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import PlainTextResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit.logger import log_action
from app.auth.users import current_active_user
from app.db import get_db
from app.hosts_mgmt.merge import get_effective_hosts_entries, render_hosts_file
from app.hosts_mgmt.models import HostsEntry
from app.hosts_mgmt.schemas import (
    EffectiveHostsEntryResponse,
    HostsEntryCreate,
    HostsEntryResponse,
    HostsEntryUpdate,
)
from app.models.host import Host
from app.models.host_group import HostGroup
from app.models.user import User

router = APIRouter(tags=["hosts-entries"])


async def _validate_host_ref(db: AsyncSession, host_ref_id: int | None) -> None:
    if host_ref_id is None:
        return
    row = await db.execute(select(Host.id).where(Host.id == host_ref_id))
    if row.scalar_one_or_none() is None:
        raise HTTPException(status_code=400, detail=f"Referenced host {host_ref_id} not found")


async def _apply_entry_update(db: AsyncSession, entry: HostsEntry, body: HostsEntryUpdate) -> None:
    """Apply a HostsEntryUpdate, handling literal↔ref side swaps."""
    data = body.model_dump(exclude_unset=True)
    if "host_ref_id" in data and data["host_ref_id"] is not None:
        data.setdefault("ip_address", None)
        data.setdefault("hostname", None)
    if "ip_address" in data and data.get("ip_address"):
        data.setdefault("host_ref_id", None)
    if "hostname" in data and data.get("hostname"):
        data.setdefault("host_ref_id", None)
    await _validate_host_ref(db, data.get("host_ref_id"))
    for field, value in data.items():
        setattr(entry, field, value)


# ---------------------------------------------------------------------------
# Group-level CRUD
# ---------------------------------------------------------------------------


@router.get(
    "/groups/{group_id}/hosts-entries",
    response_model=list[HostsEntryResponse],
)
async def list_group_hosts_entries(
    group_id: int,
    _: User = Depends(current_active_user),
    db: AsyncSession = Depends(get_db),
):
    group = await db.scalar(select(HostGroup).where(HostGroup.id == group_id))
    if not group:
        raise HTTPException(status_code=404, detail="Group not found")

    result = await db.execute(
        select(HostsEntry)
        .where(HostsEntry.group_id == group_id)
        .order_by(HostsEntry.priority.desc(), HostsEntry.id)
    )
    return result.scalars().all()


@router.post(
    "/groups/{group_id}/hosts-entries",
    response_model=HostsEntryResponse,
    status_code=201,
)
async def create_group_hosts_entry(
    group_id: int,
    body: HostsEntryCreate,
    user: User = Depends(current_active_user),
    db: AsyncSession = Depends(get_db),
):
    group = await db.scalar(select(HostGroup).where(HostGroup.id == group_id))
    if not group:
        raise HTTPException(status_code=404, detail="Group not found")

    await _validate_host_ref(db, body.host_ref_id)
    entry = HostsEntry(group_id=group_id, **body.model_dump())
    db.add(entry)
    await db.flush()

    await log_action(
        db=db,
        action="create",
        entity_type="hosts_entry",
        entity_id=entry.id,
        user_id=user.id,
        after_state={
            "ip_address": entry.ip_address,
            "hostname": entry.hostname,
            "host_ref_id": entry.host_ref_id,
        },
    )
    await db.commit()
    await db.refresh(entry)
    return entry


@router.put(
    "/groups/{group_id}/hosts-entries/{entry_id}",
    response_model=HostsEntryResponse,
)
async def update_group_hosts_entry(
    group_id: int,
    entry_id: int,
    body: HostsEntryUpdate,
    user: User = Depends(current_active_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(HostsEntry).where(
            HostsEntry.id == entry_id,
            HostsEntry.group_id == group_id,
        )
    )
    entry = result.scalar_one_or_none()
    if not entry:
        raise HTTPException(status_code=404, detail="Hosts entry not found")

    before = {
        "ip_address": entry.ip_address,
        "hostname": entry.hostname,
        "host_ref_id": entry.host_ref_id,
    }

    await _apply_entry_update(db, entry, body)

    await db.flush()

    await log_action(
        db=db,
        action="update",
        entity_type="hosts_entry",
        entity_id=entry.id,
        user_id=user.id,
        before_state=before,
        after_state={
            "ip_address": entry.ip_address,
            "hostname": entry.hostname,
            "host_ref_id": entry.host_ref_id,
        },
    )
    await db.commit()
    await db.refresh(entry)
    return entry


@router.delete("/groups/{group_id}/hosts-entries/{entry_id}", status_code=204)
async def delete_group_hosts_entry(
    group_id: int,
    entry_id: int,
    user: User = Depends(current_active_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(HostsEntry).where(
            HostsEntry.id == entry_id,
            HostsEntry.group_id == group_id,
        )
    )
    entry = result.scalar_one_or_none()
    if not entry:
        raise HTTPException(status_code=404, detail="Hosts entry not found")

    if entry.is_system:
        raise HTTPException(status_code=403, detail="Cannot delete system hosts entry")

    before = {"ip_address": entry.ip_address, "hostname": entry.hostname}
    entry_id_for_log = entry.id

    await db.delete(entry)
    await db.flush()

    await log_action(
        db=db,
        action="delete",
        entity_type="hosts_entry",
        entity_id=entry_id_for_log,
        user_id=user.id,
        before_state=before,
    )
    await db.commit()


# ---------------------------------------------------------------------------
# Host-level overrides
# ---------------------------------------------------------------------------


@router.get(
    "/hosts/{host_id}/hosts-entries",
    response_model=list[HostsEntryResponse],
)
async def list_host_hosts_entries(
    host_id: int,
    _: User = Depends(current_active_user),
    db: AsyncSession = Depends(get_db),
):
    host = await db.scalar(select(Host).where(Host.id == host_id))
    if not host:
        raise HTTPException(status_code=404, detail="Host not found")

    result = await db.execute(
        select(HostsEntry)
        .where(HostsEntry.host_id == host_id)
        .order_by(HostsEntry.priority.desc(), HostsEntry.id)
    )
    return result.scalars().all()


@router.post(
    "/hosts/{host_id}/hosts-entries",
    response_model=HostsEntryResponse,
    status_code=201,
)
async def create_host_hosts_entry(
    host_id: int,
    body: HostsEntryCreate,
    user: User = Depends(current_active_user),
    db: AsyncSession = Depends(get_db),
):
    host = await db.scalar(select(Host).where(Host.id == host_id))
    if not host:
        raise HTTPException(status_code=404, detail="Host not found")

    await _validate_host_ref(db, body.host_ref_id)
    entry = HostsEntry(host_id=host_id, **body.model_dump())
    db.add(entry)
    await db.flush()

    await log_action(
        db=db,
        action="create",
        entity_type="hosts_entry",
        entity_id=entry.id,
        user_id=user.id,
        after_state={
            "ip_address": entry.ip_address,
            "hostname": entry.hostname,
            "host_ref_id": entry.host_ref_id,
        },
    )
    await db.commit()
    await db.refresh(entry)
    return entry


@router.put(
    "/hosts/{host_id}/hosts-entries/{entry_id}",
    response_model=HostsEntryResponse,
)
async def update_host_hosts_entry(
    host_id: int,
    entry_id: int,
    body: HostsEntryUpdate,
    user: User = Depends(current_active_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(HostsEntry).where(
            HostsEntry.id == entry_id,
            HostsEntry.host_id == host_id,
        )
    )
    entry = result.scalar_one_or_none()
    if not entry:
        raise HTTPException(status_code=404, detail="Hosts entry not found")

    before = {
        "ip_address": entry.ip_address,
        "hostname": entry.hostname,
        "host_ref_id": entry.host_ref_id,
    }

    await _apply_entry_update(db, entry, body)

    await db.flush()

    await log_action(
        db=db,
        action="update",
        entity_type="hosts_entry",
        entity_id=entry.id,
        user_id=user.id,
        before_state=before,
        after_state={
            "ip_address": entry.ip_address,
            "hostname": entry.hostname,
            "host_ref_id": entry.host_ref_id,
        },
    )
    await db.commit()
    await db.refresh(entry)
    return entry


@router.delete("/hosts/{host_id}/hosts-entries/{entry_id}", status_code=204)
async def delete_host_hosts_entry(
    host_id: int,
    entry_id: int,
    user: User = Depends(current_active_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(HostsEntry).where(
            HostsEntry.id == entry_id,
            HostsEntry.host_id == host_id,
        )
    )
    entry = result.scalar_one_or_none()
    if not entry:
        raise HTTPException(status_code=404, detail="Hosts entry not found")

    if entry.is_system:
        raise HTTPException(status_code=403, detail="Cannot delete system hosts entry")

    before = {"ip_address": entry.ip_address, "hostname": entry.hostname}
    entry_id_for_log = entry.id

    await db.delete(entry)
    await db.flush()

    await log_action(
        db=db,
        action="delete",
        entity_type="hosts_entry",
        entity_id=entry_id_for_log,
        user_id=user.id,
        before_state=before,
    )
    await db.commit()


# ---------------------------------------------------------------------------
# Effective config (merged group + host overrides)
# ---------------------------------------------------------------------------


@router.get(
    "/hosts/{host_id}/effective-hosts-entries",
    response_model=list[EffectiveHostsEntryResponse],
)
async def effective_hosts_entries(
    host_id: int,
    _: User = Depends(current_active_user),
    db: AsyncSession = Depends(get_db),
):
    host = await db.scalar(select(Host).where(Host.id == host_id))
    if not host:
        raise HTTPException(status_code=404, detail="Host not found")

    return await get_effective_hosts_entries(host_id, db)


# ---------------------------------------------------------------------------
# File preview (rendered /etc/hosts)
# ---------------------------------------------------------------------------


@router.get(
    "/hosts/{host_id}/hosts-file-preview",
    response_class=PlainTextResponse,
)
async def hosts_file_preview(
    host_id: int,
    _: User = Depends(current_active_user),
    db: AsyncSession = Depends(get_db),
):
    host = await db.scalar(select(Host).where(Host.id == host_id))
    if not host:
        raise HTTPException(status_code=404, detail="Host not found")

    entries = await get_effective_hosts_entries(host_id, db)
    content = render_hosts_file(entries)
    return PlainTextResponse(content=content)
