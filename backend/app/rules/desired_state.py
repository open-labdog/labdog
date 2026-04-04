"""Shared utility to compute the desired firewall state for a host.

Replaces the N+1 query pattern (1 + 2N queries per host) with 3 fixed
queries regardless of group count.
"""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.firewall_rule import FirewallRule
from app.models.host import HostGroupMembership
from app.models.host_group import HostGroup
from app.rules.converter import firewall_rules_to_specs
from app.rules.merge import merge_group_policies, merge_group_rules
from app.rules.model import ChainPolicies, FirewallRuleSpec


async def get_desired_state(
    host_id: int,
    db: AsyncSession,
    host_source_ip: str | None = None,
) -> tuple[list[FirewallRuleSpec], ChainPolicies]:
    """Get merged desired rules and policies for a host from DB.

    Returns (merged_rules, merged_policies).
    """
    memberships = await db.execute(
        select(HostGroupMembership.c.group_id).where(
            HostGroupMembership.c.host_id == host_id
        )
    )
    group_ids = [r[0] for r in memberships.all()]

    # Fetch host-level rule overrides (needed in all branches)
    host_rules_result = await db.execute(
        select(FirewallRule).where(FirewallRule.host_id == host_id)
    )
    host_rule_specs = firewall_rules_to_specs(host_rules_result.scalars().all())

    if not group_ids:
        if not host_rule_specs:
            return [], ChainPolicies()
        return (
            merge_group_rules(
                [], host_source_ip=host_source_ip, host_rules=host_rule_specs
            ),
            ChainPolicies(),
        )

    # 1 query for all groups (replaces N individual SELECTs)
    groups_result = await db.execute(
        select(HostGroup).where(HostGroup.id.in_(group_ids))
    )
    groups_by_id = {g.id: g for g in groups_result.scalars().all()}

    # 1 query for all rules across all groups (replaces N individual SELECTs)
    rules_result = await db.execute(
        select(FirewallRule).where(FirewallRule.group_id.in_(group_ids))
    )
    rules_by_group: dict[int, list] = {}
    for rule in rules_result.scalars().all():
        rules_by_group.setdefault(rule.group_id, []).append(rule)

    groups_data = []
    for gid in group_ids:
        group = groups_by_id[gid]
        rules = firewall_rules_to_specs(rules_by_group.get(gid, []))
        groups_data.append(
            {
                "id": gid,
                "name": group.name,
                "priority": group.priority,
                "rules": rules,
                "input_policy": group.input_policy,
                "output_policy": group.output_policy,
            }
        )

    return (
        merge_group_rules(
            groups_data,
            host_source_ip=host_source_ip,
            host_rules=host_rule_specs,
        ),
        merge_group_policies(groups_data),
    )
