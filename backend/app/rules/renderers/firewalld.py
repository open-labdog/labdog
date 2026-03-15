from app.rules.model import FirewallRuleSpec


def _rule_to_firewalld_task(rule: FirewallRuleSpec) -> dict:
    """Convert a FirewallRuleSpec to ansible.posix.firewalld task parameters."""
    state = "enabled" if rule.action == "allow" else "disabled"

    # Simple port rule (no source/dest CIDR)
    if rule.source_cidr is None and rule.destination_cidr is None and rule.port_start is not None:
        port_str = str(rule.port_start)
        if rule.port_end and rule.port_end != rule.port_start:
            port_str = f"{rule.port_start}-{rule.port_end}"
        return {
            "port": f"{port_str}/{rule.protocol}",
            "zone": "public",
            "state": state,
            "permanent": True,
            "immediate": True,
        }

    # Rich rule (with source/dest CIDR or reject action)
    rich_parts = ['rule family="ipv4"']
    if rule.source_cidr:
        rich_parts.append(f'source address="{rule.source_cidr}"')
    if rule.destination_cidr:
        rich_parts.append(f'destination address="{rule.destination_cidr}"')
    if rule.port_start is not None and rule.protocol not in (None, "any"):
        port_str = str(rule.port_start)
        if rule.port_end and rule.port_end != rule.port_start:
            port_str = f"{rule.port_start}-{rule.port_end}"
        rich_parts.append(f'port port="{port_str}" protocol="{rule.protocol}"')
    action_map = {"allow": "accept", "deny": "drop", "reject": "reject"}
    rich_parts.append(action_map.get(rule.action, "drop"))

    return {
        "rich_rule": " ".join(rich_parts),
        "zone": "public",
        "state": "enabled",
        "permanent": True,
        "immediate": True,
    }


def render_firewalld_tasks(rules: list[FirewallRuleSpec]) -> list[dict]:
    """Returns list of ansible.posix.firewalld task parameter dicts."""
    return [_rule_to_firewalld_task(r) for r in rules]
