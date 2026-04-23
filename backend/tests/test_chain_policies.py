"""Tests for configurable chain default policies."""

import json

from app.rules.merge import merge_group_policies
from app.rules.model import ChainPolicies, FirewallRuleSpec
from app.rules.renderers.iptables import render_iptables_rules
from app.rules.renderers.nftables import render_nftables_config
from app.sync.diff import compute_diff
from app.sync.parsers.iptables import parse_iptables_policies
from app.sync.parsers.nftables import parse_nftables_policies

# ---------------------------------------------------------------------------
# ChainPolicies dataclass
# ---------------------------------------------------------------------------


class TestChainPolicies:
    def test_defaults(self):
        p = ChainPolicies()
        assert p.input == "drop"
        assert p.output == "accept"

    def test_custom(self):
        p = ChainPolicies(input="accept", output="drop")
        assert p.input == "accept"
        assert p.output == "drop"


# ---------------------------------------------------------------------------
# Merge engine
# ---------------------------------------------------------------------------


class TestMergeGroupPolicies:
    def test_no_groups(self):
        p = merge_group_policies([])
        assert p.input == "drop"
        assert p.output == "accept"
        assert p.input_source_group_id is None
        assert p.output_source_group_id is None

    def test_no_policies_set(self):
        groups = [
            {
                "id": 1,
                "name": "Base",
                "priority": 100,
                "rules": [],
                "input_policy": None,
                "output_policy": None,
            },
        ]
        p = merge_group_policies(groups)
        assert p.input == "drop"
        assert p.output == "accept"
        assert p.input_source_group_id is None
        assert p.output_source_group_id is None

    def test_single_group_sets_input(self):
        groups = [
            {
                "id": 1,
                "name": "Base",
                "priority": 100,
                "rules": [],
                "input_policy": "accept",
                "output_policy": None,
            },
        ]
        p = merge_group_policies(groups)
        assert p.input == "accept"
        assert p.output == "accept"  # default
        assert p.input_source_group_id == 1
        assert p.input_source_group_name == "Base"
        assert p.output_source_group_id is None

    def test_single_group_sets_output(self):
        groups = [
            {
                "id": 1,
                "name": "Base",
                "priority": 100,
                "rules": [],
                "input_policy": None,
                "output_policy": "drop",
            },
        ]
        p = merge_group_policies(groups)
        assert p.input == "drop"  # default
        assert p.output == "drop"
        assert p.input_source_group_id is None
        assert p.output_source_group_id == 1
        assert p.output_source_group_name == "Base"

    def test_highest_priority_wins(self):
        groups = [
            {
                "id": 1,
                "name": "Low",
                "priority": 50,
                "rules": [],
                "input_policy": "drop",
                "output_policy": "accept",
            },
            {
                "id": 2,
                "name": "High",
                "priority": 200,
                "rules": [],
                "input_policy": "accept",
                "output_policy": "drop",
            },
        ]
        p = merge_group_policies(groups)
        assert p.input == "accept"  # from group 2 (priority 200)
        assert p.output == "drop"  # from group 2 (priority 200)
        assert p.input_source_group_id == 2
        assert p.input_source_group_name == "High"
        assert p.output_source_group_id == 2
        assert p.output_source_group_name == "High"

    def test_partial_definition_per_chain(self):
        """Group A sets input, Group B sets output — both should take effect."""
        groups = [
            {
                "id": 1,
                "name": "GroupA",
                "priority": 100,
                "rules": [],
                "input_policy": "accept",
                "output_policy": None,
            },
            {
                "id": 2,
                "name": "GroupB",
                "priority": 50,
                "rules": [],
                "input_policy": None,
                "output_policy": "drop",
            },
        ]
        p = merge_group_policies(groups)
        assert p.input == "accept"  # from group 1 (priority 100)
        assert p.output == "drop"  # from group 2 (only one that sets it)
        assert p.input_source_group_id == 1
        assert p.input_source_group_name == "GroupA"
        assert p.output_source_group_id == 2
        assert p.output_source_group_name == "GroupB"

    def test_lower_priority_fills_gaps(self):
        """Higher priority sets input, lower priority sets output."""
        groups = [
            {
                "id": 1,
                "name": "High",
                "priority": 200,
                "rules": [],
                "input_policy": "accept",
                "output_policy": None,
            },
            {
                "id": 2,
                "name": "Low",
                "priority": 100,
                "rules": [],
                "input_policy": "drop",
                "output_policy": "drop",
            },
        ]
        p = merge_group_policies(groups)
        assert p.input == "accept"  # from group 1 (priority 200)
        assert p.output == "drop"  # from group 2 (group 1 didn't set it)
        assert p.input_source_group_id == 1
        assert p.output_source_group_id == 2

    def test_missing_policy_keys(self):
        """Groups without policy keys should still work."""
        groups = [
            {"id": 1, "name": "Bare", "priority": 100, "rules": []},
        ]
        p = merge_group_policies(groups)
        assert p.input == "drop"
        assert p.output == "accept"
        assert p.input_source_group_id is None
        assert p.output_source_group_id is None


# ---------------------------------------------------------------------------
# nftables renderer
# ---------------------------------------------------------------------------


class TestNftablesRendererPolicies:
    def test_default_policies(self):
        config = render_nftables_config([])
        assert "policy drop;" in config
        assert "policy accept;" in config

    def test_custom_input_accept(self):
        config = render_nftables_config([], policies=ChainPolicies(input="accept"))
        assert "hook input priority 0; policy accept;" in config
        assert "hook output priority 0; policy accept;" in config

    def test_custom_output_drop(self):
        config = render_nftables_config([], policies=ChainPolicies(output="drop"))
        assert "hook input priority 0; policy drop;" in config
        assert "hook output priority 0; policy drop;" in config

    def test_both_custom(self):
        config = render_nftables_config([], policies=ChainPolicies(input="accept", output="drop"))
        assert "hook input priority 0; policy accept;" in config
        assert "hook output priority 0; policy drop;" in config

    def test_none_uses_defaults(self):
        config = render_nftables_config([], policies=None)
        assert "policy drop;" in config
        assert "policy accept;" in config


# ---------------------------------------------------------------------------
# iptables renderer
# ---------------------------------------------------------------------------


class TestIptablesRendererPolicies:
    def test_default_policies(self):
        v4, _ = render_iptables_rules([])
        assert ":LABDOG-INPUT - [0:0]" in v4
        assert ":LABDOG-OUTPUT - [0:0]" in v4
        assert "-A LABDOG-INPUT -j DROP" in v4
        assert "-A LABDOG-OUTPUT -j ACCEPT" in v4
        assert ":FORWARD" not in v4

    def test_custom_input_accept(self):
        v4, _ = render_iptables_rules([], policies=ChainPolicies(input="accept"))
        assert "-A LABDOG-INPUT -j ACCEPT" in v4
        assert "-A LABDOG-OUTPUT -j ACCEPT" in v4

    def test_custom_output_drop(self):
        v4, _ = render_iptables_rules([], policies=ChainPolicies(output="drop"))
        assert "-A LABDOG-INPUT -j DROP" in v4
        assert "-A LABDOG-OUTPUT -j DROP" in v4

    def test_none_uses_defaults(self):
        v4, _ = render_iptables_rules([], policies=None)
        assert "-A LABDOG-INPUT -j DROP" in v4
        assert "-A LABDOG-OUTPUT -j ACCEPT" in v4


# ---------------------------------------------------------------------------
# nftables parser
# ---------------------------------------------------------------------------


class TestNftablesPolicyParser:
    def test_parse_default_policies(self):
        data = json.dumps(
            {
                "nftables": [
                    {
                        "chain": {
                            "family": "inet",
                            "table": "filter",
                            "name": "input",
                            "hook": "input",
                            "policy": "drop",
                        }
                    },
                    {
                        "chain": {
                            "family": "inet",
                            "table": "filter",
                            "name": "output",
                            "hook": "output",
                            "policy": "accept",
                        }
                    },
                ]
            }
        )
        p = parse_nftables_policies(data)
        assert p.input == "drop"
        assert p.output == "accept"

    def test_parse_custom_policies(self):
        data = json.dumps(
            {
                "nftables": [
                    {
                        "chain": {
                            "family": "inet",
                            "table": "filter",
                            "name": "input",
                            "hook": "input",
                            "policy": "accept",
                        }
                    },
                    {
                        "chain": {
                            "family": "inet",
                            "table": "filter",
                            "name": "output",
                            "hook": "output",
                            "policy": "drop",
                        }
                    },
                ]
            }
        )
        p = parse_nftables_policies(data)
        assert p.input == "accept"
        assert p.output == "drop"

    def test_ignores_non_inet_filter(self):
        data = json.dumps(
            {
                "nftables": [
                    {
                        "chain": {
                            "family": "ip",
                            "table": "nat",
                            "name": "prerouting",
                            "hook": "prerouting",
                            "policy": "accept",
                        }
                    },
                    {
                        "chain": {
                            "family": "inet",
                            "table": "filter",
                            "name": "input",
                            "hook": "input",
                            "policy": "accept",
                        }
                    },
                ]
            }
        )
        p = parse_nftables_policies(data)
        assert p.input == "accept"

    def test_empty_nftables(self):
        data = json.dumps({"nftables": []})
        p = parse_nftables_policies(data)
        assert p.input == "drop"
        assert p.output == "accept"


# ---------------------------------------------------------------------------
# iptables parser
# ---------------------------------------------------------------------------


class TestIptablesPolicyParser:
    def test_parse_default_policies(self):
        content = """*filter
:INPUT DROP [0:0]
:FORWARD DROP [0:0]
:OUTPUT ACCEPT [0:0]
COMMIT
"""
        p = parse_iptables_policies(content)
        assert p.input == "drop"
        assert p.output == "accept"

    def test_parse_custom_policies(self):
        content = """*filter
:INPUT ACCEPT [123:456]
:FORWARD DROP [0:0]
:OUTPUT DROP [0:0]
COMMIT
"""
        p = parse_iptables_policies(content)
        assert p.input == "accept"
        assert p.output == "drop"

    def test_empty_content(self):
        p = parse_iptables_policies("")
        assert p.input == "drop"
        assert p.output == "accept"


# ---------------------------------------------------------------------------
# Diff with policies
# ---------------------------------------------------------------------------


class TestDiffWithPolicies:
    def test_no_policy_changes(self):
        current_p = ChainPolicies(input="drop", output="accept")
        desired_p = ChainPolicies(input="drop", output="accept")
        diff = compute_diff([], [], current_policies=current_p, desired_policies=desired_p)
        assert not diff.has_changes
        assert diff.policy_changes == {}

    def test_input_policy_change(self):
        current_p = ChainPolicies(input="drop", output="accept")
        desired_p = ChainPolicies(input="accept", output="accept")
        diff = compute_diff([], [], current_policies=current_p, desired_policies=desired_p)
        assert diff.has_changes
        assert "input" in diff.policy_changes
        assert diff.policy_changes["input"] == ("drop", "accept")
        assert "output" not in diff.policy_changes

    def test_output_policy_change(self):
        current_p = ChainPolicies(input="drop", output="accept")
        desired_p = ChainPolicies(input="drop", output="drop")
        diff = compute_diff([], [], current_policies=current_p, desired_policies=desired_p)
        assert diff.has_changes
        assert "output" in diff.policy_changes
        assert diff.policy_changes["output"] == ("accept", "drop")

    def test_both_policies_change(self):
        current_p = ChainPolicies(input="drop", output="accept")
        desired_p = ChainPolicies(input="accept", output="drop")
        diff = compute_diff([], [], current_policies=current_p, desired_policies=desired_p)
        assert diff.has_changes
        assert len(diff.policy_changes) == 2

    def test_policy_change_with_rule_changes(self):
        current_rules = [
            FirewallRuleSpec(action="allow", protocol="tcp", direction="input", port_start=22)
        ]
        desired_rules = [
            FirewallRuleSpec(action="allow", protocol="tcp", direction="input", port_start=22),
            FirewallRuleSpec(action="allow", protocol="tcp", direction="input", port_start=80),
        ]
        current_p = ChainPolicies(input="drop")
        desired_p = ChainPolicies(input="accept")
        diff = compute_diff(
            current_rules, desired_rules, current_policies=current_p, desired_policies=desired_p
        )
        assert diff.has_changes
        assert len(diff.rules_to_add) == 1
        assert "input" in diff.policy_changes

    def test_no_policies_provided(self):
        """Backward compatibility: no policies means no policy comparison."""
        diff = compute_diff([], [])
        assert not diff.has_changes
        assert diff.policy_changes == {}

    def test_summary_includes_policy_changes(self):
        current_p = ChainPolicies(input="drop")
        desired_p = ChainPolicies(input="accept")
        diff = compute_diff([], [], current_policies=current_p, desired_policies=desired_p)
        s = diff.summary()
        assert "policy_changes" in s
        assert s["has_changes"] is True
