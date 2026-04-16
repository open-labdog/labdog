from app.rules.model import ChainPolicies, FirewallRuleSpec
from app.config import settings


def _make_ssh_lockout_rule(server_ip: str) -> FirewallRuleSpec:
    """Auto-injected SSH allow rule — always first, non-deletable."""
    return FirewallRuleSpec(
        action="allow",
        protocol="tcp",
        direction="input",
        source_cidr=f"{server_ip}/32",
        port_start=22,
        comment="Barricade server SSH access — auto-injected, do not remove",
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
        server_ip: Barricade server IP for SSH lockout rule (defaults to settings)
        host_source_ip: Per-host detected source IP (takes precedence over server_ip)
        host_rules: Host-level override rules; replace any group rule with the same signature

    Returns:
        Ordered list of FirewallRuleSpec (SSH lockout first, then merged rules)
    """
    if host_source_ip:
        server_ip = host_source_ip
    elif server_ip is None:
        server_ip = settings.security.barricade_server_ip

    # Sort groups by priority descending (highest priority first)
    sorted_groups = sorted(groups, key=lambda g: g["priority"], reverse=True)

    merged: list[FirewallRuleSpec] = []
    seen_signatures: set[tuple] = set()

    for group in sorted_groups:
        for rule in group["rules"]:
            # Signature: (protocol, direction, port_start, port_end, source_cidr, dest_cidr)
            sig = (rule.protocol, rule.direction, rule.port_start, rule.port_end,
                   rule.source_cidr, rule.destination_cidr,
                   rule.source_host_id, rule.destination_host_id)
            if sig not in seen_signatures:
                seen_signatures.add(sig)
                rule.group_priority = group["priority"]
                merged.append(rule)
            # If sig already seen: higher-priority group's rule wins (already in merged)

    # Apply host-level overrides — host rules replace group rules with same signature
    if host_rules:
        for rule in host_rules:
            sig = (rule.protocol, rule.direction, rule.port_start, rule.port_end,
                   rule.source_cidr, rule.destination_cidr,
                   rule.source_host_id, rule.destination_host_id)
            if sig in seen_signatures:
                # Replace existing group rule with host override
                merged = [r for r in merged if (r.protocol, r.direction, r.port_start, r.port_end,
                          r.source_cidr, r.destination_cidr,
                          r.source_host_id, r.destination_host_id) != sig]
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
