from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.db import get_db
from app.models.host_group import HostGroup
from app.models.user import User
from app.auth.users import current_active_user, current_superuser
from app.auth.rbac import get_user_accessible_group_ids
from app.schemas.groups import GroupCreate, GroupUpdate, GroupResponse

router = APIRouter(prefix="/groups", tags=["groups"])


@router.get("", response_model=list[GroupResponse])
async def list_groups(
    user: User = Depends(current_active_user),
    db: AsyncSession = Depends(get_db),
):
    accessible = await get_user_accessible_group_ids(user, db)
    q = select(HostGroup).order_by(HostGroup.priority.desc())
    if accessible is not None:
        q = q.where(HostGroup.id.in_(accessible))
    result = await db.execute(q)
    return result.scalars().all()


@router.post("", response_model=GroupResponse, status_code=201)
async def create_group(
    body: GroupCreate,
    _: User = Depends(current_superuser),
    db: AsyncSession = Depends(get_db),
):
    # Check unique name
    existing = await db.execute(select(HostGroup).where(HostGroup.name == body.name))
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=409, detail="Group name already exists")
    # Check unique priority
    existing_p = await db.execute(select(HostGroup).where(HostGroup.priority == body.priority))
    if existing_p.scalar_one_or_none():
        raise HTTPException(status_code=409, detail="Group priority already in use")
    group = HostGroup(**body.model_dump())
    db.add(group)
    await db.commit()
    await db.refresh(group)
    return group


@router.get("/{group_id}", response_model=GroupResponse)
async def get_group(
    group_id: int,
    user: User = Depends(current_active_user),
    db: AsyncSession = Depends(get_db),
):
    accessible = await get_user_accessible_group_ids(user, db)
    if accessible is not None and group_id not in accessible:
        raise HTTPException(status_code=403, detail="Not authorized")
    result = await db.execute(select(HostGroup).where(HostGroup.id == group_id))
    group = result.scalar_one_or_none()
    if not group:
        raise HTTPException(status_code=404, detail="Group not found")
    return group


@router.put("/{group_id}", response_model=GroupResponse)
async def update_group(
    group_id: int,
    body: GroupUpdate,
    user: User = Depends(current_active_user),
    db: AsyncSession = Depends(get_db),
):
    accessible = await get_user_accessible_group_ids(user, db)
    if accessible is not None and group_id not in accessible:
        raise HTTPException(status_code=403, detail="Not authorized")
    result = await db.execute(select(HostGroup).where(HostGroup.id == group_id))
    group = result.scalar_one_or_none()
    if not group:
        raise HTTPException(status_code=404, detail="Group not found")
    for field, value in body.model_dump(exclude_none=True).items():
        setattr(group, field, value)
    await db.commit()
    await db.refresh(group)
    return group


@router.delete("/{group_id}", status_code=204)
async def delete_group(
    group_id: int,
    _: User = Depends(current_superuser),
    db: AsyncSession = Depends(get_db),
):
    from app.models.host import HostGroupMembership
    # Check no hosts assigned
    members = await db.execute(select(HostGroupMembership).where(HostGroupMembership.c.group_id == group_id))
    if members.fetchone():
        raise HTTPException(status_code=400, detail="Cannot delete group with hosts assigned")
    result = await db.execute(select(HostGroup).where(HostGroup.id == group_id))
    group = result.scalar_one_or_none()
    if not group:
        raise HTTPException(status_code=404, detail="Group not found")
    await db.delete(group)
    await db.commit()
