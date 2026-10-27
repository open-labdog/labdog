"""The actions alerts may run, and what each alert did with one.

An alert asks for an action with the Grafana label ``labdog_action``
(:mod:`app.alerts.labels`). :class:`AlertAction` is LabDog's side of that
decision: the list of actions an alert is allowed to start, each with the
hosts it may run on and the parameters it runs with. The list starts
empty, so a label does nothing until someone here says it may.

:class:`AlertActionDispatch` is one row per alert that asked for an
action, written whether or not a run was started, for the same reason
``AlertEvent.investigation_outcome`` is: "why did nothing happen for this
alert?" has several answers, and the Alerts page should give the right one
rather than leave the operator to guess.
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from app.models.base import Base

#: What happened when the alert arrived.
DISPATCH_OUTCOMES = (
    "started",
    "not_allowed",  # not on the list, paused there, or the host is outside its scope
    "unknown_action",  # no action has this key
    "skipped_no_host",  # the alert names no host LabDog manages
    "skipped_token",  # a webhook alert, and the token is too short to act on
    "skipped_in_flight",  # a run of this action on this host is still going
    "skipped_cooldown",  # an alert ran this action on this host too recently
    "failed",  # the run could not be made
)

#: What the check found afterwards, for a started run.
CHECK_OUTCOMES = (
    "fixed",  # the alert resolved
    "not_effective",  # the run succeeded and the alert is still firing
    "run_failed",  # the run failed, was cancelled, or was deleted
)


class AlertAction(Base):
    """One action alerts may run, and how.

    One row per action: the label names an action key, and the key must
    lead to exactly one set of parameters and options. The target is the
    scope, not what the run covers — a run is always the one host the
    alert names, and only if that host is inside the target.
    """

    __tablename__ = "alert_actions"

    id: Mapped[int] = mapped_column(primary_key=True)
    action_key: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    target_kind: Mapped[str] = mapped_column(String(8), nullable=False, server_default="fleet")
    target_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    parameters: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default="{}")
    snapshot_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")
    verify_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")
    auto_rollback: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")
    cooldown_minutes: Mapped[int] = mapped_column(Integer, nullable=False, server_default="60")
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    __table_args__ = (
        CheckConstraint(
            "(target_kind = 'fleet' AND target_id IS NULL) OR "
            "(target_kind IN ('host','group') AND target_id IS NOT NULL)",
            name="target",
        ),
    )


class AlertActionDispatch(Base):
    __tablename__ = "alert_action_dispatches"

    id: Mapped[int] = mapped_column(primary_key=True)
    alert_event_id: Mapped[int] = mapped_column(
        ForeignKey("alert_events.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # SET NULL keeps the record of what was done after the action is taken
    # off the list; action_key says what it was.
    alert_action_id: Mapped[int | None] = mapped_column(
        ForeignKey("alert_actions.id", ondelete="SET NULL"), nullable=True, index=True
    )
    action_key: Mapped[str] = mapped_column(String(64), nullable=False)
    host_id: Mapped[int | None] = mapped_column(
        ForeignKey("hosts.id", ondelete="SET NULL"), nullable=True
    )
    action_run_id: Mapped[int | None] = mapped_column(
        ForeignKey("action_runs.id", ondelete="SET NULL"), nullable=True, index=True
    )
    outcome: Mapped[str] = mapped_column(String(24), nullable=False)
    detail: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Whether the alert's labels asked for the AI, decided when the run was
    # made: a run already under way is judged by what was asked then.
    hand_to_ai: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    check_outcome: Mapped[str | None] = mapped_column(String(16), nullable=True)
    checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC)
    )
