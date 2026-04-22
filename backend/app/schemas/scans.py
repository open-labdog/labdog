"""Pydantic v2 schemas for ScanConfig and PendingHost endpoints."""

import ipaddress
from datetime import datetime

from croniter import croniter
from pydantic import BaseModel, field_validator, model_validator

# Maximum scan throughput allowed at config creation/update time.
_MAX_IPS_PER_MINUTE = 100_000

# When a cron schedule is used we proxy a 60-minute interval for the rate check,
# because cron can fire at most once per minute.
_CRON_PROXY_INTERVAL = 60


def _validate_cidr(cidr: str) -> str:
    """Validate a single CIDR string. Raises ValueError on bad input."""
    try:
        ipaddress.ip_network(cidr, strict=False)
    except ValueError:
        raise ValueError(f"Invalid CIDR: {cidr!r}")
    return cidr


def _cidr_address_count(cidr: str) -> int:
    """Return the number of host addresses in a CIDR block."""
    net = ipaddress.ip_network(cidr, strict=False)
    prefix = net.prefixlen
    return 2 ** (32 - prefix)


def _check_rate_limit(cidrs: list[str], interval_minutes: int | None) -> None:
    """
    Raise ValueError when the effective scan footprint exceeds _MAX_IPS_PER_MINUTE.

    interval_minutes=None means a cron schedule — use _CRON_PROXY_INTERVAL as
    a rough worst-case proxy (fires every minute is the fastest cron can go).
    """
    total_addresses = sum(_cidr_address_count(c) for c in cidrs)
    effective_interval = interval_minutes if interval_minutes is not None else _CRON_PROXY_INTERVAL
    rate = total_addresses / effective_interval
    if rate > _MAX_IPS_PER_MINUTE:
        raise ValueError(
            f"Scan footprint too large: {total_addresses:,} addresses / {effective_interval} min"
            f" = {rate:,.0f} IPs/min, which exceeds the {_MAX_IPS_PER_MINUTE:,} IPs/min limit."
            " Reduce the address space or increase the interval."
        )


# ---------------------------------------------------------------------------
# ScanConfig schemas
# ---------------------------------------------------------------------------


class ScanConfigCreate(BaseModel):
    name: str
    cidrs: list[str]
    ssh_key_id: int
    ssh_port: int = 22
    default_group_ids: list[int] = []
    interval_minutes: int | None = None
    cron_expression: str | None = None
    enabled: bool = True
    auto_add: bool = False

    @field_validator("cidrs", mode="before")
    @classmethod
    def validate_cidrs(cls, v: list) -> list[str]:
        if not v:
            raise ValueError("At least one CIDR is required")
        return [_validate_cidr(str(cidr)) for cidr in v]

    @field_validator("interval_minutes")
    @classmethod
    def validate_interval(cls, v: int | None) -> int | None:
        if v is not None and not (1 <= v <= 10_080):
            raise ValueError("interval_minutes must be between 1 and 10080 (1 min to 1 week)")
        return v

    @field_validator("cron_expression")
    @classmethod
    def validate_cron(cls, v: str | None) -> str | None:
        if v is not None and not croniter.is_valid(v):
            raise ValueError(f"Invalid cron expression: {v!r}")
        return v

    @model_validator(mode="after")
    def check_schedule_exclusive(self) -> "ScanConfigCreate":
        has_interval = self.interval_minutes is not None
        has_cron = self.cron_expression is not None
        if has_interval == has_cron:
            raise ValueError(
                "Exactly one of interval_minutes or cron_expression must be set, "
                "not both or neither"
            )
        _check_rate_limit(self.cidrs, self.interval_minutes)
        return self


class ScanConfigUpdate(BaseModel):
    name: str | None = None
    cidrs: list[str] | None = None
    ssh_key_id: int | None = None
    ssh_port: int | None = None
    default_group_ids: list[int] | None = None
    interval_minutes: int | None = None
    cron_expression: str | None = None
    enabled: bool | None = None
    auto_add: bool | None = None

    # Sentinel to detect whether the field was explicitly passed
    _interval_set: bool = False
    _cron_set: bool = False

    @field_validator("cidrs", mode="before")
    @classmethod
    def validate_cidrs(cls, v: list | None) -> list[str] | None:
        if v is None:
            return None
        if not v:
            raise ValueError("At least one CIDR is required")
        return [_validate_cidr(str(cidr)) for cidr in v]

    @field_validator("interval_minutes")
    @classmethod
    def validate_interval(cls, v: int | None) -> int | None:
        if v is not None and not (1 <= v <= 10_080):
            raise ValueError("interval_minutes must be between 1 and 10080 (1 min to 1 week)")
        return v

    @field_validator("cron_expression")
    @classmethod
    def validate_cron(cls, v: str | None) -> str | None:
        if v is not None and not croniter.is_valid(v):
            raise ValueError(f"Invalid cron expression: {v!r}")
        return v

    @model_validator(mode="after")
    def check_schedule_and_rate_limit(self) -> "ScanConfigUpdate":
        fields_set = self.model_fields_set
        interval_provided = "interval_minutes" in fields_set
        cron_provided = "cron_expression" in fields_set

        # Only validate the XOR constraint when both schedule fields are explicitly provided.
        if interval_provided and cron_provided:
            has_interval = self.interval_minutes is not None
            has_cron = self.cron_expression is not None
            if has_interval and has_cron:
                raise ValueError(
                    "Exactly one of interval_minutes or cron_expression must be set, not both"
                )

        # Rate-limit check: only when CIDRs and/or schedule are being updated.
        if self.cidrs is not None:
            # Use whichever interval we have; None means cron proxy.
            _check_rate_limit(self.cidrs, self.interval_minutes)

        return self


class ScanConfigResponse(BaseModel):
    id: int
    name: str
    cidrs: list[str]
    ssh_key_id: int
    ssh_port: int
    default_group_ids: list[int]
    interval_minutes: int | None
    cron_expression: str | None
    enabled: bool
    auto_add: bool
    last_run_at: datetime | None
    last_run_status: str | None
    last_run_hosts_added: int
    last_run_hosts_pending: int
    last_run_error: str | None
    created_at: datetime
    updated_at: datetime
    # Populated by the detail endpoint only; None in list responses.
    pending_count: int | None = None

    model_config = {"from_attributes": True}


# ---------------------------------------------------------------------------
# PendingHost schemas
# ---------------------------------------------------------------------------


class PendingHostResponse(BaseModel):
    id: int
    scan_config_id: int
    ip_address: str
    hostname: str | None
    ssh_verified: bool
    ssh_error: str | None
    discovered_at: datetime

    model_config = {"from_attributes": True}


class PendingHostFleetResponse(BaseModel):
    """Fleet-wide pending host — includes the scan config name for display."""

    id: int
    scan_config_id: int
    scan_config_name: str
    ip_address: str
    hostname: str | None
    ssh_verified: bool
    ssh_error: str | None
    discovered_at: datetime


# ---------------------------------------------------------------------------
# Action body schemas
# ---------------------------------------------------------------------------


class ApproveBody(BaseModel):
    ids: list[int]


class DismissBody(BaseModel):
    ids: list[int]


class ApproveResponse(BaseModel):
    approved: int
    skipped: int
    skipped_ips: list[str]


class DismissResponse(BaseModel):
    dismissed: int


# ---------------------------------------------------------------------------
# Summary schema
# ---------------------------------------------------------------------------


class PendingSummaryResponse(BaseModel):
    total: int
