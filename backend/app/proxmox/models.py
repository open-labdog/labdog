from datetime import UTC, datetime

from sqlalchemy import Boolean, DateTime, LargeBinary, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class ProxmoxNode(Base):
    """Represents a Proxmox VE node connection.

    The API token secret is stored encrypted (AES-256-GCM).
    Encryption and decryption are handled in the API/task layers via
    ``app.crypto.encryption.encrypt_ssh_key`` /
    ``app.crypto.encryption.decrypt_ssh_key`` — never in this model.

    ``ca_cert_pem`` holds an optional PEM-encoded CA certificate used to
    verify the node's TLS certificate (BUG-52). Unlike the token secret it
    is stored as **plaintext** — CA certificates are public, not secrets —
    and is explicitly NOT encrypted (never passed through
    ``encrypt_ssh_key`` / ``decrypt_ssh_key``).
    """

    __tablename__ = "proxmox_nodes"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(100), unique=True, index=True)
    api_url: Mapped[str] = mapped_column(String(500))
    token_id: Mapped[str] = mapped_column(String(200))
    encrypted_token_secret: Mapped[bytes] = mapped_column(LargeBinary)  # AES-256-GCM encrypted
    verify_ssl: Mapped[bool] = mapped_column(Boolean, default=True)
    # Plaintext PEM CA certificate (NOT encrypted — CA certs are public).
    ca_cert_pem: Mapped[str | None] = mapped_column(Text, nullable=True, default=None)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
    )
