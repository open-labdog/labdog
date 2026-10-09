from sqlalchemy import Boolean, DateTime, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSON
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class HostModuleStatus(Base):
    __tablename__ = "host_module_status"
    __table_args__ = (
        UniqueConstraint(
            "host_id", "module_type", name="uq_host_module_status_host_id_module_type"
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    host_id: Mapped[int] = mapped_column(ForeignKey("hosts.id", ondelete="CASCADE"), nullable=False)
    module_type: Mapped[str] = mapped_column(String(50), nullable=False)
    sync_status: Mapped[str] = mapped_column(String(20), nullable=False, server_default="unknown")
    drift_check_enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="false"
    )
    last_sync_at: Mapped[DateTime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_drift_check_at: Mapped[DateTime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    collected_state: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    collected_at: Mapped[DateTime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)


def _last_verified_at():
    """When LabDog last confirmed this host's state, or NULL (BUG-99).

    The newest of: a module row that was collected, drift-checked or
    synced and did not end in error; a firewall drift check (it writes the
    host row, not a module row) while the host is not in error; and an OS
    facts collection, which only stamps on success.

    Every timestamp involved is written on attempts as well as successes,
    so a row counts only while it is healthy: ``error_message`` unset and
    ``sync_status`` not ``error`` or ``unknown`` (``unknown`` is what an
    unreachable host's rows are set to). A host whose every check now fails
    therefore has no verified time and reads as stale, which is the point:
    it is the host LabDog has lost track of.

    Computed in SQL rather than stored, so the dozen places that write
    these timestamps do not each have to remember a second column.
    """
    from sqlalchemy import case, func, select  # noqa: PLC0415

    from app.models.host import Host, SyncStatus  # noqa: PLC0415

    healthy_module = (
        select(
            func.max(
                func.greatest(
                    HostModuleStatus.collected_at,
                    HostModuleStatus.last_drift_check_at,
                    HostModuleStatus.last_sync_at,
                )
            )
        )
        .where(
            HostModuleStatus.host_id == Host.id,
            HostModuleStatus.error_message.is_(None),
            HostModuleStatus.sync_status.not_in(("error", "unknown")),
        )
        .correlate(Host)
        .scalar_subquery()
    )
    firewall_check = case(
        (
            Host.sync_status.not_in((SyncStatus.error, SyncStatus.unknown)),
            Host.last_drift_check_at,
        ),
        else_=None,
    )
    return func.greatest(healthy_module, firewall_check, Host.os_facts_collected_at)


def _attach_last_verified_at() -> None:
    from sqlalchemy.orm import column_property  # noqa: PLC0415

    from app.models.host import Host  # noqa: PLC0415

    Host.last_verified_at = column_property(_last_verified_at())


_attach_last_verified_at()
