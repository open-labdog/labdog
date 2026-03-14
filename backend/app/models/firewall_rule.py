import enum
from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, Enum, ForeignKey, Integer, String, Text
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

    id: Mapped[int] = mapped_column(primary_key=True)
    group_id: Mapped[int] = mapped_column(
        ForeignKey("host_groups.id", ondelete="CASCADE"),
        nullable=False,
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
