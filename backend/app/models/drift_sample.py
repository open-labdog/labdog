from datetime import UTC, datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class DriftSample(Base):
    """One row per drift check performed, across every module.

    Forward-only metrics substrate (no backfill, no updates, no deletes):
    every module drift-check task/endpoint writes exactly one row here,
    atomically with its ``HostModuleStatus`` / ``Host.sync_status`` write.
    Sits alongside ``HostModuleStatus`` (current state) and ``AuditLog``
    (audit trail) — this table exists purely to power the dashboard charts
    (``app.api.dashboard``) and, later, an OpenMetrics ``/metrics`` exporter
    reusing the same aggregations in ``app.metrics.service``.
    """

    __tablename__ = "drift_samples"

    id: Mapped[int] = mapped_column(primary_key=True)
    host_id: Mapped[int] = mapped_column(
        ForeignKey("hosts.id", ondelete="CASCADE"), nullable=False, index=True
    )
    module_type: Mapped[str] = mapped_column(String(50), nullable=False)
    # Values from the SyncStatus vocabulary (app.models.host.SyncStatus):
    # in_sync / out_of_sync / unknown / error / pending.
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    add_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    remove_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    policy_change_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    # Drift-check wall-clock duration; histogram source later (OpenMetrics
    # exporter). Nullable — not every call site can cheaply time itself.
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    checked_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        index=True,
    )
