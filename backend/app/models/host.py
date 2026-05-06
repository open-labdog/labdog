import enum
from datetime import UTC, datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Column,
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    String,
    Table,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class FirewallBackend(enum.StrEnum):
    nftables = "nftables"
    iptables = "iptables"
    unknown = "unknown"


class SyncStatus(enum.StrEnum):
    pending = "pending"
    in_sync = "in_sync"
    out_of_sync = "out_of_sync"
    unknown = "unknown"
    error = "error"


# Join table for Host <-> HostGroup many-to-many
#
# ``role`` is optional and currently used only by cluster-mode actions
# (e.g. ``k8s-upgrade``). The CHECK constraint backs the application
# enum: ``control_plane``, ``worker``, or NULL. Per-host actions
# ignore the field entirely.
HostGroupMembership = Table(
    "host_group_memberships",
    Base.metadata,
    Column(
        "host_id",
        Integer,
        ForeignKey("hosts.id", ondelete="CASCADE"),
        primary_key=True,
    ),
    Column(
        "group_id",
        Integer,
        ForeignKey("host_groups.id", ondelete="CASCADE"),
        primary_key=True,
    ),
    Column("role", String(length=32), nullable=True),
    CheckConstraint(
        "role IS NULL OR role IN ('control_plane', 'worker')",
        name="ck_host_group_memberships_role_valid",
    ),
)


class Host(Base):
    __tablename__ = "hosts"

    id: Mapped[int] = mapped_column(primary_key=True)
    hostname: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    ip_address: Mapped[str] = mapped_column(String(50))
    ssh_port: Mapped[int] = mapped_column(Integer, default=22)
    ssh_user: Mapped[str] = mapped_column(String(32), default="root")
    firewall_backend: Mapped[FirewallBackend] = mapped_column(
        Enum(FirewallBackend, name="firewallbackend"),
        default=FirewallBackend.unknown,
    )
    ssh_key_id: Mapped[int | None] = mapped_column(
        ForeignKey("ssh_keys.id", ondelete="SET NULL"),
        nullable=True,
    )
    sync_status: Mapped[SyncStatus] = mapped_column(
        Enum(SyncStatus, name="syncstatus"),
        default=SyncStatus.unknown,
    )
    labdog_source_ip: Mapped[str | None] = mapped_column(String(50), nullable=True)
    drift_check_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    last_sync_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_drift_check_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    os_codename: Mapped[str | None] = mapped_column(String(64), nullable=True)
    os_pretty_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    os_family: Mapped[str | None] = mapped_column(String(32), nullable=True)
    default_nic: Mapped[str | None] = mapped_column(String(32), nullable=True)
    kernel_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    kernel_release: Mapped[str | None] = mapped_column(String(32), nullable=True)
    os_facts_collected_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
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
