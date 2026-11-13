from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api._gitops_lock import check_gitops_lock
from app.api._scoped_rules import (
    RuleAudit,
    apply_fields,
    commit_created,
    commit_updated,
    delete_and_commit,
    require_group,
    require_host,
    scoped_row_or_404,
)
from app.auth.permissions import require
from app.db import get_db
from app.models.user import User
from app.user_mgmt.merge import get_effective_groups
from app.user_mgmt.models import LinuxGroup
from app.user_mgmt.schemas import (
    EffectiveLinuxGroupResponse,
    LinuxGroupCreate,
    LinuxGroupResponse,
    LinuxGroupUpdate,
)

router = APIRouter(tags=["linux-groups"])

_NOT_FOUND = "Linux group rule not found"


def _audit_state(rule: LinuxGroup) -> dict:
    return {"groupname": rule.groupname, "state": str(rule.state)}


_AUDIT = RuleAudit("linux_group", _audit_state)


# ---------------------------------------------------------------------------
# Group-level CRUD
# ---------------------------------------------------------------------------


@router.get("/groups/{group_id}/linux-groups", response_model=list[LinuxGroupResponse])
async def list_group_linux_groups(
    group_id: int,
    _: User = Depends(require("hosts.view")),
    db: AsyncSession = Depends(get_db),
):
    await require_group(db, group_id)

    result = await db.execute(
        select(LinuxGroup)
        .where(LinuxGroup.group_id == group_id)
        .order_by(LinuxGroup.priority.desc(), LinuxGroup.id)
    )
    return result.scalars().all()


@router.post(
    "/groups/{group_id}/linux-groups",
    response_model=LinuxGroupResponse,
    status_code=201,
)
async def create_group_linux_group(
    group_id: int,
    body: LinuxGroupCreate,
    user: User = Depends(require("config.edit")),
    db: AsyncSession = Depends(get_db),
):
    await check_gitops_lock(group_id, db)
    await require_group(db, group_id)

    rule = LinuxGroup(group_id=group_id, **body.model_dump())
    db.add(rule)
    await db.flush()
    return await commit_created(db, rule, _AUDIT, user)


@router.put(
    "/groups/{group_id}/linux-groups/{rule_id}",
    response_model=LinuxGroupResponse,
)
async def update_group_linux_group(
    group_id: int,
    rule_id: int,
    body: LinuxGroupUpdate,
    user: User = Depends(require("config.edit")),
    db: AsyncSession = Depends(get_db),
):
    await check_gitops_lock(group_id, db)
    rule = await scoped_row_or_404(
        db, LinuxGroup, rule_id, LinuxGroup.group_id == group_id, detail=_NOT_FOUND
    )
    before = _audit_state(rule)
    apply_fields(rule, body.model_dump(exclude_unset=True))
    await db.flush()
    return await commit_updated(db, rule, _AUDIT, user, before)


@router.delete("/groups/{group_id}/linux-groups/{rule_id}", status_code=204)
async def delete_group_linux_group(
    group_id: int,
    rule_id: int,
    user: User = Depends(require("config.edit")),
    db: AsyncSession = Depends(get_db),
):
    await check_gitops_lock(group_id, db)
    rule = await scoped_row_or_404(
        db, LinuxGroup, rule_id, LinuxGroup.group_id == group_id, detail=_NOT_FOUND
    )
    await delete_and_commit(db, rule, _AUDIT, user)


# ---------------------------------------------------------------------------
# Host-level overrides
# ---------------------------------------------------------------------------


@router.get("/hosts/{host_id}/linux-groups", response_model=list[LinuxGroupResponse])
async def list_host_linux_groups(
    host_id: int,
    _: User = Depends(require("hosts.view")),
    db: AsyncSession = Depends(get_db),
):
    await require_host(db, host_id)

    result = await db.execute(
        select(LinuxGroup)
        .where(LinuxGroup.host_id == host_id)
        .order_by(LinuxGroup.priority.desc(), LinuxGroup.id)
    )
    return result.scalars().all()


@router.post(
    "/hosts/{host_id}/linux-groups",
    response_model=LinuxGroupResponse,
    status_code=201,
)
async def create_host_linux_group(
    host_id: int,
    body: LinuxGroupCreate,
    user: User = Depends(require("config.edit")),
    db: AsyncSession = Depends(get_db),
):
    await require_host(db, host_id)

    rule = LinuxGroup(host_id=host_id, **body.model_dump())
    db.add(rule)
    await db.flush()
    return await commit_created(db, rule, _AUDIT, user)


@router.put(
    "/hosts/{host_id}/linux-groups/{rule_id}",
    response_model=LinuxGroupResponse,
)
async def update_host_linux_group(
    host_id: int,
    rule_id: int,
    body: LinuxGroupUpdate,
    user: User = Depends(require("config.edit")),
    db: AsyncSession = Depends(get_db),
):
    rule = await scoped_row_or_404(
        db, LinuxGroup, rule_id, LinuxGroup.host_id == host_id, detail=_NOT_FOUND
    )
    before = _audit_state(rule)
    apply_fields(rule, body.model_dump(exclude_unset=True))
    await db.flush()
    return await commit_updated(db, rule, _AUDIT, user, before)


@router.delete("/hosts/{host_id}/linux-groups/{rule_id}", status_code=204)
async def delete_host_linux_group(
    host_id: int,
    rule_id: int,
    user: User = Depends(require("config.edit")),
    db: AsyncSession = Depends(get_db),
):
    rule = await scoped_row_or_404(
        db, LinuxGroup, rule_id, LinuxGroup.host_id == host_id, detail=_NOT_FOUND
    )
    await delete_and_commit(db, rule, _AUDIT, user)


# ---------------------------------------------------------------------------
# Effective config (merged group + host overrides)
# ---------------------------------------------------------------------------


@router.get(
    "/hosts/{host_id}/effective-groups",
    response_model=list[EffectiveLinuxGroupResponse],
)
async def effective_groups(
    host_id: int,
    _: User = Depends(require("hosts.view")),
    db: AsyncSession = Depends(get_db),
):
    await require_host(db, host_id)

    return await get_effective_groups(host_id, db)
