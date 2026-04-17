from sqlalchemy import Boolean, CheckConstraint, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from app.models.base import Base


class HostsEntry(Base):
    __tablename__ = "hosts_entries"
    __table_args__ = (
        CheckConstraint(
            "(group_id IS NOT NULL AND host_id IS NULL)"
            " OR (group_id IS NULL AND host_id IS NOT NULL)",
            name="ck_hosts_entries_scope",
        ),
        CheckConstraint(
            "host_ref_id IS NOT NULL OR (ip_address IS NOT NULL AND hostname IS NOT NULL)",
            name="ck_hosts_entries_ref_or_literal",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    group_id: Mapped[int | None] = mapped_column(
        ForeignKey("host_groups.id", ondelete="CASCADE"), nullable=True
    )
    host_id: Mapped[int | None] = mapped_column(
        ForeignKey("hosts.id", ondelete="CASCADE"), nullable=True
    )
    host_ref_id: Mapped[int | None] = mapped_column(
        ForeignKey("hosts.id", ondelete="RESTRICT"), nullable=True
    )
    ip_address: Mapped[str | None] = mapped_column(String(45), nullable=True)  # fits IPv6
    hostname: Mapped[str | None] = mapped_column(String(253), nullable=True)
    aliases: Mapped[list] = mapped_column(JSONB, nullable=False, server_default="[]")
    comment: Mapped[str | None] = mapped_column(Text, nullable=True)
    priority: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    is_system: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[DateTime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[DateTime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )
