from datetime import datetime

from pydantic import BaseModel


class GitRepoCreate(BaseModel):
    name: str
    url: str
    branch: str = "main"
    auth_type: str  # "ssh_key" | "https_token"
    ssh_key_id: int | None = None
    https_token: str | None = None  # plaintext on input, encrypted before storage
    webhook_secret: str | None = None


class GitRepoUpdate(BaseModel):
    name: str | None = None
    url: str | None = None
    branch: str | None = None
    auth_type: str | None = None
    ssh_key_id: int | None = None
    https_token: str | None = None
    webhook_secret: str | None = None


class GitRepoResponse(BaseModel):
    id: int
    name: str
    url: str
    branch: str
    auth_type: str
    ssh_key_id: int | None
    webhook_secret: str | None  # OK to return — it's for webhook validation, not a credential
    last_commit_sha: str | None
    last_sync_at: datetime | None
    created_at: datetime
    updated_at: datetime
    # NOTE: encrypted_https_token is NEVER included

    model_config = {"from_attributes": True}


class GitOpsEnableRequest(BaseModel):
    git_repository_id: int
    file_path: str  # e.g. "groups/web-servers.yaml"


class GitOpsStatusResponse(BaseModel):
    gitops_enabled: bool
    git_repository_id: int | None = None
    gitops_file_path: str | None = None
    gitops_status: str  # disconnected | synced | error | importing
    gitops_error_message: str | None = None
    gitops_last_import_at: datetime | None = None
