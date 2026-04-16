"""Resolve host-reference FKs on FirewallRuleSpec into concrete CIDR strings.

A rule may reference a registered Host instead of a literal CIDR via
`source_host_id` / `destination_host_id`. At playbook render time we resolve
those FKs by looking up the host's current `ip_address` and substituting it as
a /32 (IPv4) or /128 (IPv6) CIDR on the spec.
"""
from __future__ import annotations

import ipaddress
from dataclasses import replace
from typing import Iterable, Mapping

from app.rules.model import FirewallRuleSpec


class HostRefResolutionError(ValueError):
    """Raised when a referenced host cannot be resolved to a usable IP."""


def _ip_to_host_cidr(ip: str) -> str:
    addr = ipaddress.ip_address(ip)
    return f"{ip}/32" if addr.version == 4 else f"{ip}/128"


def collect_referenced_host_ids(specs: Iterable[FirewallRuleSpec]) -> set[int]:
    ids: set[int] = set()
    for s in specs:
        if s.source_host_id is not None:
            ids.add(s.source_host_id)
        if s.destination_host_id is not None:
            ids.add(s.destination_host_id)
    return ids


def resolve_host_refs(
    specs: Iterable[FirewallRuleSpec],
    host_ips: Mapping[int, str | None],
) -> list[FirewallRuleSpec]:
    """Return a new list of specs with host refs materialized into CIDRs.

    `host_ips` must contain every host_id appearing on any spec's source/dest
    host ref. Missing keys or hosts without an IP raise HostRefResolutionError.
    """
    resolved: list[FirewallRuleSpec] = []
    for s in specs:
        src_cidr = s.source_cidr
        dst_cidr = s.destination_cidr
        if s.source_host_id is not None:
            ip = host_ips.get(s.source_host_id)
            if not ip:
                raise HostRefResolutionError(
                    f"rule references source host {s.source_host_id} with no IP"
                )
            src_cidr = _ip_to_host_cidr(ip)
        if s.destination_host_id is not None:
            ip = host_ips.get(s.destination_host_id)
            if not ip:
                raise HostRefResolutionError(
                    f"rule references destination host {s.destination_host_id} with no IP"
                )
            dst_cidr = _ip_to_host_cidr(ip)
        resolved.append(replace(s, source_cidr=src_cidr, destination_cidr=dst_cidr))
    return resolved
