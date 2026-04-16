"""Tests for host-reference support on firewall rules and /etc/hosts entries."""
import pytest
from pydantic import ValidationError

from app.rules.model import FirewallRuleSpec
from app.rules.resolver import (
    HostRefResolutionError,
    collect_referenced_host_ids,
    resolve_host_refs,
)
from app.rules.merge import merge_group_rules
from app.schemas.rules import RuleCreate
from app.hosts_mgmt.schemas import HostsEntryCreate


class TestRuleResolver:
    def test_ipv4_resolved_to_slash_32(self):
        spec = FirewallRuleSpec(action="allow", protocol="tcp", direction="input", source_host_id=7)
        out = resolve_host_refs([spec], {7: "192.168.1.10"})
        assert out[0].source_cidr == "192.168.1.10/32"
        assert out[0].source_host_id == 7  # preserved

    def test_ipv6_resolved_to_slash_128(self):
        spec = FirewallRuleSpec(action="allow", protocol="tcp", direction="output", destination_host_id=3)
        out = resolve_host_refs([spec], {3: "2001:db8::1"})
        assert out[0].destination_cidr == "2001:db8::1/128"

    def test_literal_cidr_untouched(self):
        spec = FirewallRuleSpec(
            action="allow", protocol="tcp", direction="input",
            source_cidr="10.0.0.0/8",
        )
        out = resolve_host_refs([spec], {})
        assert out[0].source_cidr == "10.0.0.0/8"

    def test_missing_host_raises(self):
        spec = FirewallRuleSpec(action="allow", protocol="tcp", direction="input", source_host_id=9)
        with pytest.raises(HostRefResolutionError):
            resolve_host_refs([spec], {})

    def test_empty_ip_raises(self):
        spec = FirewallRuleSpec(action="allow", protocol="tcp", direction="input", source_host_id=9)
        with pytest.raises(HostRefResolutionError):
            resolve_host_refs([spec], {9: None})

    def test_collect_ids(self):
        specs = [
            FirewallRuleSpec(action="allow", protocol="tcp", direction="input", source_host_id=1),
            FirewallRuleSpec(action="allow", protocol="tcp", direction="output", destination_host_id=2),
            FirewallRuleSpec(action="deny", protocol="tcp", direction="input", source_cidr="10.0.0.0/8"),
        ]
        assert collect_referenced_host_ids(specs) == {1, 2}


class TestMergeWithHostRefs:
    def test_same_host_ref_is_deduped(self):
        specs = [
            FirewallRuleSpec(action="allow", protocol="tcp", direction="input", source_host_id=5, port_start=22),
            FirewallRuleSpec(action="allow", protocol="tcp", direction="input", source_host_id=5, port_start=22),
        ]
        merged = merge_group_rules(
            [{"id": 1, "priority": 100, "rules": specs}],
            server_ip="10.0.0.1",
        )
        # SSH lockout prepended (1), plus 1 unique host-ref rule
        assert len(merged) == 2

    def test_different_host_refs_not_deduped(self):
        specs = [
            FirewallRuleSpec(action="allow", protocol="tcp", direction="input", source_host_id=5, port_start=22),
            FirewallRuleSpec(action="allow", protocol="tcp", direction="input", source_host_id=6, port_start=22),
        ]
        merged = merge_group_rules(
            [{"id": 1, "priority": 100, "rules": specs}],
            server_ip="10.0.0.1",
        )
        assert len(merged) == 3  # lockout + 2 unique


class TestSchemaValidation:
    def test_rule_cidr_and_host_mutually_exclusive(self):
        with pytest.raises(ValidationError):
            RuleCreate(
                action="allow", protocol="tcp", direction="input",
                source_cidr="1.2.3.4/32", source_host_id=1,
            )

    def test_rule_accepts_neither_source_nor_dest(self):
        # existing semantics: both null = "any"
        r = RuleCreate(action="allow", protocol="tcp", direction="input")
        assert r.source_cidr is None and r.source_host_id is None

    def test_rule_host_only(self):
        r = RuleCreate(action="allow", protocol="tcp", direction="input", source_host_id=42)
        assert r.source_host_id == 42

    def test_hosts_entry_literal_ok(self):
        e = HostsEntryCreate(ip_address="10.0.0.1", hostname="foo")
        assert e.host_ref_id is None

    def test_hosts_entry_ref_ok(self):
        e = HostsEntryCreate(host_ref_id=5)
        assert e.ip_address is None

    def test_hosts_entry_ref_and_literal_rejected(self):
        with pytest.raises(ValidationError):
            HostsEntryCreate(ip_address="1.2.3.4", hostname="foo", host_ref_id=5)

    def test_hosts_entry_no_fields_rejected(self):
        with pytest.raises(ValidationError):
            HostsEntryCreate()
