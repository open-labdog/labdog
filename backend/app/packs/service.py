"""Action pack sync orchestration.

Bridges the DB (``ActionPack`` + linked ``GitRepository``) and the
on-disk pack loader (``app.actions.packs``). Responsibilities:

- For git packs, resolve the linked ``GitRepository`` + its credentials
  (via the ``SSHKey`` table for SSH auth or the repo's own encrypted
  HTTPS token), build a ``GitAuthContext``, and clone/pull through
  ``app.actions.git_sync``.
- For local packs, verify the configured filesystem path is shaped
  like a pack.
- Persist sync outcomes (status, sha, error) to the DB, scrubbed of
  any secret material.
- Produce ``Pack`` objects (rooted at the checkout + subpath) for the
  registry loader to scan.

Callers (API endpoints, FastAPI lifespan, Celery worker startup) should
use the high-level helpers: ``sync_pack``, ``sync_enabled_packs``,
``load_db_packs``, ``delete_checkout``.
"""

from __future__ import annotations

import logging
import shutil
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.actions.git_sync import GitSyncError, sync_remote_pack
from app.actions.packs import Pack
from app.config import settings
from app.crypto import decrypt_ssh_key, get_master_key
from app.models.git_repository import GitAuthType, GitRepository
from app.models.ssh_key import SSHKey
from app.packs.git_auth import git_auth_context
from app.packs.models import ActionPack, PackSourceType
from app.packs.redact import redact

logger = logging.getLogger(__name__)


def derive_priority(pack: ActionPack) -> int:
    """Map a pack's row to its numeric load priority.

    Bundled is implicit at 0 via ``BUNDLED_PACK_PRIORITY`` in
    ``app.actions.registry``; every DB pack starts at ``position + 1``
    so the lowest-positioned DB pack still beats bundled.
    """
    return pack.position + 1


def checkout_path_for(pack_id: int) -> Path:
    """Deterministic on-disk location for a git pack's checkout.

    Local packs use their configured ``local_path`` directly — see
    ``effective_path_for``.
    """
    return Path(settings.ansible.packs_root_dir) / str(pack_id)


def effective_path_for(pack: ActionPack) -> Path:
    """Where the pack's manifests actually live on disk.

    ``git`` → ``<checkout>/<pack.path>`` — subpath within the managed
    checkout under ``packs_root_dir``.
    ``local`` → ``<pack.local_path>`` — the admin-supplied filesystem
    path, used in place (nothing is cloned).
    """
    if pack.source_type == PackSourceType.LOCAL:
        return Path(pack.local_path or "")
    checkout = checkout_path_for(pack.id)
    subpath = pack.path.strip("/")
    return checkout / subpath if subpath else checkout


async def _decrypt_repo_credentials(
    db: AsyncSession, repo: GitRepository
) -> tuple[str | None, str | None]:
    """Return ``(ssh_private_key, token)`` decrypted from the linked
    ``GitRepository``.

    For SSH auth, this resolves ``repo.ssh_key_id`` against the ``SSHKey``
    table and decrypts ``encrypted_private_key``. For HTTPS, decrypts
    ``repo.encrypted_https_token``. Returns ``(None, None)`` when the
    repo has no auth configured.
    """
    if repo.auth_type == GitAuthType.ssh_key:
        if repo.ssh_key_id is None:
            return None, None
        result = await db.execute(select(SSHKey).where(SSHKey.id == repo.ssh_key_id))
        ssh_key = result.scalar_one_or_none()
        if ssh_key is None:
            raise ValueError(
                f"GitRepository {repo.name!r} references ssh_key_id="
                f"{repo.ssh_key_id} which no longer exists"
            )
        key = decrypt_ssh_key(ssh_key.encrypted_private_key, get_master_key())
        return key, None
    if repo.auth_type == GitAuthType.https_token:
        if repo.encrypted_https_token is None:
            return None, None
        token = decrypt_ssh_key(repo.encrypted_https_token, get_master_key())
        return None, token
    return None, None


async def sync_pack(
    db: AsyncSession,
    pack: ActionPack,
    *,
    commit: bool = True,
) -> bool:
    """Perform a full sync of *pack*, persisting the outcome.

    Returns True on success, False on any failure (row is still updated
    with the failure reason). Never raises — the whole point is that a
    failing pack shouldn't take down the caller.

    For ``local`` packs this is a no-op on disk — it just verifies the
    configured path looks like a pack (exists + has ``actions/``) and
    records that outcome. LabDog does not own or modify the directory.
    """
    if pack.source_type == PackSourceType.LOCAL:
        return await _verify_local_pack(db, pack, commit=commit)

    # source = git — load the linked GitRepository + credentials.
    if pack.git_repository_id is None:
        return await _record_failure(
            db,
            pack,
            "git pack is not linked to a GitRepository",
            commit=commit,
        )
    repo_result = await db.execute(
        select(GitRepository).where(GitRepository.id == pack.git_repository_id)
    )
    repo = repo_result.scalar_one_or_none()
    if repo is None:
        return await _record_failure(
            db,
            pack,
            f"linked GitRepository id={pack.git_repository_id} no longer exists",
            commit=commit,
        )

    path = checkout_path_for(pack.id)
    path.parent.mkdir(parents=True, exist_ok=True)

    try:
        ssh_key, token = await _decrypt_repo_credentials(db, repo)
    except Exception as exc:
        return await _record_failure(
            db, pack, f"credential decryption failed: {exc}", commit=commit
        )

    try:
        with git_auth_context(ssh_private_key=ssh_key, token=token) as auth:
            sha = sync_remote_pack(repo.url, repo.branch, path, auth=auth)
    except (GitSyncError, ValueError) as exc:
        secrets = [s for s in (ssh_key, token) if s]
        scrubbed = redact(str(exc), secrets)
        logger.warning("pack %r sync failed: %s", pack.name, scrubbed)
        pack.last_sync_status = "failed"
        pack.last_sync_error = scrubbed
        pack.last_synced_at = datetime.now(UTC)
        if commit:
            await db.commit()
        return False

    pack.current_sha = sha
    pack.last_sync_status = "ok"
    pack.last_sync_error = None
    pack.last_synced_at = datetime.now(UTC)
    if commit:
        await db.commit()
    logger.info("pack %r synced @ %s", pack.name, sha[:8])
    return True


async def _verify_local_pack(db: AsyncSession, pack: ActionPack, *, commit: bool) -> bool:
    """Smoke-check a local pack's path and record the outcome."""
    path = Path(pack.local_path or "")
    ok = path.is_dir() and (path / "actions").is_dir()
    pack.current_sha = None
    pack.last_synced_at = datetime.now(UTC)
    if ok:
        pack.last_sync_status = "ok"
        pack.last_sync_error = None
        logger.info("pack %r (local) verified at %s", pack.name, path)
    else:
        pack.last_sync_status = "failed"
        pack.last_sync_error = f"local path {path} does not exist or lacks an actions/ directory"
        logger.warning("pack %r (local) verification failed: %s", pack.name, path)
    if commit:
        await db.commit()
    return ok


async def _record_failure(
    db: AsyncSession, pack: ActionPack, message: str, *, commit: bool
) -> bool:
    logger.warning("pack %r sync failed: %s", pack.name, message)
    pack.last_sync_status = "failed"
    pack.last_sync_error = message
    pack.last_synced_at = datetime.now(UTC)
    if commit:
        await db.commit()
    return False


async def sync_enabled_packs(db: AsyncSession) -> list[tuple[ActionPack, bool]]:
    """Sync every enabled pack. Returns [(pack, success), ...]."""
    result = await db.execute(
        select(ActionPack).where(ActionPack.enabled.is_(True)).order_by(ActionPack.id)
    )
    packs = list(result.scalars().all())
    outcomes: list[tuple[ActionPack, bool]] = []
    for pack in packs:
        ok = await sync_pack(db, pack, commit=False)
        outcomes.append((pack, ok))
    await db.commit()
    return outcomes


async def load_db_packs(db: AsyncSession) -> list[Pack]:
    """Return ``Pack`` objects for every enabled pack with a readable path.

    For git packs that's a successful checkout under ``packs_root_dir``
    (possibly narrowed by the pack's subpath); for local packs that's
    the admin-supplied filesystem path. Missing or empty paths are
    skipped — the registry is built best-effort.
    """
    result = await db.execute(
        select(ActionPack).where(ActionPack.enabled.is_(True)).order_by(ActionPack.id)
    )
    packs: list[Pack] = []
    for row in result.scalars().all():
        path = effective_path_for(row)
        if not path.is_dir():
            continue
        packs.append(
            Pack(
                name=row.name,
                path=path,
                priority=derive_priority(row),
                pack_id=row.id,
            )
        )
    return packs


def delete_checkout(pack_id: int) -> None:
    """Remove a git pack's managed checkout. Silent on missing; logs on error.

    Never called for local packs — LabDog doesn't own the directory.
    """
    path = checkout_path_for(pack_id)
    if not path.exists():
        return
    try:
        shutil.rmtree(path)
    except OSError:
        logger.warning("failed to delete pack checkout %s", path, exc_info=True)
