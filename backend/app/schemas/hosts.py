import ipaddress
from datetime import datetime

from pydantic import BaseModel, Field, field_validator

from app.hosts_mgmt.schemas import HOSTNAME_RE
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
        raise ValueError(f"ip_address '{v}' is not a valid IPv4 or IPv6 literal")
    if addr.is_loopback:
        raise ValueError(f"ip_address {v} is loopback; not a valid managed host")
    if addr.is_link_local:
        raise ValueError(f"ip_address {v} is link-local; not a valid managed host")
    if addr.is_unspecified:
        raise ValueError(f"ip_address {v} is unspecified (all-zeros); not a valid managed host")
    if addr.is_multicast:
        raise ValueError(f"ip_address {v} is multicast; not a valid managed host")
    return v


def _validate_managed_hostname(v: str | None) -> str | None:
    """Reject a hostname that could not be one.

    ``Host.hostname`` is not just a label: the hosts-file merge renders
    referenced hosts into ``/etc/hosts`` on every host in the group, so a
    hostname containing a newline appends a real line to that file — the
    same injection as the entry ``comment`` (SEC-24), reached through a
    different door. Nothing validated this field at all.
    """
    if v is None or v == "":
        return None
    if len(v) > 253:
        raise ValueError("hostname must be 253 characters or less")
    if not HOSTNAME_RE.match(v):
        raise ValueError(f"'{v}' is not a valid hostname (RFC 952/1123)")
    for label in v.split("."):
        if len(label) > 63:
            raise ValueError(f"hostname label '{label}' exceeds 63 characters")
    return v


class HostCreate(BaseModel):
    hostname: str | None = None
    ip_address: str
    # A port is 16 bits. Unbounded, this reached asyncssh and the Ansible
    # inventory as-is.
    ssh_port: int = Field(default=22, ge=1, le=65535)
    ssh_user: str = "root"
    ssh_key_id: int | None = None
    group_ids: list[int] = []
    #: Turn drift checking on for this host as it is created.
    #:
    #: Defaults to ``False`` so an existing API client keeps the behaviour
    #: it has. The *form* ticks it — the point of the change is to make
    #: drift checking a decision someone takes at the moment they are
    #: already deciding to manage the host, not a default flipped under
    #: everyone. See ``docs/ui/drift-detection.md``.
    drift_check_enabled: bool = False

    @field_validator("hostname")
    @classmethod
    def validate_hostname(cls, v: str | None) -> str | None:
        return _validate_managed_hostname(v)

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
    ssh_port: int | None = Field(default=None, ge=1, le=65535)
    ssh_user: str | None = None
    ssh_key_id: int | None = None
    firewall_backend: FirewallBackend | None = None
    group_ids: list[int] | None = None
    drift_check_enabled: bool | None = None

    @field_validator("hostname")
    @classmethod
    def validate_hostname(cls, v: str | None) -> str | None:
        return _validate_managed_hostname(v)

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
