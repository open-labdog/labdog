from datetime import datetime

from pydantic import BaseModel

from app.models.host import FirewallBackend, SyncStatus


class HostCreate(BaseModel):
    hostname: str | None = None
    ip_address: str
    ssh_port: int = 22
    ssh_user: str = "root"
    ssh_key_id: int | None = None
    group_ids: list[int] = []


class HostUpdate(BaseModel):
    hostname: str | None = None
    ip_address: str | None = None
    ssh_port: int | None = None
    ssh_user: str | None = None
    ssh_key_id: int | None = None
    firewall_backend: FirewallBackend | None = None
    group_ids: list[int] | None = None


class HostResponse(BaseModel):
    id: int
    hostname: str
    ip_address: str
    ssh_port: int
    ssh_user: str
    firewall_backend: FirewallBackend
    sync_status: SyncStatus
    drift_check_enabled: bool
    last_sync_at: datetime | None
    last_drift_check_at: datetime | None
    ssh_key_id: int | None
    group_ids: list[int] = []
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}
