import enum
from datetime import UTC, datetime

from sqlalchemy import DateTime, Enum, Integer, LargeBinary, String
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class GitAuthType(enum.StrEnum):
    none = "none"
    ssh_key = "ssh_key"
    https_token = "https_token"


class GitOpsStatus(enum.StrEnum):
    disconnected = "disconnected"
    synced = "synced"
    error = "error"
    importing = "importing"


class GitRepository(Base):
    __tablename__ = "git_repositories"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200), unique=True)
    url: Mapped[str] = mapped_column(String(500))  # SSH or HTTPS URL
    branch: Mapped[str] = mapped_column(String(100), default="main")
    auth_type: Mapped[GitAuthType] = mapped_column(
        Enum(GitAuthType, name="gitauthtype"),
    )
    ssh_key_id: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
    )
    encrypted_https_token: Mapped[bytes | None] = mapped_column(
        LargeBinary,
        nullable=True,
    )
    webhook_secret: Mapped[str | None] = mapped_column(String(200), nullable=True)
    last_commit_sha: Mapped[str | None] = mapped_column(String(64), nullable=True)
    last_sync_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
    )
