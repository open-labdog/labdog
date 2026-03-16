"""Parse /etc/ufw/user.rules (iptables-save format) into FirewallRuleSpec list."""

import re

from app.rules.model import FirewallRuleSpec

_CHAIN_DIRECTION = {
    "ufw-user-input": "input",
    "ufw-user-output": "output",
}

_ACTION_MAP = {"ACCEPT": "allow", "DROP": "deny", "REJECT": "reject"}

_RULE_RE = re.compile(r"^-A\s+(?P<chain>\S+)\s+(?P<flags>.+)$")


def _parse_rule_flags(flags_str: str) -> dict[str, str]:
    """Parse iptables flag string into key-value pairs.

    Handles: -p tcp --dport 80 -s 10.0.0.0/8 -d 192.168.0.0/16 -j ACCEPT
    """
    result: dict[str, str] = {}
    tokens = flags_str.split()
    i = 0
    while i < len(tokens):
        token = tokens[i]
        if token in ("-p", "-s", "-d", "-j", "--dport", "--sport") and i + 1 < len(tokens):
            result[token] = tokens[i + 1]
            i += 2
        else:
            i += 1
    return result


def _parse_port_spec(port_str: str) -> tuple[int, int | None]:
    """Parse '80' or '3306:3310' (iptables colon range) into (port_start, port_end)."""
    if ":" in port_str:
        start, end = port_str.split(":", 1)
        return int(start), int(end)
    return int(port_str), None


def parse_ufw_rules(content: str) -> list[FirewallRuleSpec]:
    """Parse ``/etc/ufw/user.rules`` iptables-save format into canonical rule specs."""
    rules: list[FirewallRuleSpec] = []

    for line in content.splitlines():
        line = line.strip()
        if not line.startswith("-A "):
            continue

        m = _RULE_RE.match(line)
        if not m:
            continue

        chain = m.group("chain")
        direction = _CHAIN_DIRECTION.get(chain)
        if direction is None:
            continue

        flags = _parse_rule_flags(m.group("flags"))

        action_str = flags.get("-j")
        if action_str is None:
            continue
        action = _ACTION_MAP.get(action_str)
        if action is None:
            continue

        protocol = flags.get("-p", "any")
        source_cidr = flags.get("-s")
        dest_cidr = flags.get("-d")

        port_start: int | None = None
        port_end: int | None = None
        dport = flags.get("--dport")
        if dport:
            port_start, port_end = _parse_port_spec(dport)

        rules.append(
            FirewallRuleSpec(
                action=action,
                protocol=protocol,
                direction=direction,
                source_cidr=source_cidr,
                destination_cidr=dest_cidr,
                port_start=port_start,
                port_end=port_end,
            )
        )

    return rules
