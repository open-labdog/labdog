from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.responses import Response

from app.audit.logger import log_action
from app.auth.users import current_superuser
from app.cron.merge import get_effective_cron_jobs
from app.cron.models import CronJob
from app.cron.schemas import (
    CronJobCreate,
    CronJobResponse,
    CronJobUpdate,
    EffectiveCronJobResponse,
)
from app.db import get_db
from app.models.host import Host
from app.models.host_group import HostGroup
from app.models.user import User

router = APIRouter(tags=["cron-jobs"])


# ---------------------------------------------------------------------------
# Group-level CRUD
# ---------------------------------------------------------------------------


@router.get("/groups/{group_id}/cron-jobs", response_model=list[CronJobResponse])
async def list_group_cron_jobs(
    group_id: int,
    _: User = Depends(current_superuser),
    db: AsyncSession = Depends(get_db),
):
    group = await db.scalar(select(HostGroup).where(HostGroup.id == group_id))
    if not group:
        raise HTTPException(status_code=404, detail="Group not found")

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
    user: User = Depends(current_superuser),
    db: AsyncSession = Depends(get_db),
):
    group = await db.scalar(select(HostGroup).where(HostGroup.id == group_id))
    if not group:
        raise HTTPException(status_code=404, detail="Group not found")

    rule = CronJob(group_id=group_id, **body.model_dump())
    db.add(rule)
    try:
        await db.flush()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(
            status_code=409,
            detail=f"Cron job '{body.name}' for user '{body.user}' already exists in this group",
        )

    await log_action(
        db=db,
        action="create",
        entity_type="cron_job",
        entity_id=rule.id,
        user_id=user.id,
        after_state={"name": rule.name, "user": rule.user, "state": str(rule.state)},
    )
    await db.commit()
    await db.refresh(rule)
    return rule


@router.put(
    "/groups/{group_id}/cron-jobs/{rule_id}",
    response_model=CronJobResponse,
)
async def update_group_cron_job(
    group_id: int,
    rule_id: int,
    body: CronJobUpdate,
    user: User = Depends(current_superuser),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(CronJob).where(
            CronJob.id == rule_id,
            CronJob.group_id == group_id,
        )
    )
    rule = result.scalar_one_or_none()
    if not rule:
        raise HTTPException(status_code=404, detail="Cron job rule not found")

    before = {"name": rule.name, "user": rule.user, "state": str(rule.state)}

    for field, value in body.model_dump(exclude_unset=True).items():
        setattr(rule, field, value)

    try:
        await db.flush()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(
            status_code=409,
            detail=f"Cron job '{rule.name}' for user '{rule.user}' already exists in this group",
        )

    await log_action(
        db=db,
        action="update",
        entity_type="cron_job",
        entity_id=rule.id,
        user_id=user.id,
        before_state=before,
        after_state={"name": rule.name, "user": rule.user, "state": str(rule.state)},
    )
    await db.commit()
    await db.refresh(rule)
    return rule


@router.delete("/groups/{group_id}/cron-jobs/{rule_id}", status_code=204)
async def delete_group_cron_job(
    group_id: int,
    rule_id: int,
    user: User = Depends(current_superuser),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(CronJob).where(
            CronJob.id == rule_id,
            CronJob.group_id == group_id,
        )
    )
    rule = result.scalar_one_or_none()
    if not rule:
        raise HTTPException(status_code=404, detail="Cron job rule not found")

    before = {"name": rule.name, "user": rule.user, "state": str(rule.state)}
    rule_id_for_log = rule.id

    await db.delete(rule)
    await db.flush()

    await log_action(
        db=db,
        action="delete",
        entity_type="cron_job",
        entity_id=rule_id_for_log,
        user_id=user.id,
        before_state=before,
    )
    await db.commit()
    return Response(status_code=204)


# ---------------------------------------------------------------------------
# Host-level overrides
# ---------------------------------------------------------------------------


@router.get("/hosts/{host_id}/cron-jobs", response_model=list[CronJobResponse])
async def list_host_cron_jobs(
    host_id: int,
    _: User = Depends(current_superuser),
    db: AsyncSession = Depends(get_db),
):
    host = await db.scalar(select(Host).where(Host.id == host_id))
    if not host:
        raise HTTPException(status_code=404, detail="Host not found")

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
    user: User = Depends(current_superuser),
    db: AsyncSession = Depends(get_db),
):
    host = await db.scalar(select(Host).where(Host.id == host_id))
    if not host:
        raise HTTPException(status_code=404, detail="Host not found")

    rule = CronJob(host_id=host_id, **body.model_dump())
    db.add(rule)
    try:
        await db.flush()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(
            status_code=409,
            detail=f"Cron job '{body.name}' for user '{body.user}' already exists on this host",
        )

    await log_action(
        db=db,
        action="create",
        entity_type="cron_job",
        entity_id=rule.id,
        user_id=user.id,
        after_state={"name": rule.name, "user": rule.user, "state": str(rule.state)},
    )
    await db.commit()
    await db.refresh(rule)
    return rule


@router.put(
    "/hosts/{host_id}/cron-jobs/{rule_id}",
    response_model=CronJobResponse,
)
async def update_host_cron_job(
    host_id: int,
    rule_id: int,
    body: CronJobUpdate,
    user: User = Depends(current_superuser),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(CronJob).where(
            CronJob.id == rule_id,
            CronJob.host_id == host_id,
        )
    )
    rule = result.scalar_one_or_none()
    if not rule:
        raise HTTPException(status_code=404, detail="Cron job rule not found")

    before = {"name": rule.name, "user": rule.user, "state": str(rule.state)}

    for field, value in body.model_dump(exclude_unset=True).items():
        setattr(rule, field, value)

    try:
        await db.flush()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(
            status_code=409,
            detail=f"Cron job '{rule.name}' for user '{rule.user}' already exists on this host",
        )

    await log_action(
        db=db,
        action="update",
        entity_type="cron_job",
        entity_id=rule.id,
        user_id=user.id,
        before_state=before,
        after_state={"name": rule.name, "user": rule.user, "state": str(rule.state)},
    )
    await db.commit()
    await db.refresh(rule)
    return rule


@router.delete("/hosts/{host_id}/cron-jobs/{rule_id}", status_code=204)
async def delete_host_cron_job(
    host_id: int,
    rule_id: int,
    user: User = Depends(current_superuser),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(CronJob).where(
            CronJob.id == rule_id,
            CronJob.host_id == host_id,
        )
    )
    rule = result.scalar_one_or_none()
    if not rule:
        raise HTTPException(status_code=404, detail="Cron job rule not found")

    before = {"name": rule.name, "user": rule.user, "state": str(rule.state)}
    rule_id_for_log = rule.id

    await db.delete(rule)
    await db.flush()

    await log_action(
        db=db,
        action="delete",
        entity_type="cron_job",
        entity_id=rule_id_for_log,
        user_id=user.id,
        before_state=before,
    )
    await db.commit()
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
    _: User = Depends(current_superuser),
    db: AsyncSession = Depends(get_db),
):
    host = await db.scalar(select(Host).where(Host.id == host_id))
    if not host:
        raise HTTPException(status_code=404, detail="Host not found")

    return await get_effective_cron_jobs(host_id, db)
