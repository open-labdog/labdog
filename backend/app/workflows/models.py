import enum
from datetime import UTC, datetime

from sqlalchemy import JSON, Boolean, DateTime, Enum, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class WorkflowRunStatus(enum.StrEnum):
    pending = "pending"
    running = "running"
    completed = "completed"
    failed = "failed"
    partial = "partial"


class WorkflowHostStatus(enum.StrEnum):
    pending = "pending"
    running = "running"
    success = "success"
    failed = "failed"
    skipped = "skipped"


class WorkflowStep(enum.StrEnum):
    preflight = "preflight"
    snapshot = "snapshot"
    update = "update"
    reboot = "reboot"
    verify = "verify"
    cleanup = "cleanup"
    rollback = "rollback"


class UpdateWorkflow(Base):
    __tablename__ = "update_workflows"

    id: Mapped[int] = mapped_column(primary_key=True)
    group_id: Mapped[int] = mapped_column(
        ForeignKey("host_groups.id", ondelete="CASCADE"), unique=True
    )
    batch_size: Mapped[int] = mapped_column(Integer, default=1)
    schedule_cron: Mapped[str | None] = mapped_column(String(100), nullable=True)
    pre_update_snapshot: Mapped[bool] = mapped_column(Boolean, default=True)
    auto_rollback: Mapped[bool] = mapped_column(Boolean, default=True)
    verification_prompt: Mapped[str | None] = mapped_column(Text, nullable=True)
    auto_reboot: Mapped[bool] = mapped_column(Boolean, default=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
    )


class WorkflowRun(Base):
    __tablename__ = "workflow_runs"

    id: Mapped[int] = mapped_column(primary_key=True)
    workflow_id: Mapped[int] = mapped_column(ForeignKey("update_workflows.id", ondelete="CASCADE"))
    status: Mapped[WorkflowRunStatus] = mapped_column(
        Enum(WorkflowRunStatus, name="workflowrunstatus"),
        default=WorkflowRunStatus.pending,
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    triggered_by: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
    )


class WorkflowHostRun(Base):
    __tablename__ = "workflow_host_runs"

    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("workflow_runs.id", ondelete="CASCADE"))
    host_id: Mapped[int] = mapped_column(ForeignKey("hosts.id", ondelete="CASCADE"))
    step: Mapped[WorkflowStep] = mapped_column(
        Enum(WorkflowStep, name="workflowstep"),
        default=WorkflowStep.preflight,
    )
    status: Mapped[WorkflowHostStatus] = mapped_column(
        Enum(WorkflowHostStatus, name="workflowhoststatus"),
        default=WorkflowHostStatus.pending,
    )
    snapshot_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    step_output: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
