from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_db
from app.auth.users import current_active_user
from app.models.user import User
from app.models.host_group import HostGroup
from app.models.host import Host
from app.services.models import ServiceRule
from app.services.schemas import (
    ServiceRuleCreate,
    ServiceRuleUpdate,
    ServiceRuleResponse,
    EffectiveServiceResponse,
)
from app.services.merge import get_effective_services
from app.audit.logger import log_action

router = APIRouter(tags=["services"])


# ---------------------------------------------------------------------------
# Group-level CRUD
# ---------------------------------------------------------------------------


@router.get("/groups/{group_id}/services", response_model=list[ServiceRuleResponse])
async def list_group_services(
    group_id: int,
    _: User = Depends(current_active_user),
    db: AsyncSession = Depends(get_db),
):
    group = await db.scalar(select(HostGroup).where(HostGroup.id == group_id))
    if not group:
        raise HTTPException(status_code=404, detail="Group not found")

    result = await db.execute(
        select(ServiceRule)
        .where(ServiceRule.group_id == group_id)
        .order_by(ServiceRule.priority.desc(), ServiceRule.id)
    )
    return result.scalars().all()


@router.post(
    "/groups/{group_id}/services",
    response_model=ServiceRuleResponse,
    status_code=201,
)
async def create_group_service(
    group_id: int,
    body: ServiceRuleCreate,
    user: User = Depends(current_active_user),
    db: AsyncSession = Depends(get_db),
):
    group = await db.scalar(select(HostGroup).where(HostGroup.id == group_id))
    if not group:
        raise HTTPException(status_code=404, detail="Group not found")

    rule = ServiceRule(group_id=group_id, **body.model_dump())
    db.add(rule)
    await db.flush()

    await log_action(
        db=db,
        action="create",
        entity_type="service_rule",
        entity_id=rule.id,
        user_id=user.id,
        after_state={"service_name": rule.service_name, "state": str(rule.state)},
    )
    await db.commit()
    await db.refresh(rule)
    return rule


@router.put(
    "/groups/{group_id}/services/{rule_id}",
    response_model=ServiceRuleResponse,
)
async def update_group_service(
    group_id: int,
    rule_id: int,
    body: ServiceRuleUpdate,
    user: User = Depends(current_active_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(ServiceRule).where(
            ServiceRule.id == rule_id,
            ServiceRule.group_id == group_id,
        )
    )
    rule = result.scalar_one_or_none()
    if not rule:
        raise HTTPException(status_code=404, detail="Service rule not found")

    before = {"service_name": rule.service_name, "state": str(rule.state)}

    for field, value in body.model_dump(exclude_none=True).items():
        setattr(rule, field, value)

    await db.flush()

    await log_action(
        db=db,
        action="update",
        entity_type="service_rule",
        entity_id=rule.id,
        user_id=user.id,
        before_state=before,
        after_state={"service_name": rule.service_name, "state": str(rule.state)},
    )
    await db.commit()
    await db.refresh(rule)
    return rule


@router.delete("/groups/{group_id}/services/{rule_id}", status_code=204)
async def delete_group_service(
    group_id: int,
    rule_id: int,
    user: User = Depends(current_active_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(ServiceRule).where(
            ServiceRule.id == rule_id,
            ServiceRule.group_id == group_id,
        )
    )
    rule = result.scalar_one_or_none()
    if not rule:
        raise HTTPException(status_code=404, detail="Service rule not found")

    before = {"service_name": rule.service_name, "state": str(rule.state)}
    rule_id_for_log = rule.id

    await db.delete(rule)
    await db.flush()

    await log_action(
        db=db,
        action="delete",
        entity_type="service_rule",
        entity_id=rule_id_for_log,
        user_id=user.id,
        before_state=before,
    )
    await db.commit()


# ---------------------------------------------------------------------------
# Host-level overrides
# ---------------------------------------------------------------------------


@router.get("/hosts/{host_id}/services", response_model=list[ServiceRuleResponse])
async def list_host_services(
    host_id: int,
    _: User = Depends(current_active_user),
    db: AsyncSession = Depends(get_db),
):
    host = await db.scalar(select(Host).where(Host.id == host_id))
    if not host:
        raise HTTPException(status_code=404, detail="Host not found")

    result = await db.execute(
        select(ServiceRule)
        .where(ServiceRule.host_id == host_id)
        .order_by(ServiceRule.priority.desc(), ServiceRule.id)
    )
    return result.scalars().all()


@router.post(
    "/hosts/{host_id}/services",
    response_model=ServiceRuleResponse,
    status_code=201,
)
async def create_host_service(
    host_id: int,
    body: ServiceRuleCreate,
    user: User = Depends(current_active_user),
    db: AsyncSession = Depends(get_db),
):
    host = await db.scalar(select(Host).where(Host.id == host_id))
    if not host:
        raise HTTPException(status_code=404, detail="Host not found")

    rule = ServiceRule(host_id=host_id, **body.model_dump())
    db.add(rule)
    await db.flush()

    await log_action(
        db=db,
        action="create",
        entity_type="service_rule",
        entity_id=rule.id,
        user_id=user.id,
        after_state={"service_name": rule.service_name, "state": str(rule.state)},
    )
    await db.commit()
    await db.refresh(rule)
    return rule


@router.put(
    "/hosts/{host_id}/services/{rule_id}",
    response_model=ServiceRuleResponse,
)
async def update_host_service(
    host_id: int,
    rule_id: int,
    body: ServiceRuleUpdate,
    user: User = Depends(current_active_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(ServiceRule).where(
            ServiceRule.id == rule_id,
            ServiceRule.host_id == host_id,
        )
    )
    rule = result.scalar_one_or_none()
    if not rule:
        raise HTTPException(status_code=404, detail="Service rule not found")

    before = {"service_name": rule.service_name, "state": str(rule.state)}

    for field, value in body.model_dump(exclude_none=True).items():
        setattr(rule, field, value)

    await db.flush()

    await log_action(
        db=db,
        action="update",
        entity_type="service_rule",
        entity_id=rule.id,
        user_id=user.id,
        before_state=before,
        after_state={"service_name": rule.service_name, "state": str(rule.state)},
    )
    await db.commit()
    await db.refresh(rule)
    return rule


@router.delete("/hosts/{host_id}/services/{rule_id}", status_code=204)
async def delete_host_service(
    host_id: int,
    rule_id: int,
    user: User = Depends(current_active_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(ServiceRule).where(
            ServiceRule.id == rule_id,
            ServiceRule.host_id == host_id,
        )
    )
    rule = result.scalar_one_or_none()
    if not rule:
        raise HTTPException(status_code=404, detail="Service rule not found")

    before = {"service_name": rule.service_name, "state": str(rule.state)}
    rule_id_for_log = rule.id

    await db.delete(rule)
    await db.flush()

    await log_action(
        db=db,
        action="delete",
        entity_type="service_rule",
        entity_id=rule_id_for_log,
        user_id=user.id,
        before_state=before,
    )
    await db.commit()


# ---------------------------------------------------------------------------
# Effective config (merged group + host overrides)
# ---------------------------------------------------------------------------


@router.get(
    "/hosts/{host_id}/effective-services",
    response_model=list[EffectiveServiceResponse],
)
async def effective_services(
    host_id: int,
    _: User = Depends(current_active_user),
    db: AsyncSession = Depends(get_db),
):
    host = await db.scalar(select(Host).where(Host.id == host_id))
    if not host:
        raise HTTPException(status_code=404, detail="Host not found")

    return await get_effective_services(host_id, db)
