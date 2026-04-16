from collections.abc import Sequence

from app.models.firewall_rule import FirewallRule
from app.rules.model import FirewallRuleSpec


def firewall_rule_to_spec(rule: FirewallRule) -> FirewallRuleSpec:
    """Convert SQLAlchemy FirewallRule model to FirewallRuleSpec dataclass."""
    return FirewallRuleSpec(
        action=rule.action.value if hasattr(rule.action, "value") else rule.action,
        protocol=rule.protocol.value if hasattr(rule.protocol, "value") else rule.protocol,
        direction=rule.direction.value if hasattr(rule.direction, "value") else rule.direction,
        source_cidr=rule.source_cidr,
        destination_cidr=rule.destination_cidr,
        source_host_id=rule.source_host_id,
        destination_host_id=rule.destination_host_id,
        port_start=rule.port_start,
        port_end=rule.port_end,
        comment=rule.comment,
        is_system=rule.is_system,
        priority=rule.priority,
        group_id=rule.group_id,
        host_id=rule.host_id,
        rule_id=rule.id,
    )


def firewall_rules_to_specs(rules: Sequence[FirewallRule]) -> list[FirewallRuleSpec]:
    """Batch convert FirewallRule models to specs."""
    return [firewall_rule_to_spec(r) for r in rules]


def spec_to_firewall_rule(spec: FirewallRuleSpec, group_id: int | None = None, host_id: int | None = None) -> FirewallRule:
    """Convert FirewallRuleSpec to a new FirewallRule ORM instance."""
    return FirewallRule(
        group_id=group_id,
        host_id=host_id,
        action=spec.action,
        protocol=spec.protocol,
        direction=spec.direction,
        source_cidr=spec.source_cidr,
        destination_cidr=spec.destination_cidr,
        source_host_id=spec.source_host_id,
        destination_host_id=spec.destination_host_id,
        port_start=spec.port_start,
        port_end=spec.port_end,
        comment=spec.comment,
        is_system=spec.is_system,
        priority=spec.priority,
    )
