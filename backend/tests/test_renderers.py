from app.rules.model import FirewallRuleSpec
from app.rules.renderers.nftables import render_nftables_config
from app.rules.renderers.firewalld import render_firewalld_tasks
from app.rules.renderers.ufw import render_ufw_rules


def _sample_rules():
    return [
        FirewallRuleSpec(action="allow", protocol="tcp", direction="input",
                        source_cidr="10.0.0.1/32", port_start=22,
                        comment="SSH", is_system=True),
        FirewallRuleSpec(action="allow", protocol="tcp", direction="input",
                        port_start=80, comment="HTTP"),
        FirewallRuleSpec(action="deny", protocol="tcp", direction="input",
                        port_start=3306, comment="MySQL"),
    ]


class TestNftablesRenderer:
    def test_contains_flush(self):
        config = render_nftables_config(_sample_rules())
        assert "flush ruleset" in config

    def test_contains_inet_filter(self):
        config = render_nftables_config(_sample_rules())
        assert "inet filter" in config

    def test_contains_stateful_tracking(self):
        config = render_nftables_config(_sample_rules())
        assert "ct state established,related accept" in config

    def test_contains_loopback(self):
        config = render_nftables_config(_sample_rules())
        assert "iif lo accept" in config

    def test_contains_ssh_rule(self):
        config = render_nftables_config(_sample_rules())
        assert "dport 22" in config

    def test_ipv6_cidr_uses_ip6(self):
        rules = [FirewallRuleSpec(action="allow", protocol="tcp", direction="input",
                                  source_cidr="2001:db8::/32", port_start=443)]
        config = render_nftables_config(rules)
        assert "ip6 saddr" in config


class TestFirewalldRenderer:
    def test_returns_list(self):
        tasks = render_firewalld_tasks(_sample_rules())
        assert isinstance(tasks, list)
        assert len(tasks) == 3

    def test_permanent_and_immediate(self):
        tasks = render_firewalld_tasks(_sample_rules())
        for t in tasks:
            assert t.get("permanent") == True
            assert t.get("immediate") == True


class TestUfwRenderer:
    def test_returns_tuple(self):
        v4, v6 = render_ufw_rules(_sample_rules())
        assert isinstance(v4, str)
        assert isinstance(v6, str)

    def test_contains_filter_table(self):
        v4, _ = render_ufw_rules(_sample_rules())
        assert "*filter" in v4

    def test_contains_commit(self):
        v4, _ = render_ufw_rules(_sample_rules())
        assert "COMMIT" in v4
