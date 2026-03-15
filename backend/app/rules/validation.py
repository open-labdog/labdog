import ipaddress
from app.rules.model import FirewallRuleSpec


class RuleValidationError(ValueError):
    pass


def validate_cidr(cidr: str) -> None:
    """Validate IPv4 or IPv6 CIDR notation."""
    try:
        ipaddress.ip_network(cidr, strict=False)
    except ValueError as e:
        raise RuleValidationError(f"Invalid CIDR '{cidr}': {e}") from e


def validate_port(port: int) -> None:
    if not (1 <= port <= 65535):
        raise RuleValidationError(f"Port {port} out of range (1-65535)")


def validate_rule(rule: FirewallRuleSpec) -> list[str]:
    """Validate a rule. Returns list of warning strings (non-fatal issues)."""
    warnings = []

    # Validate CIDRs
    if rule.source_cidr:
        validate_cidr(rule.source_cidr)
    if rule.destination_cidr:
        validate_cidr(rule.destination_cidr)

    # Validate ports
    if rule.port_start is not None:
        validate_port(rule.port_start)
    if rule.port_end is not None:
        validate_port(rule.port_end)
        if rule.port_start is not None and rule.port_end < rule.port_start:
            raise RuleValidationError(f"port_end ({rule.port_end}) must be >= port_start ({rule.port_start})")

    # ICMP rules must not have ports
    if rule.protocol == "icmp" and rule.port_start is not None:
        raise RuleValidationError("ICMP rules cannot specify ports")

    # Warn on overly permissive rules
    if (rule.action == "allow"
            and rule.source_cidr in (None, "0.0.0.0/0", "::/0")
            and rule.port_start is None
            and rule.protocol == "any"):
        warnings.append("Rule allows all traffic from any source on any port — this effectively disables the firewall")

    return warnings


def check_duplicate(new_rule: FirewallRuleSpec, existing_rules: list[FirewallRuleSpec]) -> bool:
    """Returns True if an equivalent rule already exists."""
    return any(new_rule.matches(r) for r in existing_rules)
