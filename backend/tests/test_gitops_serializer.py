"""Tests for GitOps YAML serializer — parse_yaml and yaml_rules_to_specs."""

import logging

import pytest

from app.gitops.schema import BarricadeGroupYAML
from app.gitops.serializer import YAMLParseError, parse_yaml, yaml_rules_to_specs

pytestmark = pytest.mark.integration


VALID_YAML = """\
group: web-servers
priority: 100
firewall:
  rules:
    - action: allow
      protocol: tcp
      direction: input
      port: 443
      source: 10.0.0.0/8
      comment: HTTPS inbound
    - action: deny
      protocol: udp
      direction: output
      dest: 0.0.0.0/0
"""


class TestYAMLSerializer:
    def test_valid_yaml_parses(self):
        """YAML with firewall rules parses into BarricadeGroupYAML."""
        result = parse_yaml(VALID_YAML)
        assert isinstance(result, BarricadeGroupYAML)
        assert result.group == "web-servers"
        assert result.priority == 100
        assert result.firewall is not None
        assert len(result.firewall.rules) == 2

        first = result.firewall.rules[0]
        assert first.action == "allow"
        assert first.protocol == "tcp"
        assert first.port == 443
        assert first.source == "10.0.0.0/8"

    def test_port_single_int(self):
        """port: 443 → port_start=443, port_end=None."""
        yaml_str = """\
group: test
firewall:
  rules:
    - action: allow
      protocol: tcp
      direction: input
      port: 443
"""
        parsed = parse_yaml(yaml_str)
        specs = yaml_rules_to_specs(parsed.firewall.rules)
        assert len(specs) == 1
        assert specs[0].port_start == 443
        assert specs[0].port_end is None

    def test_port_range_string(self):
        """port: "3306-3310" → port_start=3306, port_end=3310."""
        yaml_str = """\
group: test
firewall:
  rules:
    - action: allow
      protocol: tcp
      direction: input
      port: "3306-3310"
"""
        parsed = parse_yaml(yaml_str)
        specs = yaml_rules_to_specs(parsed.firewall.rules)
        assert len(specs) == 1
        assert specs[0].port_start == 3306
        assert specs[0].port_end == 3310

    def test_invalid_action_rejected(self):
        """action: 'explode' → YAMLParseError (via pydantic validation)."""
        yaml_str = """\
group: test
firewall:
  rules:
    - action: explode
      protocol: tcp
      direction: input
"""
        with pytest.raises(YAMLParseError, match="validation failed"):
            parse_yaml(yaml_str)

    def test_unknown_keys_ignored(self):
        """Extra top-level keys for future modules are silently ignored."""
        yaml_str = """\
group: test
future_module:
  - some_field: value
users:
  - login: deploy
firewall:
  rules:
    - action: allow
      protocol: tcp
      direction: input
      port: 80
"""
        parsed = parse_yaml(yaml_str)
        assert parsed.group == "test"
        assert parsed.firewall is not None
        assert len(parsed.firewall.rules) == 1

    def test_system_rules_stripped(self, caplog):
        """system: true rules are stripped with a warning log."""
        yaml_str = """\
group: test
firewall:
  rules:
    - action: allow
      protocol: tcp
      direction: input
      port: 22
      system: true
    - action: allow
      protocol: tcp
      direction: input
      port: 443
"""
        parsed = parse_yaml(yaml_str)
        assert len(parsed.firewall.rules) == 2

        with caplog.at_level(logging.WARNING):
            specs = yaml_rules_to_specs(parsed.firewall.rules)

        assert len(specs) == 1
        assert specs[0].port_start == 443
        assert "Stripping system rule" in caplog.text

    def test_empty_rules_list(self):
        """firewall.rules: [] → empty list, no error."""
        yaml_str = """\
group: test
firewall:
  rules: []
"""
        parsed = parse_yaml(yaml_str)
        specs = yaml_rules_to_specs(parsed.firewall.rules)
        assert specs == []

    def test_invalid_yaml_syntax(self):
        """Broken YAML raises YAMLParseError."""
        with pytest.raises(YAMLParseError, match="Invalid YAML syntax"):
            parse_yaml("group: [unterminated")

    def test_non_mapping_yaml_rejected(self):
        """Plain scalar YAML raises YAMLParseError."""
        with pytest.raises(YAMLParseError, match="must be a mapping"):
            parse_yaml("just a string")

    def test_invalid_port_range(self):
        """port: 'abc-def' → YAMLParseError."""
        yaml_str = """\
group: test
firewall:
  rules:
    - action: allow
      protocol: tcp
      direction: input
      port: "abc-def"
"""
        parsed = parse_yaml(yaml_str)
        with pytest.raises(YAMLParseError, match="Invalid port range"):
            yaml_rules_to_specs(parsed.firewall.rules)
