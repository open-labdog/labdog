import enum

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Enum as SAEnum,
    ForeignKey,
    Index,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from app.models.base import Base


class ResolverType(str, enum.Enum):
    resolv_conf = "resolv_conf"
    systemd_resolved = "systemd_resolved"
    networkmanager = "networkmanager"


class ResolverConfig(Base):
    __tablename__ = "resolver_configs"
    __table_args__ = (
        CheckConstraint(
            "(group_id IS NOT NULL AND host_id IS NULL) OR (group_id IS NULL AND host_id IS NOT NULL)",
            name="ck_resolver_configs_scope",
        ),
        # Partial unique indexes: one config per group, one per host
        Index(
            "ix_resolver_config_group_unique",
            "group_id",
            unique=True,
            postgresql_where=text("group_id IS NOT NULL"),
        ),
        Index(
            "ix_resolver_config_host_unique",
            "host_id",
            unique=True,
            postgresql_where=text("host_id IS NOT NULL"),
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    group_id: Mapped[int | None] = mapped_column(
        ForeignKey("host_groups.id", ondelete="CASCADE"), nullable=True
    )
    host_id: Mapped[int | None] = mapped_column(
        ForeignKey("hosts.id", ondelete="CASCADE"), nullable=True
    )
    nameservers: Mapped[list] = mapped_column(JSONB, nullable=False)
    search_domains: Mapped[list] = mapped_column(
        JSONB, nullable=False, server_default="[]"
    )
    options: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default="{}")
    resolver_type: Mapped[ResolverType] = mapped_column(
        SAEnum(ResolverType, name="resolvertype"),
        nullable=False,
        default=ResolverType.resolv_conf,
    )
    dns_over_tls: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[DateTime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[DateTime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )
