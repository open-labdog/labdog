import ipaddress
import logging
from typing import Any

import yaml

from app.gitops.schema import BarricadeGroupYAML, FirewallRuleYAML
from app.rules.model import FirewallRuleSpec

logger = logging.getLogger(__name__)


class YAMLParseError(Exception):
    """Raised when YAML parsing or validation fails."""

    pass


def parse_yaml(yaml_str: str) -> BarricadeGroupYAML:
    """Parse YAML string into validated BarricadeGroupYAML model.

    Raises YAMLParseError with descriptive message on failure.
    Silently ignores unknown top-level keys (future modules).
    """
    try:
        data = yaml.safe_load(yaml_str)
    except yaml.YAMLError as e:
        raise YAMLParseError(f"Invalid YAML syntax: {e}") from e

    if not isinstance(data, dict):
        raise YAMLParseError("YAML must be a mapping (key-value pairs)")

    try:
        return BarricadeGroupYAML.model_validate(data)
    except Exception as e:
        raise YAMLParseError(f"YAML validation failed: {e}") from e


def yaml_rules_to_specs(rules: list[FirewallRuleYAML]) -> list[FirewallRuleSpec]:
    """Convert YAML rule models to FirewallRuleSpec list.

    - Strips system rules with logged warning
    - Converts port format: int -> single port, "start-end" -> range
    - Validates CIDRs and ports
    """
    specs: list[FirewallRuleSpec] = []
    for rule in rules:
        # Strip system rules
        if rule.system:
            logger.warning(
                "Stripping system rule from YAML import: %s %s",
                rule.action,
                rule.protocol,
            )
            continue

        # Parse port field
        port_start: int | None = None
        port_end: int | None = None
        if rule.port is not None:
            if isinstance(rule.port, int):
                port_start = rule.port
            elif isinstance(rule.port, str) and "-" in rule.port:
                parts = rule.port.split("-", 1)
                try:
                    port_start = int(parts[0])
                    port_end = int(parts[1])
                except ValueError:
                    raise YAMLParseError(f"Invalid port range: {rule.port}")
            else:
                try:
                    port_start = int(rule.port)
                except ValueError:
                    raise YAMLParseError(f"Invalid port value: {rule.port}")

        # Validate CIDR notation
        if rule.source:
            try:
                ipaddress.ip_network(rule.source, strict=False)
            except ValueError:
                raise YAMLParseError(f"Invalid source CIDR: {rule.source}")
        if rule.dest:
            try:
                ipaddress.ip_network(rule.dest, strict=False)
            except ValueError:
                raise YAMLParseError(f"Invalid dest CIDR: {rule.dest}")

        # ICMP + port conflict
        if rule.protocol == "icmp" and rule.port is not None:
            raise YAMLParseError("ICMP protocol cannot have a port specified")

        specs.append(
            FirewallRuleSpec(
                action=rule.action,
                protocol=rule.protocol,
                direction=rule.direction,
                source_cidr=rule.source,
                destination_cidr=rule.dest,
                port_start=port_start,
                port_end=port_end,
                comment=rule.comment,
            )
        )

    return specs


def specs_to_yaml(
    specs: list[FirewallRuleSpec],
    group_name: str,
    priority: int = 0,
    input_policy: str | None = None,
    output_policy: str | None = None,
) -> str:
    """Convert FirewallRuleSpec list back to YAML string (for export/debugging)."""
    rules: list[dict[str, Any]] = []
    for spec in specs:
        rule: dict[str, Any] = {
            "action": spec.action,
            "protocol": spec.protocol,
            "direction": spec.direction,
        }
        if spec.source_cidr:
            rule["source"] = spec.source_cidr
        if spec.destination_cidr:
            rule["dest"] = spec.destination_cidr
        if spec.port_start is not None:
            if spec.port_end and spec.port_end != spec.port_start:
                rule["port"] = f"{spec.port_start}-{spec.port_end}"
            else:
                rule["port"] = spec.port_start
        if spec.comment:
            rule["comment"] = spec.comment
        if spec.is_system:
            rule["system"] = True
        rules.append(rule)

    firewall: dict[str, Any] = {"rules": rules}
    if input_policy:
        firewall["input_policy"] = input_policy
    if output_policy:
        firewall["output_policy"] = output_policy

    data: dict[str, Any] = {
        "group": group_name,
        "priority": priority,
        "firewall": firewall,
    }
    return f"# Managed by Barricade\n{yaml.dump(data, default_flow_style=False, sort_keys=False)}"
