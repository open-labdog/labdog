import ipaddress
import re
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator

HOSTNAME_RE = re.compile(
    r"^[a-zA-Z0-9]([a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?(\.[a-zA-Z0-9]([a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?)*$"
)


class HostsEntryCreate(BaseModel):
    ip_address: str | None = None
    hostname: str | None = None
    host_ref_id: int | None = None
    aliases: list[str] = []
    comment: str | None = None
    priority: int = Field(default=0, ge=0, le=10000)

    @model_validator(mode="after")
    def _validate_ref_or_literal(self):
        if self.host_ref_id is not None:
            if self.ip_address or self.hostname:
                raise ValueError("ip_address and hostname must be empty when host_ref_id is set")
        else:
            if not self.ip_address or not self.hostname:
                raise ValueError("ip_address and hostname are required when host_ref_id is not set")
        return self

    @field_validator("ip_address")
    @classmethod
    def validate_ip(cls, v: str | None) -> str | None:
        if v is None or v == "":
            return None
        try:
            ipaddress.ip_address(v)
        except ValueError:
            raise ValueError(f"'{v}' is not a valid IPv4 or IPv6 address")
        return v

    @field_validator("hostname")
    @classmethod
    def validate_hostname(cls, v: str | None) -> str | None:
        if v is None or v == "":
            return None
        if len(v) > 253:
            raise ValueError("Hostname must be 253 characters or less")
        if not HOSTNAME_RE.match(v):
            raise ValueError(f"'{v}' is not a valid hostname (RFC 952/1123)")
        # Check each label is max 63 chars
        for label in v.split("."):
            if len(label) > 63:
                raise ValueError(f"Hostname label '{label}' exceeds 63 characters")
        return v

    @field_validator("aliases")
    @classmethod
    def validate_aliases(cls, v: list[str]) -> list[str]:
        for alias in v:
            if len(alias) > 253 or not HOSTNAME_RE.match(alias):
                raise ValueError(f"'{alias}' is not a valid hostname")
        return v


class HostsEntryUpdate(BaseModel):
    ip_address: str | None = None
    hostname: str | None = None
    host_ref_id: int | None = None
    aliases: list[str] | None = None
    comment: str | None = None
    priority: int | None = Field(default=None, ge=0, le=10000)

    @field_validator("ip_address")
    @classmethod
    def validate_ip(cls, v: str | None) -> str | None:
        if v is None:
            return v
        try:
            ipaddress.ip_address(v)
        except ValueError:
            raise ValueError(f"'{v}' is not a valid IPv4 or IPv6 address")
        return v

    @field_validator("hostname")
    @classmethod
    def validate_hostname(cls, v: str | None) -> str | None:
        if v is None:
            return v
        if len(v) > 253:
            raise ValueError("Hostname must be 253 characters or less")
        if not HOSTNAME_RE.match(v):
            raise ValueError(f"'{v}' is not a valid hostname")
        for label in v.split("."):
            if len(label) > 63:
                raise ValueError(f"Label '{label}' exceeds 63 characters")
        return v

    @field_validator("aliases")
    @classmethod
    def validate_aliases(cls, v: list[str] | None) -> list[str] | None:
        if v is None:
            return v
        for alias in v:
            if len(alias) > 253 or not HOSTNAME_RE.match(alias):
                raise ValueError(f"'{alias}' is not a valid hostname")
        return v


class HostsEntryResponse(BaseModel):
    id: int
    ip_address: str | None
    hostname: str | None
    host_ref_id: int | None = None
    aliases: list[str]
    comment: str | None
    priority: int
    is_system: bool
    group_id: int | None
    host_id: int | None
    created_at: datetime
    updated_at: datetime
    model_config = {"from_attributes": True}


class EffectiveHostsEntryResponse(BaseModel):
    ip_address: str
    hostname: str
    aliases: list[str]
    comment: str | None
    is_system: bool
    source: Literal["group", "host", "system"]
    source_id: int
    source_name: str
    model_config = {"from_attributes": True}
