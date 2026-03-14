from fastapi import Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.db import get_db
from app.models.user import User
from app.models.user_group_permission import UserGroupPermission, GroupRole
from app.auth.users import current_active_user

ROLE_HIERARCHY = {GroupRole.viewer: 0, GroupRole.editor: 1, GroupRole.admin: 2}


def require_group_role(min_role: GroupRole = GroupRole.viewer):
    """Factory returning a FastAPI dependency that checks group-level access."""
    async def check_permission(
        group_id: int,
        user: User = Depends(current_active_user),
        db: AsyncSession = Depends(get_db),
    ) -> None:
        if user.is_superuser:
            return
        result = await db.execute(
            select(UserGroupPermission).where(
                UserGroupPermission.user_id == user.id,
                UserGroupPermission.group_id == group_id,
            )
        )
        perm = result.scalar_one_or_none()
        if perm is None:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not authorized to access this group")
        if ROLE_HIERARCHY.get(perm.role, -1) < ROLE_HIERARCHY.get(min_role, 0):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=f"Role '{perm.role.value}' insufficient, need '{min_role.value}'")
    return check_permission


async def get_user_accessible_group_ids(user: User, db: AsyncSession) -> list[int] | None:
    """Returns list of accessible group IDs, or None if superuser (all groups)."""
    if user.is_superuser:
        return None
    result = await db.execute(
        select(UserGroupPermission.group_id).where(UserGroupPermission.user_id == user.id)
    )
    return [row[0] for row in result.all()]
