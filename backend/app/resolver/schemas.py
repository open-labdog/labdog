import ipaddress
import re
from typing import Literal

from pydantic import BaseModel, field_validator, model_validator

ALLOWED_OPTIONS = {"ndots", "timeout", "attempts", "rotate", "edns0"}
_DNS_LABEL_RE = re.compile(r"^[a-zA-Z0-9]([a-zA-Z0-9\-]{0,61}[a-zA-Z0-9])?$")


def _validate_ip(v: str) -> str:
    try:
        ipaddress.ip_address(v)
    except ValueError:
        raise ValueError(f"Invalid IP address: {v}")
    return v


def _validate_dns_name(v: str) -> str:
    if len(v) > 253:
        raise ValueError(f"Domain name too long: {v}")
    labels = v.rstrip(".").split(".")
    for label in labels:
        if not _DNS_LABEL_RE.match(label):
            raise ValueError(f"Invalid DNS label '{label}' in domain '{v}'")
    return v


class ResolverConfigCreate(BaseModel):
    nameservers: list[str]
    search_domains: list[str] = []
    options: dict[str, int | str] = {}
    resolver_type: Literal["resolv_conf", "systemd_resolved", "networkmanager"] = "resolv_conf"
    dns_over_tls: bool = False

    @field_validator("nameservers")
    @classmethod
    def validate_nameservers(cls, v: list[str]) -> list[str]:
        if not v:
            raise ValueError("At least one nameserver is required")
        if len(v) > 3:
            raise ValueError("Maximum 3 nameservers allowed (resolv.conf limit)")
        return [_validate_ip(ns) for ns in v]

    @field_validator("search_domains")
    @classmethod
    def validate_search_domains(cls, v: list[str]) -> list[str]:
        if len(v) > 6:
            raise ValueError("Maximum 6 search domains allowed (resolv.conf limit)")
        return [_validate_dns_name(d) for d in v]

    @field_validator("options")
    @classmethod
    def validate_options(cls, v: dict[str, int | str]) -> dict[str, int | str]:
        for key, val in v.items():
            if key not in ALLOWED_OPTIONS:
                raise ValueError(
                    f"Unknown option '{key}'. Allowed: {', '.join(sorted(ALLOWED_OPTIONS))}"
                )
            if key in ("ndots", "timeout", "attempts"):
                if not isinstance(val, int) or val < 0 or val > 15:
                    raise ValueError(f"Option '{key}' must be int 0-15, got {val}")
        return v

    @model_validator(mode="after")
    def check_dns_over_tls(self):
        if self.dns_over_tls and self.resolver_type != "systemd_resolved":
            self.dns_over_tls = False  # silently ignore for non-systemd-resolved
        return self


class ResolverConfigUpdate(BaseModel):
    nameservers: list[str] | None = None
    search_domains: list[str] | None = None
    options: dict[str, int | str] | None = None
    resolver_type: Literal["resolv_conf", "systemd_resolved", "networkmanager"] | None = None
    dns_over_tls: bool | None = None

    @field_validator("nameservers")
    @classmethod
    def validate_nameservers(cls, v: list[str] | None) -> list[str] | None:
        if v is not None:
            if not v:
                raise ValueError("At least one nameserver is required")
            if len(v) > 3:
                raise ValueError("Maximum 3 nameservers allowed")
            return [_validate_ip(ns) for ns in v]
        return v

    @field_validator("search_domains")
    @classmethod
    def validate_search_domains(cls, v: list[str] | None) -> list[str] | None:
        if v is not None:
            if len(v) > 6:
                raise ValueError("Maximum 6 search domains allowed")
            return [_validate_dns_name(d) for d in v]
        return v

    @field_validator("options")
    @classmethod
    def validate_options(cls, v: dict[str, int | str] | None) -> dict[str, int | str] | None:
        if v is not None:
            for key, val in v.items():
                if key not in ALLOWED_OPTIONS:
                    raise ValueError(f"Unknown option '{key}'")
                if key in ("ndots", "timeout", "attempts"):
                    if not isinstance(val, int) or val < 0 or val > 15:
                        raise ValueError(f"Option '{key}' must be int 0-15")
            return v
        return v


class ResolverConfigResponse(BaseModel):
    model_config = {"from_attributes": True}

    id: int
    group_id: int | None = None
    host_id: int | None = None
    nameservers: list[str]
    search_domains: list[str]
    options: dict[str, int | str]
    resolver_type: str
    dns_over_tls: bool


class EffectiveResolverResponse(BaseModel):
    nameservers: list[str]
    search_domains: list[str]
    options: dict[str, int | str]
    resolver_type: str
    dns_over_tls: bool
    source: Literal["group", "host"]
    source_id: int
    source_name: str
