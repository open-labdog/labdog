"""Tests for firewall rule ↔ spec conversion."""

import pytest

from app.models.firewall_rule import FirewallRule
from app.rules.converter import firewall_rule_to_spec, firewall_rules_to_specs, spec_to_firewall_rule
from app.rules.model import FirewallRuleSpec

pytestmark = pytest.mark.integration


class TestConverter:
    def test_roundtrip_preserves_fields(self):
        """spec → rule → spec: fields match."""
        original = FirewallRuleSpec(
            action="allow",
            protocol="tcp",
            direction="input",
            source_cidr="10.0.0.0/8",
            destination_cidr="192.168.1.0/24",
            port_start=443,
            port_end=445,
            comment="HTTPS range",
            is_system=False,
            priority=5,
        )
        rule = spec_to_firewall_rule(original, group_id=42)
        roundtripped = firewall_rule_to_spec(rule)

        assert roundtripped.action == original.action
        assert roundtripped.protocol == original.protocol
        assert roundtripped.direction == original.direction
        assert roundtripped.source_cidr == original.source_cidr
        assert roundtripped.destination_cidr == original.destination_cidr
        assert roundtripped.port_start == original.port_start
        assert roundtripped.port_end == original.port_end
        assert roundtripped.comment == original.comment
        assert roundtripped.is_system == original.is_system
        assert roundtripped.priority == original.priority

    def test_batch_conversion(self):
        """firewall_rules_to_specs returns correct count."""
        rules = []
        for i in range(5):
            r = FirewallRule(
                group_id=1,
                action="allow",
                protocol="tcp",
                direction="input",
                port_start=80 + i,
                priority=i,
            )
            r.id = i + 1
            rules.append(r)

        specs = firewall_rules_to_specs(rules)
        assert len(specs) == 5
        assert specs[0].port_start == 80
        assert specs[4].port_start == 84

    def test_spec_to_rule_sets_group_id(self):
        """spec_to_firewall_rule(spec, 42) → rule.group_id == 42."""
        spec = FirewallRuleSpec(
            action="deny",
            protocol="udp",
            direction="output",
            port_start=53,
        )
        rule = spec_to_firewall_rule(spec, group_id=42)
        assert rule.group_id == 42
        assert rule.action == "deny"
        assert rule.protocol == "udp"
        assert rule.direction == "output"
        assert rule.port_start == 53

    def test_none_ports_preserved(self):
        """Spec with no ports converts to rule with None ports."""
        spec = FirewallRuleSpec(
            action="allow",
            protocol="icmp",
            direction="input",
        )
        rule = spec_to_firewall_rule(spec, group_id=1)
        assert rule.port_start is None
        assert rule.port_end is None

    def test_system_flag_preserved(self):
        """is_system flag survives roundtrip."""
        spec = FirewallRuleSpec(
            action="allow",
            protocol="tcp",
            direction="input",
            port_start=22,
            is_system=True,
        )
        rule = spec_to_firewall_rule(spec, group_id=1)
        assert rule.is_system is True
        roundtripped = firewall_rule_to_spec(rule)
        assert roundtripped.is_system is True
