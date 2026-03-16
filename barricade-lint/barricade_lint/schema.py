"""Standalone Pydantic models for Barricade YAML firewall rules.

These are a standalone copy of backend/app/gitops/schema.py — no backend imports.
"""

from typing import Literal, Optional, Union

from pydantic import BaseModel, ConfigDict


class FirewallRuleYAML(BaseModel):
    action: Literal["allow", "deny", "reject"]
    protocol: Literal["tcp", "udp", "icmp", "any"]
    direction: Literal["input", "output"]
    source: Optional[str] = None  # CIDR (IPv4 or IPv6)
    dest: Optional[str] = None  # CIDR
    port: Optional[Union[int, str]] = None  # int for single, "start-end" string for range
    comment: Optional[str] = None
    system: Optional[bool] = None  # Read but IGNORED on import


class FirewallModuleYAML(BaseModel):
    rules: list[FirewallRuleYAML] = []


class BarricadeGroupYAML(BaseModel):
    group: str  # Human-readable name
    priority: Optional[int] = None  # Informational
    firewall: Optional[FirewallModuleYAML] = None
    model_config = ConfigDict(extra="allow")  # Ignore unknown top-level keys
