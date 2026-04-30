from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api._gitops_lock import check_gitops_lock
from app.auth.users import current_active_user, current_superuser
from app.db import get_db
from app.models.firewall_rule import FirewallRule
from app.models.host import Host
from app.models.host_group import HostGroup
from app.models.user import User
from app.rules.merge import (
    get_effective_policies as _merge_get_effective_policies,
)
from app.rules.merge import (
    get_effective_rules as _merge_get_effective_rules,
)
from app.schemas.groups import GroupPoliciesUpdate
from app.schemas.rules import (
    ChainPoliciesResponse,
    EffectiveRuleResponse,
    RuleCreate,
    RuleReorder,
    RuleResponse,
    RuleUpdate,
)

router = APIRouter(tags=["rules"])


@router.get("/groups/{group_id}/rules", response_model=list[RuleResponse])
async def list_rules(
    group_id: int,
    _: User = Depends(current_active_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(FirewallRule)
        .where(FirewallRule.group_id == group_id)
        .order_by(FirewallRule.priority.desc(), FirewallRule.id)
    )
    return result.scalars().all()


async def _validate_host_refs(db: AsyncSession, body) -> None:
    ids = [i for i in (body.source_host_id, body.destination_host_id) if i is not None]
    if not ids:
        return
    rows = await db.execute(select(Host.id).where(Host.id.in_(ids)))
    found = {r[0] for r in rows.all()}
    missing = [i for i in ids if i not in found]
    if missing:
        raise HTTPException(status_code=400, detail=f"Referenced host(s) not found: {missing}")


@router.post("/groups/{group_id}/rules", response_model=RuleResponse, status_code=201)
async def create_rule(
    group_id: int,
    body: RuleCreate,
    _: User = Depends(current_superuser),
    db: AsyncSession = Depends(get_db),
):
    await check_gitops_lock(group_id, db)
    if body.protocol == "icmp" and body.port_start is not None:
        raise HTTPException(status_code=400, detail="ICMP rules cannot specify ports")
    # Validate port range
    if body.port_start and body.port_end and body.port_end < body.port_start:
        raise HTTPException(status_code=400, detail="port_end must be >= port_start")
    await _validate_host_refs(db, body)
    rule = FirewallRule(group_id=group_id, **body.model_dump())
    db.add(rule)
    await db.commit()
    await db.refresh(rule)
    return rule


@router.put("/groups/{group_id}/rules/reorder", status_code=200)
async def reorder_rules(
    group_id: int,
    body: RuleReorder,
    _: User = Depends(current_superuser),
    db: AsyncSession = Depends(get_db),
):
    await check_gitops_lock(group_id, db)
    for idx, rule_id in enumerate(reversed(body.rule_ids)):
        result = await db.execute(
            select(FirewallRule).where(
                FirewallRule.id == rule_id,
                FirewallRule.group_id == group_id,
            )
        )
        rule = result.scalar_one_or_none()
        if rule and not rule.is_system:
            rule.priority = idx
    await db.commit()
    return {"reordered": len(body.rule_ids)}


@router.put("/groups/{group_id}/rules/{rule_id}", response_model=RuleResponse)
async def update_rule(
    group_id: int,
    rule_id: int,
    body: RuleUpdate,
    _: User = Depends(current_superuser),
    db: AsyncSession = Depends(get_db),
):
    await check_gitops_lock(group_id, db)
    result = await db.execute(
        select(FirewallRule).where(
            FirewallRule.id == rule_id,
            FirewallRule.group_id == group_id,
        )
    )
    rule = result.scalar_one_or_none()
    if not rule:
        raise HTTPException(status_code=404, detail="Rule not found")
    if rule.is_system:
        raise HTTPException(status_code=403, detail="System rules cannot be modified")
    await _apply_rule_update(db, rule, body)
    await db.commit()
    await db.refresh(rule)
    return rule


async def _apply_rule_update(db: AsyncSession, rule: FirewallRule, body: RuleUpdate) -> None:
    """Apply a RuleUpdate to an ORM rule, handling cidr↔host_id side swaps."""
    data = body.model_dump(exclude_unset=True)
    # If the update touches one side of source or destination, ensure the
    # opposite half is cleared so the "xor" CHECK still holds.
    if "source_cidr" in data and data.get("source_cidr"):
        data.setdefault("source_host_id", None)
    if "source_host_id" in data and data.get("source_host_id") is not None:
        data.setdefault("source_cidr", None)
    if "destination_cidr" in data and data.get("destination_cidr"):
        data.setdefault("destination_host_id", None)
    if "destination_host_id" in data and data.get("destination_host_id") is not None:
        data.setdefault("destination_cidr", None)

    host_ids = [
        i for i in (data.get("source_host_id"), data.get("destination_host_id")) if i is not None
    ]
    if host_ids:
        rows = await db.execute(select(Host.id).where(Host.id.in_(host_ids)))
        found = {r[0] for r in rows.all()}
        missing = [i for i in host_ids if i not in found]
        if missing:
            raise HTTPException(status_code=400, detail=f"Referenced host(s) not found: {missing}")

    for field, value in data.items():
        setattr(rule, field, value)


@router.delete("/groups/{group_id}/rules/{rule_id}", status_code=204)
async def delete_rule(
    group_id: int,
    rule_id: int,
    _: User = Depends(current_superuser),
    db: AsyncSession = Depends(get_db),
):
    await check_gitops_lock(group_id, db)
    result = await db.execute(
        select(FirewallRule).where(
            FirewallRule.id == rule_id,
            FirewallRule.group_id == group_id,
        )
    )
    rule = result.scalar_one_or_none()
    if not rule:
        raise HTTPException(status_code=404, detail="Rule not found")
    if rule.is_system:
        raise HTTPException(status_code=403, detail="System rules cannot be deleted")
    await db.delete(rule)
    await db.commit()


@router.get("/hosts/{host_id}/firewall-rules", response_model=list[RuleResponse])
async def list_host_rules(
    host_id: int,
    _: User = Depends(current_active_user),
    db: AsyncSession = Depends(get_db),
):
    """List host-level firewall rule overrides."""
    result = await db.execute(
        select(FirewallRule)
        .where(FirewallRule.host_id == host_id)
        .order_by(FirewallRule.priority.desc(), FirewallRule.id)
    )
    return result.scalars().all()


@router.post("/hosts/{host_id}/firewall-rules", response_model=RuleResponse, status_code=201)
async def create_host_rule(
    host_id: int,
    body: RuleCreate,
    _: User = Depends(current_superuser),
    db: AsyncSession = Depends(get_db),
):
    """Create a host-level firewall rule override."""
    host_result = await db.execute(select(Host).where(Host.id == host_id))
    if not host_result.scalar_one_or_none():
        raise HTTPException(status_code=404, detail="Host not found")
    if body.protocol == "icmp" and body.port_start is not None:
        raise HTTPException(status_code=400, detail="ICMP rules cannot specify ports")
    if body.port_start and body.port_end and body.port_end < body.port_start:
        raise HTTPException(status_code=400, detail="port_end must be >= port_start")
    await _validate_host_refs(db, body)
    rule = FirewallRule(host_id=host_id, **body.model_dump())
    db.add(rule)
    await db.commit()
    await db.refresh(rule)
    return rule


@router.put("/hosts/{host_id}/firewall-rules/{rule_id}", response_model=RuleResponse)
async def update_host_rule(
    host_id: int,
    rule_id: int,
    body: RuleUpdate,
    _: User = Depends(current_superuser),
    db: AsyncSession = Depends(get_db),
):
    """Update a host-level firewall rule override."""
    result = await db.execute(
        select(FirewallRule).where(
            FirewallRule.id == rule_id,
            FirewallRule.host_id == host_id,
        )
    )
    rule = result.scalar_one_or_none()
    if not rule:
        raise HTTPException(status_code=404, detail="Rule not found")
    if rule.is_system:
        raise HTTPException(status_code=403, detail="System rules cannot be modified")
    await _apply_rule_update(db, rule, body)
    await db.commit()
    await db.refresh(rule)
    return rule


@router.delete("/hosts/{host_id}/firewall-rules/{rule_id}", status_code=204)
async def delete_host_rule(
    host_id: int,
    rule_id: int,
    _: User = Depends(current_superuser),
    db: AsyncSession = Depends(get_db),
):
    """Delete a host-level firewall rule override."""
    result = await db.execute(
        select(FirewallRule).where(
            FirewallRule.id == rule_id,
            FirewallRule.host_id == host_id,
        )
    )
    rule = result.scalar_one_or_none()
    if not rule:
        raise HTTPException(status_code=404, detail="Rule not found")
    if rule.is_system:
        raise HTTPException(status_code=403, detail="System rules cannot be deleted")
    await db.delete(rule)
    await db.commit()


@router.get("/hosts/{host_id}/effective-rules", response_model=list[EffectiveRuleResponse])
async def effective_rules_endpoint(
    host_id: int,
    _: User = Depends(current_active_user),
    db: AsyncSession = Depends(get_db),
):
    """Get merged ruleset for a host (all groups, priority-merged, with SSH lockout rule)."""
    return await _merge_get_effective_rules(host_id, db)


@router.get("/groups/{group_id}/policies", response_model=ChainPoliciesResponse)
async def get_group_policies(
    group_id: int,
    _: User = Depends(current_active_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(HostGroup).where(HostGroup.id == group_id))
    group = result.scalar_one_or_none()
    if not group:
        raise HTTPException(status_code=404, detail="Group not found")
    return ChainPoliciesResponse(
        input=group.input_policy or "drop",
        output=group.output_policy or "accept",
    )


@router.put("/groups/{group_id}/policies", response_model=ChainPoliciesResponse)
async def update_group_policies(
    group_id: int,
    body: GroupPoliciesUpdate,
    _: User = Depends(current_superuser),
    db: AsyncSession = Depends(get_db),
):
    await check_gitops_lock(group_id, db)
    result = await db.execute(select(HostGroup).where(HostGroup.id == group_id))
    group = result.scalar_one_or_none()
    if not group:
        raise HTTPException(status_code=404, detail="Group not found")
    group.input_policy = body.input_policy
    group.output_policy = body.output_policy
    await db.commit()
    await db.refresh(group)
    return ChainPoliciesResponse(
        input=group.input_policy or "drop",
        output=group.output_policy or "accept",
    )


@router.get("/hosts/{host_id}/effective-policies", response_model=ChainPoliciesResponse)
async def effective_policies_endpoint(
    host_id: int,
    _: User = Depends(current_active_user),
    db: AsyncSession = Depends(get_db),
):
    """Get merged chain policies for a host (all groups, priority-merged)."""
    return await _merge_get_effective_policies(host_id, db)
