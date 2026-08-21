import hashlib
import hmac
import logging

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db import get_db
from app.models.git_repository import GitRepository
from app.models.host_group import HostGroup
from app.tasks import celery_app

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/webhooks", tags=["webhooks"])


def _verify_github_signature(
    body: bytes,
    secret: str,
    signature_header: str | None,
) -> bool:
    if not signature_header or not signature_header.startswith("sha256="):
        return False
    expected = (
        "sha256="
        + hmac.new(
            secret.encode(),
            body,
            hashlib.sha256,
        ).hexdigest()
    )
    return hmac.compare_digest(expected, signature_header)


def _verify_gitea_signature(
    body: bytes,
    secret: str,
    signature_header: str | None,
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
    alt_url = url.removesuffix(".git") if url.endswith(".git") else url + ".git"
    result = await db.execute(
        select(GitRepository).where(GitRepository.url == alt_url),
    )
    return result.scalar_one_or_none()


async def _dispatch_webhook(
    repo: GitRepository,
    commit_sha: str,
    db: AsyncSession,
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
        f"Dispatched gitops webhook task for repo {repo.name}, SHA {commit_sha[:8]}",
    )


@router.post("/github")
async def github_webhook(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    body = await request.body()
    payload = await request.json()

    # Find repo by URL
    repo_url = payload.get("repository", {}).get("clone_url", "") or payload.get(
        "repository",
        {},
    ).get("ssh_url", "")
    repo = await _find_repo_by_url(repo_url, db)
    if not repo:
        return {"status": "ignored", "reason": "unknown repository"}

    # Verify signature
    if not repo.webhook_secret:
        raise HTTPException(status_code=401, detail="Webhook secret not configured")
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
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    payload = await request.json()

    repo_url = payload.get("project", {}).get(
        "git_http_url",
        "",
    ) or payload.get("project", {}).get("git_ssh_url", "")
    repo = await _find_repo_by_url(repo_url, db)
    if not repo:
        return {"status": "ignored", "reason": "unknown repository"}

    # Verify token
    if not repo.webhook_secret:
        raise HTTPException(status_code=401, detail="Webhook secret not configured")
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
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    body = await request.body()
    payload = await request.json()

    repo_url = payload.get("repository", {}).get("clone_url", "") or payload.get(
        "repository",
        {},
    ).get("ssh_url", "")
    repo = await _find_repo_by_url(repo_url, db)
    if not repo:
        return {"status": "ignored", "reason": "unknown repository"}

    if not repo.webhook_secret:
        raise HTTPException(status_code=401, detail="Webhook secret not configured")
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


def _authorized(request: Request) -> bool:
    """Constant-time check of the shared alert-webhook token.

    Grafana contact points can send either an ``Authorization: Bearer``
    header or HTTP basic auth; both are accepted, because which one is
    available depends on the Grafana version and how the contact point
    was created. An unset token refuses everything — see
    :class:`app.config.AlertsConfig`.
    """
    expected = settings.alerts.webhook_token
    if not expected:
        return False

    header = request.headers.get("Authorization") or ""
    if header.startswith("Bearer "):
        return hmac.compare_digest(expected, header[len("Bearer ") :])
    if header.startswith("Basic "):
        import base64

        try:
            decoded = base64.b64decode(header[len("Basic ") :]).decode()
        except Exception:
            return False
        # Grafana sends user:password; the token may be either half, so
        # that an operator can put it wherever their contact-point form
        # makes available.
        user, _, password = decoded.partition(":")
        return hmac.compare_digest(expected, password) or hmac.compare_digest(expected, user)
    return False


@router.post("/grafana-alerts")
async def grafana_alerts_webhook(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Receive alerts from a Grafana contact point.

    Records every alert in the payload and returns immediately. Deciding
    whether any of them is worth an AI investigation happens in a Celery
    task, per alert — Grafana retries a webhook that does not answer
    quickly, and doing policy work inline would turn a slow provider into
    duplicate notifications.

    Returns 200 with a count even when nothing was eligible. A webhook
    that returns an error for "recorded, decided not to investigate"
    teaches the sender to retry something that already succeeded.
    """
    from app.ai.alerts import from_grafana_webhook, record
    from app.settings_service import get_setting_typed

    if not _authorized(request):
        raise HTTPException(status_code=401, detail="Invalid or missing webhook token")

    if not int(await get_setting_typed("ai.alert_intake_enabled", db)):
        return {"status": "ignored", "reason": "alert intake is disabled"}

    try:
        payload = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Body is not JSON") from None
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="Expected a JSON object")

    alerts = from_grafana_webhook(payload)
    if not alerts:
        # A payload LabDog could not parse into a single usable alert is
        # worth saying out loud — silently accepting it would look like
        # working while nothing was recorded.
        logger.info("grafana-alerts: payload contained no usable alerts")
        return {"status": "accepted", "recorded": 0, "investigating": 0}

    new_ids: list[int] = []
    for alert in alerts:
        event, created = await record(db, alert, source="grafana_webhook")
        if created and alert.is_firing:
            new_ids.append(event.id)
    await db.commit()

    for event_id in new_ids:
        celery_app.send_task(
            "app.tasks.ai_alerts.investigate_alert", kwargs={"alert_event_id": event_id}
        )

    logger.info(
        "grafana-alerts: recorded %d alert(s), %d new firing",
        len(alerts),
        len(new_ids),
    )
    return {"status": "accepted", "recorded": len(alerts), "investigating": len(new_ids)}
