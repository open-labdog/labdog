import hashlib
import hmac
import logging

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_db
from app.models.git_repository import GitRepository
from app.models.host_group import HostGroup
from app.tasks import celery_app

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/webhooks", tags=["webhooks"])


def _verify_github_signature(
    body: bytes, secret: str, signature_header: str | None,
) -> bool:
    if not signature_header or not signature_header.startswith("sha256="):
        return False
    expected = "sha256=" + hmac.new(
        secret.encode(), body, hashlib.sha256,
    ).hexdigest()
    return hmac.compare_digest(expected, signature_header)


def _verify_gitea_signature(
    body: bytes, secret: str, signature_header: str | None,
) -> bool:
    if not signature_header:
        return False
    expected = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature_header)


def _verify_gitlab_token(secret: str, token_header: str | None) -> bool:
    if not token_header:
        return False
    return hmac.compare_digest(secret, token_header)


async def _find_repo_by_url(url: str, db: AsyncSession) -> GitRepository | None:
    """Try matching clone_url, ssh_url, or html_url variants."""
    result = await db.execute(select(GitRepository).where(GitRepository.url == url))
    repo = result.scalar_one_or_none()
    if repo:
        return repo
    # Try stripping .git suffix or adding it
    alt_url = url.rstrip(".git") if url.endswith(".git") else url + ".git"
    result = await db.execute(
        select(GitRepository).where(GitRepository.url == alt_url),
    )
    return result.scalar_one_or_none()


async def _dispatch_webhook(
    repo: GitRepository, commit_sha: str, db: AsyncSession,
) -> None:
    """Find all groups linked to this repo with gitops enabled and dispatch."""
    result = await db.execute(
        select(HostGroup).where(
            HostGroup.git_repository_id == repo.id,
            HostGroup.gitops_enabled == True,  # noqa: E712
        ),
    )
    groups = result.scalars().all()
    if not groups:
        logger.info(f"Webhook for repo {repo.name}: no gitops-enabled groups")
        return

    celery_app.send_task(
        "gitops.process_webhook",
        kwargs={"repo_id": repo.id, "commit_sha": commit_sha},
    )
    logger.info(
        f"Dispatched gitops webhook task for repo {repo.name}, "
        f"SHA {commit_sha[:8]}",
    )


@router.post("/github")
async def github_webhook(
    request: Request, db: AsyncSession = Depends(get_db),
):
    body = await request.body()
    payload = await request.json()

    # Find repo by URL
    repo_url = payload.get("repository", {}).get("clone_url", "") or payload.get(
        "repository", {},
    ).get("ssh_url", "")
    repo = await _find_repo_by_url(repo_url, db)
    if not repo:
        return {"status": "ignored", "reason": "unknown repository"}

    # Verify signature
    if repo.webhook_secret:
        sig = request.headers.get("X-Hub-Signature-256")
        if not _verify_github_signature(body, repo.webhook_secret, sig):
            raise HTTPException(status_code=401, detail="Invalid signature")

    # Check event type
    event = request.headers.get("X-GitHub-Event", "")
    if event != "push":
        return {"status": "ignored", "reason": f"event type: {event}"}

    # Check for branch deletion
    ref = payload.get("ref", "")
    after = payload.get("after", "")
    deleted = payload.get("deleted", False)
    if deleted or after == "0" * 40:
        return {"status": "ignored", "reason": "branch deletion"}

    # Check branch matches
    expected_ref = f"refs/heads/{repo.branch}"
    if ref != expected_ref:
        return {"status": "ignored", "reason": f"branch mismatch: {ref}"}

    await _dispatch_webhook(repo, after, db)
    return {"status": "accepted"}


@router.post("/gitlab")
async def gitlab_webhook(
    request: Request, db: AsyncSession = Depends(get_db),
):
    payload = await request.json()

    repo_url = payload.get("project", {}).get(
        "git_http_url", "",
    ) or payload.get("project", {}).get("git_ssh_url", "")
    repo = await _find_repo_by_url(repo_url, db)
    if not repo:
        return {"status": "ignored", "reason": "unknown repository"}

    # Verify token
    if repo.webhook_secret:
        token = request.headers.get("X-Gitlab-Token")
        if not _verify_gitlab_token(repo.webhook_secret, token):
            raise HTTPException(status_code=401, detail="Invalid token")

    # Check for branch deletion
    after = payload.get("checkout_sha") or payload.get("after", "")
    if not after or after == "0" * 40:
        return {"status": "ignored", "reason": "branch deletion"}

    ref = payload.get("ref", "")
    expected_ref = f"refs/heads/{repo.branch}"
    if ref != expected_ref:
        return {"status": "ignored", "reason": f"branch mismatch: {ref}"}

    await _dispatch_webhook(repo, after, db)
    return {"status": "accepted"}


@router.post("/gitea")
async def gitea_webhook(
    request: Request, db: AsyncSession = Depends(get_db),
):
    body = await request.body()
    payload = await request.json()

    repo_url = payload.get("repository", {}).get("clone_url", "") or payload.get(
        "repository", {},
    ).get("ssh_url", "")
    repo = await _find_repo_by_url(repo_url, db)
    if not repo:
        return {"status": "ignored", "reason": "unknown repository"}

    if repo.webhook_secret:
        sig = request.headers.get("X-Gitea-Signature")
        if not _verify_gitea_signature(body, repo.webhook_secret, sig):
            raise HTTPException(status_code=401, detail="Invalid signature")

    ref = payload.get("ref", "")
    after = payload.get("after", "")
    if after == "0" * 40:
        return {"status": "ignored", "reason": "branch deletion"}

    expected_ref = f"refs/heads/{repo.branch}"
    if ref != expected_ref:
        return {"status": "ignored", "reason": f"branch mismatch: {ref}"}

    await _dispatch_webhook(repo, after, db)
    return {"status": "accepted"}
