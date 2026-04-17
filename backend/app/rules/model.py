import ipaddress
from dataclasses import dataclass


def _normalize_cidr(cidr: str | None) -> str | None:
    """Normalize CIDR to a consistent format.

    Bare IPs get a host prefix (e.g. '10.0.0.1' -> '10.0.0.1/32').
    """
    if cidr is None:
        return None
    try:
        net = ipaddress.ip_network(cidr, strict=False)
        return str(net)
    except ValueError:
        return cidr


def _normalize_port_end(port_start: int | None, port_end: int | None) -> int | None:
    """Normalize port_end: treat port_end == port_start as None (single port)."""
    if port_end is not None and port_end == port_start:
        return None
    return port_end


@dataclass
class FirewallRuleSpec:
    """Canonical rule representation — backend-agnostic."""

    action: str  # "allow" | "deny" | "reject"
    protocol: str  # "tcp" | "udp" | "icmp" | "any"
    direction: str  # "input" | "output"
    source_cidr: str | None = None  # IPv4 or IPv6 CIDR
    destination_cidr: str | None = None
    source_host_id: int | None = None  # FK to hosts.id; resolved to CIDR at render time
    destination_host_id: int | None = None
    port_start: int | None = None  # single port or range start
    port_end: int | None = None  # range end (None = single port)
    comment: str | None = None
    is_system: bool = False  # True = auto-injected, non-deletable
    priority: int = 0  # ordering within group
    group_id: int | None = None  # source group (for merge tracking)
    host_id: int | None = None  # source host (for host-level overrides)
    group_priority: int | None = None  # source group priority (for display)
    rule_id: int | None = None  # DB id (for update tracking)

    def port_display(self) -> str:
        if self.port_start is None:
            return "any"
        if self.port_end and self.port_end != self.port_start:
            return f"{self.port_start}-{self.port_end}"
        return str(self.port_start)

    def _match_key(self) -> tuple:
        """Return a hashable key for functional equivalence comparison."""
        return (
            self.action,
            self.protocol,
            self.direction,
            _normalize_cidr(self.source_cidr),
            _normalize_cidr(self.destination_cidr),
            self.source_host_id,
            self.destination_host_id,
            self.port_start,
            _normalize_port_end(self.port_start, self.port_end),
        )

    def matches(self, other: "FirewallRuleSpec") -> bool:
        """Check if two rules are functionally equivalent (ignoring comment/priority/ids)."""
        return self._match_key() == other._match_key()


@dataclass
class ChainPolicies:
    """Chain default policies — backend-agnostic."""

    input: str = "drop"  # "accept" | "drop"
    output: str = "accept"  # "accept" | "drop"
    input_source_group_id: int | None = None
    input_source_group_name: str | None = None
    output_source_group_id: int | None = None
    output_source_group_name: str | None = None
