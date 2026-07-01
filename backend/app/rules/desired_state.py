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


async def load_host_ip_lookup(db: AsyncSession, host_ids: set[int]) -> dict[int, str | None]:
    """Fetch {host_id: ip_address} for the given host IDs."""
    if not host_ids:
        return {}
    rows = await db.execute(select(Host.id, Host.ip_address).where(Host.id.in_(host_ids)))
    return {row.id: row.ip_address for row in rows}


async def resolve_specs(
    db: AsyncSession,
    specs: list[FirewallRuleSpec],
    *,
    strict: bool = True,
) -> list[FirewallRuleSpec]:
    """Replace source/destination host refs on specs with concrete CIDRs.

    Fetches the current IP for every referenced host. When `strict` (default),
    raises if a referenced host is missing or has no IP (see
    resolver.HostRefResolutionError); when not `strict`, leaves such refs
    unresolved for read-only callers.
    """
    ids = collect_referenced_host_ids(specs)
    lookup = await load_host_ip_lookup(db, ids)
    return resolve_host_refs(specs, lookup, strict=strict)


async def get_desired_state(
    host_id: int,
    db: AsyncSession,
    host_source_ip: str | None = None,
    *,
    resolve_strict: bool = True,
) -> tuple[list[FirewallRuleSpec], ChainPolicies]:
    """Get merged desired rules and policies for a host from DB.

    Host references (``source_host_id`` / ``destination_host_id``) are
    materialized into concrete /32 (or /128) CIDRs via ``resolve_specs`` so
    every consumer — rendering, diffing and the effective-rules display — sees
    a real source/destination address instead of an unresolved ref that would
    otherwise render as (and diff against) "any".

    With ``resolve_strict`` (the default) a referenced host with no IP raises
    ``HostRefResolutionError`` — fail closed for rendering/diffing. Read-only
    callers (the effective-rules display) pass ``resolve_strict=False`` so a
    single dangling ref degrades to a name-only rule instead of 500-ing the
    whole request.

    Returns (merged_rules, merged_policies).
    """
    memberships = await db.execute(
        select(HostGroupMembership.c.group_id).where(HostGroupMembership.c.host_id == host_id)
    )
    group_ids = [r[0] for r in memberships.all()]

    # Fetch host-level rule overrides (needed in all branches)
    host_rules_result = await db.execute(
        select(FirewallRule).where(FirewallRule.host_id == host_id)
    )
    host_rule_specs = firewall_rules_to_specs(host_rules_result.scalars().all())

    if not group_ids:
        # Always run merge_group_rules even when there are no groups
        # and no host rules — it injects the SSH lockout rule which
        # must never be skipped (security-critical anti-lockout).
        merged = merge_group_rules(
            [], host_source_ip=host_source_ip, host_rules=host_rule_specs
        )
        return (await resolve_specs(db, merged, strict=resolve_strict), ChainPolicies())

    # 1 query for all groups (replaces N individual SELECTs)
    groups_result = await db.execute(select(HostGroup).where(HostGroup.id.in_(group_ids)))
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

    merged = merge_group_rules(
        groups_data,
        host_source_ip=host_source_ip,
        host_rules=host_rule_specs,
    )
    return (
        await resolve_specs(db, merged, strict=resolve_strict),
        merge_group_policies(groups_data),
    )
