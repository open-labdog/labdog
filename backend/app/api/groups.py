from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.db import get_db
from app.models.host_group import HostGroup
from app.models.git_repository import GitRepository, GitOpsStatus
from app.models.user import User
from app.auth.users import current_active_user, current_superuser
from app.schemas.groups import GroupCreate, GroupUpdate, GroupResponse
from app.schemas.git_repos import GitOpsEnableRequest, GitOpsStatusResponse

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
