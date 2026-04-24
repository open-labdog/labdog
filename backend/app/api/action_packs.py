"""CRUD + sync + test endpoints for action packs.

Mirrors the ``api/proxmox_nodes.py`` structure: superuser-only,
audit-logged on writes, token secrets encrypted with the same pipeline
used for Proxmox tokens.

The response schemas never include raw encrypted bytes or plaintext
credentials. Edit flows follow the "omit to keep, set to replace"
convention established by ProxmoxNodeUpdate.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.actions.registry import reload_registry_async
from app.audit.logger import log_action
from app.auth.users import current_superuser
from app.crypto import encrypt_ssh_key, get_master_key
from app.db import get_db
from app.models.user import User
from app.packs.models import ActionPack, PackAuthType, PackSourceType
from app.packs.schemas import (
    ActionPackCreate,
    ActionPackResponse,
    ActionPackSyncResponse,
    ActionPackTestRequest,
    ActionPackTestResponse,
    ActionPackUpdate,
)
from app.packs.service import (
    delete_checkout,
    sync_pack,
    test_pack_credentials,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/action-packs", tags=["action-packs"])


def _audit_snapshot(pack: ActionPack) -> dict:
    """Audit-log projection. Never includes credential bytes."""
    return {
        "name": pack.name,
        "source_type": pack.source_type.value,
        "repo_url": pack.repo_url,
        "ref": pack.ref,
        "role": pack.role.value,
        "enabled": pack.enabled,
        "auth_type": pack.auth_type.value,
    }


def _apply_create(body: ActionPackCreate, pack: ActionPack) -> None:
    master_key = get_master_key()
    pack.name = body.name
    pack.source_type = body.source_type
    pack.repo_url = body.repo_url
    pack.ref = body.ref
    pack.role = body.role
    pack.enabled = body.enabled
    pack.auth_type = body.auth_type
    pack.ssh_known_hosts = body.ssh_known_hosts
    if body.ssh_private_key:
        pack.encrypted_ssh_key = encrypt_ssh_key(body.ssh_private_key, master_key)
    if body.token:
        pack.encrypted_token = encrypt_ssh_key(body.token, master_key)


def _apply_update(body: ActionPackUpdate, pack: ActionPack) -> tuple[bool, bool]:
    """Mutate *pack* with any non-None fields from *body*.

    Returns ``(needs_resync, drop_git_checkout)``:
      * needs_resync — caller triggers sync_pack + registry reload.
      * drop_git_checkout — pack just switched from git to local; the
        old managed checkout is orphaned and should be removed.
    """
    master_key = get_master_key()
    needs_resync = False
    drop_git_checkout = False

    if body.name is not None and body.name != pack.name:
        pack.name = body.name
    if body.source_type is not None and body.source_type != pack.source_type:
        if pack.source_type == PackSourceType.GIT:
            drop_git_checkout = True
        pack.source_type = body.source_type
        needs_resync = True
    if body.repo_url is not None and body.repo_url != pack.repo_url:
        pack.repo_url = body.repo_url
        needs_resync = True
    if body.ref is not None and body.ref != pack.ref:
        pack.ref = body.ref
        needs_resync = True
    if body.role is not None:
        pack.role = body.role
    if body.enabled is not None and body.enabled != pack.enabled:
        pack.enabled = body.enabled
        needs_resync = True

    # auth transitions: if switching auth_type, clear the now-irrelevant
    # credential column. The schema's mutex validator guarantees the new
    # credential was supplied in the same request (for switches away from
    # none → ssh / https_token).
    if body.auth_type is not None and body.auth_type != pack.auth_type:
        pack.auth_type = body.auth_type
        if pack.auth_type != PackAuthType.SSH:
            pack.encrypted_ssh_key = None
        if pack.auth_type != PackAuthType.HTTPS_TOKEN:
            pack.encrypted_token = None
        needs_resync = True

    # Switching to a local source forces auth back to none and clears
    # any lingering credential bytes — the DB check constraint would
    # otherwise reject the commit.
    if pack.source_type == PackSourceType.LOCAL:
        if pack.auth_type != PackAuthType.NONE:
            pack.auth_type = PackAuthType.NONE
        pack.encrypted_ssh_key = None
        pack.encrypted_token = None

    if body.ssh_private_key:
        pack.encrypted_ssh_key = encrypt_ssh_key(body.ssh_private_key, master_key)
        needs_resync = True
    if body.ssh_known_hosts is not None:
        pack.ssh_known_hosts = body.ssh_known_hosts
    if body.token:
        pack.encrypted_token = encrypt_ssh_key(body.token, master_key)
        needs_resync = True

    return needs_resync, drop_git_checkout


# ---------------------------------------------------------------------------
# List
# ---------------------------------------------------------------------------


@router.get("", response_model=list[ActionPackResponse])
async def list_action_packs(
    _: User = Depends(current_superuser),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(ActionPack).order_by(ActionPack.id))
    return [ActionPackResponse.from_model(row) for row in result.scalars().all()]


# ---------------------------------------------------------------------------
# Pre-save test — must be above /{pack_id}/test to avoid path collision
# ---------------------------------------------------------------------------


@router.post("/test", response_model=ActionPackTestResponse)
async def test_action_pack_config(
    body: ActionPackTestRequest,
    _: User = Depends(current_superuser),
):
    """Validate a prospective pack config without touching the DB.

    Runs ``git ls-remote`` with the provided credentials — cheaper than
    a clone and sufficient to confirm auth works and the ref exists.
    """
    success, message, sha = await test_pack_credentials(
        source_type=body.source_type,
        repo_url=body.repo_url,
        ref=body.ref,
        auth_type=body.auth_type,
        ssh_private_key=body.ssh_private_key,
        ssh_known_hosts=body.ssh_known_hosts,
        token=body.token,
    )
    return ActionPackTestResponse(success=success, message=message, commit_sha=sha)


# ---------------------------------------------------------------------------
# Create
# ---------------------------------------------------------------------------


@router.post("", response_model=ActionPackResponse, status_code=201)
async def create_action_pack(
    body: ActionPackCreate,
    user: User = Depends(current_superuser),
    db: AsyncSession = Depends(get_db),
):
    existing = await db.execute(select(ActionPack).where(ActionPack.name == body.name))
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=409, detail="Action pack name already exists")

    pack = ActionPack()
    _apply_create(body, pack)
    db.add(pack)
    await db.flush()

    await log_action(
        db=db,
        action="create",
        entity_type="action_pack",
        entity_id=pack.id,
        user_id=user.id,
        after_state=_audit_snapshot(pack),
    )
    await db.commit()
    await db.refresh(pack)

    # Attempt first sync; failure is surfaced in the response body but
    # doesn't reverse the create (admin can fix creds and /sync).
    if pack.enabled:
        await sync_pack(db, pack)
        await reload_registry_async(db)

    return ActionPackResponse.from_model(pack)


# ---------------------------------------------------------------------------
# Read
# ---------------------------------------------------------------------------


@router.get("/{pack_id}", response_model=ActionPackResponse)
async def get_action_pack(
    pack_id: int,
    _: User = Depends(current_superuser),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(ActionPack).where(ActionPack.id == pack_id))
    pack = result.scalar_one_or_none()
    if pack is None:
        raise HTTPException(status_code=404, detail="Action pack not found")
    return ActionPackResponse.from_model(pack)


# ---------------------------------------------------------------------------
# Update
# ---------------------------------------------------------------------------


@router.put("/{pack_id}", response_model=ActionPackResponse)
async def update_action_pack(
    pack_id: int,
    body: ActionPackUpdate,
    user: User = Depends(current_superuser),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(ActionPack).where(ActionPack.id == pack_id))
    pack = result.scalar_one_or_none()
    if pack is None:
        raise HTTPException(status_code=404, detail="Action pack not found")

    before = _audit_snapshot(pack)

    if body.name is not None and body.name != pack.name:
        existing = await db.execute(select(ActionPack).where(ActionPack.name == body.name))
        if existing.scalar_one_or_none():
            raise HTTPException(status_code=409, detail="Action pack name already exists")

    needs_resync, drop_git_checkout = _apply_update(body, pack)

    await log_action(
        db=db,
        action="update",
        entity_type="action_pack",
        entity_id=pack.id,
        user_id=user.id,
        before_state=before,
        after_state=_audit_snapshot(pack),
    )
    await db.commit()
    await db.refresh(pack)

    if drop_git_checkout:
        # Orphaned managed checkout from the previous git configuration.
        delete_checkout(pack.id)

    if needs_resync and pack.enabled:
        await sync_pack(db, pack)
        await reload_registry_async(db)
    elif body.role is not None or body.enabled is not None:
        # Registry ordering depends on role→priority; enable/disable
        # affects membership. Reload without re-syncing.
        await reload_registry_async(db)

    return ActionPackResponse.from_model(pack)


# ---------------------------------------------------------------------------
# Delete
# ---------------------------------------------------------------------------


@router.delete("/{pack_id}", status_code=204)
async def delete_action_pack(
    pack_id: int,
    user: User = Depends(current_superuser),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(ActionPack).where(ActionPack.id == pack_id))
    pack = result.scalar_one_or_none()
    if pack is None:
        raise HTTPException(status_code=404, detail="Action pack not found")

    await log_action(
        db=db,
        action="delete",
        entity_type="action_pack",
        entity_id=pack.id,
        user_id=user.id,
        before_state=_audit_snapshot(pack),
    )
    await db.delete(pack)
    await db.commit()

    # Disk cleanup happens after the DB txn commits — if we crashed
    # between the two, the next startup sync would recreate the directory
    # and the registry would just not reference it (orphan, cheap to live
    # with until the next delete/restart).
    delete_checkout(pack_id)
    await reload_registry_async(db)


# ---------------------------------------------------------------------------
# Manual sync
# ---------------------------------------------------------------------------


@router.post("/{pack_id}/sync", response_model=ActionPackSyncResponse)
async def sync_action_pack(
    pack_id: int,
    _: User = Depends(current_superuser),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(ActionPack).where(ActionPack.id == pack_id))
    pack = result.scalar_one_or_none()
    if pack is None:
        raise HTTPException(status_code=404, detail="Action pack not found")
    if not pack.enabled:
        raise HTTPException(status_code=409, detail="Action pack is disabled")

    ok = await sync_pack(db, pack)
    await db.refresh(pack)
    if ok:
        await reload_registry_async(db)
    return ActionPackSyncResponse(
        success=ok,
        message=pack.last_sync_error or "Sync successful",
        current_sha=pack.current_sha,
        last_synced_at=pack.last_synced_at,
    )


# ---------------------------------------------------------------------------
# Existing-row connection test
# ---------------------------------------------------------------------------


@router.post("/{pack_id}/test", response_model=ActionPackTestResponse)
async def test_action_pack(
    pack_id: int,
    _: User = Depends(current_superuser),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(ActionPack).where(ActionPack.id == pack_id))
    pack = result.scalar_one_or_none()
    if pack is None:
        raise HTTPException(status_code=404, detail="Action pack not found")

    from app.packs.service import _decrypt_credentials  # noqa: PLC0415

    ssh_key, token = _decrypt_credentials(pack)
    success, message, sha = await test_pack_credentials(
        source_type=pack.source_type,
        repo_url=pack.repo_url,
        ref=pack.ref,
        auth_type=pack.auth_type,
        ssh_private_key=ssh_key,
        ssh_known_hosts=pack.ssh_known_hosts,
        token=token,
    )
    return ActionPackTestResponse(success=success, message=message, commit_sha=sha)
