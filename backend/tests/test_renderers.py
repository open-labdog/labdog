from app.rules.model import FirewallRuleSpec
from app.rules.renderers.iptables import render_iptables_rules
from app.rules.renderers.nftables import render_nftables_config


def _sample_rules():
    return [
        FirewallRuleSpec(
            action="allow",
            protocol="tcp",
            direction="input",
            source_cidr="10.0.0.1/32",
            port_start=22,
            comment="SSH",
            is_system=True,
        ),
        FirewallRuleSpec(
            action="allow", protocol="tcp", direction="input", port_start=80, comment="HTTP"
        ),
        FirewallRuleSpec(
            action="deny", protocol="tcp", direction="input", port_start=3306, comment="MySQL"
        ),
    ]


class TestNftablesRenderer:
    def test_scoped_table_flush(self):
        config = render_nftables_config(_sample_rules())
        assert "flush ruleset" not in config
        assert "delete table inet filter" in config

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
        rules = [
            FirewallRuleSpec(
                action="allow",
                protocol="tcp",
                direction="input",
                source_cidr="2001:db8::/32",
                port_start=443,
            )
        ]
        config = render_nftables_config(rules)
        assert "ip6 saddr" in config


class TestIptablesRenderer:
    def test_returns_tuple(self):
        v4, v6 = render_iptables_rules(_sample_rules())
        assert isinstance(v4, str)
        assert isinstance(v6, str)

    def test_contains_filter_table(self):
        v4, _ = render_iptables_rules(_sample_rules())
        assert "*filter" in v4

    def test_contains_commit(self):
        v4, _ = render_iptables_rules(_sample_rules())
        assert "COMMIT" in v4

    def test_contains_default_policies(self):
        v4, _ = render_iptables_rules(_sample_rules())
        assert ":LABDOG-INPUT - [0:0]" in v4
        assert ":LABDOG-OUTPUT - [0:0]" in v4
        assert "-A LABDOG-INPUT -j DROP" in v4
        assert "-A LABDOG-OUTPUT -j ACCEPT" in v4

    def test_contains_conntrack(self):
        v4, _ = render_iptables_rules(_sample_rules())
        assert "-A LABDOG-INPUT -m state --state ESTABLISHED,RELATED -j ACCEPT" in v4

    def test_contains_loopback(self):
        v4, _ = render_iptables_rules(_sample_rules())
        assert "-A LABDOG-INPUT -i lo -j ACCEPT" in v4

    def test_ipv6_rule_goes_to_v6_file(self):
        rules = [
            FirewallRuleSpec(
                action="allow",
                protocol="tcp",
                direction="input",
                source_cidr="2001:db8::/32",
                port_start=443,
            )
        ]
        v4, v6 = render_iptables_rules(rules)
        assert "2001:db8::/32" not in v4
        assert "2001:db8::/32" in v6
