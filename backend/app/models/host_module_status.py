from sqlalchemy import Boolean, DateTime, ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from app.models.base import Base


class HostModuleStatus(Base):
    __tablename__ = "host_module_status"
    __table_args__ = (
        UniqueConstraint(
            "host_id", "module_type", name="uq_host_module_status_host_id_module_type"
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    host_id: Mapped[int] = mapped_column(
        ForeignKey("hosts.id", ondelete="CASCADE"), nullable=False
    )
    module_type: Mapped[str] = mapped_column(String(50), nullable=False)
    sync_status: Mapped[str] = mapped_column(
        String(20), nullable=False, server_default="unknown"
    )
    drift_check_enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="false"
    )
    last_sync_at: Mapped[DateTime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_drift_check_at: Mapped[DateTime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
