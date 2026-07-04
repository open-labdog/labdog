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

    def _conflict_key(self) -> tuple:
        """Hashable key identifying the same match target, ignoring action.

        Used by ``merge_group_rules`` for priority-based conflict resolution
        (on the same match the higher-priority group's action wins). This runs
        *before* host refs are materialized into CIDRs, so it keys on the
        ``source_host_id`` / ``destination_host_id`` FKs to tell two distinct
        unresolved host refs apart (both would have ``source_cidr is None`` at
        merge time). Shares CIDR/port_end normalization with ``_match_key``.
        """
        return (
            self.protocol,
            self.direction,
            _normalize_cidr(self.source_cidr),
            _normalize_cidr(self.destination_cidr),
            self.source_host_id,
            self.destination_host_id,
            self.port_start,
            _normalize_port_end(self.port_start, self.port_end),
        )

    def _match_key(self) -> tuple:
        """Return a hashable key for functional equivalence comparison.

        Used by the diff engine, which runs *after* ``resolve_host_refs`` has
        materialized every host ref into a concrete /32 (or /128) CIDR. At that
        point the ``source_host_id`` / ``destination_host_id`` FKs are redundant
        with the resolved CIDR and must be **excluded**: a rule that referenced
        a host and a literal-CIDR rule that resolve to the same address are the
        same rule. The on-host state parsed back over SSH only ever carries the
        CIDR (it has no way to know a labdog host FK), so keying on the FK here
        would spuriously diff every host-ref rule as a remove + re-add.
        """
        return (
            self.action,
            self.protocol,
            self.direction,
            _normalize_cidr(self.source_cidr),
            _normalize_cidr(self.destination_cidr),
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
