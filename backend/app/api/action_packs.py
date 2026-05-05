"""CRUD + sync endpoints for action packs.

Superuser-only, audit-logged on writes. Credentials are NOT handled
here — they live on the linked ``GitRepository`` row (managed on the
Git Repos page). This router only persists the pack metadata and
dispatches sync.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.actions.registry import reload_registry_async
from app.audit.logger import log_action
from app.auth.users import current_superuser
from app.db import get_db
from app.models.git_repository import GitRepository
from app.models.user import User
from app.packs.models import ActionPack, PackSourceType
from app.packs.schemas import (
    ActionPackCreate,
    ActionPackReorderRequest,
    ActionPackResponse,
    ActionPackSyncResponse,
    ActionPackUpdate,
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
        "position": pack.position,
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
    # position is server-assigned at insert time (see create_action_pack).


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

    max_pos = await db.scalar(select(func.coalesce(func.max(ActionPack.position), 0)))
    pack = ActionPack()
    _apply_create(body, pack)
    pack.position = (max_pos or 0) + 1
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
# Reorder
# ---------------------------------------------------------------------------


@router.post("/reorder", status_code=200)
async def reorder_action_packs(
    body: ActionPackReorderRequest,
    user: User = Depends(current_superuser),
    db: AsyncSession = Depends(get_db),
):
    """Rewrite ``ActionPack.position`` for every pack in one shot.

    The submitted list is the desired top-to-bottom display order;
    the first id wins on action-key collisions and gets the highest
    position. Bundled is implicit at position 0 and never appears in
    the list.

    Rejects the request unless the submitted set exactly matches the
    set of existing pack ids — partial reorders aren't supported. The
    UI builds the payload from its full sorted list; any drift means
    something else mutated the table mid-edit and the operator should
    refresh.
    """
    rows = list(
        (await db.execute(select(ActionPack))).scalars().all()
    )
    existing_ids = {r.id for r in rows}
    submitted_ids = set(body.pack_ids)
    if existing_ids != submitted_ids or len(body.pack_ids) != len(submitted_ids):
        missing = sorted(existing_ids - submitted_ids)
        unknown = sorted(submitted_ids - existing_ids)
        duplicates = sorted(
            {pid for pid in body.pack_ids if body.pack_ids.count(pid) > 1}
        )
        raise HTTPException(
            status_code=409,
            detail={
                "kind": "reorder_set_mismatch",
                "missing_pack_ids": missing,
                "unknown_pack_ids": unknown,
                "duplicate_pack_ids": duplicates,
                "message": (
                    "Reorder request must list every existing pack id "
                    "exactly once. Refresh the page and try again."
                ),
            },
        )

    by_id = {r.id: r for r in rows}
    before_state = {r.id: r.position for r in rows}
    n = len(body.pack_ids)
    for idx, pid in enumerate(body.pack_ids):
        # First id (top of table) gets the highest position.
        by_id[pid].position = n - idx

    await log_action(
        db=db,
        action="reorder",
        entity_type="action_pack",
        entity_id=None,
        user_id=user.id,
        before_state={"positions": before_state},
        after_state={"positions": {pid: n - idx for idx, pid in enumerate(body.pack_ids)}},
    )
    await db.commit()
    await reload_registry_async(db)
    return {"reordered": n}
