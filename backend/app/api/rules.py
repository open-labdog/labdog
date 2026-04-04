from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.db import get_db
from app.models.firewall_rule import FirewallRule
from app.models.host import Host, HostGroupMembership
from app.models.host_group import HostGroup
from app.models.user import User
from app.auth.users import current_active_user, current_superuser
from app.schemas.rules import RuleCreate, RuleUpdate, RuleResponse, RuleReorder, EffectiveRuleResponse, ChainPoliciesResponse
from app.schemas.groups import GroupPoliciesUpdate
from app.rules.model import FirewallRuleSpec
from app.rules.merge import merge_group_rules, merge_group_policies
from app.rules.converter import firewall_rules_to_specs

router = APIRouter(tags=["rules"])


async def _check_gitops_lock(group_id: int, db: AsyncSession):
    """Block rule mutations on GitOps-managed groups."""
    result = await db.execute(select(HostGroup).where(HostGroup.id == group_id))
    group = result.scalar_one_or_none()
    if group and group.gitops_enabled:
        raise HTTPException(
            status_code=403,
            detail="This group is managed by GitOps. Rule changes must be made via Git.",
        )


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


@router.post("/groups/{group_id}/rules", response_model=RuleResponse, status_code=201)
async def create_rule(
    group_id: int,
    body: RuleCreate,
    _: User = Depends(current_superuser),
    db: AsyncSession = Depends(get_db),
):
    await _check_gitops_lock(group_id, db)
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
    _: User = Depends(current_superuser),
    db: AsyncSession = Depends(get_db),
):
    await _check_gitops_lock(group_id, db)
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
    await _check_gitops_lock(group_id, db)
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
    _: User = Depends(current_superuser),
    db: AsyncSession = Depends(get_db),
):
    await _check_gitops_lock(group_id, db)
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
    for field, value in body.model_dump(exclude_none=True).items():
        setattr(rule, field, value)
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
async def get_effective_rules(
    host_id: int,
    _: User = Depends(current_active_user),
    db: AsyncSession = Depends(get_db),
):
    """Get merged ruleset for a host (all groups, priority-merged, with SSH lockout rule)."""
    # Load host to get per-host source IP
    host_result = await db.execute(select(Host).where(Host.id == host_id))
    host = host_result.scalar_one_or_none()
    host_source_ip = host.barricade_source_ip if host else None

    # Get all groups for this host
    memberships_all = await db.execute(
        select(HostGroupMembership.c.group_id).where(HostGroupMembership.c.host_id == host_id)
    )
    group_ids = [r[0] for r in memberships_all.all()]

    if not group_ids:
        # Still need to check for host-level rules
        host_rules_result = await db.execute(
            select(FirewallRule).where(FirewallRule.host_id == host_id)
        )
        host_rule_specs = firewall_rules_to_specs(host_rules_result.scalars().all())
        merged_specs = merge_group_rules([], host_source_ip=host_source_ip, host_rules=host_rule_specs)
        return [EffectiveRuleResponse(
            action=r.action,
            protocol=r.protocol,
            direction=r.direction,
            source_cidr=r.source_cidr,
            destination_cidr=r.destination_cidr,
            port_start=r.port_start,
            port_end=r.port_end,
            comment=r.comment,
            priority=r.priority,
            is_system=r.is_system,
            group_id=r.group_id,
            group_name=None,
            rule_id=r.rule_id,
            group_priority=r.group_priority,
            source="system" if r.is_system else ("host" if r.host_id else "group"),
            source_id=r.host_id if r.host_id else r.group_id,
            source_name="System" if r.is_system else ("Host override" if r.host_id else ""),
        ) for r in merged_specs]

    # Build groups_data with FirewallRuleSpec objects (same pattern as sync.py)
    groups_data = []
    group_names: dict[int, str] = {}
    for gid in group_ids:
        group_result = await db.execute(select(HostGroup).where(HostGroup.id == gid))
        group = group_result.scalar_one()
        group_names[gid] = group.name
        rules_result = await db.execute(select(FirewallRule).where(FirewallRule.group_id == gid))
        rules = firewall_rules_to_specs(rules_result.scalars().all())
        groups_data.append({
            "id": gid, "priority": group.priority, "rules": rules,
            "input_policy": group.input_policy, "output_policy": group.output_policy,
        })

    # Fetch host-level rule overrides
    host_rules_result = await db.execute(
        select(FirewallRule).where(FirewallRule.host_id == host_id)
    )
    host_rule_specs = firewall_rules_to_specs(host_rules_result.scalars().all())

    # Call merge_group_rules to get merged rules WITH SSH lockout rule
    merged_specs = merge_group_rules(groups_data, host_source_ip=host_source_ip, host_rules=host_rule_specs)

    # Convert FirewallRuleSpec objects to EffectiveRuleResponse
    return [
        EffectiveRuleResponse(
            action=spec.action,
            protocol=spec.protocol,
            direction=spec.direction,
            source_cidr=spec.source_cidr,
            destination_cidr=spec.destination_cidr,
            port_start=spec.port_start,
            port_end=spec.port_end,
            comment=spec.comment,
            priority=spec.priority,
            is_system=spec.is_system,
            group_id=spec.group_id,
            group_name=group_names.get(spec.group_id) if spec.group_id else None,
            rule_id=spec.rule_id,
            group_priority=spec.group_priority,
            source="system" if spec.is_system else ("host" if spec.host_id else "group"),
            source_id=spec.host_id if spec.host_id else spec.group_id,
            source_name="System" if spec.is_system else ("Host override" if spec.host_id else group_names.get(spec.group_id, "")),
        )
        for spec in merged_specs
    ]


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
    await _check_gitops_lock(group_id, db)
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
async def get_effective_policies(
    host_id: int,
    _: User = Depends(current_active_user),
    db: AsyncSession = Depends(get_db),
):
    """Get merged chain policies for a host (all groups, priority-merged)."""
    memberships = await db.execute(
        select(HostGroupMembership.c.group_id).where(HostGroupMembership.c.host_id == host_id)
    )
    group_ids = [r[0] for r in memberships.all()]
    if not group_ids:
        return ChainPoliciesResponse(input="drop", output="accept")

    groups_data = []
    for gid in group_ids:
        group_result = await db.execute(select(HostGroup).where(HostGroup.id == gid))
        group = group_result.scalar_one()
        groups_data.append({
            "id": gid, "name": group.name, "priority": group.priority, "rules": [],
            "input_policy": group.input_policy, "output_policy": group.output_policy,
        })

    policies = merge_group_policies(groups_data)
    return ChainPoliciesResponse(
        input=policies.input,
        output=policies.output,
        input_source_group_id=policies.input_source_group_id,
        input_source_group_name=policies.input_source_group_name,
        output_source_group_id=policies.output_source_group_id,
        output_source_group_name=policies.output_source_group_name,
    )
