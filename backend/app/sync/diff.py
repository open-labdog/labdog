from dataclasses import dataclass, field
from app.rules.model import FirewallRuleSpec


@dataclass
class RulesetDiff:
    """Result of comparing current vs desired firewall rules."""
    rules_to_add: list[FirewallRuleSpec] = field(default_factory=list)
    rules_to_remove: list[FirewallRuleSpec] = field(default_factory=list)
    rules_unchanged: list[FirewallRuleSpec] = field(default_factory=list)

    @property
    def has_changes(self) -> bool:
        return bool(self.rules_to_add or self.rules_to_remove)

    def summary(self) -> dict:
        return {
            "add": len(self.rules_to_add),
            "remove": len(self.rules_to_remove),
            "unchanged": len(self.rules_unchanged),
            "has_changes": self.has_changes,
        }


def compute_diff(
    current: list[FirewallRuleSpec],
    desired: list[FirewallRuleSpec],
) -> RulesetDiff:
    """
    Compare current (on host) vs desired (from DB) rules.
    Uses matches() for comparison (ignores comments, priority, IDs).
    """
    diff = RulesetDiff()

    # Find rules in desired but not in current (to add)
    for d_rule in desired:
        found = any(d_rule.matches(c_rule) for c_rule in current)
        if found:
            diff.rules_unchanged.append(d_rule)
        else:
            diff.rules_to_add.append(d_rule)

    # Find rules in current but not in desired (to remove)
    for c_rule in current:
        found = any(c_rule.matches(d_rule) for d_rule in desired)
        if not found:
            diff.rules_to_remove.append(c_rule)

    return diff


async def fetch_current_state_stub(host_id: int) -> list[FirewallRuleSpec]:
    """
    Stub for fetching current firewall state from a host.
    Real implementation will use ansible-runner to run commands on the host.
    Returns empty list for now (meaning host has no rules, all desired rules will be "to add").
    """
    # Real implementation in a future enhancement will:
    # - nftables: run `nft -j list ruleset` and parse JSON
    # - firewalld: use `ansible.posix.firewalld_info` module
    # - ufw: slurp `/etc/ufw/user.rules` and parse
    return []
