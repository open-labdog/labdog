import ipaddress

from app.rules.model import ChainPolicies, FirewallRuleSpec


def _cidr_family(cidr: str) -> str:
    """Returns 'ip' for IPv4, 'ip6' for IPv6."""
    try:
        net = ipaddress.ip_network(cidr, strict=False)
        return "ip6" if net.version == 6 else "ip"
    except ValueError:
        return "ip"


_COMMENT_PREFIX = "Managed by LabDog"


def _safe_comment(text: str | None) -> str:
    """Build the unified comment string, sanitised for nft/iptables embedding."""
    base = _COMMENT_PREFIX
    if text:
        cleaned = text.replace('"', "'").replace("\n", " ").replace("\t", " ").strip()
        if cleaned:
            base = f"{_COMMENT_PREFIX}: {cleaned}"
    return base[:200]


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
    parts.append(f'comment "{_safe_comment(rule.comment)}"')
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
        f'    ct state established,related accept comment "{_COMMENT_PREFIX}: stateful tracking"',
        f'    iif lo accept comment "{_COMMENT_PREFIX}: loopback"',
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
