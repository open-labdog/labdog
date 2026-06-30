import json

from app.rules.model import ChainPolicies, FirewallRuleSpec
from app.rules.renderers.iptables import render_iptables_rules
from app.sync.parsers.iptables import parse_iptables_policies, parse_iptables_save
from app.sync.parsers.nftables import parse_nftables_json

# ---------------------------------------------------------------------------
# nftables
# ---------------------------------------------------------------------------

_NFT_BASIC = json.dumps(
    {
        "nftables": [
            {"chain": {"family": "inet", "table": "filter", "name": "input", "hook": "input"}},
            {
                "rule": {
                    "family": "inet",
                    "table": "filter",
                    "chain": "input",
                    "expr": [
                        {
                            "match": {
                                "left": {"payload": {"protocol": "tcp", "field": "dport"}},
                                "right": 22,
                            }
                        },
                        {"accept": None},
                    ],
                }
            },
            {
                "rule": {
                    "family": "inet",
                    "table": "filter",
                    "chain": "input",
                    "expr": [
                        {
                            "match": {
                                "left": {"payload": {"protocol": "tcp", "field": "dport"}},
                                "right": 80,
                            }
                        },
                        {"drop": None},
                    ],
                }
            },
        ]
    }
)

_NFT_INFRA = json.dumps(
    {
        "nftables": [
            {"chain": {"family": "inet", "table": "filter", "name": "input", "hook": "input"}},
            # ct state established — should be skipped
            {
                "rule": {
                    "family": "inet",
                    "table": "filter",
                    "chain": "input",
                    "expr": [
                        {"match": {"left": {"ct": {"key": "state"}}, "right": "established"}},
                        {"accept": None},
                    ],
                }
            },
            # loopback (iif — interface index, used by nftables >= 1.0) — should be skipped
            {
                "rule": {
                    "family": "inet",
                    "table": "filter",
                    "chain": "input",
                    "expr": [
                        {"match": {"left": {"meta": {"key": "iif"}}, "right": "lo"}},
                        {"accept": None},
                    ],
                }
            },
            # loopback (iifname — interface name, older nftables) — should be skipped
            {
                "rule": {
                    "family": "inet",
                    "table": "filter",
                    "chain": "input",
                    "expr": [
                        {"match": {"left": {"meta": {"key": "iifname"}}, "right": "lo"}},
                        {"accept": None},
                    ],
                }
            },
            # real rule — should be kept
            {
                "rule": {
                    "family": "inet",
                    "table": "filter",
                    "chain": "input",
                    "expr": [
                        {
                            "match": {
                                "left": {"payload": {"protocol": "tcp", "field": "dport"}},
                                "right": 443,
                            }
                        },
                        {"accept": None},
                    ],
                }
            },
        ]
    }
)

_NFT_PORT_RANGE = json.dumps(
    {
        "nftables": [
            {"chain": {"family": "inet", "table": "filter", "name": "input", "hook": "input"}},
            {
                "rule": {
                    "family": "inet",
                    "table": "filter",
                    "chain": "input",
                    "expr": [
                        {
                            "match": {
                                "left": {"payload": {"protocol": "tcp", "field": "dport"}},
                                "right": {"range": [80, 443]},
                            }
                        },
                        {"accept": None},
                    ],
                }
            },
        ]
    }
)


class TestNftablesParser:
    def test_parse_nftables_basic_rules(self):
        rules = parse_nftables_json(_NFT_BASIC)
        assert len(rules) == 2
        ssh = next(r for r in rules if r.port_start == 22)
        assert ssh.action == "allow"
        assert ssh.protocol == "tcp"
        assert ssh.direction == "input"
        http = next(r for r in rules if r.port_start == 80)
        assert http.action == "deny"

    def test_parse_nftables_skips_infrastructure(self):
        rules = parse_nftables_json(_NFT_INFRA)
        # Only the port-443 rule should survive
        assert len(rules) == 1
        assert rules[0].port_start == 443
        assert rules[0].action == "allow"

    def test_parse_nftables_port_range(self):
        rules = parse_nftables_json(_NFT_PORT_RANGE)
        assert len(rules) == 1
        r = rules[0]
        assert r.port_start == 80
        assert r.port_end == 443
        assert r.action == "allow"

    def test_parse_nftables_empty_nftables_key(self):
        rules = parse_nftables_json(json.dumps({"nftables": []}))
        assert rules == []

    def test_parse_nftables_ignores_non_inet_filter(self):
        data = json.dumps(
            {
                "nftables": [
                    {
                        "chain": {
                            "family": "ip",
                            "table": "nat",
                            "name": "prerouting",
                            "hook": "prerouting",
                        }
                    },
                    {
                        "rule": {
                            "family": "ip",
                            "table": "nat",
                            "chain": "prerouting",
                            "expr": [
                                {
                                    "match": {
                                        "left": {"payload": {"protocol": "tcp", "field": "dport"}},
                                        "right": 80,
                                    }
                                },
                                {"accept": None},
                            ],
                        }
                    },
                ]
            }
        )
        rules = parse_nftables_json(data)
        assert rules == []

    def test_parse_nftables_rule_level_comment(self):
        """nftables >= 1.0 puts comments at the rule level, not in expressions."""
        data = json.dumps(
            {
                "nftables": [
                    {
                        "chain": {
                            "family": "inet",
                            "table": "filter",
                            "name": "input",
                            "hook": "input",
                        }
                    },
                    {
                        "rule": {
                            "family": "inet",
                            "table": "filter",
                            "chain": "input",
                            "comment": "LabDog: SSH lockout rule",
                            "expr": [
                                {
                                    "match": {
                                        "left": {"payload": {"protocol": "tcp", "field": "dport"}},
                                        "right": 22,
                                    }
                                },
                                {"accept": None},
                            ],
                        }
                    },
                ]
            }
        )
        rules = parse_nftables_json(data)
        assert len(rules) == 1
        assert rules[0].comment == "LabDog: SSH lockout rule"

    def test_parse_nftables_expr_level_comment(self):
        """Older nftables puts comments as expressions — ensure fallback works."""
        data = json.dumps(
            {
                "nftables": [
                    {
                        "chain": {
                            "family": "inet",
                            "table": "filter",
                            "name": "input",
                            "hook": "input",
                        }
                    },
                    {
                        "rule": {
                            "family": "inet",
                            "table": "filter",
                            "chain": "input",
                            "expr": [
                                {
                                    "match": {
                                        "left": {"payload": {"protocol": "tcp", "field": "dport"}},
                                        "right": 443,
                                    }
                                },
                                {"accept": None},
                                {"comment": "Allow HTTPS"},
                            ],
                        }
                    },
                ]
            }
        )
        rules = parse_nftables_json(data)
        assert len(rules) == 1
        assert rules[0].comment == "Allow HTTPS"

    def test_parse_nftables_output_chain(self):
        data = json.dumps(
            {
                "nftables": [
                    {
                        "chain": {
                            "family": "inet",
                            "table": "filter",
                            "name": "output",
                            "hook": "output",
                        }
                    },
                    {
                        "rule": {
                            "family": "inet",
                            "table": "filter",
                            "chain": "output",
                            "expr": [
                                {
                                    "match": {
                                        "left": {"payload": {"protocol": "tcp", "field": "dport"}},
                                        "right": 8080,
                                    }
                                },
                                {"accept": None},
                            ],
                        }
                    },
                ]
            }
        )
        rules = parse_nftables_json(data)
        assert len(rules) == 1
        assert rules[0].direction == "output"
        assert rules[0].port_start == 8080


# ---------------------------------------------------------------------------
# iptables
# ---------------------------------------------------------------------------

_IPTABLES_BASIC = """\
*filter
:INPUT DROP [0:0]
:FORWARD DROP [0:0]
:OUTPUT ACCEPT [0:0]
-A INPUT -m state --state ESTABLISHED,RELATED -j ACCEPT
-A INPUT -i lo -j ACCEPT
-A INPUT -p tcp --dport 22 -j ACCEPT
-A INPUT -p tcp --dport 80:443 -s 10.0.0.0/8 -j ACCEPT
-A INPUT -j DROP
COMMIT
"""


class TestIptablesParser:
    def test_parse_iptables_basic_rules(self):
        rules = parse_iptables_save(_IPTABLES_BASIC)
        assert len(rules) == 3
        ssh = next(r for r in rules if r.port_start == 22)
        assert ssh.action == "allow"
        assert ssh.protocol == "tcp"
        assert ssh.direction == "input"

    def test_parse_iptables_skips_infrastructure(self):
        rules = parse_iptables_save(_IPTABLES_BASIC)
        # conntrack and loopback rules should be skipped
        for r in rules:
            assert r.protocol != "any" or r.port_start is not None or r.action == "deny"

    def test_parse_iptables_port_range(self):
        rules = parse_iptables_save(_IPTABLES_BASIC)
        range_rule = next(r for r in rules if r.port_start == 80)
        assert range_rule.port_end == 443
        assert range_rule.source_cidr == "10.0.0.0/8"
        assert range_rule.action == "allow"

    def test_parse_iptables_skips_forward_chain(self):
        content = """\
*filter
-A INPUT -p tcp --dport 22 -j ACCEPT
-A FORWARD -p tcp --dport 80 -j ACCEPT
-A OUTPUT -p tcp --dport 443 -j ACCEPT
COMMIT
"""
        rules = parse_iptables_save(content)
        assert len(rules) == 2
        assert all(r.direction in ("input", "output") for r in rules)

    def test_parse_iptables_drop_rule(self):
        rules = parse_iptables_save(_IPTABLES_BASIC)
        drop_rules = [r for r in rules if r.action == "deny"]
        assert len(drop_rules) == 1

    def test_parse_iptables_empty_content(self):
        rules = parse_iptables_save("")
        assert rules == []

    def test_parse_iptables_output_chain(self):
        content = """\
*filter
-A OUTPUT -p tcp --dport 8080 -j ACCEPT
COMMIT
"""
        rules = parse_iptables_save(content)
        assert len(rules) == 1
        assert rules[0].direction == "output"
        assert rules[0].port_start == 8080

    def test_parse_iptables_labdog_jump_chains(self):
        """LabDog never writes rules directly into INPUT/OUTPUT — it jumps to its
        own LABDOG-INPUT/LABDOG-OUTPUT chains (renderers/iptables.py) so reapply
        stays idempotent without touching Docker's or other tools' base-chain
        rules. The collector must follow that indirection or it always reports
        zero rules for every iptables-backend host."""
        content = """\
*filter
:INPUT ACCEPT [0:0]
:FORWARD DROP [0:0]
:OUTPUT ACCEPT [0:0]
:LABDOG-INPUT - [0:0]
:LABDOG-OUTPUT - [0:0]
-A INPUT -j LABDOG-INPUT
-A OUTPUT -j LABDOG-OUTPUT
-A LABDOG-INPUT -m state --state ESTABLISHED,RELATED -j ACCEPT
-A LABDOG-INPUT -i lo -j ACCEPT
-A LABDOG-INPUT -s 10.10.101.0/28 -p tcp --dport 22 -m comment --comment "Managed by LabDog: Allow SSH" -j ACCEPT
-A LABDOG-INPUT -p tcp --dport 22 -m comment --comment "Managed by LabDog" -j DROP
-A LABDOG-INPUT -m comment --comment "Managed by LabDog: default policy" -j DROP
-A LABDOG-OUTPUT -m comment --comment "Managed by LabDog: default policy" -j ACCEPT
COMMIT
"""
        rules = parse_iptables_save(content)
        assert len(rules) == 2
        ssh = next(r for r in rules if r.port_start == 22 and r.action == "allow")
        assert ssh.direction == "input"
        assert ssh.source_cidr == "10.10.101.0/28"
        deny = next(r for r in rules if r.action == "deny")
        assert deny.direction == "input"
        # the bare jump rule and the synthetic default-policy catch-all must
        # not surface as explicit rules
        assert all("default policy" not in (r.comment or "") for r in rules)

    def test_parse_iptables_policies_reads_labdog_catchall(self):
        """Base INPUT/OUTPUT stay ACCEPT regardless (the jump chain handles the
        drop before control returns), so the effective policy must come from
        LABDOG-INPUT/LABDOG-OUTPUT's catch-all rule, not the base declaration."""
        content = """\
*filter
:INPUT ACCEPT [0:0]
:OUTPUT ACCEPT [0:0]
:LABDOG-INPUT - [0:0]
:LABDOG-OUTPUT - [0:0]
-A INPUT -j LABDOG-INPUT
-A OUTPUT -j LABDOG-OUTPUT
-A LABDOG-INPUT -m comment --comment "Managed by LabDog: default policy" -j DROP
-A LABDOG-OUTPUT -m comment --comment "Managed by LabDog: default policy" -j ACCEPT
COMMIT
"""
        policies = parse_iptables_policies(content)
        assert policies.input == "drop"
        assert policies.output == "accept"

    def test_parse_iptables_policies_falls_back_without_labdog_chains(self):
        content = """\
*filter
:INPUT DROP [0:0]
:OUTPUT ACCEPT [0:0]
COMMIT
"""
        policies = parse_iptables_policies(content)
        assert policies.input == "drop"
        assert policies.output == "accept"

    def test_parse_iptables_round_trip_with_renderer(self):
        """Regression guard: whatever the renderer emits, the collector must be
        able to parse back out, or drift detection silently reports everything
        as missing."""
        rules = [
            FirewallRuleSpec(
                action="allow", protocol="tcp", direction="input", port_start=22,
                source_cidr="10.10.101.0/28", comment="Allow SSH",
            ),
            FirewallRuleSpec(action="deny", protocol="tcp", direction="input", port_start=22),
        ]
        policies = ChainPolicies(input="drop", output="accept")
        ipv4_content, _ = render_iptables_rules(rules, policies)

        parsed_rules = parse_iptables_save(ipv4_content)
        parsed_policies = parse_iptables_policies(ipv4_content)

        assert len(parsed_rules) == 2
        assert parsed_policies.input == "drop"
        assert parsed_policies.output == "accept"
