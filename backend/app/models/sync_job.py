import enum
from datetime import UTC, datetime

from sqlalchemy import DateTime, Enum, ForeignKey, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class JobStatus(enum.StrEnum):
    pending = "pending"
    running = "running"
    success = "success"
    failed = "failed"
    cancelled = "cancelled"


class SyncJob(Base):
    __tablename__ = "sync_jobs"

    id: Mapped[int] = mapped_column(primary_key=True)
    host_id: Mapped[int] = mapped_column(
        ForeignKey("hosts.id", ondelete="CASCADE"),
        nullable=False,
    )
    group_id: Mapped[int | None] = mapped_column(
        ForeignKey("host_groups.id", ondelete="SET NULL"),
        nullable=True,
    )
    status: Mapped[JobStatus] = mapped_column(
        Enum(JobStatus, name="jobstatus"),
        default=JobStatus.pending,
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    ansible_output: Mapped[str | None] = mapped_column(Text, nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Set when ``status='pending'`` because another op on the same host
    # was running. Mirrors ``ActionRun.pending_reason``; the UI renders
    # both as the same "Host busy" tooltip via RunStatusBadge. NULL on
    # non-pending rows and on legacy rows that predate this column.
    pending_reason: Mapped[str | None] = mapped_column(String(255), nullable=True)
    triggered_by_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    module_type: Mapped[str] = mapped_column(String(50), nullable=False, server_default="firewall")
    # The exact set of modules this job was asked to apply.
    #
    # ``module_type`` alone cannot express it: a bulk sync stores the
    # literal "bulk", which reconstructs as "every module". A job deferred
    # behind a busy host is re-dispatched from the row, so an operator who
    # asked to reapply *firewall* got packages, services and /etc/hosts
    # rewritten as well when the queue drained.
    #
    # NULL means "not recorded" and falls back to ``module_type``, which is
    # exactly right for the per-module endpoints — they name their single
    # module there — and for pre-existing bulk rows, which genuinely did
    # mean every module. See ``module_filter_for``.
    module_filter: Mapped[list[str] | None] = mapped_column(JSONB, nullable=True, default=None)
    # The ``ActionHostRun`` of the ``_builtin.sync`` that created this job,
    # when there was one. NULL for syncs started from the API or the
    # scheduler.
    #
    # A built-in sync whose host is busy leaves its job ``pending`` for the
    # host queue to re-dispatch later. Its own row stays ``pending`` too
    # rather than claiming success for work that has not happened (BUG-81),
    # so whoever eventually runs the job has to know whose row to close.
    #
    # ``SET NULL``, not CASCADE: run retention deletes ``action_host_runs``
    # on a schedule and must not take sync history with them.
    origin_action_host_run_id: Mapped[int | None] = mapped_column(
        ForeignKey("action_host_runs.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
        default=None,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
    )
