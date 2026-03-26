from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, LargeBinary, String
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class ProxmoxNode(Base):
    """Represents a Proxmox VE node connection.

    The API token secret is stored encrypted (AES-256-GCM).
    Encryption and decryption are handled in the API/task layers via
    ``app.crypto.encryption.encrypt_ssh_key`` /
    ``app.crypto.encryption.decrypt_ssh_key`` — never in this model.
    """

    __tablename__ = "proxmox_nodes"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(100), unique=True, index=True)
    api_url: Mapped[str] = mapped_column(String(500))
    token_id: Mapped[str] = mapped_column(String(200))
    encrypted_token_secret: Mapped[bytes] = mapped_column(LargeBinary)  # AES-256-GCM encrypted
    verify_ssl: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )
