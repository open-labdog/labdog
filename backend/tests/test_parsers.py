import json

import pytest

from app.sync.parsers.nftables import parse_nftables_json
from app.sync.parsers.firewalld import parse_firewalld_output
from app.sync.parsers.ufw import parse_ufw_rules


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
            # loopback — should be skipped
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
# firewalld
# ---------------------------------------------------------------------------

_FIREWALLD_BASIC = """\
public (active)
  target: default
  services: ssh
  ports: 80/tcp 443/tcp
  rich rules:
    rule family="ipv4" source address="10.0.0.0/8" port port="3306" protocol="tcp" accept
    rule family="ipv4" drop
"""

_FIREWALLD_EMPTY = ""


class TestFirewalldParser:
    def test_parse_firewalld_ports_line(self):
        rules = parse_firewalld_output(_FIREWALLD_BASIC)
        port_rules = [r for r in rules if r.port_start in (80, 443)]
        assert len(port_rules) == 2
        ports = {r.port_start for r in port_rules}
        assert ports == {80, 443}
        for r in port_rules:
            assert r.action == "allow"
            assert r.protocol == "tcp"
            assert r.direction == "input"

    def test_parse_firewalld_rich_rule(self):
        rules = parse_firewalld_output(_FIREWALLD_BASIC)
        rich = [r for r in rules if r.source_cidr == "10.0.0.0/8"]
        assert len(rich) == 1
        r = rich[0]
        assert r.action == "allow"
        assert r.protocol == "tcp"
        assert r.port_start == 3306

    def test_parse_firewalld_empty_output(self):
        rules = parse_firewalld_output(_FIREWALLD_EMPTY)
        assert rules == []

    def test_parse_firewalld_drop_rich_rule(self):
        rules = parse_firewalld_output(_FIREWALLD_BASIC)
        drop_rules = [r for r in rules if r.action == "deny"]
        assert len(drop_rules) == 1

    def test_parse_firewalld_port_range(self):
        output = "  ports: 3306-3310/tcp\n"
        rules = parse_firewalld_output(output)
        assert len(rules) == 1
        assert rules[0].port_start == 3306
        assert rules[0].port_end == 3310

    def test_parse_firewalld_no_ports_line(self):
        output = "public (active)\n  target: default\n  services: ssh\n"
        rules = parse_firewalld_output(output)
        assert rules == []


# ---------------------------------------------------------------------------
# UFW
# ---------------------------------------------------------------------------

_UFW_BASIC = """\
*filter
:ufw-user-input - [0:0]
:ufw-user-output - [0:0]
-A ufw-user-input -p tcp --dport 22 -j ACCEPT
-A ufw-user-input -p tcp --dport 80:443 -s 10.0.0.0/8 -j ACCEPT
-A ufw-user-input -j DROP
COMMIT
"""


class TestUfwParser:
    def test_parse_ufw_basic_rules(self):
        rules = parse_ufw_rules(_UFW_BASIC)
        assert len(rules) == 3
        ssh = next(r for r in rules if r.port_start == 22)
        assert ssh.action == "allow"
        assert ssh.protocol == "tcp"
        assert ssh.direction == "input"

    def test_parse_ufw_port_range(self):
        rules = parse_ufw_rules(_UFW_BASIC)
        range_rule = next(r for r in rules if r.port_start == 80)
        assert range_rule.port_end == 443
        assert range_rule.source_cidr == "10.0.0.0/8"
        assert range_rule.action == "allow"

    def test_parse_ufw_skips_non_user_chains(self):
        content = """\
*filter
-A INPUT -p tcp --dport 22 -j ACCEPT
-A FORWARD -j DROP
-A ufw-user-input -p tcp --dport 8080 -j ACCEPT
COMMIT
"""
        rules = parse_ufw_rules(content)
        # Only ufw-user-input chain should be parsed
        assert len(rules) == 1
        assert rules[0].port_start == 8080

    def test_parse_ufw_drop_rule(self):
        rules = parse_ufw_rules(_UFW_BASIC)
        drop_rules = [r for r in rules if r.action == "deny"]
        assert len(drop_rules) == 1

    def test_parse_ufw_empty_content(self):
        rules = parse_ufw_rules("")
        assert rules == []

    def test_parse_ufw_output_chain(self):
        content = """\
*filter
-A ufw-user-output -p tcp --dport 443 -j ACCEPT
COMMIT
"""
        rules = parse_ufw_rules(content)
        assert len(rules) == 1
        assert rules[0].direction == "output"
        assert rules[0].port_start == 443
