from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api._gitops_lock import check_gitops_lock
from app.api._scoped_rules import (
    RuleAudit,
    apply_fields,
    commit_created,
    commit_updated,
    delete_and_commit,
    require_group,
    require_host,
    scoped_row_or_404,
)
from app.auth.permissions import require
from app.db import get_db
from app.models.user import User
from app.services.merge import get_effective_services
from app.services.models import ServiceRule
from app.services.schemas import (
    EffectiveServiceResponse,
    ServiceRuleCreate,
    ServiceRuleResponse,
    ServiceRuleUpdate,
)

router = APIRouter(tags=["services"])

_NOT_FOUND = "Service rule not found"


def _audit_state(rule: ServiceRule) -> dict:
    return {"service_name": rule.service_name, "state": str(rule.state)}


_AUDIT = RuleAudit("service_rule", _audit_state)


# ---------------------------------------------------------------------------
# Group-level CRUD
# ---------------------------------------------------------------------------


@router.get("/groups/{group_id}/services", response_model=list[ServiceRuleResponse])
async def list_group_services(
    group_id: int,
    _: User = Depends(require("hosts.view")),
    db: AsyncSession = Depends(get_db),
):
    await require_group(db, group_id)

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
    user: User = Depends(require("config.edit")),
    db: AsyncSession = Depends(get_db),
):
    await check_gitops_lock(group_id, db)
    await require_group(db, group_id)

    rule = ServiceRule(group_id=group_id, **body.model_dump())
    db.add(rule)
    await db.flush()
    return await commit_created(db, rule, _AUDIT, user)


@router.put(
    "/groups/{group_id}/services/{rule_id}",
    response_model=ServiceRuleResponse,
)
async def update_group_service(
    group_id: int,
    rule_id: int,
    body: ServiceRuleUpdate,
    user: User = Depends(require("config.edit")),
    db: AsyncSession = Depends(get_db),
):
    await check_gitops_lock(group_id, db)
    rule = await scoped_row_or_404(
        db, ServiceRule, rule_id, ServiceRule.group_id == group_id, detail=_NOT_FOUND
    )
    before = _audit_state(rule)
    apply_fields(rule, body.model_dump(exclude_none=True))
    await db.flush()
    return await commit_updated(db, rule, _AUDIT, user, before)


@router.delete("/groups/{group_id}/services/{rule_id}", status_code=204)
async def delete_group_service(
    group_id: int,
    rule_id: int,
    user: User = Depends(require("config.edit")),
    db: AsyncSession = Depends(get_db),
):
    await check_gitops_lock(group_id, db)
    rule = await scoped_row_or_404(
        db, ServiceRule, rule_id, ServiceRule.group_id == group_id, detail=_NOT_FOUND
    )
    await delete_and_commit(db, rule, _AUDIT, user)


# ---------------------------------------------------------------------------
# Host-level overrides
# ---------------------------------------------------------------------------


@router.get("/hosts/{host_id}/services", response_model=list[ServiceRuleResponse])
async def list_host_services(
    host_id: int,
    _: User = Depends(require("hosts.view")),
    db: AsyncSession = Depends(get_db),
):
    await require_host(db, host_id)

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
    user: User = Depends(require("config.edit")),
    db: AsyncSession = Depends(get_db),
):
    await require_host(db, host_id)

    rule = ServiceRule(host_id=host_id, **body.model_dump())
    db.add(rule)
    await db.flush()
    return await commit_created(db, rule, _AUDIT, user)


@router.put(
    "/hosts/{host_id}/services/{rule_id}",
    response_model=ServiceRuleResponse,
)
async def update_host_service(
    host_id: int,
    rule_id: int,
    body: ServiceRuleUpdate,
    user: User = Depends(require("config.edit")),
    db: AsyncSession = Depends(get_db),
):
    rule = await scoped_row_or_404(
        db, ServiceRule, rule_id, ServiceRule.host_id == host_id, detail=_NOT_FOUND
    )
    before = _audit_state(rule)
    apply_fields(rule, body.model_dump(exclude_none=True))
    await db.flush()
    return await commit_updated(db, rule, _AUDIT, user, before)


@router.delete("/hosts/{host_id}/services/{rule_id}", status_code=204)
async def delete_host_service(
    host_id: int,
    rule_id: int,
    user: User = Depends(require("config.edit")),
    db: AsyncSession = Depends(get_db),
):
    rule = await scoped_row_or_404(
        db, ServiceRule, rule_id, ServiceRule.host_id == host_id, detail=_NOT_FOUND
    )
    await delete_and_commit(db, rule, _AUDIT, user)


# ---------------------------------------------------------------------------
# Effective config (merged group + host overrides)
# ---------------------------------------------------------------------------


@router.get(
    "/hosts/{host_id}/effective-services",
    response_model=list[EffectiveServiceResponse],
)
async def effective_services(
    host_id: int,
    _: User = Depends(require("hosts.view")),
    db: AsyncSession = Depends(get_db),
):
    await require_host(db, host_id)

    return await get_effective_services(host_id, db)
