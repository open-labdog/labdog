from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_db
from app.models.user import User
from app.models.git_repository import GitRepository, GitAuthType
from app.models.host_group import HostGroup
from app.auth.users import current_superuser
from app.schemas.git_repos import GitRepoCreate, GitRepoUpdate, GitRepoResponse
from app.crypto import encrypt_ssh_key, get_master_key

router = APIRouter(prefix="/git-repos", tags=["git-repos"])


@router.get("", response_model=list[GitRepoResponse])
async def list_git_repos(
    _: User = Depends(current_superuser),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(GitRepository).order_by(GitRepository.created_at.desc()))
    return result.scalars().all()


@router.post("", response_model=GitRepoResponse, status_code=201)
async def create_git_repo(
    body: GitRepoCreate,
    _: User = Depends(current_superuser),
    db: AsyncSession = Depends(get_db),
):
    # Check unique name
    existing = await db.execute(select(GitRepository).where(GitRepository.name == body.name))
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=409, detail="Git repository name already exists")

    repo = GitRepository(
        name=body.name,
        url=body.url,
        branch=body.branch,
        auth_type=GitAuthType(body.auth_type),
        ssh_key_id=body.ssh_key_id,
        webhook_secret=body.webhook_secret,
    )

    # Encrypt HTTPS token if provided
    if body.https_token:
        master_key = get_master_key()
        repo.encrypted_https_token = encrypt_ssh_key(body.https_token, master_key)

    db.add(repo)
    await db.commit()
    await db.refresh(repo)
    return repo


@router.get("/{repo_id}", response_model=GitRepoResponse)
async def get_git_repo(
    repo_id: int,
    _: User = Depends(current_superuser),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(GitRepository).where(GitRepository.id == repo_id))
    repo = result.scalar_one_or_none()
    if not repo:
        raise HTTPException(status_code=404, detail="Git repository not found")
    return repo


@router.put("/{repo_id}", response_model=GitRepoResponse)
async def update_git_repo(
    repo_id: int,
    body: GitRepoUpdate,
    _: User = Depends(current_superuser),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(GitRepository).where(GitRepository.id == repo_id))
    repo = result.scalar_one_or_none()
    if not repo:
        raise HTTPException(status_code=404, detail="Git repository not found")

    update_data = body.model_dump(exclude_none=True)
    token = update_data.pop("https_token", None)

    for field, value in update_data.items():
        if field == "auth_type":
            setattr(repo, field, GitAuthType(value))
        else:
            setattr(repo, field, value)

    if token:
        repo.encrypted_https_token = encrypt_ssh_key(token, get_master_key())

    await db.commit()
    await db.refresh(repo)
    return repo


@router.delete("/{repo_id}", status_code=204)
async def delete_git_repo(
    repo_id: int,
    _: User = Depends(current_superuser),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(GitRepository).where(GitRepository.id == repo_id))
    repo = result.scalar_one_or_none()
    if not repo:
        raise HTTPException(status_code=404, detail="Git repository not found")

    # Check if any groups linked
    linked = await db.execute(
        select(HostGroup).where(HostGroup.git_repository_id == repo_id)
    )
    if linked.scalar_one_or_none():
        raise HTTPException(status_code=400, detail="Cannot delete repository with linked groups")

    await db.delete(repo)
    await db.commit()


@router.post("/{repo_id}/test-connection")
async def test_connection(
    repo_id: int,
    _: User = Depends(current_superuser),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(GitRepository).where(GitRepository.id == repo_id))
    repo = result.scalar_one_or_none()
    if not repo:
        raise HTTPException(status_code=404, detail="Git repository not found")

    # Placeholder — actual git connection test will be implemented in the sync engine
    return {"status": "ok", "message": "Connection test not yet implemented"}
