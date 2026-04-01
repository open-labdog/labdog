import enum

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Enum as SAEnum,
    ForeignKey,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from app.models.base import Base


class ServiceState(str, enum.Enum):
    running = "running"
    stopped = "stopped"


class DeployMode(str, enum.Enum):
    full = "full"
    override = "override"


class ServiceRule(Base):
    __tablename__ = "service_rules"
    __table_args__ = (
        CheckConstraint(
            "(group_id IS NOT NULL AND host_id IS NULL) OR (group_id IS NULL AND host_id IS NOT NULL)",
            name="ck_service_rules_scope",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    group_id: Mapped[int | None] = mapped_column(
        ForeignKey("host_groups.id", ondelete="CASCADE"), nullable=True
    )
    host_id: Mapped[int | None] = mapped_column(
        ForeignKey("hosts.id", ondelete="CASCADE"), nullable=True
    )
    service_name: Mapped[str] = mapped_column(String(100), nullable=False)
    state: Mapped[ServiceState] = mapped_column(
        SAEnum(ServiceState, name="servicestate"),
        nullable=False,
        default=ServiceState.running,
    )
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    priority: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    comment: Mapped[str | None] = mapped_column(Text, nullable=True)
    unit_content: Mapped[str | None] = mapped_column(Text, nullable=True)
    deploy_mode: Mapped[DeployMode] = mapped_column(
        SAEnum(DeployMode, name="deploymode"),
        nullable=False,
        default=DeployMode.override,
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
