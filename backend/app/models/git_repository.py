import enum
from datetime import UTC, datetime

from sqlalchemy import DateTime, Enum, Integer, LargeBinary, String, Text
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
    #: SEC-27. The ``known_hosts`` line recorded the first time LabDog
    #: reached this repository over SSH. Every later sync is verified
    #: against it, so an intercepted connection is refused rather than
    #: silently accepted. NULL means first contact has not happened yet
    #: (or the operator cleared it after a legitimate server rekey).
    ssh_host_key_entry: Mapped[str | None] = mapped_column(Text, nullable=True)
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

    @property
    def has_pinned_host_key(self) -> bool:
        """Whether an SSH sync has recorded this server's host key.

        Read by ``GitRepoResponse`` so the API can say that syncs are
        verified without returning the key itself.
        """
        return bool(self.ssh_host_key_entry)
