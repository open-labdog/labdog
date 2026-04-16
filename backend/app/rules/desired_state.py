"""Shared utility to compute the desired firewall state for a host.

Replaces the N+1 query pattern (1 + 2N queries per host) with 3 fixed
queries regardless of group count.
"""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.firewall_rule import FirewallRule
from app.models.host import Host, HostGroupMembership
from app.models.host_group import HostGroup
from app.rules.converter import firewall_rules_to_specs
from app.rules.merge import merge_group_policies, merge_group_rules
from app.rules.model import ChainPolicies, FirewallRuleSpec
from app.rules.resolver import collect_referenced_host_ids, resolve_host_refs


async def load_host_ip_lookup(
    db: AsyncSession, host_ids: set[int]
) -> dict[int, str | None]:
    """Fetch {host_id: ip_address} for the given host IDs."""
    if not host_ids:
        return {}
    rows = await db.execute(
        select(Host.id, Host.ip_address).where(Host.id.in_(host_ids))
    )
    return {row.id: row.ip_address for row in rows}


async def resolve_specs(
    db: AsyncSession, specs: list[FirewallRuleSpec]
) -> list[FirewallRuleSpec]:
    """Replace source/destination host refs on specs with concrete CIDRs.

    Fetches the current IP for every referenced host. Raises if a referenced
    host is missing or has no IP (see resolver.HostRefResolutionError).
    """
    ids = collect_referenced_host_ids(specs)
    lookup = await load_host_ip_lookup(db, ids)
    return resolve_host_refs(specs, lookup)


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
