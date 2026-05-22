"""Per-key action-resolution endpoints.

Resolution rows pin which pack wins a contested action key. In the
pure-per-key-pinning model these rows are the **only** resolver —
without an applicable row a contested key is *unresolved* and the
action is unrunnable.

Three write paths feed the table:

1. The wizard at activation time (operator picks per-key when
   adding a pack that collides with existing keys).
2. The freeze-on-fresh-conflict logic in ``app.actions.registry``
   (auto-pin previous winner so a sync doesn't flip behaviour).
3. The endpoints here (operator inspect/edit/clear pins) and the
   bulk-pin endpoint on the action-packs router
   (``POST /api/action-packs/{id}/claim-all-keys``).

Read endpoints expose every action key contributed by more than one
pack — the registry table on ``/action-packs`` consumes this to
flag keys awaiting an operator decision and to render the per-key
radio picker.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.actions.registry import ACTION_REGISTRY_CONTRIBUTORS, reload_registry_async
from app.audit.logger import log_action
from app.auth.users import current_superuser
from app.db import get_db
from app.models.user import User
from app.packs.models import ActionPack, ActionRegistrySnapshot, ActionResolution
from app.packs.schemas import (
    ActionResolutionPackOut,
    ActionResolutionRequest,
    ContestedActionKeyOut,
)

router = APIRouter(prefix="/action-resolutions", tags=["action-resolutions"])


async def _load_resolutions(db: AsyncSession) -> dict[str, ActionResolution]:
    rows = (await db.execute(select(ActionResolution))).scalars().all()
    return {r.action_key: r for r in rows}


def _to_pack_out(contributor) -> ActionResolutionPackOut:
    return ActionResolutionPackOut(
        pack_id=contributor.pack_id,
        pack_name=contributor.pack_name,
    )


async def _build_contested_view(db: AsyncSession) -> list[ContestedActionKeyOut]:
    """Build the contested-keys view.

    Every action key with more than one contributor appears here.
    When no resolution row pins a winner the key is *unresolved* —
    ``current_winner=None``, ``is_unresolved=True``. The frontend
    blocks runs and prompts the operator.
    """
    resolutions = await _load_resolutions(db)
    out: list[ContestedActionKeyOut] = []
    for action_key, contribs in ACTION_REGISTRY_CONTRIBUTORS.items():
        if len(contribs) < 2:
            continue
        sorted_contribs = sorted(contribs, key=lambda c: c.pack_name)

        resolution_row = resolutions.get(action_key)
        resolution_out: ActionResolutionPackOut | None = None
        winner_out: ActionResolutionPackOut | None = None
        if resolution_row is not None:
            match = next((c for c in sorted_contribs if c.pack_id == resolution_row.pack_id), None)
            if match is not None:
                resolution_out = _to_pack_out(match)
                winner_out = resolution_out

        out.append(
            ContestedActionKeyOut(
                action_key=action_key,
                candidates=[_to_pack_out(c) for c in sorted_contribs],
                current_winner=winner_out,
                resolution=resolution_out,
                # "Frozen" only applies when the operator hasn't yet
                # confirmed the auto-pin from freeze-on-fresh-conflict.
                # ``decided_by_user_id IS NULL`` is how the freeze logic
                # marks its rows.
                is_frozen=(
                    resolution_row is not None and resolution_row.decided_by_user_id is None
                ),
                is_unresolved=(winner_out is None),
                decided_at=resolution_row.decided_at if resolution_row else None,
                decided_by_user_id=(resolution_row.decided_by_user_id if resolution_row else None),
            )
        )
    out.sort(key=lambda r: r.action_key)
    return out


@router.get("", response_model=list[ContestedActionKeyOut])
async def list_contested_keys(
    _: User = Depends(current_superuser),
    db: AsyncSession = Depends(get_db),
):
    """Every action key currently contributed by more than one pack.

    Drives the conflict banner + per-key resolution modal. Each row
    reports the candidates (every pack supplying a manifest for the
    key), the live winner (computed at last rebuild), and the
    operator's explicit pin if one exists.
    """
    return await _build_contested_view(db)


@router.put("/{action_key}", response_model=ContestedActionKeyOut)
async def upsert_action_resolution(
    action_key: str,
    body: ActionResolutionRequest,
    user: User = Depends(current_superuser),
    db: AsyncSession = Depends(get_db),
):
    """Set ``action_resolution[action_key] = pack_id`` (or NULL=bundled).

    Validates that the chosen pack actually contributes the key — pins
    pointing at packs that don't define the action are nonsensical.
    Triggers a registry rebuild on success.
    """
    contribs = ACTION_REGISTRY_CONTRIBUTORS.get(action_key)
    if not contribs:
        raise HTTPException(
            status_code=404,
            detail=f"action key {action_key!r} is not contributed by any pack",
        )
    if not any(c.pack_id == body.pack_id for c in contribs):
        choices = sorted({c.pack_id for c in contribs}, key=lambda v: (v is None, v))
        raise HTTPException(
            status_code=409,
            detail={
                "kind": "resolution_pack_not_contributor",
                "action_key": action_key,
                "pack_id": body.pack_id,
                "valid_pack_ids": choices,
                "message": (
                    "The chosen pack does not contribute this action "
                    "key. Pick one of the candidate packs."
                ),
            },
        )
    if body.pack_id is not None:
        exists = await db.scalar(select(ActionPack.id).where(ActionPack.id == body.pack_id))
        if exists is None:
            raise HTTPException(
                status_code=404,
                detail=f"pack_id={body.pack_id} does not exist",
            )

    existing = (
        await db.execute(select(ActionResolution).where(ActionResolution.action_key == action_key))
    ).scalar_one_or_none()
    before_state = {"pack_id": existing.pack_id} if existing is not None else None
    if existing is not None:
        existing.pack_id = body.pack_id
        existing.decided_by_user_id = user.id
    else:
        db.add(
            ActionResolution(
                action_key=action_key,
                pack_id=body.pack_id,
                decided_by_user_id=user.id,
            )
        )

    await log_action(
        db=db,
        action="resolution.upsert",
        entity_type="action_resolution",
        entity_id=None,
        user_id=user.id,
        before_state=before_state,
        after_state={"action_key": action_key, "pack_id": body.pack_id},
    )
    await db.commit()
    await reload_registry_async(db)

    rows = await _build_contested_view(db)
    match = next((r for r in rows if r.action_key == action_key), None)
    if match is None:
        raise HTTPException(
            status_code=500,
            detail="resolution applied but action key vanished from registry",
        )
    return match


@router.delete("/{action_key}", status_code=204)
async def delete_action_resolution(
    action_key: str,
    user: User = Depends(current_superuser),
    db: AsyncSession = Depends(get_db),
):
    """Drop the resolution row for a contested key.

    The key reverts to **unresolved** on the next rebuild (which this
    endpoint triggers) — there is no global ordering to fall back on.
    The action becomes unrunnable until the operator pins a winner
    again.

    Also clears the matching ``action_registry_snapshot`` row so the
    next rebuild treats the key as if it were freshly seen, instead
    of the freeze logic immediately re-pinning the previous winner.
    """
    existing = (
        await db.execute(select(ActionResolution).where(ActionResolution.action_key == action_key))
    ).scalar_one_or_none()
    if existing is None:
        raise HTTPException(status_code=404, detail="No resolution for that key")
    before_state = {"pack_id": existing.pack_id}
    await db.delete(existing)
    snapshot_row = (
        await db.execute(
            select(ActionRegistrySnapshot).where(ActionRegistrySnapshot.action_key == action_key)
        )
    ).scalar_one_or_none()
    if snapshot_row is not None:
        await db.delete(snapshot_row)
    await log_action(
        db=db,
        action="resolution.delete",
        entity_type="action_resolution",
        entity_id=None,
        user_id=user.id,
        before_state=before_state,
        after_state={"action_key": action_key},
    )
    await db.commit()
    await reload_registry_async(db)
