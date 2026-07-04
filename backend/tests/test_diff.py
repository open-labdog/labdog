from app.rules.model import FirewallRuleSpec
from app.sync.diff import compute_diff


def test_diff_finds_additions():
    current = [FirewallRuleSpec(action="allow", protocol="tcp", direction="input", port_start=22)]
    desired = [
        FirewallRuleSpec(action="allow", protocol="tcp", direction="input", port_start=22),
        FirewallRuleSpec(action="allow", protocol="tcp", direction="input", port_start=443),
    ]
    diff = compute_diff(current, desired)
    assert len(diff.rules_to_add) == 1
    assert diff.rules_to_add[0].port_start == 443


def test_diff_finds_removals():
    current = [
        FirewallRuleSpec(action="allow", protocol="tcp", direction="input", port_start=22),
        FirewallRuleSpec(action="allow", protocol="tcp", direction="input", port_start=80),
    ]
    desired = [FirewallRuleSpec(action="allow", protocol="tcp", direction="input", port_start=22)]
    diff = compute_diff(current, desired)
    assert len(diff.rules_to_remove) == 1
    assert diff.rules_to_remove[0].port_start == 80


def test_diff_finds_unchanged():
    rule = FirewallRuleSpec(action="allow", protocol="tcp", direction="input", port_start=22)
    diff = compute_diff([rule], [rule])
    assert len(diff.rules_unchanged) == 1
    assert not diff.has_changes


def test_diff_has_changes():
    current = []
    desired = [FirewallRuleSpec(action="allow", protocol="tcp", direction="input", port_start=80)]
    diff = compute_diff(current, desired)
    assert diff.has_changes


def test_diff_empty_both():
    diff = compute_diff([], [])
    assert not diff.has_changes
    assert len(diff.rules_to_add) == 0
    assert len(diff.rules_to_remove) == 0


def test_diff_host_ref_rule_matches_parsed_cidr():
    # Regression: a desired rule that referenced a labdog host is resolved to a
    # concrete /32 CIDR before diffing, but keeps its source_host_id FK (the
    # effective-rules display needs it to show the host name). The current state
    # parsed back over SSH only ever carries the CIDR (bare, no /32, no FK).
    # These are the same rule and must diff as unchanged — not remove + re-add.
    desired = [
        FirewallRuleSpec(
            action="allow",
            protocol="tcp",
            direction="input",
            source_cidr="10.10.3.3/32",
            source_host_id=7,
            port_start=22,
        )
    ]
    current = [
        FirewallRuleSpec(
            action="allow",
            protocol="tcp",
            direction="input",
            source_cidr="10.10.3.3",
            comment="Managed by LabDog",
            port_start=22,
        )
    ]
    diff = compute_diff(current, desired)
    assert not diff.has_changes
    assert len(diff.rules_unchanged) == 1
    assert not diff.rules_to_add
    assert not diff.rules_to_remove
