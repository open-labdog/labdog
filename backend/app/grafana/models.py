from datetime import UTC, datetime

from sqlalchemy import Boolean, DateTime, LargeBinary, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class GrafanaInstance(Base):
    """A registered Grafana-stack endpoint — either a Mimir/Prometheus
    (metrics) or a Loki (logs) backend. Mimir and Loki are registered
    **separately** (one row each, distinguished by ``kind``).

    A single ``url`` is stored: the ingest/remote-write URL the operator
    points the agent at (with whatever path their setup needs, e.g.
    ``.../api/v1/push``). LabDog hands that URL to the Alloy install action
    verbatim, and for querying it strips the URL down to the host and
    appends the kind's query path (see ``app.grafana.schemas.derive_query_url``)
    — so the operator never has to know two URLs.

    ``encrypted_token`` is an optional AES-256-GCM bearer token (handled in
    the API/task layers via ``app.crypto`` — never here). ``ca_cert_pem`` is
    plaintext (CA certs are public). Modeled on
    :class:`app.proxmox.models.ProxmoxNode`.
    """

    __tablename__ = "grafana_instances"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(100), unique=True, index=True)
    # "mimir" (metrics, Prometheus-compatible) or "loki" (logs).
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    # The ingest/remote-write URL, as the operator enters it (may include a
    # path like /api/v1/push). Handed to Alloy as-is; the query URL is
    # derived from it.
    url: Mapped[str] = mapped_column(String(500), nullable=False)
    # Tenant sent as the ``X-Scope-OrgID`` header (Mimir/Loki multitenancy).
    org_id: Mapped[str | None] = mapped_column(String(200), nullable=True, default=None)
    # Optional bearer token, AES-256-GCM encrypted (never plaintext here).
    encrypted_token: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True, default=None)
    verify_ssl: Mapped[bool] = mapped_column(Boolean, default=True)
    # Plaintext PEM CA certificate (NOT encrypted — CA certs are public).
    ca_cert_pem: Mapped[str | None] = mapped_column(Text, nullable=True, default=None)
    # At most one default **per kind** — the Mimir LabDog queries for host
    # metrics, and the Mimir/Loki it injects into the Alloy action when none
    # is explicitly chosen. Enforced in the API layer.
    is_default: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
    )
