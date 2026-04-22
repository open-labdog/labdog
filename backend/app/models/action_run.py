from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from app.models.base import Base


class ActionRun(Base):
    __tablename__ = "action_runs"

    id: Mapped[int] = mapped_column(primary_key=True)
    action_key: Mapped[str] = mapped_column(String(64), nullable=False)
    action_version: Mapped[str] = mapped_column(String(32), nullable=False)
    host_id: Mapped[int | None] = mapped_column(
        ForeignKey("hosts.id", ondelete="SET NULL"), nullable=True
    )
    group_id: Mapped[int | None] = mapped_column(
        ForeignKey("host_groups.id", ondelete="SET NULL"), nullable=True
    )
    parameters: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default="{}")
    parallelism: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    # status values: queued | running | succeeded | partial | failed | cancelled
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="queued")
    triggered_by_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        CheckConstraint(
            "(host_id IS NOT NULL)::int + (group_id IS NOT NULL)::int = 1",
            name="ck_action_runs_scope",
        ),
    )


class ActionHostRun(Base):
    __tablename__ = "action_host_runs"

    id: Mapped[int] = mapped_column(primary_key=True)
    action_run_id: Mapped[int] = mapped_column(
        ForeignKey("action_runs.id", ondelete="CASCADE"), nullable=False
    )
    host_id: Mapped[int] = mapped_column(ForeignKey("hosts.id", ondelete="CASCADE"), nullable=False)
    # status values: queued | running | succeeded | failed | skipped | cancelled
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="queued")
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    exit_code: Mapped[int | None] = mapped_column(Integer, nullable=True)
    output: Mapped[str] = mapped_column(Text, nullable=False, server_default="''")
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Proxmox snapshot captured before a destructive action ran. Non-null
    # means a snapshot exists (deleted on success, kept on failure/rollback).
    snapshot_name: Mapped[str | None] = mapped_column(String(128), nullable=True)

    __table_args__ = (UniqueConstraint("action_run_id", "host_id", name="uq_action_host_run"),)
