from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit.logger import log_action
from app.auth.users import current_active_user
from app.crypto import encrypt_ssh_key, get_master_key
from app.db import get_db
from app.gitops.webhook_secret import set_webhook_secret
from app.models.git_repository import GitAuthType, GitRepository
from app.models.host_group import HostGroup
from app.models.user import User
from app.packs.models import ActionPack
from app.schemas.git_repos import (
    GitRepoCreate,
    GitRepoResponse,
    GitRepoUpdate,
    derive_auth_type,
    extract_hostname,
)

router = APIRouter(prefix="/git-repos", tags=["git-repos"])


async def _flush_or_conflict(db: AsyncSession) -> None:
    """Flush, turning a duplicate name into the intended 409 (BUG-85).

    The name lookups in the handlers are a nicety for the common case; two
    concurrent requests can both pass them, and the loser's unique
    violation used to escape as a 500. Anything else is re-raised: a
    constraint that should never fire is a bug worth a traceback.
    """
    try:
        await db.flush()
    except IntegrityError as exc:
        await db.rollback()
        orig = getattr(exc, "orig", None)
        marker = f"{getattr(orig, 'constraint_name', '') or ''} {orig}"
        if "git_repositories_name" in marker:
            raise HTTPException(
                status_code=409, detail="Git repository name already exists"
            ) from exc
        raise


async def _with_pack_syncs(db: AsyncSession, repos: list[GitRepository]) -> list[GitRepoResponse]:
    """Report the latest successful fetch of each repository.

    ``GitRepository.last_sync_at`` / ``last_commit_sha`` are written only
    by the GitOps import, which also uses the SHA to skip a push it has
    already imported, so a pack sync must not touch them. A repository
    that only feeds action packs therefore read "never synced" however
    often its packs synced. The newer of the GitOps import and
    the repository's last successful pack sync is what the list and the
    repository page mean by "last sync".
    """
    out = [GitRepoResponse.model_validate(r) for r in repos]
    if not repos:
        return out
    rows = await db.execute(
        select(ActionPack.git_repository_id, ActionPack.last_synced_at, ActionPack.current_sha)
        .where(
            ActionPack.git_repository_id.in_([r.id for r in repos]),
            ActionPack.last_sync_status == "ok",
            ActionPack.last_synced_at.is_not(None),
        )
        .order_by(ActionPack.last_synced_at)
    )
    latest = {repo_id: (at, sha) for repo_id, at, sha in rows.all()}
    for resp in out:
        if resp.id in latest:
            at, sha = latest[resp.id]
            if resp.last_sync_at is None or at > resp.last_sync_at:
                resp.last_sync_at = at
                resp.last_commit_sha = sha or resp.last_commit_sha
    return out


@router.get("", response_model=list[GitRepoResponse])
async def list_git_repos(
    _: User = Depends(current_active_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(GitRepository).order_by(GitRepository.created_at.desc()))
    return await _with_pack_syncs(db, list(result.scalars().all()))


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
    )
    # SEC-28: encrypted at rest like the token beside it, never echoed back.
    set_webhook_secret(repo, body.webhook_secret)

    if body.https_token and auth_type == "https_token":
        master_key = get_master_key()
        repo.encrypted_https_token = encrypt_ssh_key(body.https_token, master_key)

    db.add(repo)
    await _flush_or_conflict(db)

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
    return (await _with_pack_syncs(db, [repo]))[0]


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
    # SEC-28: never assigned by the setattr loop below — the column is
    # ciphertext, and an omitted value means "keep the existing secret".
    webhook_secret = update_data.pop("webhook_secret", None)

    previous_url = repo.url
    for field, value in update_data.items():
        setattr(repo, field, value)

    # SEC-27: a pinned host key belongs to the server the URL names. If
    # the URL now points somewhere else, the old key can only produce a
    # spurious mismatch, so drop it and let the next sync re-TOFU.
    if extract_hostname(repo.url) != extract_hostname(previous_url):
        repo.ssh_host_key_entry = None

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

    if webhook_secret:
        set_webhook_secret(repo, webhook_secret)

    # A rename onto a taken name has no pre-check at all; flush here so it
    # answers 409 rather than failing at commit.
    await _flush_or_conflict(db)

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
    return (await _with_pack_syncs(db, [repo]))[0]


@router.post("/{repo_id}/trust-host-key", status_code=204)
async def trust_repo_host_key(
    repo_id: int,
    user: User = Depends(current_active_user),
    db: AsyncSession = Depends(get_db),
):
    """Clear the pinned SSH host key so the next sync re-TOFUs (SEC-27).

    The counterpart to ``POST /api/hosts/{id}/trust-host-key``. Use it
    when the git server was legitimately re-keyed — otherwise every sync
    fails with a host-key mismatch, which is the point. Emits an audit
    row, because accepting a new key for the repository LabDog takes
    playbooks from is a decision worth being able to look up later.
    """
    result = await db.execute(select(GitRepository).where(GitRepository.id == repo_id))
    repo = result.scalar_one_or_none()
    if not repo:
        raise HTTPException(status_code=404, detail="Git repository not found")

    repo.ssh_host_key_entry = None
    await log_action(
        db=db,
        action="trust_host_key",
        entity_type="git_repository",
        entity_id=repo.id,
        user_id=user.id,
    )
    await db.commit()


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
