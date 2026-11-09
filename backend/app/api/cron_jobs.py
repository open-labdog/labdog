from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.responses import Response

from app.api._gitops_lock import check_gitops_lock
from app.api._scoped_rules import (
    RuleAudit,
    apply_fields,
    commit_created,
    commit_updated,
    delete_and_commit,
    flush_or_409,
    require_group,
    require_host,
    scoped_row_or_404,
)
from app.auth.users import current_active_user
from app.cron.merge import get_effective_cron_jobs
from app.cron.models import CronJob
from app.cron.schemas import (
    CronJobCreate,
    CronJobResponse,
    CronJobUpdate,
    EffectiveCronJobResponse,
)
from app.db import get_db
from app.models.user import User

router = APIRouter(tags=["cron-jobs"])

_NOT_FOUND = "Cron job rule not found"


def _audit_state(rule: CronJob) -> dict:
    return {"name": rule.name, "user": rule.user, "state": str(rule.state)}


_AUDIT = RuleAudit("cron_job", _audit_state)


# ---------------------------------------------------------------------------
# Group-level CRUD
# ---------------------------------------------------------------------------


@router.get("/groups/{group_id}/cron-jobs", response_model=list[CronJobResponse])
async def list_group_cron_jobs(
    group_id: int,
    _: User = Depends(current_active_user),
    db: AsyncSession = Depends(get_db),
):
    await require_group(db, group_id)

    result = await db.execute(
        select(CronJob)
        .where(CronJob.group_id == group_id)
        .order_by(CronJob.priority.desc(), CronJob.id)
    )
    return result.scalars().all()


@router.post(
    "/groups/{group_id}/cron-jobs",
    response_model=CronJobResponse,
    status_code=201,
)
async def create_group_cron_job(
    group_id: int,
    body: CronJobCreate,
    user: User = Depends(current_active_user),
    db: AsyncSession = Depends(get_db),
):
    await check_gitops_lock(group_id, db)
    await require_group(db, group_id)

    rule = CronJob(group_id=group_id, **body.model_dump())
    db.add(rule)
    await flush_or_409(
        db,
        lambda: f"Cron job '{body.name}' for user '{body.user}' already exists in this group",
    )
    return await commit_created(db, rule, _AUDIT, user)


@router.put(
    "/groups/{group_id}/cron-jobs/{rule_id}",
    response_model=CronJobResponse,
)
async def update_group_cron_job(
    group_id: int,
    rule_id: int,
    body: CronJobUpdate,
    user: User = Depends(current_active_user),
    db: AsyncSession = Depends(get_db),
):
    await check_gitops_lock(group_id, db)
    rule = await scoped_row_or_404(
        db, CronJob, rule_id, CronJob.group_id == group_id, detail=_NOT_FOUND
    )
    before = _audit_state(rule)
    apply_fields(rule, body.model_dump(exclude_unset=True))
    await flush_or_409(
        db,
        lambda: f"Cron job '{rule.name}' for user '{rule.user}' already exists in this group",
    )
    return await commit_updated(db, rule, _AUDIT, user, before)


@router.delete("/groups/{group_id}/cron-jobs/{rule_id}", status_code=204)
async def delete_group_cron_job(
    group_id: int,
    rule_id: int,
    user: User = Depends(current_active_user),
    db: AsyncSession = Depends(get_db),
):
    await check_gitops_lock(group_id, db)
    rule = await scoped_row_or_404(
        db, CronJob, rule_id, CronJob.group_id == group_id, detail=_NOT_FOUND
    )
    await delete_and_commit(db, rule, _AUDIT, user)
    return Response(status_code=204)


# ---------------------------------------------------------------------------
# Host-level overrides
# ---------------------------------------------------------------------------


@router.get("/hosts/{host_id}/cron-jobs", response_model=list[CronJobResponse])
async def list_host_cron_jobs(
    host_id: int,
    _: User = Depends(current_active_user),
    db: AsyncSession = Depends(get_db),
):
    await require_host(db, host_id)

    result = await db.execute(
        select(CronJob)
        .where(CronJob.host_id == host_id)
        .order_by(CronJob.priority.desc(), CronJob.id)
    )
    return result.scalars().all()


@router.post(
    "/hosts/{host_id}/cron-jobs",
    response_model=CronJobResponse,
    status_code=201,
)
async def create_host_cron_job(
    host_id: int,
    body: CronJobCreate,
    user: User = Depends(current_active_user),
    db: AsyncSession = Depends(get_db),
):
    await require_host(db, host_id)

    rule = CronJob(host_id=host_id, **body.model_dump())
    db.add(rule)
    await flush_or_409(
        db,
        lambda: f"Cron job '{body.name}' for user '{body.user}' already exists on this host",
    )
    return await commit_created(db, rule, _AUDIT, user)


@router.put(
    "/hosts/{host_id}/cron-jobs/{rule_id}",
    response_model=CronJobResponse,
)
async def update_host_cron_job(
    host_id: int,
    rule_id: int,
    body: CronJobUpdate,
    user: User = Depends(current_active_user),
    db: AsyncSession = Depends(get_db),
):
    rule = await scoped_row_or_404(
        db, CronJob, rule_id, CronJob.host_id == host_id, detail=_NOT_FOUND
    )
    before = _audit_state(rule)
    apply_fields(rule, body.model_dump(exclude_unset=True))
    await flush_or_409(
        db,
        lambda: f"Cron job '{rule.name}' for user '{rule.user}' already exists on this host",
    )
    return await commit_updated(db, rule, _AUDIT, user, before)


@router.delete("/hosts/{host_id}/cron-jobs/{rule_id}", status_code=204)
async def delete_host_cron_job(
    host_id: int,
    rule_id: int,
    user: User = Depends(current_active_user),
    db: AsyncSession = Depends(get_db),
):
    rule = await scoped_row_or_404(
        db, CronJob, rule_id, CronJob.host_id == host_id, detail=_NOT_FOUND
    )
    await delete_and_commit(db, rule, _AUDIT, user)
    return Response(status_code=204)


# ---------------------------------------------------------------------------
# Effective config (merged group + host overrides)
# ---------------------------------------------------------------------------


@router.get(
    "/hosts/{host_id}/effective-cron-jobs",
    response_model=list[EffectiveCronJobResponse],
)
async def effective_cron_jobs(
    host_id: int,
    _: User = Depends(current_active_user),
    db: AsyncSession = Depends(get_db),
):
    await require_host(db, host_id)

    return await get_effective_cron_jobs(host_id, db)
