from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import delete, insert, select
from sqlalchemy.ext.asyncio import AsyncSession
from app.db import get_db
from app.models.host import HostGroupMembership
from app.models.host_group import HostGroup
from app.models.git_repository import GitRepository, GitOpsStatus
from app.models.user import User
from app.auth.users import current_active_user, current_superuser
from app.ca_certs.actions import auto_enqueue_for_new_membership
from app.schemas.groups import GroupCreate, GroupUpdate, GroupResponse
from app.schemas.git_repos import GitOpsEnableRequest, GitOpsStatusResponse


class BulkAddHostsRequest(BaseModel):
    host_ids: list[int]

router = APIRouter(prefix="/groups", tags=["groups"])


@router.get("", response_model=list[GroupResponse])
async def list_groups(
    _: User = Depends(current_active_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(HostGroup).order_by(HostGroup.priority.desc()))
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
    _: User = Depends(current_active_user),
    db: AsyncSession = Depends(get_db),
):
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
    result = await db.execute(select(HostGroup).where(HostGroup.id == group_id))
    group = result.scalar_one_or_none()
    if not group:
        raise HTTPException(status_code=404, detail="Group not found")
    # Check unique priority (skip if unchanged)
    if body.priority is not None and body.priority != group.priority:
        existing_p = await db.execute(
            select(HostGroup).where(HostGroup.priority == body.priority, HostGroup.id != group_id)
        )
        if existing_p.scalar_one_or_none():
            raise HTTPException(status_code=409, detail="Group priority already in use")
    # Check unique name (skip if unchanged)
    if body.name is not None and body.name != group.name:
        existing_n = await db.execute(
            select(HostGroup).where(HostGroup.name == body.name, HostGroup.id != group_id)
        )
        if existing_n.scalar_one_or_none():
            raise HTTPException(status_code=409, detail="Group name already exists")
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
    members = await db.execute(
        select(HostGroupMembership).where(HostGroupMembership.c.group_id == group_id)
    )
    if members.fetchone():
        raise HTTPException(status_code=400, detail="Cannot delete group with hosts assigned")
    result = await db.execute(select(HostGroup).where(HostGroup.id == group_id))
    group = result.scalar_one_or_none()
    if not group:
        raise HTTPException(status_code=404, detail="Group not found")
    await db.delete(group)
    await db.commit()


@router.get("/{group_id}/host-count")
async def get_group_host_count(
    group_id: int,
    _: User = Depends(current_active_user),
    db: AsyncSession = Depends(get_db),
):
    from sqlalchemy import func
    result = await db.execute(
        select(func.count()).select_from(HostGroupMembership).where(
            HostGroupMembership.c.group_id == group_id
        )
    )
    return {"count": result.scalar()}


@router.post("/{group_id}/hosts", status_code=200)
async def add_hosts_to_group(
    group_id: int,
    body: BulkAddHostsRequest,
    user: User = Depends(current_active_user),
    db: AsyncSession = Depends(get_db),
):
    """Add multiple hosts to this group (skips hosts already in the group)."""
    result = await db.execute(select(HostGroup).where(HostGroup.id == group_id))
    if not result.scalar_one_or_none():
        raise HTTPException(status_code=404, detail="Group not found")

    # Find which hosts are already members
    existing = await db.execute(
        select(HostGroupMembership.c.host_id).where(
            HostGroupMembership.c.group_id == group_id,
            HostGroupMembership.c.host_id.in_(body.host_ids),
        )
    )
    already_member = {r[0] for r in existing.all()}
    to_add = [hid for hid in body.host_ids if hid not in already_member]

    if to_add:
        await db.execute(
            insert(HostGroupMembership),
            [{"host_id": hid, "group_id": group_id} for hid in to_add],
        )
        await db.flush()

        # Auto-enqueue CA cert deploy for newly-added hosts (no-op if the
        # group has no certs or the host has no SSH key).
        for hid in to_add:
            await auto_enqueue_for_new_membership(
                hid, group_id, db, triggered_by_user_id=user.id
            )

        await db.commit()

    return {"added": len(to_add), "already_member": len(already_member)}


@router.delete("/{group_id}/hosts", status_code=204)
async def remove_hosts_from_group(
    group_id: int,
    body: BulkAddHostsRequest,
    _: User = Depends(current_active_user),
    db: AsyncSession = Depends(get_db),
):
    """Remove multiple hosts from this group."""
    result = await db.execute(select(HostGroup).where(HostGroup.id == group_id))
    if not result.scalar_one_or_none():
        raise HTTPException(status_code=404, detail="Group not found")

    await db.execute(
        delete(HostGroupMembership).where(
            HostGroupMembership.c.group_id == group_id,
            HostGroupMembership.c.host_id.in_(body.host_ids),
        )
    )
    await db.commit()


@router.post("/{group_id}/gitops/enable", response_model=GitOpsStatusResponse)
async def enable_gitops(
    group_id: int,
    body: GitOpsEnableRequest,
    _: User = Depends(current_superuser),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(HostGroup).where(HostGroup.id == group_id))
    group = result.scalar_one_or_none()
    if not group:
        raise HTTPException(status_code=404, detail="Group not found")

    if group.gitops_enabled:
        raise HTTPException(status_code=400, detail="GitOps already enabled for this group")

    repo_result = await db.execute(
        select(GitRepository).where(GitRepository.id == body.git_repository_id)
    )
    if not repo_result.scalar_one_or_none():
        raise HTTPException(status_code=404, detail="Git repository not found")

    group.gitops_enabled = True
    group.git_repository_id = body.git_repository_id
    group.gitops_file_path = body.file_path
    group.gitops_status = GitOpsStatus.disconnected
    group.gitops_error_message = None

    await db.commit()
    await db.refresh(group)

    return GitOpsStatusResponse(
        gitops_enabled=group.gitops_enabled,
        git_repository_id=group.git_repository_id,
        gitops_file_path=group.gitops_file_path,
        gitops_status=group.gitops_status.value,
        gitops_error_message=group.gitops_error_message,
        gitops_last_import_at=group.gitops_last_import_at,
    )


@router.post("/{group_id}/gitops/disable", response_model=GitOpsStatusResponse)
async def disable_gitops(
    group_id: int,
    _: User = Depends(current_superuser),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(HostGroup).where(HostGroup.id == group_id))
    group = result.scalar_one_or_none()
    if not group:
        raise HTTPException(status_code=404, detail="Group not found")

    # Clear gitops fields — do NOT remove rules from DB
    group.gitops_enabled = False
    group.git_repository_id = None
    group.gitops_file_path = None
    group.gitops_status = GitOpsStatus.disconnected
    group.gitops_error_message = None

    await db.commit()
    await db.refresh(group)

    return GitOpsStatusResponse(
        gitops_enabled=group.gitops_enabled,
        git_repository_id=group.git_repository_id,
        gitops_file_path=group.gitops_file_path,
        gitops_status=group.gitops_status.value,
        gitops_error_message=group.gitops_error_message,
        gitops_last_import_at=group.gitops_last_import_at,
    )


@router.get("/{group_id}/gitops/status", response_model=GitOpsStatusResponse)
async def get_gitops_status(
    group_id: int,
    _: User = Depends(current_active_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(HostGroup).where(HostGroup.id == group_id))
    group = result.scalar_one_or_none()
    if not group:
        raise HTTPException(status_code=404, detail="Group not found")

    return GitOpsStatusResponse(
        gitops_enabled=group.gitops_enabled,
        git_repository_id=group.git_repository_id,
        gitops_file_path=group.gitops_file_path,
        gitops_status=group.gitops_status.value,
        gitops_error_message=group.gitops_error_message,
        gitops_last_import_at=group.gitops_last_import_at,
    )
