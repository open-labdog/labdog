"""Git-backed pack synchronisation.

LabDog materialises configured action packs on disk by cloning or
pulling from a git remote. Works with public repos (no auth) as well
as private repos via an optional ``GitAuthContext`` from
``app.packs.git_auth``.

The contract is intentionally narrow: a given ``path`` is a checkout of a
given ``repo`` at a given ``ref``. LabDog owns the directory — on mismatch
it wipes and re-clones rather than trying to merge divergent state.
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
from pathlib import Path
from typing import TYPE_CHECKING

from app.packs.redact import redact

if TYPE_CHECKING:
    from app.packs.git_auth import GitAuthContext

logger = logging.getLogger(__name__)


class GitSyncError(RuntimeError):
    """Raised when a pack could not be synchronised. Always caller-logged."""


def _run_git(
    args: list[str],
    *,
    cwd: Path | None = None,
    auth: "GitAuthContext | None" = None,
) -> str:
    """Invoke git with optional auth context. Error output is scrubbed of
    any secrets the auth context declared before being raised."""
    pre_args = list(auth.extra_args) if auth else []
    cmd = ["git", *pre_args, *args]

    env = os.environ.copy()
    if auth and auth.extra_env:
        env.update(auth.extra_env)

    try:
        result = subprocess.run(
            cmd,
            cwd=str(cwd) if cwd else None,
            capture_output=True,
            text=True,
            check=True,
            timeout=120,
            env=env,
        )
    except FileNotFoundError as exc:
        raise GitSyncError("git executable not found on PATH") from exc
    except subprocess.TimeoutExpired as exc:
        raise GitSyncError(f"git {args[0]} timed out after 120s") from exc
    except subprocess.CalledProcessError as exc:
        raw = (exc.stderr or exc.stdout or "").strip()
        scrubbed = redact(raw, auth.redact_values if auth else None) or ""
        raise GitSyncError(
            f"git {args[0]} failed (rc={exc.returncode}): {scrubbed}"
        ) from exc
    return result.stdout


def _current_origin_url(path: Path) -> str | None:
    try:
        return _run_git(["config", "--get", "remote.origin.url"], cwd=path).strip()
    except GitSyncError:
        return None


def sync_remote_pack(
    repo: str,
    ref: str,
    path: Path,
    *,
    auth: "GitAuthContext | None" = None,
) -> str:
    """Ensure *path* is a checkout of *repo* at *ref*, cloning or updating.

    Returns the commit SHA at HEAD after the sync. Raises ``GitSyncError``
    on failure so callers can decide whether to fall back to existing
    on-disk state or surface the error.

    Strategy:
      * If *path* doesn't exist → ``git clone``.
      * If *path* exists and is a git repo pointing at the right origin →
        ``git fetch`` + ``git reset --hard FETCH_HEAD`` (atomic enough —
        we don't expect local edits in this directory).
      * If *path* exists with a different origin (or isn't a repo) →
        remove it and re-clone. LabDog owns the directory.
    """
    path = path.resolve()
    path.parent.mkdir(parents=True, exist_ok=True)

    if path.exists():
        if not (path / ".git").is_dir():
            logger.warning(
                "remote pack %s is not a git checkout; removing and re-cloning",
                path,
            )
            shutil.rmtree(path)
        else:
            existing_origin = _current_origin_url(path)
            if existing_origin != repo:
                logger.warning(
                    "remote pack %s points at %r, wanted %r; re-cloning",
                    path,
                    existing_origin,
                    repo,
                )
                shutil.rmtree(path)

    if not path.exists():
        logger.info("cloning remote pack %s@%s → %s", repo, ref, path)
        _run_git(
            ["clone", "--depth", "1", "--branch", ref, repo, str(path)],
            auth=auth,
        )
    else:
        logger.info("updating remote pack %s@%s at %s", repo, ref, path)
        _run_git(
            ["fetch", "--depth", "1", "origin", ref], cwd=path, auth=auth
        )
        _run_git(["reset", "--hard", "FETCH_HEAD"], cwd=path, auth=auth)

    return _run_git(["rev-parse", "HEAD"], cwd=path).strip()


def ls_remote(
    repo: str,
    ref: str,
    *,
    auth: "GitAuthContext | None" = None,
) -> str:
    """Resolve *ref* at *repo* without checking out anything.

    Returns the commit SHA the ref currently points at. Used by the
    connection-test endpoint to validate credentials and ref existence
    cheaply (no disk touched, one git-protocol round-trip).
    """
    out = _run_git(["ls-remote", "--exit-code", repo, ref], auth=auth)
    first = out.splitlines()[0] if out else ""
    sha = first.split()[0] if first else ""
    if not sha:
        raise GitSyncError(f"ref {ref!r} not found at {repo}")
    return sha
