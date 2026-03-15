from dataclasses import dataclass
from typing import Optional


@dataclass
class FirewallRuleSpec:
    """Canonical rule representation — backend-agnostic."""

    action: str  # "allow" | "deny" | "reject"
    protocol: str  # "tcp" | "udp" | "icmp" | "any"
    direction: str  # "input" | "output"
    source_cidr: Optional[str] = None  # IPv4 or IPv6 CIDR
    destination_cidr: Optional[str] = None
    port_start: Optional[int] = None  # single port or range start
    port_end: Optional[int] = None  # range end (None = single port)
    comment: Optional[str] = None
    is_system: bool = False  # True = auto-injected, non-deletable
    priority: int = 0  # ordering within group
    group_id: Optional[int] = None  # source group (for merge tracking)
    rule_id: Optional[int] = None  # DB id (for update tracking)

    def port_display(self) -> str:
        if self.port_start is None:
            return "any"
        if self.port_end and self.port_end != self.port_start:
            return f"{self.port_start}-{self.port_end}"
        return str(self.port_start)

    def matches(self, other: "FirewallRuleSpec") -> bool:
        """Check if two rules are functionally equivalent (ignoring comment/priority/ids)."""
        return (
            self.action == other.action
            and self.protocol == other.protocol
            and self.direction == other.direction
            and self.source_cidr == other.source_cidr
            and self.destination_cidr == other.destination_cidr
            and self.port_start == other.port_start
            and self.port_end == other.port_end
        )
