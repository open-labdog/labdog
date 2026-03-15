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
