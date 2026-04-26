from datetime import datetime

from pydantic import BaseModel, field_validator

# Recognised git URL prefixes. SSH-style URLs (``git@host:owner/repo``
# and ``ssh://``) require an SSH key; ``https://`` URLs are public if
# no token is supplied and authenticated otherwise.
SSH_URL_PREFIXES = ("git@", "ssh://")
HTTPS_URL_PREFIX = "https://"


def derive_auth_type(
    url: str,
    ssh_key_id: int | None,
    https_token: str | None,
) -> str:
    """Pick the storage ``auth_type`` for a (URL, creds) tuple.

    Pure function so the API and seed code agree on the same rule:
    SSH URLs always use ``ssh_key`` and require ``ssh_key_id``;
    HTTPS URLs with a token use ``https_token``; HTTPS URLs with no
    token are treated as public (``none``).
    """
    if url.startswith(SSH_URL_PREFIXES):
        if not ssh_key_id:
            raise ValueError("SSH URLs require an SSH key")
        return "ssh_key"
    if url.startswith(HTTPS_URL_PREFIX):
        return "https_token" if https_token else "none"
    raise ValueError("Repository URL must use https://, ssh://, or git@ scheme")


class GitRepoCreate(BaseModel):
    name: str
    url: str
    branch: str = "main"
    ssh_key_id: int | None = None
    https_token: str | None = None  # plaintext on input, encrypted before storage
    webhook_secret: str | None = None

    @field_validator("url")
    @classmethod
    def validate_url_scheme(cls, v: str) -> str:
        if not (v.startswith(HTTPS_URL_PREFIX) or v.startswith(SSH_URL_PREFIXES)):
            raise ValueError("Repository URL must use https://, ssh://, or git@ scheme")
        return v


class GitRepoUpdate(BaseModel):
    name: str | None = None
    url: str | None = None
    branch: str | None = None
    ssh_key_id: int | None = None
    https_token: str | None = None
    webhook_secret: str | None = None


class GitRepoResponse(BaseModel):
    id: int
    name: str
    url: str
    branch: str
    auth_type: str  # derived server-side; reported back for UX cues
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
