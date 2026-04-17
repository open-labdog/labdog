import ipaddress

from app.rules.model import ChainPolicies, FirewallRuleSpec


def _cidr_family(cidr: str) -> str:
    """Returns 'ip' for IPv4, 'ip6' for IPv6."""
    try:
        net = ipaddress.ip_network(cidr, strict=False)
        return "ip6" if net.version == 6 else "ip"
    except ValueError:
        return "ip"


def _rule_to_nft(rule: FirewallRuleSpec) -> str:
    parts = []
    if rule.source_cidr:
        family = _cidr_family(rule.source_cidr)
        parts.append(f"{family} saddr {rule.source_cidr}")
    if rule.destination_cidr:
        family = _cidr_family(rule.destination_cidr)
        parts.append(f"{family} daddr {rule.destination_cidr}")
    if rule.protocol not in (None, "any"):
        parts.append(rule.protocol)
        if rule.port_start is not None:
            if rule.port_end and rule.port_end != rule.port_start:
                parts.append(f"dport {rule.port_start}-{rule.port_end}")
            else:
                parts.append(f"dport {rule.port_start}")
    action_map = {"allow": "accept", "deny": "drop", "reject": "reject"}
    action = action_map.get(rule.action, "drop")
    parts.append(action)
    if rule.comment:
        comment_text = f"Barricade: {rule.comment}"
    else:
        comment_text = "Managed by Barricade"
    parts.append(f'comment "{comment_text}"')
    return " ".join(parts)


def render_nftables_config(
    rules: list[FirewallRuleSpec],
    policies: ChainPolicies | None = None,
) -> str:
    if policies is None:
        policies = ChainPolicies()

    input_rules = [r for r in rules if r.direction == "input"]
    output_rules = [r for r in rules if r.direction == "output"]

    lines = [
        "#!/usr/sbin/nft -f",
        "table inet filter {}",  # ensure table exists (no-op if already exists)
        "delete table inet filter",
        "table inet filter {",
        "  chain input {",
        f"    type filter hook input priority 0; policy {policies.input};",
        "    ct state established,related accept",
        "    iif lo accept",
    ]
    for rule in input_rules:
        lines.append(f"    {_rule_to_nft(rule)}")
    lines += [
        "  }",
        "",
        "  chain output {",
        f"    type filter hook output priority 0; policy {policies.output};",
    ]
    for rule in output_rules:
        lines.append(f"    {_rule_to_nft(rule)}")
    lines += [
        "  }",
        "}",
    ]
    return "\n".join(lines) + "\n"
