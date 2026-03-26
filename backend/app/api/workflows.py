from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.responses import Response

from app.db import get_db
from app.auth.users import current_superuser
from app.models.user import User
from app.models.host import Host
from app.packages.models import PackageRule
from app.workflows.models import UpdateWorkflow, WorkflowRun, WorkflowHostRun, WorkflowRunStatus
from app.workflows.schemas import (
    UpdateWorkflowCreate,
    UpdateWorkflowUpdate,
    UpdateWorkflowResponse,
    WorkflowRunResponse,
    WorkflowRunDetailResponse,
    WorkflowHostRunResponse,
    WorkflowTriggerResponse,
)
from app.audit.logger import log_action

router = APIRouter(tags=["workflows"])


@router.get("/groups/{group_id}/workflow", response_model=UpdateWorkflowResponse)
async def get_group_workflow(
    group_id: int,
    _: User = Depends(current_superuser),
    db: AsyncSession = Depends(get_db),
):
    workflow = await db.scalar(
        select(UpdateWorkflow).where(UpdateWorkflow.group_id == group_id)
    )
    if not workflow:
        raise HTTPException(status_code=404, detail="Workflow not found")
    return workflow


@router.put("/groups/{group_id}/workflow", response_model=UpdateWorkflowResponse)
async def upsert_group_workflow(
    group_id: int,
    body: UpdateWorkflowUpdate,
    user: User = Depends(current_superuser),
    db: AsyncSession = Depends(get_db),
):
    workflow = await db.scalar(
        select(UpdateWorkflow).where(UpdateWorkflow.group_id == group_id)
    )

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
    workflow = await db.scalar(
        select(UpdateWorkflow).where(UpdateWorkflow.group_id == group_id)
    )
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
    workflow = await db.scalar(
        select(UpdateWorkflow).where(UpdateWorkflow.group_id == group_id)
    )
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
        from app.celery_app import celery_app
        celery_app.send_task(
            "app.tasks.workflow_orchestrator.run_group_workflow",
            args=[workflow.id, run.id],
        )
    except Exception:
        pass

    return WorkflowTriggerResponse(run_id=run.id, message="Workflow run started")


@router.get("/groups/{group_id}/workflow/runs", response_model=list[WorkflowRunResponse])
async def list_workflow_runs(
    group_id: int,
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    _: User = Depends(current_superuser),
    db: AsyncSession = Depends(get_db),
):
    workflow = await db.scalar(
        select(UpdateWorkflow).where(UpdateWorkflow.group_id == group_id)
    )
    if not workflow:
        raise HTTPException(status_code=404, detail="Workflow not found")

    result = await db.execute(
        select(WorkflowRun)
        .where(WorkflowRun.workflow_id == workflow.id)
        .order_by(WorkflowRun.created_at.desc())
        .limit(limit)
        .offset(offset)
    )
    return result.scalars().all()


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
