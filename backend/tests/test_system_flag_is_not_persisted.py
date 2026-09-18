"""``is_system`` is a merge-time property, not a column.

Migration 0038 dropped ``firewall_rules.is_system`` and
``hosts_entries.is_system`` because no code path had ever written either:
the create schemas do not accept the field, the GitOps importers strip
``system: true`` before a row is built, and the only system rules and
entries LabDog has are synthesised at merge time. Five API guards, four
importer filters and two UI branches were protecting rows that could only
be made by hand.

The flag still exists where it is real — on ``FirewallRuleSpec`` and the
effective-rule and effective-entry responses — and this file pins the
line between the two. If a persisted flag comes back, it comes back
through here, deliberately, with a reason and a writer.

Not marked ``integration`` on purpose: the converter tests that used to
cover this are, and CI never runs them.
"""

from __future__ import annotations

from app.hosts_mgmt.models import HostsEntry
from app.hosts_mgmt.schemas import EffectiveHostsEntryResponse, HostsEntryResponse
from app.models.firewall_rule import FirewallRule
from app.rules.converter import spec_to_firewall_rule
from app.rules.model import FirewallRuleSpec
from app.schemas.rules import EffectiveRuleResponse, RuleResponse


class TestTheColumnIsGone:
    def test_firewall_rules_has_no_is_system_column(self) -> None:
        assert "is_system" not in FirewallRule.__table__.columns

    def test_hosts_entries_has_no_is_system_column(self) -> None:
        assert "is_system" not in HostsEntry.__table__.columns

    def test_a_system_spec_converts_to_an_ordinary_row(self) -> None:
        """The converter used to carry the flag onto the row. A spec that
        says system — the anti-lockout rule, if anyone ever tried to
        persist it — now produces a row with nothing to say about it."""
        spec = FirewallRuleSpec(
            action="allow",
            protocol="tcp",
            direction="input",
            port_start=22,
            is_system=True,
        )
        rule = spec_to_firewall_rule(spec, group_id=1)

        assert not hasattr(rule, "is_system")


class TestTheResponsesFollowTheColumn:
    """The persisted-row responses lose the field; the effective views keep
    it. Both halves matter: dropping it from the effective view would take
    the anti-lockout rule's badge with it."""

    def test_persisted_rule_response_has_no_flag(self) -> None:
        assert "is_system" not in RuleResponse.model_fields

    def test_persisted_entry_response_has_no_flag(self) -> None:
        assert "is_system" not in HostsEntryResponse.model_fields

    def test_effective_rule_response_keeps_it(self) -> None:
        assert "is_system" in EffectiveRuleResponse.model_fields

    def test_effective_entry_response_keeps_it(self) -> None:
        assert "is_system" in EffectiveHostsEntryResponse.model_fields
