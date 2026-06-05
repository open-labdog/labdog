from datetime import UTC, datetime

from sqlalchemy import Boolean, DateTime, LargeBinary, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class GrafanaInstance(Base):
    """A registered Grafana Mimir/Loki (Prometheus-compatible) backend.

    LabDog queries ``prometheus_query_url`` to render instant host metrics,
    and hands ``prometheus_push_url`` / ``loki_push_url`` to the Alloy
    install action so the agent ships data to the same backend (closing the
    loop). Modeled on :class:`app.proxmox.models.ProxmoxNode`.

    ``encrypted_token`` holds an optional bearer token, AES-256-GCM
    encrypted — encryption/decryption happen in the API/task layers via
    ``app.crypto`` helpers, never in this model. ``ca_cert_pem`` is stored
    as **plaintext** (CA certs are public, not secrets) and is explicitly
    NOT encrypted.
    """

    __tablename__ = "grafana_instances"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(100), unique=True, index=True)
    # What LabDog queries (Prometheus-compatible base, e.g.
    # ``http://mimir:9009/prometheus`` — the ``/api/v1/query`` suffix is
    # appended by the query client).
    prometheus_query_url: Mapped[str] = mapped_column(String(500))
    # What Alloy remote-writes to (e.g. ``http://mimir:9009/api/v1/push``).
    prometheus_push_url: Mapped[str] = mapped_column(String(500))
    # What Alloy pushes logs to (e.g. ``http://loki:3100/loki/api/v1/push``).
    # Optional — metrics-only backends are allowed.
    loki_push_url: Mapped[str | None] = mapped_column(String(500), nullable=True, default=None)
    # Tenant sent as the ``X-Scope-OrgID`` header (Mimir multitenancy).
    org_id: Mapped[str | None] = mapped_column(String(200), nullable=True, default=None)
    # Optional bearer token, AES-256-GCM encrypted (never plaintext here).
    encrypted_token: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True, default=None)
    verify_ssl: Mapped[bool] = mapped_column(Boolean, default=True)
    # Plaintext PEM CA certificate (NOT encrypted — CA certs are public).
    ca_cert_pem: Mapped[str | None] = mapped_column(Text, nullable=True, default=None)
    # Exactly one instance should be the default — the one LabDog queries
    # for host metrics and injects into the Alloy action when none is
    # explicitly chosen. Enforced in the API layer (unset others on set).
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
