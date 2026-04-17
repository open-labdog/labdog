import json

from app.sync.parsers.iptables import parse_iptables_save
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
                            "comment": "Barricade: SSH lockout rule",
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
        assert rules[0].comment == "Barricade: SSH lockout rule"

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
