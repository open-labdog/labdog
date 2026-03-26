from datetime import datetime

from pydantic import BaseModel, ConfigDict


class ProxmoxNodeCreate(BaseModel):
    name: str
    api_url: str
    token_id: str
    token_secret: str
    verify_ssl: bool = True


class ProxmoxNodeUpdate(BaseModel):
    name: str | None = None
    api_url: str | None = None
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
    discovered_at: datetime

    model_config = ConfigDict(from_attributes=True)
