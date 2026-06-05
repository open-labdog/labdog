from __future__ import annotations

from datetime import datetime
from typing import Any
from urllib.parse import urlparse

from pydantic import BaseModel, ConfigDict, field_validator

# Reuse the Proxmox CA-cert helpers — identical PEM handling.
from app.proxmox.schemas import _ca_cert_fingerprint, _validate_ca_cert_pem


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


class GrafanaInstanceCreate(BaseModel):
    name: str
    prometheus_query_url: str
    prometheus_push_url: str
    loki_push_url: str | None = None
    org_id: str | None = None
    token: str | None = None
    verify_ssl: bool = True
    ca_cert_pem: str | None = None
    is_default: bool = False

    @field_validator("prometheus_query_url", "prometheus_push_url")
    @classmethod
    def _req_url(cls, v: str) -> str:
        return _validate_http_url(v)

    @field_validator("loki_push_url")
    @classmethod
    def _opt_url(cls, v: str | None) -> str | None:
        if v is None or not v.strip():
            return None
        return _validate_http_url(v)

    @field_validator("ca_cert_pem")
    @classmethod
    def _ca(cls, v: str | None) -> str | None:
        if v is None or not v.strip():
            return None
        return _validate_ca_cert_pem(v)


class GrafanaInstanceUpdate(BaseModel):
    name: str | None = None
    prometheus_query_url: str | None = None
    prometheus_push_url: str | None = None
    loki_push_url: str | None = None
    org_id: str | None = None
    token: str | None = None
    verify_ssl: bool | None = None
    ca_cert_pem: str | None = None
    is_default: bool | None = None

    @field_validator("prometheus_query_url", "prometheus_push_url")
    @classmethod
    def _req_url(cls, v: str | None) -> str | None:
        if v is None:
            return v
        return _validate_http_url(v)

    @field_validator("loki_push_url")
    @classmethod
    def _opt_url(cls, v: str | None) -> str | None:
        # Tri-state: None = leave unchanged; blank = clear; else validate.
        if v is None or not v.strip():
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
    prometheus_query_url: str
    prometheus_push_url: str
    loki_push_url: str | None
    org_id: str | None
    has_token: bool
    verify_ssl: bool
    has_ca_cert: bool
    ca_cert_fingerprint: str | None = None
    is_default: bool
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


def to_response(inst: Any) -> GrafanaInstanceResponse:
    """Build a response with derived secret/CA metadata. The raw token and
    PEM are never exposed — only their presence (and the CA fingerprint)."""
    pem = inst.ca_cert_pem
    return GrafanaInstanceResponse(
        id=inst.id,
        name=inst.name,
        prometheus_query_url=inst.prometheus_query_url,
        prometheus_push_url=inst.prometheus_push_url,
        loki_push_url=inst.loki_push_url,
        org_id=inst.org_id,
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
    #: False when no Grafana instance is registered → the UI shows the
    #: "set up metrics" CTA rather than an empty/error state.
    configured: bool
    #: ISO timestamp of the freshest sample, or None when no data.
    sampled_at: datetime | None = None
    cpu: MetricValue | None = None
    memory: MetricValue | None = None
    disk: MetricValue | None = None
    #: Populated when a query failed (vs. simply having no data yet).
    error: str | None = None
