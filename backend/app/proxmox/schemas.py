from datetime import datetime
from urllib.parse import urlparse

from pydantic import BaseModel, ConfigDict, field_validator


def _validate_proxmox_url(v: str) -> str:
    """Ensure api_url uses https and does not target private/loopback addresses."""
    parsed = urlparse(v)
    if parsed.scheme != "https":
        raise ValueError("Proxmox API URL must use https://")
    hostname = parsed.hostname or ""
    if hostname in ("localhost", "127.0.0.1", "::1", "0.0.0.0"):
        raise ValueError("Proxmox API URL must not target localhost")
    return v


class ProxmoxNodeCreate(BaseModel):
    name: str
    api_url: str

    @field_validator("api_url")
    @classmethod
    def validate_api_url(cls, v: str) -> str:
        return _validate_proxmox_url(v)

    token_id: str
    token_secret: str
    verify_ssl: bool = True


class ProxmoxNodeUpdate(BaseModel):
    name: str | None = None
    api_url: str | None = None

    @field_validator("api_url")
    @classmethod
    def validate_api_url(cls, v: str | None) -> str | None:
        if v is not None:
            return _validate_proxmox_url(v)
        return v

    token_id: str | None = None
    token_secret: str | None = None
    verify_ssl: bool | None = None


class ProxmoxNodeResponse(BaseModel):
    id: int
    name: str
    api_url: str
    token_id: str
    verify_ssl: bool
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class ProxmoxTestResponse(BaseModel):
    success: bool
    message: str
    version: str | None = None


class VMMappingResponse(BaseModel):
    id: int
    host_id: int
    proxmox_node_id: int
    pve_node_name: str
    vmid: int
    vm_name: str
    vm_type: str
    discovered_at: datetime

    model_config = ConfigDict(from_attributes=True)
