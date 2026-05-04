"""Repo-scan + repo-activate endpoints.

Lives in its own module instead of being appended to
``app/api/git_repos.py`` so the original (heavily-tested) repo-CRUD
file stays small and these auto-detect endpoints can evolve freely.
The router is mounted under the same ``/api/git-repos`` prefix in
``app/main.py``.

C3 ships ``POST /api/git-repos/{repo_id}/scan``. C4 will append
``POST /api/git-repos/{repo_id}/activate`` to this same module.
"""

from __future__ import annotations

import logging
import shutil
import tempfile
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.actions.git_sync import GitSyncError, sync_remote_pack
from app.auth.users import current_superuser
from app.db import get_db
from app.models.git_repository import GitRepository
from app.models.user import User
from app.packs.git_auth import git_auth_context
from app.packs.redact import redact
from app.packs.repo_scanner import scan_repository
from app.packs.scan_conflicts import annotate_scan
from app.packs.service import _decrypt_repo_credentials
from app.schemas.repo_scan import RepoScanResponse

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/git-repos", tags=["git-repos"])


@router.post("/{repo_id}/scan", response_model=RepoScanResponse)
async def scan_repo(
    repo_id: int,
    _: User = Depends(current_superuser),
    db: AsyncSession = Depends(get_db),
) -> RepoScanResponse:
    """Clone the repo into a tmpdir, walk it for action packs and
    gitops files, and return the findings annotated with conflict info.

    Idempotent — multiple calls produce the same response (the clone
    is throw-away). Cleanup happens in ``finally``. Failures during
    clone surface as HTTP 502 with credentials redacted from the
    error message.
    """
    repo = (
        await db.execute(select(GitRepository).where(GitRepository.id == repo_id))
    ).scalar_one_or_none()
    if repo is None:
        raise HTTPException(status_code=404, detail="GitRepository not found")

    try:
        ssh_key, token = await _decrypt_repo_credentials(db, repo)
    except Exception as exc:
        # Credential decryption failure — the master key is missing or
        # the linked SSHKey row is gone. Don't reveal anything beyond
        # the failure mode.
        logger.warning("scan: credential decryption failed for repo %r: %s", repo.name, exc)
        raise HTTPException(
            status_code=502,
            detail="Could not decrypt repository credentials",
        ) from None

    clone_dir = Path(tempfile.mkdtemp(prefix="labdog-scan-"))
    try:
        try:
            with git_auth_context(ssh_private_key=ssh_key, token=token) as auth:
                head_sha = sync_remote_pack(repo.url, repo.branch, clone_dir, auth=auth)
        except (GitSyncError, ValueError) as exc:
            secrets = [s for s in (ssh_key, token) if s]
            scrubbed = redact(str(exc), secrets) or "clone failed"
            logger.warning("scan: clone failed for repo %r: %s", repo.name, scrubbed)
            raise HTTPException(status_code=502, detail=scrubbed) from None

        result = scan_repository(clone_dir, repo_name=repo.name)
        annotated = await annotate_scan(db, result)

        return RepoScanResponse(
            packs=[_pack_out(p) for p in annotated.base.packs],
            gitops_files=[_gitops_out(g) for g in annotated.base.gitops_files],
            existing_key_winners={
                k: _owner_out(v) for k, v in annotated.existing_key_winners.items()
            },
            intra_repo_key_conflicts=[_conflict_out(c) for c in annotated.intra_repo_key_conflicts],
            scan_errors=[_err_out(e) for e in annotated.base.scan_errors],
            head_sha=head_sha,
        )
    finally:
        shutil.rmtree(clone_dir, ignore_errors=True)


# ---------------------------------------------------------------------------
# Internal: dataclass → Pydantic helpers. Frozen dataclasses don't satisfy
# ``from_attributes`` cleanly across all pydantic versions, so we map
# explicitly.
# ---------------------------------------------------------------------------


def _err_out(err):
    from app.schemas.repo_scan import ScanErrorOut

    return ScanErrorOut(file=err.file, message=err.message)


def _pack_out(pack):
    from app.schemas.repo_scan import DetectedPackOut

    return DetectedPackOut(
        path=pack.path,
        name=pack.name,
        contributed_keys=list(pack.contributed_keys),
        pack_yml_present=pack.pack_yml_present,
        errors=[_err_out(e) for e in pack.errors],
    )


def _gitops_out(gitops):
    from app.schemas.repo_scan import DetectedGitopsFileOut

    return DetectedGitopsFileOut(
        path=gitops.path,
        group_name=gitops.group_name,
        errors=[_err_out(e) for e in gitops.errors],
    )


def _owner_out(owner):
    from app.schemas.repo_scan import KeyOwnerOut

    return KeyOwnerOut(
        key=owner.key,
        source=owner.source,
        pack_name=owner.pack_name,
        pack_id=owner.pack_id,
    )


def _conflict_out(conflict):
    from app.schemas.repo_scan import KeyConflictOut

    return KeyConflictOut(
        key=conflict.key,
        contributing_packs=list(conflict.contributing_packs),
    )
