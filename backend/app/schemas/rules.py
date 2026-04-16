from pydantic import BaseModel, Field, field_validator, model_validator
from datetime import datetime
from typing import Optional
import ipaddress


def _validate_side(cidr: Optional[str], host_id: Optional[int], side: str) -> None:
    has_cidr = cidr is not None and cidr != ""
    has_host = host_id is not None
    if has_cidr and has_host:
        raise ValueError(f"{side} cannot set both CIDR and host reference")


class RuleCreate(BaseModel):
    action: str          # allow | deny | reject
    protocol: str        # tcp | udp | icmp | any
    direction: str       # input | output
    source_cidr: Optional[str] = None
    destination_cidr: Optional[str] = None
    source_host_id: Optional[int] = None
    destination_host_id: Optional[int] = None
    port_start: Optional[int] = None
    port_end: Optional[int] = None
    comment: Optional[str] = None
    priority: int = Field(default=0, ge=0, le=10000)

    @model_validator(mode="after")
    def _validate_sides(self):
        _validate_side(self.source_cidr, self.source_host_id, "source")
        _validate_side(self.destination_cidr, self.destination_host_id, "destination")
        return self

    @field_validator("action")
    @classmethod
    def validate_action(cls, v):
        if v not in ("allow", "deny", "reject"):
            raise ValueError("action must be allow, deny, or reject")
        return v

    @field_validator("protocol")
    @classmethod
    def validate_protocol(cls, v):
        if v not in ("tcp", "udp", "icmp", "any"):
            raise ValueError("protocol must be tcp, udp, icmp, or any")
        return v

    @field_validator("direction")
    @classmethod
    def validate_direction(cls, v):
        if v not in ("input", "output"):
            raise ValueError("direction must be input or output")
        return v

    @field_validator("source_cidr", "destination_cidr", mode="before")
    @classmethod
    def validate_cidr(cls, v):
        if v is None:
            return v
        try:
            ipaddress.ip_network(v, strict=False)
        except ValueError as e:
            raise ValueError(f"Invalid CIDR: {e}") from e
        return v

    @field_validator("port_start", "port_end", mode="before")
    @classmethod
    def validate_port(cls, v):
        if v is None:
            return v
        if not (1 <= v <= 65535):
            raise ValueError(f"Port {v} out of range (1-65535)")
        return v


class RuleUpdate(BaseModel):
    action: Optional[str] = None
    protocol: Optional[str] = None
    direction: Optional[str] = None
    source_cidr: Optional[str] = None
    destination_cidr: Optional[str] = None
    source_host_id: Optional[int] = None
    destination_host_id: Optional[int] = None
    port_start: Optional[int] = None
    port_end: Optional[int] = None
    comment: Optional[str] = None
    priority: Optional[int] = Field(default=None, ge=0, le=10000)

    @field_validator("action")
    @classmethod
    def validate_action(cls, v):
        if v is not None and v not in ("allow", "deny", "reject"):
            raise ValueError("action must be allow, deny, or reject")
        return v

    @field_validator("protocol")
    @classmethod
    def validate_protocol(cls, v):
        if v is not None and v not in ("tcp", "udp", "icmp", "any"):
            raise ValueError("protocol must be tcp, udp, icmp, or any")
        return v

    @field_validator("direction")
    @classmethod
    def validate_direction(cls, v):
        if v is not None and v not in ("input", "output"):
            raise ValueError("direction must be input or output")
        return v

    @field_validator("source_cidr", "destination_cidr", mode="before")
    @classmethod
    def validate_cidr(cls, v):
        if v is None:
            return v
        try:
            ipaddress.ip_network(v, strict=False)
        except ValueError as e:
            raise ValueError(f"Invalid CIDR: {e}") from e
        return v

    @field_validator("port_start", "port_end", mode="before")
    @classmethod
    def validate_port(cls, v):
        if v is None:
            return v
        if not (1 <= v <= 65535):
            raise ValueError(f"Port {v} out of range (1-65535)")
        return v


class RuleResponse(BaseModel):
    id: int
    group_id: int | None = None
    host_id: int | None = None
    action: str
    protocol: str
    direction: str
    source_cidr: Optional[str]
    destination_cidr: Optional[str]
    source_host_id: Optional[int] = None
    destination_host_id: Optional[int] = None
    port_start: Optional[int]
    port_end: Optional[int]
    comment: Optional[str]
    priority: int
    is_system: bool
    created_at: datetime
    updated_at: datetime
    model_config = {"from_attributes": True}


class RuleReorder(BaseModel):
    rule_ids: list[int]  # ordered list — index 0 = highest priority


class EffectiveRuleResponse(BaseModel):
    """Response for merged/effective rules (from FirewallRuleSpec)."""
    action: str
    protocol: str
    direction: str
    source_cidr: Optional[str]
    destination_cidr: Optional[str]
    source_host_id: Optional[int] = None
    destination_host_id: Optional[int] = None
    source_host_name: Optional[str] = None
    destination_host_name: Optional[str] = None
    port_start: Optional[int]
    port_end: Optional[int]
    comment: Optional[str]
    priority: int
    is_system: bool
    # Phase 1: group info
    group_id: Optional[int] = None
    group_name: Optional[str] = None
    rule_id: Optional[int] = None
    # Phase 2: group priority
    group_priority: Optional[int] = None
    # Phase 3: source tracking
    source: str = "group"  # "group" | "host" | "system"
    source_id: Optional[int] = None
    source_name: Optional[str] = None


class ChainPoliciesResponse(BaseModel):
    input: str   # "accept" | "drop"
    output: str  # "accept" | "drop"
    input_source_group_id: int | None = None
    input_source_group_name: str | None = None
    output_source_group_id: int | None = None
    output_source_group_name: str | None = None
