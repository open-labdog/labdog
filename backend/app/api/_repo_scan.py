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
from app.schemas.repo_scan import (
    RepoActivateRequest,
    RepoActivateResponse,
    RepoScanResponse,
)

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


# ---------------------------------------------------------------------------
# Activation endpoint
# ---------------------------------------------------------------------------


@router.post("/{repo_id}/activate", response_model=RepoActivateResponse)
async def activate_repo(
    repo_id: int,
    body: RepoActivateRequest,
    user: User = Depends(current_superuser),
    db: AsyncSession = Depends(get_db),
) -> RepoActivateResponse:
    """Materialise operator-selected scan findings into ActionPack rows
    and HostGroup gitops bindings.

    Re-clones the repo and re-runs the scan to validate the operator's
    submission against the current HEAD — submissions referring to
    paths that no longer exist or producing intra-repo key conflicts
    are rejected with HTTP 409.

    Atomic: every pack insert + group rebind + audit row commits in a
    single transaction. After commit, ``sync_pack`` runs for each new
    pack so its checkout materialises immediately, then
    ``reload_registry_async`` once at the end. Failures in the
    post-commit hooks are logged but the rows persist.
    """
    from app.actions.registry import reload_registry_async
    from app.audit.logger import log_action
    from app.models.host_group import GitOpsStatus, HostGroup
    from app.packs.models import ActionPack, PackRole, PackSourceType
    from app.packs.service import sync_pack
    from app.schemas.repo_scan import ActivatedGitopsBindingOut, ActivatedPackOut

    repo = (
        await db.execute(select(GitRepository).where(GitRepository.id == repo_id))
    ).scalar_one_or_none()
    if repo is None:
        raise HTTPException(status_code=404, detail="GitRepository not found")

    user_id = user.id  # capture eagerly for post-rollback audit safety

    # Re-clone + re-scan against the current HEAD. Operator's submission
    # may be stale if the repo got pushed-to between scan and activate;
    # we validate against the live tree, not the version we showed them.
    try:
        ssh_key, token = await _decrypt_repo_credentials(db, repo)
    except Exception as exc:
        logger.warning("activate: credential decryption failed for %r: %s", repo.name, exc)
        raise HTTPException(
            status_code=502, detail="Could not decrypt repository credentials"
        ) from None

    clone_dir = Path(tempfile.mkdtemp(prefix="labdog-activate-"))
    try:
        try:
            with git_auth_context(ssh_private_key=ssh_key, token=token) as auth:
                head_sha = sync_remote_pack(repo.url, repo.branch, clone_dir, auth=auth)
        except (GitSyncError, ValueError) as exc:
            secrets = [s for s in (ssh_key, token) if s]
            scrubbed = redact(str(exc), secrets) or "clone failed"
            logger.warning("activate: clone failed for %r: %s", repo.name, scrubbed)
            raise HTTPException(status_code=502, detail=scrubbed) from None

        scan = scan_repository(clone_dir, repo_name=repo.name)
        # annotate_scan is called only for its side-effect of validating
        # that the scan tooling still works against this repo; the
        # submitted set itself is what we authorise on.
        await annotate_scan(db, scan)

        # Validate each submitted pack still exists in the scan.
        scanned_pack_paths = {p.path for p in scan.packs}
        missing_paths = [p.path for p in body.packs if p.path not in scanned_pack_paths]
        if missing_paths:
            raise HTTPException(
                status_code=409,
                detail={
                    "kind": "scan_drift",
                    "missing_pack_paths": missing_paths,
                    "message": (
                        "One or more selected packs are no longer present at HEAD. "
                        "Re-scan the repo and re-submit."
                    ),
                },
            )

        # Re-validate intra-repo conflicts against the *submitted* set —
        # if both sides of a conflict are still checked, reject.
        scanned_packs_by_path = {p.path: p for p in scan.packs}
        submitted_keys: dict[str, list[str]] = {}
        for sel in body.packs:
            for k in scanned_packs_by_path[sel.path].contributed_keys:
                submitted_keys.setdefault(k, []).append(sel.path)
        active_conflicts = {k: paths for k, paths in submitted_keys.items() if len(paths) >= 2}
        if active_conflicts:
            raise HTTPException(
                status_code=409,
                detail={
                    "kind": "intra_repo_key_conflict",
                    "conflicts": [
                        {"key": k, "contributing_packs": sorted(paths)}
                        for k, paths in sorted(active_conflicts.items())
                    ],
                    "message": (
                        "Submitted set still has intra-repo key conflicts. "
                        "Uncheck all but one contributing pack per key and re-submit."
                    ),
                },
            )

        # Validate gitops_bindings — group exists, not already bound to a
        # different repo, and the file_path is in the scanned set.
        scanned_gitops_paths = {g.path for g in scan.gitops_files}
        for binding in body.gitops_bindings:
            if binding.file_path not in scanned_gitops_paths:
                raise HTTPException(
                    status_code=409,
                    detail={
                        "kind": "gitops_file_missing",
                        "file_path": binding.file_path,
                    },
                )
            group = (
                await db.execute(select(HostGroup).where(HostGroup.id == binding.host_group_id))
            ).scalar_one_or_none()
            if group is None:
                raise HTTPException(
                    status_code=404,
                    detail=f"host_group_id={binding.host_group_id} does not exist",
                )
            if (
                group.git_repository_id is not None
                and group.git_repository_id != repo.id
                and group.gitops_enabled
            ):
                raise HTTPException(
                    status_code=409,
                    detail={
                        "kind": "group_bound_to_other_repo",
                        "host_group_id": group.id,
                        "current_repository_id": group.git_repository_id,
                        "message": (
                            f"Group {group.name!r} is already bound to a different "
                            "repository. Disable GitOps on it first."
                        ),
                    },
                )

        # Insert packs (with name-collision suffix logic).
        activated_packs: list[ActivatedPackOut] = []
        existing_names = {row[0] for row in (await db.execute(select(ActionPack.name))).all()}
        for sel in body.packs:
            final_name = await _disambiguate_pack_name(
                requested=sel.name,
                repo_name=repo.name,
                head_sha=head_sha or "",
                taken=existing_names,
            )
            pack_row = ActionPack(
                name=final_name,
                source_type=PackSourceType.GIT,
                git_repository_id=repo_id,
                path=sel.path,
                role=PackRole(sel.role),
                enabled=True,
            )
            db.add(pack_row)
            await db.flush()
            existing_names.add(final_name)

            await log_action(
                db=db,
                action="pack.activated",
                entity_type="action_pack",
                entity_id=pack_row.id,
                user_id=user_id,
                after_state={
                    "name": final_name,
                    "path": sel.path,
                    "role": sel.role,
                    "contributed_keys": list(scanned_packs_by_path[sel.path].contributed_keys),
                    "repo_id": repo_id,
                },
            )
            activated_packs.append(
                ActivatedPackOut(
                    pack_id=pack_row.id,
                    name=final_name,
                    path=sel.path,
                    role=sel.role,
                    requested_name=sel.name,
                    name_was_disambiguated=(final_name != sel.name),
                )
            )

        # Apply gitops bindings.
        activated_bindings: list[ActivatedGitopsBindingOut] = []
        for binding in body.gitops_bindings:
            group = (
                await db.execute(select(HostGroup).where(HostGroup.id == binding.host_group_id))
            ).scalar_one()
            before_state = {
                "gitops_enabled": group.gitops_enabled,
                "git_repository_id": group.git_repository_id,
                "gitops_file_path": group.gitops_file_path,
            }
            group.git_repository_id = repo_id
            group.gitops_enabled = True
            group.gitops_file_path = binding.file_path
            group.gitops_status = GitOpsStatus.disconnected

            await log_action(
                db=db,
                action="gitops.bound",
                entity_type="host_group",
                entity_id=group.id,
                user_id=user_id,
                before_state=before_state,
                after_state={
                    "gitops_enabled": True,
                    "git_repository_id": repo_id,
                    "gitops_file_path": binding.file_path,
                },
            )
            activated_bindings.append(
                ActivatedGitopsBindingOut(host_group_id=group.id, file_path=binding.file_path)
            )

        # One summary row at the end so an auditor looking for "who did
        # the bulk activation" finds a single entry.
        await log_action(
            db=db,
            action="repo.scan_activated",
            entity_type="git_repository",
            entity_id=repo_id,
            user_id=user_id,
            after_state={
                "pack_count": len(activated_packs),
                "binding_count": len(activated_bindings),
                "head_sha": head_sha,
            },
        )

        await db.commit()

        # Best-effort post-commit hooks. Failures are logged but the
        # activated rows are durable; operator can hit "Sync now" later.
        new_pack_ids = [ap.pack_id for ap in activated_packs]
        if new_pack_ids:
            for pack_id in new_pack_ids:
                pack = (
                    await db.execute(select(ActionPack).where(ActionPack.id == pack_id))
                ).scalar_one_or_none()
                if pack is not None:
                    try:
                        await sync_pack(db, pack)
                    except Exception:
                        logger.exception("post-activate sync_pack failed for pack_id=%s", pack_id)
            try:
                await reload_registry_async(db)
            except Exception:
                logger.exception("post-activate reload_registry_async failed")

        return RepoActivateResponse(
            activated_packs=activated_packs,
            activated_gitops_bindings=activated_bindings,
            head_sha=head_sha,
        )
    finally:
        shutil.rmtree(clone_dir, ignore_errors=True)


async def _disambiguate_pack_name(
    *,
    requested: str,
    repo_name: str,
    head_sha: str,
    taken: set[str],
) -> str:
    """Return a unique ``ActionPack.name`` for the requested input.

    Tries ``requested`` first, then ``requested-<repo_name>``, then
    ``requested-<short_sha>``. If all three collide (extremely unlikely),
    falls back to appending a counter. The set of taken names is
    mutated by the caller after each successful disambiguation.
    """
    if requested not in taken:
        return requested
    with_repo = f"{requested}-{repo_name}"
    if with_repo not in taken:
        return with_repo
    short_sha = head_sha[:7] if head_sha else "noref"
    with_sha = f"{requested}-{short_sha}"
    if with_sha not in taken:
        return with_sha
    counter = 2
    while f"{with_sha}-{counter}" in taken:
        counter += 1
    return f"{with_sha}-{counter}"
