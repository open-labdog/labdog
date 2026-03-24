from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, LargeBinary, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class SSHKey(Base):
    __tablename__ = "ssh_keys"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(100), unique=True)
    encrypted_private_key: Mapped[bytes] = mapped_column(LargeBinary)  # AES-256-GCM encrypted
    public_key: Mapped[str | None] = mapped_column(Text, nullable=True)
    ssh_user: Mapped[str] = mapped_column(String(32), default="root")
    is_default: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
    )
