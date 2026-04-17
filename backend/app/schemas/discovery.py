from pydantic import BaseModel

from app.schemas.hosts import HostResponse


class ScanRequest(BaseModel):
    cidr: str  # e.g. "10.0.0.0/24"
    port: int = 22
    timeout: float = 1.0  # per-host timeout in seconds


class DiscoveredHost(BaseModel):
    ip: str
    hostname: str | None = None  # reverse DNS result, None if lookup failed
    ssh_status: str = "open"  # "open" or "refused"


class ScanStatus(BaseModel):
    job_id: str
    status: str  # "pending" | "running" | "done" | "error"
    progress: int = 0  # hosts scanned so far
    total: int = 0  # total hosts to scan
    hosts_found: list[DiscoveredHost] = []
    error: str | None = None


class BulkAddRequest(BaseModel):
    ips: list[str]  # IPs to add as hosts
    ssh_key_id: int  # SSH key to assign to all
    group_ids: list[int] = []  # optional groups to assign
    ssh_port: int = 22


class FailedHost(BaseModel):
    ip: str
    error: str


class BulkAddResponse(BaseModel):
    added: int
    skipped: int  # already existed (race condition safety)
    failed: list[FailedHost] = []  # hosts that failed SSH verification
    hosts: list[HostResponse]  # created host details
