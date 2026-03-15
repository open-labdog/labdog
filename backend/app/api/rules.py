from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.db import get_db
from app.models.firewall_rule import FirewallRule
from app.models.host import HostGroupMembership
from app.models.host_group import HostGroup
from app.models.user import User
from app.auth.users import current_active_user
from app.auth.rbac import require_group_role, get_user_accessible_group_ids
from app.models.user_group_permission import GroupRole
from app.schemas.rules import RuleCreate, RuleUpdate, RuleResponse, RuleReorder

router = APIRouter(tags=["rules"])


@router.get("/groups/{group_id}/rules", response_model=list[RuleResponse])
async def list_rules(
    group_id: int,
    _: None = Depends(require_group_role(GroupRole.viewer)),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(FirewallRule)
        .where(FirewallRule.group_id == group_id)
        .order_by(FirewallRule.priority.desc(), FirewallRule.id)
    )
    return result.scalars().all()


@router.post("/groups/{group_id}/rules", response_model=RuleResponse, status_code=201)
async def create_rule(
    group_id: int,
    body: RuleCreate,
    _: None = Depends(require_group_role(GroupRole.editor)),
    db: AsyncSession = Depends(get_db),
):
    # Validate ICMP + port
    if body.protocol == "icmp" and body.port_start is not None:
        raise HTTPException(status_code=400, detail="ICMP rules cannot specify ports")
    # Validate port range
    if body.port_start and body.port_end and body.port_end < body.port_start:
        raise HTTPException(status_code=400, detail="port_end must be >= port_start")
    rule = FirewallRule(group_id=group_id, **body.model_dump())
    db.add(rule)
    await db.commit()
    await db.refresh(rule)
    return rule


@router.put("/groups/{group_id}/rules/reorder", status_code=200)
async def reorder_rules(
    group_id: int,
    body: RuleReorder,
    _: None = Depends(require_group_role(GroupRole.editor)),
    db: AsyncSession = Depends(get_db),
):
    """Batch update rule priorities. rule_ids[0] gets highest priority."""
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
    _: None = Depends(require_group_role(GroupRole.editor)),
    db: AsyncSession = Depends(get_db),
):
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
    for field, value in body.model_dump(exclude_none=True).items():
        setattr(rule, field, value)
    await db.commit()
    await db.refresh(rule)
    return rule


@router.delete("/groups/{group_id}/rules/{rule_id}", status_code=204)
async def delete_rule(
    group_id: int,
    rule_id: int,
    _: None = Depends(require_group_role(GroupRole.editor)),
    db: AsyncSession = Depends(get_db),
):
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


@router.get("/hosts/{host_id}/effective-rules", response_model=list[RuleResponse])
async def get_effective_rules(
    host_id: int,
    user: User = Depends(current_active_user),
    db: AsyncSession = Depends(get_db),
):
    """Get merged ruleset for a host (all groups, priority-merged)."""
    # Check host access — superuser bypasses, others need at least one matching group
    accessible = await get_user_accessible_group_ids(user, db)
    if accessible is not None:
        memberships_check = await db.execute(
            select(HostGroupMembership.c.group_id).where(
                HostGroupMembership.c.host_id == host_id,
                HostGroupMembership.c.group_id.in_(accessible),
            )
        )
        if not memberships_check.first():
            raise HTTPException(status_code=403, detail="Not authorized")

    # Get all groups for this host
    memberships_all = await db.execute(
        select(HostGroupMembership.c.group_id).where(HostGroupMembership.c.host_id == host_id)
    )
    group_ids = [r[0] for r in memberships_all.all()]

    if not group_ids:
        return []

    # Get all rules from all groups, ordered by group priority then rule priority
    rules_result = await db.execute(
        select(FirewallRule, HostGroup.priority.label("group_priority"))
        .join(HostGroup, FirewallRule.group_id == HostGroup.id)
        .where(FirewallRule.group_id.in_(group_ids))
        .order_by(HostGroup.priority.desc(), FirewallRule.priority.desc())
    )

    # Deduplicate by signature (higher group priority wins — first seen wins)
    seen: set[tuple] = set()
    merged = []
    for rule, _ in rules_result.all():
        sig = (
            rule.protocol,
            rule.direction,
            rule.port_start,
            rule.port_end,
            rule.source_cidr,
            rule.destination_cidr,
        )
        if sig not in seen:
            seen.add(sig)
            merged.append(rule)

    return merged
