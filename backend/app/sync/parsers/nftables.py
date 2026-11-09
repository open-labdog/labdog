"""Parse nftables JSON output (nft -j list ruleset) into FirewallRuleSpec list."""

import json
from dataclasses import dataclass

from app.rules.model import ChainPolicies, FirewallRuleSpec

_ACTION_MAP = {"accept": "allow", "drop": "deny", "reject": "reject"}


def _is_infrastructure_rule(exprs: list[dict]) -> bool:
    """Detect ct state established/related and iif lo rules that should be skipped."""
    for expr in exprs:
        if "match" in expr:
            left = expr["match"].get("left", {})
            if isinstance(left, dict):
                if left.get("ct", {}).get("key") == "state":
                    return True
                if left.get("meta", {}).get("key") in ("iif", "iifname"):
                    return True
    return False


def _extract_port(right: object) -> tuple[int | None, int | None]:
    """Extract port or port range from nftables match right-hand side."""
    if isinstance(right, int):
        return right, None
    if isinstance(right, dict) and "range" in right:
        rng = right["range"]
        if isinstance(rng, list) and len(rng) == 2:
            return int(rng[0]), int(rng[1])
    return None, None


def _extract_cidr(right: object) -> str | None:
    """Extract CIDR notation from nftables address match (string or prefix object)."""
    if isinstance(right, str):
        return right
    if isinstance(right, dict) and "prefix" in right:
        prefix = right["prefix"]
        return f"{prefix['addr']}/{prefix['len']}"
    return None


def parse_nftables_json(json_str: str) -> list[FirewallRuleSpec]:
    """Parse ``nft -j list ruleset`` JSON into canonical rule specs.

    Processes only ``inet filter`` table, ``input`` and ``output`` chains.
    Skips infrastructure rules (ct state established/related, loopback).
    """
    data = json.loads(json_str)
    nftables = data.get("nftables", [])

    chain_directions = _filter_chain_directions(nftables)

    rules: list[FirewallRuleSpec] = []
    for item in nftables:
        if "rule" not in item:
            continue
        rule = item["rule"]
        key = (rule.get("family", ""), rule.get("table", ""), rule.get("chain", ""))
        if key not in chain_directions:
            continue

        direction = chain_directions[key]
        exprs = rule.get("expr", [])

        if _is_infrastructure_rule(exprs):
            continue

        spec = _rule_spec(rule, exprs, direction)
        if spec is not None:
            rules.append(spec)

    return rules


def _filter_chain_directions(nftables: list[dict]) -> dict[tuple[str, str, str], str]:
    """``(family, table, chain) -> hook`` for the ``inet filter`` chains
    hooked at input or output, the only ones LabDog reads rules from."""
    chain_directions: dict[tuple[str, str, str], str] = {}
    for item in nftables:
        if "chain" in item:
            c = item["chain"]
            family, table, name, hook = (
                c.get("family", ""),
                c.get("table", ""),
                c.get("name", ""),
                c.get("hook", ""),
            )
            if family == "inet" and table == "filter" and hook in ("input", "output"):
                chain_directions[(family, table, name)] = hook
    return chain_directions


@dataclass
class _RuleFields:
    """What a rule's expressions have said so far; later ones win."""

    comment: str | None
    action: str | None = None
    protocol: str = "any"
    port_start: int | None = None
    port_end: int | None = None
    source_cidr: str | None = None
    dest_cidr: str | None = None


def _rule_spec(rule: dict, exprs: list[dict], direction: str) -> FirewallRuleSpec | None:
    """The rule as a spec, or None when it has no verdict LabDog maps."""
    fields = _RuleFields(comment=rule.get("comment"))  # nftables >= 1.0 puts comment at rule level

    for expr in exprs:
        for nft_action, canonical in _ACTION_MAP.items():
            if nft_action in expr:
                fields.action = canonical
                break

        if "match" in expr:
            _apply_match(fields, expr["match"])

        if "comment" in expr:
            fields.comment = expr["comment"]

    if fields.action is None:
        return None

    return FirewallRuleSpec(
        action=fields.action,
        protocol=fields.protocol,
        direction=direction,
        source_cidr=fields.source_cidr,
        destination_cidr=fields.dest_cidr,
        port_start=fields.port_start,
        port_end=fields.port_end,
        comment=fields.comment,
    )


def _apply_match(fields: _RuleFields, match: dict) -> None:
    """Fold one ``match`` expression into ``fields``."""
    left = match.get("left", {})
    right = match.get("right")

    if isinstance(left, dict) and "payload" in left:
        payload = left["payload"]
        field = payload.get("field", "")
        proto = payload.get("protocol", "")

        if field == "dport":
            fields.port_start, fields.port_end = _extract_port(right)
            if proto in ("tcp", "udp"):
                fields.protocol = proto
        elif field == "sport":
            if proto in ("tcp", "udp"):
                fields.protocol = proto
        elif field == "saddr":
            fields.source_cidr = _extract_cidr(right)
        elif field == "daddr":
            fields.dest_cidr = _extract_cidr(right)
        elif field == "protocol":
            if isinstance(right, str):
                fields.protocol = right

    if isinstance(left, dict) and "meta" in left:
        if left["meta"].get("key") == "l4proto" and isinstance(right, str):
            fields.protocol = right


def parse_nftables_policies(json_str: str) -> ChainPolicies:
    """Extract chain default policies from ``nft -j list ruleset`` JSON."""
    data = json.loads(json_str)
    nftables = data.get("nftables", [])

    input_policy = "drop"
    output_policy = "accept"

    for item in nftables:
        if "chain" not in item:
            continue
        c = item["chain"]
        if c.get("family") != "inet" or c.get("table") != "filter":
            continue
        hook = c.get("hook", "")
        policy = c.get("policy", "")
        if hook == "input" and policy:
            input_policy = policy
        elif hook == "output" and policy:
            output_policy = policy

    return ChainPolicies(input=input_policy, output=output_policy)
