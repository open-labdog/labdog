import ipaddress
import logging
from typing import Any

import yaml

from app.gitops.schema import BarricadeGroupYAML, FirewallRuleYAML, HostsEntryYAML, PackageRepositoryYAML, PackageYAML, ServiceYAML
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


def package_specs_to_yaml(specs: list[PackageYAML]) -> list[dict[str, Any]]:
    """Convert a list of ``PackageYAML`` models to YAML-ready dicts.

    Only non-default fields are included to keep output clean and minimal.
    Default values are: ``version=None``, ``state="present"``,
    ``package_manager="auto"``, ``priority=0``, ``comment=None``,
    ``hold=False``.

    Args:
        specs: Validated ``PackageYAML`` instances to serialise.

    Returns:
        A list of dicts suitable for embedding in a group YAML document.
    """
    result: list[dict[str, Any]] = []
    for spec in specs:
        entry: dict[str, Any] = {"package_name": spec.package_name}
        if spec.version is not None:
            entry["version"] = spec.version
        if spec.state != "present":
            entry["state"] = spec.state
        if spec.package_manager != "auto":
            entry["package_manager"] = spec.package_manager
        if spec.priority != 0:
            entry["priority"] = spec.priority
        if spec.comment is not None:
            entry["comment"] = spec.comment
        if spec.hold:
            entry["hold"] = spec.hold
        result.append(entry)
    return result


def package_repo_specs_to_yaml(specs: list[PackageRepositoryYAML]) -> list[dict[str, Any]]:
    """Convert a list of ``PackageRepositoryYAML`` models to YAML-ready dicts.

    Only non-default fields are included to keep output clean and minimal.
    Default values are: ``key_url=None``, ``distribution=None``,
    ``components=None``, ``state="present"``.

    Args:
        specs: Validated ``PackageRepositoryYAML`` instances to serialise.

    Returns:
        A list of dicts suitable for embedding in a group YAML document.
    """
    result: list[dict[str, Any]] = []
    for spec in specs:
        entry: dict[str, Any] = {
            "name": spec.name,
            "url": spec.url,
            "repo_type": spec.repo_type,
        }
        if spec.key_url is not None:
            entry["key_url"] = spec.key_url
        if spec.distribution is not None:
            entry["distribution"] = spec.distribution
        if spec.components is not None:
            entry["components"] = spec.components
        if spec.state != "present":
            entry["state"] = spec.state
        result.append(entry)
    return result


def hosts_entry_specs_to_yaml(specs: list[HostsEntryYAML]) -> list[dict[str, Any]]:
    """Convert a list of ``HostsEntryYAML`` models to YAML-ready dicts.

    Only non-default fields are included to keep output clean and minimal.
    Default values are: ``host_ref_id=None``, ``ip_address=None``,
    ``hostname=None``, ``aliases=[]``, ``comment=None``, ``priority=0``.

    The two variants (literal and reference) are handled transparently — for
    literal entries ``ip_address`` and ``hostname`` are always emitted; for
    reference entries ``host_ref_id`` is emitted and the literal fields are
    omitted (they are ``None``).

    Args:
        specs: Validated ``HostsEntryYAML`` instances to serialise.

    Returns:
        A list of dicts suitable for embedding in a group YAML document.
    """
    result: list[dict[str, Any]] = []
    for spec in specs:
        entry: dict[str, Any] = {}
        if spec.host_ref_id is not None:
            entry["host_ref_id"] = spec.host_ref_id
        else:
            entry["ip_address"] = spec.ip_address
            entry["hostname"] = spec.hostname
        if spec.aliases:
            entry["aliases"] = list(spec.aliases)
        if spec.comment is not None:
            entry["comment"] = spec.comment
        if spec.priority != 0:
            entry["priority"] = spec.priority
        result.append(entry)
    return result


def service_specs_to_yaml(specs: list[ServiceYAML]) -> list[dict[str, Any]]:
    """Convert a list of ``ServiceYAML`` models to YAML-ready dicts.

    Only non-default fields are included to keep output clean and minimal.
    Default values are: ``enabled=True``, ``priority=0``, ``comment=None``,
    ``unit_content=None``, ``deploy_mode="override"``.

    Args:
        specs: Validated ``ServiceYAML`` instances to serialise.

    Returns:
        A list of dicts suitable for embedding in a group YAML document.
    """
    result: list[dict[str, Any]] = []
    for spec in specs:
        entry: dict[str, Any] = {
            "service_name": spec.service_name,
            "state": spec.state,
        }
        if not spec.enabled:
            entry["enabled"] = spec.enabled
        if spec.priority != 0:
            entry["priority"] = spec.priority
        if spec.comment is not None:
            entry["comment"] = spec.comment
        if spec.unit_content is not None:
            entry["unit_content"] = spec.unit_content
        if spec.deploy_mode != "override":
            entry["deploy_mode"] = spec.deploy_mode
        result.append(entry)
    return result
