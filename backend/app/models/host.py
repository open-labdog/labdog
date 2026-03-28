import enum
from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, Enum, ForeignKey, Integer, String, Table, Column
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class FirewallBackend(str, enum.Enum):
    nftables = "nftables"
    iptables = "iptables"
    unknown = "unknown"


class SyncStatus(str, enum.Enum):
    pending = "pending"
    in_sync = "in_sync"
    out_of_sync = "out_of_sync"
    unknown = "unknown"
    error = "error"


# Join table for Host <-> HostGroup many-to-many
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
    barricade_source_ip: Mapped[str | None] = mapped_column(String(50), nullable=True)
    drift_check_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    last_sync_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_drift_check_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )
