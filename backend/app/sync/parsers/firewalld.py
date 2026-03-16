"""Parse firewall-cmd --list-all output into FirewallRuleSpec list."""

import re

from app.rules.model import FirewallRuleSpec

_RICH_RULE_RE = re.compile(
    r'rule\s+'
    r'(?:family="(?P<family>[^"]+)"\s+)?'
    r'(?:source\s+address="(?P<source>[^"]+)"\s+)?'
    r'(?:destination\s+address="(?P<dest>[^"]+)"\s+)?'
    r'(?:port\s+port="(?P<port>[^"]+)"\s+protocol="(?P<proto>[^"]+)"\s+)?'
    r'(?:protocol\s+value="(?P<proto_only>[^"]+)"\s+)?'
    r'(?P<action>accept|drop|reject)'
)


def _parse_port_spec(port_str: str) -> tuple[int, int | None]:
    """Parse '80' or '3306-3310' into (port_start, port_end)."""
    if "-" in port_str:
        start, end = port_str.split("-", 1)
        return int(start), int(end)
    return int(port_str), None


def _parse_ports_line(line: str) -> list[FirewallRuleSpec]:
    """Parse the 'ports:' line like '80/tcp 443/tcp 3306-3310/tcp'."""
    rules: list[FirewallRuleSpec] = []
    entries = line.strip().split()
    for entry in entries:
        if "/" not in entry:
            continue
        port_part, proto = entry.rsplit("/", 1)
        port_start, port_end = _parse_port_spec(port_part)
        rules.append(
            FirewallRuleSpec(
                action="allow",
                protocol=proto,
                direction="input",
                port_start=port_start,
                port_end=port_end,
            )
        )
    return rules


def _parse_rich_rule(line: str) -> FirewallRuleSpec | None:
    """Parse a single firewalld rich rule string."""
    m = _RICH_RULE_RE.search(line)
    if not m:
        return None

    action_str = m.group("action")
    action_map = {"accept": "allow", "drop": "deny", "reject": "reject"}
    action = action_map.get(action_str)
    if action is None:
        return None

    protocol = m.group("proto") or m.group("proto_only") or "any"
    source = m.group("source")
    dest = m.group("dest")

    port_start: int | None = None
    port_end: int | None = None
    if m.group("port"):
        port_start, port_end = _parse_port_spec(m.group("port"))

    return FirewallRuleSpec(
        action=action,
        protocol=protocol,
        direction="input",
        source_cidr=source,
        destination_cidr=dest,
        port_start=port_start,
        port_end=port_end,
    )


def parse_firewalld_output(output: str) -> list[FirewallRuleSpec]:
    """Parse ``firewall-cmd --list-all`` text output into canonical rule specs."""
    rules: list[FirewallRuleSpec] = []
    in_rich_rules = False

    for line in output.splitlines():
        stripped = line.strip()

        if stripped.startswith("ports:"):
            port_data = stripped[len("ports:"):].strip()
            if port_data:
                rules.extend(_parse_ports_line(port_data))
            in_rich_rules = False
            continue

        if stripped.startswith("rich rules:"):
            in_rich_rules = True
            rich_data = stripped[len("rich rules:"):].strip()
            if rich_data:
                parsed = _parse_rich_rule(rich_data)
                if parsed:
                    rules.append(parsed)
            continue

        if in_rich_rules and stripped.startswith("rule "):
            parsed = _parse_rich_rule(stripped)
            if parsed:
                rules.append(parsed)
            continue

        if ":" in stripped and not stripped.startswith("rule "):
            in_rich_rules = False

    return rules
