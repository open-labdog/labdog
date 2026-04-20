from datetime import UTC, datetime

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
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from app.models.base import Base


class ScanConfig(Base):
    __tablename__ = "scan_configs"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)

    # Scan targets — list of CIDR strings, validated.
    cidrs: Mapped[list] = mapped_column(JSONB, nullable=False, server_default="[]")

    # Auth to use when SSH-verifying hits.
    ssh_key_id: Mapped[int] = mapped_column(
        ForeignKey("ssh_keys.id", ondelete="RESTRICT"), nullable=False
    )
    ssh_port: Mapped[int] = mapped_column(Integer, nullable=False, default=22)
    ssh_user: Mapped[str] = mapped_column(String(32), nullable=False, default="root")

    # Default groups to join when a host is added.
    default_group_ids: Mapped[list] = mapped_column(
        JSONB, nullable=False, server_default="[]"
    )

    # Schedule — exactly one of the two must be set.
    interval_minutes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    cron_expression: Mapped[str | None] = mapped_column(String(100), nullable=True)

    # Behaviour.
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    # Default off — UX researcher's "no surprise IoT-flood" mitigation.
    auto_add: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    # Last-run tracking.
    last_run_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_run_status: Mapped[str | None] = mapped_column(String(20), nullable=True)
    last_run_hosts_added: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0
    )
    last_run_hosts_pending: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0
    )
    last_run_error: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    __table_args__ = (
        CheckConstraint(
            "(interval_minutes IS NOT NULL) <> (cron_expression IS NOT NULL)",
            name="ck_scan_configs_schedule_one_of",
        ),
    )


class PendingHost(Base):
    """Host discovered by a scan config with auto_add=False, awaiting user review."""

    __tablename__ = "pending_hosts"

    id: Mapped[int] = mapped_column(primary_key=True)
    scan_config_id: Mapped[int] = mapped_column(
        ForeignKey("scan_configs.id", ondelete="CASCADE"), nullable=False
    )
    ip_address: Mapped[str] = mapped_column(String(45), nullable=False)
    hostname: Mapped[str | None] = mapped_column(String(253), nullable=True)
    ssh_verified: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    ssh_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    discovered_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        UniqueConstraint("scan_config_id", "ip_address", name="uq_pending_scan_ip"),
    )
