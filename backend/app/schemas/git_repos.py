import ipaddress
import re
from datetime import datetime
from urllib.parse import urlparse

from pydantic import BaseModel, field_validator

# Recognised git URL prefixes. SSH-style URLs (``git@host:owner/repo``
# and ``ssh://``) require an SSH key; ``https://`` URLs are public if
# no token is supplied and authenticated otherwise.
SSH_URL_PREFIXES = ("git@", "ssh://")
HTTPS_URL_PREFIX = "https://"

# Networks that must never be reachable via a user-supplied git URL.
# RFC1918 private ranges (10/8, 172.16/12, 192.168/16) are intentionally
# absent — labdog's primary use-case is managing hosts on the operator's
# internal LAN and operators may run a git server there.
_BLOCKED_URL_NETWORKS = [
    ipaddress.ip_network("127.0.0.0/8"),  # loopback
    ipaddress.ip_network("::1/128"),  # IPv6 loopback
    ipaddress.ip_network("0.0.0.0/8"),  # this-host
    ipaddress.ip_network("::/128"),  # IPv6 unspecified
    ipaddress.ip_network("169.254.0.0/16"),  # link-local / cloud metadata
    ipaddress.ip_network("fe80::/10"),  # IPv6 link-local
]

# scp-style SSH URL  →  git@<host>:<path>
_SCP_HOST_RE = re.compile(r"^git@([^:]+):")


def _extract_hostname(url: str) -> str | None:
    """Return the hostname from a git URL, or None if it cannot be determined.

    Handles three forms:
    - ``https://host/path``
    - ``ssh://[user@]host[:port]/path``
    - ``git@host:path``  (scp-style — urllib.parse does not handle this)
    """
    if url.startswith("git@"):
        m = _SCP_HOST_RE.match(url)
        return m.group(1) if m else None
    parsed = urlparse(url)
    # urlparse strips brackets from IPv6 addresses; .hostname lower-cases the result.
    return parsed.hostname or None


def _check_host_blocked(host: str) -> None:
    """Raise ``ValueError`` if *host* resolves to a blocked network range.

    String-checks only — no DNS resolution is performed (DNS rebinding is
    out of scope; the operator is superuser-only).
    """
    if host.lower() == "localhost":
        raise ValueError(
            f"Repository host '{host}' is blocked: loopback addresses are not permitted"
        )

    try:
        addr = ipaddress.ip_address(host)
    except ValueError:
        # Not a numeric IP address — DNS name, leave it alone.
        return

    for network in _BLOCKED_URL_NETWORKS:
        if addr in network:
            raise ValueError(
                f"Repository host '{host}' is blocked: address falls in {network} "
                "(loopback / link-local / cloud-metadata ranges are not permitted)"
            )


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
    def validate_url(cls, v: str) -> str:
        if not (v.startswith(HTTPS_URL_PREFIX) or v.startswith(SSH_URL_PREFIXES)):
            raise ValueError("Repository URL must use https://, ssh://, or git@ scheme")
        host = _extract_hostname(v)
        if host is None:
            raise ValueError("Repository URL does not contain a recognisable hostname")
        _check_host_blocked(host)
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
