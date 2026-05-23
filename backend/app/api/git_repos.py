from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit.logger import log_action
from app.auth.users import current_active_user
from app.crypto import encrypt_ssh_key, get_master_key
from app.db import get_db
from app.models.git_repository import GitAuthType, GitRepository
from app.models.host_group import HostGroup
from app.models.user import User
from app.packs.models import ActionPack
from app.schemas.git_repos import (
    GitRepoCreate,
    GitRepoResponse,
    GitRepoUpdate,
    derive_auth_type,
)

router = APIRouter(prefix="/git-repos", tags=["git-repos"])


@router.get("", response_model=list[GitRepoResponse])
async def list_git_repos(
    _: User = Depends(current_active_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(GitRepository).order_by(GitRepository.created_at.desc()))
    return result.scalars().all()


@router.post("", response_model=GitRepoResponse, status_code=201)
async def create_git_repo(
    body: GitRepoCreate,
    user: User = Depends(current_active_user),
    db: AsyncSession = Depends(get_db),
):
    existing = await db.execute(select(GitRepository).where(GitRepository.name == body.name))
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=409, detail="Git repository name already exists")

    try:
        auth_type = derive_auth_type(body.url, body.ssh_key_id, body.https_token)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    repo = GitRepository(
        name=body.name,
        url=body.url,
        branch=body.branch,
        auth_type=GitAuthType(auth_type),
        ssh_key_id=body.ssh_key_id if auth_type == "ssh_key" else None,
        webhook_secret=body.webhook_secret,
    )

    if body.https_token and auth_type == "https_token":
        master_key = get_master_key()
        repo.encrypted_https_token = encrypt_ssh_key(body.https_token, master_key)

    db.add(repo)
    await db.flush()

    await log_action(
        db=db,
        action="create",
        entity_type="git_repository",
        entity_id=repo.id,
        user_id=user.id,
        after_state={
            "name": repo.name,
            "url": repo.url,
            "branch": repo.branch,
            "auth_type": repo.auth_type.value,
        },
    )
    await db.commit()
    await db.refresh(repo)
    return repo


@router.get("/{repo_id}", response_model=GitRepoResponse)
async def get_git_repo(
    repo_id: int,
    _: User = Depends(current_active_user),
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
    user: User = Depends(current_active_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(GitRepository).where(GitRepository.id == repo_id))
    repo = result.scalar_one_or_none()
    if not repo:
        raise HTTPException(status_code=404, detail="Git repository not found")

    before = {
        "name": repo.name,
        "url": repo.url,
        "branch": repo.branch,
        "auth_type": repo.auth_type.value,
    }

    update_data = body.model_dump(exclude_none=True)
    token = update_data.pop("https_token", None)

    for field, value in update_data.items():
        setattr(repo, field, value)

    # Re-derive auth_type whenever URL or credential inputs changed.
    # An omitted token on update means "keep the existing one"; a
    # non-empty string replaces it.
    new_url = update_data.get("url", repo.url)
    new_ssh_key_id = update_data.get("ssh_key_id", repo.ssh_key_id)
    has_token = bool(token) or bool(repo.encrypted_https_token)
    try:
        auth_type = derive_auth_type(
            new_url,
            new_ssh_key_id,
            "x" if has_token else None,  # placeholder — only the truthy/falsy bit is read
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    repo.auth_type = GitAuthType(auth_type)
    if auth_type != "ssh_key":
        repo.ssh_key_id = None
    if auth_type != "https_token":
        repo.encrypted_https_token = None

    if token and auth_type == "https_token":
        repo.encrypted_https_token = encrypt_ssh_key(token, get_master_key())

    await log_action(
        db=db,
        action="update",
        entity_type="git_repository",
        entity_id=repo.id,
        user_id=user.id,
        before_state=before,
        after_state={
            "name": repo.name,
            "url": repo.url,
            "branch": repo.branch,
            "auth_type": repo.auth_type.value,
        },
    )
    await db.commit()
    await db.refresh(repo)
    return repo


@router.delete("/{repo_id}", status_code=204)
async def delete_git_repo(
    repo_id: int,
    user: User = Depends(current_active_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(GitRepository).where(GitRepository.id == repo_id))
    repo = result.scalar_one_or_none()
    if not repo:
        raise HTTPException(status_code=404, detail="Git repository not found")

    linked = await db.execute(select(HostGroup).where(HostGroup.git_repository_id == repo_id))
    if linked.scalar_one_or_none():
        raise HTTPException(status_code=400, detail="Cannot delete repository with linked groups")

    pack_names = (
        (await db.execute(select(ActionPack.name).where(ActionPack.git_repository_id == repo_id)))
        .scalars()
        .all()
    )
    if pack_names:
        raise HTTPException(
            status_code=409,
            detail=(
                f"Cannot delete: still referenced by action pack(s): "
                f"{', '.join(sorted(pack_names))}. Delete or reassign them first "
                f"on the Action Packs page."
            ),
        )

    await log_action(
        db=db,
        action="delete",
        entity_type="git_repository",
        entity_id=repo.id,
        user_id=user.id,
        before_state={
            "name": repo.name,
            "url": repo.url,
            "branch": repo.branch,
            "auth_type": repo.auth_type.value,
        },
    )
    await db.delete(repo)
    await db.commit()


@router.post("/{repo_id}/test-connection")
async def test_connection(
    repo_id: int,
    _: User = Depends(current_active_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(GitRepository).where(GitRepository.id == repo_id))
    repo = result.scalar_one_or_none()
    if not repo:
        raise HTTPException(status_code=404, detail="Git repository not found")

    return {"status": "ok", "message": "Connection test not yet implemented"}
