from typing import Literal

from pydantic import BaseModel, ConfigDict


class FirewallRuleYAML(BaseModel):
    action: Literal["allow", "deny", "reject"]
    protocol: Literal["tcp", "udp", "icmp", "any"]
    direction: Literal["input", "output"]
    source: str | None = None  # CIDR (IPv4 or IPv6)
    dest: str | None = None  # CIDR
    port: int | str | None = None  # int for single, "start-end" string for range
    comment: str | None = None
    system: bool | None = None  # Read but IGNORED on import


class FirewallModuleYAML(BaseModel):
    rules: list[FirewallRuleYAML] = []


class BarricadeGroupYAML(BaseModel):
    group: str  # Human-readable name
    priority: int | None = None  # Informational
    firewall: FirewallModuleYAML | None = None
    model_config = ConfigDict(extra="allow")  # Ignore unknown top-level keys
