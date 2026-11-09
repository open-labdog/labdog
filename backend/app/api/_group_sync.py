"""Steps shared by the endpoints that start a sync on every member of a group.

The six module group syncs (services, packages, /etc/hosts, cron, Linux
users, resolver) were one body written six times: list the members, refuse
an empty group, skip a host that already has that module's sync in flight,
stage a pending ``SyncJob`` for each of the rest, commit, then dispatch.
:func:`sync_group_members` is that body. The firewall and bulk group syncs
and the package uninstall branch differ (audit rows, a module filter, a
key check), so they use only the member lookup and the in-flight check.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.host import HostGroupMembership
from app.models.sync_job import SyncJob
from app.models.user import User


async def group_member_ids(db: AsyncSession, group_id: int) -> list[int]:
    """The ids of the group's member hosts. A missing group has none."""
    memberships = await db.execute(
        select(HostGroupMembership.c.host_id).where(HostGroupMembership.c.group_id == group_id)
    )
    return [r[0] for r in memberships.all()]


async def group_member_ids_or_400(db: AsyncSession, group_id: int) -> list[int]:
    host_ids = await group_member_ids(db, group_id)
    if not host_ids:
        raise HTTPException(status_code=400, detail="No hosts in this group")
    return host_ids


async def has_sync_in_flight(db: AsyncSession, host_id: int, module_type: str) -> bool:
    """Whether *host_id* has a pending or running sync of *module_type*."""
    running = await db.execute(
        select(SyncJob).where(
            SyncJob.host_id == host_id,
            SyncJob.module_type == module_type,
            SyncJob.status.in_(["pending", "running"]),
        )
    )
    return running.scalar_one_or_none() is not None


async def sync_group_members(
    db: AsyncSession,
    group_id: int,
    host_ids: list[int],
    module_type: str,
    user: User,
    task: Any,
    *,
    has_config: Callable[[int, AsyncSession], Awaitable[object]] | None = None,
) -> dict[str, int]:
    """Stage and dispatch a *module_type* sync for each of the group's hosts.

    A host is skipped when *has_config* (given) finds no effective config
    for it, or when it already has a sync of this module in flight. *task*
    is the module's Celery task; it is dispatched only after the commit.
    """
    # BUG-37: dispatch after commit, not before — see app/api/sync.py.
    pending: list[tuple[int, int]] = []
    for hid in host_ids:
        if has_config is not None and not await has_config(hid, db):
            continue
        if await has_sync_in_flight(db, hid, module_type):
            continue

        job = SyncJob(
            host_id=hid,
            group_id=group_id,
            module_type=module_type,
            status="pending",
            triggered_by_user_id=user.id,
        )
        db.add(job)
        await db.flush()
        pending.append((job.id, hid))

    await db.commit()
    for job_id, hid in pending:
        task.delay(job_id=job_id, host_id=hid)
    return {"triggered": len(pending), "skipped": len(host_ids) - len(pending)}
