import enum

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy import (
    Enum as SAEnum,
)
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from app.models.base import Base


class PackageState(enum.StrEnum):
    present = "present"
    absent = "absent"
    latest = "latest"


class PackageManager(enum.StrEnum):
    apt = "apt"
    dnf = "dnf"
    yum = "yum"
    auto = "auto"


class RepoType(enum.StrEnum):
    apt = "apt"
    yum = "yum"


class PackageRule(Base):
    __tablename__ = "package_rules"
    __table_args__ = (
        CheckConstraint(
            "(group_id IS NOT NULL AND host_id IS NULL)"
            " OR (group_id IS NULL AND host_id IS NOT NULL)",
            name="ck_package_rules_scope",
        ),
        UniqueConstraint("group_id", "package_name", name="uq_package_rules_group_pkg"),
        UniqueConstraint("host_id", "package_name", name="uq_package_rules_host_pkg"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    group_id: Mapped[int | None] = mapped_column(
        ForeignKey("host_groups.id", ondelete="CASCADE"), nullable=True
    )
    host_id: Mapped[int | None] = mapped_column(
        ForeignKey("hosts.id", ondelete="CASCADE"), nullable=True
    )
    package_name: Mapped[str] = mapped_column(String(200), nullable=False)
    version: Mapped[str | None] = mapped_column(String(100), nullable=True)
    state: Mapped[PackageState] = mapped_column(
        SAEnum(PackageState, name="packagestate"),
        nullable=False,
        default=PackageState.present,
    )
    package_manager: Mapped[PackageManager] = mapped_column(
        SAEnum(PackageManager, name="packagemanager"),
        nullable=False,
        default=PackageManager.auto,
    )
    priority: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    comment: Mapped[str | None] = mapped_column(Text, nullable=True)
    hold: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[DateTime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[DateTime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


class PackageRepository(Base):
    __tablename__ = "package_repositories"
    __table_args__ = (UniqueConstraint("group_id", "name", name="uq_package_repos_group_name"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    group_id: Mapped[int] = mapped_column(
        ForeignKey("host_groups.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    url: Mapped[str] = mapped_column(String(500), nullable=False)
    key_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    repo_type: Mapped[RepoType] = mapped_column(
        SAEnum(RepoType, name="repotype"),
        nullable=False,
    )
    distribution: Mapped[str | None] = mapped_column(String(100), nullable=True)
    components: Mapped[str | None] = mapped_column(String(200), nullable=True)
    state: Mapped[PackageState] = mapped_column(
        SAEnum(PackageState, name="packagestate"),
        nullable=False,
        default=PackageState.present,
    )
    created_at: Mapped[DateTime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[DateTime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )
