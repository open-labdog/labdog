from datetime import datetime

from sqlalchemy import (
    Boolean,
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


def _default_target_kind(context) -> str:
    """``target_kind`` inferred from the FKs of the row being inserted."""
    params = context.get_current_parameters()
    if params.get("host_id") is not None:
        return "host"
    if params.get("group_id") is not None:
        return "group"
    return "fleet"


def _default_target_label(context) -> str:
    """A last-resort label. Terse but never wrong; the dispatch sites
    replace it with the target's real name."""
    params = context.get_current_parameters()
    if params.get("host_id") is not None:
        return f"host {params['host_id']}"
    if params.get("group_id") is not None:
        return f"group {params['group_id']}"
    return "All hosts"


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
    # Set when the run was created by the unified scheduler or by
    # POST /api/scheduled-actions/{id}/run-now. NULL for ad-hoc runs.
    # ``ON DELETE SET NULL`` so deleting a schedule preserves run history.
    scheduled_action_id: Mapped[int | None] = mapped_column(
        ForeignKey("scheduled_actions.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    parameters: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default="{}")
    parallelism: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    # Universal columns mirrored from ScheduledAction at dispatch time so
    # per-host executors see immutable run-time intent without a join.
    # Ignored when the underlying action is non-destructive.
    snapshot_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")
    verify_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")
    auto_rollback: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")
    # status values: queued | pending | running | succeeded | partial | failed | cancelled
    #
    # ``queued``: Celery hasn't picked the run up yet (newly inserted).
    # ``pending``: Celery picked it up, claim-or-defer decided the
    #              target host (or some member of a group target) is in
    #              use by another op, waiting for dispatch-next-pending
    #              to re-fire when the in-flight op finishes.
    # ``running``: the action is in flight.
    # terminal: ``succeeded`` | ``partial`` | ``failed`` | ``cancelled``.
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="queued")
    triggered_by_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Populated by claim-or-defer when the run transitions to ``pending``:
    # a human-readable string naming the in-flight op that is holding the
    # target host (e.g. "Waiting for sync 47 on host node-1"). Cleared back
    # to ``None`` when the run is re-dispatched and successfully claims.
    # Nullable so existing rows and non-deferred runs don't need a value.
    pending_reason: Mapped[str | None] = mapped_column(String(255), nullable=True, default=None)
    # What this run targeted, said in the run's own columns rather than
    # inferred from the FKs. The FKs above are ON DELETE SET NULL, so
    # deleting a host erases the only description an ad-hoc run had of
    # what it ran against — and under the old scope CHECK the delete
    # failed outright rather than losing it. These two columns are what
    # survives the delete.
    #
    # ``target_kind``: host | group | fleet, mirroring
    # ScheduledAction.target_kind.
    #
    # Both default from the FK columns of the row being inserted, so a
    # caller that does not set them still gets a correct — if terse —
    # description rather than an IntegrityError. Dispatch sites go
    # through :func:`app.actions.run_target.describe_target`, which
    # upgrades the label to the target's actual name.
    target_kind: Mapped[str] = mapped_column(
        String(8), nullable=False, default=_default_target_kind
    )
    # The hostname or group name as it stood at dispatch time.
    # Denormalised deliberately: once the target is gone there is nothing
    # left to join to, which is the case this exists for.
    target_label: Mapped[str] = mapped_column(
        String(255), nullable=False, default=_default_target_label
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        CheckConstraint(
            "target_kind IN ('host', 'group', 'fleet')",
            name="ck_action_runs_target_kind",
        ),
    )


class ActionHostRun(Base):
    __tablename__ = "action_host_runs"

    id: Mapped[int] = mapped_column(primary_key=True)
    action_run_id: Mapped[int] = mapped_column(
        ForeignKey("action_runs.id", ondelete="CASCADE"), nullable=False
    )
    host_id: Mapped[int] = mapped_column(ForeignKey("hosts.id", ondelete="CASCADE"), nullable=False)
    # status values: queued | pending | running | succeeded | failed | skipped | cancelled
    #
    # ``pending`` mirrors the parent ActionRun.status: Celery picked the
    # parent task up but found the target host busy and deferred. The
    # per-host row sits in ``pending`` until dispatch-next-pending
    # re-fires the parent.
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="queued")
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    exit_code: Mapped[int | None] = mapped_column(Integer, nullable=True)
    output: Mapped[str] = mapped_column(Text, nullable=False, server_default="''")
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Proxmox snapshot captured before a destructive action ran. Non-null
    # means a snapshot exists (deleted on success, kept on failure/rollback).
    snapshot_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    # Mirrors :attr:`ActionRun.pending_reason` so per-host rows in a group
    # dispatch surface the same diagnostic in the UI's host grid (each row
    # gets the same string — the defer is run-level, not host-level).
    # Nullable so non-deferred rows don't need a value.
    pending_reason: Mapped[str | None] = mapped_column(String(255), nullable=True, default=None)

    __table_args__ = (UniqueConstraint("action_run_id", "host_id", name="uq_action_host_run"),)
