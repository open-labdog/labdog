import enum
from datetime import datetime, timezone

from sqlalchemy import Boolean, CheckConstraint, DateTime, Enum, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class RuleAction(str, enum.Enum):
    allow = "allow"
    deny = "deny"
    reject = "reject"


class RuleProtocol(str, enum.Enum):
    tcp = "tcp"
    udp = "udp"
    icmp = "icmp"
    any = "any"


class RuleDirection(str, enum.Enum):
    input = "input"
    output = "output"


class FirewallRule(Base):
    __tablename__ = "firewall_rules"
    __table_args__ = (
        CheckConstraint(
            "(group_id IS NOT NULL AND host_id IS NULL) OR (group_id IS NULL AND host_id IS NOT NULL)",
            name="ck_firewall_rules_scope",
        ),
        CheckConstraint(
            "NOT (source_cidr IS NOT NULL AND source_host_id IS NOT NULL)",
            name="ck_firewall_rules_source_ref",
        ),
        CheckConstraint(
            "NOT (destination_cidr IS NOT NULL AND destination_host_id IS NOT NULL)",
            name="ck_firewall_rules_destination_ref",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    group_id: Mapped[int | None] = mapped_column(
        ForeignKey("host_groups.id", ondelete="CASCADE"),
        nullable=True,
    )
    host_id: Mapped[int | None] = mapped_column(
        ForeignKey("hosts.id", ondelete="CASCADE"),
        nullable=True,
    )
    action: Mapped[RuleAction] = mapped_column(
        Enum(RuleAction, name="ruleaction"),
    )
    protocol: Mapped[RuleProtocol] = mapped_column(
        Enum(RuleProtocol, name="ruleprotocol"),
    )
    direction: Mapped[RuleDirection] = mapped_column(
        Enum(RuleDirection, name="ruledirection"),
    )
    source_cidr: Mapped[str | None] = mapped_column(String(50), nullable=True)
    destination_cidr: Mapped[str | None] = mapped_column(String(50), nullable=True)
    source_host_id: Mapped[int | None] = mapped_column(
        ForeignKey("hosts.id", ondelete="RESTRICT"),
        nullable=True,
    )
    destination_host_id: Mapped[int | None] = mapped_column(
        ForeignKey("hosts.id", ondelete="RESTRICT"),
        nullable=True,
    )
    port_start: Mapped[int | None] = mapped_column(Integer, nullable=True)
    port_end: Mapped[int | None] = mapped_column(Integer, nullable=True)
    priority: Mapped[int] = mapped_column(Integer, default=0)
    comment: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_system: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )
