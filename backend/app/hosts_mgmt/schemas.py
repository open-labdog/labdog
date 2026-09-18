import ipaddress
import re
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator

HOSTNAME_RE = re.compile(
    r"^[a-zA-Z0-9]([a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?(\.[a-zA-Z0-9]([a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?)*$"
)

#: Characters that must never reach an ``/etc/hosts`` comment.
#:
#: The merge renders an entry as ``<ip> <hostname> <aliases>  # <comment>``
#: (see ``hosts_mgmt/merge.py``). ``hostname`` and ``aliases`` were checked
#: against HOSTNAME_RE; ``comment`` was not checked at all, so a newline in
#: it closed the line and started a real one. A comment of
#: ``"x\n1.2.3.4 deb.debian.org"`` appended a working /etc/hosts entry to
#: every host in the group — a package-mirror redirect, written by LabDog
#: itself and invisible in the UI, which renders the comment on one line
#: (SEC-24).
#:
#: NUL is included because it truncates the file at the C layer rather than
#: being written, and a carriage return alone is a line terminator to
#: enough parsers to be worth refusing.
_COMMENT_FORBIDDEN = re.compile(r"[\r\n\x00]")

#: An /etc/hosts line has no length limit worth relying on, but an
#: unbounded comment is a way to make the file unreadable rather than a
#: feature anyone wants.
_COMMENT_MAX = 128


def _validate_comment(v: str | None) -> str | None:
    """Reject anything in a comment that could end the line it sits on."""
    if v is None:
        return v
    if _COMMENT_FORBIDDEN.search(v):
        raise ValueError(
            "comment may not contain newlines or NUL — it is written into "
            "/etc/hosts on the line it annotates"
        )
    if len(v) > _COMMENT_MAX:
        raise ValueError(f"comment must be {_COMMENT_MAX} characters or less")
    return v


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

    @field_validator("comment")
    @classmethod
    def validate_comment(cls, v: str | None) -> str | None:
        return _validate_comment(v)


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

    @field_validator("comment")
    @classmethod
    def validate_comment(cls, v: str | None) -> str | None:
        return _validate_comment(v)


class HostsEntryResponse(BaseModel):
    id: int
    ip_address: str | None
    hostname: str | None
    host_ref_id: int | None = None
    aliases: list[str]
    comment: str | None
    priority: int
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
    #: Decides the entry's position in the rendered file, and with it
    #: which of two entries sharing a hostname resolves (BUG-57).
    #: ``/etc/hosts`` is read top to bottom and the first match for a
    #: name wins, so this is the one module where a per-entry priority
    #: has an ordering meaning rather than only a tie-break one. Zero
    #: for the injected system entries, which are pinned first anyway.
    priority: int = 0
    is_system: bool
    source: Literal["group", "host", "system"]
    source_id: int
    source_name: str
    model_config = {"from_attributes": True}
