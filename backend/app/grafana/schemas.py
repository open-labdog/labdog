from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from urllib.parse import urlparse

from pydantic import BaseModel, ConfigDict, field_validator

# Reuse the Proxmox CA-cert helpers — identical PEM handling.
from app.proxmox.schemas import _ca_cert_fingerprint, _validate_ca_cert_pem

Kind = Literal["mimir", "loki"]
AuthType = Literal["none", "bearer", "basic"]


def _validate_http_url(v: str) -> str:
    """Require a http(s) URL with a host. Unlike Proxmox we allow plain
    http and loopback/private hosts — a homelab Mimir/Loki commonly lives
    at ``http://mimir:9009`` on the same Docker network."""
    parsed = urlparse(v)
    if parsed.scheme not in ("http", "https"):
        raise ValueError("URL must start with http:// or https://")
    if not parsed.hostname:
        raise ValueError("URL must include a host")
    return v.rstrip("/")


def derive_query_url(url: str, kind: str) -> str:
    """Derive the query base from the operator-entered ingest URL.

    The operator enters a single URL (the remote-write / push URL, possibly
    with a path like ``/api/v1/push``). For querying we strip the path down
    to the host and append the kind's API prefix:

    * mimir → ``<scheme>://<host>/prometheus`` (query client appends
      ``/api/v1/query`` → ``/prometheus/api/v1/query``)
    * loki  → ``<scheme>://<host>/loki``
    """
    parsed = urlparse(url)
    base = f"{parsed.scheme}://{parsed.netloc}"
    return f"{base}/loki" if kind == "loki" else f"{base}/prometheus"


class GrafanaInstanceCreate(BaseModel):
    name: str
    kind: Kind
    url: str
    org_id: str | None = None
    auth_type: AuthType = "none"
    username: str | None = None
    #: The auth secret — bearer token (auth_type="bearer") or password
    #: (auth_type="basic"). Ignored when auth_type="none".
    token: str | None = None
    verify_ssl: bool = True
    ca_cert_pem: str | None = None
    is_default: bool = False

    @field_validator("url")
    @classmethod
    def _url(cls, v: str) -> str:
        return _validate_http_url(v)

    @field_validator("ca_cert_pem")
    @classmethod
    def _ca(cls, v: str | None) -> str | None:
        if v is None or not v.strip():
            return None
        return _validate_ca_cert_pem(v)


class GrafanaInstanceUpdate(BaseModel):
    name: str | None = None
    kind: Kind | None = None
    url: str | None = None
    org_id: str | None = None
    auth_type: AuthType | None = None
    username: str | None = None
    token: str | None = None
    verify_ssl: bool | None = None
    ca_cert_pem: str | None = None
    is_default: bool | None = None

    @field_validator("url")
    @classmethod
    def _url(cls, v: str | None) -> str | None:
        if v is None:
            return v
        return _validate_http_url(v)

    @field_validator("ca_cert_pem")
    @classmethod
    def _ca(cls, v: str | None) -> str | None:
        # Tri-state: None = leave unchanged; blank = clear; else validate.
        if v is None or not v.strip():
            return v
        return _validate_ca_cert_pem(v)


class GrafanaInstanceResponse(BaseModel):
    id: int
    name: str
    kind: str
    url: str
    #: Derived, read-only — the base LabDog will query (host + kind prefix).
    query_url: str
    org_id: str | None
    auth_type: str
    username: str | None
    #: True when an auth secret (bearer token / basic password) is stored.
    has_token: bool
    verify_ssl: bool
    has_ca_cert: bool
    ca_cert_fingerprint: str | None = None
    is_default: bool
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


def to_response(inst: Any) -> GrafanaInstanceResponse:
    """Build a response with derived query URL + secret/CA metadata. The raw
    token and PEM are never exposed — only their presence (and CA fingerprint)."""
    pem = inst.ca_cert_pem
    return GrafanaInstanceResponse(
        id=inst.id,
        name=inst.name,
        kind=inst.kind,
        url=inst.url,
        query_url=derive_query_url(inst.url, inst.kind),
        org_id=inst.org_id,
        auth_type=inst.auth_type,
        username=inst.username,
        has_token=inst.encrypted_token is not None,
        verify_ssl=inst.verify_ssl,
        has_ca_cert=pem is not None,
        ca_cert_fingerprint=_ca_cert_fingerprint(pem) if pem else None,
        is_default=inst.is_default,
        created_at=inst.created_at,
        updated_at=inst.updated_at,
    )


class GrafanaTestResponse(BaseModel):
    success: bool
    message: str


# ---------------------------------------------------------------------------
# Host metrics (instant) — the host-page data contract
# ---------------------------------------------------------------------------


class MetricValue(BaseModel):
    percent: float
    used: float | None = None
    total: float | None = None
    unit: str | None = None


class HostMetrics(BaseModel):
    #: False when no Mimir instance is registered → the UI shows the
    #: "set up metrics" CTA rather than an empty/error state.
    configured: bool
    #: ISO timestamp of the freshest sample, or None when no data.
    sampled_at: datetime | None = None
    cpu: MetricValue | None = None
    memory: MetricValue | None = None
    disk: MetricValue | None = None
    #: Populated when a query failed (vs. simply having no data yet).
    error: str | None = None
