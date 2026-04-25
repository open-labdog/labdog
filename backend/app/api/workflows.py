import logging

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.responses import Response

from app.audit.logger import log_action
from app.auth.users import current_superuser
from app.db import get_db
from app.models.host import Host
from app.models.user import User
from app.packages.models import PackageRule
from app.workflows.models import UpdateWorkflow, WorkflowHostRun, WorkflowRun, WorkflowRunStatus
from app.workflows.schemas import (
    UpdateWorkflowCreate,
    UpdateWorkflowResponse,
    UpdateWorkflowUpdate,
    WorkflowHostRunResponse,
    WorkflowRunDetailResponse,
    WorkflowRunResponse,
    WorkflowTriggerResponse,
)

logger = logging.getLogger(__name__)

router = APIRouter(tags=["workflows"])


@router.get("/workflows/summary")
async def list_workflows_summary(
    _: User = Depends(current_superuser),
    db: AsyncSession = Depends(get_db),
):
    """Return all workflows with group info, last run status, and host counts."""
    from app.models.host import HostGroupMembership
    from app.models.host_group import HostGroup

    wf_result = await db.execute(
        select(UpdateWorkflow, HostGroup.name, HostGroup.category)
        .join(HostGroup, UpdateWorkflow.group_id == HostGroup.id)
        .order_by(HostGroup.name)
    )
    rows = wf_result.all()
    if not rows:
        return []

    group_ids = [wf.group_id for wf, _, _ in rows]
    workflow_ids = [wf.id for wf, _, _ in rows]

    # Host counts per group
    hc_result = await db.execute(
        select(HostGroupMembership.c.group_id, func.count())
        .where(HostGroupMembership.c.group_id.in_(group_ids))
        .group_by(HostGroupMembership.c.group_id)
    )
    host_counts = {r[0]: r[1] for r in hc_result}

    # Latest run per workflow (single query using DISTINCT ON)
    latest_runs_q = await db.execute(
        select(WorkflowRun)
        .where(WorkflowRun.workflow_id.in_(workflow_ids))
        .order_by(WorkflowRun.workflow_id, WorkflowRun.created_at.desc())
        .distinct(WorkflowRun.workflow_id)
    )
    latest_by_wf: dict[int, WorkflowRun] = {r.workflow_id: r for r in latest_runs_q.scalars().all()}

    out = []
    for wf, group_name, group_category in rows:
        last_run = latest_by_wf.get(wf.id)
        out.append(
            {
                "id": wf.id,
                "group_id": wf.group_id,
                "group_name": group_name,
                "group_category": group_category,
                "batch_size": wf.batch_size,
                "schedule_cron": wf.schedule_cron,
                "pre_update_snapshot": wf.pre_update_snapshot,
                "auto_rollback": wf.auto_rollback,
                "auto_reboot": wf.auto_reboot,
                "enabled": wf.enabled,
                "host_count": host_counts.get(wf.group_id, 0),
                "last_run": {
                    "id": last_run.id,
                    "status": last_run.status.value
                    if hasattr(last_run.status, "value")
                    else last_run.status,
                    "started_at": last_run.started_at.isoformat() if last_run.started_at else None,
                    "completed_at": last_run.completed_at.isoformat()
                    if last_run.completed_at
                    else None,
                    "created_at": last_run.created_at.isoformat() if last_run.created_at else None,
                }
                if last_run
                else None,
            }
        )
    return out


@router.get("/groups/{group_id}/workflow", response_model=UpdateWorkflowResponse | None)
async def get_group_workflow(
    group_id: int,
    _: User = Depends(current_superuser),
    db: AsyncSession = Depends(get_db),
):
    """Return the group's workflow, or `null` when none has been configured.

    Returning a 200 with `null` instead of a 404 keeps the frontend's
    console clean on first load — the "not configured yet" state is
    expected, not an error.
    """
    return await db.scalar(select(UpdateWorkflow).where(UpdateWorkflow.group_id == group_id))


@router.put("/groups/{group_id}/workflow", response_model=UpdateWorkflowResponse)
async def upsert_group_workflow(
    group_id: int,
    body: UpdateWorkflowUpdate,
    user: User = Depends(current_superuser),
    db: AsyncSession = Depends(get_db),
):
    from app.actions.registry import ACTION_REGISTRY

    # Validate cron expression before touching the DB
    if body.schedule_cron is not None:
        from croniter import croniter

        if not croniter.is_valid(body.schedule_cron):
            raise HTTPException(status_code=422, detail="Invalid cron expression")

    if body.action_key is not None and body.action_key not in ACTION_REGISTRY:
        raise HTTPException(status_code=400, detail=f"Unknown action_key: {body.action_key!r}")

    if body.action_key == "linux-os-upgrade":
        params = body.action_parameters or {}
        missing = [k for k in ("current_version", "next_version") if not params.get(k)]
        if missing:
            raise HTTPException(status_code=422, detail=f"linux-os-upgrade requires: {missing}")

    workflow = await db.scalar(select(UpdateWorkflow).where(UpdateWorkflow.group_id == group_id))

    if workflow is None:
        # Create with defaults from UpdateWorkflowCreate, then apply body
        defaults = UpdateWorkflowCreate()
        workflow = UpdateWorkflow(
            group_id=group_id,
            batch_size=defaults.batch_size,
            schedule_cron=defaults.schedule_cron,
            pre_update_snapshot=defaults.pre_update_snapshot,
            auto_rollback=defaults.auto_rollback,
            verification_prompt=defaults.verification_prompt,
            auto_reboot=defaults.auto_reboot,
            enabled=defaults.enabled,
        )
        db.add(workflow)
        action = "create"
    else:
        action = "update"

    for field, value in body.model_dump(exclude_unset=True).items():
        setattr(workflow, field, value)

    await db.flush()

    # Auto-add qemu-guest-agent when enabling
    if workflow.enabled:
        existing_rule = await db.scalar(
            select(PackageRule).where(
                PackageRule.group_id == group_id,
                PackageRule.package_name == "qemu-guest-agent",
            )
        )
        if not existing_rule:
            rule = PackageRule(
                group_id=group_id,
                package_name="qemu-guest-agent",
                state="present",
            )
            db.add(rule)
            await db.flush()
            await log_action(
                db=db,
                action="create",
                entity_type="package_rule",
                entity_id=rule.id,
                user_id=user.id,
                after_state={
                    "package_name": rule.package_name,
                    "state": str(rule.state),
                    "version": rule.version,
                },
            )

    await log_action(
        db=db,
        action=action,
        entity_type="update_workflow",
        entity_id=workflow.id,
        user_id=user.id,
        after_state={
            "group_id": workflow.group_id,
            "batch_size": workflow.batch_size,
            "enabled": workflow.enabled,
        },
    )
    await db.commit()
    await db.refresh(workflow)
    return workflow


@router.delete("/groups/{group_id}/workflow", status_code=204)
async def delete_group_workflow(
    group_id: int,
    user: User = Depends(current_superuser),
    db: AsyncSession = Depends(get_db),
):
    workflow = await db.scalar(select(UpdateWorkflow).where(UpdateWorkflow.group_id == group_id))
    if not workflow:
        raise HTTPException(status_code=404, detail="Workflow not found")

    workflow_id = workflow.id
    await db.delete(workflow)
    await db.flush()

    await log_action(
        db=db,
        action="delete",
        entity_type="update_workflow",
        entity_id=workflow_id,
        user_id=user.id,
        before_state={"group_id": group_id},
    )
    await db.commit()
    return Response(status_code=204)


@router.post("/groups/{group_id}/workflow/run", response_model=WorkflowTriggerResponse)
async def trigger_workflow_run(
    group_id: int,
    user: User = Depends(current_superuser),
    db: AsyncSession = Depends(get_db),
):
    workflow = await db.scalar(select(UpdateWorkflow).where(UpdateWorkflow.group_id == group_id))
    if not workflow:
        raise HTTPException(status_code=404, detail="Workflow not found")

    # Check for active runs
    active_run = await db.scalar(
        select(WorkflowRun).where(
            WorkflowRun.workflow_id == workflow.id,
            WorkflowRun.status.in_([WorkflowRunStatus.pending, WorkflowRunStatus.running]),
        )
    )
    if active_run:
        raise HTTPException(
            status_code=409,
            detail="A workflow run is already active for this group",
        )

    run = WorkflowRun(
        workflow_id=workflow.id,
        status=WorkflowRunStatus.pending,
        triggered_by=user.id,
    )
    db.add(run)
    await db.flush()
    await db.refresh(run)

    await db.commit()

    # Dispatch Celery task
    try:
        from app.tasks import celery_app

        celery_app.send_task(
            "app.tasks.workflow_orchestrator.run_group_workflow",
            args=[workflow.id, run.id],
        )
    except Exception as exc:
        logger.warning("Failed to dispatch workflow task: %s", exc)

    return WorkflowTriggerResponse(run_id=run.id, message="Workflow run started")


@router.get("/groups/{group_id}/workflow/runs", response_model=list[WorkflowRunResponse])
async def list_workflow_runs(
    group_id: int,
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    _: User = Depends(current_superuser),
    db: AsyncSession = Depends(get_db),
):
    """Return the group's workflow runs, or `[]` when no workflow exists yet."""
    workflow = await db.scalar(select(UpdateWorkflow).where(UpdateWorkflow.group_id == group_id))
    if not workflow:
        return []

    result = await db.execute(
        select(WorkflowRun)
        .where(WorkflowRun.workflow_id == workflow.id)
        .order_by(WorkflowRun.created_at.desc())
        .limit(limit)
        .offset(offset)
    )
    return result.scalars().all()


@router.get(
    "/hosts/{host_id}/latest-workflow-run",
    response_model=WorkflowHostRunResponse | None,
)
async def get_host_latest_workflow_run(
    host_id: int,
    _: User = Depends(current_superuser),
    db: AsyncSession = Depends(get_db),
):
    """Return the most recent workflow host run for a host, or `null` if none."""
    result = await db.execute(
        select(WorkflowHostRun, Host.hostname)
        .join(Host, WorkflowHostRun.host_id == Host.id)
        .where(WorkflowHostRun.host_id == host_id)
        .order_by(WorkflowHostRun.id.desc())
        .limit(1)
    )
    row = result.first()
    if not row:
        return None
    hr, hostname = row
    return WorkflowHostRunResponse(
        id=hr.id,
        host_id=hr.host_id,
        hostname=hostname,
        step=str(hr.step.value) if hasattr(hr.step, "value") else str(hr.step),
        status=str(hr.status.value) if hasattr(hr.status, "value") else str(hr.status),
        snapshot_name=hr.snapshot_name,
        error_message=hr.error_message,
        started_at=hr.started_at,
        completed_at=hr.completed_at,
    )


@router.get("/workflow-runs/{run_id}", response_model=WorkflowRunDetailResponse)
async def get_workflow_run_detail(
    run_id: int,
    _: User = Depends(current_superuser),
    db: AsyncSession = Depends(get_db),
):
    run = await db.scalar(select(WorkflowRun).where(WorkflowRun.id == run_id))
    if not run:
        raise HTTPException(status_code=404, detail="Workflow run not found")

    host_runs_result = await db.execute(
        select(WorkflowHostRun, Host.hostname)
        .join(Host, WorkflowHostRun.host_id == Host.id)
        .where(WorkflowHostRun.run_id == run_id)
        .order_by(WorkflowHostRun.id)
    )
    rows = host_runs_result.all()

    host_run_responses = [
        WorkflowHostRunResponse(
            id=hr.id,
            host_id=hr.host_id,
            hostname=hostname,
            step=str(hr.step.value) if hasattr(hr.step, "value") else str(hr.step),
            status=str(hr.status.value) if hasattr(hr.status, "value") else str(hr.status),
            snapshot_name=hr.snapshot_name,
            error_message=hr.error_message,
            started_at=hr.started_at,
            completed_at=hr.completed_at,
        )
        for hr, hostname in rows
    ]

    return WorkflowRunDetailResponse(
        id=run.id,
        workflow_id=run.workflow_id,
        status=str(run.status.value) if hasattr(run.status, "value") else str(run.status),
        started_at=run.started_at,
        completed_at=run.completed_at,
        triggered_by=run.triggered_by,
        created_at=run.created_at,
        host_runs=host_run_responses,
    )
