from __future__ import annotations

from datetime import datetime
from typing import Any
from urllib.parse import urlparse

from cryptography import x509
from cryptography.hazmat.primitives import hashes
from pydantic import BaseModel, ConfigDict, field_validator

# Reject pathologically large PEM blobs before attempting to parse them.
_MAX_CA_CERT_BYTES = 64 * 1024


def _validate_proxmox_url(v: str) -> str:
    """Ensure api_url uses https and does not target private/loopback addresses."""
    parsed = urlparse(v)
    if parsed.scheme != "https":
        raise ValueError("Proxmox API URL must use https://")
    hostname = parsed.hostname or ""
    if hostname in ("localhost", "127.0.0.1", "::1", "0.0.0.0"):
        raise ValueError("Proxmox API URL must not target localhost")
    return v


def _validate_ca_cert_pem(v: str) -> str:
    """Validate and return a stripped PEM CA certificate.

    Accepts ANY parseable X.509 certificate (one or more ``BEGIN CERTIFICATE``
    blocks) — there is no ``CA:TRUE`` requirement, so a self-signed Proxmox
    node leaf cert works directly as its own trust anchor (the BUG-45
    scenario). Raises ``ValueError`` (→ 422) if the input exceeds the size
    cap or fails to parse as at least one certificate.

    Callers are responsible for short-circuiting blank input (the "leave
    unchanged" / "clear" sentinel) before invoking this.
    """
    cleaned = v.strip()
    if len(cleaned.encode("utf-8")) > _MAX_CA_CERT_BYTES:
        raise ValueError("CA certificate is too large (limit 64 KB)")
    try:
        certs = x509.load_pem_x509_certificates(cleaned.encode("utf-8"))
    except Exception as exc:
        raise ValueError(f"Failed to parse CA certificate: {exc}") from exc
    if not certs:
        raise ValueError("No X.509 certificate found in PEM content")
    return cleaned


def _ca_cert_fingerprint(pem: str) -> str:
    """SHA-256 fingerprint (lowercase hex) of the first/leaf cert in ``pem``."""
    cert = x509.load_pem_x509_certificates(pem.encode("utf-8"))[0]
    return cert.fingerprint(hashes.SHA256()).hex()


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
    ca_cert_pem: str | None = None

    @field_validator("ca_cert_pem")
    @classmethod
    def validate_ca_cert_pem(cls, v: str | None) -> str | None:
        # Blank/whitespace-only means "no CA" on create.
        if v is None or not v.strip():
            return None
        return _validate_ca_cert_pem(v)


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
    ca_cert_pem: str | None = None

    @field_validator("ca_cert_pem")
    @classmethod
    def validate_ca_cert_pem(cls, v: str | None) -> str | None:
        # Tri-state on update: None (omitted) = leave unchanged; blank string
        # = clear sentinel (let through untouched); non-blank = validate.
        if v is None or not v.strip():
            return v
        return _validate_ca_cert_pem(v)


class ProxmoxNodeResponse(BaseModel):
    id: int
    name: str
    api_url: str
    token_id: str
    verify_ssl: bool
    has_ca_cert: bool
    ca_cert_fingerprint: str | None = None
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


def to_response(node: Any) -> ProxmoxNodeResponse:
    """Build a :class:`ProxmoxNodeResponse` with derived CA metadata.

    ``from_attributes`` cannot synthesize ``has_ca_cert`` /
    ``ca_cert_fingerprint``, and the raw PEM is never exposed — only its
    presence and SHA-256 fingerprint.
    """
    pem = node.ca_cert_pem
    return ProxmoxNodeResponse(
        id=node.id,
        name=node.name,
        api_url=node.api_url,
        token_id=node.token_id,
        verify_ssl=node.verify_ssl,
        has_ca_cert=pem is not None,
        ca_cert_fingerprint=_ca_cert_fingerprint(pem) if pem else None,
        created_at=node.created_at,
        updated_at=node.updated_at,
    )


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
