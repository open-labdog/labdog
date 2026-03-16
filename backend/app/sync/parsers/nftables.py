"""Parse nftables JSON output (nft -j list ruleset) into FirewallRuleSpec list."""

import json
from typing import Optional

from app.rules.model import FirewallRuleSpec

_ACTION_MAP = {"accept": "allow", "drop": "deny", "reject": "reject"}


def _is_infrastructure_rule(exprs: list[dict]) -> bool:
    """Detect ct state established/related and iif lo rules that should be skipped."""
    for expr in exprs:
        if "match" in expr:
            left = expr["match"].get("left", {})
            if isinstance(left, dict):
                if left.get("ct", {}).get("key") == "state":
                    return True
                if left.get("meta", {}).get("key") == "iifname":
                    return True
    return False


def _extract_port(right: object) -> tuple[Optional[int], Optional[int]]:
    """Extract port or port range from nftables match right-hand side."""
    if isinstance(right, int):
        return right, None
    if isinstance(right, dict) and "range" in right:
        rng = right["range"]
        if isinstance(rng, list) and len(rng) == 2:
            return int(rng[0]), int(rng[1])
    return None, None


def _extract_cidr(right: object) -> Optional[str]:
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

        action: Optional[str] = None
        protocol: str = "any"
        port_start: Optional[int] = None
        port_end: Optional[int] = None
        source_cidr: Optional[str] = None
        dest_cidr: Optional[str] = None
        comment: Optional[str] = None

        for expr in exprs:
            for nft_action, canonical in _ACTION_MAP.items():
                if nft_action in expr:
                    action = canonical
                    break

            if "match" in expr:
                left = expr["match"].get("left", {})
                right = expr["match"].get("right")

                if isinstance(left, dict) and "payload" in left:
                    payload = left["payload"]
                    field = payload.get("field", "")
                    proto = payload.get("protocol", "")

                    if field == "dport":
                        port_start, port_end = _extract_port(right)
                        if proto in ("tcp", "udp"):
                            protocol = proto
                    elif field == "sport":
                        if proto in ("tcp", "udp"):
                            protocol = proto
                    elif field == "saddr":
                        source_cidr = _extract_cidr(right)
                    elif field == "daddr":
                        dest_cidr = _extract_cidr(right)
                    elif field == "protocol":
                        if isinstance(right, str):
                            protocol = right

                if isinstance(left, dict) and "meta" in left:
                    if left["meta"].get("key") == "l4proto" and isinstance(right, str):
                        protocol = right

            if "comment" in expr:
                comment = expr["comment"]

        if action is None:
            continue

        rules.append(
            FirewallRuleSpec(
                action=action,
                protocol=protocol,
                direction=direction,
                source_cidr=source_cidr,
                destination_cidr=dest_cidr,
                port_start=port_start,
                port_end=port_end,
                comment=comment,
            )
        )

    return rules
