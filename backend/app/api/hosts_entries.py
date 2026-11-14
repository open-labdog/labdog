from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import PlainTextResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api._gitops_lock import check_gitops_lock
from app.api._scoped_rules import (
    RuleAudit,
    commit_created,
    commit_updated,
    delete_and_commit,
    require_group,
    require_host,
    scoped_row_or_404,
)
from app.auth.permissions import require
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
from app.models.user import User

router = APIRouter(tags=["hosts-entries"])

_NOT_FOUND = "Hosts entry not found"


def _audit_state(entry: HostsEntry) -> dict:
    return {
        "ip_address": entry.ip_address,
        "hostname": entry.hostname,
        "host_ref_id": entry.host_ref_id,
    }


def _deleted_state(entry: HostsEntry) -> dict:
    return {"ip_address": entry.ip_address, "hostname": entry.hostname}


_AUDIT = RuleAudit("hosts_entry", _audit_state, deleted_state=_deleted_state)


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
    _: User = Depends(require("hosts.view")),
    db: AsyncSession = Depends(get_db),
):
    await require_group(db, group_id)

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
    user: User = Depends(require("config.edit")),
    db: AsyncSession = Depends(get_db),
):
    await check_gitops_lock(group_id, db)
    await require_group(db, group_id)

    await _validate_host_ref(db, body.host_ref_id)
    entry = HostsEntry(group_id=group_id, **body.model_dump())
    db.add(entry)
    await db.flush()
    return await commit_created(db, entry, _AUDIT, user)


@router.put(
    "/groups/{group_id}/hosts-entries/{entry_id}",
    response_model=HostsEntryResponse,
)
async def update_group_hosts_entry(
    group_id: int,
    entry_id: int,
    body: HostsEntryUpdate,
    user: User = Depends(require("config.edit")),
    db: AsyncSession = Depends(get_db),
):
    await check_gitops_lock(group_id, db)
    entry = await scoped_row_or_404(
        db, HostsEntry, entry_id, HostsEntry.group_id == group_id, detail=_NOT_FOUND
    )
    before = _audit_state(entry)
    await _apply_entry_update(db, entry, body)
    await db.flush()
    return await commit_updated(db, entry, _AUDIT, user, before)


@router.delete("/groups/{group_id}/hosts-entries/{entry_id}", status_code=204)
async def delete_group_hosts_entry(
    group_id: int,
    entry_id: int,
    user: User = Depends(require("config.edit")),
    db: AsyncSession = Depends(get_db),
):
    await check_gitops_lock(group_id, db)
    entry = await scoped_row_or_404(
        db, HostsEntry, entry_id, HostsEntry.group_id == group_id, detail=_NOT_FOUND
    )
    await delete_and_commit(db, entry, _AUDIT, user)


# ---------------------------------------------------------------------------
# Host-level overrides
# ---------------------------------------------------------------------------


@router.get(
    "/hosts/{host_id}/hosts-entries",
    response_model=list[HostsEntryResponse],
)
async def list_host_hosts_entries(
    host_id: int,
    _: User = Depends(require("hosts.view")),
    db: AsyncSession = Depends(get_db),
):
    await require_host(db, host_id)

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
    user: User = Depends(require("config.edit")),
    db: AsyncSession = Depends(get_db),
):
    await require_host(db, host_id)

    await _validate_host_ref(db, body.host_ref_id)
    entry = HostsEntry(host_id=host_id, **body.model_dump())
    db.add(entry)
    await db.flush()
    return await commit_created(db, entry, _AUDIT, user)


@router.put(
    "/hosts/{host_id}/hosts-entries/{entry_id}",
    response_model=HostsEntryResponse,
)
async def update_host_hosts_entry(
    host_id: int,
    entry_id: int,
    body: HostsEntryUpdate,
    user: User = Depends(require("config.edit")),
    db: AsyncSession = Depends(get_db),
):
    entry = await scoped_row_or_404(
        db, HostsEntry, entry_id, HostsEntry.host_id == host_id, detail=_NOT_FOUND
    )
    before = _audit_state(entry)
    await _apply_entry_update(db, entry, body)
    await db.flush()
    return await commit_updated(db, entry, _AUDIT, user, before)


@router.delete("/hosts/{host_id}/hosts-entries/{entry_id}", status_code=204)
async def delete_host_hosts_entry(
    host_id: int,
    entry_id: int,
    user: User = Depends(require("config.edit")),
    db: AsyncSession = Depends(get_db),
):
    entry = await scoped_row_or_404(
        db, HostsEntry, entry_id, HostsEntry.host_id == host_id, detail=_NOT_FOUND
    )
    await delete_and_commit(db, entry, _AUDIT, user)


# ---------------------------------------------------------------------------
# Effective config (merged group + host overrides)
# ---------------------------------------------------------------------------


@router.get(
    "/hosts/{host_id}/effective-hosts-entries",
    response_model=list[EffectiveHostsEntryResponse],
)
async def effective_hosts_entries(
    host_id: int,
    _: User = Depends(require("hosts.view")),
    db: AsyncSession = Depends(get_db),
):
    await require_host(db, host_id)

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
    _: User = Depends(require("hosts.view")),
    db: AsyncSession = Depends(get_db),
):
    await require_host(db, host_id)

    entries = await get_effective_hosts_entries(host_id, db)
    # Empty when no entry applies: a sync then leaves the file alone, so
    # there is no file to show.
    content = render_hosts_file(entries) if entries else ""
    return PlainTextResponse(content=content)
