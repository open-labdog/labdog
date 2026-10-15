"""Pydantic schemas for the unified scheduled-actions API.

The public shape mirrors the internal ``ScheduledAction`` model but
adds a few convenience fields (``target_name``, ``action_name``,
``destructive``, ``last_run``) that the listing endpoint resolves
server-side so the UI doesn't need to make N+1 lookup calls.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

ScheduledActionTargetKind = Literal["host", "group", "fleet"]
#: ``cron`` covers cron schedules and run-by-hand ones (no cron);
#: ``alert`` fires when a matching alert names a host in the target.
ScheduledActionTrigger = Literal["cron", "alert"]


class ScheduledActionRunSummary(BaseModel):
    """Newest action_runs row for a schedule, rolled up for the listing."""

    id: int
    status: str
    started_at: datetime | None = None
    finished_at: datetime | None = None
    created_at: datetime


class ScheduledActionRunOut(BaseModel):
    """One individual scheduled-origin action run, hydrated for the dashboard.

    Powers the "Recent Scheduled Runs" panel's per-run / grouped views: unlike
    ``ScheduledActionRunSummary`` (one latest run per schedule), this is a flat
    feed of *every* run that came from a schedule, carrying the same
    server-resolved ``action_name`` / ``target_name`` presentation hints the
    per-schedule listing uses so the UI doesn't need N+1 lookups.
    """

    id: int
    action_key: str
    action_name: str | None = None
    scheduled_action_id: int
    target_kind: ScheduledActionTargetKind
    target_name: str | None = None
    status: str
    started_at: datetime | None = None
    finished_at: datetime | None = None
    created_at: datetime
    error_message: str | None = None

    model_config = ConfigDict(from_attributes=True)


class ScheduledActionIn(BaseModel):
    """Body for POST /api/scheduled-actions and PUT /api/scheduled-actions/{id}."""

    target_kind: ScheduledActionTargetKind
    target_id: int | None = None
    action_key: str
    parameters: dict[str, Any] = Field(default_factory=dict)
    schedule_cron: str | None = None
    enabled: bool = False
    snapshot_enabled: bool = True
    verify_enabled: bool = True
    auto_rollback: bool = True
    batch_size: int = Field(default=1, ge=1)
    trigger: ScheduledActionTrigger = "cron"
    #: Alert triggers: which alerts, one rule per line (alert names and
    #: labels). See app.alerts.triggers.
    alert_match: str | None = None
    alert_cooldown_minutes: int = Field(default=60, ge=0, le=10080)
    investigate_on_failure: bool = True


class ScheduledActionOut(BaseModel):
    id: int
    target_kind: ScheduledActionTargetKind
    target_id: int | None = None
    action_key: str
    parameters: dict[str, Any]
    schedule_cron: str | None = None
    enabled: bool
    snapshot_enabled: bool
    verify_enabled: bool
    auto_rollback: bool
    batch_size: int
    trigger: ScheduledActionTrigger = "cron"
    alert_match: str | None = None
    alert_cooldown_minutes: int = 60
    investigate_on_failure: bool = True
    last_dispatched_at: datetime | None = None
    created_at: datetime
    updated_at: datetime

    # Server-resolved presentation helpers — populated by the API
    # listing/detail endpoints so the UI can render rows without
    # refetching hosts/groups/actions in a loop.
    target_name: str | None = None
    action_name: str | None = None
    pack_name: str | None = None
    destructive: bool | None = None
    last_run: ScheduledActionRunSummary | None = None

    model_config = ConfigDict(from_attributes=True)


class AlertTriggerPreviewIn(BaseModel):
    """An alert trigger's target and match, before it is saved."""

    target_kind: ScheduledActionTargetKind
    target_id: int | None = None
    alert_match: str


class AlertTriggerMatchOut(BaseModel):
    """A recorded alert an alert trigger would have fired on."""

    alert_event_id: int
    alertname: str
    status: str
    host_id: int | None = None
    hostname: str | None = None
    created_at: datetime


class ValidateCronRequest(BaseModel):
    cron: str


class ValidateCronResponse(BaseModel):
    valid: bool
    message: str | None = None
    next_run_at: list[datetime] = Field(default_factory=list)
    # The ``scheduling.timezone`` the expression was read in.
    timezone: str = "UTC"
