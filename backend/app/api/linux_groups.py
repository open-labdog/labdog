from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_db
from app.auth.users import current_superuser
from app.models.user import User
from app.models.host_group import HostGroup
from app.models.host import Host
from app.user_mgmt.models import LinuxGroup
from app.user_mgmt.schemas import (
    LinuxGroupCreate,
    LinuxGroupUpdate,
    LinuxGroupResponse,
    EffectiveLinuxGroupResponse,
)
from app.user_mgmt.merge import get_effective_groups
from app.audit.logger import log_action

router = APIRouter(tags=["linux-groups"])


# ---------------------------------------------------------------------------
# Group-level CRUD
# ---------------------------------------------------------------------------


@router.get("/groups/{group_id}/linux-groups", response_model=list[LinuxGroupResponse])
async def list_group_linux_groups(
    group_id: int,
    _: User = Depends(current_superuser),
    db: AsyncSession = Depends(get_db),
):
    group = await db.scalar(select(HostGroup).where(HostGroup.id == group_id))
    if not group:
        raise HTTPException(status_code=404, detail="Group not found")

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
    user: User = Depends(current_superuser),
    db: AsyncSession = Depends(get_db),
):
    group = await db.scalar(select(HostGroup).where(HostGroup.id == group_id))
    if not group:
        raise HTTPException(status_code=404, detail="Group not found")

    rule = LinuxGroup(group_id=group_id, **body.model_dump())
    db.add(rule)
    await db.flush()

    await log_action(
        db=db,
        action="create",
        entity_type="linux_group",
        entity_id=rule.id,
        user_id=user.id,
        after_state={"groupname": rule.groupname, "state": str(rule.state)},
    )
    await db.commit()
    await db.refresh(rule)
    return rule


@router.put(
    "/groups/{group_id}/linux-groups/{rule_id}",
    response_model=LinuxGroupResponse,
)
async def update_group_linux_group(
    group_id: int,
    rule_id: int,
    body: LinuxGroupUpdate,
    user: User = Depends(current_superuser),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(LinuxGroup).where(
            LinuxGroup.id == rule_id,
            LinuxGroup.group_id == group_id,
        )
    )
    rule = result.scalar_one_or_none()
    if not rule:
        raise HTTPException(status_code=404, detail="Linux group rule not found")

    before = {"groupname": rule.groupname, "state": str(rule.state)}

    for field, value in body.model_dump(exclude_unset=True).items():
        setattr(rule, field, value)

    await db.flush()

    await log_action(
        db=db,
        action="update",
        entity_type="linux_group",
        entity_id=rule.id,
        user_id=user.id,
        before_state=before,
        after_state={"groupname": rule.groupname, "state": str(rule.state)},
    )
    await db.commit()
    await db.refresh(rule)
    return rule


@router.delete("/groups/{group_id}/linux-groups/{rule_id}", status_code=204)
async def delete_group_linux_group(
    group_id: int,
    rule_id: int,
    user: User = Depends(current_superuser),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(LinuxGroup).where(
            LinuxGroup.id == rule_id,
            LinuxGroup.group_id == group_id,
        )
    )
    rule = result.scalar_one_or_none()
    if not rule:
        raise HTTPException(status_code=404, detail="Linux group rule not found")

    before = {"groupname": rule.groupname, "state": str(rule.state)}
    rule_id_for_log = rule.id

    await db.delete(rule)
    await db.flush()

    await log_action(
        db=db,
        action="delete",
        entity_type="linux_group",
        entity_id=rule_id_for_log,
        user_id=user.id,
        before_state=before,
    )
    await db.commit()


# ---------------------------------------------------------------------------
# Host-level overrides
# ---------------------------------------------------------------------------


@router.get("/hosts/{host_id}/linux-groups", response_model=list[LinuxGroupResponse])
async def list_host_linux_groups(
    host_id: int,
    _: User = Depends(current_superuser),
    db: AsyncSession = Depends(get_db),
):
    host = await db.scalar(select(Host).where(Host.id == host_id))
    if not host:
        raise HTTPException(status_code=404, detail="Host not found")

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
    user: User = Depends(current_superuser),
    db: AsyncSession = Depends(get_db),
):
    host = await db.scalar(select(Host).where(Host.id == host_id))
    if not host:
        raise HTTPException(status_code=404, detail="Host not found")

    rule = LinuxGroup(host_id=host_id, **body.model_dump())
    db.add(rule)
    await db.flush()

    await log_action(
        db=db,
        action="create",
        entity_type="linux_group",
        entity_id=rule.id,
        user_id=user.id,
        after_state={"groupname": rule.groupname, "state": str(rule.state)},
    )
    await db.commit()
    await db.refresh(rule)
    return rule


@router.put(
    "/hosts/{host_id}/linux-groups/{rule_id}",
    response_model=LinuxGroupResponse,
)
async def update_host_linux_group(
    host_id: int,
    rule_id: int,
    body: LinuxGroupUpdate,
    user: User = Depends(current_superuser),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(LinuxGroup).where(
            LinuxGroup.id == rule_id,
            LinuxGroup.host_id == host_id,
        )
    )
    rule = result.scalar_one_or_none()
    if not rule:
        raise HTTPException(status_code=404, detail="Linux group rule not found")

    before = {"groupname": rule.groupname, "state": str(rule.state)}

    for field, value in body.model_dump(exclude_unset=True).items():
        setattr(rule, field, value)

    await db.flush()

    await log_action(
        db=db,
        action="update",
        entity_type="linux_group",
        entity_id=rule.id,
        user_id=user.id,
        before_state=before,
        after_state={"groupname": rule.groupname, "state": str(rule.state)},
    )
    await db.commit()
    await db.refresh(rule)
    return rule


@router.delete("/hosts/{host_id}/linux-groups/{rule_id}", status_code=204)
async def delete_host_linux_group(
    host_id: int,
    rule_id: int,
    user: User = Depends(current_superuser),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(LinuxGroup).where(
            LinuxGroup.id == rule_id,
            LinuxGroup.host_id == host_id,
        )
    )
    rule = result.scalar_one_or_none()
    if not rule:
        raise HTTPException(status_code=404, detail="Linux group rule not found")

    before = {"groupname": rule.groupname, "state": str(rule.state)}
    rule_id_for_log = rule.id

    await db.delete(rule)
    await db.flush()

    await log_action(
        db=db,
        action="delete",
        entity_type="linux_group",
        entity_id=rule_id_for_log,
        user_id=user.id,
        before_state=before,
    )
    await db.commit()


# ---------------------------------------------------------------------------
# Effective config (merged group + host overrides)
# ---------------------------------------------------------------------------


@router.get(
    "/hosts/{host_id}/effective-groups",
    response_model=list[EffectiveLinuxGroupResponse],
)
async def effective_groups(
    host_id: int,
    _: User = Depends(current_superuser),
    db: AsyncSession = Depends(get_db),
):
    host = await db.scalar(select(Host).where(Host.id == host_id))
    if not host:
        raise HTTPException(status_code=404, detail="Host not found")

    return await get_effective_groups(host_id, db)
