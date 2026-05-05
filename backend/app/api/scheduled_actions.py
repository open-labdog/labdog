"""Unified scheduled-actions API.

POST/PUT/DELETE/GET on the ``ScheduledAction`` table plus a ``run-now``
shortcut and a runs-history list. All endpoints are superuser-only —
scheduling work that affects shared infrastructure is privileged.

Validation is shared with ``POST /api/actions/runs`` via
``app.actions.validation.build_param_model`` so a parameter that's
rejected ad-hoc is also rejected at schedule-create time. Cron syntax
is validated through ``croniter.is_valid``.

Built-in dispatch routing for ``run-now`` lands in C5 — until then,
``POST /api/scheduled-actions/{id}/run-now`` for a built-in returns
202 with a stub error in the response body. Pack-supplied actions
work today via the existing ``app.tasks.action_orchestrator.run_action``
task.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from croniter import croniter
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import ValidationError
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.actions.registry import ACTION_REGISTRY, ActionDefinition
from app.actions.validation import build_param_model
from app.audit.logger import log_action
from app.auth.users import current_superuser
from app.db import get_db
from app.models.action_run import ActionRun
from app.models.host import Host
from app.models.host_group import HostGroup
from app.models.scheduled_action import ScheduledAction
from app.models.user import User
from app.schemas.actions import ActionRunOut
from app.schemas.scheduled_actions import (
    ScheduledActionIn,
    ScheduledActionOut,
    ScheduledActionRunSummary,
    ValidateCronRequest,
    ValidateCronResponse,
)

logger = logging.getLogger(__name__)


router = APIRouter(prefix="/scheduled-actions", tags=["scheduled-actions"])


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


def _validate_request(body: ScheduledActionIn) -> ActionDefinition:
    """Cross-cutting validation shared by POST + PUT.

    Raises ``HTTPException`` with the appropriate status code; returns the
    resolved ``ActionDefinition`` so the caller can derive ``destructive``
    and friends without a second registry lookup.
    """
    action = ACTION_REGISTRY.get(body.action_key)
    if action is None:
        raise HTTPException(
            status_code=400, detail=f"Unknown action_key: {body.action_key!r}"
        )

    if body.target_kind == "fleet":
        if not action.supports_fleet:
            raise HTTPException(
                status_code=422,
                detail=(
                    f"Action {body.action_key!r} does not support fleet targeting"
                ),
            )
        if body.target_id is not None:
            raise HTTPException(
                status_code=422, detail="fleet target requires target_id=null"
            )
    elif body.target_kind == "group":
        if not action.supports_group:
            raise HTTPException(
                status_code=422, detail="Action does not support group runs"
            )
        if body.target_id is None:
            raise HTTPException(
                status_code=422, detail="group target requires target_id"
            )
    elif body.target_kind == "host":
        if not action.supports_host:
            raise HTTPException(
                status_code=422, detail="Action does not support host runs"
            )
        if body.target_id is None:
            raise HTTPException(
                status_code=422, detail="host target requires target_id"
            )

    if body.schedule_cron is not None and not croniter.is_valid(body.schedule_cron):
        raise HTTPException(status_code=422, detail="Invalid cron expression")

    try:
        build_param_model(action).model_validate(body.parameters)
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail=exc.errors()) from exc

    return action


async def _validate_target_exists(
    db: AsyncSession, target_kind: str, target_id: int | None
) -> str | None:
    """Confirm a host/group target exists; return the display name for
    the listing endpoint. ``fleet`` returns the literal ``"All hosts"``."""
    if target_kind == "fleet":
        return "All hosts"
    if target_kind == "host":
        host = await db.get(Host, target_id)
        if host is None:
            raise HTTPException(status_code=404, detail="Host not found")
        return host.hostname
    if target_kind == "group":
        group = await db.get(HostGroup, target_id)
        if group is None:
            raise HTTPException(status_code=404, detail="Host group not found")
        return group.name
    return None


# ---------------------------------------------------------------------------
# Output construction
# ---------------------------------------------------------------------------


def _scheduled_action_audit_payload(sa: ScheduledAction) -> dict[str, Any]:
    return {
        "target_kind": sa.target_kind,
        "target_id": sa.target_id,
        "action_key": sa.action_key,
        "parameters": sa.parameters,
        "schedule_cron": sa.schedule_cron,
        "enabled": sa.enabled,
        "snapshot_enabled": sa.snapshot_enabled,
        "verify_enabled": sa.verify_enabled,
        "auto_rollback": sa.auto_rollback,
        "batch_size": sa.batch_size,
    }


async def _hydrate(
    db: AsyncSession,
    sa: ScheduledAction,
    *,
    include_last_run: bool,
) -> ScheduledActionOut:
    """Build the response model with server-resolved presentation hints."""
    out = ScheduledActionOut.model_validate(sa)
    target_name = await _resolve_target_name(db, sa.target_kind, sa.target_id)
    out.target_name = target_name

    action = ACTION_REGISTRY.get(sa.action_key)
    if action is not None:
        out.action_name = action.name
        out.pack_name = action.pack_name
        out.destructive = action.destructive

    if include_last_run:
        last = await db.scalar(
            select(ActionRun)
            .where(ActionRun.scheduled_action_id == sa.id)
            .order_by(desc(ActionRun.created_at))
            .limit(1)
        )
        if last is not None:
            out.last_run = ScheduledActionRunSummary(
                id=last.id,
                status=last.status,
                started_at=last.started_at,
                finished_at=last.finished_at,
                created_at=last.created_at,
            )
    return out


async def _resolve_target_name(
    db: AsyncSession, target_kind: str, target_id: int | None
) -> str | None:
    if target_kind == "fleet":
        return "All hosts"
    if target_kind == "host" and target_id is not None:
        host = await db.get(Host, target_id)
        return host.hostname if host else None
    if target_kind == "group" and target_id is not None:
        group = await db.get(HostGroup, target_id)
        return group.name if group else None
    return None


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get("", response_model=list[ScheduledActionOut])
async def list_scheduled_actions(
    target_kind: str | None = Query(default=None),
    target_id: int | None = Query(default=None),
    action_key: str | None = Query(default=None),
    enabled: bool | None = Query(default=None),
    category: str | None = Query(default=None, description="'_builtin' or 'pack'"),
    include_last_run: bool = Query(default=True),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(current_superuser),
) -> list[ScheduledActionOut]:
    stmt = select(ScheduledAction).order_by(ScheduledAction.created_at.desc())
    if target_kind is not None:
        stmt = stmt.where(ScheduledAction.target_kind == target_kind)
    if target_id is not None:
        stmt = stmt.where(ScheduledAction.target_id == target_id)
    if action_key is not None:
        stmt = stmt.where(ScheduledAction.action_key == action_key)
    if enabled is not None:
        stmt = stmt.where(ScheduledAction.enabled.is_(enabled))
    if category == "_builtin":
        stmt = stmt.where(ScheduledAction.action_key.startswith("_builtin."))
    elif category == "pack":
        stmt = stmt.where(~ScheduledAction.action_key.startswith("_builtin."))

    rows = (await db.execute(stmt)).scalars().all()
    return [
        await _hydrate(db, sa, include_last_run=include_last_run) for sa in rows
    ]


@router.post("", response_model=ScheduledActionOut, status_code=201)
async def create_scheduled_action(
    body: ScheduledActionIn,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(current_superuser),
) -> ScheduledActionOut:
    _validate_request(body)
    await _validate_target_exists(db, body.target_kind, body.target_id)

    sa = ScheduledAction(
        target_kind=body.target_kind,
        target_id=body.target_id,
        action_key=body.action_key,
        parameters=body.parameters,
        schedule_cron=body.schedule_cron,
        enabled=body.enabled,
        snapshot_enabled=body.snapshot_enabled,
        verify_enabled=body.verify_enabled,
        auto_rollback=body.auto_rollback,
        batch_size=body.batch_size,
    )
    db.add(sa)
    try:
        await db.flush()
    except Exception as exc:  # noqa: BLE001
        # Unique-constraint collision: same (target_kind, target_id, action_key).
        if "uq_scheduled_actions_target_action" in str(exc):
            raise HTTPException(
                status_code=409,
                detail=(
                    "A schedule already exists for this target + action_key. "
                    "Edit the existing schedule instead."
                ),
            ) from exc
        raise

    await db.refresh(sa)
    await log_action(
        db,
        action="scheduled_action.created",
        entity_type="scheduled_action",
        entity_id=sa.id,
        user_id=user.id,
        after_state=_scheduled_action_audit_payload(sa),
    )
    await db.commit()
    return await _hydrate(db, sa, include_last_run=False)


@router.get("/{scheduled_action_id}", response_model=ScheduledActionOut)
async def get_scheduled_action(
    scheduled_action_id: int,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(current_superuser),
) -> ScheduledActionOut:
    sa = await db.get(ScheduledAction, scheduled_action_id)
    if sa is None:
        raise HTTPException(status_code=404, detail="Scheduled action not found")
    return await _hydrate(db, sa, include_last_run=True)


@router.put("/{scheduled_action_id}", response_model=ScheduledActionOut)
async def update_scheduled_action(
    scheduled_action_id: int,
    body: ScheduledActionIn,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(current_superuser),
) -> ScheduledActionOut:
    sa = await db.get(ScheduledAction, scheduled_action_id)
    if sa is None:
        raise HTTPException(status_code=404, detail="Scheduled action not found")

    # action_key + target_* are immutable — re-creating is the right
    # path for "I want to schedule a different action against this
    # target." Locking these makes the scheduler's idempotency keys
    # stable and prevents surprising audit gaps.
    if (
        body.action_key != sa.action_key
        or body.target_kind != sa.target_kind
        or body.target_id != sa.target_id
    ):
        raise HTTPException(
            status_code=422,
            detail=(
                "action_key and target are immutable on update — delete and "
                "re-create the schedule to change them."
            ),
        )

    _validate_request(body)

    before = _scheduled_action_audit_payload(sa)

    sa.parameters = body.parameters
    sa.schedule_cron = body.schedule_cron
    sa.enabled = body.enabled
    sa.snapshot_enabled = body.snapshot_enabled
    sa.verify_enabled = body.verify_enabled
    sa.auto_rollback = body.auto_rollback
    sa.batch_size = body.batch_size
    await db.flush()
    await db.refresh(sa)

    await log_action(
        db,
        action="scheduled_action.updated",
        entity_type="scheduled_action",
        entity_id=sa.id,
        user_id=user.id,
        before_state=before,
        after_state=_scheduled_action_audit_payload(sa),
    )
    await db.commit()
    return await _hydrate(db, sa, include_last_run=False)


@router.delete("/{scheduled_action_id}", status_code=204)
async def delete_scheduled_action(
    scheduled_action_id: int,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(current_superuser),
) -> None:
    sa = await db.get(ScheduledAction, scheduled_action_id)
    if sa is None:
        raise HTTPException(status_code=404, detail="Scheduled action not found")

    before = _scheduled_action_audit_payload(sa)
    sa_id = sa.id
    await db.delete(sa)
    await log_action(
        db,
        action="scheduled_action.deleted",
        entity_type="scheduled_action",
        entity_id=sa_id,
        user_id=user.id,
        before_state=before,
    )
    await db.commit()


@router.post("/{scheduled_action_id}/run-now", response_model=ActionRunOut, status_code=201)
async def run_now(
    scheduled_action_id: int,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(current_superuser),
) -> ActionRunOut:
    """Create an immediate ActionRun for this schedule and dispatch it.

    Bypasses the cron walk; same dispatch path as the scheduler. Built-in
    actions are routed through the orchestrator's per-host fork that lands
    in C5 — until then this endpoint will create the row, attempt
    dispatch, and let the existing orchestrator surface "no playbook" if
    the action is a built-in.
    """
    sa = await db.get(ScheduledAction, scheduled_action_id)
    if sa is None:
        raise HTTPException(status_code=404, detail="Scheduled action not found")

    action = ACTION_REGISTRY.get(sa.action_key)
    if action is None:
        raise HTTPException(
            status_code=400,
            detail=f"Action {sa.action_key!r} is no longer registered",
        )

    # Skip if a non-terminal run for this schedule already exists.
    in_flight = await db.scalar(
        select(ActionRun.id).where(
            ActionRun.scheduled_action_id == sa.id,
            ActionRun.status.in_(["queued", "running"]),
        ).limit(1)
    )
    if in_flight is not None:
        raise HTTPException(
            status_code=409,
            detail={
                "detail": "An identical run is already in flight",
                "running_run_id": in_flight,
            },
        )

    run = ActionRun(
        action_key=sa.action_key,
        action_version=action.version,
        host_id=sa.target_id if sa.target_kind == "host" else None,
        group_id=sa.target_id if sa.target_kind == "group" else None,
        scheduled_action_id=sa.id,
        parameters=sa.parameters,
        parallelism=sa.batch_size,
        snapshot_enabled=sa.snapshot_enabled,
        verify_enabled=sa.verify_enabled,
        auto_rollback=sa.auto_rollback,
        status="queued",
        triggered_by_user_id=user.id,
    )
    db.add(run)
    await db.flush()
    await db.refresh(run)
    sa.last_dispatched_at = datetime.now(tz=run.created_at.tzinfo)
    run_id = run.id

    await log_action(
        db,
        action="scheduled_action.dispatched",
        entity_type="scheduled_action",
        entity_id=sa.id,
        user_id=user.id,
        after_state={"action_run_id": run_id, "manual": True},
    )
    await db.commit()

    # Fire the Celery task. Built-in dispatch routing lands in C5; for
    # pack-supplied actions, the existing orchestrator handles the run.
    try:
        from app.tasks import celery_app  # noqa: PLC0415

        celery_app.send_task(
            "app.tasks.action_orchestrator.run_action", args=[run_id]
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("Failed to dispatch scheduled run %d: %s", run_id, exc)

    out = ActionRunOut.model_validate(run, from_attributes=True)
    out.host_runs = []
    return out


@router.get(
    "/{scheduled_action_id}/runs", response_model=list[ActionRunOut]
)
async def list_runs(
    scheduled_action_id: int,
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(current_superuser),
) -> list[ActionRunOut]:
    sa = await db.get(ScheduledAction, scheduled_action_id)
    if sa is None:
        raise HTTPException(status_code=404, detail="Scheduled action not found")

    rows = (
        await db.execute(
            select(ActionRun)
            .where(ActionRun.scheduled_action_id == sa.id)
            .order_by(desc(ActionRun.created_at))
            .offset(offset)
            .limit(limit)
        )
    ).scalars().all()

    out: list[ActionRunOut] = []
    for r in rows:
        item = ActionRunOut.model_validate(r, from_attributes=True)
        item.host_runs = []
        out.append(item)
    return out


@router.post("/validate-cron", response_model=ValidateCronResponse)
async def validate_cron(
    body: ValidateCronRequest,
    _: User = Depends(current_superuser),
) -> ValidateCronResponse:
    """Cron syntax check + next-3-fire-times preview. Used by the
    frontend's <CronInput /> for live feedback as the operator types."""
    if not croniter.is_valid(body.cron):
        return ValidateCronResponse(valid=False, message="Invalid cron expression")
    try:
        from datetime import UTC  # noqa: PLC0415

        now = datetime.now(UTC)
        it = croniter(body.cron, now)
        next_runs = [it.get_next(datetime) for _ in range(3)]
    except Exception as exc:  # noqa: BLE001
        return ValidateCronResponse(valid=False, message=str(exc))
    return ValidateCronResponse(valid=True, next_run_at=next_runs)
