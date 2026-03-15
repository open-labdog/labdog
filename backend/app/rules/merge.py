from app.rules.model import FirewallRuleSpec
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
) -> list[FirewallRuleSpec]:
    """
    Merge rules from multiple groups using priority-based conflict resolution.
    Higher group priority wins on conflict (same port+protocol+direction but different action).
    Always prepends the SSH lockout prevention rule.

    Args:
        groups: List of dicts with id, priority, and rules list
        server_ip: Barricade server IP for SSH lockout rule (defaults to settings)

    Returns:
        Ordered list of FirewallRuleSpec (SSH lockout first, then merged rules)
    """
    if server_ip is None:
        server_ip = settings.BARRICADE_SERVER_IP

    # Sort groups by priority descending (highest priority first)
    sorted_groups = sorted(groups, key=lambda g: g["priority"], reverse=True)

    merged: list[FirewallRuleSpec] = []
    seen_signatures: set[tuple] = set()

    for group in sorted_groups:
        for rule in group["rules"]:
            # Signature: (protocol, direction, port_start, port_end, source_cidr, dest_cidr)
            sig = (rule.protocol, rule.direction, rule.port_start, rule.port_end,
                   rule.source_cidr, rule.destination_cidr)
            if sig not in seen_signatures:
                seen_signatures.add(sig)
                merged.append(rule)
            # If sig already seen: higher-priority group's rule wins (already in merged)

    # Sort merged rules by priority within each group
    merged.sort(key=lambda r: r.priority, reverse=True)

    # Prepend SSH lockout rule (always first)
    ssh_rule = _make_ssh_lockout_rule(server_ip)
    return [ssh_rule] + merged
