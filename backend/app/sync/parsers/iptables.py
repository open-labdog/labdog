"""Parse iptables-save output into FirewallRuleSpec list."""

import re

from app.rules.model import ChainPolicies, FirewallRuleSpec

_CHAIN_DIRECTION = {
    "INPUT": "input",
    "OUTPUT": "output",
    # LabDog never writes rules directly into the base INPUT/OUTPUT chains —
    # it jumps to its own chains (see renderers/iptables.py) so reapply stays
    # idempotent without clobbering Docker's or other tools' base-chain rules.
    "LABDOG-INPUT": "input",
    "LABDOG-OUTPUT": "output",
}

_ACTION_MAP = {"ACCEPT": "allow", "DROP": "deny", "REJECT": "reject"}

_RULE_RE = re.compile(r"^-A\s+(?P<chain>\S+)\s+(?P<flags>.+)$")

_DEFAULT_POLICY_COMMENT = "default policy"


def _parse_rule_flags(flags_str: str) -> dict[str, str]:
    """Parse iptables flag string into key-value pairs.

    Handles: -p tcp --dport 80 -s 10.0.0.0/8 -d 192.168.0.0/16 -j ACCEPT
    """
    result: dict[str, str] = {}
    tokens = flags_str.split()
    i = 0
    while i < len(tokens):
        token = tokens[i]
        # Skip the entire `--comment "..."` value so flag-like sequences inside
        # the comment text (e.g. `-j`, `-p`) cannot be misread as real flags.
        if token == "--comment" and i + 1 < len(tokens):  # nosec B105 — iptables flag, not a password
            val = tokens[i + 1]
            if val.startswith('"'):
                j = i + 1
                while j < len(tokens) and not tokens[j].endswith('"'):
                    j += 1
                if j < len(tokens):
                    i = j + 1
                    continue
            i += 2
            continue
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


def _is_infrastructure_rule(flags_str: str) -> bool:
    """Return True for conntrack/loopback rules and LabDog's synthetic
    default-policy catch-all rule — none of these are user-facing rules."""
    if "-m state --state" in flags_str:
        return True
    if "-i lo" in flags_str:
        return True
    if _DEFAULT_POLICY_COMMENT in flags_str:
        return True
    return False


def parse_iptables_save(content: str) -> list[FirewallRuleSpec]:
    """Parse ``iptables-save`` output into canonical rule specs."""
    rules: list[FirewallRuleSpec] = []

    for line in content.splitlines():
        line = line.strip()
        if not line.startswith("-A "):
            continue

        m = _RULE_RE.match(line)
        if not m:
            continue

        chain = m.group("chain")

        # Skip FORWARD chain rules
        if chain == "FORWARD":
            continue

        direction = _CHAIN_DIRECTION.get(chain)
        if direction is None:
            continue

        flags_str = m.group("flags")

        # Skip infrastructure rules (conntrack, loopback)
        if _is_infrastructure_rule(flags_str):
            continue

        flags = _parse_rule_flags(flags_str)

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


_POLICY_RE = re.compile(r"^:(?P<chain>\S+)\s+(?P<policy>\S+)\s+\[")


def parse_iptables_policies(content: str) -> ChainPolicies:
    """Extract effective chain default policies from ``iptables-save`` output.

    LabDog renders its chain policy as a catch-all rule inside its own
    LABDOG-INPUT/LABDOG-OUTPUT jump chains (commented "default policy"),
    not as the base chain's built-in policy — INPUT/OUTPUT stay ACCEPT
    regardless since the jump chain handles the drop before control would
    return there. Prefer that catch-all rule's target when present; fall
    back to the base chain's declared policy otherwise.
    """
    input_policy = "drop"
    output_policy = "accept"

    for line in content.splitlines():
        m = _POLICY_RE.match(line.strip())
        if not m:
            continue
        chain = m.group("chain")
        policy = m.group("policy").lower()
        if chain == "INPUT":
            input_policy = policy
        elif chain == "OUTPUT":
            output_policy = policy

    for line in content.splitlines():
        line = line.strip()
        if not line.startswith("-A "):
            continue
        m = _RULE_RE.match(line)
        if not m:
            continue
        flags_str = m.group("flags")
        if _DEFAULT_POLICY_COMMENT not in flags_str:
            continue
        target = _parse_rule_flags(flags_str).get("-j")
        if target not in ("ACCEPT", "DROP"):
            continue
        chain = m.group("chain")
        if chain == "LABDOG-INPUT":
            input_policy = "accept" if target == "ACCEPT" else "drop"
        elif chain == "LABDOG-OUTPUT":
            output_policy = "accept" if target == "ACCEPT" else "drop"

    return ChainPolicies(input=input_policy, output=output_policy)
