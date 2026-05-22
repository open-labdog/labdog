"""CRUD + sync endpoints for action packs.

Superuser-only, audit-logged on writes. Credentials are NOT handled
here — they live on the linked ``GitRepository`` row (managed on the
Git Repos page). This router only persists the pack metadata and
dispatches sync.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.actions.registry import ACTION_REGISTRY_CONTRIBUTORS, reload_registry_async
from app.audit.logger import log_action
from app.auth.users import current_superuser
from app.db import get_db
from app.models.git_repository import GitRepository
from app.models.user import User
from app.packs.models import ActionPack, ActionResolution, PackSourceType
from app.packs.schemas import (
    ActionPackCreate,
    ActionPackResponse,
    ActionPackSyncResponse,
    ActionPackUpdate,
    ClaimAllKeysResponse,
)
from app.packs.service import delete_checkout, sync_pack

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/action-packs", tags=["action-packs"])


def _audit_snapshot(pack: ActionPack) -> dict:
    return {
        "name": pack.name,
        "source_type": pack.source_type.value,
        "git_repository_id": pack.git_repository_id,
        "path": pack.path,
        "local_path": pack.local_path,
        "enabled": pack.enabled,
    }


async def _ensure_git_repo(db: AsyncSession, repo_id: int) -> GitRepository:
    result = await db.execute(select(GitRepository).where(GitRepository.id == repo_id))
    repo = result.scalar_one_or_none()
    if repo is None:
        raise HTTPException(
            status_code=400,
            detail=f"git_repository_id={repo_id} does not reference an existing repository",
        )
    return repo


async def _response(db: AsyncSession, pack: ActionPack) -> ActionPackResponse:
    """Build the response, resolving the repo name when linked."""
    repo_name: str | None = None
    if pack.git_repository_id is not None:
        r = await db.execute(
            select(GitRepository.name).where(GitRepository.id == pack.git_repository_id)
        )
        repo_name = r.scalar_one_or_none()
    return ActionPackResponse.from_model(pack, repo_name=repo_name)


def _apply_create(body: ActionPackCreate, pack: ActionPack) -> None:
    pack.name = body.name
    pack.source_type = body.source_type
    pack.git_repository_id = body.git_repository_id
    pack.path = body.path or ""
    pack.local_path = body.local_path
    pack.enabled = body.enabled


def _apply_update(body: ActionPackUpdate, pack: ActionPack) -> tuple[bool, bool]:
    """Mutate *pack* with any non-None fields from *body*.

    Returns ``(needs_resync, drop_git_checkout)``:
      * needs_resync — caller triggers sync_pack + registry reload.
      * drop_git_checkout — pack just switched away from git; the old
        managed checkout is orphaned and should be removed.
    """
    needs_resync = False
    drop_git_checkout = False

    if body.name is not None and body.name != pack.name:
        pack.name = body.name
    if body.source_type is not None and body.source_type != pack.source_type:
        if pack.source_type == PackSourceType.GIT:
            drop_git_checkout = True
        pack.source_type = body.source_type
        needs_resync = True
    if body.git_repository_id is not None and body.git_repository_id != pack.git_repository_id:
        pack.git_repository_id = body.git_repository_id
        needs_resync = True
    if body.path is not None and body.path != pack.path:
        pack.path = body.path
        # Subpath change doesn't require re-clone, but the registry
        # needs to rescan from the new directory.
        needs_resync = True
    if body.local_path is not None and body.local_path != pack.local_path:
        pack.local_path = body.local_path
        needs_resync = True
    if body.enabled is not None and body.enabled != pack.enabled:
        pack.enabled = body.enabled
        needs_resync = True

    # Enforce shape post-update: if switching to git, ensure local_path
    # is cleared; if switching to local, ensure git_repository_id is
    # cleared. DB check constraint backs these up too.
    if pack.source_type == PackSourceType.GIT:
        pack.local_path = None
    else:
        pack.git_repository_id = None
        pack.path = ""

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
    rows = list(result.scalars().all())

    # Batch-fetch repo names for display.
    repo_ids = {r.git_repository_id for r in rows if r.git_repository_id is not None}
    repo_names: dict[int, str] = {}
    if repo_ids:
        rr = await db.execute(
            select(GitRepository.id, GitRepository.name).where(GitRepository.id.in_(repo_ids))
        )
        repo_names = {rid: name for rid, name in rr.all()}

    return [
        ActionPackResponse.from_model(row, repo_name=repo_names.get(row.git_repository_id))
        for row in rows
    ]


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
    if body.source_type == PackSourceType.GIT and body.git_repository_id is not None:
        await _ensure_git_repo(db, body.git_repository_id)

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

    if pack.enabled:
        await sync_pack(db, pack)
        await reload_registry_async(db)

    return await _response(db, pack)


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
    return await _response(db, pack)


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
    if body.git_repository_id is not None:
        await _ensure_git_repo(db, body.git_repository_id)

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
        delete_checkout(pack.id)

    if needs_resync and pack.enabled:
        await sync_pack(db, pack)
        await reload_registry_async(db)
    elif body.enabled is not None:
        await reload_registry_async(db)

    return await _response(db, pack)


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
# Bulk-pin (claim all keys this pack contributes)
# ---------------------------------------------------------------------------


@router.post("/{pack_id}/claim-all-keys", response_model=ClaimAllKeysResponse)
async def claim_all_keys(
    pack_id: int,
    user: User = Depends(current_superuser),
    db: AsyncSession = Depends(get_db),
):
    """Pin every action key this pack contributes to this pack.

    Writes (or overwrites) an ``action_resolution`` row for each key
    the pack appears in as a contributor. Other packs' resolutions
    for the same keys are flipped to point at this pack. Keys this
    pack does not contribute are not touched.

    The endpoint is idempotent — re-running with no other pack
    changes returns ``created=0 updated=0 skipped=N`` and is a no-op.

    Returns ``{created, updated, skipped}`` for the UI to surface in
    a confirmation toast and to drive the bulk-pick diff dialog.
    """
    pack = (
        await db.execute(select(ActionPack).where(ActionPack.id == pack_id))
    ).scalar_one_or_none()
    if pack is None:
        raise HTTPException(status_code=404, detail="Action pack not found")

    # Find every action key the pack currently contributes by scanning
    # the in-memory contributors view (built at the last registry
    # rebuild). Uncontested keys are included — their resolution
    # row is a no-op for the resolver but makes the operator's intent
    # ("I want this pack to own these keys, even if another pack
    # appears later") durable.
    claimed_keys = [
        key
        for key, contribs in ACTION_REGISTRY_CONTRIBUTORS.items()
        if any(c.pack_id == pack_id for c in contribs)
    ]
    if not claimed_keys:
        await log_action(
            db=db,
            action="resolution.claim_all_keys",
            entity_type="action_pack",
            entity_id=pack.id,
            user_id=user.id,
            after_state={"pack_id": pack.id, "claimed_keys": []},
        )
        await db.commit()
        return ClaimAllKeysResponse(created=0, updated=0, skipped=0)

    existing_rows = (
        (
            await db.execute(
                select(ActionResolution).where(ActionResolution.action_key.in_(claimed_keys))
            )
        )
        .scalars()
        .all()
    )
    existing_by_key = {r.action_key: r for r in existing_rows}

    created = 0
    updated = 0
    skipped = 0
    before_state: dict[str, int | None] = {}
    after_state: dict[str, int | None] = {}
    for key in claimed_keys:
        prev = existing_by_key.get(key)
        if prev is None:
            db.add(
                ActionResolution(
                    action_key=key,
                    pack_id=pack_id,
                    decided_by_user_id=user.id,
                )
            )
            created += 1
            before_state[key] = None
            after_state[key] = pack_id
        elif prev.pack_id == pack_id:
            skipped += 1
        else:
            before_state[key] = prev.pack_id
            after_state[key] = pack_id
            prev.pack_id = pack_id
            prev.decided_by_user_id = user.id
            updated += 1

    await log_action(
        db=db,
        action="resolution.claim_all_keys",
        entity_type="action_pack",
        entity_id=pack.id,
        user_id=user.id,
        before_state={"resolutions": before_state} if before_state else None,
        after_state={
            "pack_id": pack_id,
            "claimed_keys": claimed_keys,
            "resolutions": after_state,
        },
    )
    await db.commit()
    await reload_registry_async(db)
    return ClaimAllKeysResponse(created=created, updated=updated, skipped=skipped)
