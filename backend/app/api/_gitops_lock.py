"""Shared GitOps mutation lock helper.

Call ``check_gitops_lock(group_id, db)`` at the top of any POST / PUT / DELETE
endpoint that modifies group-scoped configuration managed by GitOps.  Raises
HTTP 403 when the group has ``gitops_enabled=True``.
"""

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.host_group import HostGroup

_GITOPS_LOCK_MESSAGE = "This group is managed by GitOps. Changes must be made via Git."


async def check_gitops_lock(group_id: int, db: AsyncSession) -> None:
    """Raise HTTP 403 if *group_id* is managed by GitOps.

    Args:
        group_id: Primary key of the group to inspect.
        db: Active async database session.

    Raises:
        HTTPException: 403 when ``HostGroup.gitops_enabled`` is ``True``.
    """
    result = await db.execute(select(HostGroup).where(HostGroup.id == group_id))
    group = result.scalar_one_or_none()
    if group and group.gitops_enabled:
        raise HTTPException(
            status_code=403,
            detail=_GITOPS_LOCK_MESSAGE,
        )
