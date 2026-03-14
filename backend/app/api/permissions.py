from fastapi import APIRouter, Depends, status
from pydantic import BaseModel
from sqlalchemy import select, delete
from sqlalchemy.ext.asyncio import AsyncSession
from app.db import get_db
from app.models.user_group_permission import UserGroupPermission, GroupRole
from app.models.user import User
from app.auth.users import current_superuser

router = APIRouter(prefix="/groups/{group_id}/permissions", tags=["permissions"])


class PermissionCreate(BaseModel):
    user_id: int
    role: GroupRole


class PermissionResponse(BaseModel):
    user_id: int
    group_id: int
    role: GroupRole
    model_config = {"from_attributes": True}


@router.post("", response_model=PermissionResponse, status_code=201)
async def grant_permission(group_id: int, body: PermissionCreate, _: User = Depends(current_superuser), db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(UserGroupPermission).where(UserGroupPermission.user_id == body.user_id, UserGroupPermission.group_id == group_id))
    perm = result.scalar_one_or_none()
    if perm:
        perm.role = body.role
    else:
        perm = UserGroupPermission(user_id=body.user_id, group_id=group_id, role=body.role)
        db.add(perm)
    await db.commit()
    await db.refresh(perm)
    return perm


@router.get("", response_model=list[PermissionResponse])
async def list_permissions(group_id: int, _: User = Depends(current_superuser), db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(UserGroupPermission).where(UserGroupPermission.group_id == group_id))
    return result.scalars().all()


@router.delete("/{user_id}", status_code=204)
async def revoke_permission(group_id: int, user_id: int, _: User = Depends(current_superuser), db: AsyncSession = Depends(get_db)):
    await db.execute(delete(UserGroupPermission).where(UserGroupPermission.user_id == user_id, UserGroupPermission.group_id == group_id))
    await db.commit()
