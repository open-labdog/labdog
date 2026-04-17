import pytest

from app.rules.merge import merge_group_rules
from app.rules.model import FirewallRuleSpec
from app.rules.validation import RuleValidationError, check_duplicate, validate_rule


class TestFirewallRuleSpec:
    def test_port_display_single(self):
        r = FirewallRuleSpec(action="allow", protocol="tcp", direction="input", port_start=80)
        assert r.port_display() == "80"

    def test_port_display_range(self):
        r = FirewallRuleSpec(
            action="allow", protocol="tcp", direction="input", port_start=8000, port_end=8100
        )
        assert r.port_display() == "8000-8100"

    def test_port_display_any(self):
        r = FirewallRuleSpec(action="allow", protocol="tcp", direction="input")
        assert r.port_display() == "any"

    def test_matches_same(self):
        r1 = FirewallRuleSpec(action="allow", protocol="tcp", direction="input", port_start=80)
        r2 = FirewallRuleSpec(
            action="allow", protocol="tcp", direction="input", port_start=80, comment="different"
        )
        assert r1.matches(r2)

    def test_matches_different(self):
        r1 = FirewallRuleSpec(action="allow", protocol="tcp", direction="input", port_start=80)
        r2 = FirewallRuleSpec(action="deny", protocol="tcp", direction="input", port_start=80)
        assert not r1.matches(r2)


class TestValidation:
    def test_valid_ipv4_cidr(self):
        r = FirewallRuleSpec(
            action="allow", protocol="tcp", direction="input", source_cidr="10.0.0.0/8"
        )
        warnings = validate_rule(r)
        assert isinstance(warnings, list)

    def test_valid_ipv6_cidr(self):
        r = FirewallRuleSpec(
            action="allow", protocol="tcp", direction="input", source_cidr="2001:db8::/32"
        )
        warnings = validate_rule(r)
        assert isinstance(warnings, list)

    def test_invalid_cidr_raises(self):
        r = FirewallRuleSpec(
            action="allow", protocol="tcp", direction="input", source_cidr="not-a-cidr"
        )
        with pytest.raises(RuleValidationError):
            validate_rule(r)

    def test_icmp_with_port_raises(self):
        r = FirewallRuleSpec(action="allow", protocol="icmp", direction="input", port_start=80)
        with pytest.raises(RuleValidationError):
            validate_rule(r)

    def test_port_out_of_range(self):
        r = FirewallRuleSpec(action="allow", protocol="tcp", direction="input", port_start=70000)
        with pytest.raises(RuleValidationError):
            validate_rule(r)

    def test_port_end_less_than_start(self):
        r = FirewallRuleSpec(
            action="allow", protocol="tcp", direction="input", port_start=100, port_end=50
        )
        with pytest.raises(RuleValidationError):
            validate_rule(r)

    def test_permissive_rule_warning(self):
        r = FirewallRuleSpec(
            action="allow", protocol="any", direction="input", source_cidr="0.0.0.0/0"
        )
        warnings = validate_rule(r)
        assert len(warnings) > 0

    def test_duplicate_detection(self):
        r1 = FirewallRuleSpec(action="allow", protocol="tcp", direction="input", port_start=80)
        existing = [r1]
        r2 = FirewallRuleSpec(action="allow", protocol="tcp", direction="input", port_start=80)
        assert check_duplicate(r2, existing)


class TestMerge:
    def test_priority_merge_higher_wins(self):
        g1 = {
            "id": 1,
            "priority": 200,
            "rules": [
                FirewallRuleSpec(action="deny", protocol="tcp", direction="input", port_start=80)
            ],
        }
        g2 = {
            "id": 2,
            "priority": 100,
            "rules": [
                FirewallRuleSpec(action="allow", protocol="tcp", direction="input", port_start=80)
            ],
        }
        merged = merge_group_rules([g1, g2], server_ip="10.0.0.1")
        port80 = [r for r in merged if r.port_start == 80]
        assert len(port80) == 1
        assert port80[0].action == "deny"

    def test_ssh_lockout_rule_always_present(self):
        g = {
            "id": 1,
            "priority": 100,
            "rules": [
                FirewallRuleSpec(action="allow", protocol="tcp", direction="input", port_start=80)
            ],
        }
        merged = merge_group_rules([g], server_ip="10.0.0.1")
        ssh = [r for r in merged if r.is_system]
        assert len(ssh) >= 1
        assert ssh[0].port_start == 22

    def test_empty_groups(self):
        merged = merge_group_rules([], server_ip="10.0.0.1")
        # Should still have SSH lockout rule
        assert len(merged) >= 1
        assert merged[0].is_system
