from __future__ import annotations

import json
import logging

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import ValidationError
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.actions.registry import ACTION_REGISTRY, reload_registry
from app.actions.validation import build_param_model
from app.auth.users import current_active_user, current_superuser
from app.db import get_db
from app.models.action_run import ActionHostRun, ActionRun
from app.models.user import User
from app.schemas.actions import (
    ActionDefinitionOut,
    ActionHostRunOut,
    ActionParameterOut,
    ActionRunOut,
    RunCreateBody,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/actions", tags=["actions"])


def _definition_to_out(defn) -> ActionDefinitionOut:
    """Convert an ActionDefinition dataclass to ActionDefinitionOut."""
    params = [
        ActionParameterOut(
            key=p.key,
            label=p.label,
            type=p.type,
            default=p.default,
            required=p.required,
            choices=list(p.choices) if p.choices is not None else None,
            help_text=p.help_text,
        )
        for p in defn.parameters
    ]
    return ActionDefinitionOut(
        key=defn.key,
        name=defn.name,
        description=defn.description,
        icon=defn.icon,
        version=defn.version,
        estimated_duration=defn.estimated_duration,
        destructive=defn.destructive,
        supports_group=defn.supports_group,
        supports_host=defn.supports_host,
        supports_fleet=defn.supports_fleet,
        parameters=params,
        pack_name=defn.pack_name,
        overridden_from=list(defn.overridden_from),
    )


# ---------------------------------------------------------------------------
# GET /actions — List catalog
# ---------------------------------------------------------------------------


@router.get("/", response_model=list[ActionDefinitionOut])
async def list_actions(
    _: User = Depends(current_active_user),
):
    """Return all registered actions from the catalog."""
    return [_definition_to_out(defn) for defn in ACTION_REGISTRY.values()]


@router.post("/refresh")
async def refresh_actions(
    _: User = Depends(current_superuser),
):
    """Re-sync the remote default pack and rescan user packs.

    Pulls the configured remote default pack (if any) and re-scans disk,
    then rebuilds ACTION_REGISTRY in place. Returns a summary so admins
    can confirm which packs contributed.
    """
    registry = reload_registry()
    packs = sorted({defn.pack_name for defn in registry.values()})
    return {
        "action_count": len(registry),
        "packs": packs,
    }


# ---------------------------------------------------------------------------
# POST /actions/runs — Create run + dispatch
# ---------------------------------------------------------------------------


@router.post("/runs", response_model=ActionRunOut, status_code=201)
async def create_run(
    body: RunCreateBody,
    user: User = Depends(current_active_user),
    db: AsyncSession = Depends(get_db),
):
    """Create an action run and dispatch the Celery task."""
    # Exactly one of host_id / group_id must be set
    if (body.host_id is None) == (body.group_id is None):
        raise HTTPException(
            status_code=422,
            detail="Exactly one of host_id or group_id must be provided.",
        )

    # action_key must exist in registry
    action = ACTION_REGISTRY.get(body.action_key)
    if action is None:
        raise HTTPException(status_code=400, detail="Unknown action")

    # Playbook file must exist. Built-in pseudo-actions have
    # ``playbook_path=None`` and are dispatched via dedicated per-host
    # tasks, not Ansible playbooks; the routing for ad-hoc built-in runs
    # lands in C5. Until then, reject ad-hoc built-in dispatch with a
    # clear error.
    if action.playbook_path is None:
        raise HTTPException(
            status_code=400,
            detail=(
                "Built-in actions cannot be dispatched ad-hoc through this "
                "endpoint. Schedule them via /api/scheduled-actions or use "
                "the dedicated per-tab buttons."
            ),
        )
    if not action.playbook_path.is_file():
        raise HTTPException(status_code=400, detail="Playbook file not found")

    # Check scope support
    if body.host_id is not None and not action.supports_host:
        raise HTTPException(
            status_code=422,
            detail="This action does not support host-scoped runs.",
        )
    if body.group_id is not None and not action.supports_group:
        raise HTTPException(
            status_code=422,
            detail="This action does not support group-scoped runs.",
        )

    # Validate parameters against the action's manifest schema. Catches
    # missing-required, type-mismatch, and unknown-key errors uniformly.
    try:
        build_param_model(action).model_validate(body.parameters)
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail=exc.errors()) from exc

    # Advisory lock — prevents duplicate concurrent runs for same action+scope
    lock_key = f"actions.{body.action_key}.{body.host_id or body.group_id}"
    lock_result = await db.execute(
        text("SELECT pg_try_advisory_xact_lock(hashtext(:key))"),
        {"key": lock_key},
    )
    got_lock = lock_result.scalar()
    if not got_lock:
        # Find the running/queued run
        running = await db.scalar(
            select(ActionRun).where(
                ActionRun.action_key == body.action_key,
                ActionRun.host_id == body.host_id
                if body.host_id is not None
                else ActionRun.group_id == body.group_id,
                ActionRun.status.in_(["queued", "running"]),
            )
        )
        raise HTTPException(
            status_code=409,
            detail={
                "detail": "An identical action is already running",
                "running_run_id": running.id if running else None,
            },
        )

    # Create the ActionRun record
    run = ActionRun(
        action_key=body.action_key,
        action_version=action.version,
        host_id=body.host_id,
        group_id=body.group_id,
        parameters=body.parameters,
        parallelism=body.parallelism,
        status="queued",
        triggered_by_user_id=user.id,
    )
    db.add(run)
    await db.flush()
    await db.refresh(run)

    run_id = run.id
    await db.commit()

    # Dispatch Celery task
    try:
        from app.tasks import celery_app

        celery_app.send_task("app.tasks.action_orchestrator.run_action", args=[run_id])
    except Exception as exc:
        logger.warning("Failed to dispatch actions.run task for run %d: %s", run_id, exc)

    # Reload to return fresh state (host_runs will be empty at this point)
    run = await db.get(ActionRun, run_id)
    return ActionRunOut.model_validate(run)


# ---------------------------------------------------------------------------
# GET /actions/runs — List runs
# ---------------------------------------------------------------------------


@router.get("/runs", response_model=list[ActionRunOut])
async def list_runs(
    host_id: int | None = Query(default=None),
    group_id: int | None = Query(default=None),
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    _: User = Depends(current_active_user),
    db: AsyncSession = Depends(get_db),
):
    """List action runs, optionally filtered by host or group scope."""
    q = select(ActionRun).order_by(ActionRun.created_at.desc()).limit(limit).offset(offset)
    if host_id is not None:
        q = q.where(ActionRun.host_id == host_id)
    if group_id is not None:
        q = q.where(ActionRun.group_id == group_id)

    result = await db.execute(q)
    runs = result.scalars().all()
    # Return without nested host_runs for list view
    return [ActionRunOut.model_validate(r) for r in runs]


# ---------------------------------------------------------------------------
# GET /actions/runs/{id} — Single run with host runs
# ---------------------------------------------------------------------------


@router.get("/runs/{id}", response_model=ActionRunOut)
async def get_run(
    id: int,
    _: User = Depends(current_active_user),
    db: AsyncSession = Depends(get_db),
):
    """Return a single action run with all host run details."""
    run = await db.get(ActionRun, id)
    if run is None:
        raise HTTPException(status_code=404, detail="Action run not found")

    host_runs_result = await db.execute(
        select(ActionHostRun).where(ActionHostRun.action_run_id == id).order_by(ActionHostRun.id)
    )
    host_runs = host_runs_result.scalars().all()

    out = ActionRunOut.model_validate(run)
    out.host_runs = [ActionHostRunOut.model_validate(hr) for hr in host_runs]
    return out


# ---------------------------------------------------------------------------
# GET /actions/runs/{id}/hosts/{host_id}/output — Full output text
# ---------------------------------------------------------------------------


@router.get("/runs/{id}/hosts/{host_id}/output")
async def get_host_run_output(
    id: int,
    host_id: int,
    _: User = Depends(current_active_user),
    db: AsyncSession = Depends(get_db),
):
    """Return the raw Ansible output for a specific host run as plain text."""
    host_run = await db.scalar(
        select(ActionHostRun).where(
            ActionHostRun.action_run_id == id,
            ActionHostRun.host_id == host_id,
        )
    )
    if host_run is None:
        raise HTTPException(status_code=404, detail="Host run not found")

    from fastapi.responses import PlainTextResponse

    return PlainTextResponse(content=host_run.output or "")


# ---------------------------------------------------------------------------
# POST /actions/runs/{id}/cancel — Cancel run
# ---------------------------------------------------------------------------


@router.post("/runs/{id}/cancel")
async def cancel_run(
    id: int,
    _: User = Depends(current_active_user),
    db: AsyncSession = Depends(get_db),
):
    """Signal cancellation via Redis and mark the run as cancelled if queued/running."""
    run = await db.get(ActionRun, id)
    if run is None:
        raise HTTPException(status_code=404, detail="Action run not found")

    import redis as redis_lib

    from app.config import settings

    r = redis_lib.from_url(settings.redis.url)
    r.setex(f"actions.cancel.{id}", 3600, "1")

    if run.status in ("queued", "running"):
        run.status = "cancelled"
        await db.commit()

    return {"ok": True}


# ---------------------------------------------------------------------------
# GET /actions/runs/{id}/stream — SSE stream
# ---------------------------------------------------------------------------


@router.get("/runs/{id}/stream")
async def stream_run_events(
    id: int,
    _: User = Depends(current_active_user),
    db: AsyncSession = Depends(get_db),
):
    """Server-Sent Events stream for a run. Subscribes to Redis pub/sub channel
    'actions.run.{id}', relays events. Closes when run reaches terminal status."""

    run = await db.get(ActionRun, id)
    if run is None:
        raise HTTPException(status_code=404, detail="Action run not found")

    terminal_states = {"succeeded", "failed", "partial", "cancelled"}
    run_status = run.status

    async def event_generator():
        import redis.asyncio as aioredis

        from app.config import settings

        client = aioredis.from_url(settings.redis.url)
        pubsub = client.pubsub()
        await pubsub.subscribe(f"actions.run.{id}")

        try:
            # If run is already terminal, send current status and close
            if run_status in terminal_states:
                yield f"event: status\ndata: {json.dumps({'status': run_status})}\n\n"
                return

            async for message in pubsub.listen():
                if message["type"] != "message":
                    continue

                data = json.loads(message["data"])
                event_type = data.get("event", "output")
                payload = json.dumps(data)
                yield f"event: {event_type}\ndata: {payload}\n\n"

                # Close on terminal status
                if data.get("event") == "status" and data.get("status") in terminal_states:
                    return
        finally:
            await pubsub.unsubscribe(f"actions.run.{id}")
            await client.aclose()

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


# ---------------------------------------------------------------------------
# GET /actions/{action_key} — Single action
#
# NOTE: This route must be registered AFTER all /runs* routes.  FastAPI
# evaluates routes in registration order; placing /{action_key} before
# /runs would cause "GET /actions/runs" to be captured by the dynamic
# segment (action_key="runs") and return 404 "Action not found".
# ---------------------------------------------------------------------------


@router.get("/{action_key}", response_model=ActionDefinitionOut)
async def get_action(
    action_key: str,
    _: User = Depends(current_active_user),
):
    """Return a single action definition by key."""
    defn = ACTION_REGISTRY.get(action_key)
    if defn is None:
        raise HTTPException(status_code=404, detail="Action not found")
    return _definition_to_out(defn)
