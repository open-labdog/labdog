from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit.logger import log_action
from app.auth.users import current_superuser
from app.api._gitops_lock import check_gitops_lock
from app.db import get_db
from app.models.host import Host
from app.models.host_group import HostGroup
from app.models.user import User
from app.user_mgmt.merge import get_effective_users
from app.user_mgmt.models import LinuxUser
from app.user_mgmt.schemas import (
    EffectiveLinuxUserResponse,
    LinuxUserCreate,
    LinuxUserResponse,
    LinuxUserUpdate,
)

router = APIRouter(tags=["linux-users"])


# ---------------------------------------------------------------------------
# Group-level CRUD
# ---------------------------------------------------------------------------


@router.get("/groups/{group_id}/linux-users", response_model=list[LinuxUserResponse])
async def list_group_linux_users(
    group_id: int,
    _: User = Depends(current_superuser),
    db: AsyncSession = Depends(get_db),
):
    group = await db.scalar(select(HostGroup).where(HostGroup.id == group_id))
    if not group:
        raise HTTPException(status_code=404, detail="Group not found")

    result = await db.execute(
        select(LinuxUser)
        .where(LinuxUser.group_id == group_id)
        .order_by(LinuxUser.priority.desc(), LinuxUser.id)
    )
    return result.scalars().all()


@router.post(
    "/groups/{group_id}/linux-users",
    response_model=LinuxUserResponse,
    status_code=201,
)
async def create_group_linux_user(
    group_id: int,
    body: LinuxUserCreate,
    user: User = Depends(current_superuser),
    db: AsyncSession = Depends(get_db),
):
    await check_gitops_lock(group_id, db)
    group = await db.scalar(select(HostGroup).where(HostGroup.id == group_id))
    if not group:
        raise HTTPException(status_code=404, detail="Group not found")

    rule = LinuxUser(group_id=group_id, **body.model_dump())
    db.add(rule)
    await db.flush()

    await log_action(
        db=db,
        action="create",
        entity_type="linux_user",
        entity_id=rule.id,
        user_id=user.id,
        after_state={"username": rule.username, "state": str(rule.state)},
    )
    await db.commit()
    await db.refresh(rule)
    return rule


@router.put(
    "/groups/{group_id}/linux-users/{rule_id}",
    response_model=LinuxUserResponse,
)
async def update_group_linux_user(
    group_id: int,
    rule_id: int,
    body: LinuxUserUpdate,
    user: User = Depends(current_superuser),
    db: AsyncSession = Depends(get_db),
):
    await check_gitops_lock(group_id, db)
    result = await db.execute(
        select(LinuxUser).where(
            LinuxUser.id == rule_id,
            LinuxUser.group_id == group_id,
        )
    )
    rule = result.scalar_one_or_none()
    if not rule:
        raise HTTPException(status_code=404, detail="Linux user rule not found")

    before = {"username": rule.username, "state": str(rule.state)}

    for field, value in body.model_dump(exclude_unset=True).items():
        setattr(rule, field, value)

    await db.flush()

    await log_action(
        db=db,
        action="update",
        entity_type="linux_user",
        entity_id=rule.id,
        user_id=user.id,
        before_state=before,
        after_state={"username": rule.username, "state": str(rule.state)},
    )
    await db.commit()
    await db.refresh(rule)
    return rule


@router.delete("/groups/{group_id}/linux-users/{rule_id}", status_code=204)
async def delete_group_linux_user(
    group_id: int,
    rule_id: int,
    user: User = Depends(current_superuser),
    db: AsyncSession = Depends(get_db),
):
    await check_gitops_lock(group_id, db)
    result = await db.execute(
        select(LinuxUser).where(
            LinuxUser.id == rule_id,
            LinuxUser.group_id == group_id,
        )
    )
    rule = result.scalar_one_or_none()
    if not rule:
        raise HTTPException(status_code=404, detail="Linux user rule not found")

    before = {"username": rule.username, "state": str(rule.state)}
    rule_id_for_log = rule.id

    await db.delete(rule)
    await db.flush()

    await log_action(
        db=db,
        action="delete",
        entity_type="linux_user",
        entity_id=rule_id_for_log,
        user_id=user.id,
        before_state=before,
    )
    await db.commit()


# ---------------------------------------------------------------------------
# Host-level overrides
# ---------------------------------------------------------------------------


@router.get("/hosts/{host_id}/linux-users", response_model=list[LinuxUserResponse])
async def list_host_linux_users(
    host_id: int,
    _: User = Depends(current_superuser),
    db: AsyncSession = Depends(get_db),
):
    host = await db.scalar(select(Host).where(Host.id == host_id))
    if not host:
        raise HTTPException(status_code=404, detail="Host not found")

    result = await db.execute(
        select(LinuxUser)
        .where(LinuxUser.host_id == host_id)
        .order_by(LinuxUser.priority.desc(), LinuxUser.id)
    )
    return result.scalars().all()


@router.post(
    "/hosts/{host_id}/linux-users",
    response_model=LinuxUserResponse,
    status_code=201,
)
async def create_host_linux_user(
    host_id: int,
    body: LinuxUserCreate,
    user: User = Depends(current_superuser),
    db: AsyncSession = Depends(get_db),
):
    host = await db.scalar(select(Host).where(Host.id == host_id))
    if not host:
        raise HTTPException(status_code=404, detail="Host not found")

    rule = LinuxUser(host_id=host_id, **body.model_dump())
    db.add(rule)
    await db.flush()

    await log_action(
        db=db,
        action="create",
        entity_type="linux_user",
        entity_id=rule.id,
        user_id=user.id,
        after_state={"username": rule.username, "state": str(rule.state)},
    )
    await db.commit()
    await db.refresh(rule)
    return rule


@router.put(
    "/hosts/{host_id}/linux-users/{rule_id}",
    response_model=LinuxUserResponse,
)
async def update_host_linux_user(
    host_id: int,
    rule_id: int,
    body: LinuxUserUpdate,
    user: User = Depends(current_superuser),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(LinuxUser).where(
            LinuxUser.id == rule_id,
            LinuxUser.host_id == host_id,
        )
    )
    rule = result.scalar_one_or_none()
    if not rule:
        raise HTTPException(status_code=404, detail="Linux user rule not found")

    before = {"username": rule.username, "state": str(rule.state)}

    for field, value in body.model_dump(exclude_unset=True).items():
        setattr(rule, field, value)

    await db.flush()

    await log_action(
        db=db,
        action="update",
        entity_type="linux_user",
        entity_id=rule.id,
        user_id=user.id,
        before_state=before,
        after_state={"username": rule.username, "state": str(rule.state)},
    )
    await db.commit()
    await db.refresh(rule)
    return rule


@router.delete("/hosts/{host_id}/linux-users/{rule_id}", status_code=204)
async def delete_host_linux_user(
    host_id: int,
    rule_id: int,
    user: User = Depends(current_superuser),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(LinuxUser).where(
            LinuxUser.id == rule_id,
            LinuxUser.host_id == host_id,
        )
    )
    rule = result.scalar_one_or_none()
    if not rule:
        raise HTTPException(status_code=404, detail="Linux user rule not found")

    before = {"username": rule.username, "state": str(rule.state)}
    rule_id_for_log = rule.id

    await db.delete(rule)
    await db.flush()

    await log_action(
        db=db,
        action="delete",
        entity_type="linux_user",
        entity_id=rule_id_for_log,
        user_id=user.id,
        before_state=before,
    )
    await db.commit()


# ---------------------------------------------------------------------------
# Effective config (merged group + host overrides)
# ---------------------------------------------------------------------------


@router.get(
    "/hosts/{host_id}/effective-users",
    response_model=list[EffectiveLinuxUserResponse],
)
async def effective_users(
    host_id: int,
    _: User = Depends(current_superuser),
    db: AsyncSession = Depends(get_db),
):
    host = await db.scalar(select(Host).where(Host.id == host_id))
    if not host:
        raise HTTPException(status_code=404, detail="Host not found")

    return await get_effective_users(host_id, db)
