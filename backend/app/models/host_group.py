from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, Enum, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base
from app.models.git_repository import GitOpsStatus


class HostGroup(Base):
    __tablename__ = "host_groups"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(100), unique=True, index=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    category: Mapped[str | None] = mapped_column(String(100), nullable=True)
    priority: Mapped[int] = mapped_column(Integer, unique=True)  # higher = higher priority
    input_policy: Mapped[str | None] = mapped_column(String(6), nullable=True)
    output_policy: Mapped[str | None] = mapped_column(String(6), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    git_repository_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("git_repositories.id"), nullable=True,
    )
    gitops_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    gitops_file_path: Mapped[str | None] = mapped_column(String(500), nullable=True)
    gitops_status: Mapped[GitOpsStatus] = mapped_column(
        Enum(GitOpsStatus, name="gitopsstatus"),
        default=GitOpsStatus.disconnected,
    )
    gitops_error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    gitops_last_import_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True,
    )
