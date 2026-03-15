from pydantic import BaseModel, field_validator
from datetime import datetime
from typing import Optional
import ipaddress


class RuleCreate(BaseModel):
    action: str          # allow | deny | reject
    protocol: str        # tcp | udp | icmp | any
    direction: str       # input | output
    source_cidr: Optional[str] = None
    destination_cidr: Optional[str] = None
    port_start: Optional[int] = None
    port_end: Optional[int] = None
    comment: Optional[str] = None
    priority: int = 0

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
    port_start: Optional[int] = None
    port_end: Optional[int] = None
    comment: Optional[str] = None
    priority: Optional[int] = None


class RuleResponse(BaseModel):
    id: int
    group_id: int
    action: str
    protocol: str
    direction: str
    source_cidr: Optional[str]
    destination_cidr: Optional[str]
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
    port_start: Optional[int]
    port_end: Optional[int]
    comment: Optional[str]
    priority: int
    is_system: bool
