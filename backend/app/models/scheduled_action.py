"""Scheduled-action model.

A ``ScheduledAction`` row pairs a registered action key with a target
(host / group / fleet), parameters, and a cron schedule. The unified
scheduler at ``app.tasks.scheduled_action_schedule.check_due`` walks
this table once a minute and dispatches any rows that are due.

The ``snapshot_enabled`` / ``verify_enabled`` / ``auto_rollback`` /
``batch_size`` columns are universal — present on every row but only
honoured by the orchestrator when the underlying action is
``destructive=True``. Capability flags on the manifest are the
eventual home; until that refactor lands, the operator sets them at
the schedule level because that's where the run-time intent lives.
"""

from datetime import datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Index,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from app.models.base import Base


class ScheduledAction(Base):
    __tablename__ = "scheduled_actions"

    id: Mapped[int] = mapped_column(primary_key=True)
    # ``"host"`` | ``"group"`` | ``"fleet"``. ``"fleet"`` => target_id IS NULL.
    target_kind: Mapped[str] = mapped_column(String(8), nullable=False)
    # Polymorphic — points at hosts.id or host_groups.id depending on
    # ``target_kind``. NULL when target_kind="fleet". Cleanup is via
    # deletion hooks on host/group delete (see C9).
    target_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    action_key: Mapped[str] = mapped_column(String(64), nullable=False)
    parameters: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default="{}")
    schedule_cron: Mapped[str | None] = mapped_column(String(100), nullable=True)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")

    # Universal columns. See module docstring.
    snapshot_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")
    verify_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")
    auto_rollback: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")
    batch_size: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")

    # Scheduler bookkeeping. ``last_dispatched_at`` is the cron walk's
    # reference point — it advances on each successful dispatch, so the
    # walk is independent of action_runs being kept around.
    last_dispatched_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # When the cron expression last changed or the row was last switched
    # on. The walk starts from this too, so a run time that passed while
    # the schedule was off, or that only the new expression matches, is
    # not fired on the next tick. Kept apart from ``last_dispatched_at``
    # because that one also says when the scheduler last did anything.
    schedule_changed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    __table_args__ = (
        # fleet ⇒ target_id NULL; host/group ⇒ target_id NOT NULL.
        CheckConstraint(
            "(target_kind = 'fleet' AND target_id IS NULL) OR "
            "(target_kind IN ('host','group') AND target_id IS NOT NULL)",
            name="ck_scheduled_actions_target",
        ),
        # No double-scheduling the same work on the same target. Two
        # distinct schedules on the same target need distinct action keys.
        UniqueConstraint(
            "target_kind",
            "target_id",
            "action_key",
            name="uq_scheduled_actions_target_action",
        ),
        Index("ix_scheduled_actions_due", "action_key", "enabled"),
        Index("ix_scheduled_actions_target", "target_kind", "target_id"),
    )

    def set_schedule(self, cron: str | None, enabled: bool, now: datetime) -> None:
        """Change the cron expression and the enabled flag together.

        A new expression, or switching the row on, restarts the walk at
        *now*. Without that, the walk would carry on from the last
        dispatch and fire straight away for whatever it found in between.
        """
        if cron != self.schedule_cron or (enabled and not self.enabled):
            self.schedule_changed_at = now
        self.schedule_cron = cron
        self.enabled = enabled

    def walk_start(self) -> datetime:
        """Where the cron walk looks for the next run from."""
        marks = [t for t in (self.last_dispatched_at, self.schedule_changed_at) if t is not None]
        return max(marks) if marks else self.created_at
