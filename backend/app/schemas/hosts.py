import ipaddress
from datetime import datetime

from pydantic import BaseModel, field_validator

from app.models.host import FirewallBackend, SyncStatus
from app.schemas._shared import validate_linux_username


def _validate_ip_address(v: str) -> str:
    """Parse *v* as an IPv4 or IPv6 literal and reject special-use ranges.

    Raises:
        ValueError: for non-IP strings, loopback, link-local, unspecified,
            or multicast addresses.
    """
    try:
        addr = ipaddress.ip_address(v)
    except ValueError:
        raise ValueError(
            f"ip_address '{v}' is not a valid IPv4 or IPv6 literal"
        )
    if addr.is_loopback:
        raise ValueError(
            f"ip_address {v} is loopback; not a valid managed host"
        )
    if addr.is_link_local:
        raise ValueError(
            f"ip_address {v} is link-local; not a valid managed host"
        )
    if addr.is_unspecified:
        raise ValueError(
            f"ip_address {v} is unspecified (all-zeros); not a valid managed host"
        )
    if addr.is_multicast:
        raise ValueError(
            f"ip_address {v} is multicast; not a valid managed host"
        )
    return v


class HostCreate(BaseModel):
    hostname: str | None = None
    ip_address: str
    ssh_port: int = 22
    ssh_user: str = "root"
    ssh_key_id: int | None = None
    group_ids: list[int] = []

    @field_validator("ip_address")
    @classmethod
    def validate_ip_address(cls, v: str) -> str:
        return _validate_ip_address(v)

    @field_validator("ssh_user")
    @classmethod
    def validate_ssh_user(cls, v: str) -> str:
        return validate_linux_username(v)


class HostUpdate(BaseModel):
    hostname: str | None = None
    ip_address: str | None = None
    ssh_port: int | None = None
    ssh_user: str | None = None
    ssh_key_id: int | None = None
    firewall_backend: FirewallBackend | None = None
    group_ids: list[int] | None = None
    drift_check_enabled: bool | None = None

    @field_validator("ip_address")
    @classmethod
    def validate_ip_address(cls, v: str | None) -> str | None:
        if v is None:
            return v
        return _validate_ip_address(v)

    @field_validator("ssh_user")
    @classmethod
    def validate_ssh_user(cls, v: str | None) -> str | None:
        if v is None:
            return v
        return validate_linux_username(v)


class HostResponse(BaseModel):
    id: int
    hostname: str
    ip_address: str
    ssh_port: int
    ssh_user: str
    firewall_backend: FirewallBackend
    sync_status: SyncStatus
    labdog_source_ip: str | None
    drift_check_enabled: bool
    last_sync_at: datetime | None
    last_drift_check_at: datetime | None
    ssh_key_id: int | None
    os_codename: str | None
    os_pretty_name: str | None
    os_family: str | None
    default_nic: str | None
    kernel_version: str | None
    kernel_release: str | None
    os_facts_collected_at: datetime | None
    ssh_host_key_entry: str | None
    group_ids: list[int] = []
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}
