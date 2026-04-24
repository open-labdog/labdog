"""Action pack sync orchestration.

Bridges the DB (``ActionPack`` rows) and the on-disk pack loader
(``app.actions.packs``). Responsibilities:

- Decrypt credentials + build a ``GitAuthContext``.
- Sync the checkout via ``app.actions.git_sync``.
- Persist sync outcomes (status, sha, error) to the DB, scrubbed of
  any secret material.
- Produce ``Pack`` objects for the registry to scan.

Callers (API endpoints, FastAPI lifespan, Celery worker startup) should
use the high-level helpers: ``sync_pack``, ``sync_enabled_packs``,
``rebuild_registry``, ``delete_checkout``.
"""

from __future__ import annotations

import logging
import shutil
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.actions.git_sync import GitSyncError, ls_remote, sync_remote_pack
from app.actions.packs import Pack
from app.config import settings
from app.crypto import decrypt_ssh_key, get_master_key
from app.packs.git_auth import git_auth_context
from app.packs.models import ActionPack, PackAuthType, PackRole, PackSourceType
from app.packs.redact import redact

logger = logging.getLogger(__name__)


#: Load-order tiers (bigger = wins on collision). Bundled stays at 0
#: via ``BUNDLED_PACK_PRIORITY`` in ``app.actions.registry``.
PRIORITY_DEFAULT_GIT = 10
PRIORITY_OVERRIDE_GIT = 100
PRIORITY_LOCAL = 1000


def derive_priority(pack: ActionPack) -> int:
    """Map a pack's source+role to its numeric load priority.

    Callers of the pack loader only see the derived integer; the DB
    stores semantic attributes and admins pick them from a finite set
    in the UI. Integer priorities are an internal implementation
    detail.
    """
    if pack.source_type == PackSourceType.LOCAL:
        return PRIORITY_LOCAL
    if pack.role == PackRole.DEFAULT:
        return PRIORITY_DEFAULT_GIT
    return PRIORITY_OVERRIDE_GIT


def checkout_path_for(pack_id: int) -> Path:
    """Deterministic on-disk location for a git pack's checkout.

    Local packs use their configured ``repo_url`` directly — see
    ``effective_path_for``.
    """
    return Path(settings.ansible.packs_root_dir) / str(pack_id)


def effective_path_for(pack: ActionPack) -> Path:
    """Where the pack's manifests actually live on disk.

    ``git`` → the managed checkout under ``packs_root_dir``.
    ``local`` → the admin-supplied filesystem path.
    """
    if pack.source_type == PackSourceType.LOCAL:
        return Path(pack.repo_url)
    return checkout_path_for(pack.id)


def _decrypt_credentials(pack: ActionPack) -> tuple[str | None, str | None]:
    """Return ``(ssh_private_key, token)`` decrypted from the row.

    Returns the appropriate field based on ``auth_type``; the other is
    always ``None``. Raises on cryptographic failure — caller handles.
    """
    if pack.auth_type == PackAuthType.SSH and pack.encrypted_ssh_key is not None:
        key = decrypt_ssh_key(pack.encrypted_ssh_key, get_master_key())
        return key, None
    if pack.auth_type == PackAuthType.HTTPS_TOKEN and pack.encrypted_token is not None:
        token = decrypt_ssh_key(pack.encrypted_token, get_master_key())
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

    path = checkout_path_for(pack.id)
    path.parent.mkdir(parents=True, exist_ok=True)

    ssh_key, token = _decrypt_credentials(pack)
    try:
        with git_auth_context(
            auth_type=pack.auth_type,
            ssh_private_key=ssh_key,
            ssh_known_hosts=pack.ssh_known_hosts,
            token=token,
        ) as auth:
            sha = sync_remote_pack(pack.repo_url, pack.ref, path, auth=auth)
    except (GitSyncError, ValueError) as exc:
        # ValueError is raised by git_auth_context for missing creds /
        # missing known_hosts; treat it the same as a sync failure so
        # the admin sees the message in the UI.
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
    path = Path(pack.repo_url)
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


async def test_pack_credentials(
    *,
    source_type: PackSourceType = PackSourceType.GIT,
    repo_url: str,
    ref: str,
    auth_type: PackAuthType,
    ssh_private_key: str | None = None,
    ssh_known_hosts: str | None = None,
    token: str | None = None,
) -> tuple[bool, str, str | None]:
    """Validate a (prospective) pack config.

    For ``git`` source this runs ``git ls-remote`` to confirm auth works
    and the ref exists. For ``local`` source it just checks the path
    looks like a pack. Returns ``(success, message, commit_sha)``.
    """
    if source_type == PackSourceType.LOCAL:
        path = Path(repo_url)
        if not path.is_dir():
            return False, f"path {path} is not a directory", None
        if not (path / "actions").is_dir():
            return (
                False,
                f"path {path} is missing an actions/ directory",
                None,
            )
        return True, f"Local pack found at {path}", None

    try:
        with git_auth_context(
            auth_type=auth_type,
            ssh_private_key=ssh_private_key,
            ssh_known_hosts=ssh_known_hosts,
            token=token,
        ) as auth:
            sha = ls_remote(repo_url, ref, auth=auth)
    except (GitSyncError, ValueError) as exc:
        secrets = [s for s in (ssh_private_key, token) if s]
        return False, redact(str(exc), secrets) or str(exc), None
    return True, f"Resolved {ref} at {repo_url}", sha


async def load_db_packs(db: AsyncSession) -> list[Pack]:
    """Return ``Pack`` objects for every enabled pack with a readable path.

    For git packs that's a successful checkout under ``packs_root_dir``;
    for local packs that's the admin-supplied filesystem path. Missing
    or empty paths are skipped — the registry is built best-effort.
    """
    result = await db.execute(
        select(ActionPack).where(ActionPack.enabled.is_(True)).order_by(ActionPack.id)
    )
    packs: list[Pack] = []
    for row in result.scalars().all():
        path = effective_path_for(row)
        if not path.is_dir():
            continue
        packs.append(Pack(name=row.name, path=path, priority=derive_priority(row)))
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
