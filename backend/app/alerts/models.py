"""What an alert-triggered schedule did with one alert.

One row per (alert, matching schedule). Written whether or not a run was
started, for the same reason ``AlertEvent.investigation_outcome`` is: "why
did nothing happen for this alert?" has several answers, and the Alerts
page should give the right one rather than leave the operator to guess.
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base

#: What happened when the alert arrived.
DISPATCH_OUTCOMES = (
    "started",
    "skipped_no_host",  # the alert names no host LabDog manages
    "skipped_token",  # a webhook alert, and the token is too short to act on
    "skipped_in_flight",  # a run of this schedule on this host is still going
    "skipped_cooldown",  # this schedule ran on this host too recently
    "failed",  # the action is no longer registered, or the run could not be made
)

#: What the check found afterwards, for a started run.
CHECK_OUTCOMES = (
    "fixed",  # the alert resolved
    "not_effective",  # the run succeeded and the alert is still firing
    "run_failed",  # the run failed, was cancelled, or was deleted
)


class AlertActionDispatch(Base):
    __tablename__ = "alert_action_dispatches"

    id: Mapped[int] = mapped_column(primary_key=True)
    alert_event_id: Mapped[int] = mapped_column(
        ForeignKey("alert_events.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # SET NULL keeps the record of what was done after the schedule is
    # deleted; action_key says what it was.
    scheduled_action_id: Mapped[int | None] = mapped_column(
        ForeignKey("scheduled_actions.id", ondelete="SET NULL"), nullable=True, index=True
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
    # The schedule's setting when the run was made, so editing the schedule
    # later does not change what happens to a run already under way.
    investigate_on_failure: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    check_outcome: Mapped[str | None] = mapped_column(String(16), nullable=True)
    checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC)
    )
