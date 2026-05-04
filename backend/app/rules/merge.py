from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.host import Host, HostGroupMembership
from app.models.host_group import HostGroup
from app.rules.model import ChainPolicies, FirewallRuleSpec
from app.schemas.rules import ChainPoliciesResponse, EffectiveRuleResponse


def _make_ssh_lockout_rule(server_ip: str) -> FirewallRuleSpec:
    """Auto-injected SSH allow rule — always first, non-deletable."""
    return FirewallRuleSpec(
        action="allow",
        protocol="tcp",
        direction="input",
        source_cidr=f"{server_ip}/32",
        port_start=22,
        comment="anti-lockout SSH allow — auto-injected, do not remove",
        is_system=True,
        priority=999999,  # always highest priority
    )


def merge_group_rules(
    groups: list[dict],  # [{"id": int, "priority": int, "rules": list[FirewallRuleSpec]}]
    server_ip: str | None = None,
    host_source_ip: str | None = None,
    host_rules: list[FirewallRuleSpec] | None = None,
) -> list[FirewallRuleSpec]:
    """
    Merge rules from multiple groups using priority-based conflict resolution.
    Higher group priority wins on conflict (same port+protocol+direction but different action).
    Always prepends the SSH lockout prevention rule.

    Args:
        groups: List of dicts with id, priority, and rules list
        server_ip: LabDog server IP for SSH lockout rule (defaults to settings)
        host_source_ip: Per-host detected source IP (takes precedence over server_ip)
        host_rules: Host-level override rules; replace any group rule with the same signature

    Returns:
        Ordered list of FirewallRuleSpec (SSH lockout first, then merged rules)
    """
    if host_source_ip:
        server_ip = host_source_ip
    elif server_ip is None:
        server_ip = settings.security.labdog_server_ip

    # Sort groups by priority descending (highest priority first)
    sorted_groups = sorted(groups, key=lambda g: g["priority"], reverse=True)

    merged: list[FirewallRuleSpec] = []
    seen_signatures: set[tuple] = set()

    for group in sorted_groups:
        for rule in group["rules"]:
            # Signature: (protocol, direction, port_start, port_end, source_cidr, dest_cidr)
            sig = (
                rule.protocol,
                rule.direction,
                rule.port_start,
                rule.port_end,
                rule.source_cidr,
                rule.destination_cidr,
                rule.source_host_id,
                rule.destination_host_id,
            )
            if sig not in seen_signatures:
                seen_signatures.add(sig)
                rule.group_priority = group["priority"]
                merged.append(rule)
            # If sig already seen: higher-priority group's rule wins (already in merged)

    # Apply host-level overrides — host rules replace group rules with same signature
    if host_rules:
        for rule in host_rules:
            sig = (
                rule.protocol,
                rule.direction,
                rule.port_start,
                rule.port_end,
                rule.source_cidr,
                rule.destination_cidr,
                rule.source_host_id,
                rule.destination_host_id,
            )
            if sig in seen_signatures:
                # Replace existing group rule with host override
                merged = [
                    r
                    for r in merged
                    if (
                        r.protocol,
                        r.direction,
                        r.port_start,
                        r.port_end,
                        r.source_cidr,
                        r.destination_cidr,
                        r.source_host_id,
                        r.destination_host_id,
                    )
                    != sig
                ]
            seen_signatures.add(sig)
            merged.append(rule)

    # Sort by group priority first (higher = first), then rule priority within group
    merged.sort(key=lambda r: (r.group_priority or 0, r.priority), reverse=True)

    # Prepend SSH lockout rule (always first)
    ssh_rule = _make_ssh_lockout_rule(server_ip)
    return [ssh_rule] + merged


def merge_group_policies(groups: list[dict]) -> ChainPolicies:
    """Merge chain default policies from multiple groups using priority.

    For each chain, the highest-priority group that defines a non-None
    value wins.  If no group defines a policy, system defaults apply
    (input=drop, output=accept).
    """
    sorted_groups = sorted(groups, key=lambda g: g["priority"], reverse=True)
    input_policy: str | None = None
    output_policy: str | None = None
    input_source: tuple[int | None, str | None] = (None, None)
    output_source: tuple[int | None, str | None] = (None, None)

    for group in sorted_groups:
        if input_policy is None and group.get("input_policy"):
            input_policy = group["input_policy"]
            input_source = (group["id"], group.get("name"))
        if output_policy is None and group.get("output_policy"):
            output_policy = group["output_policy"]
            output_source = (group["id"], group.get("name"))
        if input_policy and output_policy:
            break

    return ChainPolicies(
        input=input_policy or "drop",
        output=output_policy or "accept",
        input_source_group_id=input_source[0],
        input_source_group_name=input_source[1],
        output_source_group_id=output_source[0],
        output_source_group_name=output_source[1],
    )


async def get_effective_rules(
    host_id: int,
    db: AsyncSession,
) -> list[EffectiveRuleResponse]:
    """Merged effective firewall ruleset for a host, in API response shape.

    Wraps ``get_desired_state`` (raw ``FirewallRuleSpec`` output) and
    decorates each rule with display metadata: group/host names and
    a ``source`` attribution (``"system"`` / ``"host"`` / ``"group"``).

    For raw specs (sync task / orchestrator), call
    ``app.rules.desired_state.get_desired_state`` directly.
    """
    # Deferred import — desired_state imports from merge, so a top-level
    # import here would create a circular module load.
    from app.rules.desired_state import get_desired_state  # noqa: PLC0415

    host_result = await db.execute(select(Host).where(Host.id == host_id))
    host = host_result.scalar_one_or_none()
    host_source_ip = host.labdog_source_ip if host else None

    merged_specs, _policies = await get_desired_state(host_id, db, host_source_ip=host_source_ip)

    group_ids = {s.group_id for s in merged_specs if s.group_id}
    if group_ids:
        group_rows = await db.execute(
            select(HostGroup.id, HostGroup.name).where(HostGroup.id.in_(group_ids))
        )
        group_names = {r.id: r.name for r in group_rows}
    else:
        group_names = {}

    host_ref_ids = {
        i for s in merged_specs for i in (s.source_host_id, s.destination_host_id) if i is not None
    }
    if host_ref_ids:
        host_rows = await db.execute(
            select(Host.id, Host.hostname).where(Host.id.in_(host_ref_ids))
        )
        host_names = {r.id: r.hostname for r in host_rows}
    else:
        host_names = {}

    return [
        EffectiveRuleResponse(
            action=spec.action,
            protocol=spec.protocol,
            direction=spec.direction,
            source_cidr=spec.source_cidr,
            destination_cidr=spec.destination_cidr,
            source_host_id=spec.source_host_id,
            destination_host_id=spec.destination_host_id,
            source_host_name=host_names.get(spec.source_host_id) if spec.source_host_id else None,
            destination_host_name=host_names.get(spec.destination_host_id)
            if spec.destination_host_id
            else None,
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
            source_name="System"
            if spec.is_system
            else ("Host override" if spec.host_id else group_names.get(spec.group_id, "")),
        )
        for spec in merged_specs
    ]


async def get_effective_policies(
    host_id: int,
    db: AsyncSession,
) -> ChainPoliciesResponse:
    """Merged effective chain policies for a host, in API response shape."""
    memberships = await db.execute(
        select(HostGroupMembership.c.group_id).where(HostGroupMembership.c.host_id == host_id)
    )
    group_ids = [r[0] for r in memberships.all()]
    if not group_ids:
        return ChainPoliciesResponse(input="drop", output="accept")

    groups_result = await db.execute(select(HostGroup).where(HostGroup.id.in_(group_ids)))
    groups_data = [
        {
            "id": g.id,
            "name": g.name,
            "priority": g.priority,
            "rules": [],
            "input_policy": g.input_policy,
            "output_policy": g.output_policy,
        }
        for g in groups_result.scalars().all()
    ]

    policies = merge_group_policies(groups_data)
    return ChainPoliciesResponse(
        input=policies.input,
        output=policies.output,
        input_source_group_id=policies.input_source_group_id,
        input_source_group_name=policies.input_source_group_name,
        output_source_group_id=policies.output_source_group_id,
        output_source_group_name=policies.output_source_group_name,
    )
